"""Grants.gov federal opportunity provider.

Uses the public Search2 REST API (no auth, no key). This is the federal half of
the index: NSF, NIH, DARPA, DOE, USDA and the rest of Grants.gov all surface
here. Response shape verified live against:
    POST https://api.grants.gov/v1/api/search2
    body: {"keyword": "...", "oppStatuses": "posted", "rows": N}
    -> data.oppHits[] = {id, number, title, agencyCode, agency,
                          openDate, closeDate, oppStatus, cfdaList}
Detail pages live at https://www.grants.gov/search-results-detail/{id}
"""

from __future__ import annotations

import logging

import httpx

from schema import Opportunity, _parse_date, clean_text

log = logging.getLogger("providers.grants_gov")

API_URL = "https://api.grants.gov/v1/api/search2"
DETAIL_URL = "https://www.grants.gov/search-results-detail/{id}"


class GrantsGovProvider:
    name = "grants.gov"
    source = "federal"

    def __init__(self, timeout: float = 12.0):
        self.timeout = timeout

    async def search(self, query: str, *, limit: int = 15, **_filters) -> list[Opportunity]:
        payload = {
            "keyword": query,
            "oppStatuses": "forecasted|posted",
            "rows": max(1, min(limit, 50)),
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(API_URL, json=payload)
                resp.raise_for_status()
                body = resp.json()
        except Exception as exc:  # noqa: BLE001 - graceful degradation by design
            log.warning("grants.gov search failed: %s", exc)
            return []

        if body.get("errorcode") not in (0, None):
            log.warning("grants.gov returned errorcode=%s msg=%s",
                        body.get("errorcode"), body.get("msg"))
            return []

        hits = (body.get("data") or {}).get("oppHits") or []
        results: list[Opportunity] = []
        for h in hits:
            opp_id = str(h.get("id") or "").strip()
            if not opp_id:
                continue
            close = h.get("closeDate") or ""
            results.append(
                Opportunity(
                    id=f"grantsgov:{opp_id}",
                    title=clean_text(h.get("title"), 240) or "(untitled opportunity)",
                    url=DETAIL_URL.format(id=opp_id),
                    source="federal",
                    provider=self.name,
                    agency=h.get("agency") or h.get("agencyCode"),
                    description=clean_text(
                        f"{h.get('agency', '')} · {h.get('number', '')}".strip(" ·")
                    ),
                    deadline=close or "Not posted",
                    deadline_date=_parse_date(close),
                    amount=None,
                    focus_areas=[c for c in (h.get("cfdaList") or []) if c],
                )
            )
        return results
