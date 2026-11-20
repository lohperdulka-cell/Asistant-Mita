"""Пункт N6: inline-режим — профиль, приватность, офлайн-fallback.

Сетевой Telegram не нужен: собираем InlineQuery вручную и смотрим,
что бот положил в query.answer().
"""

import asyncio
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

SENT: list[dict] = []


def _handler(request: httpx.Request) -> httpx.Response:
    SENT.append(json.loads(request.content.decode()))
    return httpx.Response(
        200, json={"choices": [{"message": {"content": "Инлайн-ответ"}}]}
    )


_REAL_CLIENT = httpx.AsyncClient
httpx.AsyncClient = lambda *a, **kw: _REAL_CLIENT(
    *a, transport=httpx.MockTransport(_handler), **kw
)

from config import settings
from database import Database
from inline_mode import PRIVATE_HINT, inline_handler, profile_summary
from llm_client import llm

FAILED: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    if ok:
        print(f"PASS  {name}")
    else:
        FAILED.append(name)
        print(f"FAIL  {name}  {detail}")


class FakeQuery:
    def __init__(self, text: str, user_id: int = 601) -> None:
        self.query = text
        self.from_user = type("U", (), {"id": user_id})()
        self.results: list = []
        self.kw: dict = {}

    async def answer(self, results, **kw) -> bool:
        self.results = list(results)
        self.kw = kw
        return True


def bodies(results) -> str:
    parts = []
    for item in results:
        content = getattr(item, "input_message_content", None)
        parts.append(getattr(content, "message_text", "") or "")
        parts.append(getattr(item, "title", "") or "")
        parts.append(getattr(item, "description", "") or "")
    return " ".join(parts)


async def main() -> None:
    object.__setattr__(settings, "openrouter_api_key", "k")
    object.__setattr__(settings, "groq_api_key", "")

    db = Database(str(Path(tempfile.mkdtemp()) / "i.db"))
    await db.init()
    uid = 601
    await db.touch_user(uid, "u", "U")
    await db.save_profile(
        uid, goal="study", style="friendly", features=["notes", "materials"]
    )

    # ---- профиль читаемый
    row = await db.get_user(uid)
    summary = profile_summary(row)
    check("N6 профиль содержит цель словами", "Учёба" in summary, summary)
    check("N6 профиль без кодов", "study" not in summary, summary)
    check("N6 профиль содержит функции словами", "Заметки" in summary, summary)
    check("N6 профиль пуст без пользователя", profile_summary(None) == "")

    # ---- пустой запрос
    query = FakeQuery("")
    await inline_handler(query, db)
    check("N6 пустой запрос даёт результат", len(query.results) >= 1, str(len(query.results)))
    check("N6 ответ персонален", query.kw.get("is_personal") is True, str(query.kw))

    # ---- обычный запрос
    SENT.clear()
    query = FakeQuery("план на день")
    await inline_handler(query, db)
    check("N6 LLM вызвана один раз", len(SENT) == 1, str(len(SENT)))
    system = SENT[0]["messages"][0]["content"]
    check("N6 LLM получила цель профиля", "Учёба" in system, system[:120])
    check("N6 LLM получила стиль из профиля", "дружелюбн" in system.lower(), system[:200])
    titles = [item.title for item in query.results]
    check("N6 есть ответ Миты", "Ответ Миты" in titles, str(titles))
    check("N6 ответ содержит текст LLM",
          "Инлайн-ответ" in bodies(query.results), bodies(query.results)[:80])

    # ---- приватные данные не утекают
    await db.add_note(uid, "секретный пароль 1234")
    await db.add_material(uid, "файл.pdf", "pdf", "файл.pdf", 100, 1)
    SENT.clear()
    query = FakeQuery("мои заметки")
    await inline_handler(query, db)
    blob = bodies(query.results)
    check("N6 заметки не утекли", "пароль" not in blob, blob[:100])
    check("N6 материалы не утекли", "файл.pdf" not in blob, blob[:100])
    check("N6 подсказка про личный чат есть",
          PRIVATE_HINT in blob or "личном чате" in blob, blob[:100])

    # ---- LLM недоступна
    object.__setattr__(settings, "openrouter_api_key", "")
    query = FakeQuery("привет")
    await inline_handler(query, db)
    body = bodies(query.results)
    check("N6 офлайн-fallback отвечает", bool(query.results), str(len(query.results)))
    check("N6 офлайн сообщает о недоступности",
          "LLM" in body or "недоступна" in body.lower(), body[:100])
    check("N6 офлайн не теряет запрос", "привет" in body, body[:100])

    # ---- пользователь без онбординга
    query = FakeQuery("тест", user_id=999_999)
    await inline_handler(query, db)
    check("N6 inline работает без онбординга", len(query.results) >= 1, str(len(query.results)))

    # ---- пользовательский стиль доходит до LLM
    object.__setattr__(settings, "openrouter_api_key", "k")
    await db.add_user_style(uid, "custom_pirate", "Пират", "Говори как пират")
    await db.set_user_prefs(uid, style="custom_pirate")
    SENT.clear()
    query = FakeQuery("расскажи про море")
    await inline_handler(query, db)
    system = SENT[0]["messages"][0]["content"]
    check("N6 свой стиль доходит до LLM", "пират" in system.lower(), system[:200])

    await db.close()

    print()
    if FAILED:
        print(f"ПРОВАЛЕНО: {len(FAILED)} — {FAILED}")
        raise SystemExit(1)
    print("ALL INLINE (N6) TESTS PASSED")


asyncio.run(main())
httpx.AsyncClient = _REAL_CLIENT
