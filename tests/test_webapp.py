import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path

TMP = Path(tempfile.mkdtemp())
os.environ["DATA_DIR"] = str(TMP)
os.environ["BOT_TOKEN"] = "123456:TEST_TOKEN_для_проверки"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx
from database import Database
from webapp.app import create_app
from webapp.auth import InitDataError, sign_init_data, verify_init_data

BOT_TOKEN = os.environ["BOT_TOKEN"]


def make_init_data(user_id: int, name: str = "Тест", age: int = 0) -> str:
    fields = {
        "auth_date": str(int(time.time()) - age),
        "query_id": "AAF",
        "user": json.dumps({"id": user_id, "first_name": name}, separators=(",", ":")),
    }
    return sign_init_data(fields, BOT_TOKEN)


def headers_for(user_id: int, age: int = 0) -> dict:
    return {"X-Init-Data": make_init_data(user_id, age=age)}


async def main():
    # ---- юнит-проверки подписи
    good = make_init_data(101)
    parsed = verify_init_data(good, BOT_TOKEN)
    assert parsed["user"]["id"] == 101
    print("signature verify OK")

    tampered = good.replace("101", "102")
    try:
        verify_init_data(tampered, BOT_TOKEN)
        raise AssertionError("подмена должна ломать подпись")
    except InitDataError as exc:
        print("tamper rejected:", exc)

    try:
        verify_init_data(good, "999:OTHER_TOKEN")
        raise AssertionError("чужой токен должен ломать подпись")
    except InitDataError as exc:
        print("wrong token rejected:", exc)

    try:
        verify_init_data(make_init_data(101, age=90000), BOT_TOKEN, ttl=3600)
        raise AssertionError("просроченный initData должен отвергаться")
    except InitDataError as exc:
        print("expired rejected:", exc)

    try:
        verify_init_data("", BOT_TOKEN)
        raise AssertionError("пустой initData")
    except InitDataError as exc:
        print("empty rejected:", exc)

    # ---- приложение
    db = Database(TMP / "t.db")
    await db.init()
    await db.touch_user(101, "tester", "Тест")
    await db.save_profile(
        101, goal="study", features=["notes", "materials"], style="brief",
        timezone="Europe/Moscow", digest_time="09:00", onboarded=True,
    )
    await db.touch_user(202, "other", "Другой")
    await db.save_profile(202, goal="work", style="formal", onboarded=True)

    app = create_app(db)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # без заголовка
        r = await client.get("/api/notes")
        assert r.status_code == 401, r.status_code
        print("401 without initData OK")

        # healthz без авторизации
        r = await client.get("/healthz")
        assert r.status_code == 200 and r.json()["ok"] is True
        print("healthz OK")

        # главная страница
        r = await client.get("/")
        assert r.status_code == 200
        body = r.text
        assert 'id="app"' in body and "telegram-web-app.js" in body
        assert "#0a0a0a" in (await client.get("/static/style.css")).text
        print("index + css served OK")

        # /api/me
        r = await client.get("/api/me", headers=headers_for(101))
        assert r.status_code == 200, r.text
        me = r.json()
        print("me:", me)
        assert me["user"]["id"] == 101 and me["profile"]["goal"] == "study"
        assert me["stats"]["notes"] == 0

        # заметки: создание/список/закрытие/удаление
        r = await client.post("/api/notes", json={"text": "купить молоко"}, headers=headers_for(101))
        assert r.status_code == 201, r.text
        note_id = r.json()["id"]
        r = await client.get("/api/notes", headers=headers_for(101))
        assert len(r.json()["items"]) == 1
        r = await client.post(f"/api/notes/{note_id}/done", headers=headers_for(101))
        assert r.status_code == 200
        r = await client.get("/api/notes", headers=headers_for(101))
        assert r.json()["items"][0]["done"] is True
        print("notes API OK")

        # изоляция: другой пользователь не видит и не может закрыть чужую заметку
        r = await client.get("/api/notes", headers=headers_for(202))
        assert r.json()["items"] == []
        r = await client.post(f"/api/notes/{note_id}/done", headers=headers_for(202))
        assert r.status_code == 404
        print("notes isolation OK")

        # пустой текст отклоняется
        r = await client.post("/api/notes", json={"text": ""}, headers=headers_for(101))
        assert r.status_code == 422
        print("validation OK")

        # напоминания
        r = await client.post(
            "/api/reminders",
            json={"text": "позвонить", "remind_at": "2030-01-01T10:00:00+00:00"},
            headers=headers_for(101),
        )
        assert r.status_code == 201, r.text
        reminder_id = r.json()["id"]
        r = await client.get("/api/reminders", headers=headers_for(101))
        assert len(r.json()["items"]) == 1
        r = await client.delete(f"/api/reminders/{reminder_id}", headers=headers_for(101))
        assert r.status_code == 200
        r = await client.post(
            "/api/reminders", json={"text": "x", "remind_at": "не дата"}, headers=headers_for(101)
        )
        assert r.status_code == 422
        print("reminders API OK")

        # материалы: создание через store_material (реальные чанки)
        long_text = (
            "SQLite поддерживает режим WAL и полнотекстовый поиск FTS5. "
            "Индексы ускоряют выборку. " * 20
        )
        r = await client.post(
            "/api/materials", json={"title": "sql.txt", "text": long_text}, headers=headers_for(101)
        )
        assert r.status_code == 201, r.text
        material_id = r.json()["id"]
        r = await client.get("/api/materials", headers=headers_for(101))
        items = r.json()["items"]
        assert len(items) == 1 and items[0]["chunks"] >= 1
        print("materials API OK, chunks:", items[0]["chunks"])
        chunks = await db.user_chunks(101)
        assert len(chunks) == items[0]["chunks"]
        r = await client.delete(f"/api/materials/{material_id}", headers=headers_for(101))
        assert r.status_code == 200
        assert await db.user_chunks(101) == []
        print("materials delete cascades OK")

        # стиль
        r = await client.post("/api/style", json={"style": "friendly"}, headers=headers_for(101))
        assert r.status_code == 200
        assert (await db.get_user(101))["style"] == "friendly"
        r = await client.post("/api/style", json={"style": "hacker"}, headers=headers_for(101))
        assert r.status_code == 422
        print("style API OK")

        # привычки
        habit_id = await db.add_habit(101, "зарядка")
        r = await client.get("/api/habits", headers=headers_for(101))
        assert len(r.json()["items"]) == 1 and r.json()["items"][0]["done_today"] is False
        r = await client.post(f"/api/habits/{habit_id}/toggle", headers=headers_for(101))
        assert r.json()["done_today"] is True
        r = await client.get("/api/habits", headers=headers_for(101))
        assert r.json()["items"][0]["done_today"] is True
        assert r.json()["items"][0]["streak"] == 1
        print("habits API OK")

        # не онбордированный пользователь
        r = await client.get("/api/me", headers=headers_for(999))
        assert r.status_code == 403
        print("unonboarded 403 OK")

        # ---- чат Mini App (N3)
        import llm_client as llm_mod

        def fake_transport(request):
            return httpx.Response(
                200, json={"choices": [{"message": {"content": "Привет из LLM"}}]}
            )

        real_client = httpx.AsyncClient
        httpx.AsyncClient = lambda *a, **kw: real_client(
            *a, transport=httpx.MockTransport(fake_transport), **kw
        )
        object.__setattr__(llm_mod.settings, "openrouter_api_key", "test-key")
        try:
            r = await client.get("/api/chat", headers=headers_for(101))
            assert r.status_code == 200 and r.json()["items"] == [], r.text
            print("chat history empty OK")

            r = await client.post(
                "/api/chat", headers=headers_for(101), json={"text": "Как дела?"}
            )
            assert r.status_code == 201, r.text
            payload = r.json()
            assert payload["ok"] is True, payload
            assert payload["reply"]["content"] == "Привет из LLM"
            print("chat send OK")

            r = await client.get("/api/chat", headers=headers_for(101))
            items = r.json()["items"]
            assert [i["role"] for i in items] == ["user", "assistant"], items
            assert items[0]["source"] == "webapp"
            print("chat history persisted OK")

            r = await client.post(
                "/api/chat", headers=headers_for(101), json={"text": ""}
            )
            assert r.status_code == 422, r.status_code
            print("chat empty rejected OK")

            r = await client.delete("/api/chat", headers=headers_for(101))
            assert r.json()["removed"] >= 2
            r = await client.get("/api/chat", headers=headers_for(101))
            assert r.json()["items"] == []
            print("chat clear OK")
        finally:
            httpx.AsyncClient = real_client

    await db.close()
    print("ALL WEBAPP TESTS PASSED")


asyncio.run(main())
