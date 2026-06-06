# Funder Finder

A working build of a compound AI grant-discovery agent, after Tang & Kejriwal,
[*A Compound AI Agent for Conversational Grant Discovery*](https://arxiv.org/abs/2605.02366)
(USC/GRAIL, 2026). One unified, normalized index of federal and foundation
opportunities, searched by a ReAct agent that reads your proposal PDF and streams
grounded results with its reasoning visible.

This is an independent rebuild of that architecture. Kindora stands in for the
foundation-scraping half of the paper's aggregation layer.

---

## What it is

Two loosely-coupled layers, exactly as the paper frames them:

**Aggregation layer (the index).** Federal opportunities come live from the
[Grants.gov public API](https://www.grants.gov) (NSF, NIH, DARPA, DOE, USDA, and
the rest). Foundation programs come from [Kindora](https://kindora.co) over MCP,
the corpus that is the single largest slice of the paper's index. Both are
normalized into one `Opportunity` record shape at request time
(`backend/schema.py`), so nothing downstream cares which portal a result came
from.

**Query layer (the agent).** A ReAct loop (`backend/agent.py`) gives an Anthropic
model exactly two tools, mirroring the paper:

- `search_index` — runs Grants.gov and Kindora concurrently, merges, dedupes, and
  ranks by deadline urgency. The grounded, primary source.
- `web_search` — live web (Tavily) for very recent postings the index hasn't
  caught. Optional; the agent leans on the index without it.

Upload a proposal PDF and the agent reads the full text as context and extracts
search terms itself. Its strategy is conservative by design: index first, web
only when results are sparse or you ask about the last few days. Every result is
tied to a real URL from a tool call. It will not invent a program or a deadline.

---

## Architecture deviation from the paper

The paper crawls sources on a **biweekly** schedule into an **Algolia** index and
serves cached data. This build queries Kindora and Grants.gov **live** per
request. For a single-instance prototype that trades a little latency for zero
crawl infrastructure and fresher federal data. To match the paper exactly, mirror
both sources into a Neon/Postgres table on a schedule (see *Scaling* below) and
point `search_index` at that.

---

## Layout

```
backend/
  app.py              FastAPI: /healthz, /api/upload, /api/chat (SSE), serves UI
  agent.py            ReAct loop, two tools, merge/dedupe/rank
  schema.py           canonical Opportunity + normalization
  pdf_utils.py        proposal PDF text extraction
  providers/
    grants_gov.py     Grants.gov public API (federal)
    kindora.py        Kindora MCP client (foundation)
    web_search.py     Tavily (optional)
  requirements.txt
  .env.example
frontend/
  index.html          single-file chat UI (streaming, PDF drag-drop)
site/
  index.html          wayan.com/funder-finder landing  <- FTP this
  about.html          methodology + honest-metric note  <- FTP this
render.yaml           one-click Render blueprint
```

---

## Run locally

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # add your ANTHROPIC_API_KEY
export $(grep -v '^#' .env | xargs)
uvicorn app:app --reload --port 8000
```

Open http://localhost:8000. Check wiring at http://localhost:8000/healthz.

Only `ANTHROPIC_API_KEY` is required. Kindora works with its default public URL.
`TAVILY_API_KEY` is optional and just enables the web_search tool.

---

## Deploy to Render

The repo includes `render.yaml`. Push to GitHub, create a Render Blueprint from
the repo, and set `ANTHROPIC_API_KEY` (and optionally `TAVILY_API_KEY`) as
secrets in the dashboard. Health check is `/healthz`.

## The landing pages (FTP)

`site/index.html` and `site/about.html` are standalone and self-styled. FTP both
to `wayan.com/funder-finder/`. Before you do, set `APP_URL` near the bottom of
`site/index.html` to your live Render URL so the "Open the app" button points at
the running service.

## Scaling notes (Neon)

The proposal store in `app.py` is in-memory, fine for one instance. If you run
multiple Render instances or want the paper's cached-index model, add Neon:

- Move uploaded proposal text to a `proposals` table keyed by id.
- Add an `opportunities` table and a scheduled job (Render Cron) that calls the
  providers and upserts normalized records biweekly, then have `search_index`
  query Postgres full-text instead of hitting the APIs live.

---

## On the numbers, read this before you quote them

The paper reports grant-discovery time falling **from 30–45 minutes (manual
portal searching) to under 10 minutes**, plus 3,000+ users, a first token within
two seconds, and a biweekly refresh. Useful framing. Not benchmarks.

The authors say so plainly: the paper offers **no formal evaluation and no
controlled comparison**. The 30–45 minute baseline is what a manual search
"typically" takes; the under-10-minutes figure comes from a walkthrough scenario,
not a timed study. The user count and latency are deployment telemetry. All
reported in good faith, none of it a result in the sense a reviewer means the
word, no control group and no relevance measure.

Quote the time saving as a **design target**, with that caveat attached. The
[about page](site/about.html) states this explicitly for a public audience. The
claim that would actually be worth making, recall and relevance against an
expert-labeled shortlist, is the one nobody in this space has published yet,
including this build.

---

## Verification status

Smoke-tested live against Grants.gov and the Kindora MCP endpoint: both providers
return and normalize correctly, the merge/dedupe/rank is correct, and the
no-API-key path degrades to a clean error instead of crashing. The full LLM tool
loop requires your `ANTHROPIC_API_KEY` and was not exercised in that test; the
wiring up to the model boundary is verified.

Architecture source:
[arXiv:2605.02366](https://arxiv.org/abs/2605.02366). Not affiliated with USC,
GRAIL, or Kindora.
