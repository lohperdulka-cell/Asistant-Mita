"""RAG: чанки, эмбеддинги и поиск с каскадным fallback.

Движки по убыванию качества:
1. embeddings  — sentence-transformers (тяжёлый, ~500 МБ, опционально);
2. tfidf       — scikit-learn TfidfVectorizer (лёгкий, ~30 МБ);
3. keywords    — чистый Python, взвешенный BM25-подобный поиск.

Выбор: RAG_ENGINE=auto пробует по порядку и запоминает доступный движок.
Это не переобучение модели: материалы лишь подкладываются в промпт.
"""

from __future__ import annotations

import logging
import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Sequence

from config import settings

log = logging.getLogger(__name__)

WORD_RE = re.compile(r"[\w\-]{2,}", re.UNICODE)
SENTENCE_SPLIT = re.compile(r"(?<=[.!?…])\s+|\n+")

STOPWORDS = {
    "и", "в", "во", "не", "что", "он", "на", "я", "с", "со", "как", "а", "то",
    "все", "она", "так", "его", "но", "да", "ты", "к", "у", "же", "вы", "за",
    "бы", "по", "только", "ее", "мне", "было", "вот", "от", "меня", "еще",
    "нет", "о", "из", "ему", "теперь", "когда", "даже", "ну", "вдруг", "ли",
    "если", "уже", "или", "ни", "быть", "был", "него", "до", "вас", "нибудь",
    "опять", "уж", "вам", "ведь", "там", "потом", "себя", "ничего", "ей",
    "может", "они", "тут", "где", "есть", "надо", "ней", "для", "мы", "тебя",
    "их", "чем", "была", "сам", "чтоб", "без", "будто", "чего", "раз", "тоже",
    "себе", "под", "будет", "ж", "тогда", "кто", "этот", "того", "потому",
    "этого", "какой", "совсем", "ним", "здесь", "этом", "один", "почти",
    "мой", "тем", "чтобы", "нее", "сейчас", "были", "куда", "зачем", "всех",
    "никогда", "можно", "при", "наконец", "два", "об", "другой", "хоть",
    "после", "над", "больше", "тот", "через", "эти", "нас", "про", "всего",
    "них", "какая", "много", "разве", "три", "эту", "моя", "впрочем", "хорошо",
    "свою", "этой", "перед", "иногда", "лучше", "чуть", "том", "нельзя",
    "такой", "им", "более", "всегда", "конечно", "всю", "между", "the", "and",
    "for", "are", "but", "not", "you", "all", "can", "her", "was", "one",
    "our", "out", "his", "has", "had", "how", "its", "who", "did", "yes",
    "this", "that", "with", "from", "have", "they", "will", "what", "when",
    "there", "their", "about", "would", "which", "these", "such", "into",
}

RAG_NOTICE = (
    "ℹ️ Ответ построен на <b>твоих загруженных материалах</b> (RAG-контекст). "
    "Модель не переобучается — я просто подставляю найденные фрагменты в запрос."
)


@dataclass
class ChunkHit:
    chunk_id: int
    material_id: int
    title: str
    text: str
    score: float


def tokenize(text: str) -> list[str]:
    words = [w.lower() for w in WORD_RE.findall(text)]
    return [w for w in words if w not in STOPWORDS and len(w) > 1]


# ------------------------------------------------------------------- чанкинг


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in SENTENCE_SPLIT.split(text) if s.strip()]


def chunk_text(
    text: str, size: int | None = None, overlap: int | None = None
) -> list[str]:
    """Режет текст по границам предложений с перекрытием."""
    size = size or settings.chunk_size
    overlap = overlap if overlap is not None else settings.chunk_overlap
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= size:
        return [text]

    sentences = split_sentences(text)
    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        if len(sentence) > size:
            # очень длинное «предложение» — режем по словам
            for piece in _split_long(sentence, size):
                if current:
                    chunks.append(current.strip())
                    current = ""
                chunks.append(piece)
            continue
        if current and len(current) + len(sentence) + 1 > size:
            chunks.append(current.strip())
            tail = current[-overlap:] if overlap > 0 else ""
            current = (tail + " " + sentence).strip() if tail else sentence
        else:
            current = f"{current} {sentence}".strip() if current else sentence
    if current.strip():
        chunks.append(current.strip())
    return [c for c in chunks if c.strip()]


def _split_long(text: str, size: int) -> list[str]:
    words = text.split()
    out, current = [], ""
    for word in words:
        if current and len(current) + len(word) + 1 > size:
            out.append(current.strip())
            current = word
        else:
            current = f"{current} {word}".strip() if current else word
    if current.strip():
        out.append(current.strip())
    return out


# ------------------------------------------------------------------ движки


class BaseIndex:
    name = "base"

    def __init__(self, rows: Sequence) -> None:
        self.rows = list(rows)

    def search(self, query: str, top_k: int) -> list[ChunkHit]:
        raise NotImplementedError


class KeywordIndex(BaseIndex):
    """BM25-подобный поиск без зависимостей."""

    name = "keywords"

    def __init__(self, rows: Sequence) -> None:
        super().__init__(rows)
        self.docs = [Counter(tokenize(row["text"])) for row in self.rows]
        self.lengths = [sum(doc.values()) or 1 for doc in self.docs]
        self.avg_len = sum(self.lengths) / len(self.lengths) if self.lengths else 1
        df: Counter = Counter()
        for doc in self.docs:
            df.update(doc.keys())
        total = len(self.docs) or 1
        self.idf = {
            term: math.log(1 + (total - freq + 0.5) / (freq + 0.5))
            for term, freq in df.items()
        }

    def search(self, query: str, top_k: int) -> list[ChunkHit]:
        terms = tokenize(query)
        if not terms:
            return []
        k1, b = 1.5, 0.75
        scored: list[tuple[float, int]] = []
        for index, doc in enumerate(self.docs):
            score = 0.0
            length = self.lengths[index]
            for term in terms:
                freq = doc.get(term, 0)
                if not freq:
                    continue
                idf = self.idf.get(term, 0.0)
                score += idf * (freq * (k1 + 1)) / (
                    freq + k1 * (1 - b + b * length / self.avg_len)
                )
            if score > 0:
                scored.append((score, index))
        scored.sort(reverse=True)
        return [self._hit(index, score) for score, index in scored[:top_k]]

    def _hit(self, index: int, score: float) -> ChunkHit:
        row = self.rows[index]
        return ChunkHit(
            chunk_id=int(row["id"]),
            material_id=int(row["material_id"]),
            title=row["title"],
            text=row["text"],
            score=float(score),
        )


class TfidfIndex(BaseIndex):
    name = "tfidf"

    def __init__(self, rows: Sequence) -> None:
        super().__init__(rows)
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.metrics.pairwise import linear_kernel

        self._linear_kernel = linear_kernel
        self.vectorizer = TfidfVectorizer(
            lowercase=True, ngram_range=(1, 2), min_df=1, sublinear_tf=True
        )
        self.matrix = self.vectorizer.fit_transform([row["text"] for row in self.rows])

    def search(self, query: str, top_k: int) -> list[ChunkHit]:
        vector = self.vectorizer.transform([query])
        scores = self._linear_kernel(vector, self.matrix).ravel()
        order = scores.argsort()[::-1][:top_k]
        hits = []
        for index in order:
            score = float(scores[index])
            if score <= 0:
                continue
            row = self.rows[int(index)]
            hits.append(
                ChunkHit(
                    chunk_id=int(row["id"]),
                    material_id=int(row["material_id"]),
                    title=row["title"],
                    text=row["text"],
                    score=score,
                )
            )
        return hits


class EmbeddingIndex(BaseIndex):
    name = "embeddings"

    def __init__(self, rows: Sequence) -> None:
        super().__init__(rows)
        import numpy as np
        from sentence_transformers import SentenceTransformer

        self._np = np
        self.model = SentenceTransformer(settings.embed_model)

        stored = [row["tokens"] for row in self.rows]
        if all(stored):
            import json

            vectors = [json.loads(item) for item in stored]
            self.matrix = np.array(vectors, dtype="float32")
        else:
            self.matrix = self.model.encode(
                [row["text"] for row in self.rows], convert_to_numpy=True
            )
        norms = np.linalg.norm(self.matrix, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        self.matrix = self.matrix / norms

    def search(self, query: str, top_k: int) -> list[ChunkHit]:
        vector = self.model.encode([query], convert_to_numpy=True)[0]
        norm = self._np.linalg.norm(vector) or 1.0
        scores = self.matrix @ (vector / norm)
        order = scores.argsort()[::-1][:top_k]
        return [
            ChunkHit(
                chunk_id=int(self.rows[int(i)]["id"]),
                material_id=int(self.rows[int(i)]["material_id"]),
                title=self.rows[int(i)]["title"],
                text=self.rows[int(i)]["text"],
                score=float(scores[i]),
            )
            for i in order
            if float(scores[i]) > 0
        ]


ENGINES = {"embeddings": EmbeddingIndex, "tfidf": TfidfIndex, "keywords": KeywordIndex}
_active_engine: str | None = None


def resolve_engine(preferred: str | None = None) -> str:
    """Определяет доступный движок, кэшируя результат."""
    global _active_engine
    if _active_engine:
        return _active_engine

    wanted = (preferred or settings.rag_engine or "auto").lower()
    if wanted in ENGINES:
        _active_engine = wanted
        return wanted

    for candidate in ("embeddings", "tfidf"):
        module = "sentence_transformers" if candidate == "embeddings" else "sklearn"
        try:
            __import__(module)
            _active_engine = candidate
            log.info("RAG-движок: %s", candidate)
            return candidate
        except Exception as exc:
            log.info("RAG-движок %s недоступен (%s), пробую следующий", candidate, exc)

    _active_engine = "keywords"
    log.info("RAG-движок: keywords (fallback)")
    return _active_engine


def reset_engine_cache() -> None:
    global _active_engine
    _active_engine = None


def build_index(rows: Sequence) -> BaseIndex:
    engine = resolve_engine()
    if not rows:
        return KeywordIndex([])
    for candidate in [engine, "tfidf", "keywords"]:
        if candidate not in ENGINES:
            continue
        try:
            return ENGINES[candidate](rows)
        except Exception as exc:
            log.warning("Движок %s не смог построить индекс: %s", candidate, exc)
    return KeywordIndex(rows)


async def search_materials(db, user_id: int, query: str, top_k: int | None = None) -> list[ChunkHit]:
    rows = await db.user_chunks(user_id)
    if not rows:
        return []
    index = build_index(rows)
    hits = index.search(query, top_k or settings.rag_top_k)
    # Отбрасываем совсем слабые совпадения: у косинуса шкала 0..1,
    # у BM25 — безразмерная, поэтому порог только для векторных движков.
    if index.name in {"tfidf", "embeddings"}:
        hits = [hit for hit in hits if hit.score >= 0.02]
    return hits


def build_context(hits: list[ChunkHit], max_chars: int = 6000) -> str:
    parts, used = [], 0
    for number, hit in enumerate(hits, start=1):
        block = f"[{number}] «{hit.title}»:\n{hit.text}"
        if used + len(block) > max_chars:
            break
        parts.append(block)
        used += len(block)
    return "\n\n".join(parts)


async def answer_with_context(
    db, user_row, question: str, top_k: int | None = None
) -> tuple[str, list[ChunkHit], str | None]:
    """Поиск по материалам + ответ LLM. Возвращает (текст, хиты, ошибка)."""
    from llm_client import llm

    user_id = user_row["user_id"]
    hits = await search_materials(db, user_id, question, top_k)
    if not hits:
        return (
            "🤷 В твоих материалах я ничего подходящего не нашла.\n"
            "Загрузи документы: /upload — или переформулируй вопрос.",
            [],
            None,
        )

    context = build_context(hits)
    style = user_row["style"] or "brief"
    system = (
        "Ты — Мита, личный ассистент. Отвечай, опираясь ИСКЛЮЧИТЕЛЬНО на "
        "фрагменты материалов пользователя ниже. Если в них нет ответа — "
        "скажи об этом прямо и не выдумывай. Ссылайся на номера фрагментов "
        "в квадратных скобках."
    )
    prompt = f"Материалы:\n{context}\n\nВопрос: {question}"

    result = await llm.complete(prompt, system=system, style=style)
    if not result.ok:
        fallback = "\n\n".join(
            f"[{i}] «{hit.title}»\n{hit.text[:600]}" for i, hit in enumerate(hits, start=1)
        )
        return (
            "⚠️ LLM недоступна, поэтому показываю найденные фрагменты как есть:\n\n"
            + fallback,
            hits,
            result.error,
        )
    return f"{result.text}\n\n{RAG_NOTICE}", hits, None


__all__ = [
    "chunk_text",
    "tokenize",
    "resolve_engine",
    "reset_engine_cache",
    "build_index",
    "search_materials",
    "answer_with_context",
    "build_context",
    "ChunkHit",
    "RAG_NOTICE",
]
