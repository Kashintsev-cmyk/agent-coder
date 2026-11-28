from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from agent_coder.agent.modes import Mode
from agent_coder.telegram.handlers import task as task_handlers


@pytest.mark.asyncio
async def test_continue_callback_rejects_stale_task_and_starts_matching_task(
    monkeypatch,
) -> None:
    session = SimpleNamespace(id="session-1", task_state={"id": "task-1"})
    monkeypatch.setattr(task_handlers.Session, "load", lambda sid: session)
    active_sessions: list[tuple[int, str]] = []
    monkeypatch.setattr(
        "agent_coder.telegram.state.set_active_session",
        lambda chat_id, sid: active_sessions.append((chat_id, sid)),
    )
    started: list[tuple[object, ...]] = []
    bot = object()

    async def fake_run_task(*args, **kwargs) -> None:
        started.append((*args, kwargs))

    monkeypatch.setattr(task_handlers, "_run_task", fake_run_task)
    task_handlers._running.clear()
    answers: list[tuple[str, bool]] = []
    edits: list[object] = []

    class _Message:
        chat = SimpleNamespace(id=42)

        async def edit_reply_markup(self, *, reply_markup):
            edits.append(reply_markup)

    class _Query:
        message = _Message()

        def __init__(self, data: str) -> None:
            self.data = data

        async def answer(self, text: str, *, show_alert: bool = False) -> None:
            answers.append((text, show_alert))

    try:
        await task_handlers.cb_continue_task(
            _Query("continue_task:session-1:old-task"), SimpleNamespace()
        )
        assert answers[-1] == ("Эта задача уже закрыта или изменена", True)
        assert not started

        await task_handlers.cb_continue_task(
            _Query("continue_task:session-1:task-1"), bot
        )
        running = task_handlers._running[42]
        await running

        assert answers[-1] == ("Продолжаю…", False)
        assert active_sessions == [(42, "session-1")]
        assert edits == [None]
        assert started == [
            (bot, 42, session, "продолжи", {"images": None, "continuation": True})
        ]
    finally:
        task_handlers._running.clear()


class _FakeBot:
    def __init__(self) -> None:
        self.messages: list[tuple[int, str, dict[str, object]]] = []

    async def send_message(self, chat_id: int, text: str, **kwargs):
        self.messages.append((chat_id, text, kwargs))
        return SimpleNamespace(message_id=1)


class _FakeRunner:
    def __init__(self, *args) -> None:
        pass

    def events(self):
        return SimpleNamespace()

    async def flush(self) -> None:
        pass


class _FakeClient:
    async def start(self) -> None:
        pass

    async def close(self) -> None:
        pass


@pytest.mark.asyncio
async def test_auto_continue_runs_next_wave_then_shows_continue_button(
    monkeypatch,
) -> None:
    monkeypatch.setattr(task_handlers, "LLMClient", _FakeClient)
    monkeypatch.setattr(task_handlers, "_TaskRunner", _FakeRunner)
    monkeypatch.setattr(task_handlers.config, "MAX_AUTO_CONTINUE", 1)
    results = [
        SimpleNamespace(
            watchdog_triggered=False,
            provider_error=False,
            cost_limit_reached=False,
            no_progress=False,
            limit_reached=True,
            iterations_used=2,
            files_changed=True,
        ),
        SimpleNamespace(
            watchdog_triggered=False,
            provider_error=False,
            cost_limit_reached=False,
            no_progress=False,
            limit_reached=True,
            iterations_used=2,
            files_changed=True,
        ),
    ]
    calls: list[tuple[str, bool]] = []

    class _Loop:
        def __init__(self, **kwargs) -> None:
            pass

        async def run(self, text, *, images, continuation):
            calls.append((text, continuation))
            return results.pop(0)

    monkeypatch.setattr(task_handlers, "AgentLoop", _Loop)
    session = SimpleNamespace(
        id="session-1",
        task_state={"id": "task-1"},
        mode=Mode.ACT,
        yolo=False,
        save=lambda: None,
    )
    bot = _FakeBot()

    await task_handlers._run_task(bot, 42, session, "initial request")

    assert calls == [("initial request", False), ("продолжи", True)]
    assert session.task_state["auto_continue"] == 1
    assert any("Автопродолжений: 1/1" in text for _, text, _ in bot.messages)
    assert task_handlers._running.get(42) is None


@pytest.mark.asyncio
async def test_auto_continue_stops_when_wave_has_no_progress(monkeypatch) -> None:
    monkeypatch.setattr(task_handlers, "LLMClient", _FakeClient)
    monkeypatch.setattr(task_handlers, "_TaskRunner", _FakeRunner)
    monkeypatch.setattr(task_handlers.config, "MAX_AUTO_CONTINUE", 3)
    calls: list[tuple[str, bool]] = []

    class _Loop:
        def __init__(self, **kwargs) -> None:
            pass

        async def run(self, text, *, images, continuation):
            calls.append((text, continuation))
            return SimpleNamespace(
                watchdog_triggered=False,
                provider_error=False,
                cost_limit_reached=False,
                no_progress=False,
                limit_reached=True,
                iterations_used=200,
                files_changed=False,
                new_work_state_facts=False,
                successful_checks=0,
            )

    monkeypatch.setattr(task_handlers, "AgentLoop", _Loop)
    session = SimpleNamespace(
        id="session-2",
        task_state={"id": "task-2"},
        mode=Mode.ACT,
        yolo=False,
        save=lambda: None,
    )
    bot = _FakeBot()

    await task_handlers._run_task(bot, 42, session, "initial request")

    assert calls == [("initial request", False)]
    assert "auto_continue" not in session.task_state
    assert any("не обнаружено изменений файлов" in text for _, text, _ in bot.messages)


@pytest.mark.asyncio
async def test_cancellation_closes_task_and_cleans_running_state(monkeypatch) -> None:
    monkeypatch.setattr(task_handlers, "LLMClient", _FakeClient)
    monkeypatch.setattr(task_handlers, "_TaskRunner", _FakeRunner)
    monkeypatch.setattr(task_handlers, "_shutting_down", False)

    class _Loop:
        def __init__(self, **kwargs) -> None:
            pass

        async def run(self, *args, **kwargs):
            raise asyncio.CancelledError

    monkeypatch.setattr(task_handlers, "AgentLoop", _Loop)
    ended: list[bool] = []
    saved: list[bool] = []
    session = SimpleNamespace(
        task_state={"id": "task-1"},
        mode=Mode.ACT,
        yolo=False,
        end_task=lambda: ended.append(True),
        save=lambda: saved.append(True),
    )
    bot = _FakeBot()
    task_handlers._running[42] = asyncio.current_task()

    try:
        with pytest.raises(asyncio.CancelledError):
            await task_handlers._run_task(bot, 42, session, "request")

        assert ended == [True]
        assert saved == [True]
        assert any(text == "⏹ Отменено" for _, text, _ in bot.messages)
        assert 42 not in task_handlers._running
    finally:
        task_handlers._running.clear()
