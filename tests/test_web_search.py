"""Тесты инструмента web_search (поиск в интернете через RouterAI)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from agent_coder.llm.types import Usage
from agent_coder.tools import web_search as ws
from agent_coder.tools.registry import ToolExecutionResult, ToolStatus


class _FakeMessage:
    def __init__(self, content: str | None, annotations: Any = None) -> None:
        self.content = content
        if annotations is not None:
            self.annotations = annotations


class _FakeChoice:
    def __init__(self, message: _FakeMessage) -> None:
        self.message = message


class _FakeRaw:
    def __init__(self, message: _FakeMessage) -> None:
        self.choices = [_FakeChoice(message)]


class _FakeResponse:
    """Минимальная замена ChatResponse: message.content + raw.choices[].message."""

    def __init__(self, content: str | None, annotations: Any = None) -> None:
        self.message = SimpleNamespace(content=content)
        self.raw = _FakeRaw(_FakeMessage(content, annotations))
        self.usage = Usage(input_tokens=123, output_tokens=7, cost=0.02)


# --- _extract_annotations -------------------------------------------------


def test_extract_annotations_pulls_url_citations():
    annotations = [
        {
            "type": "url_citation",
            "url_citation": {"url": "https://a.example", "title": "A"},
        },
        {
            "type": "url_citation",
            "url_citation": {"url": "https://b.example", "title": "B"},
        },
    ]
    resp = _FakeResponse("text", annotations)
    assert ws._extract_annotations(resp) == [
        {"url": "https://a.example", "title": "A"},
        {"url": "https://b.example", "title": "B"},
    ]


def test_extract_annotations_skips_non_citation_and_duplicates():
    annotations = [
        {"type": "other", "url_citation": {"url": "https://x.example"}},
        {"type": "url_citation", "url_citation": {"url": "https://dup.example"}},
        {"type": "url_citation", "url_citation": {"url": "https://dup.example"}},
    ]
    resp = _FakeResponse("t", annotations)
    assert ws._extract_annotations(resp) == [
        {"url": "https://dup.example", "title": "https://dup.example"}
    ]


def test_extract_annotations_without_field_returns_empty():
    resp = _FakeResponse("text", None)
    assert ws._extract_annotations(resp) == []


def test_extract_annotations_without_raw_returns_empty():
    class _NoRaw:
        pass

    assert ws._extract_annotations(_NoRaw()) == []


# --- _format --------------------------------------------------------------


def test_format_without_sources_is_plain_text():
    assert ws._format("q", "ответ", []) == "ответ"


def test_format_appends_numbered_sources():
    out = ws._format(
        "q",
        "ответ",
        [{"url": "https://a", "title": "A"}, {"url": "https://b", "title": "B"}],
    )
    assert "ответ" in out
    assert "1. A — https://a" in out
    assert "2. B — https://b" in out


def test_format_truncates_long_answer():
    out = ws._format("q", "x" * (ws.MAX_ANSWER_CHARS + 100), [])
    assert len(out) < ws.MAX_ANSWER_CHARS + 100
    assert "обрезан" in out


def test_format_empty_answer_suggests_reformulation():
    out = ws._format("запрос", "", [])
    assert "не дал текстового ответа" in out
    assert "переформулировать" in out


# --- _execute -------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_rejects_empty_query():
    ctx = ws.ToolContext(workspace="/tmp", mode=None)  # type: ignore[arg-type]
    result = await ws.web_search_tool.executor({"query": "   "}, ctx)
    assert isinstance(result, ToolExecutionResult)
    assert result.status == ToolStatus.ERROR
    assert result.output == "Ошибка: пустой query"


@pytest.mark.asyncio
async def test_execute_requires_api_key(monkeypatch):
    monkeypatch.setattr(ws.config, "ROUTERAI_API_KEY", "")
    ctx = ws.ToolContext(workspace="/tmp", mode=None, model="active/model")  # type: ignore[arg-type]
    result = await ws._execute({"query": "python"}, ctx)
    assert "ROUTERAI_API_KEY" in result


@pytest.mark.asyncio
async def test_execute_formats_search_result(monkeypatch):
    """Поиск запускается активной моделью, а tool возвращает факты и ссылки."""
    monkeypatch.setattr(ws.config, "ROUTERAI_API_KEY", "test-key")

    seen: dict[str, Any] = {}

    class _Client:
        async def start(self) -> None:
            seen["started"] = True

        async def close(self) -> None:
            seen["closed"] = True

        async def chat(self, messages, **kwargs):
            seen["messages"] = messages
            seen["kwargs"] = kwargs
            annotations = [
                {
                    "type": "url_citation",
                    "url_citation": {"url": "https://a.example", "title": "A"},
                }
            ]
            return _FakeResponse("свежие данные", annotations)

    monkeypatch.setattr(ws, "LLMClient", _Client)
    reported_usage: list[Usage] = []

    async def on_usage(usage: Usage) -> None:
        reported_usage.append(usage)

    ctx = ws.ToolContext(
        workspace="/tmp",
        mode=None,
        session_id="sess-123",
        model="active/model",
        on_usage=on_usage,
    )  # type: ignore[arg-type]

    result = await ws.web_search_tool.executor({"query": "версия python"}, ctx)
    assert isinstance(result, ToolExecutionResult)
    assert result.status == ToolStatus.SUCCESS_NO_CHANGE
    out = result.output

    assert seen["started"] and seen["closed"]
    assert seen["kwargs"]["web_search"] is True
    assert seen["kwargs"]["model"] == "active/model"
    # sticky-роутинг: поиск идёт под session_id основной сессии
    assert seen["kwargs"]["session_id"] == "sess-123"
    assert "версия python" in seen["messages"][0]["content"]
    assert (
        "итоговый ответ сформирует вызывающая модель" in seen["messages"][0]["content"]
    )
    assert "свежие данные" in out
    assert "https://a.example" in out
    assert reported_usage == [Usage(input_tokens=123, output_tokens=7, cost=0.02)]


@pytest.mark.asyncio
async def test_execute_requires_active_model():
    ctx = ws.ToolContext(workspace="/tmp", mode=None)  # type: ignore[arg-type]
    assert "модель" in await ws._execute({"query": "python"}, ctx)


@pytest.mark.asyncio
async def test_execute_prefers_configured_web_search_model(monkeypatch):
    """WEB_SEARCH_MODEL (дешёвая) важнее активной модели сессии."""
    monkeypatch.setattr(ws.config, "ROUTERAI_API_KEY", "test-key")
    monkeypatch.setattr(ws.config, "WEB_SEARCH_MODEL", "anthropic/claude-haiku-5.5")

    seen: dict[str, Any] = {}

    class _Client:
        async def start(self) -> None:
            pass

        async def close(self) -> None:
            pass

        async def chat(self, messages, **kwargs):
            seen["model"] = kwargs["model"]
            return _FakeResponse("факты", [])

    monkeypatch.setattr(ws, "LLMClient", _Client)
    ctx = ws.ToolContext(workspace="/tmp", mode=None, model="anthropic/claude-opus-5")  # type: ignore[arg-type]

    await ws._execute({"query": "python"}, ctx)

    assert seen["model"] == "anthropic/claude-haiku-5.5"


@pytest.mark.asyncio
async def test_execute_reports_chat_failure(monkeypatch):
    """Падение запроса — текст ошибки, а не исключение наружу."""
    monkeypatch.setattr(ws.config, "ROUTERAI_API_KEY", "test-key")

    class _Client:
        async def start(self) -> None:
            pass

        async def close(self) -> None:
            pass

        async def chat(self, messages, **kwargs):
            raise RuntimeError("network down")

    monkeypatch.setattr(ws, "LLMClient", _Client)
    ctx = ws.ToolContext(workspace="/tmp", mode=None, model="active/model")  # type: ignore[arg-type]

    out = await ws._execute({"query": "x"}, ctx)
    assert "Ошибка поиска" in out
    assert "network down" in out


# --- регистрация и режимы -------------------------------------------------


def test_tool_is_registered_and_read_only():
    from agent_coder.tools import build_registry

    registry = build_registry()
    tool = registry.get("web_search")
    assert tool is not None
    assert tool.read_only is True
    assert tool.requires_approval is False


def test_web_search_available_in_plan_and_audit():
    """read_only=True → доступен в PLAN/AUDIT (поиск фактов нужен и там)."""
    from agent_coder.agent.prompts import Mode
    from agent_coder.tools import build_registry

    registry = build_registry()
    for mode in (Mode.PLAN, Mode.AUDIT):
        names = {t.name for t in registry.for_mode(mode)}
        assert "web_search" in names, f"web_search недоступен в {mode}"
