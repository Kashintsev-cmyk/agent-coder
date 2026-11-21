# RouterAI: поля API `/models` и `/models/{id}/endpoints`

Справка по фактической структуре ответов RouterAI. Сверено с живым API
2026-11-21 (`ROUTERAI_BASE_URL=https://routerai.ru/api/v1`). Нужна, чтобы
не гадать «какое поле где лежит» при добавлении capability, цен и лимитов.

**Главная ловушка:** `/models` отдаёт **агрегат** по всем провайдерам, а
`supported_parameters` конкретного запроса зависит от того, на какой endpoint
балансировщик уведёт запрос. Агрегат может обещать параметр, которого нет
ни у одного endpoint (см. `reasoning_effort` ниже).

## 1. `GET /models`

```
{"data": [ {...модель...}, ... ]}     # 614 моделей на 2026-11-21
```

### Поля записи модели

| Поле | Тип | Что это |
|---|---|---|
| `id` | str | `vendor/model` — ключ, по нему фильтруем `CURATED_MODEL_IDS` |
| `name` | str | Человеческое имя |
| `created` | int | Unix-время публикации |
| `description` | str | Описание на русском |
| `context_length` | int | Контекст в токенах |
| `architecture` | obj | `{modality, tokenizer, instruct_type, input_modalities, output_modalities}` |
| `pricing` | obj | `{prompt, completion, web_search, input_cache_read, input_cache_write, input_cache_write_1h, image, image_output, internal_reasoning, request}` — ₽ за 1 токен (см. `pricing_units`) |
| `pricing_units` | obj | `{'prompt': 'token', ...}` — правило пересчёта |
| `supported_parameters` | list[str] | **Агрегат по всем провайдерам** (объединение) |
| `default_parameters` | obj | Рекомендованные значения (`top_k`, `top_p`, ...); обычно `{}` |
| `per_request_limits` | obj/null | Ограничения на запрос; на 2026-11-21 всегда `null` |

`architecture.input_modalities` — источник флага `vision`:
`"image" in input_modalities`.

### `pricing` — не минимум и не максимум по endpoint

Значение `/models` — ставка **одного** провайдера (как правило, дефолтного
для модели), а не лучшая цена. Примеры на 2026-11-21:

| модель | `/models` prompt | минимум по endpoints | комментарий |
|---|---|---|---|
| deepseek-v4-pro-0813 | 19.75 ₽ (Baidu) | 14.94 ₽ (Relace) | дешевле на 32% |
| gpt-6-luna-pro | 10.68 ₽ | 5.34 ₽ | в два раза |
| claude-haiku-5.5 | 10.68 ₽ | 10.68 ₽ | базовая (Anthropic) |

Нельзя показывать цену из `/models` как «минимальную» — это ставка
случайного провайдера.

## 2. `GET /models/{vendor}/{model}/endpoints`

```
{"data": {
  "id": ..., "name": ..., "created": ..., "description": ...,
  "architecture": {...},
  "endpoints": [ {...endpoint...}, ... ]
}}
```

**`data` — объект, а не массив.** Конструкция `jq '.data[].supported_parameters'`
падает с `Cannot index object with number`. Правильно:
`jq '.data.endpoints[].supported_parameters'`.

Обратите внимание: `vendor/model` содержит `/`, и в URL он идёт как есть
(`.../models/deepseek/deepseek-v4-pro/endpoints`).

### Поля endpoint

| Поле | Тип | Что это |
|---|---|---|
| `name` | str | `"Провайдер \| vendor/model"` |
| `provider_name` | str | Провайдер: `Anthropic`, `Baidu`, `OpenAI`, ... |
| `tag` | str | Короткий тег провайдера |
| `country` / `countries` | str / list | Юрисдикции (у OpenAI/Anthropic — только `us`) |
| `context_length` | int | Контекст **этого** endpoint (может отличаться от `/models`) |
| `quantization` | str | `fp4`, `fp8`, `mxfp4`, `unknown`, иногда `null` |
| `max_completion_tokens` | int | Лимит выхода (разброс 131072..1048576) |
| `max_prompt_tokens` | int | Лимит входа (922000 у gpt-6*) |
| `supported_parameters` | list[str] | **Параметры, которые принимает этот endpoint** |
| `status` | int | `0` — доступен |
| `pricing` | obj | Та же схема, что в `/models` |
| `pricing_units` | obj | `{'prompt': 'token', ...}` |
| `variable_pricings` | list | Тиры и «время суток», см. ниже |

### `variable_pricings` — тиры и time-of-day

Два типа, оба важны для оценки стоимости:

**`prompt-threshold`** — long-context тир. Выше порога пересчитывается **вся**
ставка за 1M, а не только превышение:

| модель | порог | вход | выход |
|---|---|---|---|
| claude-haiku-5.5 | 100 000 | ×5.00 | ×5.00 |
| gpt-6-luna-pro | 272 000 | ×2.00 | ×1.50 |
| gpt-6.1-terra-pro | 272 000 | ×2.00 | ×1.50 |
| gpt-6.1-sol-pro | 272 000 | ×2.00 | ×1.50 |
| deepseek-*, claude-sonnet-5.5, claude-opus-5.5 | — | тиров нет | — |

**`time-of-day`** — цена зависит от часа UTC (у deepseek через
BytePlus/DeepSeek/Alibaba): ночная ставка ровно ×0.5 от дневной.

```json
{"type": "prompt-threshold", "threshold": 272000,
 "prompt": 0.00021369686, "completion": 0.0010684843}
```

## 3. Матрица curated-моделей (2026-11-21)

`re_eff` / `verb` / `ws` — сколько endpoint'ов из N реально поддерживают
параметр.

| модель | ep | context | вход ₽/1M | тир | re_eff | verb | ws |
|---|---|---|---|---|---|---|---|
| deepseek-v4.1-flash-1017 | 49 | 1.00–1.05M | 3.53–271.90 | — | 11/49 | 0/49 | 3/49 |
| claude-haiku-5.5 | 5 | 1.00M | 10.68 | >100K ×5 | **0/5** | 5/5 | 5/5 |
| gpt-6-luna-pro | 4 | 1.05M | 5.34–21.37 | >272K ×2 | **0/4** | 4/4 | 4/4 |
| deepseek-v4-pro-0813 | 20 | 1.00–1.05M | 14.94–296.18 | — | 1/20 | 0/20 | 0/20 |
| gpt-6.1-terra-pro | 4 | 1.05M | 53.42–213.70 | >272K ×2 | **0/4** | 4/4 | 4/4 |
| claude-sonnet-5.5 | 5 | 1.00M | 213.70 | — | **0/5** | 5/5 | 5/5 |
| gpt-6.1-sol-pro | 4 | 1.05M | 106.85–427.39 | >272K ×2 | **0/4** | 4/4 | 4/4 |
| claude-opus-5.5 | 6 | 1.00M | 427.39–854.79 | — | **0/6** | 6/6 | 6/6 |

`tools` и `reasoning` — 100% endpoint у всех 8. `status=0` — 100%.

## 4. Известные ловушки

### `reasoning_effort` объявлен, но не поддерживается

`/models` пишет `reasoning_effort` в `supported_parameters` **всех 8** моделей,
но в endpoints: 6 моделей — `0/N`, deepseek-flash — `11/49`,
deepseek-v4-pro — `1/20`. То есть агрегат `agg ⊃ union` — параметра нет
вообще нигде, а он заявлен.

RouterAI при этом **молча игнорирует** параметр (probe: `max_tokens=1` с
`reasoning_effort=medium` → HTTP 200). Жёсткого 400 нет, но и эффекта нет:
UI показывал бы уровень reasoning, который ни на что не влияет.
Вывод: гейтить отправку по capability модели, а не по агрегату `/models`.

### `web_search` есть не у всех провайдеров

Флаг «встроенный поиск» (`pricing.web_search`) в `/models` может
отсутствовать, хотя у части endpoints он есть: у deepseek-v4.1-flash
3/49 endpoints тарифицируют `web_search`, а в агрегате поля нет.

### Код не читает (но API отдаёт)

`max_completion_tokens`, `max_prompt_tokens`, `pricing.image`,
`pricing.image_output`, `pricing.internal_reasoning`, `pricing.request`,
`default_parameters`, `per_request_limits`, `countries`,
`variable_pricings` (тиры и время суток).

## 5. Web search: как передавать и сколько стоит

**Передавать только через `extra_body`.** `plugins` — расширение RouterAI,
которого нет в сигнатуре `AsyncCompletions.create`. В top-level kwargs SDK
падает:

```
TypeError: AsyncCompletions.create() got an unexpected keyword argument 'plugins'
```

Правильно:

```python
client.chat.completions.create(
    model="anthropic/claude-haiku-5.5",
    messages=[...],
    extra_body={"plugins": [{"id": "web", "engine": "exa", "max_results": 3}]},
)
```

Источники приходят в `choices[0].message.annotations` — список объектов
`Annotation` (pydantic), не dict. У каждого `type == "url_citation"` и поля
`url_citation.url` / `.title`. Через `.model_dump()` превращается в обычный
dict.

### Цена: `native` в 16 раз дороже

Замер на haiku-5.5, запрос «последняя версия Python» (2026-11-21), ответ по
существу одинаковый во всех трёх случаях:

| способ | input-токенов | ≈₽ | источников |
|---|---|---|---|
| `plugins` engine=`native` | 20 329 | 0.258 | 3 |
| `plugins` engine=`exa` | 818 | 0.037 | 3 |
| `web_search_options={}` | 1 284 | 0.048 | 5 |

`native` тянет полные страницы в контекст — отсюда ×16. Если поиск нужен
часто, `WEB_SEARCH_ENGINE=exa` дешевле на порядок. Пустой `content` при
`max_tokens` меньше ~1000 — это reasoning съел бюджет, а не отсутствие
ответа: ставьте лимит с запасом.

## 6. Как проверить руками

```bash
set -a && . ./.env && set +a
curl -sS -H "Authorization: Bearer $ROUTERAI_API_KEY" \
  "$ROUTERAI_BASE_URL/models/deepseek/deepseek-v4-pro/endpoints" \
  | jq '.data.endpoints[] | {provider_name, context_length, supported_parameters,
                             tier: [.variable_pricings[]? | select(.type=="prompt-threshold")]}'
```
