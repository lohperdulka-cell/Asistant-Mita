"""Пункт N2: ввод без команд — кнопка меню -> FSM -> сохранение.

Проверяем на реальном диспетчере: событие проходит через все роутеры,
поэтому тест ловит и конфликты фильтров (например, перехват текста
RAG-обработчиком).
"""

import asyncio
import sys
import tempfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

import llm_client

def _fake_llm(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": "Ответ Миты"}}]})

_REAL_CLIENT = httpx.AsyncClient
httpx.AsyncClient = lambda *a, **kw: _REAL_CLIENT(
    *a, transport=httpx.MockTransport(_fake_llm), **kw
)

from aiogram import Bot
from aiogram.fsm.storage.base import StorageKey
from aiogram.types import CallbackQuery, Chat, Message, Update, User

import bot as bot_module
from database import Database

FAILED = []


def check(name: str, ok: bool, detail: str = "") -> None:
    if ok:
        print(f"PASS  {name}")
    else:
        FAILED.append(name)
        print(f"FAIL  {name}  {detail}")


CALLS = []


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
        if name in ("AnswerCallbackQuery", "EditMessageReplyMarkup", "SendChatAction"):
            return True
        if name == "EditMessageText":
            return Message(
                message_id=method.message_id,
                date=0,
                chat=Chat(id=UID, type="private"),
                text=method.text or "",
            ).as_(self)
        if name == "GetMe":
            return User(id=999, is_bot=True, first_name="M", username="m_bot")
        raise RuntimeError(name)


UID = 777
USER = User(id=UID, is_bot=False, first_name="Тест")
CHAT = Chat(id=UID, type="private")


def make_message(bot, text):
    return Message(message_id=1, date=0, chat=CHAT, from_user=USER, text=text).as_(bot)


def make_callback(bot, data):
    return CallbackQuery(
        id="1",
        from_user=USER,
        chat_instance="ci",
        data=data,
        message=Message(
            message_id=2, date=0, chat=CHAT, from_user=USER, text="x"
        ).as_(bot),
    ).as_(bot)


def last_text() -> str:
    for method in reversed(CALLS):
        if type(method).__name__ == "SendMessage":
            return method.text or ""
    return ""


async def main() -> None:
    workdir = tempfile.mkdtemp()
    db = Database(str(Path(workdir) / "flows.db"))
    await db.init()
    await db.touch_user(UID, "tester", "Тест")

    bot = FakeBot("123:T")
    dp = bot_module.build_dispatcher(db)
    dp.bot = bot
    key = StorageKey(bot_id=bot.id, chat_id=CHAT.id, user_id=USER.id)

    # --- заметка через кнопку ---------------------------------------------
    await dp.feed_update(bot, Update(update_id=1, callback_query=make_callback(bot, "menu:notes")))
    state = await dp.storage.get_state(key)
    check("N2 кнопка «Заметки» включает FSM", state == "Flow:note", f"state={state}")
    check("N2 бот просит текст заметки", "Напиши текст" in last_text(), last_text()[:60])

    await dp.feed_update(bot, Update(update_id=2, message=make_message(bot, "купить молоко")))
    notes = await db.list_notes(UID)
    check("N2 текст сохранён как заметка", len(notes) == 1 and notes[0]["text"] == "купить молоко",
          str([dict(n) for n in notes]))
    state = await dp.storage.get_state(key)
    check("N2 состояние сброшено после сохранения", state is None, f"state={state}")

    # --- напоминание через кнопку -----------------------------------------
    await dp.feed_update(
        bot, Update(update_id=3, callback_query=make_callback(bot, "menu:reminders"))
    )
    check("N2 кнопка «Напоминания» включает FSM",
          await dp.storage.get_state(key) == "Flow:reminder")
    await dp.feed_update(
        bot, Update(update_id=4, message=make_message(bot, "через 5 минут выпить воды"))
    )
    reminders = await db.list_reminders(UID)
    check("N2 напоминание создано без команды", len(reminders) >= 1,
          str([dict(r) for r in reminders]))

    # --- привычка через кнопку --------------------------------------------
    await dp.feed_update(
        bot, Update(update_id=5, callback_query=make_callback(bot, "menu:habits"))
    )
    await dp.feed_update(bot, Update(update_id=6, message=make_message(bot, "зарядка")))
    habits = await db.list_habits(UID)
    check("N2 привычка создана без команды", len(habits) == 1, str([dict(h) for h in habits]))

    # --- материал текстом через кнопку ------------------------------------
    await dp.feed_update(
        bot, Update(update_id=7, callback_query=make_callback(bot, "menu:materials"))
    )
    await dp.feed_update(
        bot,
        Update(
            update_id=8,
            message=make_message(bot, "Фотосинтез — это процесс в хлоропластах растений. " * 4),
        ),
    )
    materials = await db.list_materials(UID)
    check("N2 материал сохранён без команды", len(materials) == 1,
          str([dict(m) for m in materials]))

    # --- отмена ------------------------------------------------------------
    await dp.feed_update(
        bot, Update(update_id=9, callback_query=make_callback(bot, "menu:notes"))
    )
    await dp.feed_update(
        bot, Update(update_id=10, callback_query=make_callback(bot, "flow:cancel"))
    )
    state = await dp.storage.get_state(key)
    check("N2 отмена сбрасывает состояние", state is None, f"state={state}")
    before = len(await db.list_notes(UID))
    await dp.feed_update(bot, Update(update_id=11, message=make_message(bot, "после отмены")))
    after = len(await db.list_notes(UID))
    check("N2 после отмены текст не пишется в заметки", before == after, f"{before}->{after}")

    # --- обычный текст уходит в чат, а не в RAG ---------------------------
    object.__setattr__(bot_module.settings, "openrouter_api_key", "test-key")
    await dp.feed_update(bot, Update(update_id=12, message=make_message(bot, "Как дела?")))
    history = await db.chat_history(UID)
    roles = [row["role"] for row in history]
    check("N2 свободный текст идёт в чат с LLM", roles == ["user", "assistant"], str(roles))
    check("N2 ответ LLM сохранён", history and history[-1]["content"] == "Ответ Миты")

    # --- явный RAG-запрос по-прежнему работает ----------------------------
    CALLS.clear()
    await dp.feed_update(
        bot,
        Update(update_id=13, message=make_message(bot, "найди в моих материалах фотосинтез")),
    )
    sent = [m.text or "" for m in CALLS if type(m).__name__ == "SendMessage"]
    check(
        "N2 RAG-запрос перехвачен materials",
        any("Ищу в твоих материалах" in text for text in sent),
        str(sent)[:120],
    )
    check(
        "N2 RAG-ответ помечен как контекст",
        any("RAG-контекст" in text or "не переобучается" in text for text in sent),
        str(sent)[-1][:80],
    )

    await db.close()

    print()
    if FAILED:
        print(f"ПРОВАЛЕНО: {len(FAILED)} — {FAILED}")
        raise SystemExit(1)
    print("ALL FLOWS (N2) TESTS PASSED")


asyncio.run(main())
httpx.AsyncClient = _REAL_CLIENT
