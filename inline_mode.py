"""Inline-режим «Миты».

Особенности:
* профиль (цель, стиль, функции) подтягивается по user_id автора запроса;
* приватные данные (заметки, материалы) в inline НЕ отдаются никогда —
  только общий ответ по стилю и подсказка открыть бота в личке;
* если LLM недоступна, отдаётся офлайн-подсказка, а не пустой список.
"""

from __future__ import annotations

import logging

from aiogram import Router
from aiogram.types import (
    InlineQuery,
    InlineQueryResultArticle,
    InputTextMessageContent,
)

from config import settings
from database import Database, features_of
from llm_client import OFFLINE_NOTE, llm

log = logging.getLogger(__name__)
router = Router(name="inline")

MAX_QUERY = 400
PRIVATE_HINT = (
    "🔒 Твои заметки и материалы доступны только в личном чате со мной.\n"
    "Открой бота и продолжим там."
)


def profile_summary(row) -> str:
    if row is None:
        return ""
    goal = row["goal"] or ""
    style = row["style"] or "brief"
    feats = ", ".join(features_of(row))
    parts = []
    if goal:
        parts.append(f"цель пользователя: {goal}")
    if feats:
        parts.append(f"нужные функции: {feats}")
    parts.append(f"стиль: {style}")
    return "; ".join(parts)


def build_article(query: str, text: str, title: str, description: str) -> InlineQueryResultArticle:
    return InlineQueryResultArticle(
        id=str(abs(hash((query, title))) % 10_000_000),
        title=title,
        description=description[:120],
        input_message_content=InputTextMessageContent(message_text=text),
    )


@router.inline_query()
async def inline_handler(query: InlineQuery, db: Database) -> None:
    text = (query.query or "").strip()
    user_id = query.from_user.id
    row = await db.get_user(user_id)
    style = (row["style"] if row else None) or "brief"
    summary = profile_summary(row)

    results: list[InlineQueryResultArticle] = []

    if not text:
        results.append(
            build_article(
                "empty",
                "Мита на связи. Открой меня в личке, чтобы я помнила твои заметки.",
                "Мита — личный ассистент",
                "Напиши запрос после @username, например: @bot план на день",
            )
        )
        if settings.bot_username:
            results.append(
                build_article(
                    "hint",
                    f"Открой @{settings.bot_username} в личке — там онбординг, заметки и материалы.",
                    "Как пользоваться",
                    "Приватные данные доступны только в личном чате",
                )
            )
        await query.answer(results, cache_time=5, is_personal=True)
        return

    prompt = text[:MAX_QUERY]
    system = "Ты — Мита, краткий и полезный ассистент в inline-режиме Telegram."
    if summary:
        system += f"\nПрофиль автора запроса: {summary}."
    system += (
        "\nНе выдумывай личные данные пользователя (заметки, файлы) — "
        "их у тебя нет. Дай общий полезный ответ."
    )

    result = await llm.complete(prompt, system=system, style=style, max_tokens=400)
    if result.ok:
        body = result.text
        desc = body.replace("\n", " ")[:110]
    else:
        body = f"{OFFLINE_NOTE}\n\nЗапрос: {prompt}"
        desc = "LLM недоступна — офлайн-подсказка"

    results.append(build_article(prompt, body, "Ответ Миты", desc))
    results.append(
        build_article(
            prompt + ":private",
            PRIVATE_HINT,
            "Мои заметки и материалы",
            "Доступно только в личном чате с ботом",
        )
    )
    if settings.bot_username:
        results.append(
            build_article(
                prompt + ":open",
                f"Открыть @{settings.bot_username} в личке → /notes, /materials",
                "Открыть бота",
                "Заметки, напоминания, материалы, привычки",
            )
        )

    await query.answer(results, cache_time=3, is_personal=True)


__all__ = ["router", "profile_summary"]
