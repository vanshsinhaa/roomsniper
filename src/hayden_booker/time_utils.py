from __future__ import annotations

import re
from datetime import date, datetime, time


def parse_hhmm(value: str) -> time:
    return datetime.strptime(value, "%H:%M").time()


def minutes_since_midnight(value: str | time) -> int:
    parsed = parse_hhmm(value) if isinstance(value, str) else value
    return parsed.hour * 60 + parsed.minute


def format_hhmm(total_minutes: int) -> str:
    hour, minute = divmod(total_minutes, 60)
    return f"{hour:02d}:{minute:02d}"


def required_half_hours(start: str, end: str) -> tuple[str, ...]:
    first = minutes_since_midnight(start)
    final = minutes_since_midnight(end)
    return tuple(format_hhmm(value) for value in range(first, final, 30))


def parse_slot_label(label: str) -> tuple[str, str] | None:
    cleaned = (
        re.sub(r"\s+", " ", label.strip())
        .upper()
        .replace("\N{EN DASH}", "-")
        .replace("\N{EM DASH}", "-")
    )
    match = re.search(
        r"(\d{1,2}:\d{2}\s*(?:AM|PM))\s*-\s*(\d{1,2}:\d{2}\s*(?:AM|PM))",
        cleaned,
    )
    if not match:
        return None
    return (_to_24_hour(match.group(1)), _to_24_hour(match.group(2)))


def _to_24_hour(value: str) -> str:
    compact = re.sub(r"\s+", " ", value.strip().upper())
    return datetime.strptime(compact, "%I:%M %p").strftime("%H:%M")


def parse_offered_date(text: str, value: str | None = None) -> date | None:
    candidates = [
        candidate.strip() for candidate in (value, text) if candidate and candidate.strip()
    ]
    formats = (
        "%Y-%m-%d",
        "%m/%d/%Y",
        "%m/%d/%y",
        "%A, %B %d, %Y",
        "%a, %b %d, %Y",
        "%B %d, %Y",
        "%b %d, %Y",
    )
    for candidate in candidates:
        iso_match = re.search(r"\d{4}-\d{2}-\d{2}", candidate)
        if iso_match:
            try:
                return date.fromisoformat(iso_match.group())
            except ValueError:
                pass
        for fmt in formats:
            try:
                return datetime.strptime(candidate, fmt).date()
            except ValueError:
                continue
    return None


def human_time(value: str) -> str:
    return datetime.strptime(value, "%H:%M").strftime("%I:%M %p").lstrip("0")
