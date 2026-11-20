"""Тесты сборки kwargs для chat.completions.create (_build_request_kwargs)."""

from __future__ import annotations

from agent_coder.llm.client import _apply_cache_anchor, _build_request_kwargs


def test_minimal():
    kw = _build_request_kwargs("m", [{"role": "user", "content": "x"}])
    assert kw == {"model": "m", "messages": [{"role": "user", "content": "x"}]}


def test_with_tools_and_tool_choice():
    kw = _build_request_kwargs(
        "m",
        [],
        tools=[{"type": "function"}],
        tool_choice="auto",
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
    assert kw["plugins"] == [{"id": "web", "engine": "native", "max_results": 5}]


def test_web_search_off_no_plugins():
    kw = _build_request_kwargs("m", [])
    assert "plugins" not in kw
    kw2 = _build_request_kwargs("m", [], web_search=False)
    assert "plugins" not in kw2


def test_all_options_together():
    kw = _build_request_kwargs(
        "m",
        [],
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
        "m",
        [],
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


def test_temperature_default_passed_by_loop():
    """Проверка, что loop передаёт temperature=config.TEMPERATURE.

    Здесь — просто sanity-check на чистую функцию; фактическая передача
    проверяется интеграционно через loop.run.
    """
    from agent_coder.config import config

    kw = _build_request_kwargs("m", [], temperature=config.TEMPERATURE)
    assert kw["temperature"] == config.TEMPERATURE
    assert 0.0 <= kw["temperature"] <= 2.0


def test_session_id_sets_extra_headers():
    """Sticky-роутинг: session_id -> extra_headers['x-session-id']."""
    kw = _build_request_kwargs("m", [], session_id="sess-42")
    assert kw["extra_headers"] == {"x-session-id": "sess-42"}


def test_session_id_empty_skips_headers():
    kw = _build_request_kwargs("m", [])
    assert "extra_headers" not in kw
    kw2 = _build_request_kwargs("m", [], session_id="")
    assert "extra_headers" not in kw2


def test_session_id_keeps_extra_body():
    """extra_headers и extra_body (top_k у qwen) не перетирают друг друга."""
    kw = _build_request_kwargs("qwen/qwen3.7-flash", [], session_id="s1")
    assert kw["extra_body"]["top_k"] == 20
    assert kw["extra_headers"] == {"x-session-id": "s1"}


# --- cache_anchor: точка кэша на последнем СТАБИЛЬНОМ сообщении --------
#
# Регрессия 2026-11-20: top-level cache_control ставит точку в КОНЕЦ запроса.
# Волатильный хвост [WORK_STATE] стал последним сообщением → каждый turn
# уникален → 27/27 запросов cache=0. Якорь должен стоять ДО хвоста.


def _msgs():
    return [
        {"role": "system", "content": "S stable"},
        {"role": "user", "content": "task"},
        {"role": "assistant", "content": "ok"},
        {"role": "user", "content": "res"},
        {"role": "user", "content": "[WORK_STATE] volatile"},
    ]


def test_cache_anchor_skips_volatile_tail():
    """stable_count=4: якорь на индексе 3, хвост (4) не тронут."""
    out = _apply_cache_anchor(_msgs(), stable_count=4)
    assert out[3]["content"] == [
        {"type": "text", "text": "res", "cache_control": {"type": "ephemeral"}}
    ]
    assert out[4]["content"] == "[WORK_STATE] volatile"
    assert "cache_control" not in str(out[4])


def test_cache_anchor_does_not_mutate_input():
    """История сессии (session.messages) не должна мутировать."""
    src = _msgs()
    snapshot = [dict(m) for m in src]
    _apply_cache_anchor(src, stable_count=4)
    assert src == snapshot
    assert isinstance(src[3]["content"], str)


def test_cache_anchor_none_covers_whole_request():
    """stable_count=None (хвоста нет) — якорь на последнем сообщении."""
    msgs = [{"role": "system", "content": "S"}, {"role": "user", "content": "u"}]
    out = _apply_cache_anchor(msgs, stable_count=None)
    assert out[-1]["content"][0]["cache_control"] == {"type": "ephemeral"}
    assert isinstance(out[0]["content"], str)


def test_cache_anchor_skips_empty_content():
    """assistant с tool_calls: content=None — кэшировать нечего, берём выше."""
    msgs = [
        {"role": "system", "content": "S"},
        {"role": "user", "content": "task"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "1"}]},
        {"role": "user", "content": "[WORK_STATE] x"},
    ]
    out = _apply_cache_anchor(msgs, stable_count=3)
    assert isinstance(out[1]["content"], list)
    assert out[1]["content"][0]["cache_control"] == {"type": "ephemeral"}
    assert out[2]["content"] is None


def test_cache_anchor_multimodal_block():
    """Список блоков: cache_control — на последний text-блок."""
    msgs = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "t"},
                {"type": "image_url", "image_url": {"url": "data:..."}},
            ],
        },
        {"role": "user", "content": "[WORK_STATE] v"},
    ]
    out = _apply_cache_anchor(msgs, stable_count=1)
    blocks = out[0]["content"]
    assert blocks[0]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in blocks[1]
    # исходные блоки не мутированы
    assert "cache_control" not in msgs[0]["content"][0]


def test_cache_anchor_empty_and_zero():
    """Пустой вход и stable_count<=0 — no-op, без падения."""
    assert _apply_cache_anchor([], stable_count=3) == []
    msgs = [{"role": "user", "content": "x"}]
    assert _apply_cache_anchor(msgs, stable_count=0) is msgs


def test_cache_anchor_is_idempotent():
    """Повторный вызов не дублирует cache_control в блоке."""
    once = _apply_cache_anchor(_msgs(), stable_count=4)
    twice = _apply_cache_anchor(once, stable_count=4)
    assert twice[3]["content"] == once[3]["content"]
