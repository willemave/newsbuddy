from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.settings import Settings, get_settings
from app.models.api.briefing import (
    BriefingDigSearchResult,
    BriefingDigSummarizeResponse,
)
from app.models.db import VendorUsageRecord
from app.services.exa_client import exa_search
from app.services.llm_agents import get_basic_agent
from app.services.vendor_usage import record_model_usage

DIG_SYSTEM_PROMPT = (
    "You expand a selected fragment from a personal news briefing into a grounded mini-explainer. "
    "Use only the passage context and the numbered search results; never invent facts. Structure: "
    "first a 2-3 sentence paragraph explaining the fragment in the passage's context; then 3 to 5 "
    "bullet lines, each starting with '- ', each carrying the most concrete facts available "
    "(numbers, dates, names, mechanisms) and ending with its source number like [2]; include one "
    'or two short verbatim quotes from the search results in "double quotes" with their '
    "citation, where a striking phrase exists; then one closing sentence on why it matters to "
    "the reader. Format with light markdown: **bold** the two to four most load-bearing terms or "
    "figures and use '-' bullets — no headings, no preamble, no 'based on the search results'. "
    "Aim for 160-240 words."
)


def search_fragment(
    db: Session,
    *,
    user_id: int,
    fragment: str,
) -> tuple[list[BriefingDigSearchResult], int]:
    del db
    started_at = time.perf_counter()
    results = exa_search(
        fragment[:200],
        num_results=5,
        max_characters=1500,
        telemetry={
            "feature": "briefing_dig",
            "operation": "briefing_dig.search",
            "source": "api",
            "user_id": user_id,
        },
    )
    elapsed_ms = round((time.perf_counter() - started_at) * 1000)
    return [
        BriefingDigSearchResult(
            title=result.title,
            url=result.url,
            snippet=result.snippet,
            published_date=result.published_date,
        )
        for result in results
    ], elapsed_ms


def summarize_fragment(
    db: Session,
    *,
    user_id: int,
    fragment: str,
    passage_context: str,
    results: list[BriefingDigSearchResult],
    settings: Settings | None = None,
) -> BriefingDigSummarizeResponse:
    settings = settings or get_settings()
    _enforce_hourly_limit(db, user_id=user_id, settings=settings)
    started_at = time.perf_counter()
    model_spec = settings.briefing_model
    prompt = _summary_prompt(fragment=fragment, passage_context=passage_context, results=results)
    agent = get_basic_agent(model_spec, str, DIG_SYSTEM_PROMPT)
    result = agent.run_sync(prompt)
    record_model_usage(
        "briefing_dig",
        result,
        model_spec=model_spec,
        persist={
            "feature": "briefing_dig",
            "operation": "briefing_dig.summarize",
            "source": "api",
            "user_id": user_id,
        },
    )
    elapsed_ms = round((time.perf_counter() - started_at) * 1000)
    return BriefingDigSummarizeResponse(
        summary=str(result.output).strip(),
        model=model_spec,
        elapsed_ms=elapsed_ms,
    )


def _enforce_hourly_limit(db: Session, *, user_id: int, settings: Settings) -> None:
    if settings.briefing_dig_hourly_limit <= 0:
        return
    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=1)
    count = (
        db.query(VendorUsageRecord.id)
        .filter(VendorUsageRecord.user_id == user_id)
        .filter(VendorUsageRecord.feature == "briefing_dig")
        .filter(VendorUsageRecord.created_at >= cutoff)
        .count()
    )
    if count >= settings.briefing_dig_hourly_limit:
        raise HTTPException(status_code=429, detail="Briefing dig limit reached")


def _summary_prompt(
    *,
    fragment: str,
    passage_context: str,
    results: list[BriefingDigSearchResult],
) -> str:
    result_lines = []
    for index, result in enumerate(results, start=1):
        published = f" (published {result.published_date})" if result.published_date else ""
        result_lines.append(
            f"{index}. {result.title}{published}\n"
            f"URL: {result.url}\nSnippet: {result.snippet or ''}"
        )
    return (
        f"Selected fragment: {fragment[:300]}\n\n"
        f"Briefing context: {passage_context[:2000]}\n\n"
        "Search results:\n" + "\n\n".join(result_lines)
    )
