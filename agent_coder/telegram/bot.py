"""Запуск Telegram-бота."""
from __future__ import annotations

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.types import BotCommand, ErrorEvent

from agent_coder.agent.media_cleanup import cleanup_old_media
from agent_coder.config import config
from agent_coder.model_catalog import catalog
from agent_coder.telegram import handlers
from agent_coder.telegram.renderer import html_escape

logger = logging.getLogger(__name__)


# Меню команд (синий значок "/" в Telegram).
BOT_COMMANDS = [
    BotCommand(command="start",     description="🏠 Главное меню"),
    BotCommand(command="server",    description="🖥 Диагностика сервера"),
    BotCommand(command="audit",     description="🔒 Аудит проекта"),
    BotCommand(command="balance",   description="💳 Баланс RouterAI"),
    BotCommand(command="new",       description="➕ Новая сессия"),
    BotCommand(command="chat",      description="💬 Обсуждение"),
    BotCommand(command="sessions",  description="📚 Все сессии"),
    BotCommand(command="current",   description="📍 Текущая сессия"),
    BotCommand(command="history",   description="📜 История сессии"),
    BotCommand(command="model",     description="🧠 Выбрать модель"),
    BotCommand(command="reasoning", description="💭 Уровень reasoning"),
    BotCommand(command="mode",      description="🔄 Plan / Act / Audit"),
    BotCommand(command="cost",      description="💰 Стоимость сессии"),
    BotCommand(command="projects",  description="📂 Проекты"),
    BotCommand(command="cancel",    description="⏹ Отменить задачу"),
    BotCommand(command="help",      description="❓ Справка"),
]


async def _register_commands(bot: Bot) -> None:
    try:
        await bot.set_my_commands(BOT_COMMANDS)
        logger.info("Меню команд зарегистрировано: %d команд", len(BOT_COMMANDS))
    except Exception as e:
        logger.error("Не удалось зарегистрировать меню команд: %s", e)


async def _catalog_refresh_once() -> None:
    try:
        ok = await catalog.refresh()
        logger.info(
            "model catalog refresh: ok=%s source=%s",
            ok, catalog.source,
        )
    except Exception:
        logger.exception("model catalog refresh unexpected error")


async def _media_cleanup_once() -> None:
    """Best-effort очистка медиа старше MEDIA_RETENTION_DAYS."""
    try:
        import asyncio
        files, dirs = await asyncio.to_thread(cleanup_old_media)
        if files or dirs:
            logger.info("media retention: удалено %d файлов, %d папок", files, dirs)
    except Exception:
        logger.exception("media cleanup unexpected error")


async def _media_cleanup_loop() -> None:
    """Раз в сутки (плюс один раз при старте)."""
    import asyncio
    await _media_cleanup_once()
    while True:
        try:
            await asyncio.sleep(24 * 3600)
        except asyncio.CancelledError:
            return
        await _media_cleanup_once()


async def _catalog_refresh_loop() -> None:
    # Периодический refresh каждые MODELS_CACHE_TTL часов.
    interval = max(1, config.MODELS_CACHE_TTL) * 3600
    while True:
        try:
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            return
        await _catalog_refresh_once()


async def _run() -> None:
    if not config.TELEGRAM_TOKEN:
        print("❌ TELEGRAM_TOKEN не установлен в .env")
        sys.exit(2)
    if not config.TELEGRAM_USER_ID:
        print("❌ TELEGRAM_USER_ID не установлен в .env")
        sys.exit(2)

    try:
        config.validate()
    except ValueError as e:
        print(f"❌ Ошибка конфигурации:\n{e}")
        sys.exit(2)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)

    bot = Bot(
        token=config.TELEGRAM_TOKEN,
        default=DefaultBotProperties(),
    )
    dp = Dispatcher()
    dp.include_router(handlers.router)

    # Глобальный обработчик ошибок — юзер увидит сообщение, а не тишину.
    @dp.errors()
    async def on_error(event: ErrorEvent) -> bool:
        exc = event.exception
        # 429 от Telegram — транзиентная ошибка, юзеру отвечать нечем
        from aiogram.exceptions import TelegramRetryAfter
        if isinstance(exc, TelegramRetryAfter):
            logger.warning("Telegram 429, retry_after=%s", exc.retry_after)
            return True

        logger.exception("Global error: %s", exc)

        try:
            if event.update.message:
                await event.update.message.answer(
                    f"❌ Ошибка: <code>{html_escape(str(exc))[:200]}</code>",
                    parse_mode="HTML",
                )
            elif event.update.callback_query:
                await event.update.callback_query.answer(
                    "❌ Ошибка — попробуй ещё раз", show_alert=True,
                )
        except Exception:
            pass  # не даём упасть внутри обработчика ошибок

        return True  # не пробрасывать дальше

    me = await bot.get_me()
    logger.info("Бот запущен: @%s (id=%d)", me.username, me.id)

    await _register_commands(bot)

    refresh_task = asyncio.create_task(_catalog_refresh_once())
    refresh_loop_task = asyncio.create_task(_catalog_refresh_loop())
    media_task = asyncio.create_task(_media_cleanup_loop())

    # Non-blocking: не ждём refresh, бот отвечает из fallback/cache.

    await bot.delete_webhook(drop_pending_updates=True)
    try:
        await dp.start_polling(bot)
    finally:
        # По SIGTERM/SIGINT aiogram сам останавливает polling и мы попадаем
        # сюда — успеваем уведомить и прервать активные задачи.
        try:
            await handlers.shutdown(bot)
        except Exception as e:
            logger.warning("shutdown failed: %s", e)
        for t in (media_task, refresh_loop_task, refresh_task):
            if not t.done():
                t.cancel()
                try:
                    await t
                except (asyncio.CancelledError, Exception):
                    pass
        await bot.session.close()


def main() -> None:
    try:
        asyncio.run(_run())
    except (KeyboardInterrupt, SystemExit):
        print("\nОстановлено")
