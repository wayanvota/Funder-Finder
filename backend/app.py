"""Funder Finder API.

A compound AI grant-discovery service after Tang & Kejriwal, "A Compound AI
Agent for Conversational Grant Discovery" (arXiv:2605.02366v1). Two layers:

  - Aggregation: a unified, normalized opportunity index. Federal grants come
    live from the Grants.gov public API; foundation programs come from Kindora's
    corpus over MCP. (The paper runs a biweekly crawler into Algolia; here the
    index is queried live so there is nothing to keep warm for a prototype.)
  - Query: a ReAct agent (see agent.py) with search_index + web_search tools
    that ingests an uploaded proposal PDF and streams grounded results.

Endpoints:
  GET  /healthz          liveness + which providers are configured
  POST /api/upload       multipart PDF -> {proposal_id, chars, preview}
  POST /api/chat         JSON {message, proposal_id?, history?} -> SSE stream
  GET  /                 serves the single-file chat UI
"""

from __future__ import annotations

import json
import logging
import os
import uuid

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse

from agent import GrantAgent
from pdf_utils import extract_text
from providers import KindoraProvider, WebSearchProvider

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("app")

app = FastAPI(title="Funder Finder", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "*").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)

agent = GrantAgent()

# In-memory proposal store. Fine for a single-instance prototype; swap for Neon
# or Redis if you scale to multiple Render instances.
_PROPOSALS: dict[str, str] = {}
MAX_PDF_BYTES = int(os.getenv("MAX_PDF_BYTES", str(15 * 1024 * 1024)))

FRONTEND = os.path.join(os.path.dirname(__file__), "..", "frontend", "index.html")


@app.get("/healthz")
async def healthz():
    return {
        "ok": True,
        "model": os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6"),
        "providers": {
            "grants_gov": True,
            "kindora": True,
            "kindora_url": KindoraProvider().url,
            "web_search": WebSearchProvider().enabled,
            "anthropic_key": bool(os.getenv("ANTHROPIC_API_KEY")),
        },
    }


@app.post("/api/upload")
async def upload(file: UploadFile = File(...)):
    data = await file.read()
    if len(data) > MAX_PDF_BYTES:
        raise HTTPException(413, "PDF too large")
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(400, "Please upload a .pdf file")
    text = extract_text(data)
    if not text:
        raise HTTPException(422, "Could not extract text from that PDF")
    pid = uuid.uuid4().hex
    _PROPOSALS[pid] = text
    return {"proposal_id": pid, "chars": len(text), "preview": text[:400]}


async def _sse(message: str, proposal_text: str | None, history: list[dict]):
    async def gen():
        try:
            async for event in agent.run(message, proposal_text, history):
                yield f"data: {json.dumps(event)}\n\n"
        except Exception as exc:  # noqa: BLE001
            log.exception("agent run failed")
            yield f"data: {json.dumps({'type': 'error', 'text': str(exc)})}\n\n"
    return gen


@app.post("/api/chat")
async def chat(request: Request):
    body = await request.json()
    message = (body.get("message") or "").strip()
    if not message and not body.get("proposal_id"):
        raise HTTPException(400, "Provide a message or a proposal_id")
    proposal_text = None
    if body.get("proposal_id"):
        proposal_text = _PROPOSALS.get(body["proposal_id"])
        if proposal_text is None:
            raise HTTPException(404, "Unknown proposal_id (it may have expired)")
    if not message:
        message = "Find grant opportunities that fit this proposal."
    history = body.get("history") or []
    gen = await _sse(message, proposal_text, history)
    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/")
async def index():
    if os.path.exists(FRONTEND):
        return FileResponse(FRONTEND)
    return {"service": "funder-finder", "see": "/healthz"}
