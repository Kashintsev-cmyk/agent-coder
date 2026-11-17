#!/usr/bin/env bash
# Применение/откат патчей PrivateTmp для agent-coder-bot.service.
#
# Запускать С ХОСТА под пользователем, у которого есть sudo:
#   ./patches/apply.sh A          # отключить PrivateTmp (общий /tmp)
#   ./patches/apply.sh B          # оставить PrivateTmp, увести TMPDIR в ~/.cache
#   ./patches/apply.sh status     # что применено + приватный ли /tmp
#   ./patches/apply.sh revert     # снять все наши drop-in'ы и вернуть как было
#   ./patches/apply.sh check      # сквозная проверка «записал внутри — вижу снаружи»
#
# Изнутри бота (эта сессия) sudo НЕ работает: в юните NoNewPrivileges=true.
# status работает без root и без sudo — только на чтение.
#
# ВАЖНО про диагностику: если запускать этот скрипт ИЗНУТРИ юнита,
# `ls /tmp/systemd-private-*` ничего не найдёт — приватный tmp именно тем и
# приватен, что его каталога не видно из его же mount-namespace. Поэтому
# приватность определяется по /proc/<MainPID>/mountinfo, а не по ls.

set -euo pipefail

UNIT="agent-coder-bot.service"
PATCH_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DROPIN_DIR="/etc/systemd/system/${UNIT}.d"
WORKDIR="/home/botuser/bots/agent-coder"
# НЕ внутри WORKDIR: путь под деревом репозитория ломает _workspace_allowed()
# (project_utils.py:42) — см. README, секция «TMPDIR не должен указывать
# в репозиторий».
TMPDIR_FOR_B="/home/botuser/.cache/agent-coder/tmp"

VARIANT_A="10-private-tmp-off.conf"
VARIANT_B="20-tmpdir-outside-repo.conf"

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

need_root() {
    if [ "$(id -u)" -ne 0 ]; then
        c_fail "нужен root: sudo $0 ${1:-}"
        exit 1
    fi
}

main_pid() {
    systemctl show "$UNIT" -p MainPID --value 2>/dev/null || echo 0
}

# Запущены ли мы внутри самого юнита (тогда host-часть проверок недоступна).
inside_unit() {
    grep -qs "${UNIT}" /proc/self/cgroup && return 0
    grep -qs "${UNIT}" /proc/self/mountinfo && return 0
    return 1
}

# Приватный ли /tmp у юнита. Источник истины — mountinfo процесса:
#   приватный  -> поле root содержит systemd-private-.../tmp
#   общий      -> отдельной записи для /tmp нет, либо она без systemd-private
# Печатает: private | shared | down
tmp_state() {
    local pid="$1"
    if [ "${pid:-0}" -le 0 ] || [ ! -r "/proc/${pid}/mountinfo" ]; then
        echo "down"; return
    fi
    local line
    line="$(awk '$5 == "/tmp" {print}' "/proc/${pid}/mountinfo" 2>/dev/null || true)"
    if [ -z "$line" ]; then
        echo "shared"; return
    fi
    case "$line" in
        *systemd-private*) echo "private" ;;
        *)                 echo "shared" ;;
    esac
}

show_status() {
    echo "=== ${UNIT} ==="
    if inside_unit; then
        c_warn "скрипт запущен ИЗНУТРИ юнита: host-часть проверок ограничена"
        c_info "приватный tmp изнутри себя не виден — это ожидаемо, не баг"
    fi

    c_info "юнит: /etc/systemd/system/${UNIT}"
    if [ -d "$DROPIN_DIR" ]; then
        c_info "drop-in'ы:"
        ls -1 "$DROPIN_DIR" | sed 's/^/    /'
    else
        c_info "drop-in'ов нет ($DROPIN_DIR отсутствует)"
    fi
    echo

    systemctl show "$UNIT" -p PrivateTmp -p Environment -p ReadWritePaths \
        --no-pager 2>/dev/null || c_warn "systemctl show не сработал"
    echo

    local pid state tmpdir
    pid="$(main_pid)"
    state="$(tmp_state "$pid")"
    tmpdir="$(systemctl show "$UNIT" -p Environment --no-pager 2>/dev/null \
        | tr ' ' '\n' | sed -n 's/^TMPDIR=//p')"

    echo "=== состояние /tmp ==="
    c_info "MainPID=${pid:-0} (0 = юнит не запущен)"
    c_info "TMPDIR=${tmpdir:-<не задан>}"
    case "$state" in
        private)
            # PrivateTmp сам по себе не проблема: важно, куда смотрит TMPDIR.
            # TMPDIR вне приватного /tmp -> файлы видны снаружи (вариант B).
            case "$tmpdir" in
                ""|/tmp|/tmp/*)
                    c_fail "PrivateTmp АКТИВЕН и TMPDIR не уведён из /tmp —"
                    c_fail "временные файлы с хоста не видны"
                    awk '$5 == "/tmp" {print "    " $4}' "/proc/${pid}/mountinfo"
                    status_needs_fix=1
                    ;;
                *)
                    c_ok "PrivateTmp активен, но TMPDIR=${tmpdir} — вне /tmp"
                    c_info "временные файлы пишутся туда и видны с хоста"
                    ;;
            esac
            ;;
        shared)
            c_ok "PrivateTmp НЕ активен — /tmp общий с хостом"
            ;;
        down)
            c_warn "юнит не запущен — сравнивать нечего"
            ;;
    esac

    if [ "$(id -u)" -eq 0 ] && ! inside_unit; then
        echo
        c_info "каталоги systemd-private на хосте:"
        # shellcheck disable=SC2012
        ls -1d /tmp/systemd-private-*"${UNIT}"-* 2>/dev/null | sed 's/^/    /' \
            || c_info "    нет"
    fi

    echo
    if [ "${status_needs_fix:-0}" = "1" ]; then
        echo "Как чинить: sudo $0 B   (сохранить изоляцию, увести TMPDIR)"
        echo "            sudo $0 A   (просто отключить PrivateTmp)"
    fi
}

apply_variant() {
    local variant="$1" conf
    case "$variant" in
        A|a) conf="$VARIANT_A" ;;
        B|b) conf="$VARIANT_B" ;;
        *) c_fail "неизвестный вариант: $variant (A|B)"; exit 1 ;;
    esac

    need_root "$variant"

    if [ ! -f "${PATCH_DIR}/agent-coder-bot.service.d/${conf}" ]; then
        c_fail "нет файла ${PATCH_DIR}/agent-coder-bot.service.d/${conf}"
        exit 1
    fi

    # Варианты взаимоисключающие: A снимает изоляцию tmp, B её сохраняет.
    local other
    if [ "$conf" = "$VARIANT_A" ]; then other="$VARIANT_B"; else other="$VARIANT_A"; fi
    if [ -f "${DROPIN_DIR}/${other}" ]; then
        c_warn "снимаю ранее применённый ${other}"
        rm -f "${DROPIN_DIR}/${other}"
    fi

    if [ "$conf" = "$VARIANT_B" ]; then
        # TMPDIR должен существовать ДО старта юнита.
        install -d -o botuser -g botuser -m 0755 "$TMPDIR_FOR_B"
        c_ok "каталог $TMPDIR_FOR_B готов"
    fi

    install -d -m 0755 "$DROPIN_DIR"
    install -o root -g root -m 0644 \
        "${PATCH_DIR}/agent-coder-bot.service.d/${conf}" \
        "${DROPIN_DIR}/${conf}"
    c_ok "установлен drop-in: ${DROPIN_DIR}/${conf}"

    systemctl daemon-reload
    c_ok "daemon-reload"
    systemctl restart "$UNIT"
    c_ok "restart $UNIT"
    echo
    show_status
}

revert_all() {
    need_root revert
    local removed=0 conf
    for conf in "$VARIANT_A" "$VARIANT_B"; do
        if [ -f "${DROPIN_DIR}/${conf}" ]; then
            rm -f "${DROPIN_DIR}/${conf}"
            c_ok "удалён ${DROPIN_DIR}/${conf}"
            removed=1
        fi
    done
    if [ "$removed" -eq 0 ]; then
        c_info "нечего откатывать"
    fi
    if [ -d "$DROPIN_DIR" ] && [ -z "$(ls -A "$DROPIN_DIR" 2>/dev/null)" ]; then
        rmdir "$DROPIN_DIR" 2>/dev/null && c_ok "удалён пустой $DROPIN_DIR" || true
    fi
    systemctl daemon-reload
    systemctl restart "$UNIT"
    c_ok "restart $UNIT — юнит снова как в репозитории"
    echo
    show_status
}

do_check() {
    need_root check
    echo "=== сквозная проверка: записал внутри юнита — читаю на хосте ==="
    local pid marker bot_tmp
    pid="$(main_pid)"
    if [ "${pid:-0}" -le 0 ]; then
        c_fail "юнит не запущен — проверять нечего"
        exit 1
    fi

    # TMPDIR берём из реального окружения процесса: nsenter не читает
    # Environment= юнита, поэтому иначе получим /tmp по умолчанию и
    # проверим не то, что использует бот.
    bot_tmp="$(tr '\0' '\n' < "/proc/${pid}/environ" 2>/dev/null \
               | sed -n 's/^TMPDIR=//p' | head -1)"
    [ -z "$bot_tmp" ] && bot_tmp="(не задан -> /tmp)"

    c_info "TMPDIR процесса бота: ${bot_tmp}"
    c_info "состояние /tmp: $(tmp_state "$pid")"

    marker=".agent-coder-tmp-probe.$$"
    local out
    out="$(nsenter -t "$pid" -m -- python3 -c \
        "import os,tempfile
d=tempfile.gettempdir()
p=os.path.join(d,'${marker}')
open(p,'w').write('x')
print(d)" 2>&1)" || true
    out="$(printf '%s' "$out" | tail -1)"

    if [ ! -f "${out}/${marker}" ]; then
        c_fail "запись изнутри юнита не удалась или файл не виден с хоста"
        c_info "вывод: ${out}"
        c_info "для варианта A нужен ReadWritePaths=/tmp (ProtectSystem=strict)"
        exit 1
    fi
    c_ok "файл создан изнутри и ВИДЕН С ХОСТА: ${out}/${marker}"
    rm -f "${out}/${marker}"
    c_ok "проверка пройдена"
}

cmd="${1:-}"
case "$cmd" in
    A|a|B|b) apply_variant "$cmd" ;;
    status)  show_status ;;
    revert)  revert_all ;;
    check)   do_check ;;
    ""|help|-h|--help)
        sed -n '2,17p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
        ;;
    *) c_fail "неизвестная команда: $cmd"; exit 1 ;;
esac
