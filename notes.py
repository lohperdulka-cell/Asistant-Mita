"""Заметки, напоминания и ежедневная сводка.

Команды: /note, /notes, /done, /remind, /reminders, /delremind.
Сводка собирается в scheduler.py и переиспользует digest_text().
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import settings
from database import Database
from llm_client import llm
from timeparse import human_delta, parse_reminder

log = logging.getLogger(__name__)
router = Router(name="notes")

MAX_NOTE = 3500


def user_tz(row) -> ZoneInfo:
    name = (row["timezone"] if row else None) or settings.default_tz
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo("UTC")


def fmt_local(when_utc: datetime, tz: ZoneInfo) -> str:
    return when_utc.astimezone(tz).strftime("%d.%m %H:%M")


# ------------------------------------------------------------------- заметки


@router.message(Command("note"))
async def note_add(message: Message, command: CommandObject, db: Database) -> None:
    text = (command.args or "").strip()
    if not text:
        await message.answer(
            "Напиши текст заметки: <code>/note купить хлеб</code>"
        )
        return
    note_id = await db.add_note(message.from_user.id, text[:MAX_NOTE])
    await message.answer(f"📝 Заметка #{note_id} сохранена.")


@router.message(Command("notes"))
async def notes_list(message: Message, db: Database) -> None:
    rows = await db.list_notes(message.from_user.id)
    if not rows:
        await message.answer("Открытых заметок нет. Добавь: /note текст")
        return
    lines = [f"<b>Заметки</b> ({len(rows)}):"]
    kb = InlineKeyboardBuilder()
    for row in rows:
        text = row["text"].replace("\n", " ")
        lines.append(f"#{row['id']} · {text[:120]}")
        kb.button(text=f"✅ #{row['id']}", callback_data=f"note:done:{row['id']}")
    kb.adjust(3)
    lines.append("\nЗакрыть: /done &lt;id&gt; или кнопкой ниже.")
    await message.answer("\n".join(lines), reply_markup=kb.as_markup())


@router.message(Command("done"))
async def note_done(message: Message, command: CommandObject, db: Database) -> None:
    raw = (command.args or "").strip().lstrip("#")
    if not raw.isdigit():
        await message.answer("Формат: <code>/done 3</code>")
        return
    ok = await db.complete_note(message.from_user.id, int(raw))
    await message.answer("✅ Заметка закрыта." if ok else "Не нашла такую заметку.")


@router.callback_query(F.data.startswith("note:done:"))
async def note_done_button(callback: CallbackQuery, db: Database) -> None:
    note_id = int(callback.data.rsplit(":", 1)[1])
    ok = await db.complete_note(callback.from_user.id, note_id)
    await callback.answer("Закрыто" if ok else "Не найдено", show_alert=not ok)
    if ok and callback.message:
        try:
            await callback.message.edit_reply_markup(
                reply_markup=await rebuild_notes_kb(db, callback.from_user.id)
            )
        except Exception:
            pass


async def rebuild_notes_kb(db: Database, user_id: int):
    rows = await db.list_notes(user_id)
    kb = InlineKeyboardBuilder()
    for row in rows:
        kb.button(text=f"✅ #{row['id']}", callback_data=f"note:done:{row['id']}")
    kb.adjust(3)
    return kb.as_markup()


# -------------------------------------------------------------- напоминания


@router.message(Command("remind"))
async def remind_add(message: Message, command: CommandObject, db: Database) -> None:
    raw = (command.args or "").strip()
    if not raw:
        await message.answer(
            "Формат: <code>/remind через 10 минут позвонить маме</code>\n"
            "Или: <code>/remind завтра в 9:00 зарядка</code>"
        )
        return

    row = await db.get_user(message.from_user.id)
    tz = user_tz(row)
    parsed = await parse_reminder(raw, str(tz))
    if parsed is None:
        await message.answer(
            "Не поняла время 😔\n"
            "Примеры: <code>/remind через 10 минут ...</code>, "
            "<code>/remind завтра в 9:00 ...</code>, "
            "<code>/remind 25.12 в 10:00 ...</code>"
        )
        return

    text = parsed.rest or "напоминание"
    reminder_id = await db.add_reminder(
        message.from_user.id, text, parsed.when_utc.isoformat()
    )
    await message.answer(
        f"⏰ Напоминание #{reminder_id} на {fmt_local(parsed.when_utc, tz)} "
        f"({human_delta(parsed.when_utc)}): {text}"
    )


@router.message(Command("reminders"))
async def reminders_list(message: Message, db: Database) -> None:
    rows = await db.list_reminders(message.from_user.id)
    if not rows:
        await message.answer("Активных напоминаний нет.")
        return
    row = await db.get_user(message.from_user.id)
    tz = user_tz(row)
    lines = ["<b>Напоминания</b>:"]
    for item in rows:
        when = datetime.fromisoformat(item["remind_at"])
        lines.append(f"#{item['id']} · {fmt_local(when, tz)} · {item['text'][:80]}")
    lines.append("\nУдалить: /delremind &lt;id&gt;")
    await message.answer("\n".join(lines))


@router.message(Command("delremind"))
async def remind_delete(message: Message, command: CommandObject, db: Database) -> None:
    raw = (command.args or "").strip().lstrip("#")
    if not raw.isdigit():
        await message.answer("Формат: <code>/delremind 3</code>")
        return
    ok = await db.delete_reminder(message.from_user.id, int(raw))
    await message.answer("Удалено." if ok else "Не нашла такое напоминание.")


# ------------------------------------------------------------------- сводка


async def digest_text(db: Database, user_row) -> tuple[str, InlineKeyboardBuilder | None]:
    """Собирает текст ежедневной сводки и (опционально) клавиатуру привычек."""
    from habits import habit_keyboard, habits_digest_lines

    user_id = user_row["user_id"]
    tz = user_tz(user_row)
    now_local = datetime.now(timezone.utc).astimezone(tz)

    notes = await db.list_notes(user_id)
    reminders = await db.reminders_for_day(user_id, now_local.strftime("%Y-%m-%d"))

    parts = [f"☀️ <b>Доброе утро!</b> {now_local:%d.%m, %H:%M}"]

    if notes:
        parts.append(f"\n📝 <b>Открытые заметки</b> ({len(notes)}):")
        for row in notes[:10]:
            parts.append(f"· #{row['id']} {row['text'][:100]}")
    else:
        parts.append("\n📝 Открытых заметок нет.")

    if reminders:
        parts.append(f"\n⏰ <b>Напоминания на день</b> ({len(reminders)}):")
        for row in reminders:
            when = datetime.fromisoformat(row["remind_at"])
            parts.append(f"· {fmt_local(when, tz)} — {row['text'][:80]}")
    else:
        parts.append("\n⏰ Напоминаний на день нет.")

    habit_lines = await habits_digest_lines(db, user_id, tz)
    if habit_lines:
        parts.append("\n🔥 <b>Привычки</b>:")
        parts.extend(habit_lines)

    text = "\n".join(parts)

    # Приветствие в выбранном стиле — только если LLM доступна.
    style = user_row["style"] or "brief"
    greeting = await llm.complete(
        "Напиши одно короткое приветствие на утро (1 предложение), без фактов.",
        system="Ты — Мита, личный ассистент. Только приветствие, без списков.",
        style=style,
        max_tokens=60,
    )
    if greeting.ok:
        text = text.replace("☀️ <b>Доброе утро!</b>", f"☀️ <b>{greeting.text.strip()}</b>", 1)

    kb = await habit_keyboard(db, user_id, tz) if habit_lines else None
    return text, kb


__all__ = ["router", "digest_text", "user_tz", "fmt_local"]
