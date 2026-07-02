"""One-off prototype server: static unread-briefing pages + live dig-deeper API.

Replaces the plain `python3 -m http.server 8787` for the unread-briefing
prototype. Serves the artifact directory as before and adds two endpoints the
Mad-Lib newspaper page calls when an insight fragment is tapped:

- GET  /api/search?q=...      fast Exa web search, returns titles/urls/snippets
- POST /api/summarize         DeepSeek Flash digest of fragment + search results

Hacky by design: no auth (Tailscale-only), in-process, sync endpoints running
in FastAPI's threadpool. CORS is open so the page can be served from any port
(including a plain static server) while the API lives on its own dedicated port.

Run the API on the dedicated port the page's JS targets (8790); optionally run a
second instance on 8787 to also serve the page at the familiar URL:
    uv run uvicorn scripts.serve_dig_deeper:app --host 0.0.0.0 --port 8790
    uv run uvicorn scripts.serve_dig_deeper:app --host 0.0.0.0 --port 8787
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.exa_client import exa_search  # noqa: E402
from app.services.llm_agents import get_basic_agent  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent / "outputs/unread_briefing_prototype/user_1_current"
DIG_MODEL_SPEC = "openrouter:deepseek/deepseek-v4-flash"
SEARCH_RESULTS = 4
SNIPPET_CHARS = 900

DIG_SYSTEM_PROMPT = (
    "You expand a tapped fragment from a news briefing into a short grounded "
    "deep dive. Informational register: direct declarative sentences, concrete "
    "numbers and mechanisms, no preamble, no 'based on the search results', no "
    "hedging boilerplate. 3-5 sentences, then stop."
)

app = FastAPI()

# The page may be loaded from a different port (e.g. a plain static server on
# 8787) while this API runs on its own dedicated port, so allow cross-origin.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class SummarizeRequest(BaseModel):
    fragment: str = Field(..., min_length=3, max_length=600)
    passage: str = Field("", max_length=4000)
    results: list[dict] = Field(default_factory=list)


@app.get("/api/search")
def api_search(q: str) -> dict:
    started = time.monotonic()
    results = exa_search(
        query=q[:200],
        num_results=SEARCH_RESULTS,
        max_characters=SNIPPET_CHARS,
    )
    return {
        "query": q,
        "elapsed_ms": int((time.monotonic() - started) * 1000),
        "results": [
            {
                "title": result.title,
                "url": result.url,
                "snippet": result.snippet or "",
                "published_date": result.published_date,
            }
            for result in results
        ],
    }


@app.post("/api/summarize")
def api_summarize(request: SummarizeRequest) -> dict:
    started = time.monotonic()
    snippets = "\n\n".join(
        f"[{index}] {result.get('title', '')} — {result.get('url', '')}\n"
        f"{(result.get('snippet') or '')[:SNIPPET_CHARS]}"
        for index, result in enumerate(request.results[:SEARCH_RESULTS], start=1)
    )
    prompt = (
        f"Tapped fragment: {request.fragment}\n\n"
        f"Briefing context: {request.passage[:1500]}\n\n"
        f"Web search results:\n{snippets or '(no results)'}\n\n"
        "Write the deep dive now. If the search results contradict or date the "
        "fragment, say so plainly."
    )
    agent = get_basic_agent(DIG_MODEL_SPEC, str, DIG_SYSTEM_PROMPT)
    result = agent.run_sync(prompt)
    return {
        "summary": result.output,
        "model": DIG_MODEL_SPEC,
        "elapsed_ms": int((time.monotonic() - started) * 1000),
    }


app.mount("/", StaticFiles(directory=ROOT, html=True), name="static")
