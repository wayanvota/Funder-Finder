"""Web search provider — the agent's second tool.

In the paper, web_search complements the structured index for very recent
postings the biweekly crawl hasn't caught yet. We back it with Tavily (a search
API built for LLM agents). If no TAVILY_API_KEY is set, the tool degrades to an
empty result plus a note, so the agent simply leans on the index. No silent
scraping fallback.

Configuration (env):
    TAVILY_API_KEY   enables live web search
    WEB_SEARCH_MAX   default 6 results
"""

from __future__ import annotations

import logging
import os

import httpx

from schema import Opportunity, clean_text

log = logging.getLogger("providers.web_search")

TAVILY_URL = "https://api.tavily.com/search"


class WebSearchProvider:
    name = "web"
    source = "web"

    def __init__(self, api_key: str | None = None, timeout: float = 12.0):
        self.api_key = api_key or os.getenv("TAVILY_API_KEY")
        self.timeout = timeout
        self.max_results = int(os.getenv("WEB_SEARCH_MAX", "6"))

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    async def search(self, query: str, *, limit: int | None = None, **_filters) -> list[Opportunity]:
        if not self.enabled:
            log.info("web_search disabled (no TAVILY_API_KEY)")
            return []
        n = limit or self.max_results
        payload = {
            "api_key": self.api_key,
            "query": f"{query} grant funding opportunity",
            "max_results": max(1, min(n, 10)),
            "search_depth": "basic",
            "include_answer": False,
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(TAVILY_URL, json=payload)
                resp.raise_for_status()
                body = resp.json()
        except Exception as exc:  # noqa: BLE001
            log.warning("web_search failed: %s", exc)
            return []

        out: list[Opportunity] = []
        for r in body.get("results", []):
            url = r.get("url")
            if not url:
                continue
            out.append(
                Opportunity(
                    id=f"web:{url}",
                    title=clean_text(r.get("title"), 200) or url,
                    url=url,
                    source="web",
                    provider=self.name,
                    agency=None,
                    description=clean_text(r.get("content")),
                    deadline="See source",
                )
            )
        return out
