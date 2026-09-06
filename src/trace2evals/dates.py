"""Tiny date helpers shared by the scripted backend and the failure miner.

The demo pins the calendar year so traces and goldens stay reproducible.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

DEMO_YEAR = 2026

_MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}

_DATE_RE = re.compile(r"\b(" + "|".join(_MONTHS) + r")\s+(\d{1,2})\b", re.IGNORECASE)


def parse_explicit_date(text: str) -> str | None:
    """Extract an explicit 'Month Day' mention as an ISO date, or None."""
    iso = re.findall(r"\b\d{4}-\d{2}-\d{2}\b", text)
    matches = list(_DATE_RE.finditer(text))
    if len(iso) + len(matches) != 1:
        return None
    try:
        if iso:
            return datetime.strptime(iso[0], "%Y-%m-%d").date().isoformat()
        match = matches[0]
        year = re.match(r",?\s+(\d{4})\b", text[match.end() :])
        return (
            datetime(int(year[1]) if year else DEMO_YEAR, _MONTHS[match[1].lower()], int(match[2]))
            .date()
            .isoformat()
        )
    except ValueError:
        return None


def shift_date(iso_date: str, days: int) -> str:
    shifted = datetime.strptime(iso_date, "%Y-%m-%d") + timedelta(days=days)
    return shifted.strftime("%Y-%m-%d")
