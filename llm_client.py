"""Клиент LLM с цепочкой провайдеров и graceful degradation.

Провайдеры перебираются по порядку из LLM_CHAIN: если первый вернул ошибку
(лимит, 401, таймаут), запрос уходит к следующему. Так бот продолжает
отвечать, когда у бесплатного провайдера кончилась квота.

Если ключей нет вовсе — возвращается понятный офлайн-ответ, бот не падает
(важно для Termux с нестабильным интернетом).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

from config import settings

log = logging.getLogger(__name__)

STYLE_HINTS = {
    "brief": "Отвечай максимально кратко: 1–3 предложения или короткий список.",
    "detailed": "Отвечай подробно и структурно: заголовки, списки, примеры.",
    "friendly": "Отвечай дружелюбно, живо, на «ты», с лёгкой эмодзи.",
    "formal": "Отвечай формально, нейтрально, без эмодзи и сленга.",
}

OFFLINE_NOTE = (
    "⚠️ LLM сейчас недоступна (нет ключа или сети). "
    "Ниже — то, что я нашла без неё."
)


@dataclass(frozen=True)
class Provider:
    name: str
    base_url: str
    api_key: str
    model: str

    @property
    def enabled(self) -> bool:
        return bool(self.api_key and self.base_url and self.model)


@dataclass
class LLMResult:
    text: str
    ok: bool
    error: str | None = None
    provider: str | None = None


# Пауза перед повторным обращением к провайдеру, который только что упал.
# Бесплатные тарифы часто отдают 429 на минуту — нет смысла долбить каждым запросом.
COOLDOWN_SECONDS = 60


class LLMClient:
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        timeout: int | None = None,
    ) -> None:
        # Явные аргументы полностью заменяют цепочку — так удобно в тестах.
        self._explicit = api_key is not None or base_url is not None or model is not None
        self.api_key = api_key if api_key is not None else settings.llm_api_key
        self.base_url = (base_url or settings.llm_base_url).rstrip("/")
        self.model = model or settings.llm_model
        self.timeout = timeout or settings.llm_timeout
        self._cooldown: dict[str, float] = {}

    def providers(self) -> list[Provider]:
        if self._explicit:
            return [
                Provider(
                    name="explicit",
                    base_url=self.base_url,
                    api_key=self.api_key,
                    model=self.model,
                )
            ]

        known = {
            "primary": Provider(
                name="primary",
                base_url=self.base_url,
                api_key=self.api_key,
                model=self.model,
            ),
            "openrouter": Provider(
                name="openrouter",
                base_url="https://openrouter.ai/api/v1",
                api_key=settings.openrouter_api_key,
                model=settings.openrouter_model,
            ),
            "groq": Provider(
                name="groq",
                base_url="https://api.groq.com/openai/v1",
                api_key=settings.groq_api_key,
                model=settings.groq_model,
            ),
        }
        order = [part.strip().lower() for part in settings.llm_chain.split(",")]
        chain = [known[name] for name in order if name in known]
        chain.extend(p for name, p in known.items() if name not in order)
        return [p for p in chain if p.enabled]

    @property
    def ready(self) -> bool:
        return bool(self.providers())

    def _cooling(self, name: str) -> bool:
        until = self._cooldown.get(name, 0)
        return time.monotonic() < until

    async def complete(
        self,
        prompt: str,
        system: str | None = None,
        style: str | None = None,
        history: list[dict[str, str]] | None = None,
        max_tokens: int | None = None,
    ) -> LLMResult:
        chain = self.providers()
        if not chain:
            return LLMResult(text="", ok=False, error="LLM_API_KEY не задан")

        messages: list[dict[str, str]] = []
        system_text = system or "Ты — Мита, краткий и полезный личный ассистент."
        if style and style in STYLE_HINTS:
            system_text = f"{system_text}\n{STYLE_HINTS[style]}"
        messages.append({"role": "system", "content": system_text})
        for item in history or []:
            if item.get("role") in {"user", "assistant"} and item.get("content"):
                messages.append({"role": item["role"], "content": item["content"]})
        messages.append({"role": "user", "content": prompt})

        payload: dict[str, Any] = {
            "messages": messages,
            "temperature": 0.6,
            "max_tokens": max_tokens or settings.llm_max_tokens,
        }

        # Сначала провайдеры без cooldown, затем остывающие — как последний шанс.
        ordered = [p for p in chain if not self._cooling(p.name)]
        ordered += [p for p in chain if self._cooling(p.name)]

        errors: list[str] = []
        for provider in ordered:
            result = await self._call(provider, payload)
            if result.ok:
                self._cooldown.pop(provider.name, None)
                return result
            errors.append(f"{provider.name}: {result.error}")
            if result.error and result.error.startswith(
                ("HTTP 401", "HTTP 403", "HTTP 429", "таймаут")
            ):
                self._cooldown[provider.name] = time.monotonic() + COOLDOWN_SECONDS
            log.warning("LLM %s не сработал: %s", provider.name, result.error)

        return LLMResult(text="", ok=False, error="; ".join(errors)[:300])

    async def _call(self, provider: Provider, payload: dict[str, Any]) -> LLMResult:
        body = dict(payload, model=provider.model)
        headers = {
            "Authorization": f"Bearer {provider.api_key}",
            "Content-Type": "application/json",
        }
        if provider.name == "openrouter":
            # OpenRouter просит эти заголовки для статистики; без них тоже работает.
            headers["HTTP-Referer"] = "https://github.com/mita-assistant-bot"
            headers["X-Title"] = "Mita Assistant"

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{provider.base_url}/chat/completions",
                    headers=headers,
                    json=body,
                )
            if response.status_code >= 400:
                return LLMResult(
                    text="",
                    ok=False,
                    error=f"HTTP {response.status_code}",
                    provider=provider.name,
                )
            data = response.json()
            text = (data["choices"][0]["message"]["content"] or "").strip()
            if not text:
                return LLMResult(
                    text="", ok=False, error="пустой ответ", provider=provider.name
                )
            return LLMResult(text=text, ok=True, provider=provider.name)
        except httpx.TimeoutException:
            return LLMResult(text="", ok=False, error="таймаут", provider=provider.name)
        except (httpx.HTTPError, KeyError, ValueError, TypeError) as exc:
            return LLMResult(
                text="", ok=False, error=str(exc)[:200], provider=provider.name
            )


llm = LLMClient()

__all__ = [
    "LLMClient",
    "LLMResult",
    "Provider",
    "llm",
    "STYLE_HINTS",
    "OFFLINE_NOTE",
]
