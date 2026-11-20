"""Разбор времени для /remind: regex-правила + LLM-fallback.

Правила понимают русский естественный язык: «через 10 минут», «через
полчаса», «завтра в 9:00», «в пятницу в 18:30», «25.12 в 10:00».
Возвращается момент в UTC и остаток строки — текст напоминания.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from dateutil import parser as date_parser

log = logging.getLogger(__name__)

WEEKDAYS = {
    "понедельник": 0, "пн": 0,
    "вторник": 1, "вт": 1,
    "среда": 2, "среду": 2, "ср": 2,
    "четверг": 3, "чт": 3,
    "пятница": 4, "пятницу": 4, "пт": 4,
    "суббота": 5, "субботу": 5, "сб": 5,
    "воскресенье": 6, "вс": 6,
}

NUM_WORDS = {
    "полтора": 1.5,
    "один": 1, "одну": 1, "два": 2, "две": 2, "три": 3, "четыре": 4,
    "пять": 5, "шесть": 6, "семь": 7, "восемь": 8, "девять": 9, "десять": 10,
    "пятнадцать": 15, "двадцать": 20, "тридцать": 30, "сорок": 40,
}

TIME_TOKEN = r"(\d{1,2})(?:[:.](\d{2}))?"

REL_RE = re.compile(
    r"\bчерез\s+(?:(?P<word>полчаса|пол\s?часа|полтора\s+часа)|"
    r"(?P<num>\d+(?:[.,]\d+)?|" + "|".join(NUM_WORDS) + r")\s*"
    r"(?P<unit>сек\w*|минут\w*|мин\b|час\w*|день|дня|дней|сут\w*|недел\w*))",
    re.IGNORECASE,
)

DAY_WORD_RE = re.compile(r"\b(?P<word>послезавтра|завтра|сегодня)\b", re.IGNORECASE)
WEEKDAY_RE = re.compile(
    r"\b(?:в|во)\s+(?P<wd>" + "|".join(WEEKDAYS) + r")\b", re.IGNORECASE
)
DATE_RE = re.compile(r"\b(?P<d>\d{1,2})\.(?P<m>\d{1,2})(?:\.(?P<y>\d{2,4}))?\b")
TIME_RE = re.compile(r"\b(?:в|во)\s+" + TIME_TOKEN + r"\b", re.IGNORECASE)
LOOSE_TIME_RE = re.compile(r"\b" + TIME_TOKEN + r"\b", re.IGNORECASE)


@dataclass
class ParsedTime:
    when_utc: datetime
    rest: str
    source: str


def _num(raw: str) -> float | None:
    raw = raw.strip().lower().replace(",", ".")
    if raw in NUM_WORDS:
        return NUM_WORDS[raw]
    try:
        return float(raw)
    except ValueError:
        return None


def _unit_seconds(unit: str) -> int | None:
    unit = unit.lower()
    if unit.startswith("сек"):
        return 1
    if unit.startswith("мин"):
        return 60
    if unit.startswith("час"):
        return 3600
    if unit.startswith(("день", "дня", "дней", "сут")):
        return 86400
    if unit.startswith("недел"):
        return 7 * 86400
    return None


def _strip(text: str, spans: list[tuple[int, int]]) -> str:
    if not spans:
        return text.strip(" ,.;—-")
    spans.sort()
    result, cursor = [], 0
    for start, end in spans:
        result.append(text[cursor:start])
        cursor = end
    result.append(text[cursor:])
    cleaned = " ".join(" ".join(result).split())
    return cleaned.strip(" ,.;—-")


def _at(hour: int, minute: int, base: datetime) -> datetime | None:
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return base.replace(hour=hour, minute=minute, second=0, microsecond=0)


def parse_time(text: str, tz_name: str, now: datetime | None = None) -> ParsedTime | None:
    """Пытается вытащить время из текста. Возвращает UTC-момент и остаток."""
    if not text or not text.strip():
        return None
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo("UTC")
    now_local = (now or datetime.now(timezone.utc)).astimezone(tz)

    # 1. Относительное: «через 10 минут»
    match = REL_RE.search(text)
    if match:
        if match.group("word"):
            word = match.group("word").lower().replace(" ", "")
            seconds = 5400 if "полтора" in word else 1800
        else:
            value = _num(match.group("num"))
            unit = _unit_seconds(match.group("unit"))
            if value is None or unit is None:
                return None
            seconds = int(value * unit)
        seconds = max(seconds, 60)
        when = now_local + timedelta(seconds=seconds)
        return ParsedTime(when.astimezone(timezone.utc), _strip(text, [match.span()]), "relative")

    spans: list[tuple[int, int]] = []
    target_date = now_local.date()

    day_word = DAY_WORD_RE.search(text)
    if day_word:
        word = day_word.group("word").lower()
        if word == "завтра":
            target_date += timedelta(days=1)
        elif word == "послезавтра":
            target_date += timedelta(days=2)
        spans.append(day_word.span())

    weekday = WEEKDAY_RE.search(text)
    if weekday:
        wd = WEEKDAYS[weekday.group("wd").lower()]
        delta = (wd - now_local.weekday()) % 7
        if delta == 0 and not day_word:
            delta = 7  # «в понедельник» в понедельник = следующий
        target_date = now_local.date() + timedelta(days=delta)
        spans.append(weekday.span())

    date_match = DATE_RE.search(text)
    if date_match:
        day = int(date_match.group("d"))
        month = int(date_match.group("m"))
        year_raw = date_match.group("y")
        year = now_local.year
        if year_raw:
            year = int(year_raw) + (2000 if len(year_raw) == 2 else 0)
        try:
            target_date = target_date.replace(year=year, month=month, day=day)
        except ValueError:
            return None
        spans.append(date_match.span())

    hour = minute = None
    time_match = TIME_RE.search(text)
    if time_match:
        hour, minute = int(time_match.group(1)), int(time_match.group(2) or 0)
        spans.append(time_match.span())
    else:
        # время без предлога: "завтра 9:00" или "19:30 ужин".
        # Пропускаем совпадения, попавшие внутрь уже найденной даты (25.12.2027).
        for loose in LOOSE_TIME_RE.finditer(text):
            start, end = loose.span()
            if any(start < span_end and span_start < end for span_start, span_end in spans):
                continue
            hour, minute = int(loose.group(1)), int(loose.group(2) or 0)
            spans.append(loose.span())
            break

    if hour is None:
        return None
    if minute is None:
        minute = 0

    base = now_local.replace(
        year=target_date.year, month=target_date.month, day=target_date.day
    )
    when = _at(hour, minute, base)
    if when is None:
        return None
    if not day_word and not date_match and not weekday and when <= now_local:
        when += timedelta(days=1)
    if when <= now_local:
        when += timedelta(days=1)
    return ParsedTime(when.astimezone(timezone.utc), _strip(text, spans), "absolute")


async def parse_time_llm(text: str, tz_name: str, now: datetime | None = None) -> ParsedTime | None:
    """Fallback: просит LLM вернуть ISO-8601. Тихо возвращает None при неудаче."""
    from llm_client import llm

    if not llm.ready:
        return None
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo("UTC")
    now_local = (now or datetime.now(timezone.utc)).astimezone(tz)
    prompt = (
        f"Сейчас: {now_local.strftime('%Y-%m-%d %H:%M')} ({tz_name}).\n"
        f"Текст: {text!r}\n"
        "Верни только дату-время в формате YYYY-MM-DD HH:MM (локальное время, "
        "не раньше текущего момента) и через символ | текст напоминания без времени. "
        "Если времени нет — ответь NONE."
    )
    result = await llm.complete(prompt, style=None, max_tokens=60)
    if not result.ok:
        return None
    raw = result.text.strip().strip("`")
    if "NONE" in raw.upper():
        return None
    parts = [part.strip() for part in raw.split("|", 1)]
    try:
        when_local = date_parser.parse(parts[0], dayfirst=True)
    except (ValueError, OverflowError):
        return None
    if when_local.tzinfo is None:
        when_local = when_local.replace(tzinfo=tz)
    if when_local <= now_local:
        return None
    rest = parts[1] if len(parts) > 1 and parts[1] else text
    return ParsedTime(when_local.astimezone(timezone.utc), rest, "llm")


async def parse_reminder(
    text: str, tz_name: str, now: datetime | None = None
) -> ParsedTime | None:
    parsed = parse_time(text, tz_name, now)
    if parsed is not None:
        return parsed
    return await parse_time_llm(text, tz_name, now)


def human_delta(when_utc: datetime, now: datetime | None = None) -> str:
    delta = when_utc - (now or datetime.now(timezone.utc))
    seconds = int(delta.total_seconds())
    if seconds <= 0:
        return "прямо сейчас"
    minutes = max(1, round(seconds / 60))
    if minutes < 60:
        return f"через {minutes} мин"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"через {hours} ч {minutes} мин" if minutes else f"через {hours} ч"
    days = hours // 24
    return f"через {days} дн"


__all__ = ["parse_time", "parse_time_llm", "parse_reminder", "ParsedTime", "human_delta"]
