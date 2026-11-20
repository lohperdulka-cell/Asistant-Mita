"""Извлечение текста из загруженных материалов.

Каждый парсер изолирован и при отсутствии библиотеки возвращает понятную
ошибку вместо падения бота (важно для Termux).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".txt", ".md", ".markdown", ".rst", ".csv", ".html", ".htm"}
MAX_CHARS = 400_000


@dataclass
class ExtractResult:
    text: str
    error: str | None = None

    @property
    def ok(self) -> bool:
        return bool(self.text.strip()) and self.error is None


def clean_text(raw: str) -> str:
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()[:MAX_CHARS]


def extract_pdf(path: Path) -> ExtractResult:
    try:
        from pypdf import PdfReader
    except ImportError:
        return ExtractResult(
            "",
            "PDF не поддерживается: не установлен pypdf.\n"
            "Установите: pip install pypdf",
        )
    try:
        reader = PdfReader(str(path))
        pages = []
        for index, page in enumerate(reader.pages, start=1):
            try:
                pages.append(page.extract_text() or "")
            except Exception as exc:
                log.warning("PDF страница %s: %s", index, exc)
        text = clean_text("\n\n".join(pages))
        if not text:
            return ExtractResult(
                "", "В PDF нет текстового слоя (возможно, скан). Нужен OCR."
            )
        return ExtractResult(text)
    except Exception as exc:
        log.warning("Ошибка чтения PDF: %s", exc)
        return ExtractResult("", f"Не удалось прочитать PDF: {exc}")


def extract_docx(path: Path) -> ExtractResult:
    try:
        import docx
    except ImportError:
        return ExtractResult(
            "",
            "DOCX не поддерживается: не установлен python-docx.\n"
            "Установите: pip install python-docx",
        )
    try:
        document = docx.Document(str(path))
        parts = [para.text for para in document.paragraphs]
        for table in document.tables:
            for row in table.rows:
                parts.append(" | ".join(cell.text.strip() for cell in row.cells))
        text = clean_text("\n".join(parts))
        if not text:
            return ExtractResult("", "Документ пуст.")
        return ExtractResult(text)
    except Exception as exc:
        log.warning("Ошибка чтения DOCX: %s", exc)
        return ExtractResult("", f"Не удалось прочитать DOCX: {exc}")


def extract_plain(path: Path) -> ExtractResult:
    for encoding in ("utf-8", "cp1251", "latin-1"):
        try:
            return ExtractResult(clean_text(path.read_text(encoding=encoding)))
        except UnicodeDecodeError:
            continue
        except OSError as exc:
            return ExtractResult("", f"Не удалось прочитать файл: {exc}")
    return ExtractResult("", "Не удалось определить кодировку файла.")


def extract_html_text(raw: str) -> ExtractResult:
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return ExtractResult(clean_text(re.sub(r"<[^>]+>", " ", raw)))
    try:
        soup = BeautifulSoup(raw, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header", "noscript"]):
            tag.decompose()
        return ExtractResult(clean_text(soup.get_text("\n")))
    except Exception as exc:
        return ExtractResult("", f"Не удалось разобрать HTML: {exc}")


def extract_file(path: Path) -> ExtractResult:
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        return ExtractResult(
            "",
            f"Формат {suffix or 'без расширения'} не поддерживается.\n"
            "Можно: PDF, DOCX, TXT, MD, CSV, HTML.",
        )
    if suffix == ".pdf":
        return extract_pdf(path)
    if suffix == ".docx":
        return extract_docx(path)
    if suffix in {".html", ".htm"}:
        try:
            return extract_html_text(path.read_text(encoding="utf-8", errors="replace"))
        except OSError as exc:
            return ExtractResult("", str(exc))
    return extract_plain(path)


async def fetch_url_text(url: str, timeout: int = 25) -> ExtractResult:
    import httpx

    if not url.startswith(("http://", "https://")):
        return ExtractResult("", "Ссылка должна начинаться с http:// или https://")
    try:
        async with httpx.AsyncClient(
            timeout=timeout, follow_redirects=True,
            headers={"User-Agent": "MitaBot/1.0 (+personal assistant)"},
        ) as client:
            response = await client.get(url)
        if response.status_code >= 400:
            return ExtractResult("", f"Сайт ответил {response.status_code}")
        content_type = response.headers.get("content-type", "")
        if "pdf" in content_type:
            return ExtractResult("", "PDF по ссылке: скачайте файл и отправьте боту документом.")
        if "html" in content_type or not content_type:
            return extract_html_text(response.text)
        return ExtractResult(clean_text(response.text))
    except Exception as exc:
        log.warning("Ошибка загрузки ссылки %s: %s", url, exc)
        return ExtractResult("", f"Не удалось загрузить ссылку: {exc}")


__all__ = [
    "ExtractResult",
    "extract_file",
    "fetch_url_text",
    "clean_text",
    "SUPPORTED_EXTENSIONS",
]
