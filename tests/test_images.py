"""Тесты приёма изображений (B6.2)."""

from __future__ import annotations

import base64

from agent_coder.agent.loop import _build_user_content

# Минимальный JPEG (1x1 пиксель)
_JPEG_BYTES = base64.b64decode(
    "/9j/4AAQSkZJRgABAQEASABIAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8U"
    "HRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wAALCAABAAEBAREA"
    "/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQA"
    "AAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJico"
    "KSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKT"
    "lJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo"
    "6erx8vP09fb3+Pn6/9oACAEBAAA/AKpr/9k="
)


def test_build_user_content_no_images():
    assert _build_user_content("задача", None) == "задача"
    assert _build_user_content("задача", []) == "задача"


def test_build_user_content_with_image(tmp_path):
    img = tmp_path / "pic.jpg"
    img.write_bytes(_JPEG_BYTES)

    content = _build_user_content("что на фото?", [img])
    assert isinstance(content, list)
    assert content[0] == {"type": "text", "text": "что на фото?"}
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    # base64 декодируется обратно в исходные байты
    b64_part = content[1]["image_url"]["url"].split(",", 1)[1]
    assert base64.b64decode(b64_part) == _JPEG_BYTES


def test_build_user_content_multiple_images(tmp_path):
    a = tmp_path / "a.jpg"
    a.write_bytes(_JPEG_BYTES)
    b = tmp_path / "b.jpg"
    b.write_bytes(_JPEG_BYTES)
    content = _build_user_content("сравни", [a, b])
    assert len(content) == 3  # text + 2 image
    assert all(c["type"] == "image_url" for c in content[1:])


def test_build_user_content_missing_file_skipped(tmp_path):
    missing = tmp_path / "no.jpg"
    content = _build_user_content("x", [missing])
    # только текстовый блок — изображение пропущено
    assert len(content) == 1
    assert content[0]["type"] == "text"


# --- save_media ---


def test_save_media_creates_file(tmp_path, monkeypatch):
    from agent_coder.agent.prompts import Mode
    from agent_coder.agent.session import Session
    from agent_coder.config import config

    monkeypatch.setattr(config, "SESSIONS_DIR", tmp_path)
    s = Session.new(workspace="/tmp", model="openai/gpt-5.6-luna", mode=Mode.ACT)
    p = s.save_media(_JPEG_BYTES, ext="jpg")
    assert p.exists()
    assert p.parent == tmp_path / "media" / s.id
    assert p.read_bytes() == _JPEG_BYTES


def test_save_media_strips_dot(tmp_path, monkeypatch):
    from agent_coder.agent.prompts import Mode
    from agent_coder.agent.session import Session
    from agent_coder.config import config

    monkeypatch.setattr(config, "SESSIONS_DIR", tmp_path)
    s = Session.new(workspace="/tmp", model="x", mode=Mode.ACT)
    p = s.save_media(b"x", ext=".PNG")
    assert p.suffix == ".png"


def test_save_media_empty_ext(tmp_path, monkeypatch):
    from agent_coder.agent.prompts import Mode
    from agent_coder.agent.session import Session
    from agent_coder.config import config

    monkeypatch.setattr(config, "SESSIONS_DIR", tmp_path)
    s = Session.new(workspace="/tmp", model="x", mode=Mode.ACT)
    p = s.save_media(b"x", ext="")
    assert p.suffix == ".bin"
