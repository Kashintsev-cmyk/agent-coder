"""Тесты карусели /sessions: пагинация keyboards.sessions_keyboard."""

from __future__ import annotations

from agent_coder.agent.session import Mode, Session
from agent_coder.telegram import keyboards


def _session(i: int) -> Session:
    s = Session.new(workspace="/tmp", model="test/model", mode=Mode.ACT)
    s.id = f"s{i:07d}"
    s.title = f"session {i}"
    return s


def _sessions(n: int) -> list[Session]:
    return [_session(i) for i in range(n)]


def test_single_page_when_few_sessions():
    kb = keyboards.sessions_keyboard(_sessions(5))
    rows = kb.inline_keyboard
    # 5 сессий + "Назад", без навигации
    assert len(rows) == 6
    assert rows[0][0].callback_data == "open:s0000000"
    assert rows[-1][0].callback_data == "back"


def test_pagination_buttons_and_slice():
    sessions = _sessions(20)
    kb = keyboards.sessions_keyboard(sessions, page=1)
    rows = kb.inline_keyboard
    # 8 сессий + навигация + "Назад"
    session_rows = rows[:-2]
    assert len(session_rows) == keyboards.SESSIONS_PER_PAGE
    # Вторая страница: индексы 8..15
    assert session_rows[0][0].callback_data == "open:s0000008"
    assert session_rows[-1][0].callback_data == "open:s0000015"
    # Навигация: ◀ 2/3 ▶
    nav = rows[-2]
    texts = [b.text for b in nav]
    assert texts == ["◀", "2/3", "▶"]
    assert [b.callback_data for b in nav] == [
        "sessions_page:0",
        "noop",
        "sessions_page:2",
    ]


def test_first_page_no_prev_button():
    kb = keyboards.sessions_keyboard(_sessions(20), page=0)
    nav = kb.inline_keyboard[-2]
    assert [b.text for b in nav] == ["1/3", "▶"]
    assert nav[0].callback_data == "noop"
    assert nav[1].callback_data == "sessions_page:1"


def test_last_page_no_next_button():
    kb = keyboards.sessions_keyboard(_sessions(20), page=2)
    nav = kb.inline_keyboard[-2]
    assert [b.text for b in nav] == ["◀", "3/3"]


def test_page_out_of_range_clamped():
    sessions = _sessions(20)
    # Страница за пределами — зажимается на последней (◀ 3/3)
    kb = keyboards.sessions_keyboard(sessions, page=99)
    nav = kb.inline_keyboard[-2]
    assert [b.text for b in nav] == ["◀", "3/3"]
    # Отрицательная — на первой (1/3 ▶)
    kb2 = keyboards.sessions_keyboard(sessions, page=-5)
    nav2 = kb2.inline_keyboard[-2]
    assert [b.text for b in nav2] == ["1/3", "▶"]


def test_callback_data_length_limit():
    # callback_data ≤ 64 байт (ограничение Telegram)
    kb = keyboards.sessions_keyboard(_sessions(200), page=12)
    for row in kb.inline_keyboard:
        for b in row:
            assert len(b.callback_data.encode()) <= 64
