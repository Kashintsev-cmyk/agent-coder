"""Инструмент: выполнение команды в терминале с whitelist безопасных команд."""
from __future__ import annotations

import re
from typing import Any

from agent_coder.shell_runner import run_shell
from agent_coder.tools.registry import Tool, ToolContext

TIMEOUT_SEC = 300
MAX_OUTPUT = 30_000


# --- Whitelist безопасных read-only команд ---
# Если команда матчится хотя бы одному шаблону И не содержит опасных символов,
# она выполняется БЕЗ approval.

# Опасные символы: редиректы, подстановки, точки с запятой, backticks, &&/||.
_FORBIDDEN = re.compile(r"[;&`$><\n(){}]|&&|\|\|")

# Секретные пути — всегда требуют approval.
_FORBIDDEN_PATHS = re.compile(
    r"(/etc/(shadow|gshadow|sudoers)(\.d)?|"
    r"\.ssh/|id_rsa|id_ed25519|id_ecdsa|"
    r"\.env(\.|$|\s)|\.credentials|\.netrc|\.pgpass|\.my\.cnf|"
    r"\.aws/|\.gcloud/|\.kube/|\.docker/config\.json|"
    r"\.npmrc|\.pypirc|\.git-credentials|"
    r"/proc/(?:\d+|self|thread-self)/environ)",
    re.IGNORECASE,
)

# Hard-block: команды, запрещённые ВСЕГДА, даже с yolo.
# Эти команды стирают данные без возможности восстановления —
# никакая автономность не оправдывает их выполнение агентом.
_HARD_BLOCK = re.compile(
    r"(?:"
    r"\brm\s+-[a-z]*[rf][a-z]*\s+/(?:\s|$)"   # rm -rf /
    r"|\brm\s+-[a-z]*[rf][a-z]*\s+/\*"        # rm -rf /*
    r"|\brm\s+-[a-z]*[rf][a-z]*\s+/(?:bin|boot|dev|etc|home|lib|proc|root|sbin|sys|usr|var)\b"
    r"|\bmkfs(?:\.\w+)?\b"                    # mkfs, mkfs.ext4, ...
    r"|\bdd\s+.*of=/dev/(?:sd|nvme|hd|vd)"
    r"|\bdd\s+.*of=/dev/mmcblk"
    r"|:\s*\(\s*\)\s*\{"                     # fork bomb :(){ :|:& };:
    r"|\bshred\s+/dev/"
    r"|\bwipefs\b"
    r"|\b>\s*/dev/(?:sd|nvme|hd)"
    r")",
    re.IGNORECASE,
)


# Каждый шаблон — regex, заякоренный с начала строки (^).
_SAFE_PATTERNS: list[re.Pattern] = [
    # --- Ресурсы ---
    re.compile(r"^df(\s+-\w+)*(\s+\S+)*$"),
    re.compile(r"^free(\s+-\w+)*$"),
    re.compile(r"^uptime(\s+-\w+)*$"),
    re.compile(r"^du(\s+-\w+)*(\s+\S+)+$"),
    re.compile(r"^lsblk(\s+-\w+)*$"),
    re.compile(r"^blkid(\s+\S+)?$"),
    re.compile(r"^swapon(\s+--show|\s+-s)?$"),
    re.compile(r"^swapoff\s+--show$"),
    re.compile(r"^mount(\s+\|\s+column\s+-t)?$"),
    re.compile(r"^findmnt(\s+\S+)?$"),
    re.compile(r"^stat\s+\S+$"),
    re.compile(r"^file\s+\S+$"),
    re.compile(r"^readlink\s+\S+$"),
    re.compile(r"^realpath\s+\S+$"),

    # --- Система ---
    re.compile(r"^whoami$"),
    re.compile(r"^id(\s+\S+)*$"),
    re.compile(r"^hostname(\s+-\w+)*$"),
    re.compile(r"^uname(\s+-\w+)*$"),
    re.compile(r"^date(\s+[+\-\d\s:%A-Za-z]+)?$"),
    re.compile(r"^timedatectl(\s+status)?$"),
    re.compile(r"^locale(\s+-\w+)?$"),

    # --- /proc: глобальные ---
    re.compile(r"^cat\s+/proc/(loadavg|meminfo|cpuinfo|uptime|version|swaps|vmstat|"
               r"zoneinfo|filesystems|mounts|diskstats|interrupts|softirqs|buddyinfo|"
               r"slabinfo|pagetypeinfo|modules|partitions)$"),
    re.compile(r"^cat\s+/proc/pressure/(cpu|memory|io)$"),

    # --- /proc: процесс ---
    re.compile(r"^cat\s+/proc/\d+/(status|smaps|smaps_rollup|statm|stat|cmdline|comm|"
               r"io|limits|maps|numa_maps|wchan|oom_score|oom_adj|oom_score_adj|"
               r"cgroup|mountinfo|net/dev|sched|schedstat|stack|syscall|timers|"
               r"uid_map|gid_map|autogroup)$"),
    re.compile(r"^cat\s+/sys/fs/cgroup/[^\s]+$"),
    re.compile(r"^ls\s+/proc/\d+/$"),
    re.compile(r"^ls(\s+-\w+)*\s+/proc/\d+/(fd|cwd|exe|root|task)/$"),
    re.compile(r"^ls(\s+-\w+)*\s+/sys/fs/cgroup/[^\s]+$"),

    # --- Процессы ---
    re.compile(r"^ps\s+aux(\s+--sort=\S+)?$"),
    re.compile(r"^ps\s+-ef$"),
    re.compile(r"^ps\s+-eo\s+\S+$"),
    re.compile(r"^ps\s+-p\s+\d+$"),
    re.compile(r"^ps\s+-o\s+\S+\s+-p\s+\d+$"),
    re.compile(r"^pgrep(\s+-\w+)*\s+\S+$"),
    re.compile(r"^pstree(\s+-\w+)*(\s+\d+)?$"),
    re.compile(r"^top\s+-bn1(\s+-o\s+\S+)?$"),

    # --- Systemd ---
    re.compile(r"^systemctl\s+status\s+[\w@.\-]+"
               r"(\s+--no-pager|\s+--lines=\d+|\s+-n\s*\d+|\s+-l)*$"),
    re.compile(r"^systemctl\s+is-active\s+[\w@.\-]+$"),
    re.compile(r"^systemctl\s+is-enabled\s+[\w@.\-]+$"),
    re.compile(r"^systemctl\s+show\s+[\w@.\-]+$"),
    re.compile(r"^systemctl\s+cat\s+[\w@.\-]+$"),
    re.compile(r"^systemctl\s+list-units(\s+--\S+)*$"),
    re.compile(r"^systemctl\s+list-unit-files(\s+--\S+)*$"),
    re.compile(r"^systemctl\s+list-timers(\s+--\S+)*$"),
    re.compile(r"^systemctl\s+--failed(\s+--no-pager)?$"),
    re.compile(r"^systemd-cgtop(\s+-\w+)*$"),

    # --- Docker ---
    re.compile(r"^docker\s+ps(\s+-\w+)*$"),
    re.compile(r"^docker\s+images(\s+-\w+)*$"),
    re.compile(r"^docker\s+stats\s+--no-stream$"),
    re.compile(r"^docker\s+inspect\s+\S+$"),
    re.compile(r"^docker\s+logs(\s+--tail\s+\d+)?\s+\S+$"),

    # --- Сеть ---
    re.compile(r"^ss(\s+-\w+)*$"),
    re.compile(r"^netstat\s+-t\w*$"),
    re.compile(r"^ip\s+(-br\s+)?(a|addr|route|link|neigh)(\s+\S+)*$"),
    re.compile(r"^ping\s+-c\s+\d+\s+\S+$"),

    # --- Логи ---
    re.compile(r"^journalctl(\s+-u\s+[\w@.\-]+)?(\s+-n\s+\d+)?"
               r"(\s+--since\s+\S+)?(\s+--no-pager)?(\s+-p\s+\w+)?$"),
    re.compile(r"^dmesg(\s+\S+)*$"),

    # --- Файлы (read-only) ---
    re.compile(r"^ls(\s+-\w+)*(\s+\S+)*$"),
    re.compile(r"^pwd$"),
    re.compile(r"^cat\s+\S+$"),
    re.compile(r"^head\s+(-\d+\s+)?\S+$"),
    re.compile(r"^tail\s+(-\d+\s+)?\S+$"),
    re.compile(r"^wc\s+(-\w+\s+)?\S+$"),
    re.compile(r"^find\s+\S+(\s+-name\s+\S+)?(\s+-type\s+\w+)?(\s+-maxdepth\s+\d+)?$"),
    re.compile(r"^tree(\s+-\w+)*(\s+\S+)?$"),

    # --- Утилиты ---
    re.compile(r"^which\s+\S+$"),
    re.compile(r"^nvidia-smi(\s+\S+)*$"),
    re.compile(r"^lsof\s+-p\s+\d+$"),
    re.compile(r"^pmap(\s+-x)?\s+\d+$"),
    re.compile(r"^smem(\s+-\w+)*$"),
    re.compile(r"^vmstat(\s+\d+(\s+\d+)?)?$"),
    re.compile(r"^iostat(\s+-\w+)*$"),
    re.compile(r"^mpstat(\s+\d+(\s+\d+)?)?$"),
]


# Разрешённые команды для pipe: только read-only.
_ALLOWED_PIPES = re.compile(
    r"^(ps|cat|free|df|uptime|ss|ip|ls|head|tail|grep|wc|sort|uniq|cut|tr|"
    r"systemctl|journalctl|docker|lsof|du|smem|pmap|vmstat|iostat|mpstat|"
    r"which|uname|id|whoami|hostname|date|findmnt|stat|file|"
    r"lsblk|blkid|swapon|pstree|tree|readlink|realpath|locale|"
    r"find|sort|column|nl|rev|xxd|od|hexdump)\s+",
    re.IGNORECASE,
)


def _split_pipes_safe(cmd: str) -> list[str] | None:
    """Разбить команду по `|` вне кавычек.

    Возвращает None, если кавычки не сбалансированы (подозрительная команда).
    Уважает одинарные и двойные кавычки, escape через \\.
    """
    parts: list[str] = []
    current: list[str] = []
    in_single = False
    in_double = False
    escape = False

    for ch in cmd:
        if escape:
            current.append(ch)
            escape = False
            continue
        if ch == "\\" and not in_single:
            current.append(ch)
            escape = True
            continue
        if ch == "'" and not in_double:
            in_single = not in_single
            current.append(ch)
            continue
        if ch == '"' and not in_single:
            in_double = not in_double
            current.append(ch)
            continue
        if ch == "|" and not in_single and not in_double:
            parts.append("".join(current).strip())
            current = []
            continue
        current.append(ch)

    if in_single or in_double:
        return None

    last = "".join(current).strip()
    if last:
        parts.append(last)
    return parts


def _is_safe_command(args: dict[str, Any]) -> bool:
    """Проверяет, безопасна ли команда (whitelisted, без опасных символов)."""
    cmd = (args.get("command") or "").strip()
    if not cmd or len(cmd) > 300:
        return False

    # Секретные пути — всегда требуют approval
    if _FORBIDDEN_PATHS.search(cmd):
        return False

    # Pipe — если есть `|` (не только внутри кавычек), разбираем аккуратно
    if "|" in cmd:
        parts = _split_pipes_safe(cmd)
        if parts is None:
            return False
        for part in parts:
            if not part:
                return False
            if not _ALLOWED_PIPES.match(part):
                return False
            # Внутри части запрещены: ; & ` $ < > ( ) { } (| уже разделили)
            if re.search(r"[;&`$<>(){}]", part):
                return False
        return True

    # Одиночная команда
    if _FORBIDDEN.search(cmd):
        return False
    return any(p.match(cmd) for p in _SAFE_PATTERNS)




async def _execute(args: dict[str, Any], ctx: ToolContext) -> str:
    command = args.get("command", "").strip()
    if not command:
        return "Ошибка: пустая команда"

    # Hard-block: никогда не выполняем — ни с yolo, ни с approval.
    if _HARD_BLOCK.search(command):
        return (
            "🛑 Команда заблокирована на уровне безопасности: "
            "потенциально разрушительная операция "
            "(rm -rf /, mkfs, dd на диск, fork bomb и т.п.). "
            "Выполни вручную, если уверен."
        )

    result = await run_shell(
        command,
        cwd=ctx.workspace,
        timeout=TIMEOUT_SEC,
        max_bytes=MAX_OUTPUT,
        mask=True,
    )
    if result.timed_out:
        return f"Команда прервана по таймауту ({TIMEOUT_SEC}с)"
    if result.error:
        return f"Ошибка запуска: {result.error}"

    parts = [f"$ {command}", f"exit code: {result.returncode}"]
    if result.stdout.strip():
        parts.append(f"--- stdout ---\n{result.stdout}")
    if result.stderr.strip():
        parts.append(f"--- stderr ---\n{result.stderr}")
    return "\n".join(parts)


run_command_tool = Tool(
    name="run_command",
    description=(
        "Выполнить shell-команду в рабочей директории. "
        "Безопасные read-only команды (df, free, uptime, ps, ls, cat, "
        "systemctl status/show, docker ps, ss, ip, journalctl, pgrep, lsof, "
        "pmap, lsblk, stat и т.п.) выполняются без подтверждения. "
        "Pipe (|) между двумя такими командами тоже разрешён. "
        "Всё остальное (rm, kill, sudo, redirects, `;`) требует approval. "
        "Таймаут 300 секунд. Вывод ограничен 30000 байт на поток."
    ),
    parameters={
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "Команда целиком"},
        },
        "required": ["command"],
    },
    executor=_execute,
    requires_approval=True,
    read_only=False,
    is_safe_call=_is_safe_command,
)
