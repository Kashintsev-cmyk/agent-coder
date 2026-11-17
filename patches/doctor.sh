#!/usr/bin/env bash
# Проверка, что M7-защита жива и workflow патчей не сломан.
#
# Read-only: ничего не меняет. Возвращает 0, если всё в порядке.
#   ./patches/doctor.sh
#
# Проверяет:
#   1. RO-монтирование agent_coder/ (нельзя писать в живой код);
#   2. writable-точки (sessions/, patches/);
#   3. скрипт export.sh на месте и исполняем;
#   4. клоны в sessions/patch-work/ не текут в git status / ruff / pytest;
#   5. патчи в patches/ ложатся на текущий HEAD или уже в нём;
#   6. ssh-доступ к origin (нужен для push с хоста).

set -uo pipefail

REPO="/home/botuser/bots/agent-coder"
PATCHES="$REPO/patches"
WORKSPACES="$REPO/sessions/patch-work"
VENV="$REPO/.venv"
rc=0

if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
    C_OK=$'\033[32m'; C_FAIL=$'\033[31m'; C_INFO=$'\033[34m'
    C_WARN=$'\033[33m'; C_OFF=$'\033[0m'
else
    C_OK=; C_FAIL=; C_INFO=; C_WARN=; C_OFF=
fi
ok()   { printf '%s✓ %s%s\n' "$C_OK"   "$*" "$C_OFF"; }
bad()  { printf '%s✗ %s%s\n' "$C_FAIL" "$*" "$C_OFF"; rc=1; }
info() { printf '%s· %s%s\n' "$C_INFO" "$*" "$C_OFF"; }
warn() { printf '%s! %s%s\n' "$C_WARN" "$*" "$C_OFF"; }
hdr()  { printf '\n=== %s ===\n' "$*"; }

hdr "1. M7: живой код read-only"
if touch "$REPO/agent_coder/.wtest" 2>/dev/null; then
    unlink "$REPO/agent_coder/.wtest"
    warn "agent_coder/ ДОСТУПЕН на запись — M7 не работает"
    info "проверь ReadOnlyPaths в deploy/agent-coder-bot.service"
    rc=1
else
    ok "agent_coder/ read-only (M7 в силе)"
fi
for d in tests docs deploy; do
    if touch "$REPO/$d/.wtest" 2>/dev/null; then
        unlink "$REPO/$d/.wtest"
        warn "$d/ доступен на запись"
        rc=1
    else
        ok "$d/ read-only"
    fi
done

hdr "2. Writable-точки"
for d in sessions patches; do
    if touch "$REPO/$d/.wtest" 2>/dev/null; then
        unlink "$REPO/$d/.wtest"
        ok "$d/ writable"
    else
        bad "$d/ не пишется — экспорт патчей сломается"
    fi
done

hdr "3. export.sh"
if [ -x "$PATCHES/export.sh" ]; then
    ok "export.sh исполняем"
    bash -n "$PATCHES/export.sh" && ok "синтаксис OK" || bad "синтаксис битый"
else
    bad "patches/export.sh отсутствует или не chmod +x"
fi

hdr "4. Клоны не текут в git/ruff/pytest"
if git -C "$REPO" status --porcelain | grep -q 'patch-work'; then
    bad "клоны видны в git status (проверь .gitignore: sessions/)"
else
    ok "git status чист (клоны под sessions/, gitignored)"
fi
if [ -d "$WORKSPACES" ] && [ -n "$(ls -A "$WORKSPACES" 2>/dev/null)" ]; then
    info "клонов: $(find "$WORKSPACES" -maxdepth 1 -mindepth 1 -type d | wc -l)"
    fmt_out=$("$VENV/bin/ruff" format --check . 2>&1 | tail -1)
    case "$fmt_out" in
        *"already formatted"*)
            n=$(printf '%s' "$fmt_out" | grep -oE '[0-9]+ files would' | grep -oE '[0-9]+' || echo 0)
            [ "${n:-0}" -le 23 ] && ok "ruff format: $n (= baseline 23)" \
                                 || bad "ruff format: $n > baseline 23 — клон течёт"
            ;;
        *) warn "ruff format: неожиданный вывод" ;;
    esac
else
    ok "клонов нет"
fi

hdr "5. Патчи в patches/"
shopt -s nullglob
patches=("$PATCHES"/[0-9][0-9][0-9][0-9]-*.patch)
if [ ${#patches[@]} -eq 0 ]; then
    warn "патчей нет"
else
    tmp=$(mktemp -d)
    git clone -q "$REPO" "$tmp/v"
    for p in "${patches[@]}"; do
        if git -C "$tmp/v" apply --check "$p" 2>/dev/null; then
            ok "$(basename "$p") — ложится на HEAD"
        elif git -C "$tmp/v" apply --check -R "$p" 2>/dev/null; then
            ok "$(basename "$p") — уже в HEAD (применён)"
        else
            warn "$(basename "$p") — не ложится (конфликт или устарел)"
        fi
    done
    rm -rf "$tmp"
fi

hdr "6. Push-доступ к origin"
# ssh -T всегда завершается с 1 (GitHub не даёт shell) — глушим статус,
# иначе pipefail валит пайплайн при живом ключе.
if { timeout 8 ssh -o BatchMode=yes -o ConnectTimeout=5 -T git@github.com 2>&1 || true; } \
     | grep -qi 'successfully authenticated'; then
    ok "ssh github — ключ принят"
else
    bad "ssh github недоступен — push с хоста не пройдёт"
fi
git -C "$REPO" remote -v | head -1 | sed 's/^/  /'

echo
if [ "$rc" -eq 0 ]; then
    ok "все проверки пройдены"
else
    bad "есть проблемы — см. выше"
fi
exit $rc
