"""Сессия агента: история сообщений, накопленная стоимость, сохранение."""

from __future__ import annotations

import json
import logging
import os
import shutil
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agent_coder.agent.prompts import Mode
from agent_coder.config import config
from agent_coder.llm.types import Usage
from agent_coder.models import DEFAULT_REASONING

if TYPE_CHECKING:
    from agent_coder.agent.session_index import SessionMeta

logger = logging.getLogger(__name__)


def content_to_text(content: Any) -> str:
    """Нормализовать content сообщения в строку.

    Поддерживает форматы OpenAI:
      - None → ""
      - str → как есть
      - list[{"type":"text", "text":...}] → склеивает тексты
      - list[{"type":"image_url", ...}] → "[изображение]"
      - смешанный → текст + плейсхолдеры
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            t = block.get("type")
            if t == "text":
                parts.append(block.get("text") or "")
            elif t == "image_url":
                parts.append("[изображение]")
        return "\n".join(p for p in parts if p)
    return str(content)


def _make_default_title(messages: list[dict[str, Any]]) -> str:
    for msg in messages:
        if msg.get("role") == "user":
            text = content_to_text(msg.get("content")).strip().replace("\n", " ")
            if text:
                return text[:60] + ("…" if len(text) > 60 else "")
    return "(без названия)"


@dataclass
class Session:
    id: str
    workspace: str
    model: str
    mode: Mode
    title: str = ""
    reasoning_effort: str = DEFAULT_REASONING
    web_search: bool = False
    yolo: bool = False  # без approval на опасные операции
    service_tier: str = ""  # "" = использовать config.SERVICE_TIER
    verbosity: str = ""  # "" = не передавать параметр
    # Модель выбрана пользователем вручную (/model или меню сессии).
    # True — роутер не меняет модель, работает только fallback-цепочка.
    model_manual: bool = False
    # Структурированная рабочая память (см. agent/work_state.py).
    # Не пересуммаризуется LLM при compress(): детерминированный набор
    # фактов, инжектится в system prompt и служит скелетом резюме.
    work_state: dict[str, list[str]] = field(default_factory=dict)
    # Прочитанные файлы: path -> краткий digest (header + def/class/import).
    # Детерминированно, без LLM. Переживает compress() — чтобы модель
    # видела «уже читал X» и не перечитывала целиком (сессия 6cc7d5bd:
    # 92 read_file, 37 уникальных, 55 повторов после сжатий = деньги).
    read_paths: dict[str, str] = field(default_factory=dict)
    # Статистика сжатия истории (B5.1: только собираем)
    last_input_tokens: int = 0  # usage.input_tokens последнего запроса
    summary_count: int = 0  # сколько раз сжимали историю
    messages: list[dict[str, Any]] = field(default_factory=list)
    total_usage: Usage = field(default_factory=Usage)
    request_count: int = 0
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    @classmethod
    def new(
        cls,
        workspace: str,
        model: str,
        mode: Mode,
        title: str = "",
        web_search: bool = False,
        model_manual: bool = False,
    ) -> Session:
        return cls(
            id=uuid.uuid4().hex[:8],
            workspace=workspace,
            model=model,
            mode=mode,
            title=title,
            web_search=web_search,
            model_manual=model_manual,
        )

    def save_media(self, data: bytes, ext: str = "bin") -> Path:
        """Сохранить бинарные данные в media_dir с уникальным именем.

        Возвращает путь к файлу. Директория создаётся лениво.
        """
        import uuid as _uuid

        self.media_dir.mkdir(parents=True, exist_ok=True)
        clean_ext = ext.lstrip(".").lower() or "bin"
        path = self.media_dir / f"{_uuid.uuid4().hex}.{clean_ext}"
        path.write_bytes(data)
        return path

    @property
    def media_dir(self) -> Path:
        """Директория для медиа-вложений этой сессии.

        Создаётся лениво (при первом сохранении файла), путь детерминированный.
        """
        return config.SESSIONS_DIR / "media" / self.id

    def add_message(self, message: dict[str, Any]) -> None:
        # Sanitize: убираем нативный DSML DeepSeek из assistant-контента.
        # Иначе модель видит его в истории и воспроизводит снова.
        if message.get("role") == "assistant":
            content = message.get("content")
            if isinstance(content, str) and "DSML" in content:
                from agent_coder.log_safety import strip_dsml

                message["content"] = strip_dsml(content)
        self.messages.append(message)
        self.updated_at = time.time()
        if not self.title and message.get("role") == "user":
            self.title = _make_default_title([message])

    def add_usage(self, usage: Usage) -> None:
        self.total_usage = self.total_usage + usage
        self.request_count += 1
        self.updated_at = time.time()

    def preview(self) -> str:
        for msg in reversed(self.messages):
            if msg.get("role") == "user":
                text = content_to_text(msg.get("content")).strip().replace("\n", " ")
                return text[:70] + ("…" if len(text) > 70 else "")
        return ""

    def age_str(self) -> str:
        delta = int(time.time() - self.updated_at)
        if delta < 60:
            return f"{delta}с назад"
        if delta < 3600:
            return f"{delta // 60}м назад"
        if delta < 86400:
            return f"{delta // 3600}ч назад"
        return f"{delta // 86400}д назад"

    def sanitize_tool_calls(self) -> int:
        """Починить dangling tool_calls у ПОСЛЕДНЕГО assistant.

        Идём к последнему assistant (независимо от того, что после него:
        tool/user/assistant) — собираем id всех tool-ответов ПОСЛЕ него
        и добиваем заглушками те, на которые ответа нет.

        Раньше алгоритм прерывался на первом `tool` или `user` с конца,
        из-за чего после аварийного рестарта часть tool_calls оставалась
        без ответов → API возвращал 400 ("tool_calls without responses").

        Возвращает число добавленных сообщений (0 если всё ок).
        """
        if not self.messages:
            return 0

        # Ищем последний assistant (без ранних выходов)
        last_assistant_idx = None
        for i in range(len(self.messages) - 1, -1, -1):
            if self.messages[i].get("role") == "assistant":
                last_assistant_idx = i
                break

        if last_assistant_idx is None:
            return 0

        assistant = self.messages[last_assistant_idx]
        tool_calls = assistant.get("tool_calls") or []
        if not tool_calls:
            return 0

        # Собираем id всех tool-ответов ПОСЛЕ assistant
        answered_ids: set[str] = set()
        for i in range(last_assistant_idx + 1, len(self.messages)):
            msg = self.messages[i]
            if msg.get("role") == "tool":
                tid = msg.get("tool_call_id")
                if tid:
                    answered_ids.add(tid)

        # Добавляем заглушки для неотвеченных
        added = 0
        for tc in tool_calls:
            tid = tc.get("id")
            if not tid or tid in answered_ids:
                continue
            self.messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tid,
                    # НЕ начинать с «Ошибка»: watchdog (loop.py) считает
                    # такие tool-результаты ошибками и инкрементирует
                    # consecutive_errors. После аварийного рестарта с 3
                    # dangling tool_calls счётчик сразу 3/5 → ложный стоп.
                    # «Прервано» — нейтрально: не ошибка, не прогресс.
                    "content": "Прервано: выполнение не завершено (перезапуск)",
                }
            )
            added += 1

        if added:
            self.updated_at = time.time()
        return added

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["mode"] = self.mode.value
        d["total_usage"] = asdict(self.total_usage)
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Session:
        data = dict(data)
        data["mode"] = Mode(data["mode"])
        data["total_usage"] = Usage(**data["total_usage"])
        if "title" not in data or not data["title"]:
            data["title"] = _make_default_title(data.get("messages", []))
        # Обратная совместимость со старыми сессиями без reasoning
        if "reasoning_effort" not in data:
            data["reasoning_effort"] = DEFAULT_REASONING
        if "web_search" not in data:
            data["web_search"] = False
        if "yolo" not in data:
            data["yolo"] = False
        if "last_input_tokens" not in data:
            data["last_input_tokens"] = 0
        if "summary_count" not in data:
            data["summary_count"] = 0
        if "model_manual" not in data:
            data["model_manual"] = False
        if "work_state" not in data:
            data["work_state"] = {}
        if "read_paths" not in data:
            data["read_paths"] = {}

        # Разовая чистка: если в истории есть DSML-мусор (старые версии
        # кода не фильтровали) — убираем при загрузке.
        msgs = data.get("messages", [])
        if any(
            isinstance(m.get("content"), str) and "DSML" in m.get("content", "")
            for m in msgs
        ):
            from agent_coder.log_safety import strip_dsml

            for m in msgs:
                if m.get("role") == "assistant" and isinstance(m.get("content"), str):
                    m["content"] = strip_dsml(m["content"])

        return cls(**data)

    @classmethod
    def from_meta(cls, meta: SessionMeta) -> Session:
        """Собрать лёгкую Session (без messages) из индекса.

        Используется в list_all()/latest() — для UI-списков, где messages
        не нужны. Для полноценной работы нужен Session.load(id).
        """
        try:
            mode = Mode(meta.mode)
        except ValueError:
            mode = Mode.ACT
        usage = Usage(cost=float(meta.cost))
        return cls(
            id=meta.id,
            workspace=meta.workspace,
            model=meta.model,
            mode=mode,
            title=meta.title,
            request_count=meta.request_count,
            created_at=meta.created_at,
            updated_at=meta.updated_at,
            total_usage=usage,
        )

    def save(self) -> Path:
        """Атомарная запись: tmp + os.replace (защита от SIGKILL/диска)."""
        config.SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        path = config.SESSIONS_DIR / f"{self.id}.json"
        payload = json.dumps(self.to_dict(), ensure_ascii=False, indent=2)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, path)

        # Обновить sidecar-индекс (не критично — ошибку логируем, не падаем)
        try:
            from agent_coder.agent.session_index import index as _idx

            _idx.update(self)
        except Exception as e:
            logger.warning("session_index.update failed for %s: %s", self.id, e)

        return path

    @classmethod
    def load(cls, session_id: str) -> Session | None:
        """Загрузить сессию. None — если файла нет или он битый.

        Раньше JSONDecodeError/OSError/ValueError улетали в хендлер и
        глобальный error-хендлер бота — юзер видел путь файла и трейс.
        Теперь ловим и логируем.
        """
        path = config.SESSIONS_DIR / f"{session_id}.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return cls.from_dict(data)
        except (json.JSONDecodeError, OSError, ValueError, KeyError, TypeError) as e:
            logger.error("session load failed for %s: %s", session_id, e)
            return None

    @classmethod
    def list_all(cls) -> list[Session]:
        """Список сессий для UI — читает sidecar-индекс, а не полные JSON.

        При рассинхроне (число *.json != число записей в индексе) —
        автоматический rebuild. Полные messages в результат не грузятся;
        для работы с конкретной сессией — Session.load(id).
        """
        if not config.SESSIONS_DIR.exists():
            return []

        from agent_coder.agent.session_index import INDEX_FILENAME
        from agent_coder.agent.session_index import index as _idx

        # Защита от рассинхрона: пользователь мог вручную удалить/добавить
        # *.json, а индекс остался старым.
        disk_ids = {
            p.stem
            for p in config.SESSIONS_DIR.glob("*.json")
            if p.name != INDEX_FILENAME
        }
        need_rebuild = False
        if _idx.count() == 0 or len(disk_ids) != _idx.count():
            need_rebuild = True
        else:
            # Точечная проверка: все id из индекса есть на диске и наоборот
            idx_ids = {m.id for m in _idx.list()}
            if idx_ids != disk_ids:
                need_rebuild = True

        if need_rebuild:
            _idx.rebuild(config.SESSIONS_DIR)

        return [cls.from_meta(m) for m in _idx.list()]

    @classmethod
    def latest(cls, workspace: str | None = None) -> Session | None:
        for s in cls.list_all():
            if workspace is None or s.workspace == workspace:
                return s
        return None

    def delete(self) -> int:
        """Удалить файл сессии и связанные audit-отчёты.

        Возвращает число удалённых файлов (включая саму сессию).
        """
        removed = 0
        session_path = config.SESSIONS_DIR / f"{self.id}.json"
        if session_path.exists():
            session_path.unlink()
            removed += 1

        audits_dir = config.SESSIONS_DIR / "audits"
        if audits_dir.exists():
            for pattern in (f"{self.id}_*.md", f"{self.id}_*.pdf"):
                for p in audits_dir.glob(pattern):
                    try:
                        p.unlink()
                        removed += 1
                    except Exception:
                        pass

        # Медиа сессии (sessions/media/<id>/) — удаляем рекурсивно
        media_root = config.SESSIONS_DIR / "media" / self.id
        if media_root.exists():
            try:
                for f in media_root.rglob("*"):
                    if f.is_file():
                        removed += 1
                shutil.rmtree(media_root)
            except Exception as e:
                logger.warning("media cleanup failed for %s: %s", self.id, e)

        # Архив сжатой истории (RAG-lite)
        from agent_coder.agent import history_archive

        if history_archive.delete(self.id):
            removed += 1

        # Удалить из sidecar-индекса
        try:
            from agent_coder.agent.session_index import index as _idx

            _idx.remove(self.id)
        except Exception as e:
            logger.warning("session_index.remove failed for %s: %s", self.id, e)

        return removed

    def rename(self, new_title: str) -> None:
        self.title = new_title.strip() or self.title
        self.updated_at = time.time()
