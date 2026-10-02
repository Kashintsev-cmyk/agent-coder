"""Тесты сборки kwargs для chat.completions.create (_build_request_kwargs)."""
from __future__ import annotations

from agent_coder.llm.client import _build_request_kwargs


def test_minimal():
    kw = _build_request_kwargs("m", [{"role": "user", "content": "x"}])
    assert kw == {"model": "m", "messages": [{"role": "user", "content": "x"}]}


def test_with_tools_and_tool_choice():
    kw = _build_request_kwargs(
        "m", [], tools=[{"type": "function"}], tool_choice="auto",
    )
    assert kw["tools"] == [{"type": "function"}]
    assert kw["tool_choice"] == "auto"


def test_temperature_zero_is_passed():
    # 0.0 — валидное значение, не должно теряться как falsy
    kw = _build_request_kwargs("m", [], temperature=0.0)
    assert kw["temperature"] == 0.0


def test_reasoning_effort_medium():
    kw = _build_request_kwargs("m", [], reasoning_effort="medium")
    assert kw["reasoning_effort"] == "medium"


def test_reasoning_effort_none_skipped():
    kw = _build_request_kwargs("m", [], reasoning_effort="none")
    assert "reasoning_effort" not in kw


def test_reasoning_effort_empty_skipped():
    kw = _build_request_kwargs("m", [], reasoning_effort="")
    assert "reasoning_effort" not in kw


def test_web_search_on():
    kw = _build_request_kwargs("m", [], web_search=True)
    assert kw["plugins"] == [
        {"id": "web", "engine": "native", "max_results": 5}
    ]


def test_web_search_off_no_plugins():
    kw = _build_request_kwargs("m", [])
    assert "plugins" not in kw
    kw2 = _build_request_kwargs("m", [], web_search=False)
    assert "plugins" not in kw2


def test_all_options_together():
    kw = _build_request_kwargs(
        "m", [],
        tools=[{"type": "function"}],
        tool_choice="required",
        temperature=0.2,
        reasoning_effort="high",
        web_search=True,
    )
    assert kw["model"] == "m"
    assert kw["tools"] == [{"type": "function"}]
    assert kw["tool_choice"] == "required"
    assert kw["temperature"] == 0.2
    assert kw["reasoning_effort"] == "high"
    assert kw["plugins"][0]["id"] == "web"


def test_service_tier_default_skipped():
    kw = _build_request_kwargs("m", [], service_tier="default")
    assert "service_tier" not in kw


def test_service_tier_empty_skipped():
    kw = _build_request_kwargs("m", [], service_tier="")
    assert "service_tier" not in kw


def test_service_tier_none_skipped():
    kw = _build_request_kwargs("m", [])
    assert "service_tier" not in kw


def test_service_tier_flex_passed():
    kw = _build_request_kwargs("m", [], service_tier="flex")
    assert kw["service_tier"] == "flex"


def test_service_tier_priority_passed():
    kw = _build_request_kwargs("m", [], service_tier="priority")
    assert kw["service_tier"] == "priority"


def test_all_with_service_tier():
    kw = _build_request_kwargs(
        "m", [],
        reasoning_effort="medium",
        web_search=True,
        service_tier="flex",
    )
    assert kw["reasoning_effort"] == "medium"
    assert kw["plugins"][0]["id"] == "web"
    assert kw["service_tier"] == "flex"


def test_verbosity_skipped_when_empty():
    kw = _build_request_kwargs("m", [])
    assert "verbosity" not in kw
    kw2 = _build_request_kwargs("m", [], verbosity="")
    assert "verbosity" not in kw2


def test_verbosity_passed():
    kw = _build_request_kwargs("m", [], verbosity="low")
    assert kw["verbosity"] == "low"


def test_verbosity_max():
    kw = _build_request_kwargs("m", [], verbosity="max")
    assert kw["verbosity"] == "max"
