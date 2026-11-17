#!/usr/bin/env bash
# Workflow для правок кода agent-coder БЕЗ права писать в живое дерево.
#
# Почему так: agent_coder/, tests/, docs/ смонтированы read-only
# (ReadOnlyPaths= в deploy/agent-coder-bot.service, M7). Бот физически не
# может править собственный код — защита от prompt-injection и бага в
# write_file. Правки идут в writable-клоне, оттуда экспортируются в
# patches/, на живое дерево накладываются с хоста.
#
# Почему клон в sessions/patch-work/:
#   - внутри workspace, иначе write_file/edit_file отвергают путь;
#   - sessions/ — один из немногих ReadWritePaths в юните;
#   - sessions/ в .gitignore -> не мусорит в git status, а ruff/pytest
#     уважают .gitignore и не видят клон.
#
# Использование:
#   ./patches/export.sh new NAME      # клон под задачу NAME
#   ./patches/export.sh export NAME   # диф клона -> patches/NNNN-NAME.patch
#   ./patches/export.sh check NAME    # apply в чистом клоне + ruff + pytest
#   ./patches/export.sh status        # что накопилось в patches/

set -euo pipefail

REPO="/home/botuser/bots/agent-coder"
PATCHES="$REPO/patches"
WORKSPACES="$REPO/sessions/patch-work"
VENV="$REPO/.venv"
DEFAULT_NAME="sandbox"
CHECK_TMP=""

cleanup() {
    [ -n "$CHECK_TMP" ] && rm -rf "$CHECK_TMP"
    return 0    # иначе статус trap перезаписывает exit-код скрипта
}
trap cleanup EXIT

if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
    C_OK=$'\033[32m'; C_FAIL=$'\033[31m'; C_INFO=$'\033[34m'
    C_WARN=$'\033[33m'; C_OFF=$'\033[0m'
else
    C_OK=; C_FAIL=; C_INFO=; C_WARN=; C_OFF=
fi
c_ok()   { printf '%s✓ %s%s\n' "$C_OK"   "$*" "$C_OFF"; }
c_fail() { printf '%s✗ %s%s\n' "$C_FAIL" "$*" "$C_OFF"; }
c_info() { printf '%s· %s%s\n' "$C_INFO" "$*" "$C_OFF"; }
c_warn() { printf '%s! %s%s\n' "$C_WARN" "$*" "$C_OFF"; }

clone_path() {
    local name="${1:-$DEFAULT_NAME}"
    case "$name" in
        */*|..) c_fail "имя без слэшей: $name" >&2; return 1 ;;
    esac
    printf '%s/%s' "$WORKSPACES" "$name"
}

next_number() {
    local last
    last=$(find "$PATCHES" -maxdepth 1 -name '[0-9][0-9][0-9][0-9]-*.patch' \
             -printf '%f\n' 2>/dev/null | sort | tail -1)
    if [ -z "$last" ]; then
        printf '0001'
    else
        printf '%04d' "$(( 10#${last%%-*} + 1 ))"
    fi
}

cmd_new() {
    local name="${1:-$DEFAULT_NAME}" dst
    dst=$(clone_path "$name") || exit 1
    if [ -e "$dst" ]; then
        c_fail "уже существует: ${dst#"$REPO"/}"
        c_info "пересоздать: python -c \"import shutil;shutil.rmtree('$dst')\""
        exit 1
    fi
    mkdir -p "$WORKSPACES"
    git clone -q "$REPO" "$dst"
    c_ok "клон: ${dst#"$REPO"/}"
    c_info "HEAD $(git -C "$dst" rev-parse --short HEAD)"
    c_info "правь файлы там, затем: ./patches/export.sh export $name"
}

cmd_export() {
    local name="${1:-$DEFAULT_NAME}" src num out
    src=$(clone_path "$name") || exit 1
    [ -d "$src/.git" ] || { c_fail "нет клона — сначала: export.sh new $name"; exit 1; }

    if [ -z "$(git -C "$src" status --porcelain)" ]; then
        c_fail "в клоне нет изменений — нечего экспортировать"
        exit 1
    fi

    num=$(next_number)
    out="$PATCHES/$num-$name.patch"

    # -N: новые файлы попадают в диф как создание, иначе git diff их не видит.
    git -C "$src" add -N .
    git -C "$src" diff --binary --output="$out"

    if [ ! -s "$out" ]; then
        c_fail "пустой диф — патч не создан"
        unlink "$out" 2>/dev/null || true
        exit 1
    fi

    c_ok "патч: ${out#"$REPO"/}"
    c_info "$(git -C "$src" diff --shortstat)"
    echo
    git -C "$src" diff --stat | sed 's/^/  /'
    echo
    c_info "проверка: ./patches/export.sh check $name"
}

cmd_check() {
    local name="${1:-$DEFAULT_NAME}" patch rc=0 files f
    patch=$(find "$PATCHES" -maxdepth 1 -name "*-$name.patch" -printf '%f\n' | sort | tail -1)
    [ -n "$patch" ] || { c_fail "нет патча *-$name.patch в patches/"; exit 1; }
    patch="$PATCHES/$patch"
    c_info "патч: ${patch#"$REPO"/}"

    CHECK_TMP=$(mktemp -d)
    git clone -q "$REPO" "$CHECK_TMP/v"
    if ! git -C "$CHECK_TMP/v" apply --check "$patch" 2>/dev/null; then
        c_fail "патч не ложится на $(git -C "$CHECK_TMP/v" rev-parse --short HEAD)"
        exit 1
    fi
    git -C "$CHECK_TMP/v" apply "$patch"
    c_ok "apply --check: чисто"

    # Проверяем только файлы из патча: в дереве есть baseline-нарушения
    # (ruff format по всему репо даёт 23 файла ещё до наших правок).
    files=$(grep '^+++ b/' "$patch" | sed 's|^+++ b/||' || true)
    if [ -z "$files" ]; then
        c_warn "не удалось определить файлы из патча"
    else
        local py_files=()
        while IFS= read -r f; do
            case "$f" in *.py) py_files+=("$CHECK_TMP/v/$f") ;; esac
        done <<< "$files"
        if [ ${#py_files[@]} -gt 0 ]; then
            "$VENV/bin/ruff" format --check "${py_files[@]}" >/dev/null 2>&1 \
                && c_ok "ruff format" || c_warn "ruff format хочет правок"
            "$VENV/bin/ruff" check "${py_files[@]}" >/dev/null 2>&1 \
                && c_ok "ruff check" || { c_fail "ruff check"; rc=1; }
        else
            c_info "в патче нет .py — ruff пропущен"
        fi
    fi
    echo
    ( cd "$CHECK_TMP/v" && "$VENV/bin/pytest" -q 2>&1 | tail -1 | sed 's/^/  /' ) || rc=1
    return $rc
}

cmd_status() {
    local n d dirty
    n=$(find "$PATCHES" -maxdepth 1 -name '[0-9][0-9][0-9][0-9]-*.patch' | wc -l)
    c_info "патчей в patches/: $n"
    find "$PATCHES" -maxdepth 1 -name '[0-9][0-9][0-9][0-9]-*.patch' \
        -printf '  %f  (%s байт)\n' | sort

    echo
    if [ -d "$WORKSPACES" ] && [ -n "$(ls -A "$WORKSPACES" 2>/dev/null)" ]; then
        c_info "клоны в ${WORKSPACES#"$REPO"/}:"
        for d in "$WORKSPACES"/*/; do
            [ -d "$d/.git" ] || continue
            dirty=$(git -C "$d" status --porcelain | wc -l)
            printf '  %-32s %s\n' "$(basename "$d")" \
                "$([ "$dirty" -eq 0 ] && echo clean || echo "$dirty изменений")"
        done
    else
        c_info "клонов нет — ./patches/export.sh new NAME"
    fi

    echo
    c_info "живое дерево:"
    if git -C "$REPO" status --porcelain | grep -q .; then
        git -C "$REPO" status --porcelain | sed 's/^/  /'
    else
        c_ok "чисто"
    fi
}

case "${1:-}" in
    new)    shift; cmd_new    "$@" ;;
    export) shift; cmd_export "$@" ;;
    check)  shift; cmd_check  "$@" ;;
    status) cmd_status ;;
    *)
        echo "usage: $0 {new|export|check|status} [NAME]" >&2
        echo
        echo "  new NAME      клон в sessions/patch-work/NAME (править тут)"
        echo "  export NAME   диф -> patches/NNNN-NAME.patch"
        echo "  check NAME    apply в чистом клоне + ruff + pytest"
        echo "  status        что в patches/ и что грязно в клонах"
        exit 2
        ;;
esac
