"""Планировщик: отправка напоминаний каждую минуту и ежедневные сводки.

APScheduler работает в том же процессе, что и polling: на Termux это
дешевле, чем отдельный сервис. Джобы защищены от исключений, чтобы одна
ошибка не останавливала остальные.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from config import settings
from database import Database
from llm_client import llm

log = logging.getLogger(__name__)


class ReminderScheduler:
    def __init__(self, bot: Bot, db: Database) -> None:
        self.bot = bot
        self.db = db
        self.scheduler = AsyncIOScheduler(timezone="UTC")

    def start(self) -> None:
        self.scheduler.add_job(
            self.send_due_reminders,
            IntervalTrigger(minutes=1),
            id="due_reminders",
            max_instances=1,
            coalesce=True,
            replace_existing=True,
        )
        self.scheduler.add_job(
            self.send_digests,
            IntervalTrigger(minutes=1),
            id="digests",
            max_instances=1,
            coalesce=True,
            replace_existing=True,
        )
        self.scheduler.start()
        log.info("Планировщик запущен (напоминания + сводки каждую минуту)")

    def shutdown(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)

    # ------------------------------------------------------------ напоминания

    async def send_due_reminders(self) -> None:
        try:
            now_iso = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
            rows = await self.db.due_reminders(now_iso)
            for row in rows:
                try:
                    await self.bot.send_message(
                        row["user_id"],
                        f"⏰ <b>Напоминание</b>\n{row['text']}",
                    )
                except TelegramAPIError as exc:
                    log.warning("Не смогла отправить напоминание %s: %s", row["id"], exc)
                finally:
                    await self.db.mark_reminder_sent(row["id"])
        except Exception:
            log.exception("Ошибка в send_due_reminders")

    # ---------------------------------------------------------------- сводки

    async def send_digests(self) -> None:
        try:
            now_utc = datetime.now(timezone.utc)
            users = await self.db.all_onboarded_users()
            for user in users:
                try:
                    tz = ZoneInfo(user["timezone"] or settings.default_tz)
                except Exception:
                    tz = ZoneInfo("UTC")
                now_local = now_utc.astimezone(tz)
                digest_time = (user["digest_time"] or "09:00").strip()
                if now_local.strftime("%H:%M") != digest_time:
                    continue
                last = await self.db.get_setting(user["user_id"], "last_digest")
                if last == now_local.strftime("%Y-%m-%d"):
                    continue  # уже отправляли сегодня
                await self.send_digest_to(user)
                await self.db.set_setting(
                    user["user_id"], "last_digest", now_local.strftime("%Y-%m-%d")
                )
        except Exception:
            log.exception("Ошибка в send_digests")

    async def send_digest_to(self, user_row) -> None:
        from notes import digest_text

        try:
            text, kb = await digest_text(self.db, user_row)
            await self.bot.send_message(
                user_row["user_id"], text, reply_markup=kb
            )
            log.info("Сводка отправлена пользователю %s", user_row["user_id"])
        except TelegramAPIError as exc:
            log.warning("Не смогла отправить сводку %s: %s", user_row["user_id"], exc)
        except Exception:
            log.exception("Ошибка сборки сводки для %s", user_row["user_id"])


__all__ = ["ReminderScheduler"]
