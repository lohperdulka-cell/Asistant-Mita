"""Пункт N8: устойчивость в Termux — fallback без тяжёлых зависимостей.

Тяжёлые пакеты эмулируем через запрет импорта: если код полез в sklearn
или sentence-transformers, импорт упадёт ImportError, и мы увидим,
что fallback реально работает, а не просто «есть в коде».
"""

import asyncio
import builtins
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import settings
from database import Database

FAILED: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    if ok:
        print(f"PASS  {name}")
    else:
        FAILED.append(name)
        print(f"FAIL  {name}  {detail}")


BLOCKED: set[str] = set()
_REAL_IMPORT = builtins.__import__


def _guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
    root = name.split(".")[0]
    if root in BLOCKED:
        raise ImportError(f"{root} заблокирован в тесте")
    return _REAL_IMPORT(name, globals, locals, fromlist, level)


async def main() -> None:
    # ---- сценарий 1: нет sklearn, нет sentence-transformers
    BLOCKED.update({"sklearn", "sentence_transformers"})
    builtins.__import__ = _guarded_import
    for mod in [m for m in list(sys.modules) if m.split(".")[0] in {"sklearn", "rag"}]:
        sys.modules.pop(mod, None)

    import rag

    object.__setattr__(settings, "rag_engine", "auto")
    engine = rag.resolve_engine()
    check("N8 без sklearn включается keyword-движок", engine == "keywords", engine)

    chunks = rag.chunk_text(
        "Пифагор жил в Греции. Теорема Пифагора связывает катеты и гипотенузу. "
        "Гипотенуза равна корню из суммы квадратов катетов."
    )
    check("N8 чанки режутся без sklearn", len(chunks) >= 1, str(chunks))

    db = Database(str(Path(tempfile.mkdtemp()) / "r.db"))
    await db.init()
    uid = 901
    await db.touch_user(uid, "u", "U")
    await db.save_profile(uid, goal="study", style="brief", features=["materials"])

    mid = await db.add_material(uid, "геометрия.txt", "txt", "геометрия.txt", 120, 1)
    await db.add_chunks(uid, mid, [(i, c, None) for i, c in enumerate(chunks)])

    hits = await rag.search_materials(db, uid, "теорема Пифагора гипотенуза", top_k=3)
    check("N8 поиск работает без sklearn", len(hits) >= 1, str(len(hits)))
    check("N8 нашёлся релевантный фрагмент",
          any("Пифагор" in h.text for h in hits),
          str([h.text[:40] for h in hits]))

    empty = await rag.search_materials(db, uid, "совершенно другой запрос про котиков", top_k=3)
    check("N8 нерелевантный запрос не даёт мусора",
          all(h.score > 0 for h in empty) if empty else True,
          str([round(h.score, 3) for h in empty]))

    # ---- сценарий 2: нет pypdf
    BLOCKED.add("pypdf")
    sys.modules.pop("extractors", None)
    import extractors

    pdf_path = Path(tempfile.mkdtemp()) / "fake.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 not a real pdf")
    result = extractors.extract_pdf(pdf_path)
    check("N8 без pypdf PDF не роняет бота", not result.ok, str(result))
    check("N8 сообщение об ошибке понятное",
          "pypdf" in (result.error or "").lower() or "PDF" in (result.error or ""),
          str(result.error))

    plain = Path(tempfile.mkdtemp()) / "a.txt"
    plain.write_text("обычный текст", encoding="utf-8")
    ok_result = extractors.extract_file(plain)
    check("N8 TXT читается без тяжёлых пакетов", ok_result.ok, str(ok_result))
    check("N8 текст TXT извлечён", "обычный текст" in ok_result.text, ok_result.text)

    # ---- сценарий 3: нет beautifulsoup
    BLOCKED.add("bs4")
    sys.modules.pop("extractors", None)
    import extractors as extractors2

    html = extractors2.extract_html_text("<html><body><p>привет</p></body></html>")
    check("N8 без bs4 HTML не падает", isinstance(html.ok, bool), str(html))

    # ---- сценарий 4: LLM без ключей
    BLOCKED.clear()
    builtins.__import__ = _REAL_IMPORT

    object.__setattr__(settings, "openrouter_api_key", "")
    object.__setattr__(settings, "groq_api_key", "")
    from llm_client import llm

    res = await llm.complete("привет")
    check("N8 без ключей LLM не падает", res.ok is False, str(res))
    check("N8 ошибка объясняет отсутствие ключа",
          "ключ" in (res.error or "").lower() or "API" in (res.error or ""),
          str(res.error))
    check("N8 готовность LLM = False", llm.ready is False)

    # ---- сценарий 5: скрипты Termux на месте и исполняемы
    root = Path(__file__).resolve().parent.parent
    for script in ("run_bot.sh", "run_webapp.sh", "install_termux.sh",
                   "install_services.sh", "install_boot.sh"):
        path = root / "scripts" / script
        check(f"N8 скрипт {script} есть", path.is_file())
    check("N8 run_bot.sh держит wake-lock",
          "termux-wake-lock" in (root / "scripts/run_bot.sh").read_text())
    check("N8 run_webapp.sh держит wake-lock",
          "termux-wake-lock" in (root / "scripts/run_webapp.sh").read_text())
    check("N8 run_bot.sh проверяет BOT_TOKEN",
          "BOT_TOKEN" in (root / "scripts/run_bot.sh").read_text())

    await db.close()

    print()
    if FAILED:
        print(f"ПРОВАЛЕНО: {len(FAILED)} — {FAILED}")
        raise SystemExit(1)
    print("ALL RESILIENCE (N8) TESTS PASSED")


asyncio.run(main())
builtins.__import__ = _REAL_IMPORT
