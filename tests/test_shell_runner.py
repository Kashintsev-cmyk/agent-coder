"""Тесты shell_runner.run_shell."""

from __future__ import annotations

import pytest

from agent_coder.shell_runner import ShellResult, run_shell


@pytest.mark.asyncio
async def test_simple_success():
    r = await run_shell("echo hello", timeout=5)
    assert r.ok
    assert r.returncode == 0
    assert "hello" in r.stdout


@pytest.mark.asyncio
async def test_nonzero_returncode():
    r = await run_shell("exit 7", timeout=5)
    assert r.returncode == 7
    assert r.ok  # не таймаут, не ошибка запуска — команда отработала


@pytest.mark.asyncio
async def test_stderr_captured():
    r = await run_shell("echo err 1>&2", timeout=5)
    assert "err" in r.stderr


@pytest.mark.asyncio
async def test_timeout_kills():
    r = await run_shell("sleep 10", timeout=1)
    assert r.timed_out
    assert not r.ok
    assert "таймаут" in r.error


@pytest.mark.asyncio
async def test_mask_secrets():
    r = await run_shell(
        "echo ROUTERAI_API_KEY=sk-abcdef1234567890ABCDEF",
        timeout=5,
        mask=True,
    )
    assert "sk-abcdef" not in r.stdout
    assert "<hidden>" in r.stdout


@pytest.mark.asyncio
async def test_mask_disabled():
    r = await run_shell(
        "echo ROUTERAI_API_KEY=sk-abcdef1234567890ABCDEF",
        timeout=5,
        mask=False,
    )
    assert "sk-abcdef" in r.stdout


@pytest.mark.asyncio
async def test_max_bytes_truncates():
    r = await run_shell(
        "head -c 1000 /dev/zero | tr '\\0' 'x'", timeout=5, max_bytes=100
    )
    assert "(обрезано" in r.stdout
    assert len(r.stdout) < 300


@pytest.mark.asyncio
async def test_max_bytes_zero_no_truncate():
    r = await run_shell(
        "head -c 1000 /dev/zero | tr '\\0' 'x'",
        timeout=5,
        max_bytes=0,
    )
    assert "(обрезано" not in r.stdout


@pytest.mark.asyncio
async def test_cwd(tmp_path):
    r = await run_shell("pwd", cwd=tmp_path, timeout=5)
    assert str(tmp_path) in r.stdout


@pytest.mark.asyncio
async def test_output_property_prefers_stdout():
    r = ShellResult(returncode=0, stdout="out", stderr="err")
    assert r.output == "out"


@pytest.mark.asyncio
async def test_output_property_stderr_fallback():
    r = ShellResult(returncode=0, stdout="", stderr="err")
    assert r.output == "err"


def test_output_property_empty():
    r = ShellResult(returncode=0, stdout="", stderr="")
    assert r.output == "(пусто)"


@pytest.mark.asyncio
async def test_pipe_works():
    r = await run_shell("echo abc | tr a-z A-Z", timeout=5)
    assert "ABC" in r.stdout


@pytest.mark.asyncio
async def test_kills_process_group_on_timeout():
    # Спавним дочерний процесс и вешаем родителя — killpg должен убить оба
    r = await run_shell("sleep 100 & sleep 100", timeout=1)
    assert r.timed_out
