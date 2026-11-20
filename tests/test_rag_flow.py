import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp())
os.environ["DATA_DIR"] = str(TMP)
os.environ["RAG_ENGINE"] = "auto"
os.environ["MAX_UPLOAD_MB"] = "5"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandObject
from aiogram.types import Chat, Document, Message, User

import materials
import rag
from database import Database

CALLS = []
TEST_FILES = {}
UID = 42
USER = User(id=UID, is_bot=False, first_name="U")
CHAT = Chat(id=UID, type="private")


class FakeBot(Bot):
    async def __call__(self, method, request_timeout=None):
        CALLS.append(method)
        name = type(method).__name__
        if name == "SendMessage":
            return Message(
                message_id=1,
                date=0,
                chat=Chat(id=int(method.chat_id), type="private"),
                text=method.text or "",
                reply_markup=method.reply_markup,
            ).as_(self)
        if name == "GetMe":
            return User(id=1, is_bot=True, first_name="M", username="m")
        raise RuntimeError(name)

    async def download(self, file, destination, **kwargs):
        src = Path(TEST_FILES[Path(destination).suffix])
        Path(destination).write_bytes(src.read_bytes())
        return destination


def msg(text=None, doc=None, bot=None):
    return Message(
        message_id=1, date=0, chat=CHAT, from_user=USER, text=text, document=doc
    ).as_(bot)


async def main():
    from fpdf import FPDF

    pdf_path = TMP / "lecture.pdf"
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)
    pdf.multi_cell(
        0,
        6,
        "Photosynthesis converts light energy into chemical energy. "
        "It happens in chloroplasts. Light reactions occur on thylakoid membranes. "
        "The Calvin cycle happens in the stroma.",
    )
    pdf.output(str(pdf_path))
    TEST_FILES[".pdf"] = pdf_path

    import docx

    document = docx.Document()
    document.add_paragraph("SQLite is an embedded relational database.")
    document.add_paragraph("It supports WAL mode and FTS5 full-text search. Indexes speed up queries.")
    docx_path = TMP / "notes.docx"
    document.save(str(docx_path))
    TEST_FILES[".docx"] = docx_path

    txt_path = TMP / "plain.txt"
    txt_path.write_text(
        "Заметки по Python: asyncio позволяет писать конкурентный код. "
        "aiogram 3 использует роутеры и фильтры. " * 3,
        encoding="utf-8",
    )
    TEST_FILES[".txt"] = txt_path

    db = Database(TMP / "t.db")
    await db.init()
    bot = FakeBot("1:T", default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    await db.touch_user(UID, "u", "U")
    await db.save_profile(UID, goal="Учёба", style="brief", timezone="Europe/Moscow", onboarded=True)

    CALLS.clear()
    await materials.upload_help(msg("/upload", None, bot))
    assert "Как добавить материалы" in CALLS[-1].text
    print("upload help OK")

    for index, (name, path) in enumerate(
        [("lecture.pdf", pdf_path), ("notes.docx", docx_path), ("plain.txt", txt_path)], start=1
    ):
        CALLS.clear()
        doc = Document(
            file_id=f"f{index}",
            file_unique_id=f"u{index}",
            file_name=name,
            file_size=path.stat().st_size,
        )
        await materials.on_document(msg(doc=doc, bot=bot), bot, db)
        replies = [c.text for c in CALLS if type(c).__name__ == "SendMessage"]
        print(f"{name} replies:", replies)
        assert any(f"материал #{index}" in text for text in replies), replies
        print(f"{name} ingest OK")

    CALLS.clear()
    doc = Document(file_id="f9", file_unique_id="u9", file_name="photo.jpg", file_size=100)
    await materials.on_document(msg(doc=doc, bot=bot), bot, db)
    assert "не поддерживается" in CALLS[-1].text
    print("unsupported format OK")

    CALLS.clear()
    await materials.materials_list(msg("/materials", None, bot), db)
    listing = CALLS[-1].text
    print(listing)
    assert "материалы</b> (3)" in listing
    assert "lecture.pdf" in listing and "notes.docx" in listing
    print("materials list OK")

    import httpx
    import llm_client

    captured = {}

    def handler(request):
        payload = json.loads(request.content)
        captured["messages"] = payload["messages"]
        captured["auth"] = request.headers.get("authorization")
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "Фотосинтез идёт в хлоропластах [1]."}}]}
        )

    llm_client.llm.api_key = "test-key"
    llm_client.llm.base_url = "https://fake.api/v1"
    real_client = httpx.AsyncClient

    class PatchedClient(real_client):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(*args, **kwargs)

    httpx.AsyncClient = PatchedClient
    rag.reset_engine_cache()

    CALLS.clear()
    await materials.on_text(msg("найди в моих материалах photosynthesis и объясни?", bot=bot), db)
    reply = CALLS[-1].text
    print("RAG reply:", reply[:300])
    assert "хлоропласт" in reply
    assert "RAG-контекст" in reply
    assert "Источники" in reply
    assert captured["auth"] == "Bearer test-key"
    system_prompt = captured["messages"][0]["content"]
    user_prompt = captured["messages"][-1]["content"]
    assert "ИСКЛЮЧИТЕЛЬНО" in system_prompt
    assert "Photosynthesis" in user_prompt
    print("RAG answer grounded in material OK")

    CALLS.clear()
    await materials.forget(
        msg("/forget 1", None, bot), CommandObject(command="forget", args="1"), db
    )
    assert "удалён" in CALLS[-1].text
    assert len(await db.list_materials(UID)) == 2
    chunks = await db.user_chunks(UID)
    assert all(ch["material_id"] != 1 for ch in chunks)
    print("forget OK, remaining chunks:", len(chunks))

    CALLS.clear()
    await materials.on_text(msg("что такое photosynthesis в материалах?", bot=bot), db)
    assert "ничего подходящего" in CALLS[-1].text
    print("post-forget RAG OK")

    httpx.AsyncClient = real_client
    await db.close()
    await bot.session.close()
    print("ALL MATERIALS/RAG FLOW TESTS PASSED")


asyncio.run(main())
