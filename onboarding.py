"""Онбординг «Миты»: FSM-диалог при первом запуске и /settings.

Шаги: цель -> функции (мультивыбор) -> стиль общения -> часовой пояс ->
время ежедневной сводки. Всё сохраняется в таблицу users.
"""

from __future__ import annotations

import logging
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import settings
from database import Database, features_of

log = logging.getLogger(__name__)
router = Router(name="onboarding")


class Onboarding(StatesGroup):
    goal = State()
    features = State()
    style = State()
    timezone = State()
    digest = State()


GOALS = [
    ("study", "Учёба"),
    ("work", "Работа"),
    ("personal", "Личные дела"),
    ("health", "Здоровье/привычки"),
    ("creative", "Творчество"),
    ("other", "Другое"),
]

FEATURES = [
    ("notes", "Заметки"),
    ("reminders", "Напоминания"),
    ("materials", "Материалы (RAG)"),
    ("ideas", "Генерация идей"),
    ("short", "Краткие ответы"),
    ("habits", "Трекер привычек"),
]

STYLES = [
    ("brief", "Кратко"),
    ("detailed", "Подробно"),
    ("friendly", "Дружелюбно"),
    ("formal", "Формально"),
]

TIMEZONES = [
    ("Europe/Moscow", "Москва (UTC+3)"),
    ("Europe/Kaliningrad", "Калининград (UTC+2)"),
    ("Europe/Kiev", "Киев (UTC+2)"),
    ("Europe/Minsk", "Минск (UTC+3)"),
    ("Asia/Yekaterinburg", "Екатеринбург (UTC+5)"),
    ("Asia/Novosibirsk", "Новосибирск (UTC+7)"),
    ("Asia/Krasnoyarsk", "Красноярск (UTC+7)"),
    ("Asia/Vladivostok", "Владивосток (UTC+10)"),
    ("Asia/Almaty", "Алматы (UTC+5)"),
    ("Asia/Tbilisi", "Тбилиси (UTC+4)"),
    ("UTC", "UTC"),
]

DIGEST_TIMES = ["07:00", "08:00", "09:00", "10:00", "12:00", "18:00", "20:00", "21:00"]

GOAL_TITLES = dict(GOALS)
FEATURE_TITLES = dict(FEATURES)
STYLE_TITLES = dict(STYLES)

TIME_RE = re.compile(r"^([01]?\d|2[0-3])[:.\s]?([0-5]?\d)$")


def is_valid_timezone(name: str) -> bool:
    try:
        ZoneInfo(name)
        return True
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return False


def parse_digest_time(raw: str) -> str | None:
    """'9:5', '09.30', '0930' -> '09:30'. Невалидное -> None."""
    match = TIME_RE.match((raw or "").strip())
    if not match:
        return None
    hour, minute = int(match.group(1)), int(match.group(2))
    if hour > 23 or minute > 59:
        return None
    return f"{hour:02d}:{minute:02d}"


# ------------------------------------------------------------------ клавиатуры


def goal_keyboard():
    kb = InlineKeyboardBuilder()
    for code, title in GOALS:
        kb.button(text=title, callback_data=f"ob:goal:{code}")
    kb.adjust(2)
    return kb.as_markup()


def features_keyboard(selected: list[str]):
    kb = InlineKeyboardBuilder()
    for code, title in FEATURES:
        mark = "✅ " if code in selected else "▫️ "
        kb.button(text=f"{mark}{title}", callback_data=f"ob:feat:{code}")
    kb.adjust(2)
    kb.row(InlineKeyboardButton(text="Готово ➡️", callback_data="ob:feat:done"))
    return kb.as_markup()


def style_keyboard():
    kb = InlineKeyboardBuilder()
    for code, title in STYLES:
        kb.button(text=title, callback_data=f"ob:style:{code}")
    kb.adjust(2)
    return kb.as_markup()


def timezone_keyboard():
    kb = InlineKeyboardBuilder()
    for code, title in TIMEZONES:
        kb.button(text=title, callback_data=f"ob:tz:{code}")
    kb.adjust(2)
    kb.row(InlineKeyboardButton(text="Ввести вручную", callback_data="ob:tz:manual"))
    return kb.as_markup()


def digest_keyboard():
    kb = InlineKeyboardBuilder()
    for value in DIGEST_TIMES:
        kb.button(text=value, callback_data=f"ob:digest:{value}")
    kb.adjust(4)
    kb.row(InlineKeyboardButton(text="Своё время", callback_data="ob:digest:manual"))
    return kb.as_markup()


def settings_keyboard():
    kb = InlineKeyboardBuilder()
    kb.button(text="Пройти онбординг заново", callback_data="ob:restart")
    return kb.as_markup()


# --------------------------------------------------------------------- шаги


async def ask_goal(message: Message, state: FSMContext) -> None:
    await state.set_state(Onboarding.goal)
    await state.update_data(goal=None, features=[], style=None, timezone=None, digest_time=None)
    await message.answer(
        "Привет! Я Мита — твой личный ассистент.\n\n"
        "Настроимся под тебя, это займёт меньше минуты.\n\n"
        "<b>Шаг 1/4. Какая у тебя основная цель?</b>",
        reply_markup=goal_keyboard(),
    )


async def ask_features(message: Message, state: FSMContext) -> None:
    await state.set_state(Onboarding.features)
    data = await state.get_data()
    await message.answer(
        "<b>Шаг 2/4. Что тебе нужно?</b>\n"
        "Можно выбрать несколько, затем нажми «Готово».",
        reply_markup=features_keyboard(data.get("features") or []),
    )


async def ask_style(message: Message, state: FSMContext) -> None:
    await state.set_state(Onboarding.style)
    await message.answer("<b>Шаг 3/4. Как мне с тобой общаться?</b>", reply_markup=style_keyboard())


async def ask_timezone(message: Message, state: FSMContext) -> None:
    await state.set_state(Onboarding.timezone)
    await message.answer(
        "<b>Шаг 4/4. Твой часовой пояс?</b>\n"
        "Он нужен, чтобы напоминания и сводка приходили вовремя.",
        reply_markup=timezone_keyboard(),
    )


async def ask_digest(message: Message, state: FSMContext) -> None:
    await state.set_state(Onboarding.digest)
    await message.answer(
        "<b>В какое время присылать ежедневную сводку?</b>",
        reply_markup=digest_keyboard(),
    )


async def finish(message: Message, state: FSMContext, db: Database) -> None:
    data = await state.get_data()
    await db.save_profile(
        message.chat.id if message.chat.type == "private" else message.from_user.id,
        goal=data.get("goal") or "other",
        features=data.get("features") or [],
        style=data.get("style") or "brief",
        timezone=data.get("timezone") or settings.default_tz,
        digest_time=data.get("digest_time") or "09:00",
        onboarded=True,
    )
    await state.clear()
    await message.answer(
        "🎉 <b>Готово!</b> Я настроена под тебя.\n\n"
        "Дальше — команды /note, /remind, /materials, /habits или меню ниже."
    )
    await message.answer(main_menu_text(data), reply_markup=main_menu_keyboard())


def main_menu_text(data: dict) -> str:
    return (
        "<b>Главное меню</b>\n\n"
        f"Цель: {GOAL_TITLES.get(data.get('goal') or '', '—')}\n"
        f"Стиль: {STYLE_TITLES.get(data.get('style') or '', '—')}\n"
        f"Сводка: {data.get('digest_time') or '09:00'} "
        f"({data.get('timezone') or settings.default_tz})"
    )


def main_menu_keyboard():
    kb = InlineKeyboardBuilder()
    kb.button(text="Заметки", callback_data="menu:notes")
    kb.button(text="Напоминания", callback_data="menu:reminders")
    kb.button(text="Материалы", callback_data="menu:materials")
    kb.button(text="Привычки", callback_data="menu:habits")
    kb.button(text="Настройки", callback_data="menu:settings")
    kb.adjust(2, 2, 1)
    return kb.as_markup()


# ------------------------------------------------------------------ хендлеры


@router.message(CommandStart())
async def start(message: Message, state: FSMContext, db: Database) -> None:
    user = message.from_user
    await db.touch_user(user.id, user.username, user.first_name)
    if await db.is_onboarded(user.id):
        await message.answer(
            "С возвращением! Я тебя помню.\n\n"
            "Напиши что-нибудь или выбери действие ниже.",
            reply_markup=main_menu_keyboard(),
        )
        return
    await ask_goal(message, state)


@router.callback_query(Onboarding.goal, F.data.startswith("ob:goal:"))
async def goal_chosen(callback: CallbackQuery, state: FSMContext) -> None:
    goal = callback.data.rsplit(":", 1)[1]
    await state.update_data(goal=goal)
    await callback.answer()
    try:
        await callback.message.edit_text(f"Цель: <b>{GOAL_TITLES.get(goal, goal)}</b>")
    except TelegramBadRequest:
        pass
    await ask_features(callback.message, state)


@router.callback_query(Onboarding.features, F.data.startswith("ob:feat:"))
async def feature_toggled(callback: CallbackQuery, state: FSMContext) -> None:
    code = callback.data.rsplit(":", 1)[1]
    data = await state.get_data()
    selected = list(data.get("features") or [])

    if code == "done":
        if not selected:
            await callback.answer("Выбери хотя бы одну функцию.", show_alert=True)
            return
        await callback.answer("Принято")
        try:
            await callback.message.edit_text(
                "Функции: <b>"
                + ", ".join(FEATURE_TITLES.get(c, c) for c in selected)
                + "</b>"
            )
        except TelegramBadRequest:
            pass
        await ask_style(callback.message, state)
        return

    if code in selected:
        selected.remove(code)
    else:
        selected.append(code)
    await state.update_data(features=selected)
    await callback.answer()
    try:
        await callback.message.edit_reply_markup(reply_markup=features_keyboard(selected))
    except TelegramBadRequest:
        pass


@router.callback_query(Onboarding.style, F.data.startswith("ob:style:"))
async def style_chosen(callback: CallbackQuery, state: FSMContext) -> None:
    style = callback.data.rsplit(":", 1)[1]
    await state.update_data(style=style)
    await callback.answer()
    try:
        await callback.message.edit_text(f"Стиль: <b>{STYLE_TITLES.get(style, style)}</b>")
    except TelegramBadRequest:
        pass
    await ask_timezone(callback.message, state)


@router.callback_query(Onboarding.timezone, F.data.startswith("ob:tz:"))
async def timezone_chosen(callback: CallbackQuery, state: FSMContext) -> None:
    # IANA-пояс сам содержит ":", поэтому режем только по первым двум разделителям
    code = callback.data.split(":", 2)[2]
    if code == "manual":
        await callback.answer()
        try:
            await callback.message.edit_text(
                "Напиши часовой пояс в формате IANA, например <code>Europe/Moscow</code>."
            )
        except TelegramBadRequest:
            pass
        return
    if not is_valid_timezone(code):
        await callback.answer("Не знаю такой пояс, попробуй ещё.", show_alert=True)
        return
    await state.update_data(timezone=code)
    await callback.answer()
    try:
        await callback.message.edit_text(f"Часовой пояс: <b>{code}</b>")
    except TelegramBadRequest:
        pass
    await ask_digest(callback.message, state)


@router.message(Onboarding.timezone)
async def timezone_manual(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if not is_valid_timezone(raw):
        await message.answer(
            "Не нашла такой пояс. Пример: <code>Europe/Moscow</code>"
        )
        return
    await state.update_data(timezone=raw)
    await message.answer(f"Часовой пояс: <b>{raw}</b>")
    await ask_digest(message, state)


@router.callback_query(Onboarding.digest, F.data.startswith("ob:digest:"))
async def digest_chosen(callback: CallbackQuery, state: FSMContext, db: Database) -> None:
    # "ob:digest:08:30" — время тоже содержит ":", берём весь остаток
    raw = callback.data.split(":", 2)[2]
    if raw == "manual":
        await callback.answer()
        try:
            await callback.message.edit_text("Во сколько слать сводку? Формат: <code>09:30</code>")
        except TelegramBadRequest:
            pass
        return
    parsed = parse_digest_time(raw)
    if parsed is None:
        await callback.answer("Не поняла время.", show_alert=True)
        return
    await state.update_data(digest_time=parsed)
    await callback.answer()
    try:
        await callback.message.edit_text(f"Сводка: <b>{parsed}</b>")
    except TelegramBadRequest:
        pass
    await finish(callback.message, state, db)


@router.message(Onboarding.digest)
async def digest_manual(message: Message, state: FSMContext, db: Database) -> None:
    parsed = parse_digest_time(message.text or "")
    if parsed is None:
        await message.answer("Формат: <code>09:30</code> — часы и минуты.")
        return
    await state.update_data(digest_time=parsed)
    await finish(message, state, db)


# ------------------------------------------------------------------ настройки


@router.message(Command("settings"))
async def settings_command(message: Message, db: Database) -> None:
    row = await db.get_user(message.from_user.id)
    if row is None:
        await message.answer("Сначала напиши /start.")
        return
    feats = features_of(row)
    await message.answer(
        "<b>Твой профиль</b>\n\n"
        f"Цель: {GOAL_TITLES.get(row['goal'] or '', '—')}\n"
        f"Функции: {', '.join(FEATURE_TITLES.get(f, f) for f in feats) or '—'}\n"
        f"Стиль: {STYLE_TITLES.get(row['style'] or '', '—')}\n"
        f"Часовой пояс: {row['timezone']}\n"
        f"Сводка: {row['digest_time']}\n\n"
        "Можно пройти онбординг заново.",
        reply_markup=settings_keyboard(),
    )


@router.callback_query(F.data == "ob:restart")
async def restart(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    try:
        await callback.message.edit_text("Начинаем заново.")
    except TelegramBadRequest:
        pass
    await ask_goal(callback.message, state)
