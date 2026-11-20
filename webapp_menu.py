"""Главное меню бота и кнопка запуска Mini App.

Telegram требует https для Web App. Если публичный адрес не задан,
кнопка не показывается, а бот объясняет, как её включить.
"""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    Message,
    WebAppInfo,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import settings
from database import Database, features_of
from flows import Flow, cancel_kb

log = logging.getLogger(__name__)
router = Router(name="menu")


def webapp_available() -> bool:
    return settings.webapp_base_url.startswith("https://")


def main_menu(db_row) -> object:
    kb = InlineKeyboardBuilder()
    kb.button(text="✍️ Заметки", callback_data="menu:notes")
    kb.button(text="⏰ Напоминания", callback_data="menu:reminders")
    kb.button(text="📚 Материалы", callback_data="menu:materials")
    kb.button(text="🔥 Привычки", callback_data="menu:habits")
    kb.button(text="⚙️ Настройки", callback_data="menu:settings")
    kb.adjust(2, 2, 1)
    if webapp_available():
        kb.row(
            InlineKeyboardButton(
                text="Открыть Миту",
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
        "Командой можно не пользоваться: нажми кнопку в меню и просто напиши "
        "текст — я сама пойму, что сохранить.\n\n"
        "📝 /note текст · /notes · /done id\n"
        "⏰ /remind через 10 минут … · /reminders · /delremind id\n"
        "📚 /upload · /materials · /forget id · /addtext текст\n"
        "🔥 /habit add название · /habits\n"
        "💬 /clear — очистить историю диалога\n"
        "⚙ /settings · /menu\n\n"
        "Просто напиши вопрос — отвечу. Спроси «найди в моих материалах …», "
        "и я поищу по загруженным файлам (RAG).",
        reply_markup=main_menu(row) if row else None,
    )


@router.callback_query(F.data.startswith("menu:"))
async def menu_callback(callback: CallbackQuery, state: FSMContext, db: Database) -> None:
    section = callback.data.split(":", 1)[1]
    user_id = callback.from_user.id
    await callback.answer()
    if callback.message is None:
        return

    if section == "notes":
        await callback.message.answer(
            "✍️ <b>Заметки</b>\n\n"
            "Напиши текст — сохраню сразу.\n"
            "Список и закрытие — кнопками ниже.",
            reply_markup=_notes_kb(),
        )
        await state.set_state(Flow.note)
        return

    if section == "reminders":
        await callback.message.answer(
            "⏰ <b>Напоминания</b>\n\n"
            "Напиши когда и что напомнить: <code>через 20 минут выпить воды</code>.",
            reply_markup=cancel_kb(),
        )
        await state.set_state(Flow.reminder)
        return

    if section == "materials":
        await callback.message.answer(
            "📚 <b>Материалы</b>\n\n"
            "Пришли текст, файл (PDF/DOCX/TXT/MD) или ссылку.\n"
            "Это RAG-контекст: ищу по нему, модель на нём не обучается.",
            reply_markup=_materials_kb(),
        )
        await state.set_state(Flow.material)
        return

    if section == "habits":
        await callback.message.answer(
            "🔥 <b>Привычки</b>\n\nНапиши название привычки — добавлю.",
            reply_markup=_habits_kb(),
        )
        await state.set_state(Flow.habit)
        return

    if section == "settings":
        from settings_menu import settings_view

        text, markup = await settings_view(db, user_id)
        await callback.message.answer(text, reply_markup=markup)
        return


def _notes_kb() -> object:
    kb = InlineKeyboardBuilder()
    kb.button(text="Список заметок", callback_data="notes:list")
    kb.button(text="Отмена", callback_data="flow:cancel")
    kb.adjust(2)
    return kb.as_markup()


def _materials_kb() -> object:
    kb = InlineKeyboardBuilder()
    kb.button(text="Мои материалы", callback_data="mat:list")
    kb.button(text="Отмена", callback_data="flow:cancel")
    kb.adjust(2)
    return kb.as_markup()


def _habits_kb() -> object:
    kb = InlineKeyboardBuilder()
    kb.button(text="Мои привычки", callback_data="habits:list")
    kb.button(text="Отмена", callback_data="flow:cancel")
    kb.adjust(2)
    return kb.as_markup()


@router.callback_query(F.data == "notes:list")
async def notes_list_cb(callback: CallbackQuery, db: Database) -> None:
    from notes import send_notes_list

    await callback.answer()
    if callback.message:
        await send_notes_list(callback.message, db, callback.from_user.id)


@router.callback_query(F.data == "mat:list")
async def materials_list_cb(callback: CallbackQuery, db: Database) -> None:
    from materials import send_materials_list

    await callback.answer()
    if callback.message:
        await send_materials_list(callback.message, db, callback.from_user.id)


@router.callback_query(F.data == "habits:list")
async def habits_list_cb(callback: CallbackQuery, db: Database) -> None:
    from habits import send_habits_list

    await callback.answer()
    if callback.message:
        await send_habits_list(callback.message, db, callback.from_user.id)


__all__ = ["router", "main_menu", "menu_text", "webapp_available"]
