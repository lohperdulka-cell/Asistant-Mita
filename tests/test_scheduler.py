import asyncio, sys, tempfile
from pathlib import Path
from datetime import datetime, timezone, timedelta
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import Chat, Message, User
import scheduler as sch
from database import Database

CALLS = []
class FakeBot(Bot):
    async def __call__(self, method, request_timeout=None):
        CALLS.append(method)
        n = type(method).__name__
        if n == "SendMessage":
            return Message(message_id=1, date=0, chat=Chat(id=int(method.chat_id), type="private"),
                           text=method.text or "", reply_markup=method.reply_markup).as_(self)
        if n == "GetMe": return User(id=1, is_bot=True, first_name="M", username="m")
        raise RuntimeError(n)

async def main():
    db = Database(Path(tempfile.mkdtemp())/"t.db"); await db.init()
    bot = FakeBot("1:T", default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    await db.touch_user(1, "a", "A")
    await db.save_profile(1, timezone="Europe/Moscow", digest_time="09:00", onboarded=True)
    s = sch.ReminderScheduler(bot, db)
    s.start()
    jobs = {j.id: j for j in s.scheduler.get_jobs()}
    print("jobs:", sorted(jobs), "running:", s.scheduler.running)
    assert set(jobs) == {"due_reminders", "digests"}
    assert str(jobs["due_reminders"].trigger) == "interval[0:01:00]"

    # сводка в неправильное время -> не отправляется
    CALLS.clear(); await s.send_digests()
    assert not [c for c in CALLS if type(c).__name__=="SendMessage"]
    print("digest time gate OK (nothing sent at wrong time)")

    # подкрутим digest_time на текущее локальное время пользователя
    from zoneinfo import ZoneInfo
    now_local = datetime.now(timezone.utc).astimezone(ZoneInfo("Europe/Moscow"))
    await db.save_profile(1, digest_time=now_local.strftime("%H:%M"))
    CALLS.clear(); await s.send_digests()
    sent = [c for c in CALLS if type(c).__name__=="SendMessage"]
    assert len(sent) == 1 and "Доброе утро" in sent[0].text
    print("digest sent at matching time OK")

    # защита от повторной отправки в тот же день
    CALLS.clear(); await s.send_digests()
    assert not [c for c in CALLS if type(c).__name__=="SendMessage"]
    print("digest daily de-dup OK")

    # следующая минута -> не отправляет (время не совпадает)
    await db.set_setting(1, "last_digest", "1970-01-01")
    await db.save_profile(1, digest_time="03:33")
    CALLS.clear(); await s.send_digests()
    assert not [c for c in CALLS if type(c).__name__=="SendMessage"]
    print("digest wrong-minute guard OK")

    s.shutdown()
    await asyncio.sleep(0.1)
    assert not s.scheduler.running, s.scheduler.state
    await db.close(); await bot.session.close()
    print("ALL SCHEDULER TESTS PASSED")

asyncio.run(main())
