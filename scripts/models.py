"""Выбор бесплатной модели OpenRouter без правки .env вручную.

    python scripts/models.py            # список бесплатных моделей
    python scripts/models.py groq       # модели Groq
    python scripts/models.py use <id>   # прописать модель в .env

Ключ к модели не привязан: модель передаётся в теле каждого запроса,
поэтому её можно менять хоть на каждый запрос.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env"
OPENROUTER_MODELS = "https://openrouter.ai/api/v1/models"
GROQ_MODELS = "https://api.groq.com/openai/v1/models"


def fetch(url: str, api_key: str = "") -> dict:
    req = urllib.request.Request(url)
    if api_key:
        req.add_header("Authorization", f"Bearer {api_key}")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def list_openrouter() -> list[dict]:
    data = fetch(OPENROUTER_MODELS)["data"]
    free = [m for m in data if m["id"].endswith(":free")]
    free.sort(key=lambda m: (m.get("context_length") or 0), reverse=True)
    return free


def list_groq() -> list[dict]:
    key = os.environ.get("GROQ_API_KEY", "")
    if not key:
        key = read_env("GROQ_API_KEY")
    if not key:
        print("GROQ_API_KEY не задан — список моделей Groq требует ключа.")
        print("Смотрите https://console.groq.com/docs/models")
        return []
    return fetch(GROQ_MODELS, key).get("data", [])


def read_env(name: str) -> str:
    if not ENV_FILE.exists():
        return ""
    for raw in ENV_FILE.read_text().splitlines():
        if raw.startswith(f"{name}="):
            return raw.split("=", 1)[1].strip()
    return ""


def write_env(name: str, value: str) -> None:
    text = ENV_FILE.read_text() if ENV_FILE.exists() else ""
    pattern = re.compile(rf"^{re.escape(name)}=.*$", re.M)
    if pattern.search(text):
        text = pattern.sub(f"{name}={value}", text, count=1)
    else:
        text = text.rstrip("\n") + f"\n{name}={value}\n"
    ENV_FILE.write_text(text)


def cmd_list_openrouter() -> int:
    models = list_openrouter()
    print(f"Бесплатные модели OpenRouter ({len(models)}):\n")
    print(f"{'id':52} {'контекст':>10}  описание")
    print("-" * 100)
    for m in models:
        ctx = m.get("context_length") or 0
        name = (m.get("name") or "").replace("(free)", "").strip()
        print(f"{m['id']:52} {ctx:>10}  {name[:40]}")
    current = read_env("OPENROUTER_MODEL")
    print(f"\nСейчас в .env: {current or '(не задано)'}")
    print("\nВыбрать: python scripts/models.py use <id>")
    print("Роутер (сам выбирает свободную): python scripts/models.py use openrouter/free")
    return 0


def cmd_list_groq() -> int:
    models = list_groq()
    if not models:
        return 1
    print(f"Модели Groq ({len(models)}):\n")
    for m in sorted(models, key=lambda x: x.get("id", "")):
        ctx = m.get("context_window") or m.get("context_length") or 0
        print(f"{m.get('id', '?'):52} {ctx:>10}")
    print(f"\nСейчас в .env: {read_env('GROQ_MODEL') or '(не задано)'}")
    print("\nВыбрать: python scripts/models.py use <id> --groq")
    return 0


def cmd_use(model_id: str, groq: bool) -> int:
    if model_id == "openrouter/free":
        var = "OPENROUTER_MODEL"
    elif model_id.endswith(":free"):
        var = "OPENROUTER_MODEL"
    else:
        var = "GROQ_MODEL" if groq else "OPENROUTER_MODEL"
    if not ENV_FILE.exists():
        print(f"Нет {ENV_FILE} — скопируйте .env.example в .env")
        return 1
    write_env(var, model_id)
    print(f"Записано: {var}={model_id}")
    print("Перезапустите бота, чтобы применилось.")
    return 0


def main(argv: list[str]) -> int:
    if len(argv) >= 3 and argv[1] == "use":
        return cmd_use(argv[2], groq="--groq" in argv)
    if len(argv) >= 2 and argv[1] == "groq":
        return cmd_list_groq()
    return cmd_list_openrouter()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
