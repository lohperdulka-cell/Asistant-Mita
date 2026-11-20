"""Трекер привычек: /habit add, /habit del, /habits, отметки кнопками.

Серия считается по подряд идущим дням; процент — за последние 7 дней.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import settings
from database import Database

log = logging.getLogger(__name__)
router = Router(name="habits")


def today_local(tz: ZoneInfo) -> date:
    return datetime.now(timezone.utc).astimezone(tz).date()


def local_tz(row) -> ZoneInfo:
    name = (row["timezone"] if row else None) or settings.default_tz
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo("UTC")


def streak_days(days: set[str], today: date) -> int:
    streak = 0
    cursor = today
    if today.isoformat() not in days:
        cursor = today - timedelta(days=1)  # сегодня ещё можно отметить
    while cursor.isoformat() in days:
        streak += 1
        cursor -= timedelta(days=1)
    return streak


def week_percent(days: set[str], today: date) -> int:
    start = today - timedelta(days=6)
    hits = sum(1 for i in range(7) if (start + timedelta(days=i)).isoformat() in days)
    return round(hits / 7 * 100)


# ------------------------------------------------------------------ команды


@router.message(Command("habit"))
async def habit_command(message: Message, command: CommandObject, db: Database) -> None:
    args = (command.args or "").strip()
    if not args:
        await message.answer(
            "Формат:\n"
            "<code>/habit add зарядка</code> — добавить\n"
            "<code>/habit del 3</code> — архивировать\n"
            "Отметки — кнопками в утренней сводке или /habits"
        )
        return

    parts = args.split(maxsplit=1)
    action = parts[0].lower()
    rest = parts[1].strip() if len(parts) > 1 else ""

    if action in {"add", "new", "добавить"}:
        if not rest:
            await message.answer("Напиши название: <code>/habit add зарядка</code>")
            return
        habit_id = await db.add_habit(message.from_user.id, rest)
        if habit_id is None:
            await message.answer("Такая привычка уже есть или название пустое.")
            return
        await message.answer(f"🔥 Привычка #{habit_id} «{rest}» добавлена.")
        return

    if action in {"del", "delete", "rm", "удалить"}:
        if not rest.isdigit():
            await message.answer("Формат: <code>/habit del 3</code>")
            return
        ok = await db.archive_habit(message.from_user.id, int(rest))
        await message.answer("Привычка архивирована." if ok else "Не нашла такую привычку.")
        return

    # /habit зарядка — трактуем как add
    habit_id = await db.add_habit(message.from_user.id, args)
    if habit_id is None:
        await message.answer("Такая привычка уже есть.")
        return
    await message.answer(f"🔥 Привычка #{habit_id} «{args}» добавлена.")


@router.message(Command("habits"))
async def habits_list(message: Message, db: Database) -> None:
    await send_habits_list(message, db, message.from_user.id)


async def send_habits_list(message: Message, db: Database, user_id: int) -> None:
    row = await db.get_user(user_id)
    tz = local_tz(row)
    today = today_local(tz)
    habits = await db.list_habits(user_id)
    if not habits:
        await message.answer(
            "Привычек пока нет.\n\nНажми 🔥 Привычки в меню и напиши название."
        )
        return

    done = await db.habit_done_today(user_id, today.isoformat())
    lines = [f"🔥 <b>Привычки</b> · {today:%d.%m}"]
    kb = InlineKeyboardBuilder()
    for habit in habits:
        since = (today - timedelta(days=60)).isoformat()
        days = set(await db.habit_days(user_id, habit["id"], since))
        mark = "✅" if habit["id"] in done else "▫️"
        lines.append(
            f"{mark} #{habit['id']} {habit['title']} — "
            f"серия {streak_days(days, today)} дн, неделя {week_percent(days, today)}%"
        )
        label = f"{'✅' if habit['id'] in done else '▫️'} {habit['title'][:18]}"
        kb.button(text=label, callback_data=f"habit:toggle:{habit['id']}")
    kb.adjust(2)
    await message.answer("\n".join(lines), reply_markup=kb.as_markup())


@router.callback_query(F.data.startswith("habit:toggle:"))
async def habit_toggle(callback: CallbackQuery, db: Database) -> None:
    habit_id = int(callback.data.rsplit(":", 1)[1])
    row = await db.get_user(callback.from_user.id)
    tz = local_tz(row)
    day = today_local(tz).isoformat()

    done = await db.habit_done_today(callback.from_user.id, day)
    if habit_id in done:
        await db.unlog_habit(callback.from_user.id, habit_id, day)
        await callback.answer("Отметка снята")
    else:
        ok = await db.log_habit(callback.from_user.id, habit_id, day)
        await callback.answer("Отмечено ✅" if ok else "Не нашла привычку", show_alert=not ok)

    if callback.message:
        try:
            await callback.message.edit_reply_markup(
                reply_markup=await habit_keyboard(db, callback.from_user.id, tz)
            )
        except Exception:
            pass


# ------------------------------------------------------------------ сводка


async def habit_keyboard(db: Database, user_id: int, tz: ZoneInfo):
    today = today_local(tz)
    habits = await db.list_habits(user_id)
    if not habits:
        return None
    done = await db.habit_done_today(user_id, today.isoformat())
    kb = InlineKeyboardBuilder()
    for habit in habits:
        mark = "✅" if habit["id"] in done else "▫️"
        kb.button(
            text=f"{mark} {habit['title'][:20]}",
            callback_data=f"habit:toggle:{habit['id']}",
        )
    kb.adjust(2)
    return kb.as_markup()


async def habits_digest_lines(db: Database, user_id: int, tz: ZoneInfo) -> list[str]:
    habits = await db.list_habits(user_id)
    if not habits:
        return []
    today = today_local(tz)
    done = await db.habit_done_today(user_id, today.isoformat())
    lines = []
    for habit in habits:
        since = (today - timedelta(days=60)).isoformat()
        days = set(await db.habit_days(user_id, habit["id"], since))
        mark = "✅" if habit["id"] in done else "▫️"
        lines.append(
            f"{mark} {habit['title']} — серия {streak_days(days, today)} дн, "
            f"неделя {week_percent(days, today)}%"
        )
    return lines


__all__ = [
    "router",
    "habit_keyboard",
    "habits_digest_lines",
    "streak_days",
    "week_percent",
    "today_local",
]
