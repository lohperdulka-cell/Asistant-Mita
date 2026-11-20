"""Каталог моделей и провайдеров для выбора в боте и Mini App.

Только статические данные: id модели, человекочитаемое имя и пометка о лимитах.
Ничего не запрашивает по сети — список работает офлайн.

Список бесплатных моделей OpenRouter меняется; актуальный всегда можно получить
командой `python scripts/models.py list`. Если модели в каталоге устарели,
бот не падает: LLM вернёт ошибку провайдера, и сработает fallback по цепочке.
"""

from __future__ import annotations

PROVIDER_TITLES = {
    "openrouter": "OpenRouter",
    "groq": "Groq",
}

# (id, название, пометка о лимитах)
OPENROUTER_MODELS: list[tuple[str, str, str]] = [
    ("google/gemma-4-31b-it:free", "Gemma 4 31B", "бесплатно, 262k контекст"),
    ("google/gemma-4-26b-a4b-it:free", "Gemma 4 26B A4B", "бесплатно, MoE"),
    ("nvidia/nemotron-3-ultra-550b-a55b:free", "Nemotron 3 Ultra", "бесплатно, 1M контекст"),
    ("nvidia/nemotron-3.5-lightning:free", "Nemotron 3.5 Lightning", "бесплатно, быстрый"),
    ("inclusionai/ling-3.2-flash:free", "Ling 3.2 Flash", "бесплатно, 1M контекст"),
    ("openrouter/free", "Авто-роутер", "сам выберет свободную модель"),
]

GROQ_MODELS: list[tuple[str, str, str]] = [
    ("llama-3.3-70b-versatile", "Llama 3.3 70B", "быстрый, щедрые лимиты"),
    ("openai/gpt-oss-120b", "GPT-OSS 120B", "бесплатно, ~30 RPM / 1000 в день"),
    ("openai/gpt-oss-20b", "GPT-OSS 20B", "бесплатно, быстрее 120B"),
    ("llama-3.1-8b-instant", "Llama 3.1 8B", "самый быстрый"),
    ("mixtral-8x7b-32768", "Mixtral 8x7B", "длинный контекст"),
]

PROVIDERS: dict[str, list[tuple[str, str, str]]] = {
    "openrouter": OPENROUTER_MODELS,
    "groq": GROQ_MODELS,
}

_ALL_MODELS = {
    model_id: title
    for models in PROVIDERS.values()
    for model_id, title, _ in models
}


def model_title(model_id: str | None) -> str:
    if not model_id:
        return "авто"
    return _ALL_MODELS.get(model_id, model_id)


def provider_title(provider: str | None) -> str:
    if not provider:
        return "авто (цепочка)"
    return PROVIDER_TITLES.get(provider, provider)


def models_for(provider: str | None) -> list[tuple[str, str, str]]:
    if not provider:
        return []
    return PROVIDERS.get(provider, [])


def is_known(provider: str, model_id: str) -> bool:
    return any(model_id == mid for mid, _, _ in models_for(provider))


__all__ = [
    "PROVIDER_TITLES",
    "OPENROUTER_MODELS",
    "GROQ_MODELS",
    "PROVIDERS",
    "model_title",
    "provider_title",
    "models_for",
    "is_known",
]
