"""Реестр серверных диагностических команд.

Все команды выполняются напрямую через subprocess (минуя LLM) — это бесплатно
и мгновенно. Никаких approval не требуется, потому что команды заранее
отобраны как read-only и безопасные.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ServerOp:
    id: str
    label: str        # текст кнопки
    cmd: str          # точная shell-команда
    description: str  # что показывает
    group: str = "main"


SERVER_OPS: list[ServerOp] = [
    # --- Ресурсы ---
    ServerOp("disk",      "🖥 Диск",        "df -h",                                     "Свободное место"),
    ServerOp("mem",       "🧠 Память",      "free -h",                                   "ОЗУ и swap"),
    ServerOp("load",      "⚙️ Load",        "uptime && cat /proc/loadavg",               "Load average и uptime"),
    ServerOp("top_cpu",   "🔥 Top CPU",     "ps aux --sort=-%cpu | head -12",            "Топ процессов по CPU"),
    ServerOp("top_mem",   "💾 Top RAM",     "ps aux --sort=-%mem | head -12",            "Топ процессов по памяти"),

    # --- Система ---
    ServerOp("whoami",    "👤 Кто я",       "whoami && id && hostname",                  "Пользователь и хост"),
    ServerOp("uname",     "🐧 Ядро",        "uname -a",                                  "Информация о ядре"),
    ServerOp("date",      "🕐 Время",       "date && timedatectl 2>/dev/null | head -5", "Системное время"),

    # --- Сеть ---
    ServerOp("ports",     "🌐 Порты",       "ss -tuln",                                  "Слушающие порты"),
    ServerOp("net",       "🔌 Сеть",        "ip -br a 2>/dev/null || ip a",              "Сетевые интерфейсы"),

    # --- Systemd ---
    ServerOp("systemd",   "📋 Systemd",     "systemctl list-units --type=service --state=running --no-pager 2>/dev/null | head -30",
                                                  "Активные сервисы"),
    ServerOp("failed",    "❌ Failed",      "systemctl --failed --no-pager 2>/dev/null", "Проваленные юниты"),

    # --- Docker ---
    ServerOp("docker",    "🐳 Docker",      "docker ps --format 'table {{.Names}}\\t{{.Status}}\\t{{.Image}}' 2>/dev/null || echo 'Docker недоступен'",
                                                  "Запущенные контейнеры"),

    # --- Логи ---
    ServerOp("journal",   "📜 Журнал",      "journalctl -n 30 --no-pager 2>/dev/null",   "Последние 30 строк журнала"),
    ServerOp("dmesg",     "🔍 dmesg",       "dmesg 2>/dev/null | tail -30 || echo 'Нет доступа к dmesg'", "Последние сообщения ядра"),
]


def find_op(op_id: str) -> ServerOp | None:
    for op in SERVER_OPS:
        if op.id == op_id:
            return op
    return None
