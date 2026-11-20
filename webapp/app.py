"""FastAPI-бэкенд Mini App «Миты».

Все API-методы требуют заголовок X-Init-Data с подписанным initData:
пользователь опознаётся только по подписи, id из тела запроса не принимается.
"""

import logging
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from config import settings
from database import Database, features_of
from webapp.auth import (
    InitDataError,
    WebAppUser,
    user_from_init_data,
    verify_init_data,
)

log = logging.getLogger("mita.webapp")

BASE = Path(__file__).resolve().parent
STYLES = {"brief", "detailed", "friendly", "formal"}


class NoteIn(BaseModel):
    text: str = Field(min_length=1, max_length=3500)


class ReminderIn(BaseModel):
    text: str = Field(min_length=1, max_length=500)
    remind_at: str = Field(description="ISO-8601 в UTC")


class MaterialIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=20, max_length=200_000)
    kind: str = "text"


class StyleIn(BaseModel):
    style: str


class HabitIn(BaseModel):
    title: str = Field(min_length=1, max_length=80)


def create_app(db: Database) -> FastAPI:
    app = FastAPI(title="Мита Mini App", docs_url=None, redoc_url=None)
    app.state.db = db

    app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
    templates = Jinja2Templates(directory=str(BASE / "templates"))

    async def current_user(
        x_init_data: Annotated[str | None, Header(alias="X-Init-Data")] = None,
    ) -> WebAppUser:
        if not x_init_data:
            raise HTTPException(status_code=401, detail="Нет X-Init-Data")
        try:
            data = verify_init_data(x_init_data, settings.bot_token)
            return user_from_init_data(data)
        except InitDataError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc

    UserDep = Annotated[WebAppUser, Depends(current_user)]

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request, "index.html", {"bot_username": settings.bot_username}
        )

    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:
        return {"ok": True, "app": "mita"}

    @app.get("/api/me")
    async def me(user: UserDep) -> dict[str, Any]:
        row = await db.get_user(user.id)
        if row is None:
            raise HTTPException(status_code=403, detail="Сначала пройди онбординг в боте")
        return {
            "user": {"id": user.id, "name": user.display_name},
            "profile": {
                "goal": row["goal"],
                "style": row["style"],
                "timezone": row["timezone"],
                "digest_time": row["digest_time"],
                "features": features_of(row),
            },
            "stats": await db.stats(user.id),
        }

    @app.get("/api/notes")
    async def notes(user: UserDep) -> dict[str, Any]:
        rows = await db.list_notes(user.id, include_done=True)
        return {
            "items": [
                {
                    "id": row["id"],
                    "text": row["text"],
                    "done": bool(row["done"]),
                    "created_at": row["created_at"],
                }
                for row in rows
            ]
        }

    @app.post("/api/notes", status_code=201)
    async def add_note(payload: NoteIn, user: UserDep) -> dict[str, Any]:
        note_id = await db.add_note(user.id, payload.text)
        return {"id": note_id, "text": payload.text.strip(), "done": False}

    @app.post("/api/notes/{note_id}/done")
    async def done_note(note_id: int, user: UserDep) -> dict[str, Any]:
        ok = await db.complete_note(user.id, note_id)
        if not ok:
            raise HTTPException(status_code=404, detail="Заметка не найдена")
        return {"ok": True}

    @app.delete("/api/notes/{note_id}")
    async def delete_note(note_id: int, user: UserDep) -> dict[str, Any]:
        ok = await db.delete_note(user.id, note_id)
        if not ok:
            raise HTTPException(status_code=404, detail="Заметка не найдена")
        return {"ok": True}

    @app.get("/api/reminders")
    async def reminders(user: UserDep) -> dict[str, Any]:
        rows = await db.list_reminders(user.id, limit=200)
        return {
            "items": [
                {
                    "id": row["id"],
                    "text": row["text"],
                    "remind_at": row["remind_at"],
                    "sent": bool(row["sent"]),
                }
                for row in rows
            ]
        }

    @app.post("/api/reminders", status_code=201)
    async def add_reminder(payload: ReminderIn, user: UserDep) -> dict[str, Any]:
        try:
            when = datetime.fromisoformat(payload.remind_at)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="remind_at не ISO-8601") from exc
        reminder_id = await db.add_reminder(user.id, payload.text, when.isoformat())
        return {"id": reminder_id, "text": payload.text, "remind_at": when.isoformat()}

    @app.delete("/api/reminders/{reminder_id}")
    async def delete_reminder(reminder_id: int, user: UserDep) -> dict[str, Any]:
        ok = await db.delete_reminder(user.id, reminder_id)
        if not ok:
            raise HTTPException(status_code=404, detail="Напоминание не найдено")
        return {"ok": True}

    @app.get("/api/materials")
    async def materials(user: UserDep) -> dict[str, Any]:
        rows = await db.list_materials(user.id)
        return {
            "items": [
                {
                    "id": row["id"],
                    "title": row["title"],
                    "kind": row["kind"],
                    "chars": row["chars"],
                    "chunks": row["chunks"],
                    "created_at": row["created_at"],
                }
                for row in rows
            ]
        }

    @app.post("/api/materials", status_code=201)
    async def add_material(payload: MaterialIn, user: UserDep) -> dict[str, Any]:
        from materials import store_material

        material_id, error = await store_material(
            db, user.id, payload.title, payload.kind, "webapp", payload.text
        )
        if material_id is None:
            raise HTTPException(status_code=422, detail=error or "Не удалось сохранить")
        return {"id": material_id, "title": payload.title}

    @app.delete("/api/materials/{material_id}")
    async def delete_material(material_id: int, user: UserDep) -> dict[str, Any]:
        ok = await db.delete_material(user.id, material_id)
        if not ok:
            raise HTTPException(status_code=404, detail="Материал не найден")
        return {"ok": True}

    @app.get("/api/habits")
    async def habits(user: UserDep) -> dict[str, Any]:
        from habits import local_tz, streak_days, today_local, week_percent
        from datetime import timedelta

        row = await db.get_user(user.id)
        tz = local_tz(row)
        today = today_local(tz)
        done = await db.habit_done_today(user.id, today.isoformat())
        items = []
        for habit in await db.list_habits(user.id):
            since = (today - timedelta(days=60)).isoformat()
            days = set(await db.habit_days(user.id, habit["id"], since))
            items.append(
                {
                    "id": habit["id"],
                    "title": habit["title"],
                    "done_today": habit["id"] in done,
                    "streak": streak_days(days, today),
                    "week_percent": week_percent(days, today),
                }
            )
        return {"items": items}

    @app.post("/api/habits/{habit_id}/toggle")
    async def toggle_habit(habit_id: int, user: UserDep) -> dict[str, Any]:
        from habits import local_tz, today_local

        row = await db.get_user(user.id)
        day = today_local(local_tz(row)).isoformat()
        done = await db.habit_done_today(user.id, day)
        if habit_id in done:
            await db.unlog_habit(user.id, habit_id, day)
            return {"done_today": False}
        ok = await db.log_habit(user.id, habit_id, day)
        if not ok:
            raise HTTPException(status_code=404, detail="Привычка не найдена")
        return {"done_today": True}

    @app.post("/api/style")
    async def set_style(payload: StyleIn, user: UserDep) -> dict[str, Any]:
        if payload.style not in STYLES:
            raise HTTPException(status_code=422, detail="Неизвестный стиль")
        await db.save_profile(user.id, style=payload.style)
        return {"style": payload.style}

    @app.exception_handler(Exception)
    async def on_error(request: Request, exc: Exception) -> JSONResponse:
        log.exception("Ошибка webapp: %s", exc)
        return JSONResponse(status_code=500, content={"detail": "Внутренняя ошибка"})

    return app


__all__ = ["create_app"]
