"""Главное меню бота и кнопка запуска Mini App.

Telegram требует https для Web App. Если публичный адрес не задан,
кнопка не показывается, а бот объясняет, как её включить.
"""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    Message,
    WebAppInfo,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import settings
from database import Database, features_of

log = logging.getLogger(__name__)
router = Router(name="menu")


def webapp_available() -> bool:
    return settings.webapp_base_url.startswith("https://")


def main_menu(db_row) -> object:
    kb = InlineKeyboardBuilder()
    kb.button(text="Заметки", callback_data="menu:notes")
    kb.button(text="Напоминания", callback_data="menu:reminders")
    kb.button(text="Материалы", callback_data="menu:materials")
    kb.button(text="Привычки", callback_data="menu:habits")
    kb.button(text="Настройки", callback_data="menu:settings")
    kb.adjust(2, 2, 1)
    if webapp_available():
        kb.row(
            InlineKeyboardButton(
                text="Открыть Mini App",
                web_app=WebAppInfo(url=settings.webapp_base_url + "/"),
            )
        )
    return kb.as_markup()


def menu_text(db_row) -> str:
    from onboarding import GOAL_TITLES, STYLE_TITLES

    features = ", ".join(features_of(db_row)) or "—"
    return (
        "<b>Главное меню</b>\n\n"
        f"Цель: {GOAL_TITLES.get(db_row['goal'] or '', '—')}\n"
        f"Стиль: {STYLE_TITLES.get(db_row['style'] or '', '—')}\n"
        f"Функции: {features}\n"
        f"Сводка: {db_row['digest_time']} ({db_row['timezone']})"
    )


@router.message(Command("menu"))
async def menu_command(message: Message, db: Database) -> None:
    row = await db.get_user(message.from_user.id)
    if row is None:
        await message.answer("Напиши /start, чтобы я тебя настроила.")
        return
    await message.answer(menu_text(row), reply_markup=main_menu(row))


@router.message(Command("app"))
async def app_command(message: Message) -> None:
    if not webapp_available():
        await message.answer(
            "Mini App требует https-адрес.\n\n"
            "Запусти туннель (например <code>cloudflared tunnel --url "
            "http://127.0.0.1:8080</code>), затем впиши адрес в <code>.env</code>:\n"
            "<code>WEBAPP_PUBLIC_URL=https://…</code> и перезапусти бота."
        )
        return
    await message.answer(
        "Открывай Mini App 👇",
        reply_markup=InlineKeyboardBuilder()
        .row(
            InlineKeyboardButton(
                text="Открыть Mini App",
                web_app=WebAppInfo(url=settings.webapp_base_url + "/"),
            )
        )
        .as_markup(),
    )


@router.message(Command("help"))
async def help_command(message: Message, db: Database) -> None:
    row = await db.get_user(message.from_user.id)
    await message.answer(
        "<b>Что я умею</b>\n\n"
        "📝 /note текст · /notes · /done id\n"
        "⏰ /remind через 10 минут … · /reminders · /delremind id\n"
        "📚 /upload · /materials · /forget id · /addtext текст\n"
        "🔥 /habit add название · /habits\n"
        "⚙ /settings · /menu\n\n"
        "Просто напиши вопрос — отвечу. Спроси «найди в моих материалах …», "
        "и я поищу по загруженным файлам (RAG).",
        reply_markup=main_menu(row) if row else None,
    )


@router.callback_query(F.data.startswith("menu:"))
async def menu_callback(callback: CallbackQuery, db: Database) -> None:
    section = callback.data.split(":", 1)[1]
    await callback.answer()
    hints = {
        "notes": "Заметки: /note текст · /notes · /done id",
        "reminders": "Напоминания: /remind через 10 минут … · /reminders",
        "materials": "Материалы: /upload · /materials · /forget id",
        "habits": "Привычки: /habit add зарядка · /habits",
        "settings": "Настройки: /settings",
    }
    if callback.message:
        await callback.message.answer(hints.get(section, "Неизвестный раздел"))


__all__ = ["router", "main_menu", "menu_text", "webapp_available"]
