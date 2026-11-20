import asyncio
import os
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

TMP = Path(tempfile.mkdtemp())
os.environ["DATA_DIR"] = str(TMP)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandObject
from aiogram.types import CallbackQuery, Chat, Message, User

import habits
from database import Database

CALLS = []
UID = 77
USER = User(id=UID, is_bot=False, first_name="H")
CHAT = Chat(id=UID, type="private")


class FakeBot(Bot):
    async def __call__(self, method, request_timeout=None):
        CALLS.append(method)
        name = type(method).__name__
        if name == "SendMessage":
            return Message(
                message_id=1,
                date=0,
                chat=Chat(id=int(method.chat_id), type="private"),
                text=method.text or "",
                reply_markup=method.reply_markup,
            ).as_(self)
        if name in ("AnswerCallbackQuery", "EditMessageReplyMarkup"):
            return True
        if name == "GetMe":
            return User(id=1, is_bot=True, first_name="M", username="m")
        raise RuntimeError(name)


def msg(text, bot):
    return Message(message_id=1, date=0, chat=CHAT, from_user=USER, text=text).as_(bot)


def cb(data, bot):
    return CallbackQuery(
        id="1",
        from_user=USER,
        chat_instance="ci",
        data=data,
        message=Message(message_id=2, date=0, chat=CHAT, from_user=USER, text="x").as_(bot),
    ).as_(bot)


def cmd(command, args=None):
    return CommandObject(command=command, args=args)


async def main():
    db = Database(TMP / "t.db")
    await db.init()
    bot = FakeBot("1:T", default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    await db.touch_user(UID, "h", "H")
    await db.save_profile(
        UID, goal="health", features=["habits"], style="brief",
        timezone="Europe/Moscow", onboarded=True,
    )

    # /habit без аргументов -> подсказка
    CALLS.clear()
    await habits.habit_command(msg("/habit", bot), cmd("habit"), db)
    assert "Формат" in CALLS[-1].text
    print("habit help OK")

    # /habit add
    CALLS.clear()
    await habits.habit_command(msg("/habit add зарядка", bot), cmd("habit", "add зарядка"), db)
    assert "Привычка #1" in CALLS[-1].text
    # дубликат
    CALLS.clear()
    await habits.habit_command(msg("/habit add зарядка", bot), cmd("habit", "add зарядка"), db)
    assert "уже есть" in CALLS[-1].text
    # shorthand /habit чтение
    await habits.habit_command(msg("/habit чтение", bot), cmd("habit", "чтение"), db)
    assert len(await db.list_habits(UID)) == 2
    print("habit add + dedupe + shorthand OK")

    # /habits
    CALLS.clear()
    await habits.habits_list(msg("/habits", bot), db)
    text = CALLS[-1].text
    print(text)
    assert "серия 0 дн" in text and "неделя 0%" in text
    assert CALLS[-1].reply_markup is not None
    buttons = [b.callback_data for row in CALLS[-1].reply_markup.inline_keyboard for b in row]
    assert "habit:toggle:1" in buttons and "habit:toggle:2" in buttons
    print("habits list OK")

    # отметка кнопкой
    await habits.habit_toggle(cb("habit:toggle:1", bot), db)
    tz = ZoneInfo("Europe/Moscow")
    today = datetime.now(timezone.utc).astimezone(tz).date()
    assert 1 in await db.habit_done_today(UID, today.isoformat())
    CALLS.clear()
    await habits.habits_list(msg("/habits", bot), db)
    assert "серия 1 дн" in CALLS[-1].text
    print("toggle + streak update OK")

    # повторное нажатие снимает отметку
    await habits.habit_toggle(cb("habit:toggle:1", bot), db)
    assert 1 not in await db.habit_done_today(UID, today.isoformat())
    print("toggle off OK")

    # серия за 5 дней подряд
    for i in range(1, 5):
        await db.log_habit(UID, 1, (today - timedelta(days=i)).isoformat())
    await db.log_habit(UID, 1, today.isoformat())
    CALLS.clear()
    await habits.habits_list(msg("/habits", bot), db)
    assert "серия 5 дн" in CALLS[-1].text, CALLS[-1].text
    assert "неделя 71%" in CALLS[-1].text, CALLS[-1].text
    print("5-day streak OK")

    # архив привычки
    CALLS.clear()
    await habits.habit_command(msg("/habit del 2", bot), cmd("habit", "del 2"), db)
    assert "архивирована" in CALLS[-1].text
    assert len(await db.list_habits(UID)) == 1
    CALLS.clear()
    await habits.habit_command(msg("/habit del 99", bot), cmd("habit", "del 99"), db)
    assert "Не нашла" in CALLS[-1].text
    print("habit archive OK")

    # сводка содержит клавиатуру привычек
    import notes

    row = await db.get_user(UID)
    text, kb = await notes.digest_text(db, row)
    assert "Привычки" in text and kb is not None
    print("digest has habit keyboard OK")

    # чужой пользователь не может отметить чужую привычку
    await db.touch_user(888, "x", "X")
    ok = await db.log_habit(888, 1, today.isoformat())
    assert ok is False
    print("cross-user habit guard OK")

    await db.close()
    await bot.session.close()
    print("ALL HABITS TESTS PASSED")


asyncio.run(main())
