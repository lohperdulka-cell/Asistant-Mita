"""Пошаговый ввод без команд: кнопка -> вопрос -> текст -> сохранение.

Каждый сценарий — короткая FSM-цепочка. Команды (/note, /remind, ...)
остаются рабочей альтернативой и живут в своих роутерах.

Роутер подключается последним: он ловит обычный текст, поэтому не должен
перехватывать сообщения, адресованные другим обработчикам.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import settings
from database import Database
from timeparse import parse_reminder

log = logging.getLogger(__name__)
router = Router(name="flows")

MAX_NOTE = 3500
MAX_REMIND = 500


class Flow(StatesGroup):
    note = State()
    reminder = State()
    habit = State()
    material = State()


def cancel_kb() -> object:
    return InlineKeyboardBuilder().row(
        InlineKeyboardButton(text="Отмена", callback_data="flow:cancel")
    ).as_markup()


def tz_of(row) -> ZoneInfo:
    name = (row["timezone"] if row else None) or settings.default_tz
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo("UTC")


@router.callback_query(F.data == "flow:cancel")
async def cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer("Отменено")
    await state.clear()
    if callback.message:
        await callback.message.edit_text("Отменила. Ничего не сохраняла.")


@router.callback_query(F.data == "flow:note")
async def start_note(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.set_state(Flow.note)
    if callback.message:
        await callback.message.answer(
            "✍️ Напиши текст заметки одним сообщением.", reply_markup=cancel_kb()
        )


@router.callback_query(F.data == "flow:reminder")
async def start_reminder(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.set_state(Flow.reminder)
    if callback.message:
        await callback.message.answer(
            "⏰ Напиши когда и что напомнить.\n"
            "Например: <code>через 20 минут выпить воды</code> "
            "или <code>завтра в 9:00 позвонить маме</code>.",
            reply_markup=cancel_kb(),
        )


@router.callback_query(F.data == "flow:habit")
async def start_habit(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.set_state(Flow.habit)
    if callback.message:
        await callback.message.answer(
            "🔥 Напиши название привычки, например <code>зарядка</code>.",
            reply_markup=cancel_kb(),
        )


@router.callback_query(F.data == "flow:material")
async def start_material(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.set_state(Flow.material)
    if callback.message:
        await callback.message.answer(
            "📚 Пришли текст материала одним сообщением, файлом (PDF/DOCX/TXT/MD) "
            "или ссылкой.\n\n"
            "Это RAG-контекст: я буду искать по нему, но модель на нём не обучается.",
            reply_markup=cancel_kb(),
        )


@router.message(StateFilter(Flow.note), F.text)
async def save_note(message: Message, state: FSMContext, db: Database) -> None:
    text = (message.text or "").strip()
    if not text:
        await message.answer("Пустая заметка. Напиши текст.")
        return
    note_id = await db.add_note(message.from_user.id, text[:MAX_NOTE])
    await state.clear()
    await message.answer(f"📝 Заметка #{note_id} сохранена.")


@router.message(StateFilter(Flow.reminder), F.text)
async def save_reminder(message: Message, state: FSMContext, db: Database) -> None:
    text = (message.text or "").strip()
    row = await db.get_user(message.from_user.id)
    tz = tz_of(row)
    parsed = await parse_reminder(text, str(tz))
    if parsed is None:
        await message.answer(
            "Не поняла время 😔\n\n"
            "Скажи иначе: <code>через 15 минут …</code>, "
            "<code>сегодня в 18:30 …</code>, <code>завтра в 9:00 …</code>."
        )
        return
    body = parsed.rest or "напоминание"
    reminder_id = await db.add_reminder(
        message.from_user.id, body[:MAX_REMIND], parsed.when_utc.isoformat()
    )
    await state.clear()
    local = parsed.when_utc.astimezone(tz).strftime("%d.%m в %H:%M")
    await message.answer(f"⏰ Напомню {local}: {body}\n<code>#{reminder_id}</code>")


@router.message(StateFilter(Flow.habit), F.text)
async def save_habit(message: Message, state: FSMContext, db: Database) -> None:
    title = (message.text or "").strip()
    if not title:
        await message.answer("Напиши название привычки.")
        return
    habit_id = await db.add_habit(message.from_user.id, title)
    await state.clear()
    if habit_id is None:
        await message.answer("Такая привычка уже есть.")
        return
    await message.answer(f"🔥 Привычка «{title}» добавлена. Отмечать буду в сводке.")


@router.message(StateFilter(Flow.material), F.text)
async def save_material(message: Message, state: FSMContext, db: Database) -> None:
    from materials import store_material

    text = (message.text or "").strip()
    if not text:
        await message.answer("Пусто. Пришли текст, файл или ссылку.")
        return
    await state.clear()
    kind = "link" if text.startswith(("http://", "https://")) else "text"
    title = text[:60] + ("…" if len(text) > 60 else "")
    material_id, error = await store_material(
        db, message.from_user.id, title, kind, "bot", text
    )
    if material_id is None:
        await message.answer(f"Не сохранила: {error}")
        return
    await message.answer(
        f"📚 Материал #{material_id} сохранён. Теперь можно спрашивать по нему."
    )


__all__ = ["router", "Flow"]
