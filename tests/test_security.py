"""Пункт N9: безопасность — изоляция данных, защита API, отсутствие секретов.

Проверяем не «есть ли проверка в коде», а что она реально отказывает:
чужой initData, просроченный initData, подделанный user_id.
"""

import asyncio
import re
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

from config import settings
from database import Database
from webapp.app import create_app
from webapp.auth import InitDataError, sign_init_data, verify_init_data

FAILED: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    if ok:
        print(f"PASS  {name}")
    else:
        FAILED.append(name)
        print(f"FAIL  {name}  {detail}")


TOKEN = "123456:TEST-TOKEN"
ROOT = Path(__file__).resolve().parent.parent


def make_init(user_id: int, token: str = TOKEN, age: int = 0) -> str:
    return sign_init_data(
        {
            "auth_date": str(int(time.time()) - age),
            "query_id": "AAE",
            "user": '{"id":%d,"first_name":"U"}' % user_id,
        },
        token,
    )


async def seed(db: Database, user_id: int, note: str) -> None:
    await db.touch_user(user_id, "u", "U")
    await db.save_profile(
        user_id, goal="study", style="brief", features=["notes"], onboarded=True
    )
    await db.add_note(user_id, note)


async def main() -> None:
    object.__setattr__(settings, "bot_token", TOKEN)

    # ---- подпись
    good = make_init(1001)
    parsed = verify_init_data(good, TOKEN)
    check("N9 корректная подпись принимается", "user" in parsed, str(parsed))

    for label, bad in (
        ("чужой токен", make_init(1001, token="999:ДРУГОЙ")),
        ("просроченный", make_init(1001, age=10 * 24 * 3600)),
        ("без hash", "auth_date=1&user=%7B%22id%22%3A1%7D"),
        ("пустой", ""),
    ):
        try:
            verify_init_data(bad, TOKEN)
            check(f"N9 отклоняется initData: {label}", False, "принято!")
        except InitDataError:
            check(f"N9 отклоняется initData: {label}", True)

    # подмена user_id без пересчёта подписи
    forged = good.replace("%7B%22id%22%3A1001", "%7B%22id%22%3A2002")
    try:
        verify_init_data(forged, TOKEN)
        check("N9 подмена user_id отклоняется", False, "принято!")
    except InitDataError:
        check("N9 подмена user_id отклоняется", True)

    try:
        verify_init_data(good, "")
        check("N9 пустой BOT_TOKEN отклоняется", False)
    except InitDataError:
        check("N9 пустой BOT_TOKEN отклоняется", True)

    # ---- API
    db = Database(str(Path(tempfile.mkdtemp()) / "s.db"))
    await db.init()
    try:
        await run_api_checks(db)
    finally:
        # без close поток aiosqlite держит процесс и тест «висит»
        await db.close()


async def run_api_checks(db: Database) -> None:
    await seed(db, 1001, "заметка первого")
    await seed(db, 2002, "заметка второго")

    app = create_app(db)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        r = await client.get("/api/notes")
        check("N9 API без заголовка = 401", r.status_code == 401, str(r.status_code))

        r = await client.get("/api/notes", headers={"X-Init-Data": "garbage-not-signed"})
        check("N9 API с мусором = 401", r.status_code == 401, str(r.status_code))

        r = await client.get("/api/notes", headers={"X-Init-Data": make_init(1001, token="9:X")})
        check("N9 API с чужим токеном = 401", r.status_code == 401, str(r.status_code))

        r = await client.get("/api/notes", headers={"X-Init-Data": make_init(1001, age=999999)})
        check("N9 API с просроченным = 401", r.status_code == 401, str(r.status_code))

        r = await client.get("/api/notes", headers={"X-Init-Data": make_init(1001)})
        check("N9 свой пользователь получает 200", r.status_code == 200, str(r.status_code))
        body = r.text
        check("N9 видит свою заметку", "заметка первого" in body, body[:120])
        check("N9 не видит чужую заметку", "заметка второго" not in body, body[:120])

        r = await client.get("/api/notes", headers={"X-Init-Data": make_init(2002)})
        check("N9 второй видит только своё",
              "заметка второго" in r.text and "заметка первого" not in r.text,
              r.text[:120])

        # чужую заметку нельзя удалить по id
        first_notes = await db.list_notes(1001)
        note_id = first_notes[0]["id"]
        r = await client.request(
            "DELETE",
            f"/api/notes/{note_id}",
            headers={"X-Init-Data": make_init(2002)},
        )
        check("N9 чужую заметку удалить нельзя", r.status_code == 404, str(r.status_code))
        check("N9 заметка уцелела", len(await db.list_notes(1001)) == 1)

        # неизвестный пользователь без онбординга
        r = await client.get("/api/notes", headers={"X-Init-Data": make_init(7777)})
        check("N9 без онбординга = 403", r.status_code == 403, str(r.status_code))

    # ---- секреты только из окружения
    offenders = []
    secret_re = re.compile(r"(sk-[A-Za-z0-9]{16,}|gsk_[A-Za-z0-9]{16,}|\d{8,10}:[A-Za-z0-9_-]{30,})")
    for path in ROOT.rglob("*.py"):
        if ".venv" in path.parts or "tests" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for match in secret_re.finditer(text):
            offenders.append(f"{path.name}: {match.group(0)[:12]}…")
    check("N9 в коде нет захардкоженных токенов", not offenders, str(offenders))

    from config import _env

    check("N9 конфиг читает токен из окружения",
          "os.environ" in Path(ROOT / "config.py").read_text(), "нет os.environ")
    check("N9 _env без значения возвращает пустую строку, а не падает",
          _env("НЕТ_ТАКОЙ_ПЕРЕМЕННОЙ_XYZ") == "")

    # ---- .env не в git
    gitignore = (ROOT / ".gitignore").read_text() if (ROOT / ".gitignore").exists() else ""
    check("N9 .env в .gitignore", ".env" in gitignore, gitignore[:60])

    print()
    if FAILED:
        print(f"ПРОВАЛЕНО: {len(FAILED)} — {FAILED}")
        raise SystemExit(1)
    print("ALL SECURITY (N9) TESTS PASSED")


asyncio.run(main())
