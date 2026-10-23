"""Тесты сжатия истории (agent_coder.agent.compression)."""

from __future__ import annotations

from agent_coder.agent.compression import (
    find_cut,
    format_for_summary,
    soft_limit,
)
from agent_coder.config import config


def _mk_session(model="openai/gpt-5.6-luna"):
    from agent_coder.agent.prompts import Mode
    from agent_coder.agent.session import Session

    return Session.new(workspace="/tmp", model=model, mode=Mode.ACT)


# --- soft_limit ---


def test_soft_limit_uses_min_of_context_and_cap():
    s = _mk_session("openai/gpt-5.6-luna")  # context 1_050_000
    # 0.6 * 1_050_000 = 630_000 → берётся cap из config
    assert soft_limit(s) == config.MAX_HISTORY_TOKENS


def test_soft_limit_small_model():
    # kimi-k2.7-code context=262_144 → 0.6*262_144 ≈ 157_286 → cap из config
    s = _mk_session("moonshotai/kimi-k2.7-code")
    assert soft_limit(s) == config.MAX_HISTORY_TOKENS


def test_soft_limit_unknown_model_returns_cap():
    s = _mk_session("no/such-model-xyz")
    assert soft_limit(s) == config.MAX_HISTORY_TOKENS


def test_soft_limit_context_wins_when_smaller_than_cap(monkeypatch):
    """Если 0.6*context < MAX_HISTORY_TOKENS — порог задаёт модель, а не cap."""
    from agent_coder.model_catalog import catalog

    class _TinyModel:
        id = "test/tiny"
        context = 10_000
        name = "Tiny"

    monkeypatch.setattr(catalog, "get", lambda mid: _TinyModel())
    s = _mk_session("test/tiny")
    assert soft_limit(s) == 6_000


# --- find_cut ---


def _msgs(seq: list[str]) -> list[dict]:
    return [{"role": r, "content": ""} for r in seq]


def test_find_cut_short_returns_none():
    assert find_cut(_msgs(["system", "user", "assistant"])) is None


def test_find_cut_prefers_recent_user():
    # 12 сообщений, ищем user/assistant до индекса total-min_recent
    msgs = _msgs(["system"] + ["user"] * 11)
    cut = find_cut(msgs, min_recent=6)
    assert cut is not None
    assert msgs[cut]["role"] == "user"
    assert len(msgs) - cut >= 6


def test_find_cut_skips_tool():
    # Последние 6 — tool, ищем выше
    msgs = _msgs(["system"] + ["user", "assistant"] + ["tool"] * 8)
    cut = find_cut(msgs, min_recent=6)
    assert cut is not None
    assert msgs[cut]["role"] in ("user", "assistant")


def test_find_cut_no_user_or_assistant_in_range():
    # только tool в доступном диапазоне, но system на 0
    msgs = _msgs(["system"] + ["tool"] * 11)
    assert find_cut(msgs, min_recent=6) is None


# --- format_for_summary ---


def test_format_for_summary_basic():
    msgs = [
        {"role": "user", "content": "задача"},
        {"role": "assistant", "content": "думаю"},
        {"role": "tool", "content": "результат"},
    ]
    out = format_for_summary(msgs)
    assert "ПОЛЬЗОВАТЕЛЬ" in out
    assert "АССИСТЕНТ" in out
    assert "[TOOL]" in out


def test_format_for_summary_truncates_tool():
    msgs = [{"role": "tool", "content": "x" * 5000}]
    out = format_for_summary(msgs)
    # [TOOL]: + extracted(2048) — fallback head/tail с маркером
    assert len(out) <= 2100
    assert "обрезано" in out


def test_format_for_summary_assistant_with_tool_calls():
    msgs = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"function": {"name": "read_file"}}],
        }
    ]
    out = format_for_summary(msgs)
    assert "вызвал: read_file" in out


# --- estimate_tokens ---


def test_estimate_tokens_empty():
    from agent_coder.agent.compression import estimate_tokens

    assert estimate_tokens([]) == 0


def test_estimate_tokens_grows_with_text():
    from agent_coder.agent.compression import estimate_tokens

    small = estimate_tokens([{"role": "user", "content": "x" * 400}])
    big = estimate_tokens([{"role": "user", "content": "x" * 4000}])
    assert small == 100  # 400 символов / 4
    assert big == 1000
    assert big > small


def test_estimate_tokens_counts_tool_calls():
    from agent_coder.agent.compression import estimate_tokens

    plain = estimate_tokens([{"role": "assistant", "content": "hi"}])
    with_tc = estimate_tokens(
        [
            {
                "role": "assistant",
                "content": "hi",
                "tool_calls": [
                    {"function": {"name": "run_command", "arguments": "{}"}}
                ],
            }
        ]
    )
    assert with_tc > plain


def test_estimate_tokens_handles_multimodal_content():
    from agent_coder.agent.compression import estimate_tokens

    n = estimate_tokens(
        [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "x" * 80},
                    {"type": "image_url", "image_url": {"url": "data:..."}},
                ],
            }
        ]
    )
    assert n > 0


# --- compress (с mock-клиентом) ---

import pytest

from agent_coder.agent.compression import compress
from agent_coder.llm.types import Usage


class _FakeMessage:
    def __init__(self, content: str):
        self.content = content


class _FakeResponse:
    def __init__(self, content: str, usage: Usage):
        self.message = _FakeMessage(content)
        self.usage = usage


class _FakeClient:
    def __init__(self, reply: str = "кратко"):
        self.reply = reply
        self.calls: list[dict] = []

    async def chat(self, messages, **kwargs):
        self.calls.append({"messages": messages, "kwargs": kwargs})
        return _FakeResponse(
            self.reply,
            Usage(input_tokens=100, output_tokens=20, cost=0.01),
        )


@pytest.mark.asyncio
async def test_compress_replaces_old():
    s = _mk_session()
    # system + 14 user/assistant пар
    s.messages = [{"role": "system", "content": "SYSTEM"}]
    for i in range(14):
        s.messages.append({"role": "user", "content": f"задача {i}"})
        s.messages.append({"role": "assistant", "content": f"ответ {i}"})

    before = len(s.messages)
    pre_cost = s.total_usage.cost
    pre_count = s.request_count
    client = _FakeClient(reply="СЖАТО")
    comp = await compress(s, client)
    assert comp is not None
    assert comp.tokens_before > 0
    assert comp.tokens_after < comp.tokens_before
    assert comp.messages_before == before
    assert comp.messages_after == len(s.messages)
    assert comp.summary_count == 1

    # system на месте
    assert s.messages[0]["role"] == "system"
    assert s.messages[0]["content"] == "SYSTEM"
    # второй элемент — резюме
    assert s.messages[1]["role"] == "user"
    assert "СЖАТО" in s.messages[1]["content"]
    assert "сжатие #1" in s.messages[1]["content"]
    # стало меньше
    assert len(s.messages) < before
    assert s.summary_count == 1
    assert s.last_input_tokens == 0
    # B11.x: cost summary-вызова учтён в session.total_usage.
    # Инварианта: add_usage вызван ровно один раз (request_count +1),
    # cost вырос. Точная величина cost — свойство мока (resp.usage),
    # не compress, поэтому не привязываемся к 0.01.
    assert s.total_usage.cost > pre_cost
    assert s.request_count == pre_count + 1


@pytest.mark.asyncio
async def test_compress_short_history_returns_false():
    s = _mk_session()
    s.messages = [{"role": "system", "content": "S"}]
    s.messages.append({"role": "user", "content": "hi"})
    s.messages.append({"role": "assistant", "content": "ok"})

    client = _FakeClient()
    ok = await compress(s, client)
    assert ok is None
    assert len(client.calls) == 0  # LLM не вызвали


@pytest.mark.asyncio
async def test_compress_empty_reply_returns_false():
    s = _mk_session()
    s.messages = [{"role": "system", "content": "S"}]
    for i in range(14):
        s.messages.append({"role": "user", "content": f"u{i}"})
        s.messages.append({"role": "assistant", "content": f"a{i}"})

    client = _FakeClient(reply="   ")  # пусто после strip
    ok = await compress(s, client)
    assert ok is None
    assert s.summary_count == 0


@pytest.mark.asyncio
async def test_compress_llm_error_returns_false():
    s = _mk_session()
    s.messages = [{"role": "system", "content": "S"}]
    for i in range(14):
        s.messages.append({"role": "user", "content": f"u{i}"})
        s.messages.append({"role": "assistant", "content": f"a{i}"})

    class _BoomClient:
        async def chat(self, *a, **kw):
            raise RuntimeError("boom")

    ok = await compress(s, _BoomClient())
    assert ok is None
    assert s.summary_count == 0


@pytest.mark.asyncio
async def test_compress_preserves_recent():
    s = _mk_session()
    s.messages = [{"role": "system", "content": "S"}]
    for i in range(10):
        s.messages.append({"role": "user", "content": f"u{i}"})
        s.messages.append({"role": "assistant", "content": f"a{i}"})
    # Последние два — контрольные (recent)
    s.messages.append({"role": "user", "content": "СВЕЖАЯ_ЗАДАЧА"})
    s.messages.append({"role": "assistant", "content": "СВЕЖИЙ_ОТВЕТ"})

    client = _FakeClient(reply="SUMMARY")
    await compress(s, client)

    # Хвост — те же свежие
    assert s.messages[-2]["content"] == "СВЕЖАЯ_ЗАДАЧА"
    assert s.messages[-1]["content"] == "СВЕЖИЙ_ОТВЕТ"


# --- новые защиты (update-режим, huge-recent, cooldown) ---

import time as _time

from agent_coder.agent import compression as _comp


@pytest.mark.asyncio
async def test_compress_updates_prev_summary_separately():
    """Предыдущее резюме передаётся отдельно, а не в общем транскрипте."""
    s = _mk_session()
    s.messages = [{"role": "system", "content": "S"}]
    s.messages.append(
        {
            "role": "user",
            "content": "[Резюме предыдущего контекста, сжатие #1]\nСТАРОЕ_РЕЗЮМЕ",
        }
    )
    s.summary_count = 1  # одно сжатие уже было
    for i in range(14):
        s.messages.append({"role": "user", "content": f"u{i}"})
        s.messages.append({"role": "assistant", "content": f"a{i}"})

    client = _FakeClient(reply="ОБНОВЛЁННОЕ")
    comp = await compress(s, client)
    assert comp is not None

    sent = client.calls[0]
    # Используется update-промпт
    assert "ПРЕДЫДУЩЕЕ РЕЗЮМЕ" in sent["messages"][1]["content"]
    assert "СТАРОЕ_РЕЗЮМЕ" in sent["messages"][1]["content"]
    assert "НОВЫЕ СОБЫТИЯ" in sent["messages"][1]["content"]
    # Старое резюме не попало в транскрипт второй раз
    transcript_part = sent["messages"][1]["content"].split("НОВЫЕ СОБЫТИЯ ДИАЛОГА:")[1]
    assert "СТАРОЕ_РЕЗЮМЕ" not in transcript_part
    # Новый счётчик сжатия
    assert "сжатие #2" in s.messages[1]["content"]


@pytest.mark.asyncio
async def test_compress_skips_when_old_share_tiny():
    """Огромный recent-блок: сжатие почти не сэкономит — пропускаем."""
    s = _mk_session()
    s.messages = [{"role": "system", "content": "S"}]
    # Маленькая старая часть
    for i in range(10):
        s.messages.append({"role": "user", "content": f"u{i}"})
        s.messages.append({"role": "assistant", "content": f"a{i}"})
    # Огромный recent-блок (последние 6 сообщений)
    s.messages.append({"role": "user", "content": "x" * 100_000})
    s.messages.append({"role": "assistant", "content": "y" * 100_000})
    s.messages.append({"role": "user", "content": "x" * 100_000})
    s.messages.append({"role": "assistant", "content": "y" * 100_000})
    s.messages.append({"role": "user", "content": "x" * 100_000})
    s.messages.append({"role": "assistant", "content": "y" * 100_000})

    client = _FakeClient(reply="НЕ_ДОЛЖЕН_ВЫЗВАТЬСЯ")
    comp = await compress(s, client)
    assert comp is None
    assert len(client.calls) == 0


@pytest.mark.asyncio
async def test_compress_cooldown_after_failures(monkeypatch):
    """После 2 неудач подряд — cooldown, LLM не дёргаем."""
    monkeypatch.setattr(_comp, "_COOLDOWN_SECONDS", 60.0)
    s = _mk_session()
    s.messages = [{"role": "system", "content": "S"}]
    for i in range(14):
        s.messages.append({"role": "user", "content": f"u{i}"})
        s.messages.append({"role": "assistant", "content": f"a{i}"})

    class _BoomClient:
        def __init__(self):
            self.calls = 0

        async def chat(self, *a, **kw):
            self.calls += 1
            raise RuntimeError("boom")

    client = _BoomClient()
    assert await compress(s, client) is None
    assert await compress(s, client) is None
    assert client.calls == 2
    # Третий вызов — в cooldown, LLM не вызывается
    assert await compress(s, client) is None
    assert client.calls == 2

    # Успех сбрасывает cooldown
    _comp._register_success(s.id)
    ok_client = _FakeClient(reply="ok")
    assert await compress(s, ok_client) is not None


def test_cooldown_expires(monkeypatch):
    monkeypatch.setattr(_comp, "_COOLDOWN_SECONDS", 60.0)
    _comp._register_failure("sess-x")
    _comp._register_failure("sess-x")
    assert _comp._cooldown_active("sess-x")
    # Симулируем истечение
    _comp._fail_until["sess-x"] = _time.monotonic() - 1
    assert not _comp._cooldown_active("sess-x")
    _comp._register_success("sess-x")


def test_format_for_summary_skips_prev_summary():
    msgs = [
        {
            "role": "user",
            "content": "[Резюме предыдущего контекста, сжатие #1]\nстарое",
        },
        {"role": "user", "content": "живой вопрос"},
    ]
    out = format_for_summary(msgs)
    assert "старое" not in out
    assert "живой вопрос" in out


# --- архивация при сжатии (RAG-lite) ---

from agent_coder.agent import history_archive


@pytest.fixture
def sessions_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(
        type(history_archive.config), "SESSIONS_DIR", tmp_path, raising=False
    )
    return tmp_path


@pytest.mark.asyncio
async def test_compress_archives_old_messages(sessions_dir):
    s = _mk_session()
    s.messages = [{"role": "system", "content": "S"}]
    for i in range(14):
        s.messages.append({"role": "user", "content": f"уникальный вопрос {i}"})
        s.messages.append({"role": "assistant", "content": f"ответ {i}"})

    await compress(s, _FakeClient(reply="SUMMARY"))

    archived = history_archive.load(s.id)
    # 28 старых − 6 recent = 22 заархивировано
    assert len(archived) == 22
    assert any("уникальный вопрос 0" in str(m.get("content")) for m in archived)
    # Свежие (recent) в архив не попали
    assert not any("уникальный вопрос 13" in str(m.get("content")) for m in archived)


@pytest.mark.asyncio
async def test_compress_archive_failure_not_fatal(sessions_dir, monkeypatch):
    """Ошибка записи архива не ломает сжатие."""
    s = _mk_session()
    s.messages = [{"role": "system", "content": "S"}]
    for i in range(14):
        s.messages.append({"role": "user", "content": f"u{i}"})
        s.messages.append({"role": "assistant", "content": f"a{i}"})

    def _boom(sid, msgs):
        raise OSError("disk full")

    monkeypatch.setattr(history_archive, "append_messages", _boom)
    comp = await compress(s, _FakeClient(reply="SUMMARY"))
    assert comp is not None
    assert "SUMMARY" in s.messages[1]["content"]


# --- точная оценка токенов (кириллица vs латиница) ---


def test_estimate_tokens_cyrillic_denser():
    from agent_coder.agent.compression import estimate_tokens

    cyr = estimate_tokens([{"role": "user", "content": "а" * 200}])
    lat = estimate_tokens([{"role": "user", "content": "a" * 200}])
    # Кириллица ~2 симв/токен → 100; латиница ~4 → 50
    assert cyr == 100
    assert lat == 50
    assert cyr > lat


def test_estimate_tokens_mixed():
    from agent_coder.agent.compression import estimate_tokens

    # 100 кириллических + 100 латинских символов
    n = estimate_tokens([{"role": "user", "content": "а" * 100 + "a" * 100}])
    assert n == 75  # 100/2 + 100/4


# --- _validate_summary: защита от выдуманных SHA/путей ---

from agent_coder.agent.compression import _validate_summary


def test_validate_summary_flags_invented_sha() -> None:
    summary = "### Сделано\n- правил models.py, коммит deadbeef1"
    transcript = "ПОЛЬЗОВАТЕЛЬ:\nпоправь models.py"
    out = _validate_summary(summary, transcript)
    assert "deadbeef1 (не подтверждено)" in out
    assert "models.py (не подтверждено)" not in out


def test_validate_summary_keeps_valid_sha() -> None:
    summary = "коммит abc1234"
    transcript = "git commit: abc1234 done"
    assert _validate_summary(summary, transcript) == summary


def test_validate_summary_flags_invented_path() -> None:
    summary = "правил agent_coder/models.py и config.py"
    transcript = "поправь config.py"
    out = _validate_summary(summary, transcript)
    assert "agent_coder/models.py (не подтверждено)" in out
    assert "config.py (не подтверждено)" not in out


def test_validate_summary_keeps_valid_path() -> None:
    summary = "правил agent_coder/models.py"
    transcript = "[TOOL]: patched agent_coder/models.py"
    assert _validate_summary(summary, transcript) == summary


def test_validate_summary_idempotent() -> None:
    summary = "коммит deadbeef1"
    transcript = "просто текст без sha"
    once = _validate_summary(summary, transcript)
    twice = _validate_summary(once, transcript)
    assert once == twice
    assert once.count("(не подтверждено)") == 1


def test_validate_summary_ignores_plain_numbers() -> None:
    summary = "context 1048576"
    transcript = "x"
    assert _validate_summary(summary, transcript) == summary


def test_validate_summary_ignores_dates_and_fractions() -> None:
    assert _validate_summary("дата 18/10/2026", "x") == "дата 18/10/2026"
    assert _validate_summary("доля 1/2", "x") == "доля 1/2"


def test_validate_summary_already_marked_not_doubled() -> None:
    summary = "deadbeef1 (не подтверждено)"
    transcript = "просто текст"
    assert _validate_summary(summary, transcript) == summary


@pytest.mark.asyncio
async def test_compress_validates_summary_against_transcript() -> None:
    """Сквозной: выдуманный SHA в ответе модели -> помечен в session."""
    s = _mk_session()
    s.messages = [{"role": "system", "content": "S"}]
    for i in range(14):
        s.messages.append({"role": "user", "content": f"u{i}"})
        s.messages.append({"role": "assistant", "content": f"a{i}"})

    client = _FakeClient(reply="### Сделано\n- коммит deadbeef1")
    comp = await compress(s, client)
    assert comp is not None
    assert "deadbeef1 (не подтверждено)" in s.messages[1]["content"]


@pytest.mark.asyncio
async def test_compress_empty_reply_still_counts_usage():
    """Пустой ответ LLM → compress вернул None, но cost учтён.

    Деньги потрачены на chat-вызов, даже если summary непригоден.
    Контракт: add_usage должен стоять ДО `if not text: return None`.
    """
    s = _mk_session()
    s.messages = [{"role": "system", "content": "SYSTEM"}]
    for i in range(14):
        s.messages.append({"role": "user", "content": f"задача {i}"})
        s.messages.append({"role": "assistant", "content": f"ответ {i}"})
    pre_cost = s.total_usage.cost
    pre_count = s.request_count
    client = _FakeClient(reply="")
    comp = await compress(s, client)
    assert comp is None
    assert s.total_usage.cost > pre_cost
    assert s.request_count == pre_count + 1
