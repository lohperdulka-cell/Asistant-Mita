"""SQLite-слой «Миты»: схема, миграции и типовые запросы.

Используется aiosqlite. Все запросы параметризованы — конкатенации SQL нет.
Схема описана версиями в MIGRATIONS; применённая версия хранится в
PRAGMA user_version, поэтому повторный запуск безопасен.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

import aiosqlite

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1

MIGRATIONS: dict[int, list[str]] = {
    1: [
        """
        CREATE TABLE IF NOT EXISTS users (
            user_id      INTEGER PRIMARY KEY,
            username     TEXT,
            first_name   TEXT,
            goal         TEXT,
            features     TEXT NOT NULL DEFAULT '[]',
            style        TEXT NOT NULL DEFAULT 'brief',
            timezone     TEXT NOT NULL DEFAULT 'Europe/Moscow',
            digest_time  TEXT NOT NULL DEFAULT '09:00',
            onboarded    INTEGER NOT NULL DEFAULT 0,
            created_at   TEXT NOT NULL,
            updated_at   TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS notes (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL,
            text       TEXT NOT NULL,
            done       INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            done_at    TEXT,
            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_notes_user ON notes(user_id, done)",
        """
        CREATE TABLE IF NOT EXISTS reminders (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL,
            text       TEXT NOT NULL,
            remind_at  TEXT NOT NULL,
            sent       INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_reminders_due ON reminders(sent, remind_at)",
        """
        CREATE TABLE IF NOT EXISTS materials (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL,
            title      TEXT NOT NULL,
            kind       TEXT NOT NULL,
            source     TEXT,
            chars      INTEGER NOT NULL DEFAULT 0,
            chunks     INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_materials_user ON materials(user_id)",
        """
        CREATE TABLE IF NOT EXISTS chunks (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            material_id INTEGER NOT NULL,
            user_id     INTEGER NOT NULL,
            ord         INTEGER NOT NULL,
            text        TEXT NOT NULL,
            tokens      TEXT,
            FOREIGN KEY (material_id) REFERENCES materials(id) ON DELETE CASCADE
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_chunks_user ON chunks(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_chunks_material ON chunks(material_id)",
        """
        CREATE TABLE IF NOT EXISTS habits (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL,
            title      TEXT NOT NULL,
            active     INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
        )
        """,
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_habits_user_title ON habits(user_id, title)",
        """
        CREATE TABLE IF NOT EXISTS habit_logs (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            habit_id INTEGER NOT NULL,
            user_id  INTEGER NOT NULL,
            day      TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE (habit_id, day),
            FOREIGN KEY (habit_id) REFERENCES habits(id) ON DELETE CASCADE
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_habit_logs_user ON habit_logs(user_id, day)",
        """
        CREATE TABLE IF NOT EXISTS settings (
            user_id INTEGER NOT NULL,
            key     TEXT NOT NULL,
            value   TEXT,
            PRIMARY KEY (user_id, key)
        )
        """,
    ]
}


def utcnow() -> str:
    """Единый формат меток времени: ISO-8601 в UTC с 'Z'."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class Database:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._conn: aiosqlite.Connection | None = None

    # ------------------------------------------------------------------ infra

    async def connect(self) -> aiosqlite.Connection:
        if self._conn is not None:
            return self._conn
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = await aiosqlite.connect(self.path)
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA foreign_keys=ON")
        await conn.execute("PRAGMA busy_timeout=5000")
        self._conn = conn
        return conn

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database.connect() не был вызван")
        return self._conn

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def init(self) -> None:
        conn = await self.connect()
        cursor = await conn.execute("PRAGMA user_version")
        row = await cursor.fetchone()
        current = int(row[0]) if row else 0
        for version in sorted(MIGRATIONS):
            if version <= current:
                continue
            log.info("Применяю миграцию БД v%s", version)
            for statement in MIGRATIONS[version]:
                await conn.execute(statement)
            await conn.execute(f"PRAGMA user_version={version}")
        await conn.commit()

    async def execute(self, sql: str, params: Sequence[Any] = ()) -> None:
        await self.conn.execute(sql, params)
        await self.conn.commit()

    async def fetchone(self, sql: str, params: Sequence[Any] = ()) -> aiosqlite.Row | None:
        cursor = await self.conn.execute(sql, params)
        return await cursor.fetchone()

    async def fetchall(self, sql: str, params: Sequence[Any] = ()) -> list[aiosqlite.Row]:
        cursor = await self.conn.execute(sql, params)
        return list(await cursor.fetchall())

    async def insert(self, sql: str, params: Sequence[Any] = ()) -> int:
        cursor = await self.conn.execute(sql, params)
        await self.conn.commit()
        return int(cursor.lastrowid or 0)

    # ------------------------------------------------------------------ users

    async def get_user(self, user_id: int) -> aiosqlite.Row | None:
        return await self.fetchone("SELECT * FROM users WHERE user_id = ?", (user_id,))

    async def touch_user(
        self, user_id: int, username: str | None = None, first_name: str | None = None
    ) -> None:
        """Создаёт пользователя-заготовку или обновляет его публичные поля."""
        now = utcnow()
        await self.conn.execute(
            """
            INSERT INTO users (user_id, username, first_name, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username = COALESCE(excluded.username, users.username),
                first_name = COALESCE(excluded.first_name, users.first_name),
                updated_at = excluded.updated_at
            """,
            (user_id, username, first_name, now, now),
        )
        await self.conn.commit()

    async def save_profile(self, user_id: int, **fields: Any) -> None:
        allowed = {"goal", "features", "style", "timezone", "digest_time", "onboarded"}
        payload = {k: v for k, v in fields.items() if k in allowed}
        if not payload:
            return
        if "features" in payload and not isinstance(payload["features"], str):
            payload["features"] = json.dumps(payload["features"], ensure_ascii=False)
        if "onboarded" in payload:
            payload["onboarded"] = 1 if payload["onboarded"] else 0
        payload["updated_at"] = utcnow()
        columns = ", ".join(f"{key} = ?" for key in payload)
        await self.execute(
            f"UPDATE users SET {columns} WHERE user_id = ?",
            (*payload.values(), user_id),
        )

    async def is_onboarded(self, user_id: int) -> bool:
        row = await self.get_user(user_id)
        return bool(row and row["onboarded"])

    async def all_onboarded_users(self) -> list[aiosqlite.Row]:
        return await self.fetchall("SELECT * FROM users WHERE onboarded = 1")

    # ------------------------------------------------------------------ notes

    async def add_note(self, user_id: int, text: str) -> int:
        return await self.insert(
            "INSERT INTO notes (user_id, text, created_at) VALUES (?, ?, ?)",
            (user_id, text.strip(), utcnow()),
        )

    async def list_notes(self, user_id: int, include_done: bool = False) -> list[aiosqlite.Row]:
        if include_done:
            sql = "SELECT * FROM notes WHERE user_id = ? ORDER BY id DESC"
            return await self.fetchall(sql, (user_id,))
        sql = "SELECT * FROM notes WHERE user_id = ? AND done = 0 ORDER BY id DESC"
        return await self.fetchall(sql, (user_id,))

    async def complete_note(self, user_id: int, note_id: int) -> bool:
        row = await self.fetchone(
            "SELECT id FROM notes WHERE id = ? AND user_id = ?", (note_id, user_id)
        )
        if row is None:
            return False
        await self.execute(
            "UPDATE notes SET done = 1, done_at = ? WHERE id = ?", (utcnow(), note_id)
        )
        return True

    async def delete_note(self, user_id: int, note_id: int) -> bool:
        row = await self.fetchone(
            "SELECT id FROM notes WHERE id = ? AND user_id = ?", (note_id, user_id)
        )
        if row is None:
            return False
        await self.execute("DELETE FROM notes WHERE id = ?", (note_id,))
        return True

    # -------------------------------------------------------------- reminders

    async def add_reminder(self, user_id: int, text: str, remind_at: str) -> int:
        return await self.insert(
            "INSERT INTO reminders (user_id, text, remind_at, created_at) VALUES (?, ?, ?, ?)",
            (user_id, text.strip(), remind_at, utcnow()),
        )

    async def due_reminders(self, now_iso: str) -> list[aiosqlite.Row]:
        return await self.fetchall(
            "SELECT * FROM reminders WHERE sent = 0 AND remind_at <= ? ORDER BY remind_at",
            (now_iso,),
        )

    async def mark_reminder_sent(self, reminder_id: int) -> None:
        await self.execute("UPDATE reminders SET sent = 1 WHERE id = ?", (reminder_id,))

    async def reminders_for_day(self, user_id: int, day_prefix: str) -> list[aiosqlite.Row]:
        """Напоминания пользователя на конкретную дату (по локальному ISO-префиксу)."""
        return await self.fetchall(
            """
            SELECT * FROM reminders
            WHERE user_id = ? AND remind_at LIKE ? AND sent = 0
            ORDER BY remind_at
            """,
            (user_id, f"{day_prefix}%"),
        )

    async def list_reminders(self, user_id: int, limit: int = 50) -> list[aiosqlite.Row]:
        return await self.fetchall(
            """
            SELECT * FROM reminders
            WHERE user_id = ? AND sent = 0
            ORDER BY remind_at LIMIT ?
            """,
            (user_id, limit),
        )

    async def delete_reminder(self, user_id: int, reminder_id: int) -> bool:
        row = await self.fetchone(
            "SELECT id FROM reminders WHERE id = ? AND user_id = ?", (reminder_id, user_id)
        )
        if row is None:
            return False
        await self.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))
        return True

    # -------------------------------------------------------------- materials

    async def add_material(
        self, user_id: int, title: str, kind: str, source: str | None, chars: int, chunks: int
    ) -> int:
        return await self.insert(
            """
            INSERT INTO materials (user_id, title, kind, source, chars, chunks, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (user_id, title[:200], kind, source, chars, chunks, utcnow()),
        )

    async def list_materials(self, user_id: int) -> list[aiosqlite.Row]:
        return await self.fetchall(
            "SELECT * FROM materials WHERE user_id = ? ORDER BY id DESC", (user_id,)
        )

    async def get_material(self, user_id: int, material_id: int) -> aiosqlite.Row | None:
        return await self.fetchone(
            "SELECT * FROM materials WHERE id = ? AND user_id = ?", (material_id, user_id)
        )

    async def delete_material(self, user_id: int, material_id: int) -> bool:
        row = await self.get_material(user_id, material_id)
        if row is None:
            return False
        await self.execute("DELETE FROM chunks WHERE material_id = ?", (material_id,))
        await self.execute("DELETE FROM materials WHERE id = ?", (material_id,))
        return True

    async def add_chunks(
        self, user_id: int, material_id: int, chunks: Iterable[tuple[int, str, list[float] | None]]
    ) -> None:
        rows = [
            (
                material_id,
                user_id,
                ord_,
                text,
                json.dumps(tokens) if tokens else None,
            )
            for ord_, text, tokens in chunks
        ]
        if not rows:
            return
        await self.conn.executemany(
            "INSERT INTO chunks (material_id, user_id, ord, text, tokens) VALUES (?, ?, ?, ?, ?)",
            rows,
        )
        await self.conn.commit()

    async def user_chunks(self, user_id: int, limit: int = 2000) -> list[aiosqlite.Row]:
        return await self.fetchall(
            """
            SELECT c.id, c.material_id, c.ord, c.text, c.tokens, m.title
            FROM chunks c JOIN materials m ON m.id = c.material_id
            WHERE c.user_id = ?
            ORDER BY c.id
            LIMIT ?
            """,
            (user_id, limit),
        )

    # ----------------------------------------------------------------- habits

    async def add_habit(self, user_id: int, title: str) -> int | None:
        title = title.strip()
        if not title:
            return None
        try:
            return await self.insert(
                "INSERT INTO habits (user_id, title, created_at) VALUES (?, ?, ?)",
                (user_id, title[:80], utcnow()),
            )
        except aiosqlite.IntegrityError:
            return None

    async def list_habits(self, user_id: int, only_active: bool = True) -> list[aiosqlite.Row]:
        if only_active:
            return await self.fetchall(
                "SELECT * FROM habits WHERE user_id = ? AND active = 1 ORDER BY id",
                (user_id,),
            )
        return await self.fetchall(
            "SELECT * FROM habits WHERE user_id = ? ORDER BY id", (user_id,)
        )

    async def archive_habit(self, user_id: int, habit_id: int) -> bool:
        row = await self.fetchone(
            "SELECT id FROM habits WHERE id = ? AND user_id = ?", (habit_id, user_id)
        )
        if row is None:
            return False
        await self.execute("UPDATE habits SET active = 0 WHERE id = ?", (habit_id,))
        return True

    async def log_habit(self, user_id: int, habit_id: int, day: str) -> bool:
        row = await self.fetchone(
            "SELECT id FROM habits WHERE id = ? AND user_id = ?", (habit_id, user_id)
        )
        if row is None:
            return False
        cursor = await self.conn.execute(
            """
            INSERT OR IGNORE INTO habit_logs (habit_id, user_id, day, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (habit_id, user_id, day, utcnow()),
        )
        await self.conn.commit()
        return bool(cursor.rowcount)

    async def unlog_habit(self, user_id: int, habit_id: int, day: str) -> None:
        await self.execute(
            "DELETE FROM habit_logs WHERE habit_id = ? AND user_id = ? AND day = ?",
            (habit_id, user_id, day),
        )

    async def habit_days(self, user_id: int, habit_id: int, since: str) -> list[str]:
        rows = await self.fetchall(
            """
            SELECT day FROM habit_logs
            WHERE habit_id = ? AND user_id = ? AND day >= ?
            ORDER BY day
            """,
            (habit_id, user_id, since),
        )
        return [row["day"] for row in rows]

    async def habit_done_today(self, user_id: int, day: str) -> set[int]:
        rows = await self.fetchall(
            "SELECT habit_id FROM habit_logs WHERE user_id = ? AND day = ?", (user_id, day)
        )
        return {int(row["habit_id"]) for row in rows}

    # --------------------------------------------------------------- settings

    async def set_setting(self, user_id: int, key: str, value: str) -> None:
        await self.execute(
            """
            INSERT INTO settings (user_id, key, value) VALUES (?, ?, ?)
            ON CONFLICT(user_id, key) DO UPDATE SET value = excluded.value
            """,
            (user_id, key, value),
        )

    async def get_setting(self, user_id: int, key: str, default: str | None = None) -> str | None:
        row = await self.fetchone(
            "SELECT value FROM settings WHERE user_id = ? AND key = ?", (user_id, key)
        )
        return row["value"] if row else default

    # ------------------------------------------------------------- статистика

    async def stats(self, user_id: int) -> dict[str, int]:
        async def count(sql: str, *params: Any) -> int:
            row = await self.fetchone(sql, params)
            return int(row[0]) if row and row[0] is not None else 0

        return {
            "notes": await count("SELECT COUNT(*) FROM notes WHERE user_id = ? AND done = 0", user_id),
            "reminders": await count(
                "SELECT COUNT(*) FROM reminders WHERE user_id = ? AND sent = 0", user_id
            ),
            "materials": await count("SELECT COUNT(*) FROM materials WHERE user_id = ?", user_id),
            "habits": await count(
                "SELECT COUNT(*) FROM habits WHERE user_id = ? AND active = 1", user_id
            ),
        }


def features_of(row: aiosqlite.Row | dict[str, Any] | None) -> list[str]:
    """Достаёт список функций из строки users (хранится как JSON)."""
    if row is None:
        return []
    raw = row["features"] if not isinstance(row, dict) else row.get("features")
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return [str(item) for item in parsed] if isinstance(parsed, list) else []


__all__ = ["Database", "features_of", "utcnow", "SCHEMA_VERSION"]
