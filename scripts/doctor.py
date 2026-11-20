"""Проверка настроек «Миты»: что вписано, что работает, чего не хватает.

Запуск:  python scripts/doctor.py
Ничего не меняет, только читает .env и делает безопасные проверки.
"""

import asyncio
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OK, WARN, BAD = "  OK  ", " WARN ", " FAIL "


def line(status: str, name: str, detail: str = "") -> None:
    print(f"[{status}] {name}" + (f" — {detail}" if detail else ""))


def mask(value: str) -> str:
    if not value:
        return "пусто"
    if len(value) <= 10:
        return value[:2] + "…"
    return f"{value[:6]}…{value[-4:]} (длина {len(value)})"


def api_get(token: str, method: str) -> dict:
    url = f"https://api.telegram.org/bot{token}/{method}"
    with urllib.request.urlopen(url, timeout=25) as resp:
        return json.load(resp)


def api_post(url: str, payload: dict, key: str) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=45) as resp:
        return json.load(resp)


def main() -> int:
    problems = 0

    print("=" * 62)
    print(" Мита: проверка настроек")
    print("=" * 62)

    env_file = ROOT / ".env"
    if env_file.exists():
        line(OK, ".env найден", str(env_file))
    else:
        line(BAD, ".env не найден", f"создайте: cp .env.example {env_file}")
        problems += 1

    from config import settings

    print("\n-- Telegram --")
    if not settings.bot_token:
        line(BAD, "BOT_TOKEN", "не задан — бот не запустится")
        problems += 1
    else:
        line(OK, "BOT_TOKEN задан", mask(settings.bot_token))
        try:
            data = api_get(settings.bot_token, "getMe")
            me = data.get("result", {})
            line(OK, "токен принят Telegram", f"@{me.get('username')} ({me.get('first_name')})")
            if me.get("username") and not settings.bot_username:
                line(WARN, "BOT_USERNAME пуст", f"впишите BOT_USERNAME={me['username']}")
            elif settings.bot_username and me.get("username") != settings.bot_username:
                line(WARN, "BOT_USERNAME не совпадает",
                     f"в .env «{settings.bot_username}», у бота «{me.get('username')}»")
            else:
                line(OK, "BOT_USERNAME совпадает", f"@{settings.bot_username}")

            if me.get("supports_inline_queries"):
                line(OK, "inline-режим включён")
            else:
                line(WARN, "inline-режим ВЫКЛЮЧЕН",
                     "в @BotFather: /setinline → выберите бота → любой плейсхолдер")

            hook = api_get(settings.bot_token, "getWebhookInfo").get("result", {})
            if hook.get("url"):
                line(WARN, "установлен webhook", f"{hook['url']} — конфликтует с polling, удалите через /deletewebhook")
            else:
                line(OK, "webhook пуст", "polling будет работать")
        except urllib.error.HTTPError as exc:
            line(BAD, "токен отклонён Telegram", f"HTTP {exc.code} — проверьте токен в .env")
            problems += 1
        except urllib.error.URLError as exc:
            line(BAD, "нет связи с Telegram", str(exc.reason))
            problems += 1

    print("\n-- LLM --")
    from llm_client import LLMClient

    probe = LLMClient()
    chain = probe.providers()
    if not chain:
        line(WARN, "ключей LLM нет",
             "бот запустится, но умные ответы/RAG будут заглушкой; "
             "впишите OPENROUTER_API_KEY или GROQ_API_KEY")
    else:
        line(OK, "провайдеры в цепочке", " → ".join(p.name for p in chain))
        for provider in chain:
            line(OK, f"{provider.name}: настройки", f"{provider.base_url} · {provider.model}")
            try:
                resp = api_post(
                    f"{provider.base_url}/chat/completions",
                    {"model": provider.model,
                     "messages": [{"role": "user", "content": "ответь одним словом: работает"}],
                     "max_tokens": 16},
                    provider.api_key,
                )
                answer = resp["choices"][0]["message"]["content"].strip()
                line(OK, f"{provider.name}: отвечает", repr(answer[:40]))
            except urllib.error.HTTPError as exc:
                body = exc.read().decode(errors="replace")[:160]
                hint = {
                    401: "неверный ключ",
                    402: "кончились деньги на балансе",
                    403: "нет доступа к модели",
                    404: "проверьте адрес или имя модели",
                    429: "лимит запросов — бот переключится на следующего",
                }.get(exc.code, "ошибка")
                status = WARN if exc.code == 429 else BAD
                line(status, f"{provider.name}: не отвечает", f"HTTP {exc.code} ({hint}) {body}")
                if status is BAD:
                    problems += 1
            except Exception as exc:
                line(BAD, f"{provider.name}: недоступен", f"{type(exc).__name__}: {exc}")
                problems += 1

    print("\n-- Данные --")
    try:
        settings.ensure_dirs()
        line(OK, "DATA_DIR", str(settings.data_dir))
        line(OK, "БД", str(settings.resolved_db_path))

        async def check_db() -> None:
            from database import Database

            db = Database(settings.resolved_db_path)
            await db.init()
            await db.close()

        asyncio.run(check_db())
        line(OK, "БД открывается, схема применена")
    except Exception as exc:
        line(BAD, "проблема с БД", f"{type(exc).__name__}: {exc}")
        problems += 1

    print("\n-- RAG --")
    try:
        import rag

        engine = rag.resolve_engine()
        line(OK, "движок поиска", engine)
        if engine == "keywords":
            line(WARN, "только ключевые слова",
                 "поставьте RAG-пакеты: pip install -r requirements-rag.txt")
        elif engine == "tfidf":
            line(WARN, "TF-IDF", "не кросс-язычный: русский вопрос к английскому PDF не найдётся")
        for module, label in (("pypdf", "PDF"), ("docx", "DOCX"), ("bs4", "ссылки/HTML")):
            try:
                __import__(module)
                line(OK, label, f"{module} установлен")
            except ImportError:
                line(WARN, label, f"нет {module} — этот формат не читается")
    except Exception as exc:
        line(BAD, "RAG", f"{type(exc).__name__}: {exc}")
        problems += 1

    print("\n-- Mini App --")
    line(OK, "WEBAPP_HOST:PORT", f"{settings.webapp_host}:{settings.webapp_port}")
    if settings.webapp_public_url.startswith("https://"):
        line(OK, "WEBAPP_PUBLIC_URL", settings.webapp_public_url)
    elif settings.webapp_public_url:
        line(BAD, "WEBAPP_PUBLIC_URL без https",
             "Telegram открывает Web App только по https")
        problems += 1
    else:
        line(WARN, "WEBAPP_PUBLIC_URL пуст",
             "кнопка Mini App не появится; остальное работает")
    try:
        with urllib.request.urlopen(
            f"http://{settings.webapp_host}:{settings.webapp_port}/healthz", timeout=5
        ) as resp:
            line(OK, "сервер отвечает", resp.read().decode()[:40])
    except Exception:
        line(WARN, "сервер не запущен", "это нормально, если не поднимали: bash scripts/run_webapp.sh")

    print("\n" + "=" * 62)
    if problems:
        print(f" НАДО ИСПРАВИТЬ: {problems}. Смотрите строки [ FAIL ] выше.")
    else:
        print(" Критичных проблем нет. Можно запускать: bash scripts/run_bot.sh")
    print("=" * 62)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
