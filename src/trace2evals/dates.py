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
    match = _DATE_RE.search(text)
    if match is None:
        return None
    month = _MONTHS[match.group(1).lower()]
    day = int(match.group(2))
    return f"{DEMO_YEAR}-{month:02d}-{day:02d}"


def shift_date(iso_date: str, days: int) -> str:
    shifted = datetime.strptime(iso_date, "%Y-%m-%d") + timedelta(days=days)
    return shifted.strftime("%Y-%m-%d")
