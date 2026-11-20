"""Пункт N4: настройки стиля/провайдера/модели и их влияние на LLM.

Проверяем два свойства:
1) выбор пользователя сохраняется в БД и переживает повторное чтение;
2) выбранный провайдер встаёт первым в цепочке, но не отключает остальные —
   иначе один сбой провайдера ломал бы весь бот.
"""

import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import settings
from database import Database
from llm_client import STYLE_HINTS, llm
from model_registry import (
    GROQ_MODELS,
    OPENROUTER_MODELS,
    is_known,
    model_title,
    models_for,
    provider_title,
)
from settings_menu import all_styles, settings_view, style_prompt

FAILED = []


def check(name: str, ok: bool, detail: str = "") -> None:
    if ok:
        print(f"PASS  {name}")
    else:
        FAILED.append(name)
        print(f"FAIL  {name}  {detail}")


def enable_keys() -> None:
    object.__setattr__(settings, "openrouter_api_key", "or-key")
    object.__setattr__(settings, "groq_api_key", "groq-key")


async def main() -> None:
    # ---- каталог моделей самосогласован
    check("N4 у каждого провайдера есть модели",
          bool(OPENROUTER_MODELS) and bool(GROQ_MODELS))
    check("N4 is_known находит модель из каталога",
          is_known("groq", GROQ_MODELS[0][0]))
    check("N4 is_known отвергает чужую модель",
          not is_known("groq", "google/gemma-4-31b-it:free"))
    check("N4 models_for('auto') пустой", models_for(None) == [])
    check("N4 model_title для неизвестной модели возвращает id",
          model_title("x/y") == "x/y")
    check("N4 provider_title(None) = авто",
          "авто" in provider_title(None))

    # ---- БД: сохранение настроек
    db = Database(str(Path(tempfile.mkdtemp()) / "s.db"))
    await db.init()
    uid = 4242
    await db.touch_user(uid, "u", "U")
    await db.save_profile(uid, goal="study", style="brief", features=["notes"])

    check("N4 по умолчанию провайдер пуст", (await db.get_user(uid))["provider"] is None)

    await db.set_user_prefs(uid, provider="groq", model=GROQ_MODELS[0][0])
    row = await db.get_user(uid)
    check("N4 провайдер сохранён", row["provider"] == "groq", str(row["provider"]))
    check("N4 модель сохранена", row["model"] == GROQ_MODELS[0][0], str(row["model"]))

    await db.set_user_prefs(uid, provider="", model="")
    row = await db.get_user(uid)
    check("N4 пустая строка сбрасывает в NULL",
          row["provider"] is None and row["model"] is None,
          f"{row['provider']}/{row['model']}")

    await db.set_user_prefs(uid, lang="en", theme="light")
    row = await db.get_user(uid)
    check("N4 язык и тема сохранены",
          row["lang"] == "en" and row["theme"] == "light")

    # ---- стили
    styles = await all_styles(db, uid)
    for code in ("brief", "detailed", "friendly", "formal"):
        check(f"N4 базовый стиль {code} доступен", code in styles, str(list(styles)))
    check("N4 промпт стиля непустой",
          bool(await style_prompt(db, uid, "friendly")))
    check("N4 неизвестный стиль даёт None",
          await style_prompt(db, uid, "нет-такого") is None)

    style_id = await db.add_user_style(uid, "custom_pirate", "Пират", "Говори как пират")
    check("N4 свой стиль создан", style_id is not None)
    styles = await all_styles(db, uid)
    check("N4 свой стиль попал в список", styles.get("custom_pirate", ("",))[0] == "Пират")
    check("N4 промпт своего стиля отдаётся",
          await style_prompt(db, uid, "custom_pirate") == "Говори как пират")
    dup = await db.add_user_style(uid, "custom_pirate", "Пират 2", "Другой промпт")
    check("N4 дубль кода стиля отклонён", dup is None)

    # ---- экран настроек
    text, markup = await settings_view(db, uid)
    check("N4 экран настроек показывает стиль", "Стиль" in text, text[:60])
    check("N4 экран настроек показывает модель", "Модель" in text)
    check("N4 экран настроек имеет кнопки", markup is not None)

    text, markup = await settings_view(db, 999_999)
    check("N4 без онбординга экран просит /start", "/start" in text, text[:40])

    # ---- влияние выбора на цепочку провайдеров
    enable_keys()
    chain = [p.name for p in llm.providers()]
    check("N4 цепочка содержит оба провайдера",
          {"openrouter", "groq"} <= set(chain), str(chain))

    chain = [p.name for p in llm.providers(prefer="groq")]
    check("N4 выбранный провайдер первый в цепочке", chain[0] == "groq", str(chain))
    check("N4 остальные провайдеры остаются как fallback",
          "openrouter" in chain, str(chain))

    chain = llm.providers(prefer="groq", model=GROQ_MODELS[1][0])
    check("N4 выбранная модель применяется к провайдеру",
          chain[0].model == GROQ_MODELS[1][0], chain[0].model)
    check("N4 модель из настроек не ломает base_url",
          "groq.com" in chain[0].base_url, chain[0].base_url)

    chain = [p.name for p in llm.providers(prefer="нет-такого")]
    check("N4 неизвестный провайдер игнорируется",
          {"openrouter", "groq"} <= set(chain), str(chain))

    # ---- отключённый ключ выкидывает провайдера из цепочки
    object.__setattr__(settings, "groq_api_key", "")
    chain = [p.name for p in llm.providers(prefer="groq")]
    check("N4 без ключа провайдер исключён из цепочки",
          "groq" not in chain, str(chain))
    check("N4 при этом бот остаётся работоспособным",
          "openrouter" in chain, str(chain))

    await db.close()

    print()
    if FAILED:
        print(f"ПРОВАЛЕНО: {len(FAILED)} — {FAILED}")
        raise SystemExit(1)
    print("ALL SETTINGS (N4) TESTS PASSED")


asyncio.run(main())
