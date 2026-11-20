"""Точка входа «Миты»: сборка диспетчера, меню команд и запуск.

Запуск:  python bot.py
Все секреты берутся из переменных окружения (см. config.py).
"""

from __future__ import annotations

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramNetworkError, TelegramUnauthorizedError
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    BotCommand,
    BotCommandScopeDefault,
    MenuButtonCommands,
    MenuButtonWebApp,
    WebAppInfo,
)

from config import settings
from database import Database

log = logging.getLogger("mita")

COMMANDS = [
    BotCommand(command="start", description="Начать / онбординг"),
    BotCommand(command="settings", description="Профиль и настройки"),
    BotCommand(command="note", description="Добавить заметку"),
    BotCommand(command="notes", description="Мои заметки"),
    BotCommand(command="done", description="Закрыть заметку"),
    BotCommand(command="remind", description="Напоминание"),
    BotCommand(command="reminders", description="Список напоминаний"),
    BotCommand(command="upload", description="Как загрузить материалы"),
    BotCommand(command="materials", description="Мои материалы"),
    BotCommand(command="forget", description="Удалить материал"),
    BotCommand(command="addtext", description="Сохранить текст как материал"),
    BotCommand(command="habit", description="Добавить привычку"),
    BotCommand(command="habits", description="Мои привычки"),
    BotCommand(command="clear", description="Очистить историю диалога"),
    BotCommand(command="menu", description="Главное меню"),
    BotCommand(command="app", description="Открыть Mini App"),
    BotCommand(command="help", description="Помощь"),
]


def build_dispatcher(db: Database) -> Dispatcher:
    dp = Dispatcher(storage=MemoryStorage())
    dp["db"] = db

    from flows import router as flows_router
    from habits import router as habits_router
    from inline_mode import router as inline_router
    from llm_chat import router as chat_router
    from materials import router as materials_router
    from notes import router as notes_router
    from onboarding import router as onboarding_router
    from settings_menu import router as settings_router
    from webapp_menu import router as menu_router

    dp.include_router(onboarding_router)
    dp.include_router(menu_router)
    dp.include_router(settings_router)
    dp.include_router(notes_router)
    dp.include_router(habits_router)
    # flows идёт раньше materials: в активном FSM-состоянии текст — это ответ
    # сценарию, а не запрос по материалам.
    dp.include_router(flows_router)
    dp.include_router(materials_router)
    dp.include_router(inline_router)
    dp.include_router(chat_router)
    return dp


async def set_commands(bot: Bot) -> None:
    try:
        await bot.set_my_commands(COMMANDS, scope=BotCommandScopeDefault())
    except Exception as exc:  # сеть может отвалиться — не критично
        log.warning("Не удалось установить команды: %s", exc)


async def set_menu_button(bot: Bot) -> None:
    """Кнопка меню ведёт в Mini App. Telegram требует https, иначе откат на команды."""
    url = settings.webapp_base_url
    if not url.startswith("https://"):
        log.warning(
            "WEBAPP_PUBLIC_URL не https (%s) — кнопка меню останется списком команд", url
        )
        try:
            await bot.set_chat_menu_button(menu_button=MenuButtonCommands())
        except Exception as exc:
            log.warning("Не удалось вернуть кнопку команд: %s", exc)
        return
    try:
        await bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(text="Открыть Миту", web_app=WebAppInfo(url=url + "/"))
        )
        log.info("Кнопка меню ведёт в Mini App: %s", url)
    except Exception as exc:
        log.warning("Не удалось установить кнопку Mini App: %s", exc)


async def main() -> None:
    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if not settings.is_configured:
        print(
            "BOT_TOKEN не задан. Скопируйте .env.example в .env и впишите токен.\n"
            "  cp .env.example .env && nano .env",
            file=sys.stderr,
        )
        raise SystemExit(1)

    settings.ensure_dirs()
    db = Database(settings.resolved_db_path)
    await db.init()

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = build_dispatcher(db)
    await set_commands(bot)
    await set_menu_button(bot)

    try:
        me = await bot.get_me()
    except TelegramUnauthorizedError:
        print(
            "Telegram отклонил BOT_TOKEN. Проверьте токен от @BotFather в .env",
            file=sys.stderr,
        )
        await bot.session.close()
        raise SystemExit(1)
    except TelegramNetworkError as exc:
        print(
            f"Нет связи с Telegram API: {exc}\n"
            "Проверьте интернет на телефоне и повторите запуск.",
            file=sys.stderr,
        )
        await bot.session.close()
        raise SystemExit(1)
    log.info("Запуск @%s (id=%s)", me.username, me.id)

    from scheduler import ReminderScheduler

    reminder_scheduler = ReminderScheduler(bot, db)
    reminder_scheduler.start()

    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        reminder_scheduler.shutdown()
        await db.close()
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
