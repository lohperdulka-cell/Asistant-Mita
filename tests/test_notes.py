import asyncio, sys, tempfile
from pathlib import Path
from datetime import datetime, timezone, timedelta
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandObject
from aiogram.types import Chat, Message, User, CallbackQuery

import notes, habits, scheduler as sch
from database import Database

CALLS = []
class FakeBot(Bot):
    async def __call__(self, method, request_timeout=None):
        CALLS.append(method)
        n = type(method).__name__
        if n == "SendMessage":
            return Message(message_id=1, date=0, chat=Chat(id=int(method.chat_id), type="private"),
                           text=method.text or "", reply_markup=method.reply_markup).as_(self)
        if n in ("AnswerCallbackQuery", "EditMessageReplyMarkup"): return True
        if n == "GetMe": return User(id=999, is_bot=True, first_name="M", username="m_bot")
        raise RuntimeError(n)

UID = 555
USER = User(id=UID, is_bot=False, first_name="U")
CHAT = Chat(id=UID, type="private")

def msg(text, bot):
    return Message(message_id=1, date=0, chat=CHAT, from_user=USER, text=text).as_(bot)

def cmd(text, bot):
    parts = text.split(maxsplit=1)
    return msg(text, bot), CommandObject(command=parts[0][1:], args=parts[1] if len(parts)>1 else None)

def cb(data, bot):
    return CallbackQuery(id="1", from_user=USER, chat_instance="ci", data=data,
        message=Message(message_id=2, date=0, chat=CHAT, from_user=USER, text="x").as_(bot)).as_(bot)

async def main():
    db = Database(Path(tempfile.mkdtemp())/"t.db"); await db.init()
    bot = FakeBot("123:T", default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    await db.touch_user(UID, "u", "U")
    await db.save_profile(UID, goal="work", features=["notes","reminders","habits"],
                          style="brief", timezone="Europe/Moscow", digest_time="09:00", onboarded=True)

    # /note без аргумента
    m, c = cmd("/note", bot); await notes.note_add(m, c, db)
    assert "Напиши текст" in CALLS[-1].text

    # /note с текстом
    m, c = cmd("/note купить хлеб", bot); await notes.note_add(m, c, db)
    assert "Заметка #1" in CALLS[-1].text
    m, c = cmd("/note позвонить врачу", bot); await notes.note_add(m, c, db)
    print("note add OK")

    # /notes
    CALLS.clear(); m, c = cmd("/notes", bot); await notes.notes_list(m, db)
    assert "(2)" in CALLS[-1].text and "Заметки" in CALLS[-1].text and CALLS[-1].reply_markup
    print("notes list OK")

    # /done
    m, c = cmd("/done 1", bot); await notes.note_done(m, c, db)
    assert "закрыта" in CALLS[-1].text
    m, c = cmd("/done 999", bot); await notes.note_done(m, c, db)
    assert "Не нашла" in CALLS[-1].text
    # кнопка
    await notes.note_done_button(cb("note:done:2", bot), db)
    assert await db.list_notes(UID) == []
    print("note done OK (cmd + button)")

    # /remind через 1 минуту
    CALLS.clear()
    m, c = cmd("/remind через 1 минуту проверить чайник", bot)
    await notes.remind_add(m, c, db)
    print("remind reply:", CALLS[-1].text)
    assert "Напоминание #1" in CALLS[-1].text and "проверить чайник" in CALLS[-1].text

    # /remind с нераспознанным временем
    m, c = cmd("/remind просто текст без времени", bot); await notes.remind_add(m, c, db)
    assert "Не поняла время" in CALLS[-1].text
    print("remind bad-time guard OK")

    # /reminders
    CALLS.clear(); m, c = cmd("/reminders", bot); await notes.reminders_list(m, db)
    assert "Напоминания" in CALLS[-1].text and "чайник" in CALLS[-1].text
    print("reminders list OK")

    # планировщик: подкрутим время в БД на "сейчас" и вызовем джобу
    past = (datetime.now(timezone.utc) - timedelta(seconds=5)).replace(microsecond=0).isoformat()
    await db.execute("UPDATE reminders SET remind_at = ? WHERE id = 1", (past,))
    CALLS.clear()
    sched = sch.ReminderScheduler(bot, db)
    await sched.send_due_reminders()
    sent = [c for c in CALLS if type(c).__name__ == "SendMessage"]
    assert len(sent) == 1 and "чайник" in sent[0].text, [c.text for c in sent]
    print("SCHEDULER DELIVERED:", sent[0].text.replace("\n"," | "))
    # повторный вызов не должен дублировать
    CALLS.clear(); await sched.send_due_reminders()
    assert not [c for c in CALLS if type(c).__name__=="SendMessage"]
    print("no duplicate delivery OK")

    # /delremind
    m, c = cmd("/delremind 1", bot); await notes.remind_delete(m, c, db)
    assert "Удалено" in CALLS[-1].text

    # привычки
    await db.add_habit(UID, "зарядка")
    CALLS.clear(); m, c = cmd("/habits", bot); await habits.habits_list(m, db)
    assert "Привычки" in CALLS[-1].text and "зарядка" in CALLS[-1].text
    await habits.habit_toggle(cb("habit:toggle:1", bot), db)
    today = datetime.now(timezone.utc).astimezone(habits.local_tz(await db.get_user(UID))).date().isoformat()
    assert 1 in await db.habit_done_today(UID, today)
    await habits.habit_toggle(cb("habit:toggle:1", bot), db)
    assert 1 not in await db.habit_done_today(UID, today)
    print("habits toggle OK")

    # сводка
    await db.add_note(UID, "тестовая заметка")
    await db.add_habit(UID, "чтение")
    row = await db.get_user(UID)
    text, kb = await notes.digest_text(db, row)
    print("DIGEST:\n" + text)
    assert "Открытые заметки" in text and "тестовая заметка" in text
    assert "Привычки" in text and kb is not None
    print("digest OK")

    # send_digest_to реально отправляет
    CALLS.clear(); await sched.send_digest_to(row)
    sent = [c for c in CALLS if type(c).__name__=="SendMessage"]
    assert sent and sent[0].reply_markup is not None
    print("digest delivery OK")

    await db.close(); await bot.session.close()
    print("ALL NOTES/REMINDERS/HABITS TESTS PASSED")

async def runner():
    try:
        await main()
    finally:
        pass

asyncio.run(runner())
