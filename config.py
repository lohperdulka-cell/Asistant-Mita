"""Конфигурация «Миты» — только из переменных окружения.

Секреты в коде не хранятся. Значения по умолчанию подобраны так,
чтобы бот запускался на Termux без дополнительной настройки путей.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:  # python-dotenv не обязателен
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover - dotenv просто не подхватится
    pass


BASE_DIR = Path(__file__).resolve().parent


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    raw = _env(name, "1" if default else "0").lower()
    return raw in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    bot_token: str = field(default_factory=lambda: _env("BOT_TOKEN"))
    bot_username: str = field(default_factory=lambda: _env("BOT_USERNAME").lstrip("@"))

    # Основной провайдер (любой OpenAI-совместимый).
    llm_api_key: str = field(default_factory=lambda: _env("LLM_API_KEY"))
    llm_base_url: str = field(
        default_factory=lambda: _env("LLM_BASE_URL", "https://api.openai.com/v1")
    )
    llm_model: str = field(default_factory=lambda: _env("LLM_MODEL", "gpt-4o-mini"))
    llm_timeout: int = field(default_factory=lambda: _env_int("LLM_TIMEOUT", 60))
    llm_max_tokens: int = field(default_factory=lambda: _env_int("LLM_MAX_TOKENS", 900))

    # Резервные провайдеры: используются, если основной вернул ошибку.
    openrouter_api_key: str = field(default_factory=lambda: _env("OPENROUTER_API_KEY"))
    openrouter_model: str = field(
        default_factory=lambda: _env("OPENROUTER_MODEL", "google/gemma-4-31b-it:free")
    )
    groq_api_key: str = field(default_factory=lambda: _env("GROQ_API_KEY"))
    groq_model: str = field(
        default_factory=lambda: _env("GROQ_MODEL", "llama-3.3-70b-versatile")
    )
    llm_chain: str = field(default_factory=lambda: _env("LLM_CHAIN", "primary,openrouter,groq"))

    data_dir: Path = field(
        default_factory=lambda: Path(_env("DATA_DIR", str(BASE_DIR / "data"))).expanduser()
    )
    db_path: Path | None = field(
        default_factory=lambda: (
            Path(_env("DB_PATH")).expanduser() if _env("DB_PATH") else None
        )
    )

    webapp_host: str = field(default_factory=lambda: _env("WEBAPP_HOST", "127.0.0.1"))
    webapp_port: int = field(default_factory=lambda: _env_int("WEBAPP_PORT", 8080))
    webapp_url: str = field(default_factory=lambda: _env("WEBAPP_URL", "").rstrip("/"))
    webapp_public_url: str = field(
        default_factory=lambda: _env("WEBAPP_PUBLIC_URL", "").rstrip("/")
    )

    default_tz: str = field(default_factory=lambda: _env("DEFAULT_TZ", "Europe/Moscow"))
    log_level: str = field(default_factory=lambda: _env("LOG_LEVEL", "INFO").upper())

    rag_engine: str = field(default_factory=lambda: _env("RAG_ENGINE", "auto").lower())
    embed_model: str = field(
        default_factory=lambda: _env(
            "EMBED_MODEL", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
        )
    )
    chunk_size: int = field(default_factory=lambda: _env_int("CHUNK_SIZE", 900))
    chunk_overlap: int = field(default_factory=lambda: _env_int("CHUNK_OVERLAP", 150))
    rag_top_k: int = field(default_factory=lambda: _env_int("RAG_TOP_K", 4))

    max_upload_mb: int = field(default_factory=lambda: _env_int("MAX_UPLOAD_MB", 20))

    @property
    def is_configured(self) -> bool:
        return bool(self.bot_token)

    @property
    def llm_ready(self) -> bool:
        return bool(self.llm_api_key or self.openrouter_api_key or self.groq_api_key)

    @property
    def resolved_db_path(self) -> Path:
        return self.db_path or (self.data_dir / "mita.db")

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def webapp_base_url(self) -> str:
        if self.webapp_public_url:
            return self.webapp_public_url
        if self.webapp_url:
            return self.webapp_url
        return f"http://{self.webapp_host}:{self.webapp_port}"

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.uploads_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()

__all__ = ["Settings", "settings", "BASE_DIR"]
