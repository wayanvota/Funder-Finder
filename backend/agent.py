"""ReAct query-processing layer.

An LLM equipped with exactly two tools, mirroring the paper:

  search_index(query, state?, max_results?)
      -> the unified, normalized opportunity index. Runs the Grants.gov federal
         provider and the Kindora foundation provider concurrently, merges,
         dedupes, and ranks. This is the grounded, hallucination-free source.

  web_search(query)
      -> live web for very recent postings the biweekly crawl hasn't caught.

The agent ingests an uploaded proposal's full text as context and auto-extracts
search terms (no manual keyword entry). Canonical strategy: hit the index first;
fall back to web only when results are sparse or the user asks about very recent
postings. Everything streams: reasoning, tool calls, and result cards.

Events yielded by `run()` (each a dict, serialized to SSE upstream):
    {"type": "status",  "text": str}
    {"type": "message", "text": str}                # streamed assistant tokens
    {"type": "tool_call", "tool": str, "input": dict}
    {"type": "results", "opportunities": [opp_dict]}
    {"type": "done"}
    {"type": "error", "text": str}
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import AsyncIterator

from anthropic import AsyncAnthropic

from providers import GrantsGovProvider, KindoraProvider, WebSearchProvider
from schema import Opportunity, days_until

log = logging.getLogger("agent")

MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")
MAX_TOKENS = int(os.getenv("AGENT_MAX_TOKENS", "1800"))
MAX_TOOL_TURNS = int(os.getenv("AGENT_MAX_TOOL_TURNS", "5"))

SYSTEM_PROMPT = """You are Funder Finder, a grant-discovery agent for researchers and nonprofits.

You have two tools:
- search_index: a unified, biweekly-normalized index of U.S. federal grants
  (Grants.gov: NSF, NIH, DARPA, DOE, USDA and more) and private foundation
  programs (via Kindora). This is your PRIMARY, factually grounded source.
- web_search: live web search for very recent postings the index may not have
  caught yet.

Strategy:
1. ALWAYS call search_index first. Extract concrete search terms from the user's
   description or uploaded proposal yourself — do not ask them to specify
   keywords.
2. Only call web_search if index results are sparse, OR the user explicitly asks
   about very recently posted opportunities.
3. Ground every opportunity you mention in a tool result with its real URL.
   Never invent a program, deadline, or link. If you are unsure, say so.
4. Be concise. Briefly say what you searched for and why, then let the result
   cards speak. Note deadlines and whether a program is federal or foundation.
5. Support iterative refinement: when the user adds a constraint (deadline,
   geography, collaboration, funding size), re-search or filter rather than
   restarting.

When a proposal PDF is provided, read it as the researcher's full context and
extract the domain, methods, and goals to drive the search."""

TOOLS = [
    {
        "name": "search_index",
        "description": (
            "Search the unified, normalized index of federal grants (Grants.gov) "
            "and foundation programs (Kindora). Use concrete topical terms. This "
            "is the grounded, primary source — call it first."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Topical search terms, e.g. 'climate-resilient agriculture smallholder'"},
                "state": {"type": "string", "description": "Optional two-letter US state code to bias geography, e.g. 'CA'"},
                "max_results": {"type": "integer", "description": "Max results (default 12)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "web_search",
        "description": (
            "Live web search for very recent grant postings not yet in the index. "
            "Use only when index results are sparse or the user asks about the "
            "last few days/weeks."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
            },
            "required": ["query"],
        },
    },
]


def _dedupe_rank(opps: list[Opportunity]) -> list[Opportunity]:
    """Merge providers, drop dup URLs, rank by deadline urgency then source."""
    seen: set[str] = set()
    unique: list[Opportunity] = []
    for o in opps:
        key = (o.url or "").strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(o)

    def sort_key(o: Opportunity):
        d = days_until(o.deadline_date)
        # opportunities with a real upcoming deadline first (soonest), then
        # rolling/open (no date), federal slightly ahead of foundation on ties
        has_deadline = 0 if d is not None and d >= 0 else 1
        return (has_deadline, d if d is not None and d >= 0 else 9999,
                0 if o.source == "federal" else 1)

    unique.sort(key=sort_key)
    return unique


class GrantAgent:
    def __init__(self):
        self.client = AsyncAnthropic()  # reads ANTHROPIC_API_KEY
        self.grants = GrantsGovProvider()
        self.kindora = KindoraProvider()
        self.web = WebSearchProvider()

    async def _run_search_index(self, query: str, state: str | None,
                                max_results: int) -> list[Opportunity]:
        n = max(4, min(max_results or 12, 24))
        per = max(3, n // 2)
        results = await asyncio.gather(
            self.grants.search(query, limit=per),
            self.kindora.search(query, limit=per, state=state),
            return_exceptions=True,
        )
        merged: list[Opportunity] = []
        for r in results:
            if isinstance(r, list):
                merged.extend(r)
            else:
                log.warning("search_index sub-provider error: %s", r)
        return _dedupe_rank(merged)[:n]

    async def _run_web_search(self, query: str) -> list[Opportunity]:
        return await self.web.search(query)

    def _tool_result_text(self, opps: list[Opportunity]) -> str:
        if not opps:
            return "No opportunities found for that query."
        lines = []
        for o in opps:
            d = o.deadline or "n/a"
            lines.append(f"- [{o.source}] {o.title} — {o.agency or '?'} — deadline: {d} — {o.url}")
        return "\n".join(lines)

    async def run(self, user_message: str, proposal_text: str | None = None,
                  history: list[dict] | None = None) -> AsyncIterator[dict]:
        """Drive the ReAct loop, yielding streaming events."""
        if not os.getenv("ANTHROPIC_API_KEY"):
            yield {"type": "error", "text": "ANTHROPIC_API_KEY is not set on the server."}
            return

        content_blocks: list[dict] = []
        if proposal_text:
            snippet = proposal_text[:18000]
            content_blocks.append({
                "type": "text",
                "text": f"[Uploaded proposal context]\n{snippet}\n[End proposal]",
            })
        content_blocks.append({"type": "text", "text": user_message})

        messages: list[dict] = list(history or [])
        messages.append({"role": "user", "content": content_blocks})

        all_results: list[Opportunity] = []
        emitted_urls: set[str] = set()

        for turn in range(MAX_TOOL_TURNS):
            assistant_blocks: list[dict] = []
            tool_uses: list[dict] = []
            try:
                async with self.client.messages.stream(
                    model=MODEL,
                    max_tokens=MAX_TOKENS,
                    system=SYSTEM_PROMPT,
                    tools=TOOLS,
                    messages=messages,
                ) as stream:
                    async for event in stream:
                        if event.type == "content_block_delta" and event.delta.type == "text_delta":
                            yield {"type": "message", "text": event.delta.text}
                    final = await stream.get_final_message()
            except Exception as exc:  # noqa: BLE001
                log.exception("LLM stream failed")
                yield {"type": "error", "text": "The model could not complete this search. Please retry."}
                return

            for block in final.content:
                if block.type == "text":
                    assistant_blocks.append({"type": "text", "text": block.text})
                elif block.type == "tool_use":
                    assistant_blocks.append({
                        "type": "tool_use", "id": block.id,
                        "name": block.name, "input": block.input,
                    })
                    tool_uses.append({"id": block.id, "name": block.name, "input": block.input})

            messages.append({"role": "assistant", "content": assistant_blocks})

            if final.stop_reason != "tool_use" or not tool_uses:
                break  # agent produced its final answer

            tool_result_blocks: list[dict] = []
            for tu in tool_uses:
                name, args = tu["name"], (tu["input"] or {})
                yield {"type": "tool_call", "tool": name, "input": args}
                if name == "search_index":
                    opps = await self._run_search_index(
                        args.get("query", ""), args.get("state"),
                        args.get("max_results", 12),
                    )
                elif name == "web_search":
                    opps = await self._run_web_search(args.get("query", ""))
                else:
                    opps = []

                # stream new result cards as they're discovered
                fresh = [o for o in opps if (o.url or "").lower() not in emitted_urls]
                for o in fresh:
                    emitted_urls.add((o.url or "").lower())
                all_results.extend(fresh)
                if fresh:
                    yield {"type": "results",
                           "opportunities": [o.to_dict() for o in fresh]}

                tool_result_blocks.append({
                    "type": "tool_result",
                    "tool_use_id": tu["id"],
                    "content": self._tool_result_text(opps),
                })

            messages.append({"role": "user", "content": tool_result_blocks})

        yield {"type": "done"}
