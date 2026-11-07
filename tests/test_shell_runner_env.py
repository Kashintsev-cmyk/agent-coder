"""M3 (2026-11-07): clean_env() + env= в run_shell.

Регресс на finding `8f0cd848`/M3: `run_checks` запускал `pytest` с
наследованием os.environ бота → `*_KEY`/`*_TOKEN` утекали в audited
workspace через RCE (`conftest.py` — теперь `--noconftest`).
"""

from __future__ import annotations

import asyncio

import pytest

from agent_coder.shell_runner import _CLEAN_ENV_KEYS, clean_env, run_shell


def test_clean_env_excludes_secrets(monkeypatch):
    """Whitelist не пропускает *_KEY / *_TOKEN / *_SECRET."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("TELEGRAM_TOKEN", "tg-test")
    monkeypatch.setenv("SOME_SECRET", "s-test")
    monkeypatch.setenv("PATH", "/usr/bin")
    env = clean_env()
    assert "OPENAI_API_KEY" not in env
    assert "TELEGRAM_TOKEN" not in env
    assert "SOME_SECRET" not in env
    assert env.get("PATH") == "/usr/bin"


def test_clean_env_only_whitelist_keys(monkeypatch):
    """Никаких ключей вне whitelist (даже «безобидных»)."""
    monkeypatch.setenv("FOO_PUBLIC", "bar")
    monkeypatch.setenv("HOME", "/home/test")
    env = clean_env()
    assert "FOO_PUBLIC" not in env
    assert env.get("HOME") == "/home/test"
    assert set(env) <= set(_CLEAN_ENV_KEYS)


@pytest.mark.asyncio
async def test_run_shell_passes_env(monkeypatch):
    """run_shell(env=...) пробрасывает env в create_subprocess_shell."""
    captured: dict = {}

    class _FakeProc:
        returncode = 0
        pid = 0

        async def communicate(self):
            return b"", b""

    async def _fake_create(cmd, **kw):
        captured["cmd"] = cmd
        captured["env"] = kw.get("env")
        return _FakeProc()

    monkeypatch.setattr(asyncio, "create_subprocess_shell", _fake_create)
    await run_shell("echo hi", env={"FOO": "bar"})
    assert captured["env"] == {"FOO": "bar"}


@pytest.mark.asyncio
async def test_run_shell_env_clean_excludes_secrets(monkeypatch):
    """End-to-end: run_shell(env=clean_env()) — секреты не видны subprocess."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-leak-test")
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    res = await run_shell("env", env=clean_env(), timeout=5.0, mask=False)
    assert res.returncode == 0, res
    assert "OPENAI_API_KEY" not in res.stdout
    assert "sk-leak-test" not in res.stdout


@pytest.mark.asyncio
async def test_run_shell_env_none_inherits(monkeypatch):
    """Backward-compat: env=None наследует os.environ."""
    monkeypatch.setenv("M3_INHERIT_TEST", "visible-42")
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    res = await run_shell("env", env=None, timeout=5.0, mask=False)
    assert res.returncode == 0, res
    assert "M3_INHERIT_TEST=visible-42" in res.stdout
