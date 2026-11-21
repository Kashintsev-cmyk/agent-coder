"""Пакетная обработка альбомов (media_group): несколько вложений = одна задача.

Проблема: Telegram присылает альбом как НЕСКОЛЬКО отдельных Message с общим
`media_group_id`. Без склейки первое фото стартовало задачу, а остальные
получали «Другая задача уже выполняется» — юзер отправлял 5 скриншотов,
модель видела один.

Проверяем: накопление элементов, дебаунс (одна задача на альбом), лимит
элементов, текст задачи из подписи/файлов, очистку буфера.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_coder.telegram.handlers import task as task_handler


def _message(chat_id: int = 42, media_group_id: str | None = None, caption: str = ""):
    """Минимальный двойник aiogram Message для логики альбомов."""
    return SimpleNamespace(
        chat=SimpleNamespace(id=chat_id),
        media_group_id=media_group_id,
        caption=caption,
    )


def _session(session_id: str = "sess1"):
    return SimpleNamespace(id=session_id, model="test/model")


@pytest.fixture(autouse=True)
def _clean_albums():
    """Буфер альбомов — модульная глобальная переменная, чистим между тестами."""
    task_handler._albums.clear()
    yield
    for buf in task_handler._albums.values():
        if buf.timer is not None:
            buf.timer.cancel()
    task_handler._albums.clear()


# --- _album_key -----------------------------------------------------------


def test_album_key_uses_chat_and_group_id():
    key = task_handler._album_key(_message(chat_id=7, media_group_id="grp"))
    assert key == "7:grp"


def test_album_key_none_without_media_group():
    """Одиночное сообщение — не альбом, ключа нет (задача стартует сразу)."""
    assert task_handler._album_key(_message(media_group_id=None)) is None


# --- _album_task_text -----------------------------------------------------


def test_task_text_uses_caption_when_present():
    buf = task_handler._AlbumBuffer(
        chat_id=1, session_id="s", items=[Path("a.jpg")], caption="Что на фото?"
    )
    assert task_handler._album_task_text(buf) == "Что на фото?"


def test_task_text_includes_each_text_file_with_header():
    """Каждый файл — с заголовком, иначе модель не поймёт, где чей кусок."""
    buf = task_handler._AlbumBuffer(
        chat_id=1,
        session_id="s",
        texts=[("a.log", "line1"), ("b.log", "line2")],
    )
    text = task_handler._album_task_text(buf)
    assert "=== a.log ===" in text
    assert "line1" in text
    assert "=== b.log ===" in text
    assert "line2" in text


def test_task_text_caption_precedes_files():
    buf = task_handler._AlbumBuffer(
        chat_id=1,
        session_id="s",
        caption="Найди причину падения",
        texts=[("err.log", "Traceback")],
    )
    text = task_handler._album_task_text(buf)
    assert text.index("Найди причину падения") < text.index("err.log")


def test_task_text_default_mentions_count_for_images_only():
    buf = task_handler._AlbumBuffer(
        chat_id=1, session_id="s", items=[Path("a.jpg"), Path("b.jpg")]
    )
    text = task_handler._album_task_text(buf)
    assert "2 изображений" in text


# --- _album_source_note ---------------------------------------------------


def test_source_note_images_only():
    buf = task_handler._AlbumBuffer(
        chat_id=1, session_id="s", items=[Path("a.jpg"), Path("b.jpg")]
    )
    assert task_handler._album_source_note(buf) == "2 изображений"


def test_source_note_mixed():
    buf = task_handler._AlbumBuffer(
        chat_id=1,
        session_id="s",
        items=[Path("a.jpg")],
        texts=[("a.log", "x")],
    )
    assert task_handler._album_source_note(buf) == "1 изображений + 1 файлов"


def test_source_note_empty_album_fallback():
    buf = task_handler._AlbumBuffer(chat_id=1, session_id="s")
    assert task_handler._album_source_note(buf) == "альбом"


# --- _collect_album_item --------------------------------------------------


@pytest.mark.asyncio
async def test_collect_accumulates_items_in_one_buffer(monkeypatch):
    """Два фото одного альбома попадают в ОДИН буфер (а не в две задачи)."""
    # Дебаунс не должен срабатывать в тесте — иначе уйдёт фоновая задача.
    monkeypatch.setattr(task_handler, "_ALBUM_DEBOUNCE_SEC", 3600)

    msg = _message(media_group_id="g1")
    key = task_handler._album_key(msg)
    assert key is not None

    ok1 = await task_handler._collect_album_item(
        key, msg, None, _session(), Path("1.jpg")
    )
    ok2 = await task_handler._collect_album_item(
        key, msg, None, _session(), Path("2.jpg")
    )
    assert ok1 and ok2

    buf = task_handler._albums[key]
    assert [p.name for p in buf.items] == ["1.jpg", "2.jpg"]
    assert buf.total == 2


@pytest.mark.asyncio
async def test_collect_keeps_first_caption(monkeypatch):
    monkeypatch.setattr(task_handler, "_ALBUM_DEBOUNCE_SEC", 3600)
    key = "42:g2"
    await task_handler._collect_album_item(
        key, _message(caption="первая"), None, _session(), Path("1.jpg")
    )
    await task_handler._collect_album_item(
        key, _message(caption="вторая"), None, _session(), Path("2.jpg")
    )
    assert task_handler._albums[key].caption == "первая"


@pytest.mark.asyncio
async def test_collect_accumulates_text_files(monkeypatch):
    monkeypatch.setattr(task_handler, "_ALBUM_DEBOUNCE_SEC", 3600)
    key = "42:g3"
    await task_handler._collect_album_item(
        key, _message(), None, _session(), Path(), text=("a.log", "aaa")
    )
    await task_handler._collect_album_item(
        key, _message(), None, _session(), Path(), text=("b.log", "bbb")
    )
    buf = task_handler._albums[key]
    assert buf.texts == [("a.log", "aaa"), ("b.log", "bbb")]
    assert buf.items == []


@pytest.mark.asyncio
async def test_collect_rejects_beyond_limit(monkeypatch):
    """Сверх _ALBUM_MAX_ITEMS элемент отбрасывается (не молча, вызывающий скажет)."""
    monkeypatch.setattr(task_handler, "_ALBUM_DEBOUNCE_SEC", 3600)
    monkeypatch.setattr(task_handler, "_ALBUM_MAX_ITEMS", 2)
    key = "42:g4"

    assert await task_handler._collect_album_item(
        key, _message(), None, _session(), Path("1.jpg")
    )
    assert await task_handler._collect_album_item(
        key, _message(), None, _session(), Path("2.jpg")
    )
    # Третий — сверх лимита.
    assert not await task_handler._collect_album_item(
        key, _message(), None, _session(), Path("3.jpg")
    )
    assert task_handler._albums[key].total == 2


@pytest.mark.asyncio
async def test_collect_cancels_previous_timer(monkeypatch):
    """Каждый новый элемент сдвигает дебаунс — старая задача отменяется."""
    monkeypatch.setattr(task_handler, "_ALBUM_DEBOUNCE_SEC", 3600)
    key = "42:g5"
    await task_handler._collect_album_item(
        key, _message(), None, _session(), Path("1.jpg")
    )
    first = task_handler._albums[key].timer
    assert first is not None
    await task_handler._collect_album_item(
        key, _message(), None, _session(), Path("2.jpg")
    )
    second = task_handler._albums[key].timer
    assert second is not first
    # Первый таймер отменён — даём event loop обработать отмену.
    await pytest.importorskip("asyncio").sleep(0)
    assert first.cancelled() or first.done()


# --- _flush_album ---------------------------------------------------------


@pytest.mark.asyncio
async def test_flush_starts_one_task_with_all_images(monkeypatch):
    """Ровно ОДНА задача, и в неё уходят ВСЕ изображения альбома."""
    captured: dict[str, object] = {}

    async def _start_task(bot, message, task_text, source_note="", images=None):
        captured["task_text"] = task_text
        captured["images"] = images
        captured["source_note"] = source_note

    monkeypatch.setattr(task_handler, "_start_task", _start_task)
    monkeypatch.setattr(task_handler, "_get_active_session", lambda cid: _session())

    key = "42:g6"
    task_handler._albums[key] = task_handler._AlbumBuffer(
        chat_id=42,
        session_id="sess1",
        items=[Path("1.jpg"), Path("2.jpg")],
    )

    await task_handler._flush_album(key, None, _message())

    assert captured["images"] == [Path("1.jpg"), Path("2.jpg")]
    assert "2 изображений" in str(captured["source_note"])
    assert key not in task_handler._albums, "буфер должен очищаться"


@pytest.mark.asyncio
async def test_flush_skips_when_session_changed(monkeypatch):
    """Пока копился альбом, сессию сменили → задачу в чужую сессию не шлём."""
    called = False

    async def _start_task(*args, **kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(task_handler, "_start_task", _start_task)
    monkeypatch.setattr(
        task_handler, "_get_active_session", lambda cid: _session("другая")
    )

    key = "42:g7"
    task_handler._albums[key] = task_handler._AlbumBuffer(
        chat_id=42, session_id="sess1", items=[Path("1.jpg")]
    )

    await task_handler._flush_album(key, None, _message())
    assert not called
    # Буфер всё равно очищен: альбом относится к прошлой сессии.
    assert key not in task_handler._albums


@pytest.mark.asyncio
async def test_flush_noop_for_unknown_key():
    """Флаш несуществующего ключа — без исключений (таймер мог опередить)."""
    await task_handler._flush_album("нет:такого", None, _message())


@pytest.mark.asyncio
async def test_flush_noop_for_empty_buffer(monkeypatch):
    called = False

    async def _start_task(*args, **kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(task_handler, "_start_task", _start_task)
    monkeypatch.setattr(task_handler, "_get_active_session", lambda cid: _session())

    key = "42:g8"
    task_handler._albums[key] = task_handler._AlbumBuffer(chat_id=42, session_id="s")
    await task_handler._flush_album(key, None, _message())
    assert not called


@pytest.mark.asyncio
async def test_flush_mixed_sends_images_and_text_in_one_task(monkeypatch):
    """Изображения + текстовые файлы уезжают одной задачей, а не двумя."""
    captured: dict[str, object] = {}

    async def _start_task(bot, message, task_text, source_note="", images=None):
        captured["task_text"] = task_text
        captured["images"] = images
        captured["source_note"] = source_note

    monkeypatch.setattr(task_handler, "_start_task", _start_task)
    monkeypatch.setattr(task_handler, "_get_active_session", lambda cid: _session())

    key = "42:g9"
    task_handler._albums[key] = task_handler._AlbumBuffer(
        chat_id=42,
        session_id="sess1",
        items=[Path("shot.jpg")],
        texts=[("err.log", "Traceback: boom")],
    )

    await task_handler._flush_album(key, None, _message())

    assert captured["images"] == [Path("shot.jpg")]
    assert "err.log" in str(captured["task_text"])
    assert "Traceback: boom" in str(captured["task_text"])
    assert "1 изображений + 1 файлов" in str(captured["source_note"])
