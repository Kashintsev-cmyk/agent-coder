# Окружение botuser (host-level)

Знание о host-tooling, которое **не хранится в репо** (root-owned или
multi-project). При переносе на другой хост — воспроизвести вручную.

## Docker read-only для /server (M6)

Бот **не входит** в docker-группу (членство = root-эквивалент через socket:
`docker run -v /:/host` даёт полный доступ к FS хоста). Read-only docker
диагностика (`/server → 🐳`) идёт через `sudo -n /usr/bin/docker …` с узким
sudoers — только `ps/images/stats --no-stream/inspect/logs`.

Установка (один раз, root) — команды с 4-пробельным отступом:

    # 1. Шаблон из репо
    sudo install -o root -g root -m 0440 deploy/agent-coder-docker.sudoers \
         /etc/sudoers.d/agent-coder-docker

    # 2. Синтаксис
    sudo visudo -c -f /etc/sudoers.d/agent-coder-docker

    # 3. Путь docker — сверить с `which docker` (шаблон: /usr/bin/docker)
    which docker

    # 4. Smoke от botuser
    sudo -u botuser sudo -n /usr/bin/docker ps

Если docker в `/usr/local/bin/docker` — поправить пути в
`deploy/agent-coder-docker.sudoers` **и** в
`agent_coder/tools/run_command.py` (`_SAFE_PATTERNS`, те же regex).

Строки в `run_command._SAFE_PATTERNS` строгие (с `$`): `sudo -n
/usr/bin/docker ps; rm -rf /` не пройдёт — `_FORBIDDEN` блокирует `;`/`&&`/
`>`, а pipe с `sudo` не матчится `_ALLOWED_PIPES`. Write-команды
(`run/exec/rm/...`) не входят ни в `_SAFE_PATTERNS`, ни в sudoers.

---

## botctl — управление systemd-ботами

`/usr/local/bin/botctl` (`root:root`, `0755`) — обёртка над `systemctl` и
`journalctl` для 11 ботов (включая `agent-coder-bot`). Список сервисов —
переменная `SERVICES` внутри файла, правится там же (sudoers не трогаем).

```
sudo botctl list                          # все боты: статус + enabled
sudo botctl status agent-coder-bot        # один бот
sudo botctl restart agent-coder-bot       # рестарт
sudo botctl logs agent-coder-bot          # journalctl -n 100
sudo botctl logs-follow agent-coder-bot   # journalctl -f
```

**Имя юнита — `agent-coder-bot.service`**, не `agent-coder`. Исторически
`SERVICES` содержал `agent-coder` — исправлено 2026-10-19 (сессия B7):
`agent-coder-bot` вместо `agent-coder`. Если команда `botctl ... agent-coder`
падает с `Unit ... could not be found` — значит правка откатилась.

## ~/.bashrc — universal multi-project

301 строка, без секретов (`export *_KEY/_TOKEN/_SECRET` отсутствуют).
Проект-релевантное для agent-coder:

- **Автоактивация venv при `cd` в `~/bots/<project>`**: ищет
  `venv/bin/activate` **или** `.venv/bin/activate` (правка 2026-10-19,
  B7: раньше `.venv` не находился, `agent-coder` оставался без venv).
- `p <name>` — переход в `~/bots/<name>` с автовенв и `export PROJECT`.
- `ptail`, `plog`, `pdb` — для проектов с `logs/trading.log` и
  `db/trading.db` (carry-farm и т.п.), **не** для agent-coder.
- `prestart` — рестарт сервиса; маппинг `futures_3.0 → carry-farm`.
- Цветные хелперы `ok/fail/warn/info/note`, `hdr/subhdr/sep` — для
  оформления вывода в интерактиве.

## Установка на новом хосте

1. Скопировать `botctl` в `/usr/local/bin/`, `chmod 755`,
   `chown root:root`, добавить NOPASSWD в `/etc/sudoers.d/` (по вкусу).
2. `.bashrc` — минимум: `PROJECTS_DIR`, `__auto_project` с `venv`/`.venv`,
   `PATH="$HOME/.local/bin:$HOME/bin:$PATH"`.
3. `agent-coder-bot.service` — симлинк из `deploy/` в
   `/etc/systemd/system/`, `systemctl daemon-reload`, `systemctl enable`.
4. Проверка: `sudo botctl list | grep agent-coder-bot` → `active`,
   `bash -ic 'cd ~/bots/agent-coder && echo $VIRTUAL_ENV'` → `.venv`.

## См. также

- `deploy/agent-coder-bot.service` — юнит (версионируется).
- `AGENTS.md` — принципы проекта и известные расхождения.
- `docs/FINDINGS.md` — backlog B1–B5 и история багов.
