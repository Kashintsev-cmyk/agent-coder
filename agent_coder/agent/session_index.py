"""Индекс метаданных сессий для быстрого list_all.

Проблема: sessions/*.json содержат полную историю, включая base64-картинки
(до ~1 МБ на файл). Парсить всё на /sessions — дорого и блокирует loop.

Решение: sidecar-индекс sessions/index.json с лёгкими метаданными.
- Session.save() → index.update()
- Session.delete() → index.remove()
- Session.list_all() → index.list()
- Если индекс отсутствует/устарел → rebuild() из существующих JSON.

Формат индекса:
{
  "_version": 1,
  "_updated_at": <float>,
  "sessions": {
    "<id>": {
      "id": "...", "title": "...", "workspace": "...", "model": "...",
      "mode": "act", "cost": 0.0, "request_count": 0,
      "created_at": 0.0, "updated_at": 0.0
    }, ...
  }
}
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

logger = logging.getLogger(__name__)

INDEX_VERSION = 1
INDEX_FILENAME = "index.json"


class _HasMeta(Protocol):
    """Минимальный интерфейс, который нужен индексу от сессии."""

    id: str
    title: str
    workspace: str
    model: str
    mode: Any  # Mode с .value
    request_count: int
    created_at: float
    updated_at: float
    total_usage: Any  # Usage с .cost


@dataclass(frozen=True)
class SessionMeta:
    """Лёгкая мета-запись о сессии (без messages)."""

    id: str
    title: str
    workspace: str
    model: str
    mode: str
    cost: float
    request_count: int
    created_at: float
    updated_at: float

    @classmethod
    def from_session(cls, s: _HasMeta) -> SessionMeta:
        mode_val = getattr(getattr(s, "mode", None), "value", None)
        if mode_val is None:
            mode_val = str(getattr(s, "mode", "")) or "act"
        cost = 0.0
        try:
            cost = float(getattr(getattr(s, "total_usage", None), "cost", 0.0))
        except (TypeError, ValueError):
            cost = 0.0
        return cls(
            id=str(s.id),
            title=str(getattr(s, "title", "") or ""),
            workspace=str(getattr(s, "workspace", "") or ""),
            model=str(getattr(s, "model", "") or ""),
            mode=mode_val,
            cost=cost,
            request_count=int(getattr(s, "request_count", 0) or 0),
            created_at=float(getattr(s, "created_at", 0.0) or 0.0),
            updated_at=float(getattr(s, "updated_at", 0.0) or 0.0),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SessionIndex:
    """In-memory + дисковый индекс метаданных сессий.

    Singleton по смыслу — создаётся один раз в session.py, привязан
    к config.SESSIONS_DIR / "index.json".
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path: Path = path if path is not None else self._default_path()
        self._sessions: dict[str, SessionMeta] = {}
        self._loaded: bool = False

    @staticmethod
    def _default_path() -> Path:
        from agent_coder.config import config

        return config.SESSIONS_DIR / INDEX_FILENAME

    @property
    def path(self) -> Path:
        return self._path

    # ---------- диск ----------

    def load(self) -> bool:
        """Прочитать индекс с диска. True — если успешно (или файла нет).

        Возвращает False только при битом JSON / несовпадении версии,
        при этом очищает in-memory.
        """
        if not self._path.exists():
            self._sessions = {}
            self._loaded = True
            return True

        try:
            raw = self._path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("Не удалось прочитать индекс сессий %s: %s", self._path, exc)
            return False

        try:
            payload = json.loads(raw)
        except (ValueError, TypeError) as exc:
            logger.warning("Битый JSON в индексе сессий %s: %s", self._path, exc)
            self._sessions = {}
            self._loaded = False
            return False

        if not isinstance(payload, dict):
            logger.warning("Индекс сессий %s — не объект", self._path)
            self._sessions = {}
            self._loaded = False
            return False

        if payload.get("_version") != INDEX_VERSION:
            logger.info(
                "Индекс сессий %s имеет версию %r, ожидается %r — пересоберу",
                self._path,
                payload.get("_version"),
                INDEX_VERSION,
            )
            self._sessions = {}
            self._loaded = False
            return False

        raw_sessions = payload.get("sessions")
        if not isinstance(raw_sessions, dict):
            logger.warning("В индексе %s нет секции sessions", self._path)
            self._sessions = {}
            self._loaded = False
            return False

        parsed: dict[str, SessionMeta] = {}
        for sid, data in raw_sessions.items():
            if not isinstance(data, dict):
                continue
            try:
                parsed[sid] = SessionMeta(
                    id=str(data.get("id", sid)),
                    title=str(data.get("title", "") or ""),
                    workspace=str(data.get("workspace", "") or ""),
                    model=str(data.get("model", "") or ""),
                    mode=str(data.get("mode", "act") or "act"),
                    cost=float(data.get("cost", 0.0) or 0.0),
                    request_count=int(data.get("request_count", 0) or 0),
                    created_at=float(data.get("created_at", 0.0) or 0.0),
                    updated_at=float(data.get("updated_at", 0.0) or 0.0),
                )
            except (TypeError, ValueError) as exc:
                logger.warning("Пропускаю запись %r в индексе: %s", sid, exc)

        self._sessions = parsed
        self._loaded = True
        return True

    def save(self) -> None:
        """Атомарная запись индекса."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "_version": INDEX_VERSION,
            "_updated_at": time.time(),
            "sessions": {sid: m.to_dict() for sid, m in self._sessions.items()},
        }
        tmp = self._path.with_name(self._path.name + ".tmp")
        try:
            tmp.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(tmp, self._path)
        except OSError as exc:
            logger.warning("Не удалось записать индекс сессий %s: %s", self._path, exc)
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass

    # ---------- операции ----------

    def update(self, session: _HasMeta) -> None:
        """Обновить запись о сессии (или создать). Пишет на диск."""
        if not self._loaded:
            self.load()
        meta = SessionMeta.from_session(session)
        self._sessions[meta.id] = meta
        self.save()

    def remove(self, session_id: str) -> None:
        """Удалить запись. Пишет на диск, только если что-то было."""
        if not self._loaded:
            self.load()
        if session_id in self._sessions:
            del self._sessions[session_id]
            self.save()

    def list(self) -> list[SessionMeta]:
        """Все мета-записи, отсортированные по updated_at (свежие впереди)."""
        if not self._loaded:
            self.load()
        return sorted(
            self._sessions.values(),
            key=lambda m: m.updated_at,
            reverse=True,
        )

    def get(self, session_id: str) -> SessionMeta | None:
        if not self._loaded:
            self.load()
        return self._sessions.get(session_id)

    def count(self) -> int:
        if not self._loaded:
            self.load()
        return len(self._sessions)

    # ---------- rebuild ----------

    def rebuild(self, sessions_dir: Path) -> int:
        """Перестроить индекс из существующих sessions/*.json.

        Читает каждый JSON целиком (дорого при больших сессиях, но
        выполняется редко — при первом запуске или рассинхроне).
        Битые/непонятные файлы пропускает с warning.

        Возвращает количество успешно прочитанных сессий.
        """
        from agent_coder.agent.session import Session  # локальный импорт

        if not sessions_dir.exists():
            self._sessions = {}
            self.save()
            return 0

        parsed: dict[str, SessionMeta] = {}
        for p in sessions_dir.glob("*.json"):
            if p.name == INDEX_FILENAME:
                continue
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                s = Session.from_dict(data)
                parsed[s.id] = SessionMeta.from_session(s)
            except Exception as exc:
                logger.warning("rebuild: пропускаю %s (%s)", p.name, exc)
                continue

        self._sessions = parsed
        self._loaded = True
        self.save()
        logger.info("Индекс сессий пересобран: %d записей", len(parsed))
        return len(parsed)


# Глобальный singleton. Session использует его через ленивый импорт.
index = SessionIndex()
