"""Пункт N5: память диалога — история, контекст, обрезка, изоляция.

Проверяем, что история реально уходит в LLM (второй запрос видит первый),
что она не растёт бесконечно и что пользователи не видят чужие сообщения.
"""

import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

SENT = []


def _handler(request: httpx.Request) -> httpx.Response:
    import json

    payload = json.loads(request.content.decode())
    SENT.append(payload.get("messages", []))
    return httpx.Response(
        200, json={"choices": [{"message": {"content": "ответ-" + str(len(SENT))}}]}
    )


_REAL = httpx.AsyncClient
httpx.AsyncClient = lambda *a, **kw: _REAL(
    *a, transport=httpx.MockTransport(_handler), **kw
)

from config import settings
from database import Database
from llm_chat import reply_with_llm, system_prompt
from llm_client import llm

FAILED = []


def check(name: str, ok: bool, detail: str = "") -> None:
    if ok:
        print(f"PASS  {name}")
    else:
        FAILED.append(name)
        print(f"FAIL  {name}  {detail}")


class FakeMessage:
    def __init__(self, user_id: int):
        self.text = ""
        self.from_user = type("U", (), {"id": user_id})()
        self.chat = type("C", (), {"id": user_id})()
        self.bot = type("B", (), {"send_chat_action": self._act})()
        self.sent: list[str] = []

    async def _act(self, *a, **kw):
        return True

    async def answer(self, text, **kw):
        self.sent.append(text)
        return None


async def main() -> None:
    object.__setattr__(settings, "openrouter_api_key", "k")
    object.__setattr__(settings, "groq_api_key", "")
    llm.reset_cooldown() if hasattr(llm, "reset_cooldown") else None

    db = Database(str(Path(tempfile.mkdtemp()) / "c.db"))
    await db.init()
    uid, other = 501, 502
    for user in (uid, other):
        await db.touch_user(user, "u", "U")
        await db.save_profile(user, goal="study", style="friendly", features=["notes"])

    # ---- первый запрос: истории ещё нет
    msg = FakeMessage(uid)
    await reply_with_llm(msg, db, "Меня зовут Аня")
    check("N5 первый ответ получен", len(msg.sent) == 1, str(msg.sent))
    first_messages = SENT[-1]
    check("N5 в первом запросе нет истории",
          len([m for m in first_messages if m["role"] != "system"]) == 1,
          str(first_messages))

    # ---- второй запрос: видит первый
    await reply_with_llm(msg, db, "Как меня зовут?")
    second = [m for m in SENT[-1] if m["role"] != "system"]
    check("N5 второй запрос содержит историю", len(second) == 3, str(second))
    check("N5 история начинается с прошлого вопроса",
          second[0]["content"] == "Меня зовут Аня", second[0]["content"])
    check("N5 в истории есть прошлый ответ",
          second[1]["role"] == "assistant", second[1]["role"])

    # ---- системный промпт берёт цель из профиля
    row = await db.get_user(uid)
    prompt = await system_prompt(db, row)
    check("N5 системный промпт содержит цель словами", "Учёба" in prompt, prompt[:80])
    check("N5 системный промпт не содержит код цели", "study" not in prompt)
    check("N5 системный промпт содержит стиль", "Стиль ответа" in prompt)
    check("N5 системный промпт без профиля не падает",
          "Мита" in await system_prompt(db, None))

    # ---- изоляция пользователей
    await db.add_chat_message(other, "user", "секрет второго", "bot")
    history = await db.chat_history(uid)
    texts = [r["content"] for r in history]
    check("N5 чужое сообщение не попало в историю", "секрет второго" not in texts, str(texts))

    # ---- обрезка истории
    for index in range(400):
        await db.add_chat_message(uid, "user", f"msg {index}", "bot")
    history = await db.chat_history(uid, limit=1000)
    check("N5 история ограничена 300 сообщениями", len(history) == 300, str(len(history)))
    check("N5 обрезаются самые старые",
          history[-1]["content"] == "msg 399", history[-1]["content"])

    # ---- /clear
    removed = await db.clear_chat(uid)
    check("N5 очистка удалила сообщения", removed == 300, str(removed))
    check("N5 после очистки история пуста", await db.chat_history(uid) == [])
    check("N5 очистка не тронула другого пользователя",
          len(await db.chat_history(other)) == 1)

    # ---- длинный ввод обрезается
    msg2 = FakeMessage(uid)
    await reply_with_llm(msg2, db, "х" * 9000)
    stored = await db.chat_history(uid)
    check("N5 длинный ввод сохранён обрезанным",
          len(stored[0]["content"]) == 4000, str(len(stored[0]["content"])))

    await db.close()

    print()
    if FAILED:
        print(f"ПРОВАЛЕНО: {len(FAILED)} — {FAILED}")
        raise SystemExit(1)
    print("ALL CHAT MEMORY (N5) TESTS PASSED")


asyncio.run(main())
httpx.AsyncClient = _REAL
