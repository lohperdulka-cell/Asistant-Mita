"""Пункт N7: онбординг проходится целиком и перепроходится из /settings.

События идут через настоящий диспетчер, поэтому тест ловит конфликты
роутеров: например, если /settings перехватит старый обработчик.
"""

import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aiogram import Bot
from aiogram.fsm.storage.base import StorageKey
from aiogram.types import CallbackQuery, Chat, Message, Update, User

import bot as bot_module
from database import Database

FAILED: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    if ok:
        print(f"PASS  {name}")
    else:
        FAILED.append(name)
        print(f"FAIL  {name}  {detail}")


CALLS: list = []


class FakeBot(Bot):
    async def __call__(self, method, request_timeout=None):
        CALLS.append(method)
        name = type(method).__name__
        if name == "SendMessage":
            return Message(
                message_id=len(CALLS),
                date=0,
                chat=Chat(id=int(method.chat_id), type="private"),
                text=method.text or "",
                reply_markup=method.reply_markup,
            ).as_(self)
        if name in ("EditMessageText", "EditMessageReplyMarkup"):
            return Message(
                message_id=1,
                date=0,
                chat=Chat(id=UID, type="private"),
                text=getattr(method, "text", "") or "",
                reply_markup=getattr(method, "reply_markup", None),
            ).as_(self)
        if name in ("AnswerCallbackQuery", "SendChatAction"):
            return True
        if name == "GetMe":
            return User(id=999, is_bot=True, first_name="M", username="m_bot")
        raise RuntimeError(name)


UID = 801
USER = User(id=UID, is_bot=False, first_name="Тест")
CHAT = Chat(id=UID, type="private")


def msg(bot, text):
    return Message(message_id=1, date=0, chat=CHAT, from_user=USER, text=text).as_(bot)


def cb(bot, data):
    return CallbackQuery(
        id="1",
        from_user=USER,
        chat_instance="ci",
        data=data,
        message=Message(message_id=2, date=0, chat=CHAT, from_user=USER, text="x").as_(bot),
    ).as_(bot)


def last_text() -> str:
    for method in reversed(CALLS):
        if type(method).__name__ == "SendMessage":
            return method.text or ""
    return ""


async def main() -> None:
    db = Database(str(Path(tempfile.mkdtemp()) / "ob.db"))
    await db.init()
    bot = FakeBot("123:T")
    dp = bot_module.build_dispatcher(db)
    dp.bot = bot
    key = StorageKey(bot_id=bot.id, chat_id=CHAT.id, user_id=USER.id)

    async def feed(update_id, **kwargs):
        await dp.feed_update(bot, Update(update_id=update_id, **kwargs))

    # ---- /start у нового пользователя запускает онбординг
    await feed(1, message=msg(bot, "/start"))
    check("N7 /start запускает шаг цели",
          await dp.storage.get_state(key) == "Onboarding:goal",
          str(await dp.storage.get_state(key)))
    check("N7 показаны цели", "цель" in last_text().lower() or "Мита" in last_text())

    # ---- шаг 1: цель
    await feed(2, callback_query=cb(bot, "ob:goal:study"))
    check("N7 цель принята, шаг функций",
          await dp.storage.get_state(key) == "Onboarding:features")
    data = await dp.storage.get_data(key)
    check("N7 цель сохранена в состоянии", data.get("goal") == "study", str(data.get("goal")))

    # ---- шаг 2: мультивыбор функций
    await feed(3, callback_query=cb(bot, "ob:feat:notes"))
    await feed(4, callback_query=cb(bot, "ob:feat:habits"))
    data = await dp.storage.get_data(key)
    check("N7 функции выбраны", set(data.get("features") or []) == {"notes", "habits"},
          str(data.get("features")))

    await feed(5, callback_query=cb(bot, "ob:feat:notes"))
    data = await dp.storage.get_data(key)
    check("N7 повторное нажатие снимает выбор", data.get("features") == ["habits"],
          str(data.get("features")))
    await feed(6, callback_query=cb(bot, "ob:feat:notes"))

    await feed(7, callback_query=cb(bot, "ob:feat:done"))
    check("N7 переход к стилю",
          await dp.storage.get_state(key) == "Onboarding:style")

    # ---- шаг 3: стиль
    await feed(8, callback_query=cb(bot, "ob:style:friendly"))
    check("N7 переход к часовому поясу",
          await dp.storage.get_state(key) == "Onboarding:timezone")

    # ---- шаг 4: часовой пояс и время сводки
    await feed(9, callback_query=cb(bot, "ob:tz:Europe/Moscow"))
    check("N7 переход к времени сводки",
          await dp.storage.get_state(key) == "Onboarding:digest")

    await feed(10, callback_query=cb(bot, "ob:digest:08:30"))
    check("N7 состояние очищено после финиша",
          await dp.storage.get_state(key) is None)

    # ---- профиль в БД
    row = await db.get_user(UID)
    check("N7 онбординг отмечен", bool(row["onboarded"]))
    check("N7 цель в БД", row["goal"] == "study", str(row["goal"]))
    check("N7 стиль в БД", row["style"] == "friendly", str(row["style"]))
    check("N7 часовой пояс в БД", row["timezone"] == "Europe/Moscow", str(row["timezone"]))
    check("N7 время сводки в БД", row["digest_time"] == "08:30", str(row["digest_time"]))
    from database import features_of

    check("N7 функции в БД", set(features_of(row)) == {"notes", "habits"}, str(features_of(row)))

    # ---- повторный /start не переспрашивает
    CALLS.clear()
    await feed(11, message=msg(bot, "/start"))
    check("N7 повторный /start не запускает онбординг",
          await dp.storage.get_state(key) is None)
    check("N7 повторный /start приветствует", "помню" in last_text().lower(),
          last_text()[:60])

    # ---- /settings показывает расширенный экран
    CALLS.clear()
    await feed(12, message=msg(bot, "/settings"))
    text = last_text()
    check("N7 /settings показывает настройки", "Настройки" in text, text[:60])
    check("N7 /settings показывает стиль", "Стиль" in text)
    check("N7 /settings показывает провайдера", "Провайдер" in text)
    check("N7 /settings предлагает перепройти",
          "Онбординг заново" in str(CALLS[-1].reply_markup))

    # ---- перепрохождение
    await feed(13, callback_query=cb(bot, "ob:restart"))
    check("N7 кнопка перезапускает онбординг",
          await dp.storage.get_state(key) == "Onboarding:goal",
          str(await dp.storage.get_state(key)))

    await feed(14, callback_query=cb(bot, "ob:goal:work"))
    await feed(15, callback_query=cb(bot, "ob:feat:reminders"))
    await feed(16, callback_query=cb(bot, "ob:feat:done"))
    await feed(17, callback_query=cb(bot, "ob:style:formal"))
    await feed(18, callback_query=cb(bot, "ob:tz:Asia/Almaty"))
    await feed(19, callback_query=cb(bot, "ob:digest:21:00"))

    row = await db.get_user(UID)
    check("N7 перепрохождение обновило цель", row["goal"] == "work", str(row["goal"]))
    check("N7 перепрохождение обновило стиль", row["style"] == "formal", str(row["style"]))
    check("N7 перепрохождение обновило пояс",
          row["timezone"] == "Asia/Almaty", str(row["timezone"]))
    check("N7 перепрохождение обновило функции",
          set(features_of(row)) == {"reminders"}, str(features_of(row)))

    # ---- ручной ввод времени
    await feed(20, callback_query=cb(bot, "ob:restart"))
    await feed(21, callback_query=cb(bot, "ob:goal:health"))
    await feed(22, callback_query=cb(bot, "ob:feat:habits"))
    await feed(23, callback_query=cb(bot, "ob:feat:done"))
    await feed(24, callback_query=cb(bot, "ob:style:brief"))
    await feed(25, callback_query=cb(bot, "ob:tz:UTC"))
    await feed(26, message=msg(bot, "7:5"))
    row = await db.get_user(UID)
    check("N7 ручное время нормализовано", row["digest_time"] == "07:05",
          str(row["digest_time"]))
    check("N7 невалидное время не сохраняется",
          await dp.storage.get_state(key) is None)

    await db.close()

    print()
    if FAILED:
        print(f"ПРОВАЛЕНО: {len(FAILED)} — {FAILED}")
        raise SystemExit(1)
    print("ALL ONBOARDING (N7) TESTS PASSED")


asyncio.run(main())
