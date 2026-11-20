"""Свободный чат с LLM в боте: пользователь просто пишет, без команд.

Роутер подключается последним, поэтому сюда попадает только текст, который
не перехватили команды, онбординг и FSM-сценарии.

Ответы пишутся в ту же историю, что и чат Mini App — пользователь видит
одну ленту с обоих устройств.
"""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message

from config import settings
from database import Database
from llm_client import OFFLINE_NOTE, llm
from settings_menu import style_prompt

log = logging.getLogger(__name__)
router = Router(name="llm_chat")

MAX_INPUT = 4000
HISTORY_DEPTH = 10

ERROR_HINTS = {
    "HTTP 401": "ключ провайдера не принят — проверьте его в настройках",
    "HTTP 402": "на счёте провайдера закончились средства",
    "HTTP 403": "доступ к модели запрещён для этого ключа",
    "HTTP 429": "исчерпан лимит бесплатных запросов — попробуйте через минуту",
    "таймаут": "провайдер не ответил вовремя — попробуйте ещё раз",
}


def explain(error: str | None) -> str:
    """Переводит техническую ошибку провайдера в понятную фразу."""
    if not error:
        return "неизвестная ошибка"
    if "не задан" in error:
        return "не настроен ключ LLM"
    parts = []
    for marker, hint in ERROR_HINTS.items():
        if marker in error:
            parts.append(hint)
    if parts:
        return "; ".join(dict.fromkeys(parts))
    return error[:200]


async def system_prompt(db: Database, row) -> str:
    base = (
        "Ты — Мита, личный ассистент в Telegram. "
        "Отвечай по делу и не выдумывай факты о пользователе."
    )
    if row is None:
        return base
    if row["goal"]:
        from onboarding import GOAL_TITLES

        base += f"\nОсновная цель пользователя: {GOAL_TITLES.get(row['goal'], row['goal'])}."
    prompt = await style_prompt(db, row["user_id"], row["style"])
    if prompt:
        base += f"\nСтиль ответа: {prompt}"
    return base


async def reply_with_llm(
    message: Message, db: Database, text: str, source: str = "bot"
) -> None:
    """Общий путь: история -> LLM -> сохранение обоих сообщений."""
    user_id = message.from_user.id
    row = await db.get_user(user_id)

    history_rows = await db.chat_history(user_id, limit=HISTORY_DEPTH)
    history = [
        {"role": item["role"], "content": item["content"]}
        for item in history_rows
        if item["role"] in {"user", "assistant"}
    ]

    await db.add_chat_message(user_id, "user", text[:MAX_INPUT], source)
    await message.bot.send_chat_action(message.chat.id, "typing")

    result = await llm.complete(
        text[:MAX_INPUT],
        system=await system_prompt(db, row),
        history=history,
        provider=row["provider"] if row else None,
        model=row["model"] if row else None,
    )

    if result.ok:
        await db.add_chat_message(user_id, "assistant", result.text, source)
        await message.answer(result.text)
        return

    reason = explain(result.error)
    await message.answer(
        f"⚠️ Не смогла ответить: {reason}.\n\n"
        "Повтори запрос через минуту. Если не поможет — проверь настройки "
        "модели командой /settings."
    )
    log.warning("LLM не ответила (%s): %s", user_id, result.error)


@router.message(Command("clear"))
async def clear_chat_command(message: Message, db: Database) -> None:
    removed = await db.clear_chat(message.from_user.id)
    await message.answer(
        f"🧹 История диалога очищена ({removed} сообщений).\n"
        "Заметки, напоминания и материалы не тронуты."
    )


@router.message(F.text & ~F.text.startswith("/"))
async def free_chat(message: Message, db: Database) -> None:
    if message.from_user is None:
        return
    text = (message.text or "").strip()
    if not text:
        return
    if not settings.llm_ready:
        await message.answer(
            f"{OFFLINE_NOTE}\n\n"
            "Добавь ключ в <code>.env</code> (OPENROUTER_API_KEY или GROQ_API_KEY) "
            "и перезапусти бота."
        )
        return
    await reply_with_llm(message, db, text)


__all__ = ["router", "reply_with_llm", "system_prompt", "explain"]
