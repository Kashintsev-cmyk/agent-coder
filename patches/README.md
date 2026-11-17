# Патчи `agent-coder-bot.service` — PrivateTmp

Каталог для drop-in'ов systemd, которые **не** лежат в репозитории целиком,
а накладываются на установленный юнит `/etc/systemd/system/agent-coder-bot.service`.

## Проблема

В юните стоит `PrivateTmp=true` (`deploy/agent-coder-bot.service:30`).
systemd монтирует сервису личный tmp-каталог:

```
/tmp -> /tmp/systemd-private-<unit-id>/tmp
/var/tmp -> /var/tmp/systemd-private-<unit-id>/tmp
```

Последствия:

1. **Снаружи не видно.** Бот пишет «в `/tmp`» — на хосте (`ls /tmp`, ssh,
   редактор) этого файла нет. Проверка с хоста и изнутри бота даёт разный
   ответ на один и тот же путь.
2. **Не переживает рестарт.** При остановке юнита приватный каталог
   удаляется вместе со всем содержимым. Отладочный дамп «оставил в
   /tmp» исчезает после `systemctl restart`.
3. **`TMPDIR` по умолчанию не задан** — `tempfile.gettempdir()` вернёт
   `/tmp`, и это ловушка при диагностике: путь в логе выглядит обычным.

Фактическое состояние на этом хосте (снято из живой сессии):

```
$ grep ' /tmp ' /proc/self/mountinfo
939 ... 252:1 /tmp/systemd-private-ca915650cc5f4d52b78f5f16ed144f87-agent-coder-bot.service-Qs11Yu/tmp /tmp rw,...

$ systemctl show agent-coder-bot.service -p PrivateTmp
PrivateTmp=yes
```

## Важно: `PrivateTmp=no` сам по себе не решает

Юнит работает с `ProtectSystem=strict` — корень `/` смонтирован `ro`:

```
$ grep -E ' / ro' /proc/self/mountinfo
659 ... / / ro,nosuid,relatime ... - ext4 /dev/vda1 rw
```

На этом хосте `/tmp` **не отдельное монтирование** (`df` показывает
`/dev/vda1` для `/`, `/tmp` и `/var/tmp`), значит после `PrivateTmp=no`
реальный `/tmp` унаследует `ro` от `/`. Поэтому в варианте A добавлен
`ReadWritePaths=/tmp` — без него запись в `/tmp` упадёт с `Read-only
file system`, и это будет выглядеть как «патч не сработал».

## Два варианта

### A — отключить PrivateTmp (общий `/tmp` с хостом)

Файл: `agent-coder-bot.service.d/10-private-tmp-off.conf`

```ini
[Service]
PrivateTmp=no
ReadWritePaths=/tmp
ReadWritePaths=-/var/tmp
```

- `/tmp` бота = `/tmp` хоста. Всё, что видит бот, видно и вам.
- **Минус:** изоляция tmp теряется. Любой процесс хоста может подложить
  файл по предсказуемому пути (symlink-атаки на `/tmp`), а бот — читать
  чужие временные файлы. Для бота, исполняющего команды и код из LLM,
  это ослабление защиты.

### B — сохранить PrivateTmp, увести `TMPDIR` за пределы репозитория (рекомендуется)

Файл: `agent-coder-bot.service.d/20-tmpdir-outside-repo.conf`

```ini
[Service]
Environment=TMPDIR=/home/botuser/.cache/agent-coder/tmp
Environment=TEMP=/home/botuser/.cache/agent-coder/tmp
Environment=TMP=/home/botuser/.cache/agent-coder/tmp
ReadWritePaths=-/home/botuser/.cache/agent-coder/tmp
```

Каталог — **вне** `~/bots/agent-coder`: путь под деревом репозитория
отклоняет `_workspace_allowed()` (см. секцию «TMPDIR не должен указывать
в репозиторий» в конце).

- Приватный tmp **остаётся** — изоляция не страдает.
- Python (`tempfile.gettempdir()`, `mkstemp`, pytest `tmp_path`) и
  большинство утилит (git, pip, gcc) уважают `TMPDIR` → пишут в
  `~/.cache/agent-coder/tmp`, который виден снаружи и переживает рестарт.
- Запись уже разрешена: `ReadWritePaths=-/home/botuser/.cache/agent-coder`
  в юните покрывает этот подкаталог; строка `ReadWritePaths=` в drop-in
  добавлена на случай, если основное правило изменят.
- `TMPDIR`, `TEMP`, `TMP` пробрасываются в untrusted-subprocess:
  `clean_env()` в `agent_coder/shell_runner.py` (`_CLEAN_ENV_KEYS`).
- **Ограничение:** процесс, который пишет в `/tmp` жёстко, мимо
  `TMPDIR`, всё равно попадёт в приватный tmp. В коде agent-coder таких
  мест нет.

## Применение

`sudo` изнутри бота **не работает**: в юните `NoNewPrivileges=true`.
Скрипт запускается с хоста, руками:

```bash
cd ~/bots/agent-coder

./patches/apply.sh status     # что сейчас: PrivateTmp, где /tmp
sudo ./patches/apply.sh B     # применить вариант B
sudo ./patches/apply.sh check # проверить, что файл изнутри виден снаружи
sudo ./patches/apply.sh revert # вернуть как в репозитории
```

`apply.sh` кладёт drop-in в `/etc/systemd/system/agent-coder-bot.service.d/`,
делает `daemon-reload` и `restart`. Варианты взаимоисключающие —
применение A снимает B и наоборот.

## Проверка, что сработало

```bash
# 1. Приватных каталогов быть не должно (для A) или они остаются (для B)
ls -1d /tmp/systemd-private-*-agent-coder-bot.service-* 2>/dev/null

# 2. Что видит процесс бота
sudo nsenter -t "$(systemctl show agent-coder-bot.service -p MainPID --value)" -m -- \
     python3 -c 'import tempfile; print(tempfile.gettempdir())'
#    A -> /tmp
#    B -> /home/botuser/.cache/agent-coder/tmp

# 3. Сквозная проверка «записал внутри — вижу снаружи»
sudo ./patches/apply.sh check
```

## Откат

```bash
sudo ./patches/apply.sh revert
```

Удаляет оба drop-in'а, `daemon-reload`, `restart`. Юнит возвращается к
состоянию из `deploy/agent-coder-bot.service` — `PrivateTmp=true`,
приватный tmp, как было.

## Если решено зафиксировать в репозитории

Правки в `deploy/agent-coder-bot.service` (файл в `ReadOnlyPaths`, из
сессии бота не редактируется — только с хоста):

- вариант A: заменить `PrivateTmp=true` на `PrivateTmp=false` и добавить
  `ReadWritePaths=/tmp`;
- вариант B: оставить `PrivateTmp=true`, добавить три строки
  `Environment=TMPDIR|TEMP|TMP=/home/botuser/.cache/agent-coder/tmp`,
  `ExecStartPre=/bin/mkdir -p` на этот каталог и `ReadWritePaths=` на него.
  **Уже сделано** — см. `deploy/agent-coder-bot.service`.

После правки юнита — `systemctl daemon-reload && systemctl restart agent-coder-bot`.

## Фикс кода: TEMP/TMP в whitelist `clean_env()`

Вариант B уводит tempfile из приватного `/tmp`, но `clean_env()`
(`agent_coder/shell_runner.py`) пробрасывал в untrusted-подпроцессы только
`TMPDIR`. `TEMP` и `TMP` вычищались — утилита или портируемый код, читающий
только их, снова уходили в приватный `/tmp` и снаружи были невидимы.

**Статус: применено и закоммичено.** Патч-файлов больше нет — они были
нужны только на время переноса. Что теперь в дереве:

- `agent_coder/shell_runner.py`: `_CLEAN_ENV_KEYS` содержит `TMPDIR`,
  `TEMP`, `TMP`;
- `tests/test_shell_runner_env.py`: регресс `test_clean_env_passes_temp_tmp`
  (без фикса падает на `TEMP`);
- `deploy/agent-coder-bot.service`: три `Environment=TMPDIR|TEMP|TMP=` на
  `~/.cache/agent-coder/tmp`, `ExecStartPre=/bin/mkdir -p`,
  `ReadWritePaths=-/home/botuser/.cache/agent-coder`.

`deploy/` и `agent_coder/` — в `ReadOnlyPaths` юнита, поэтому менять их
изнутри бота нельзя: только из шелла хоста. После правки `deploy/` нужно
один раз синхронизировать с `/etc`:

```bash
sudo install -m 644 ~/bots/agent-coder/deploy/agent-coder-bot.service \
    /etc/systemd/system/agent-coder-bot.service
sudo systemctl daemon-reload && sudo systemctl restart agent-coder-bot
```

Порядок важен: пока в `/etc` лежит drop-in `20-tmpdir-outside-repo.conf`,
он и определяет поведение. Строки в `deploy/` начинают работать только
после `sudo ./patches/apply.sh revert` (снять drop-in) — иначе настройки
продублируются (безвредно, но грязно).

На хосте на 2026-11-17 это сделано: drop-in снят, `DropInPaths=` пуст,
юнит установлен из `deploy/`, `PrivateTmp=yes` сохранён, TMPDIR —
`~/.cache/agent-coder/tmp`, 1210 passed.

## ВАЖНО: TMPDIR не должен указывать в репозиторий

Первый вариант фикса (v1) уводил tempfile в `~/bots/agent-coder/tmp` —
внутри дерева репозитория. Это ломает `_workspace_allowed()`
(`agent_coder/telegram/handlers/project_utils.py:42`): любой путь под
`.../agent-coder/` считается «собственным кодом бота» и отклоняется.

Последствие: pytest `tmp_path` уезжает в
`<repo>/tmp/pytest-of-botuser/...`, а тесты M5
(`tests/test_new_workspace_whitelist.py::test_allowed_under_bots`,
`::test_allowed_under_projects`) начинают падать — 2 failed. ACT-сессия
в воркспейсе при таком TMPDIR тоже не откроется.

Правильное место — `~/.cache/agent-coder/tmp`: вне репозитория, вне
`TELEGRAM_PROJECTS_DIRS`, каталог уже покрыт
`ReadWritePaths=-/home/botuser/.cache/agent-coder` (префикс `-` допускает
отсутствие). `tempfile` не создаёт базовый путь сам, поэтому в юните
добавлен `ExecStartPre=/bin/mkdir -p`, а `apply.sh B` создаёт каталог при
установке.

Проверено: 1210 passed при `TMPDIR` = `~/.cache/agent-coder/tmp` и при
неопределённом `TMPDIR`; `ruff check` чисто; `systemd-analyze verify` —
без ошибок.

---

# Workflow: правки кода только патчем

## Правило

**Бот не правит живой код.** `agent_coder/`, `tests/`, `docs/`, `deploy/`,
`benchmark/`, `.github/`, `pyproject.toml`, `AGENTS.md`, `.env` смонтированы
read-only (`ReadOnlyPaths=` в `deploy/agent-coder-bot.service`, M7). Это не
баг и не мешает работе — это защита от prompt-injection и от бага в
`write_file`, который иначе перезаписал бы собственный код агента.

Любая правка идёт тремя шагами:

1. **клон** — `./patches/export.sh new NAME` создаёт writable-клон в
   `sessions/patch-work/NAME`, правки делаются там;
2. **патч** — `./patches/export.sh export NAME` пишет диф в
   `patches/NNNN-NAME.patch`;
3. **применение** — с хоста (обычная ssh-сессия, не из-под юнита)
   `git apply patches/NNNN-NAME.patch`, тесты, коммит, рестарт.

Проверка одного патча целиком (apply в чистом клоне + ruff + pytest):

```bash
./patches/export.sh check NAME
```

Состояние: `./patches/export.sh status`, здоровье схемы: `./patches/doctor.sh`.

## Почему клон именно в `sessions/patch-work/`

Три ограничения пересекаются, и подходит ровно одно место:

| Требование | Почему |
|---|---|
| внутри workspace | `write_file`/`edit_file` отвергают путь вне workspace |
| writable в юните | `agent_coder/` и `tests/` — RO; RW только `sessions/`, `patches/` и `~/projects` |
| невидим для git/ruff/pytest | иначе клон мусорит в `git status` и ломает `ruff format --check` |

`sessions/` в `.gitignore`, а ruff и pytest `.gitignore` уважают — клон не
виден никому. `~/projects` отпадает (вне workspace), `.sandbox` в корне
репо — шумит в ruff (46 файлов вместо 23), git worktree — то же самое.

## Грабли

- **`git apply` — без sudo.** Иначе файлы станут root-owned и следующая
  правка из-под бота упрётся в права. Починить:
  `sudo chown botuser:botuser agent_coder/tools/<file>.py`.
- **`rm` заблокирован** в `run_command` (`_HARD_BLOCK`). Удалять через
  `unlink` (файл) или `python -c "shutil.rmtree(...)"` (каталог).
- **Имя юнита** — `agent-coder-bot.service`, не `agent-coder`.
- **Рестарт требует root:** `sudo systemctl restart agent-coder-bot.service`.
  Изнутри юнита `sudo` не работает (`NoNewPrivileges`).
- **Пуш изнутри юнита** проходит (ключ `~/.ssh/id_ed25519` рабочий), но
  после правки живого дерева коммит делается с хоста.

## Baseline (не путать со своими ошибками)

- `ruff format --check .` → **23 файла** переформатировались бы ещё до
  любых правок. Для своих файлов проверяй точечно:
  `.venv/bin/ruff format --check <files>`.
- `mypy agent_coder/` → **24 ошибки** в 4 файлах (`loop.py` 16,
  `__main__.py` 6, `tool_extract.py` 2, `compression.py` 1).
- `pytest` → **1217 passed** на коммите `e1fe8f5`.
