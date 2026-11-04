"""Run reproducible live model benchmark cases in isolated git worktrees.

This runner deliberately delegates task execution to the public CLI, so the
benchmark measures the same agent loop users run. It never runs by default:
provide --run and an API key/configured environment before spending money.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODELS = [
    # Existing curated models
    "deepseek/deepseek-v4.1-flash",
    "openai/gpt-5.6-luna",
    "z-ai/glm-5.3-flash",
    "google/gemini-3.8-flash",
    "moonshotai/kimi-k2.7-code",
    "anthropic/claude-sonnet-5.5",
    "x-ai/grok-4.7",
    # New comparison candidates (DeepSeek and Gemini overlap with the list above)
    "anthropic/claude-opus-5.5",
    "openai/gpt-6-astra",
    "moonshotai/kimi-k3",
    "z-ai/glm-5.3",
]


def _git(*args: str, cwd: Path = ROOT) -> str:
    return subprocess.check_output(["git", *args], cwd=cwd, text=True).strip()


def _project_for(task: dict, cli_project: str | None) -> str | None:
    """B-provenance: приоритет CLI > task['project'] > None.

    Логическое имя проекта (na_shtile, weather-bot, agent_coder) пишется
    в results.json для последующей дизамбигуации. Физический путь worktree
    (git.base / workspace) эфемерен и не заменяет project.
    """
    return cli_project or task.get("project") or None


def _run(cmd: list[str], cwd: Path, output: Path) -> int:
    started = time.monotonic()
    with output.open("w", encoding="utf-8") as stream:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            stdout=stream,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
    return proc.returncode, round(time.monotonic() - started, 3)


def _latest_session(workspace: Path, sessions: Path) -> dict[str, Any] | None:
    candidates = sorted(
        sessions.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True
    )
    for path in candidates:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if data.get("workspace") == str(workspace):
            return data
    return None


def _apply_current_changes(workspace: Path, base: str) -> None:
    """Make the worktree use the current uncommitted agent implementation."""
    patch = subprocess.run(
        ["git", "diff", "--binary", base],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    ).stdout
    if patch:
        subprocess.run(
            ["git", "apply", "--whitespace=nowarn"],
            cwd=workspace,
            input=patch,
            text=True,
            check=True,
        )


def _prepare_fixture(
    workspace: Path, fixture: str, task_id: str, sessions_dir: Path | None = None
) -> None:
    """Run an optional repository-local setup script before the model starts."""
    if not fixture:
        raise ValueError(f"Task {task_id} has no setup fixture")
    setup = ROOT / "benchmark" / "fixtures" / fixture
    if not setup.is_file():
        raise FileNotFoundError(f"Benchmark fixture not found: {setup}")
    argv = [sys.executable, str(setup), str(workspace)]
    if sessions_dir is not None:
        argv.append(str(sessions_dir))
    subprocess.run(argv, cwd=ROOT, check=True)
    # Fixture пишет sentinel-тест (test_benchmark_target.py) в корень
    # worktree, а pyproject наследует testpaths = ["tests"] из репо →
    # pytest его не собирает, hard pass неотличим от soft (B9.2g.bench).
    # Патчим worktree-pyproject ДО fixture-commit, чтобы правка не попала
    # в baseline_changed и не «изменила» changed_files модели.
    _pp = workspace / "pyproject.toml"
    _sentinel = workspace / "test_benchmark_target.py"
    if _pp.is_file() and _sentinel.is_file():
        _txt = _pp.read_text(encoding="utf-8")
        if "test_benchmark_target.py" not in _txt:
            if 'testpaths = ["tests"]' in _txt:
                _txt = _txt.replace(
                    'testpaths = ["tests"]',
                    'testpaths = ["tests", "test_benchmark_target.py"]',
                    1,
                )
            elif "testpaths" in _txt:
                _txt = re.sub(
                    r"(?m)^testpaths\s*=.*$",
                    'testpaths = ["tests", "test_benchmark_target.py"]',
                    _txt,
                    count=1,
                )
            _pp.write_text(_txt, encoding="utf-8")
    # Fixture пишет untracked-файлы; без commit git diff их не видит, и
    # правки модели теряются (17/18 fix: changed=0 при edit_file в логе).
    # Коммитим fixture — её файлы становятся tracked, правки модели видны.
    subprocess.run(["git", "add", "-A"], cwd=workspace, check=False)
    subprocess.run(
        ["git", "commit", "-m", f"benchmark: {task_id} fixture", "--no-verify"],
        cwd=workspace,
        check=False,
        capture_output=True,
    )


def _git_stats(
    workspace: Path, base: str, baseline_files: set[str], baseline_changed: set[str]
) -> dict[str, Any]:
    diff = subprocess.run(
        ["git", "diff", "--stat", base],
        cwd=workspace,
        text=True,
        capture_output=True,
        check=False,
    ).stdout
    names = set(
        subprocess.run(
            ["git", "diff", "--name-only", base],
            cwd=workspace,
            text=True,
            capture_output=True,
            check=False,
        ).stdout.splitlines()
    )
    status = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=workspace,
        text=True,
        capture_output=True,
        check=False,
    ).stdout.splitlines()
    untracked = {line[3:] for line in status if line.startswith("?? ")} - baseline_files
    names.difference_update(baseline_changed)
    names.update(untracked)
    return {"changed_files": sorted(names), "diff_stat": diff.strip()}


def _restore_fixture_files(workspace: Path, benchmark_base: str) -> None:
    """Восстановить sentinel и testpaths из fixture-коммита перед pytest.

    Модель могла изменить/удалить test_benchmark_target.py или testpaths
    в pyproject — тогда hard-сигнал подделан. Точечно: только эти два
    файла, не весь worktree (иначе сотрём правки модели).
    """
    subprocess.run(
        [
            "git",
            "checkout",
            benchmark_base,
            "--",
            "test_benchmark_target.py",
            "pyproject.toml",
        ],
        cwd=workspace,
        check=False,
        capture_output=True,
    )


def run_case(
    task: dict[str, Any],
    model: str,
    run_dir: Path,
    base: str,
    max_iterations: int,
    case_timeout: float | None = None,
    keep_worktree: bool = False,
    max_cost: float | None = None,
) -> dict[str, Any]:
    # Per-task override: audit 74 файла = 25 итераций, fix = 12.
    max_iterations = int(task.get("max_iterations", max_iterations))
    case_id = f"{task['id']}__{model.replace('/', '_')}"
    workspace = run_dir / "worktrees" / case_id
    log = run_dir / "logs" / f"{case_id}.log"
    workspace.parent.mkdir(parents=True, exist_ok=True)
    log.parent.mkdir(parents=True, exist_ok=True)
    # A previous interrupted runner may have left the worktree registered.
    if workspace.exists():
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(workspace)],
            cwd=ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    subprocess.run(
        ["git", "worktree", "add", "--detach", str(workspace), base],
        cwd=ROOT,
        check=True,
        stdout=subprocess.DEVNULL,
    )
    try:
        _apply_current_changes(workspace, base)
        _prepare_fixture(
            workspace, task.get("setup", ""), task["id"], run_dir / "sessions"
        )
        # B9.2g.bench: fixture кладёт эталонную session (audit-A, 79.6₽).
        # Её total_usage/patch_results — не расход этого прогона.
        baseline_session = _latest_session(workspace, run_dir / "sessions") or {}
        baseline_usage = dict(baseline_session.get("total_usage") or {})
        baseline_patch_count = len(baseline_session.get("patch_results") or [])
        baseline_verify_count = len(baseline_session.get("verify_results") or [])
        # Baseline — после fixture: её файлы не «изменения модели».
        baseline_changed = set(_git("diff", "--name-only", cwd=workspace).splitlines())
        baseline_files = {
            str(p.relative_to(workspace))
            for p in workspace.rglob("*")
            if p.is_file() and ".git" not in p.parts
        }
        benchmark_base = _git("rev-parse", "HEAD", cwd=workspace)
        started = time.monotonic()
        rc = -1
        env = os.environ.copy()
        env["SESSIONS_DIR"] = str(run_dir / "sessions")
        _apply_cost_cap(env, max_cost)
        command = [
            sys.executable,
            "-m",
            "agent_coder",
            task["prompt"],
            "--cwd",
            ".",
            "--model",
            model,
            "--yolo",
            "--max-iterations",
            str(max_iterations),
        ]
        if task["category"] == "audit":
            command.append("--audit")
        elif task["category"] == "verify":
            command.append("--verify")
        command.extend(task.get("extra_args", []))
        with log.open("w", encoding="utf-8") as stream:
            try:
                proc = subprocess.run(
                    command,
                    cwd=workspace,
                    env=env,
                    stdout=stream,
                    stderr=subprocess.STDOUT,
                    text=True,
                    timeout=case_timeout,
                    check=False,
                )
                rc = proc.returncode
            except subprocess.TimeoutExpired:
                # Keep the case in results and continue with the next model.
                rc = 124
                stream.write("\n[benchmark] case timeout exceeded\n")
        session = _latest_session(workspace, run_dir / "sessions") or {}
        # B9.2g.bench: snapshot — следующий прогон (другая модель)
        # перезаписывает run_dir/sessions/a6a74ebf.json через fixture.
        # Без snapshot остаётся только последняя модель (verify-01: 6 → 1).
        _snap_dir = run_dir / "sessions" / "snapshots"
        _snap_dir.mkdir(parents=True, exist_ok=True)
        (_snap_dir / f"{case_id}.json").write_text(
            json.dumps(
                {
                    "case_id": case_id,
                    "model": model,
                    "task_id": task["id"],
                    "category": task["category"],
                    "baseline_verify_count": baseline_verify_count,
                    "baseline_patch_count": baseline_patch_count,
                    "verify_results_new": (session.get("verify_results") or [])[
                        baseline_verify_count:
                    ],
                    "patch_results_new": (session.get("patch_results") or [])[
                        baseline_patch_count:
                    ],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        raw_usage = session.get("total_usage") or {}
        usage = {
            k: max(0, raw_usage.get(k, 0) - baseline_usage.get(k, 0))
            for k in (
                "input_tokens",
                "output_tokens",
                "cache_read_tokens",
                "cache_write_tokens",
                "cost",
            )
        }
        new_patch_results = (session.get("patch_results") or [])[baseline_patch_count:]
        # ACT/PLAN/AUDIT: patch_results/verify_results are empty
        # for these modes. Recover iteration/tool counts from the
        # archived session transcript instead of hardcoding 0.
        act_archive = _find_case_archive(run_dir / "sessions")
        if act_archive is not None:
            act_iterations, act_tool_calls = _count_iterations_and_tools(act_archive)
        else:
            act_iterations, act_tool_calls = 0, 0
        patch_iterations = sum(int(pr.get("iterations", 0)) for pr in new_patch_results)
        new_verify_results = (session.get("verify_results") or [])[
            baseline_verify_count:
        ]
        verify_iterations = sum(
            int(vr.get("iterations", 0)) for vr in new_verify_results
        )
        verify_confirmed = sum(
            1 for vr in new_verify_results if vr.get("status") == "confirmed"
        )
        git_stats = _git_stats(
            workspace, benchmark_base, baseline_files, baseline_changed
        )
        # После прогона: pytest в worktree. Только для не-audit с правками.
        # Аудит read-only. create/refactor/fix — тесты обязательны (prompt),
        # success без них ложный (B9.2: deepseek подгонял assert'ы).
        # rc=4/5 (usage / no tests) — ran=False; passed только rc==0.
        tests: dict[str, Any] = {
            "ran": False,
            "passed": False,
            "returncode": None,
            "tail": "",
        }
        if task["category"] != "audit" and git_stats["changed_files"]:
            _restore_fixture_files(workspace, benchmark_base)
            try:
                # B9.2g.bench: worktree venv (symlink из fixture) — иначе
                # системный python без aiogram/pydantic-settings → ImportError
                # на test_imports.py. Fallback на sys.executable.
                _ws_venv_py = workspace / ".venv" / "bin" / "python"
                _py = str(_ws_venv_py) if _ws_venv_py.exists() else sys.executable
                proc = subprocess.run(
                    [_py, "-m", "pytest", "-q", "--no-header"],
                    cwd=workspace,
                    text=True,
                    capture_output=True,
                    timeout=180,
                    check=False,
                )
                rc_pytest = proc.returncode
                tests["returncode"] = rc_pytest
                tests["ran"] = rc_pytest not in (4, 5)
                tests["passed"] = rc_pytest == 0
                out = (proc.stdout or "") + (proc.stderr or "")
                tests["tail"] = out.strip().splitlines()[-1] if out.strip() else ""
            except (subprocess.TimeoutExpired, OSError) as exc:
                tests["returncode"] = -1
                tests["tail"] = f"pytest error: {exc}"
        result = {
            "task_id": task["id"],
            "category": task["category"],
            "model": model,
            "returncode": rc,
            "duration_seconds": round(time.monotonic() - started, 3),
            # B9.2g.bench: run_patch не пишет в session.messages/request_count,
            # берём из patch_results (сумма итераций новых прогонов).
            "iterations": patch_iterations + verify_iterations + act_iterations,
            "tool_calls": act_tool_calls,
            "usage": usage,
            "git": git_stats,
            "tests": tests,
            "log": str(log.relative_to(run_dir)),
        }
        # B11 audit: meta.json из .agent-coder/audit-*/ (findings/coverage/total)
        audit_meta = {}
        if task["category"] == "audit":
            for meta_path in workspace.glob(".agent-coder/audit-*/meta.json"):
                try:
                    audit_meta = json.loads(meta_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    pass
        if audit_meta:
            result["audit"] = {
                "findings": audit_meta.get("findings_count", 0),
                "coverage": audit_meta.get("coverage_count", 0),
                "total_files": audit_meta.get("total_files", 0),
            }
        if task["category"] == "audit":
            min_findings = int(task.get("min_findings", 0))
            result["success"] = (
                rc == 0
                and not git_stats["changed_files"]
                and result.get("audit", {}).get("findings", 0) >= min_findings
            )
        elif task["category"] == "verify":
            # verify — read-only: файлы не меняются, ценим confirmed findings.
            min_confirmed = int(task.get("min_confirmed", 1))
            result["success"] = (
                rc == 0
                and not git_stats["changed_files"]
                and verify_confirmed >= min_confirmed
            )
            result["verify"] = {"confirmed": verify_confirmed}
        else:
            result["success"] = (
                rc == 0 and bool(git_stats["changed_files"]) and tests["passed"]
            )
        return result
    finally:
        # B9.2g.bench: --keep-worktree — не удалять при failure,
        # чтобы посмотреть diff и упавшие тесты после прогона.
        if not keep_worktree:
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(workspace)],
                cwd=ROOT,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )


def _count_iterations_and_tools(session_path: Path) -> tuple[int, int]:
    """Count iterations and tool calls from a session file.

    Prefers `.json` (sessions/<uuid>.json — full transcript with
    `request_count` and `messages`). `request_count` is the CLI's own counter
    and is the canonical iteration count; falls back to counting assistant
    messages if absent. `.jsonl` (sessions/archive/<uuid>.jsonl — the
    search_history index) has only tool results in practice, assistant
    messages are counted defensively. Missing/unreadable -> (0, 0).
    """
    if not session_path.exists():
        return 0, 0

    if session_path.suffix == ".json":
        try:
            data = json.loads(session_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return 0, 0
        if not isinstance(data, dict):
            return 0, 0
        messages = data.get("messages") or []
        tool_calls = sum(
            1 for m in messages if isinstance(m, dict) and m.get("role") == "tool"
        )
        request_count = data.get("request_count")
        if isinstance(request_count, int) and request_count >= 0:
            return request_count, tool_calls
        iterations = sum(
            1 for m in messages if isinstance(m, dict) and m.get("role") == "assistant"
        )
        return iterations, tool_calls

    iterations = 0
    tool_calls = 0
    for line in session_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        if role == "assistant":
            iterations += 1
        elif role == "tool":
            tool_calls += 1
    return iterations, tool_calls


def _find_case_archive(sessions_dir: Path) -> Path | None:
    """Session file for the just-finished case.

    Prefers sessions/*.json (full transcript: messages + request_count) and
    falls back to sessions/archive/*.jsonl (tool results only, iterations
    usually 0). run_dir is unique per invocation and run_case runs strictly
    sequentially, so the freshest matching file is the current case.
    `sessions/index.json` is the session catalog, not a case — excluded.
    Missing dir or empty dir -> None.
    """
    if not sessions_dir.exists():
        return None
    json_files = [
        p for p in sessions_dir.glob("*.json") if p.is_file() and p.name != "index.json"
    ]
    if json_files:
        return max(json_files, key=lambda p: p.stat().st_mtime)
    archive_dir = sessions_dir / "archive"
    if not archive_dir.exists():
        return None
    jsonl_files = [p for p in archive_dir.glob("*.jsonl") if p.is_file()]
    if not jsonl_files:
        return None
    return max(jsonl_files, key=lambda p: p.stat().st_mtime)


def _apply_cost_cap(env: dict[str, str], max_cost: float | None) -> None:
    """Прокинуть hard-лимит в CLI через env (MAX_COST_PER_*); None/0 — no-op."""
    if max_cost is not None and max_cost > 0:
        env["MAX_COST_PER_TASK"] = str(max_cost)
        env["MAX_COST_PER_SESSION"] = str(max_cost)


def _cumulative_cost(results: list[dict[str, Any]]) -> float:
    total = 0.0
    for r in results:
        try:
            total += float((r.get("usage") or {}).get("cost") or 0)
        except (TypeError, ValueError):
            continue
    return total


def _should_stop(results: list[dict[str, Any]], max_cost: float | None) -> bool:
    if max_cost is None or max_cost <= 0:
        return False
    return _cumulative_cost(results) >= max_cost


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run", action="store_true", help="Actually call models; otherwise print plan"
    )
    parser.add_argument("--tasks", default=str(Path(__file__).with_name("tasks.json")))
    parser.add_argument("--models", nargs="+", default=DEFAULT_MODELS)
    parser.add_argument("--output", default="benchmark/results")
    parser.add_argument(
        "--project",
        default=None,
        help=(
            "Логическое имя проекта (na_shtile, weather-bot, agent_coder). "
            "Пишется в results.json для provenance (Фаза B, дизамбигуация)."
        ),
    )
    parser.add_argument("--max-iterations", type=int, default=12)
    parser.add_argument(
        "--case-timeout",
        type=float,
        default=900,
        help="Maximum seconds per model case (0 disables the limit).",
    )
    parser.add_argument("--task-id", action="append")
    parser.add_argument(
        "--keep-worktree",
        action="store_true",
        help="Не удалять worktree после прогона (для диагностики failure).",
    )
    parser.add_argument(
        "--max-cost",
        type=float,
        default=None,
        help="Hard-лимит стоимости в ₽ на весь прогон: env MAX_COST_PER_TASK/SESSION в CLI + stop-between-cases. 0 = без лимита.",
    )
    args = parser.parse_args()
    # Benchmark: .env не попадает в worktree (gitignored), но subprocess
    # наследует os.environ. Грузим ключи из ROOT/.env один раз —
    # override=False, реальные env-переменные не перебиваются.
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env", override=False)
    tasks = json.loads(Path(args.tasks).read_text(encoding="utf-8"))
    if args.task_id:
        tasks = [t for t in tasks if t["id"] in args.task_id]
    plan = {
        "models": args.models,
        "tasks": [t["id"] for t in tasks],
        "count": len(tasks) * len(args.models),
    }
    if not args.run:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        print(
            "Dry run only. Add --run to call the configured provider.", file=sys.stderr
        )
        return 0
    run_dir = (Path(args.output) / time.strftime("%Y%m%d-%H%M%S")).resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    base = _git("rev-parse", "HEAD")
    results = []
    for task in tasks:
        for model in args.models:
            print(
                f"[{len(results) + 1}/{plan['count']}] {task['id']} / {model}",
                flush=True,
            )
            _res = run_case(
                task,
                model,
                run_dir,
                base,
                args.max_iterations,
                case_timeout=args.case_timeout or None,
                keep_worktree=args.keep_worktree,
                max_cost=args.max_cost,
            )
            if isinstance(_res, dict):
                _res.setdefault("project", _project_for(task, args.project))
            results.append(_res)
            (run_dir / "results.json").write_text(
                json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            if _should_stop(results, args.max_cost):
                print(
                    f"Stopped: cumulative cost {_cumulative_cost(results):.2f}₽ "
                    f">= --max-cost {args.max_cost}₽",
                    file=sys.stderr,
                )
                break
    print(run_dir / "results.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
