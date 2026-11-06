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
    r"\brm\s+-[a-z]*[rf][a-z]*\s+/(?:\s|$)"  # rm -rf /
    r"|\brm\s+-[a-z]*[rf][a-z]*\s+/\*"  # rm -rf /*
    r"|\brm\s+-[a-z]*[rf][a-z]*\s+/(?:bin|boot|dev|etc|home|lib|proc|root|sbin|sys|usr|var)\b"
    r"|\bmkfs(?:\.\w+)?\b"  # mkfs, mkfs.ext4, ...
    r"|\bdd\s+.*of=/dev/(?:sd|nvme|hd|vd)"
    r"|\bdd\s+.*of=/dev/mmcblk"
    r"|:\s*\(\s*\)\s*\{"  # fork bomb :(){ :|:& };:
    r"|\bshred\s+/dev/"
    r"|\bwipefs\b"
    r"|\b>\s*/dev/(?:sd|nvme|hd)"
    r")",
    re.IGNORECASE,
)


# B9.2g.4: rm с -r И -f на критичный путь. Regex _HARD_BLOCK
# (`-[a-z]*[rf][a-z]*`) матчил только слитный кластер `-rf`/`-fr` —
# `rm -r -f /` (раздельные флаги) проходил. Хелпер парсит токены.
_RM_CRITICAL_PATHS = frozenset(
    {
        "/",
        "/bin",
        "/boot",
        "/dev",
        "/etc",
        "/home",
        "/lib",
        "/lib64",
        "/opt",
        "/proc",
        "/root",
        "/run",
        "/sbin",
        "/srv",
        "/sys",
        "/usr",
        "/var",
    }
)
_RM_INVOCATION = re.compile(r"(?:^|[\s;|&()])rm\s+([^\n;|&()]+)")


def _is_destructive_rm(command: str) -> bool:
    """rm с -r/-R (recursive) И -f/--force на критичный путь."""
    for m in _RM_INVOCATION.finditer(command):
        tokens = m.group(1).split()
        has_r = has_f = False
        targets: list[str] = []
        after_ddash = False
        for tok in tokens:
            if after_ddash:
                targets.append(tok)
                continue
            if tok == "--":
                after_ddash = True
                continue
            if tok.startswith("--"):
                low = tok.lower()
                if low == "--recursive":
                    has_r = True
                elif low == "--force":
                    has_f = True
                continue
            if tok.startswith("-") and len(tok) > 1:
                if "r" in tok or "R" in tok:
                    has_r = True
                if "f" in tok:
                    has_f = True
                continue
            targets.append(tok)
        if not (has_r and has_f):
            continue
        for t in targets:
            base = t.rstrip("/*")
            if (
                t in _RM_CRITICAL_PATHS
                or base in _RM_CRITICAL_PATHS
                or t.startswith(("/*", "~/"))
                or t in ("~", "$HOME")
            ):
                return True
    return False


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
    re.compile(
        r"^date(?:\s+(?:-u|--utc|--universal|--rfc-email|-R|"
        r"--rfc-3339=\w+|-I(?:seconds|secs|ns|date|hours|minutes)?|"
        r"--iso-8601=\S+|--debug|--date=\S+|-d\s+\S+|\+\S+))*$"
    ),
    re.compile(r"^timedatectl(\s+status)?$"),
    re.compile(r"^locale(\s+-\w+)?$"),
    # --- /proc: глобальные ---
    re.compile(
        r"^cat\s+/proc/(loadavg|meminfo|cpuinfo|uptime|version|swaps|vmstat|"
        r"zoneinfo|filesystems|mounts|diskstats|interrupts|softirqs|buddyinfo|"
        r"slabinfo|pagetypeinfo|modules|partitions)$"
    ),
    re.compile(r"^cat\s+/proc/pressure/(cpu|memory|io)$"),
    # --- /proc: процесс ---
    re.compile(
        r"^cat\s+/proc/\d+/(status|smaps|smaps_rollup|statm|stat|cmdline|comm|"
        r"io|limits|maps|numa_maps|wchan|oom_score|oom_adj|oom_score_adj|"
        r"cgroup|mountinfo|net/dev|sched|schedstat|stack|syscall|timers|"
        r"uid_map|gid_map|autogroup)$"
    ),
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
    re.compile(
        r"^systemctl\s+status\s+[\w@.\-]+"
        r"(\s+--no-pager|\s+--lines=\d+|\s+-n\s*\d+|\s+-l)*$"
    ),
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
    re.compile(
        r"^ip(?:\s+-br)?\s+"
        r"(?:a|addr|address|link|l|neigh|neighbor|n|route|r|rule|"
        r"netns|net|maddr|maddress|mroute|tunnel|tun|vrf|xfrm)"
        r"(?:\s+(?:show|list|get)(?:\s+\S+)*)?$"
    ),
    re.compile(r"^ping\s+-c\s+\d+\s+\S+$"),
    # --- Логи ---
    re.compile(
        r"^journalctl(\s+-u\s+[\w@.\-]+)?(\s+-n\s+\d+)?"
        r"(\s+--since\s+\S+)?(\s+--no-pager)?(\s+-p\s+\w+)?$"
    ),
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
    re.compile(
        r"^nvidia-smi(?:\s+(?:-q|--query|--query-gpu=\S+|"
        r"--query-compute-apps=\S+|--format=\S+|-L|--list-gpus|"
        r"-l\s+\d+|--loop=\d+|-i\s+\S+|--id=\S+|"
        r"--filename=\S+|-h|--help))*$"
    ),
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
    r"find|sort|column|nl|rev|xxd|od|hexdump|"
    # B9.2g.6: текстовые утилиты (sed/awk дополнительно
    # проверяются в _is_read_only_part).
    r"sed|awk|rg|diff|cmp|comm|git)(?:\s+|$)",
    re.IGNORECASE,
)


# B9.2g.2 (BLOCKER): мутирующие подкоманды в pipe — запрещены.
# _ALLOWED_PIPES матчил только basename (docker, systemctl, ip, sort,
# find), поэтому `docker rm -f X | cat`, `systemctl restart foo | cat`,
# `ip link set eth0 down | cat`, `sort -o out.txt | ...`,
# `find . -delete | ...` проходили pipe-ветку без _SAFE_PATTERNS.
_MUTATING_PIPE_PATTERNS = re.compile(
    r"^(?:"
    r"docker\s+(?:rm|rmi|kill|stop|restart|start|run|exec|create|commit|"
    r"build|push|pull|tag|update|prune|system|volume|network|secret|"
    r"config|swarm|stack|node|service|image|container|plugin|context|"
    r"trust|rename|pause|unpause|attach|cp|diff|export|import|load|"
    r"save|login|logout|wait|events)\b"
    r"|systemctl\s+(?:start|stop|restart|reload|try-restart|"
    r"reload-or-restart|force-reload|enable|disable|reenable|preset|"
    r"mask|unmask|kill|link|revert|set-property|edit|isolate|halt|"
    r"poweroff|reboot|suspend|hibernate|hybrid-sleep|sleep|"
    r"daemon-reload|daemon-reexec|switch-root|exit|freeze|cancel|"
    r"clean|reset-failed|set-environment|unset-environment|"
    r"import-environment)\b"
    r"|ip\s+(?:link|addr|address|route|rule|neigh|netns|tunnel|"
    r"maddress|mroute|vrf|xdp)\s+(?:add|del|delete|set|change|replace|"
    r"flush|append|prepend|save|restore)\b"
    r"|date\s+-s\b"
    r"|timedatectl\s+set-\w+"
    r"|nvidia-smi\s+-(?:pl|pm|lgc|ac|rac|am)\b"
    r"|find\b[^\n;|&]*\s-(?:delete|exec(?:dir)?|ok(?:dir)?|fls|fprint0?|fprintf)\b"
    r"|sort\b[^\n;|&]*(?:\s-o\b|\s--output\b)"
    r"|git\s+(?:push|pull|fetch|commit|add|rm|mv|reset|checkout|"
    r"switch|rebase|merge|cherry-pick|stash|clean|gc|prune|"
    r"filter-branch|update-ref|replace|am|apply|bisect|notes|"
    r"submodule|worktree|config|branch\s+-[dDmMcC]|"
    r"tag\s+-[dasf]|remote\s+(?:add|remove|set-url|rename|set-head|prune)|"
    r"reflog\s+(?:expire|delete))\b"
    # B9.2g.6: rg умеет исполнение через --pre (препроцессор).
    r"|rg\s[^;|&]*--pre(?:-glob)?(?:=|\s)"
    r")",
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


def _is_safe_git_metadata(cmd: str) -> bool:
    """git read-only commands, которые выводят только метаданные/имена
    файлов, никогда — содержимое. Безопасны даже если pathspec содержит
    секретный путь (`.env` в `git log -- .env` только фильтрует, не читает).

    Блокируются: -p/--patch/-u/--full-diff/-U<n> (diff = content),
    :<path> (git show HEAD:.env), pipe (обрабатывается отдельно).
    Не в whitelist: show/diff/blame/grep/cat-file — они по умолчанию
    или по флагам выводят content.
    """
    if not cmd.startswith("git "):
        return False
    if _FORBIDDEN.search(cmd) or "|" in cmd:
        return False
    if re.search(
        r"(?:^|\s)(?:-p|--patch|-u|--full-diff|-U\d+|--unified)(?:\s|=|$)",
        cmd,
    ):
        return False
    # B9.2g.5 (BLOCKER): флаги записи в файл. `git log --output=/tmp/x`
    # пишет на диск, оставаясь в SAFE_SUBCOMMANDS (log). `-o` запрещаем
    # здесь же — для archive/bundle/format-patch/fast-export.
    if re.search(r"(?:^|\s)(?:--output(?:=|\s)|-o\s+\S)", cmd):
        return False
    if ":" in cmd:
        return False
    rest = cmd[len("git ") :].lstrip()
    if not rest:
        return False
    parts = rest.split()
    sub = parts[0]
    args = parts[1:]

    SAFE_SUBCOMMANDS = {
        "status",
        "log",
        "ls-files",
        "ls-tree",
        "rev-parse",
        "rev-list",
        "describe",
        "shortlog",
        "show-ref",
        "for-each-ref",
    }
    if sub in SAFE_SUBCOMMANDS:
        return True

    if sub == "branch":
        # только listing, без мутаций
        MUT = {
            "-d",
            "-D",
            "-m",
            "-M",
            "-c",
            "-C",
            "-f",
            "--force",
            "--delete",
            "--move",
            "--copy",
        }
        for a in args:
            if a in MUT or a.startswith(("-d", "-D", "-m", "-M", "-c", "-C")):
                return False
            if a.startswith(("--force", "--delete", "--move")):
                return False
        return True

    if sub == "tag":
        # listing: без args или с -l/--list/-n/-v; любой positional = создание
        MUT = {
            "-a",
            "-s",
            "-f",
            "-d",
            "-m",
            "-F",
            "-u",
            "-e",
            "--annotate",
            "--sign",
            "--force",
            "--delete",
            "--message",
            "--file",
            "--local-user",
        }
        for a in args:
            if a in MUT or a.startswith(
                ("-a", "-s", "-f", "-d", "-m", "-F", "-u", "-e")
            ):
                return False
            if a.startswith(("--annotate", "--sign", "--force", "--delete")):
                return False
            if not a.startswith("-"):
                return False  # positional = name → create
        return True

    if sub == "remote":
        MUT = {
            "add",
            "remove",
            "rm",
            "set-url",
            "rename",
            "set-head",
            "prune",
            "update",
        }
        for a in args:
            if a in MUT:
                return False
        return True

    return False


def _strip_sq(text: str) -> str:
    """Вырезать одинарные кавычки: внутри '...' shell НЕ интерпретирует
    $, {}, () и т.д. Двойные кавычки не трогаем: там shell расширяет
    $ и backticks."""
    return re.sub(r"'[^']*'", "''", text)


def _is_safe_sed(cmd: str) -> bool:
    """sed — read-only печать. Обязателен -n (выборочная печать).

    Запрещены: -i/--in-place, -f/--file (скрипт из файла), команды
    w/W (запись в файл), r/R (вставка файла), e (исполнение) —
    включая флаги s///w и s///e. Эвристика по позиции команды."""
    if not re.match(r"^sed(\s|$)", cmd):
        return False
    if re.search(r"\s-i|--in-place|\s-f(?:\s|=)|--file=", cmd):
        return False
    if not re.search(r"\s-n(?:\s|$)", cmd):
        return False
    if re.search(r"(?:^|[;{}\d,$/])\s*[wWrRe](?:\s|['\x22]|$)", cmd):
        return False
    return True


def _is_safe_awk(cmd: str) -> bool:
    """awk — read-only фильтрация/печать (B9.2g.6, строгая версия).

    Запрещены: -f/--file (программа из файла), -W exec/source,
    system(), getline, close(), fflush — внешние процессы/IO.
    Запрещены | и > в сырой строке: output-redirect awk
    ('{print > "f"}', '{print | "cmd"}') прячется в одинарных
    кавычках — _strip_sq в _is_read_only_part вырезал бы его.
    Известные FP (safe-side, уходят в approval): сравнения '> N'
    и разделитель -F'|' внутри программы.
    """
    if not re.match(r"^awk(\s|$)", cmd):
        return False
    if re.search(r"\s-f(?:\s|=|$)|--file=|\s-W\s*(?:exec|source)\b", cmd):
        return False
    if re.search(
        r"\bsystem\s*\(|\bgetline\b|\bclose\s*\(|\bfflush\b", cmd, re.IGNORECASE
    ):
        return False
    if re.search(r"[>|]", cmd):
        return False
    return True


def _is_read_only_part(part: str) -> bool:
    """Единый предикат read-only сегмента (B9.2g.5, шаг 2).

    Одна логика на часть pipe: запрещённые символы, basename,
    мутации, git-метаданные. Одиночная команда переедет сюда
    после расширения аргументов (шаг 3-4) — тогда whitelist
    перестанет разъезжаться между ветками.
    """
    if not part:
        return False
    # ; & ` $ < > ( ) { } и \n (\n — разделитель команд в shell,
    # B9.2g.2: `ls | cat\nrm -rf X` шёл в shell).
    if re.search(r"[;&`$<>(){}\n]", _strip_sq(part)):
        return False
    if not _ALLOWED_PIPES.match(part):
        return False
    if _MUTATING_PIPE_PATTERNS.match(part):
        return False
    # B9.2g.5: git в pipe — только read-only метаданные
    # (git log --output=..., archive, bundle, format-patch, fast-export).
    if part.startswith("git ") and not _is_safe_git_metadata(part):
        return False
    # B9.2g.6: sed/awk — только печать, без записи/исполнения.
    if part.startswith("sed") and not _is_safe_sed(part):
        return False
    if part.startswith("awk") and not _is_safe_awk(part):
        return False
    return True


def _is_safe_command(args: dict[str, Any]) -> bool:
    """Проверяет, безопасна ли команда (whitelisted, без опасных символов)."""
    cmd = (args.get("command") or "").strip()
    if not cmd or len(cmd) > 300:
        return False

    # Safe read-only git metadata (logs, ls-files, status) — не выводит
    # содержимое, pathspec-фильтр не читает файл. Bypass _FORBIDDEN_PATHS.
    if _is_safe_git_metadata(cmd):
        return True

    # Секретные пути — всегда требуют approval
    if _FORBIDDEN_PATHS.search(cmd):
        return False

    # B9.2g.2 (BLOCKER): hard-block ДО pipe-split. Раньше `rm -rf / | cat`
    # проходил pipe-ветку (она не звала _HARD_BLOCK), а _execute блокировал
    # только после того, как is_call_allowed в PLAN/AUDIT уже вернул True.
    if _HARD_BLOCK.search(cmd) or _is_destructive_rm(cmd):
        return False

    # Pipe — если есть `|` (не только внутри кавычек), разбираем аккуратно
    if "|" in cmd:
        parts = _split_pipes_safe(cmd)
        if parts is None:
            return False
        # B9.2g.5 (шаг 2): единый предикат на каждую часть.
        return all(_is_read_only_part(part) for part in parts)

    # Одиночная команда
    if _FORBIDDEN.search(_strip_sq(cmd)):
        return False
    return _is_read_only_part(cmd) or any(p.match(cmd) for p in _SAFE_PATTERNS)


async def _execute(args: dict[str, Any], ctx: ToolContext) -> str:
    command = args.get("command", "").strip()
    if not command:
        return "Ошибка: пустая команда"

    # Hard-block: никогда не выполняем — ни с yolo, ни с approval.
    if _HARD_BLOCK.search(command) or _is_destructive_rm(command):
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
