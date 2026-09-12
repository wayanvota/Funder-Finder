"""Deterministic provider boundary for browser-to-server tests."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import app as production_app  # noqa: E402


class FixtureAgent:
    async def run(self, message, proposal_text=None, history=None):
        if "trigger internal exception" in message.lower():
            raise RuntimeError("ANTHROPIC_API_KEY=synthetic-secret")
        if "simulate provider failure" in message.lower():
            yield {"type": "error", "text": "The grant index is temporarily unavailable. Please retry."}
            return

        hostile = "<img" in message.lower()
        hostile_markup = '<img src=x onerror="window.__funderFinderXss=1">'
        yield {"type": "status", "text": "Searching deterministic fixture index"}
        yield {"type": "tool_call", "tool": hostile_markup if hostile else "search_index", "input": {"query": message}}
        yield {
            "type": "results",
            "opportunities": [
                {
                    "id": "fixture:community-health",
                    "title": hostile_markup if hostile else "Community Health Access Fund",
                    "agency": "Fixture Foundation",
                    "source": hostile_markup if hostile else "foundation",
                    "description": "Controlled fixture result for browser-to-server testing.",
                    "url": "javascript:window.__funderFinderXss=1" if hostile else "https://example.org/grants/community-health",
                    "deadline": hostile_markup if hostile else "Rolling",
                    "deadline_date": None,
                    "amount": "$25,000-$100,000",
                    "focus_areas": ["community health", "access"],
                    "geography": ["United States"],
                    "provider": "e2e-fixture",
                }
            ],
        }
        context = " with the uploaded proposal" if proposal_text else ""
        yield {"type": "message", "text": f"Found one cited fixture opportunity{context}. Query: {message}"}
        yield {"type": "done"}


production_app.agent = FixtureAgent()
app = production_app.app
