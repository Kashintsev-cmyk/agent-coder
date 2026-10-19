# Окружение botuser (host-level)

Знание о host-tooling, которое **не хранится в репо** (root-owned или
multi-project). При переносе на другой хост — воспроизвести вручную.

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
