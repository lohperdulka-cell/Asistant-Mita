"""Настройки профиля в боте: стиль, модель, провайдер, язык, тема.

Роутер подключается после flows: callback'и `set:*` не конфликтуют с текстом,
поэтому порядок влияет только на читаемость.
"""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import settings
from database import Database
from llm_client import STYLE_HINTS
from model_registry import (
    GROQ_MODELS,
    OPENROUTER_MODELS,
    PROVIDER_TITLES,
    model_title,
    provider_title,
)

log = logging.getLogger(__name__)
router = Router(name="settings_menu")

LANGS = {"ru": "Русский", "en": "English"}
THEMES = {"dark": "Тёмная", "light": "Светлая"}


class StyleForm(StatesGroup):
    title = State()
    prompt = State()


def lang_title(code: str | None) -> str:
    return LANGS.get(code or "ru", code or "ru")


def theme_title(code: str | None) -> str:
    return THEMES.get(code or "dark", code or "dark")


def _base_styles() -> dict[str, str]:
    return dict(STYLE_HINTS)


async def all_styles(db: Database, user_id: int) -> dict[str, tuple[str, str]]:
    """code -> (название, промпт). Свои стили дополняют базовые."""
    result: dict[str, tuple[str, str]] = {
        code: (code, prompt) for code, prompt in _base_styles().items()
    }
    from onboarding import STYLE_TITLES

    for code, (_, prompt) in list(result.items()):
        result[code] = (STYLE_TITLES.get(code, code), prompt)
    for row in await db.list_user_styles(user_id):
        result[row["code"]] = (row["title"], row["prompt"])
    return result


async def style_prompt(db: Database, user_id: int, code: str | None) -> str | None:
    if not code:
        return None
    styles = await all_styles(db, user_id)
    return styles.get(code, (None, None))[1]


async def settings_view(db: Database, user_id: int) -> tuple[str, object]:
    from onboarding import STYLE_TITLES

    row = await db.get_user(user_id)
    if row is None:
        return "Сначала напиши /start.", None
    styles = await all_styles(db, user_id)
    style_name = styles.get(row["style"] or "", (STYLE_TITLES.get(row["style"] or "", "—"), ""))[0]
    text = (
        "<b>⚙️ Настройки</b>\n\n"
        f"Стиль: {style_name}\n"
        f"Провайдер: {provider_title(row['provider'])}\n"
        f"Модель: {model_title(row['model'])}\n"
        f"Язык: {lang_title(row['lang'])}\n"
        f"Тема: {theme_title(row['theme'])}"
    )
    kb = InlineKeyboardBuilder()
    kb.button(text="🎭 Стиль", callback_data="set:style")
    kb.button(text="🤖 Модель", callback_data="set:model")
    kb.button(text="🌐 Язык", callback_data="set:lang")
    kb.button(text="🎨 Тема", callback_data="set:theme")
    kb.button(text="✏️ Свой стиль", callback_data="set:style:add")
    kb.button(text="🔄 Онбординг заново", callback_data="ob:restart")
    kb.adjust(2, 2, 2)
    return text, kb.as_markup()


@router.callback_query(F.data == "set:style")
async def choose_style(callback: CallbackQuery, db: Database) -> None:
    await callback.answer()
    styles = await all_styles(db, callback.from_user.id)
    row = await db.get_user(callback.from_user.id)
    current = row["style"] if row else None
    kb = InlineKeyboardBuilder()
    for code, (title, _) in styles.items():
        mark = "✅ " if code == current else ""
        kb.button(text=f"{mark}{title}", callback_data=f"set:style:{code}")
    kb.button(text="✏️ Добавить свой", callback_data="set:style:add")
    kb.adjust(2)
    if callback.message:
        await callback.message.answer("Выбери стиль общения:", reply_markup=kb.as_markup())


@router.callback_query(F.data == "set:style:add")
async def add_style_start(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.set_state(StyleForm.title)
    if callback.message:
        await callback.message.answer(
            "✏️ Как назвать стиль? Например: <code>Пират</code> или <code>Наставник</code>"
        )


@router.message(StyleForm.title, F.text)
async def add_style_title(message: Message, state: FSMContext) -> None:
    title = (message.text or "").strip()[:60]
    if not title:
        await message.answer("Название пустое. Напиши ещё раз.")
        return
    await state.update_data(title=title)
    await state.set_state(StyleForm.prompt)
    await message.answer(
        f"Опиши, как отвечать в стиле «{title}».\n"
        "Например: <code>Отвечай как пират, используй морские словечки</code>"
    )


@router.message(StyleForm.prompt, F.text)
async def add_style_prompt(message: Message, state: FSMContext, db: Database) -> None:
    prompt = (message.text or "").strip()[:2000]
    if len(prompt) < 5:
        await message.answer("Слишком коротко. Опиши подробнее.")
        return
    data = await state.get_data()
    title = data.get("title", "Свой стиль")
    code = "custom_" + str(abs(hash(title)) % 10_000)
    style_id = await db.add_user_style(message.from_user.id, code, title, prompt)
    await state.clear()
    if style_id is None:
        await message.answer("Не сохранила: похоже, такой стиль уже есть.")
        return
    await db.set_user_prefs(message.from_user.id, style=code)
    await message.answer(
        f"✅ Стиль «{title}» создан и выбран.\n\n"
        f"Промпт: <i>{prompt[:200]}</i>"
    )


@router.callback_query(F.data.startswith("set:style:"))
async def apply_style(callback: CallbackQuery, db: Database) -> None:
    code = callback.data.split(":", 2)[2]
    if code == "add":
        return
    styles = await all_styles(db, callback.from_user.id)
    if code not in styles:
        await callback.answer("Стиль не найден", show_alert=True)
        return
    await db.set_user_prefs(callback.from_user.id, style=code)
    await callback.answer(f"Стиль: {styles[code][0]}")
    text, markup = await settings_view(db, callback.from_user.id)
    if callback.message:
        try:
            await callback.message.edit_text(text, reply_markup=markup)
        except Exception:
            await callback.message.answer(text, reply_markup=markup)


@router.callback_query(F.data == "set:model")
async def choose_model(callback: CallbackQuery, db: Database) -> None:
    await callback.answer()
    row = await db.get_user(callback.from_user.id)
    current_provider = (row["provider"] if row else None) or "auto"
    current_model = row["model"] if row else None
    kb = InlineKeyboardBuilder()
    kb.button(text="🔀 Авто (цепочка)", callback_data="set:provider:auto")
    for code, title in PROVIDER_TITLES.items():
        mark = "✅ " if code == current_provider else ""
        kb.button(text=f"{mark}{title}", callback_data=f"set:provider:{code}")
    kb.adjust(1)
    if callback.message:
        await callback.message.answer(
            f"Текущая модель: <b>{model_title(current_model)}</b>\n\nВыбери провайдера:",
            reply_markup=kb.as_markup(),
        )


@router.callback_query(F.data.startswith("set:provider:"))
async def choose_provider(callback: CallbackQuery, db: Database) -> None:
    provider = callback.data.split(":", 2)[2]
    await callback.answer()
    if provider == "auto":
        await db.set_user_prefs(callback.from_user.id, provider="", model="")
        await callback.message.answer("🔀 Вернула авто-режим: провайдеры перебираются по цепочке.")
        return
    models = OPENROUTER_MODELS if provider == "openrouter" else GROQ_MODELS
    if provider not in PROVIDER_TITLES or not models:
        await callback.message.answer("Провайдер недоступен.")
        return
    kb = InlineKeyboardBuilder()
    for model_id, title, _note in models:
        kb.button(text=title, callback_data=f"set:model:{provider}:{model_id}")
    kb.adjust(1)
    if callback.message:
        await callback.message.answer(
            f"<b>{PROVIDER_TITLES[provider]}</b> — выбери модель:",
            reply_markup=kb.as_markup(),
        )


@router.callback_query(F.data.startswith("set:model:"))
async def apply_model(callback: CallbackQuery, db: Database) -> None:
    _, _, provider, model_id = callback.data.split(":", 3)
    await db.set_user_prefs(callback.from_user.id, provider=provider, model=model_id)
    await callback.answer("Сохранено")
    text, markup = await settings_view(db, callback.from_user.id)
    if callback.message:
        await callback.message.answer(
            f"✅ Модель: <b>{model_title(model_id)}</b>", reply_markup=markup
        )


@router.callback_query(F.data == "set:lang")
async def choose_lang(callback: CallbackQuery, db: Database) -> None:
    await callback.answer()
    kb = InlineKeyboardBuilder()
    for code, title in LANGS.items():
        kb.button(text=title, callback_data=f"set:lang:{code}")
    kb.adjust(2)
    if callback.message:
        await callback.message.answer("Выбери язык интерфейса:", reply_markup=kb.as_markup())


@router.callback_query(F.data.startswith("set:lang:"))
async def apply_lang(callback: CallbackQuery, db: Database) -> None:
    code = callback.data.split(":", 2)[2]
    if code not in LANGS:
        await callback.answer("Неизвестный язык", show_alert=True)
        return
    await db.set_user_prefs(callback.from_user.id, lang=code)
    await callback.answer(f"Язык: {LANGS[code]}")
    text, markup = await settings_view(db, callback.from_user.id)
    if callback.message:
        try:
            await callback.message.edit_text(text, reply_markup=markup)
        except Exception:
            await callback.message.answer(text, reply_markup=markup)


@router.callback_query(F.data == "set:theme")
async def choose_theme(callback: CallbackQuery, db: Database) -> None:
    await callback.answer()
    kb = InlineKeyboardBuilder()
    for code, title in THEMES.items():
        kb.button(text=title, callback_data=f"set:theme:{code}")
    kb.adjust(2)
    if callback.message:
        await callback.message.answer(
            "Тема Mini App (в боте тема не влияет на сообщения):",
            reply_markup=kb.as_markup(),
        )


@router.callback_query(F.data.startswith("set:theme:"))
async def apply_theme(callback: CallbackQuery, db: Database) -> None:
    code = callback.data.split(":", 2)[2]
    if code not in THEMES:
        await callback.answer("Неизвестная тема", show_alert=True)
        return
    await db.set_user_prefs(callback.from_user.id, theme=code)
    await callback.answer(f"Тема: {THEMES[code]}")
    text, markup = await settings_view(db, callback.from_user.id)
    if callback.message:
        try:
            await callback.message.edit_text(text, reply_markup=markup)
        except Exception:
            await callback.message.answer(text, reply_markup=markup)


__all__ = [
    "router",
    "all_styles",
    "style_prompt",
    "settings_view",
    "lang_title",
    "theme_title",
    "LANGS",
    "THEMES",
]
