"""Проверка цепочки LLM-провайдеров: перебор, cooldown, офлайн-режим."""

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

for key in ("LLM_API_KEY", "OPENROUTER_API_KEY", "GROQ_API_KEY", "LLM_CHAIN"):
    os.environ.pop(key, None)

import httpx

import config
import llm_client
from llm_client import LLMClient

PASSED = 0
FAILED = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"PASS  {name}" + (f"  [{detail}]" if detail else ""))
    else:
        FAILED += 1
        print(f"FAIL  {name}  [{detail}]")


def reload_env(**env: str) -> None:
    for key in ("LLM_API_KEY", "OPENROUTER_API_KEY", "GROQ_API_KEY", "LLM_CHAIN"):
        os.environ.pop(key, None)
    os.environ.update(env)
    import importlib

    importlib.reload(config)
    importlib.reload(llm_client)


def fake_transport(responses: dict[str, httpx.Response], seen: list[str]):
    """Подменяет сеть: отдаёт ответ по имени провайдера из URL."""

    def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        seen.append(host)
        for marker, resp in responses.items():
            if marker in host:
                return resp
        return httpx.Response(500, json={"error": "unexpected host"})

    return httpx.MockTransport(handler)


def patch_client(client: LLMClient, transport) -> None:
    original = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = transport
        return original(*args, **kwargs)

    httpx.AsyncClient = factory
    client._restore = original


def restore_client(client: LLMClient) -> None:
    if hasattr(client, "_restore"):
        httpx.AsyncClient = client._restore


def ok_response(text: str = "ответ") -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": text}}]})


def main() -> int:
    print("=" * 60)
    print(" LLM: цепочка провайдеров")
    print("=" * 60)

    # 1. Без ключей — не готов, но не падает
    reload_env()
    client = LLMClient()
    check("без ключей ready=False", client.ready is False)
    res = asyncio.run(client.complete("привет"))
    check("без ключей возвращает ok=False", res.ok is False, res.error or "")

    # 2. Один ключ — один провайдер
    reload_env(OPENROUTER_API_KEY="or-key")
    client = LLMClient()
    check("только OPENROUTER → ready", client.ready is True)
    check("в цепочке 1 провайдер", [p.name for p in client.providers()] == ["openrouter"])

    # 3. Оба ключа — порядок по умолчанию
    reload_env(OPENROUTER_API_KEY="or-key", GROQ_API_KEY="gsk-key")
    client = LLMClient()
    check("два провайдера по умолчанию",
          [p.name for p in client.providers()] == ["openrouter", "groq"])

    # 4. LLM_CHAIN меняет порядок
    reload_env(OPENROUTER_API_KEY="or-key", GROQ_API_KEY="gsk-key", LLM_CHAIN="groq,openrouter")
    client = LLMClient()
    check("LLM_CHAIN задаёт порядок",
          [p.name for p in client.providers()] == ["groq", "openrouter"])

    # 5. Fallback: первый упал 429 → второй отвечает
    reload_env(OPENROUTER_API_KEY="or-key", GROQ_API_KEY="gsk-key")
    client = LLMClient()
    seen: list[str] = []
    patch_client(client, fake_transport(
        {"openrouter": httpx.Response(429, json={"error": "rate limit"}),
         "groq": ok_response("от Groq")}, seen))
    res = asyncio.run(client.complete("тест"))
    restore_client(client)
    check("fallback сработал на второй провайдер", res.ok and res.provider == "groq",
          f"provider={res.provider}")
    check("текст от второго провайдера", res.text == "от Groq", res.text)
    check("были запросы к обоим", len(seen) == 2, str(seen))

    # 6. Cooldown: упавший провайдер пропускается в следующий раз
    check("упавший в cooldown", "openrouter" in client._cooldown)
    seen2: list[str] = []
    patch_client(client, fake_transport({"groq": ok_response("снова Groq")}, seen2))
    res = asyncio.run(client.complete("тест 2"))
    restore_client(client)
    check("cooldown пропускает упавшего", seen2 == ["api.groq.com"], str(seen2))
    check("второй запрос тоже успешен", res.ok and res.provider == "groq")

    # 7. Первый рабочий — второй не дёргается
    client = LLMClient()
    seen3: list[str] = []
    patch_client(client, fake_transport(
        {"openrouter": ok_response("от OpenRouter"), "groq": ok_response("от Groq")}, seen3))
    res = asyncio.run(client.complete("тест 3"))
    restore_client(client)
    check("первый успешный останавливает перебор", res.provider == "openrouter" and len(seen3) == 1,
          f"{res.provider}, запросов {len(seen3)}")

    # 8. Оба упали — понятная ошибка, не исключение
    client = LLMClient()
    patch_client(client, fake_transport(
        {"openrouter": httpx.Response(500), "groq": httpx.Response(500)}, []))
    res = asyncio.run(client.complete("тест 4"))
    restore_client(client)
    check("оба упали → ok=False без исключения", res.ok is False)
    check("в ошибке видны оба провайдера",
          "openrouter" in (res.error or "") and "groq" in (res.error or ""), res.error or "")

    # 9. Явный клиент игнорирует цепочку (нужно тестам)
    explicit = LLMClient(api_key="k", base_url="https://x/v1", model="m")
    check("явный клиент = один провайдер", [p.name for p in explicit.providers()] == ["explicit"])

    # 10. Стиль попадает в system-промпт
    client = LLMClient()
    captured = {}

    def capture(request: httpx.Request) -> httpx.Response:
        import json as _json
        captured["messages"] = _json.loads(request.content)["messages"]
        captured["model"] = _json.loads(request.content)["model"]
        return ok_response("ок")

    patch_client(client, httpx.MockTransport(capture))
    asyncio.run(client.complete("вопрос", style="formal"))
    restore_client(client)
    check("стиль в system-промпте", "формально" in captured["messages"][0]["content"])
    check("модель уходит в payload", captured["model"] == config.settings.openrouter_model,
          captured["model"])

    print("\n" + "=" * 60)
    print(f"ИТОГО: {PASSED} passed, {FAILED} failed")
    print("=" * 60)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
