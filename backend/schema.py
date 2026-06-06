"""Canonical opportunity record and normalization helpers.

Every data source (Grants.gov, Kindora foundations, web search) is normalized
into a single `Opportunity` shape so the agent and the frontend never have to
care which portal a result came from. This is the "unified, normalized index"
idea from the paper, implemented as a normalization boundary rather than a
warehouse table.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, asdict, field
from datetime import date, datetime
from typing import Optional


CANONICAL_FIELDS = (
    "id",
    "title",
    "agency",
    "source",          # "federal" | "foundation" | "web"
    "description",
    "url",
    "deadline",        # human-readable string, may be "Rolling"/"Open"
    "deadline_date",   # ISO date if we could parse one, else None
    "amount",          # human-readable funding range, may be None
    "focus_areas",     # list[str]
    "geography",       # list[str]
    "provider",        # which provider produced this ("grants.gov", "kindora", "web")
)


@dataclass
class Opportunity:
    title: str
    url: str
    source: str = "federal"
    provider: str = "unknown"
    agency: Optional[str] = None
    description: Optional[str] = None
    deadline: Optional[str] = None
    deadline_date: Optional[str] = None
    amount: Optional[str] = None
    focus_areas: list[str] = field(default_factory=list)
    geography: list[str] = field(default_factory=list)
    id: Optional[str] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        # stable, predictable key order for the frontend
        return {k: d.get(k) for k in CANONICAL_FIELDS}


def _parse_date(value: Optional[str]) -> Optional[str]:
    """Best-effort parse of the many date formats federal portals emit.

    Returns an ISO date string (YYYY-MM-DD) or None. Tolerant by design: the
    paper's lesson #1 is graceful degradation over strict schema enforcement.
    """
    if not value or not isinstance(value, str):
        return None
    value = value.strip()
    if not value or value.lower() in {"rolling", "open", "n/a", "not specified"}:
        return None
    fmts = ("%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y", "%b %d, %Y", "%B %d, %Y", "%d %B %Y")
    for fmt in fmts:
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            continue
    # last resort: pull an ISO-ish substring
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", value)
    if m:
        return m.group(0)
    return None


def days_until(iso_date: Optional[str]) -> Optional[int]:
    if not iso_date:
        return None
    try:
        d = date.fromisoformat(iso_date)
    except ValueError:
        return None
    return (d - date.today()).days


def clean_text(value: Optional[str], limit: int = 600) -> Optional[str]:
    if not value:
        return None
    value = re.sub(r"\s+", " ", str(value)).strip()
    if len(value) > limit:
        value = value[: limit - 1].rstrip() + "…"
    return value or None
