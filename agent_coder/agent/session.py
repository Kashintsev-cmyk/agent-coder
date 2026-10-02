"""Сессия агента: история сообщений, накопленная стоимость, сохранение."""
from __future__ import annotations

import json
import logging
import shutil
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from agent_coder.agent.prompts import Mode
from agent_coder.config import config
from agent_coder.llm.types import Usage
from agent_coder.models import DEFAULT_REASONING

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
    service_tier: str = ""  # "" = использовать config.SERVICE_TIER
    verbosity: str = ""  # "" = не передавать параметр
    # Статистика сжатия истории (B5.1: только собираем)
    last_input_tokens: int = 0  # usage.input_tokens последнего запроса
    summary_count: int = 0      # сколько раз сжимали историю
    messages: list[dict[str, Any]] = field(default_factory=list)
    total_usage: Usage = field(default_factory=Usage)
    request_count: int = 0
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    @classmethod
    def new(cls, workspace: str, model: str, mode: Mode, title: str = "",
            web_search: bool = False) -> Session:
        return cls(
            id=uuid.uuid4().hex[:8],
            workspace=workspace,
            model=model,
            mode=mode,
            title=title,
            web_search=web_search,
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
        """Починить dangling tool_calls: если последний assistant имеет
        tool_calls без ответов tool — добавить им заглушку.

        Вызывается перед каждым запросом к LLM. Возвращает число
        добавленных сообщений (0 если всё ок).
        """
        if not self.messages:
            return 0

        # Идём с конца: находим незакрытые tool_calls
        last_assistant_idx = None
        for i in range(len(self.messages) - 1, -1, -1):
            role = self.messages[i].get("role")
            if role == "assistant":
                last_assistant_idx = i
                break
            if role == "tool":
                # Последний assistant уже имеет tool-ответ, всё ок
                return 0
            if role == "user":
                # После последнего assistant нет tool — но и user новый
                return 0

        if last_assistant_idx is None:
            return 0

        assistant = self.messages[last_assistant_idx]
        tool_calls = assistant.get("tool_calls") or []
        if not tool_calls:
            return 0

        # Собираем id уже отвеченных
        answered_ids = set()
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
            self.messages.append({
                "role": "tool",
                "tool_call_id": tid,
                "content": "Ошибка: выполнение прервано (перезапуск бота/клиента)",
            })
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
        if "last_input_tokens" not in data:
            data["last_input_tokens"] = 0
        if "summary_count" not in data:
            data["summary_count"] = 0
        return cls(**data)

    def save(self) -> Path:
        config.SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        path = config.SESSIONS_DIR / f"{self.id}.json"
        path.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return path

    @classmethod
    def load(cls, session_id: str) -> Session | None:
        path = config.SESSIONS_DIR / f"{session_id}.json"
        if not path.exists():
            return None
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))

    @classmethod
    def list_all(cls) -> list[Session]:
        if not config.SESSIONS_DIR.exists():
            return []
        out = []
        for p in config.SESSIONS_DIR.glob("*.json"):
            try:
                out.append(cls.from_dict(json.loads(p.read_text(encoding="utf-8"))))
            except Exception:
                continue
        out.sort(key=lambda s: s.updated_at, reverse=True)
        return out

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

        return removed

    def rename(self, new_title: str) -> None:
        self.title = new_title.strip() or self.title
        self.updated_at = time.time()
