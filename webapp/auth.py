"""Валидация Telegram WebApp initData.

Алгоритм из документации Telegram:
  secret_key = HMAC_SHA256(key="WebAppData", msg=BOT_TOKEN)
  data_check_string = "key=value" строки, отсортированные по ключу, через \n
  hash == HMAC_SHA256(key=secret_key, msg=data_check_string)
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from dataclasses import dataclass
from urllib.parse import parse_qsl

log = logging.getLogger(__name__)

DEFAULT_TTL = 24 * 3600


@dataclass
class WebAppUser:
    id: int
    first_name: str = ""
    username: str = ""
    language_code: str = ""

    @property
    def display_name(self) -> str:
        return self.first_name or self.username or str(self.id)


class InitDataError(ValueError):
    """initData отсутствует, повреждён или подпись не сходится."""


def _secret_key(bot_token: str) -> bytes:
    return hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()


def verify_init_data(
    init_data: str,
    bot_token: str,
    ttl: int = DEFAULT_TTL,
    now: float | None = None,
) -> dict:
    """Проверяет подпись и свежесть. Возвращает распарсенные поля."""
    if not init_data:
        raise InitDataError("initData пуст")
    if not bot_token:
        raise InitDataError("BOT_TOKEN не настроен на сервере")

    pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = pairs.pop("hash", None)
    if not received_hash:
        raise InitDataError("в initData нет hash")

    data_check_string = "\n".join(f"{key}={pairs[key]}" for key in sorted(pairs))
    expected = hmac.new(
        _secret_key(bot_token), data_check_string.encode(), hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(expected, received_hash):
        raise InitDataError("подпись initData не совпадает")

    auth_date_raw = pairs.get("auth_date")
    if auth_date_raw:
        try:
            auth_date = int(auth_date_raw)
        except ValueError as exc:
            raise InitDataError("некорректный auth_date") from exc
        current = now if now is not None else time.time()
        if ttl and current - auth_date > ttl:
            raise InitDataError("initData просрочен")

    if "user" in pairs:
        try:
            pairs["user"] = json.loads(pairs["user"])
        except json.JSONDecodeError as exc:
            raise InitDataError("некорректный user в initData") from exc
    return pairs


def user_from_init_data(data: dict) -> WebAppUser:
    raw = data.get("user") or {}
    if not isinstance(raw, dict) or "id" not in raw:
        raise InitDataError("в initData нет пользователя")
    return WebAppUser(
        id=int(raw["id"]),
        first_name=raw.get("first_name", ""),
        username=raw.get("username", ""),
        language_code=raw.get("language_code", ""),
    )


def sign_init_data(fields: dict, bot_token: str) -> str:
    """Собирает корректно подписанный initData. Нужен для тестов и отладки."""
    from urllib.parse import urlencode

    data_check_string = "\n".join(f"{key}={fields[key]}" for key in sorted(fields))
    signature = hmac.new(
        _secret_key(bot_token), data_check_string.encode(), hashlib.sha256
    ).hexdigest()
    return urlencode({**fields, "hash": signature})


__all__ = [
    "verify_init_data",
    "user_from_init_data",
    "sign_init_data",
    "InitDataError",
    "WebAppUser",
]
