"""Тест: --max-iterations не затирает env-лимиты, если не задан явно."""

from __future__ import annotations

import sys
from unittest import mock

import pytest

from agent_coder import __main__ as cli
from agent_coder.config import config


@pytest.fixture
def _save_limits():
    """Сохранить/восстановить глобальные лимиты (они мутируются в main)."""
    old = (
        config.MAX_ITERATIONS_PLAN,
        config.MAX_ITERATIONS_ACT,
        config.MAX_ITERATIONS_AUDIT,
    )
    yield
    (
        config.MAX_ITERATIONS_PLAN,
        config.MAX_ITERATIONS_ACT,
        config.MAX_ITERATIONS_AUDIT,
    ) = old


def test_no_flag_keeps_env_limits(_save_limits, monkeypatch):
    """Без --max-iterations лимиты из env не трогаются."""
    config.MAX_ITERATIONS_PLAN = 30
    config.MAX_ITERATIONS_ACT = 50
    config.MAX_ITERATIONS_AUDIT = 100

    with mock.patch.object(sys, "argv", ["agent-coder"]):
        args = cli.parse_args()
    assert args.max_iterations is None

    # симулируем условие из main
    if args.max_iterations is not None:
        config.MAX_ITERATIONS_PLAN = args.max_iterations
        config.MAX_ITERATIONS_ACT = args.max_iterations
        config.MAX_ITERATIONS_AUDIT = args.max_iterations

    assert config.MAX_ITERATIONS_PLAN == 30
    assert config.MAX_ITERATIONS_ACT == 50
    assert config.MAX_ITERATIONS_AUDIT == 100


def test_explicit_flag_overrides_all(_save_limits, monkeypatch):
    """С --max-iterations N — N применяется ко всем режимам."""
    config.MAX_ITERATIONS_PLAN = 30
    config.MAX_ITERATIONS_ACT = 50
    config.MAX_ITERATIONS_AUDIT = 100

    with mock.patch.object(sys, "argv", ["agent-coder", "--max-iterations", "7"]):
        args = cli.parse_args()
    assert args.max_iterations == 7

    if args.max_iterations is not None:
        config.MAX_ITERATIONS_PLAN = args.max_iterations
        config.MAX_ITERATIONS_ACT = args.max_iterations
        config.MAX_ITERATIONS_AUDIT = args.max_iterations

    assert config.MAX_ITERATIONS_PLAN == 7
    assert config.MAX_ITERATIONS_ACT == 7
    assert config.MAX_ITERATIONS_AUDIT == 7
