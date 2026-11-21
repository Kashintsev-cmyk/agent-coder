"""Инструмент: поиск в интернете через встроенный web search RouterAI.

Как работает: RouterAI принимает ``plugins=[{"id": "web", "engine": ...}]``
в ``chat/completions`` — провайдер сам делает поиск, подмешивает результаты в
контекст и отвечает текстом. Источники приходят в ``message.annotations``
(``type="url_citation"``) — из них собираем список ссылок.

Это НЕ наш инструмент ``search`` (ripgrep по проекту): тому нужен локальный
файл, этому — сеть. Отдельный инструмент, потому что у них разная цена,
разные ошибки и разный смысл в промте.

Чего инструмент не делает: не ходит по ссылкам и не парсит HTML. За нас это
делает провайдер; мы только формулируем запрос и приводим ответ к виду
«текст + источники».

Стоимость: один вызов LLM с поиском. Модель — ``WEB_SEARCH_TOOL_MODEL``
(по умолчанию дешёвая haiku), лимит источников —
``WEB_SEARCH_MAX_RESULTS``.
"""

from __future__ import annotations

import logging
from typing import Any

from agent_coder.config import config
from agent_coder.llm.client import LLMClient
from agent_coder.tools.registry import Tool, ToolContext

logger = logging.getLogger(__name__)

# Сколько символов ответа отдавать модели. Провайдер пишет развёрнуто; в
# контекст агента длинная простыня не нужна — она уже оплачена, но занимает
# окно и мешает следующему шагу.
MAX_ANSWER_CHARS = 4000
MAX_SOURCES = 10


def _extract_annotations(response: Any) -> list[dict[str, Any]]:
    """Достать ``url_citation`` из ответа OpenAI-совместимого клиента.

    ``response.raw`` — объект SDK (pydantic), у которого ``annotations`` может
    быть и полем модели, и ``model_extra``. Пробуем оба пути и не падаем,
    если провайдер поле не прислал: источники — приятный бонус, ответ важен.
    """
    raw = getattr(response, "raw", None)
    choices = getattr(raw, "choices", None) or []
    if not choices:
        return []
    message = getattr(choices[0], "message", None)
    if message is None:
        return []

    annotations = getattr(message, "annotations", None)
    if not annotations:
        dump = getattr(message, "model_dump", None)
        if callable(dump):
            try:
                annotations = dump().get("annotations")
            except Exception:  # pragma: no cover — защита от чужой схемы
                annotations = None
    if not annotations:
        return []

    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in annotations:
        if not isinstance(item, dict):
            dump = getattr(item, "model_dump", None)
            item = dump() if callable(dump) else None
        if not isinstance(item, dict):
            continue
        if item.get("type") != "url_citation":
            continue
        citation = item.get("url_citation") or {}
        url = citation.get("url")
        if not url or url in seen:
            continue
        seen.add(url)
        out.append({"url": url, "title": citation.get("title") or url})
        if len(out) >= MAX_SOURCES:
            break
    return out


def _format(query: str, answer: str, sources: list[dict[str, str]]) -> str:
    """Собрать ответ инструмента: текст + нумерованные источники."""
    text = (answer or "").strip()
    if not text:
        return (
            f"Поиск по запросу {query!r} не дал текстового ответа. "
            "Попробуй переформулировать запрос."
        )
    if len(text) > MAX_ANSWER_CHARS:
        text = text[:MAX_ANSWER_CHARS].rstrip() + "\n… (ответ обрезан)"
    if not sources:
        return text
    lines = ["", "Источники:"]
    for i, src in enumerate(sources, 1):
        lines.append(f"{i}. {src['title']} — {src['url']}")
    return text + "\n".join(lines)


async def _execute(args: dict[str, Any], ctx: ToolContext) -> str:
    query = (args.get("query") or "").strip()
    if not query:
        return "Ошибка: пустой query"

    model = config.WEB_SEARCH_TOOL_MODEL
    if not config.ROUTERAI_API_KEY:
        return "Ошибка: ROUTERAI_API_KEY не задан — поиск недоступен"

    client = LLMClient()
    await client.start()
    try:
        response = await client.chat(
            [
                {
                    "role": "user",
                    "content": (
                        f"{query}\n\n"
                        "Найди актуальную информацию в интернете и ответь по существу. "
                        "Приведи ссылки на источники."
                    ),
                }
            ],
            model=model,
            web_search=True,
        )
    except Exception as exc:
        logger.warning("web_search: запрос не удался (%s): %s", model, exc)
        return f"Ошибка поиска в интернете: {exc}"
    finally:
        try:
            await client.close()
        except Exception:  # pragma: no cover — закрытие не должно ронять ответ
            logger.debug("web_search: close() failed", exc_info=True)

    answer = response.message.content or ""
    sources = _extract_annotations(response)
    return _format(query, answer, sources)


web_search_tool = Tool(
    name="web_search",
    description=(
        "Поиск в интернете (встроенный web search RouterAI). Возвращает ответ "
        "и список источников со ссылками. Нужен, когда знаний модели "
        "недостаточно: свежие версии, релизы, документация, новости. "
        "Не путать с search — тот ищет по локальному проекту."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Поисковый запрос или вопрос к интернету",
            }
        },
        "required": ["query"],
    },
    executor=_execute,
    read_only=True,
)
