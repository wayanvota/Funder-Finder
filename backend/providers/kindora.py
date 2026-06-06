"""Kindora foundation provider.

Kindora maintains the foundation-program corpus (IRS 990 data + funder-site
scrapes). In the paper's index, "Foundation" is the single largest category at
8,696 opportunities; Kindora is the natural drop-in for that half of the
aggregation layer, so we don't rebuild the most brittle scraping pipeline.

Kindora is exposed as an MCP server (streamable HTTP). This provider is a small,
dependency-light MCP client that calls the `search_open_grants` tool. It does
the full JSON-RPC handshake (initialize -> initialized -> tools/call) and parses
either JSON or SSE responses.

Configuration (env):
    KINDORA_MCP_URL   default https://www.kindora.co/mcp
    KINDORA_MCP_TOKEN optional bearer token if the endpoint requires auth

If the endpoint is unreachable or unauthenticated, this provider returns an
empty list and the app keeps working on federal data alone (graceful
degradation). Swap in Kindora's REST API here if/when you have a key.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional

import httpx

from schema import Opportunity, _parse_date, clean_text

log = logging.getLogger("providers.kindora")

DEFAULT_URL = "https://kindora-mcp.azurewebsites.net/mcp"
TOOL_NAME = "search_open_grants"


class KindoraProvider:
    name = "kindora"
    source = "foundation"

    def __init__(self, url: Optional[str] = None, token: Optional[str] = None,
                 timeout: float = 15.0):
        self.url = url or os.getenv("KINDORA_MCP_URL", DEFAULT_URL)
        self.token = token or os.getenv("KINDORA_MCP_TOKEN")
        self.timeout = timeout
        self._enabled = bool(self.url)

    def _headers(self, session_id: Optional[str] = None) -> dict:
        h = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        if session_id:
            h["Mcp-Session-Id"] = session_id
        return h

    @staticmethod
    def _parse_response(resp: httpx.Response) -> Optional[dict]:
        """MCP streamable HTTP can answer with JSON or an SSE stream."""
        ctype = resp.headers.get("content-type", "")
        text = resp.text
        if "text/event-stream" in ctype:
            for line in text.splitlines():
                line = line.strip()
                if line.startswith("data:"):
                    chunk = line[len("data:"):].strip()
                    if not chunk or chunk == "[DONE]":
                        continue
                    try:
                        obj = json.loads(chunk)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(obj, dict) and ("result" in obj or "error" in obj):
                        return obj
            return None
        try:
            return resp.json()
        except Exception:  # noqa: BLE001
            return None

    async def _rpc(self, client: httpx.AsyncClient, method: str,
                   params: Optional[dict], req_id: Optional[int],
                   session_id: Optional[str]) -> tuple[Optional[dict], Optional[str]]:
        body: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if req_id is not None:
            body["id"] = req_id
        if params is not None:
            body["params"] = params
        resp = await client.post(self.url, json=body, headers=self._headers(session_id))
        new_session = resp.headers.get("mcp-session-id") or session_id
        if req_id is None:  # notification, no response expected
            return None, new_session
        resp.raise_for_status()
        return self._parse_response(resp), new_session

    async def search(self, query: str, *, limit: int = 15, state: Optional[str] = None,
                     deadline_days: int = 120, **_filters) -> list[Opportunity]:
        if not self._enabled:
            return []
        try:
            async with httpx.AsyncClient(timeout=self.timeout,
                                         follow_redirects=True) as client:
                # 1. initialize
                init, session_id = await self._rpc(
                    client, "initialize",
                    {
                        "protocolVersion": "2025-03-26",
                        "capabilities": {},
                        "clientInfo": {"name": "funder-finder", "version": "0.1.0"},
                    },
                    req_id=1, session_id=None,
                )
                if not init or "result" not in init:
                    log.warning("kindora initialize failed: %s", init)
                    return []
                # 2. initialized notification
                await self._rpc(client, "notifications/initialized", {},
                                req_id=None, session_id=session_id)
                # 3. call the tool
                args: dict[str, Any] = {"query": query, "limit": max(1, min(limit, 50)),
                                        "deadline_days": deadline_days}
                if state:
                    args["state"] = state
                called, session_id = await self._rpc(
                    client, "tools/call",
                    {"name": TOOL_NAME, "arguments": args},
                    req_id=2, session_id=session_id,
                )
        except Exception as exc:  # noqa: BLE001 - graceful degradation
            log.warning("kindora search failed: %s", exc)
            return []

        payload = self._extract_payload(called)
        if not payload:
            return []
        return self._normalize(payload)

    @staticmethod
    def _extract_payload(called: Optional[dict]) -> Optional[dict]:
        if not called or "result" not in called:
            return None
        result = called["result"]
        content = result.get("content") or []
        for block in content:
            if block.get("type") == "text":
                try:
                    return json.loads(block["text"])
                except (json.JSONDecodeError, KeyError):
                    continue
        # some servers put it in structuredContent
        if isinstance(result.get("structuredContent"), dict):
            return result["structuredContent"]
        return None

    def _normalize(self, payload: dict) -> list[Opportunity]:
        rows = payload.get("results") or []
        out: list[Opportunity] = []
        for r in rows:
            url = r.get("application_url") or r.get("funder_kindora_url") or ""
            if not url:
                continue
            deadline = r.get("deadline") or "Rolling"
            out.append(
                Opportunity(
                    id=f"kindora:{r.get('funder_ein') or r.get('title','')[:40]}",
                    title=clean_text(r.get("title"), 240) or "(untitled program)",
                    url=url,
                    source=r.get("source") or "foundation",
                    provider=self.name,
                    agency=r.get("funder_name"),
                    description=clean_text(r.get("description")),
                    deadline=deadline,
                    deadline_date=_parse_date(deadline),
                    amount=r.get("grant_range") if r.get("grant_range") not in (None, "Not specified") else None,
                    focus_areas=[f for f in (r.get("focus_areas") or []) if f][:8],
                    geography=[g for g in (r.get("geographic_focus") or []) if g][:6],
                )
            )
        return out
