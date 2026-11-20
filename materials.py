"""Приём материалов и RAG-ответы: /upload, /materials, /forget, документы, ссылки.

Пользователь сам решает, что отдать боту: файл документом, текст сообщением
или ссылку. Ничего не сканируется с телефона.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject, Filter
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import settings
from database import Database
from extractors import SUPPORTED_EXTENSIONS, extract_file, fetch_url_text
from llm_client import llm
from rag import answer_with_context, chunk_text, resolve_engine

log = logging.getLogger(__name__)
router = Router(name="materials")

URL_RE = re.compile(r"^https?://\S+$", re.IGNORECASE)
MIN_TEXT = 80
MAX_TEXT_MESSAGE = 12_000


def engine_hint() -> str:
    engine = resolve_engine()
    if engine == "embeddings":
        return "эмбеддинги (sentence-transformers)"
    if engine == "tfidf":
        return "TF-IDF (scikit-learn)"
    return "поиск по ключевым словам (лёгкий режим)"


async def store_material(
    db: Database,
    user_id: int,
    title: str,
    kind: str,
    source: str | None,
    text: str,
) -> tuple[int | None, str | None]:
    """Режет текст, сохраняет материал и чанки. Возвращает (id, ошибка)."""
    if not text.strip():
        return None, "Текст пустой."
    chunks = chunk_text(text)
    if not chunks:
        return None, "Не удалось разбить текст на фрагменты."

    material_id = await db.add_material(
        user_id, title, kind, source, len(text), len(chunks)
    )
    await db.add_chunks(user_id, material_id, [(i, chunk, None) for i, chunk in enumerate(chunks)])
    return material_id, None


async def ingest_text(
    message: Message, db: Database, title: str, kind: str, source: str | None, text: str
) -> None:
    material_id, error = await store_material(
        db, message.from_user.id, title, kind, source, text
    )
    if material_id is None:
        await message.answer(f"⚠️ {error}")
        return
    chunks = len(chunk_text(text))
    await message.answer(
        f"📚 «{title}» сохранён как материал #{material_id}\n"
        f"Символов: {len(text)}, фрагментов: {chunks}\n"
        f"Поиск: {engine_hint()}\n\n"
        "Теперь можешь спросить: «найди в моих материалах … и объясни»."
    )


# --------------------------------------------------------------- документы


@router.message(F.document)
async def on_document(message: Message, bot: Bot, db: Database) -> None:
    document = message.document
    filename = document.file_name or f"file_{document.file_unique_id}"
    suffix = Path(filename).suffix.lower()

    if suffix not in SUPPORTED_EXTENSIONS:
        await message.answer(
            f"Формат {suffix or '—'} не поддерживается.\n"
            "Пришли PDF, DOCX, TXT, MD, CSV или HTML."
        )
        return

    limit = settings.max_upload_mb * 1024 * 1024
    if (document.file_size or 0) > limit:
        await message.answer(
            f"Файл больше {settings.max_upload_mb} МБ. Пришли что-то поменьше."
        )
        return

    await message.answer("⏳ Читаю документ…")
    settings.ensure_dirs()
    target = settings.uploads_dir / f"{message.from_user.id}_{document.file_unique_id}{suffix}"
    try:
        await bot.download(document, destination=target)
    except Exception as exc:
        log.warning("Не смогла скачать файл: %s", exc)
        await message.answer(f"⚠️ Не удалось скачать файл: {exc}")
        return

    result = extract_file(target)
    if not result.ok:
        await message.answer(f"⚠️ {result.error}")
        return

    await ingest_text(
        message, db, filename, suffix.lstrip("."), f"file:{filename}", result.text
    )


# ------------------------------------------------------------- текст/ссылка


@router.message(Command("upload"))
async def upload_help(message: Message) -> None:
    await message.answer(
        "📤 <b>Как добавить материалы</b>\n\n"
        "1. Пришли <b>документ</b>: PDF, DOCX, TXT, MD, CSV, HTML\n"
        "2. Или отправь <b>ссылку</b> — я прочитаю страницу\n"
        "3. Или пришли <b>текст</b> сообщением (от 80 символов)\n\n"
        "Всё это станет моим RAG-контекстом: я ищу по нему и отвечаю "
        "с опорой на твои материалы. Модель при этом не переобучается.\n\n"
        f"Лимит файла: {settings.max_upload_mb} МБ. Поиск: {engine_hint()}"
    )


@router.message(Command("materials"))
async def materials_list(message: Message, db: Database) -> None:
    await send_materials_list(message, db, message.from_user.id)


async def send_materials_list(message: Message, db: Database, user_id: int) -> None:
    rows = await db.list_materials(user_id)
    if not rows:
        await message.answer(
            "Материалов пока нет.\n\n"
            "Нажми 📚 Материалы в меню и пришли текст, файл или ссылку."
        )
        return
    lines = [f"📚 <b>Твои материалы</b> ({len(rows)}):"]
    kb = InlineKeyboardBuilder()
    for row in rows:
        created = row["created_at"][:10]
        lines.append(
            f"#{row['id']} · {row['title'][:60]}\n"
            f"    {row['kind']}, {row['chars']} симв., {row['chunks']} фрагм., {created}"
        )
        kb.button(text=f"🗑 #{row['id']}", callback_data=f"mat:del:{row['id']}")
    kb.adjust(4)
    lines.append("\nУдалить — кнопкой ниже.")
    await message.answer("\n".join(lines), reply_markup=kb.as_markup())


@router.callback_query(F.data.startswith("mat:del:"))
async def delete_material_cb(callback: CallbackQuery, db: Database) -> None:
    material_id = int(callback.data.rsplit(":", 1)[1])
    ok = await db.delete_material(callback.from_user.id, material_id)
    await callback.answer("Удалено" if ok else "Не найдено", show_alert=not ok)
    if ok and callback.message:
        await send_materials_list(callback.message, db, callback.from_user.id)


@router.message(Command("forget"))
async def forget(message: Message, command: CommandObject, db: Database) -> None:
    raw = (command.args or "").strip().lstrip("#")
    if not raw.isdigit():
        await message.answer("Формат: <code>/forget 3</code>\nСписок: /materials")
        return
    ok = await db.delete_material(message.from_user.id, int(raw))
    await message.answer(
        "🗑 Материал удалён вместе с фрагментами." if ok else "Не нашла такой материал."
    )


# ------------------------------------------------------- вопросы по материалам


RAG_TRIGGERS = (
    "в моих материалах", "в материалах", "по материалам", "найди в моих",
    "найди в материалах", "что в материале", "из моего документа",
    "в документе", "по конспекту", "в конспекте",
)


class IsMaterialRequest(Filter):
    """Пропускает только ссылки и явные запросы по материалам.

    Обычный текст должен доходить до llm_chat: в aiogram первый совпавший
    обработчик останавливает цепочку, поэтому широкий фильтр здесь нельзя.
    """

    async def __call__(self, message: Message) -> bool:
        text = (message.text or "").strip()
        if not text or text.startswith("/"):
            return False
        if URL_RE.match(text):
            return True
        lowered = text.lower()
        return any(trigger in lowered for trigger in RAG_TRIGGERS)


@router.message(IsMaterialRequest())
async def on_text(message: Message, db: Database) -> None:
    text = (message.text or "").strip()
    row = await db.get_user(message.from_user.id)
    if row is None:
        await message.answer("Напиши /start, чтобы я тебя настроила.")
        return

    if URL_RE.match(text):
        await message.answer("⏳ Читаю страницу…")
        result = await fetch_url_text(text)
        if not result.ok:
            await message.answer(f"⚠️ {result.error}")
            return
        await ingest_text(message, db, text[:80], "url", text, result.text)
        return

    await message.answer("🔎 Ищу в твоих материалах…")
    answer, hits, error = await answer_with_context(db, row, text)
    if hits:
        titles = sorted({hit.title for hit in hits})
        answer += "\n\n📎 Источники: " + ", ".join(f"«{t}»" for t in titles[:4])
    await message.answer(answer)


@router.message(Command("addtext"))
async def add_text_command(message: Message, command: CommandObject, db: Database) -> None:
    text = (command.args or "").strip()
    if len(text) < MIN_TEXT:
        await message.answer(f"Нужно минимум {MIN_TEXT} символов текста.")
        return
    title = text[:50].replace("\n", " ") + "…"
    await ingest_text(message, db, title, "text", None, text[:MAX_TEXT_MESSAGE])


__all__ = ["router", "store_material", "ingest_text", "engine_hint"]
