# Live model benchmark

Набор содержит 12 одинаковых задач (3 create, 3 refactor, 3 audit, 3 fix) и
запускает каждую в отдельном git worktree. Сравниваются семь базовых моделей проекта и новые кандидаты. DeepSeek V4.1 Flash
и Gemini 3.8 Flash присутствуют в обоих списках, поэтому в прогоне каждый ID
встречается один раз — всего 11 моделей:

**Базовые модели проекта**

- `deepseek/deepseek-v4.1-flash`
- `openai/gpt-5.6-luna`
- `z-ai/glm-5.3-flash`
- `google/gemini-3.8-flash`
- `moonshotai/kimi-k2.7-code`
- `anthropic/claude-sonnet-5.5`
- `x-ai/grok-4.7`

**Новые кандидаты**

- `anthropic/claude-opus-5.5`
- `openai/gpt-6-astra`
- `moonshotai/kimi-k3`
- `z-ai/glm-5.3`

## Мини-пилот: Telegram-бот прогноза погоды

Чтобы быстро сравнить стиль работы моделей на одной одинаковой задаче создания,
используйте пустой starter-проект и задачу в `weather_task.json`. Fixture создаёт
одинаковую структуру в отдельном worktree для каждого кандидата. Реализуется бот,
который отвечает на `/weather <город>` и получает прогноз через Open-Meteo.

Сначала проверьте план (6 моделей — 6 запусков, без расходов):

```bash
python -m benchmark.runner --tasks benchmark/weather_task.json \\
  --models anthropic/claude-opus-5.5 openai/gpt-6-astra google/gemini-3.8-flash \\
  moonshotai/kimi-k3 deepseek/deepseek-v4.1-flash z-ai/glm-5.3
```

Запуск с реальными вызовами провайдера:

```bash
python -m benchmark.runner --run --tasks benchmark/weather_task.json \\
  --models anthropic/claude-opus-5.5 openai/gpt-6-astra google/gemini-3.8-flash \\
  moonshotai/kimi-k3 deepseek/deepseek-v4.1-flash z-ai/glm-5.3 --case-timeout 900
```

Это будет именно **сравнение новых шести кандидатов**, а не семи базовых моделей.
Если нужен контрольный baseline — добавьте их ID в `--models`.

## Запуск

Сначала проверьте план без расходов:

```bash
python -m benchmark.runner
```

Один тестовый кейс на всех моделях:

```bash
python -m benchmark.runner --run --task-id audit-01 --case-timeout 900
```

Полный раунд:

```bash
python -m benchmark.runner --run --max-iterations 12
```

Результаты, логи и worktree-сессии сохраняются в абсолютном каталоге
`benchmark/results/<timestamp>/`. Worktree запускается с `--cwd .`, чтобы путь
рабочей директории не дублировался внутри самого worktree.
Для сопоставимости запускайте раунд с одного commit и не меняйте `.env` между
моделями. `--run` намеренно обязателен: он вызывает RouterAI и расходует баланс. По умолчанию
на один кейс отводится 900 секунд; измените `--case-timeout` или задайте `0`,
чтобы отключить лимит. При таймауте кейс получает `returncode: 124`, сохраняется
в `results.json`, а раунд продолжает работу.

## Интерпретация

У каждой задачи есть setup-скрипт в `benchmark/fixtures/`: он разворачивает
небольшой отдельный проект/bug в worktree до запуска модели. Audit запускается
через штатный `--audit` режим и потому не может писать файлы.

`results.json` собирает return code, время, число запросов/инструментов, RouterAI
usage/cost и список изменённых или новых файлов. Для audit успех означает
отсутствие изменений; для остальных категорий — наличие изменений. Это только
технический сигнал: качество решения, прохождение тестов, лишние изменения и
точность аудита нужно проверить по логам/диффам и добавить в итоговую таблицу.

Перед сравнением полезно повторить каждый кейс 2–3 раза и считать средние,
медиану стоимости и success rate отдельно по четырём категориям. Не делайте
вывод о превосходстве модели по одному прогону.

## A/B-пилот workflow-подсказок

`workflow_tasks.json` содержит по одному сопоставимому кейсу для PLAN, ACT и
AUDIT. Оба профиля используют те же задачу, fixture, модель и код агента:
`baseline` запускает исходный текст задачи, а `optimized` добавляет короткую
workflow-подсказку (раньше остановить исследование в PLAN, группировать
связанные действия в ACT, ограничивать аудит применимыми областями).
Это **пилот пользовательских prompt-протоколов**, а не сравнение system prompt
или конфигурации ядра. Для проверки изменений самих системных промптов нужен
отдельный кодовый профиль/commit.

Сначала dry-run, он не вызывает модель и не расходует баланс:

```bash
.venv/bin/python -m benchmark.runner \\
  --tasks benchmark/workflow_tasks.json \\
  --models deepseek/deepseek-v4.1-flash \\
  --variants baseline optimized --repeats 2
```

Если план выглядит корректно, добавьте `--run` для живого прогона. Для первого
пилота рекомендуется один недорогой model ID и `--max-cost` как общий лимит:

```bash
.venv/bin/python -m benchmark.runner --run \\
  --tasks benchmark/workflow_tasks.json \\
  --models deepseek/deepseek-v4.1-flash \\
  --variants baseline optimized --repeats 2 --max-cost 20
```

Каждая пара получает отдельный worktree и каталог сессии; A/B-кейсы идут рядом,
а результат маркируется `mode`, `variant`, `repeat`, числом LLM-запросов и tool
calls. `success` — только технический сигнал (например, тесты для ACT и отсутствие
изменений в PLAN/AUDIT), **не оценка качества**. Для PLAN/AUDIT изучите лог и
сверьте итог с `quality_review` в `results.json`; сравнивайте стоимость, запросы,
время и качество минимум по двум-трём повторам. Поля `iterations` и
`llm_requests` обозначают запросы к модели, а `tool_calls` — ответы инструментов.
