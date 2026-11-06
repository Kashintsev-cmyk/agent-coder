"""git read-only в audit: метаданные разрешены, контент/мутации — нет.

Контекст: на audit-04 (na_shtile) модель 5+ раз пыталась `git log`,
`git ls-files .env`, `git log -- .env` для проверки секретов в истории
— все заблокированы (git не в _SAFE_PATTERNS). В итоге git-история
не проверена, класс MAJOR «секреты в git» не покрыт.

Решение: whitelist git-подкоманд, которые выводят только метаданные
или имена файлов (status, log, ls-files, rev-parse, branch, tag,
remote, describe, shortlog, show-ref, for-each-ref). Bypass
_FORBIDDEN_PATHS — pathspec-фильтр (`git log -- .env`) не читает
содержимое, только отбирает коммиты.

show/diff/blame/grep/cat-file/archive НЕ в whitelist: они могут
выводить контент. `-p`/`--patch`/`-u`/`--unified` заблокированы
regex (diff-контент). `:` (git show HEAD:file) заблокировано.
"""

from __future__ import annotations

import pytest

from agent_coder.tools.run_command import _is_safe_command


def _cmd(s: str) -> bool:
    return _is_safe_command({"command": s})


# ---------- SAFE: metadata ----------


@pytest.mark.parametrize(
    "cmd",
    [
        "git status",
        "git status --porcelain",
        "git status -s",
        "git log",
        "git log --oneline",
        "git log --oneline -15",
        "git log --oneline -n 5",
        "git log --oneline --all",
        "git log --format=%H",
        "git log --stat",
        "git log --stat --oneline",
        "git ls-files",
        "git ls-files bot/",
        "git rev-parse HEAD",
        "git rev-parse --short HEAD",
        "git rev-list --count HEAD",
        "git branch",
        "git branch -a",
        "git branch --show-current",
        "git tag",
        "git tag --list",
        "git remote",
        "git remote -v",
        "git describe --tags",
        "git shortlog -sn",
        "git show-ref",
        "git for-each-ref",
        "git ls-tree HEAD",
    ],
)
def test_git_metadata_safe(cmd: str) -> None:
    assert _cmd(cmd) is True


# ---------- SAFE: pathspec не читает файл ----------


@pytest.mark.parametrize(
    "cmd",
    [
        "git ls-files .env",
        "git ls-files --error-unmatch .env",
        "git log --oneline --all -- .env",
        "git log --format=%H -- .env",
        "git log -Ssecret --oneline",
        "git log -Gpassword --oneline",
        "git ls-tree HEAD .env",
    ],
)
def test_git_pathspec_metadata_safe(cmd: str) -> None:
    """pathspec-фильтр (`-- .env`, `-S`, `-G`) не читает содержимое."""
    assert _cmd(cmd) is True


# ---------- BLOCKED: контент ----------


@pytest.mark.parametrize(
    "cmd",
    [
        "git log -p",
        "git log --patch",
        "git log -p -- .env",
        "git log -u -- .env",
        "git log -U5",
        "git log --unified=5",
        "git log --full-diff",
        "git show HEAD",
        "git show HEAD:.env",
        "git show --stat HEAD",
        "git blame bot/main.py",
        "git blame -L 1,5 bot/main.py",
        "git grep secret",
        "git grep -n password",
        "git cat-file -p HEAD",
        "git archive HEAD",
        "git diff",
        "git diff HEAD",
        "git diff --stat",
    ],
)
def test_git_content_blocked(cmd: str) -> None:
    assert _cmd(cmd) is False


# ---------- BLOCKED: мутации ----------


@pytest.mark.parametrize(
    "cmd",
    [
        "git push origin master",
        "git push",
        "git pull",
        "git fetch",
        "git commit -m fix",
        "git commit --amend",
        "git add .",
        "git rm file",
        "git mv a b",
        "git reset --hard",
        "git reset --soft HEAD",
        "git checkout main",
        "git checkout -- .",
        "git switch main",
        "git rebase main",
        "git merge main",
        "git cherry-pick abc",
        "git stash",
        "git stash pop",
        "git clean -fd",
        "git gc",
        "git prune",
        "git config user.name x",
        "git config --global user.email x",
        "git remote add origin url",
        "git remote remove origin",
        "git tag -a v1 -m x",
        "git tag -d v1",
        "git branch -d old",
        "git branch -D old",
        "git branch -m old new",
        "git filter-branch",
        "git update-ref HEAD x",
        "git reflog expire --all",
    ],
)
def test_git_mutating_blocked(cmd: str) -> None:
    assert _cmd(cmd) is False


# ---------- BLOCKED: shell-инъекции в git ----------


@pytest.mark.parametrize(
    "cmd",
    [
        "git log --oneline && rm x",
        "git ls-files && rm -rf bot",
        "git log --oneline || echo x",
        "git log --oneline ; rm x",
        "git log `whoami`",
        "git log $(whoami)",
        "git log > /tmp/x",
        "git log < /tmp/x",
    ],
)
def test_git_shell_injection_blocked(cmd: str) -> None:
    assert _cmd(cmd) is False


# ---------- pipe: safe passes, mutating blocked ----------


@pytest.mark.parametrize(
    "cmd",
    [
        "git log --oneline | head -20",
        "git log --oneline | tail -5",
        "git log --oneline | wc -l",
        "git ls-files | sort",
        "git ls-files | grep bot/",
        "git log --oneline | cat",
    ],
)
def test_git_safe_pipe(cmd: str) -> None:
    assert _cmd(cmd) is True


@pytest.mark.parametrize(
    "cmd",
    [
        "git push | cat",
        "git log --oneline | rm foo",
        "git log --oneline | git push",
        "git commit -m x | cat",
        "git log | tee /tmp/x",
    ],
)
def test_git_unsafe_pipe_blocked(cmd: str) -> None:
    assert _cmd(cmd) is False


# ---------- BLOCKED: запись в файл через флаги (B9.2g.5) ----------


@pytest.mark.parametrize(
    "cmd",
    [
        "git log --output=/tmp/x -2",
        "git log --output=/tmp/x -2 | cat",
        "git archive -o /tmp/z.tar HEAD | cat",
        "git bundle create /tmp/b.bundle HEAD | cat",
        "git format-patch -o /tmp/p HEAD | cat",
        "git fast-export --all --output=/tmp/e | cat",
        "find . -fprint /tmp/x | cat",
    ],
)
def test_git_output_flag_blocked(cmd: str) -> None:
    assert _cmd(cmd) is False
