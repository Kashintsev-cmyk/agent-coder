"""Run reproducible live model benchmark cases in isolated git worktrees.

This runner deliberately delegates task execution to the public CLI, so the
benchmark measures the same agent loop users run. It never runs by default:
provide --run and an API key/configured environment before spending money.
"""

from __future__ import annotations

import argparse
import json
import os
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


def run_case(
    task: dict[str, Any],
    model: str,
    run_dir: Path,
    base: str,
    max_iterations: int,
    case_timeout: float | None = None,
    keep_worktree: bool = False,
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
        patch_iterations = sum(int(pr.get("iterations", 0)) for pr in new_patch_results)
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
            "iterations": patch_iterations,
            "tool_calls": 0,
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run", action="store_true", help="Actually call models; otherwise print plan"
    )
    parser.add_argument("--tasks", default=str(Path(__file__).with_name("tasks.json")))
    parser.add_argument("--models", nargs="+", default=DEFAULT_MODELS)
    parser.add_argument("--output", default="benchmark/results")
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
            results.append(
                run_case(
                    task,
                    model,
                    run_dir,
                    base,
                    args.max_iterations,
                    case_timeout=args.case_timeout or None,
                    keep_worktree=args.keep_worktree,
                )
            )
            (run_dir / "results.json").write_text(
                json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
            )
    print(run_dir / "results.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
