"""Prompt-based filtering for X posts before digest ingestion."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, Field

from app.core.logging import get_logger
from app.core.settings import get_settings
from app.models.user import User
from app.services.llm_agents import get_basic_agent
from app.services.x_api import XTweet

logger = get_logger(__name__)

X_DIGEST_FILTER_MODEL = "google:gemini-3.1-flash-lite-preview"
X_DIGEST_FILTER_THRESHOLD = 0.65
X_DIGEST_FILTER_REASON_MAX_CHARS = 240

DEFAULT_X_DIGEST_FILTER_PROMPT = (
    "Include high-signal posts that are likely to improve a daily digest: original reporting, "
    "firsthand product or company updates, technical insight, market structure changes, "
    "meaningful data points, strong analysis, or unusually clear synthesis. Exclude memes, "
    "engagement bait, vague reactions, low-context commentary, repetitive hype, and pure "
    "self-promotion unless the post adds concrete new information."
)

X_DIGEST_FILTER_SYSTEM_PROMPT = """You score whether an X post should be included in a user's
daily digest input pool.

Return a JSON object with exactly these fields:
{
  "score": 0.0,
  "reason": "short justification"
}

Scoring rules:
- Use a 0.0 to 1.0 relevance score.
- Higher scores mean the post is more likely to improve a concise, high-signal digest.
- Reward concrete new information, expertise, data, important announcements, and strong synthesis.
- Penalize memes, jokes, hype, engagement bait, repetitive commentary, vague takes, and pure
  promotion.
- Base the score on the user's custom filter instructions as the highest-priority preference layer.
- Keep the reason short, factual, and specific.
"""


class _XDigestFilterOutput(BaseModel):
    """Structured LLM output for one X digest filtering decision."""

    score: float = Field(..., ge=0.0, le=1.0)
    reason: str = Field(..., min_length=1, max_length=X_DIGEST_FILTER_REASON_MAX_CHARS)


@dataclass(frozen=True)
class XDigestFilterDecision:
    """Resolved filtering decision for one X post."""

    score: float
    reason: str
    accepted: bool
    errored: bool = False


@dataclass(frozen=True)
class XDigestFilterEvalCase:
    """One labeled pass/fail example for the X digest filter."""

    name: str
    tweet: XTweet
    user_prompt: str
    source_type: str
    source_label: str
    expected_accept: bool


@dataclass(frozen=True)
class XDigestFilterEvalResult:
    """Outcome for one labeled X digest filter eval case."""

    case_name: str
    expected_accept: bool
    decision: XDigestFilterDecision
    passed: bool


def normalize_x_digest_filter_prompt(prompt: str | None) -> str | None:
    """Normalize a stored user X digest filter prompt."""
    if prompt is None:
        return None
    cleaned = prompt.strip()
    return cleaned or None


def resolve_user_x_digest_filter_prompt(user: User) -> str:
    """Resolve the active X digest filter prompt for a user."""
    stored_prompt = normalize_x_digest_filter_prompt(user.x_digest_filter_prompt)
    if stored_prompt:
        return stored_prompt
    return DEFAULT_X_DIGEST_FILTER_PROMPT


def score_x_digest_candidate(
    *,
    tweet: XTweet,
    user_prompt: str,
    source_type: str,
    source_label: str,
) -> XDigestFilterDecision:
    """Score whether an X post should be accepted into the digest source pool."""
    prompt = _build_filter_prompt(
        tweet=tweet,
        user_prompt=user_prompt,
        source_type=source_type,
        source_label=source_label,
    )
    try:
        settings = get_settings()
        agent = get_basic_agent(
            X_DIGEST_FILTER_MODEL,
            _XDigestFilterOutput,
            X_DIGEST_FILTER_SYSTEM_PROMPT,
        )
        result = agent.run_sync(prompt, model_settings={"timeout": settings.worker_timeout_seconds})
        output = _extract_agent_output(result)
        reason = " ".join(output.reason.split()).strip()[:X_DIGEST_FILTER_REASON_MAX_CHARS]
        return XDigestFilterDecision(
            score=output.score,
            reason=reason or "No reason provided",
            accepted=output.score >= X_DIGEST_FILTER_THRESHOLD,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "X digest filter scoring failed",
            extra={
                "component": "x_digest_filter",
                "operation": "score_candidate",
                "item_id": tweet.id,
                "context_data": {
                    "source_type": source_type,
                    "source_label": source_label,
                    "error": str(exc),
                },
            },
        )
        return XDigestFilterDecision(
            score=0.0,
            reason="Filter model unavailable or invalid output",
            accepted=False,
            errored=True,
        )


def evaluate_x_digest_filter_cases(
    *,
    cases: list[XDigestFilterEvalCase],
) -> list[XDigestFilterEvalResult]:
    """Run labeled pass/fail eval cases through the current X digest filter."""
    results: list[XDigestFilterEvalResult] = []
    for case in cases:
        decision = score_x_digest_candidate(
            tweet=case.tweet,
            user_prompt=case.user_prompt,
            source_type=case.source_type,
            source_label=case.source_label,
        )
        results.append(
            XDigestFilterEvalResult(
                case_name=case.name,
                expected_accept=case.expected_accept,
                decision=decision,
                passed=decision.accepted == case.expected_accept,
            )
        )
    return results


def _build_filter_prompt(
    *,
    tweet: XTweet,
    user_prompt: str,
    source_type: str,
    source_label: str,
) -> str:
    author = (
        tweet.author_name
        or (f"@{tweet.author_username}" if tweet.author_username else "Unknown")
    )
    lines = [
        "User filter instructions:",
        user_prompt.strip(),
        "",
        "Source context:",
        f"- source_type: {source_type}",
        f"- source_label: {source_label}",
        "",
        "Candidate X post:",
        f"- author: {author}",
        f"- handle: @{tweet.author_username}" if tweet.author_username else "- handle: unknown",
        f"- created_at: {tweet.created_at or 'unknown'}",
        f"- like_count: {tweet.like_count if tweet.like_count is not None else 'unknown'}",
        f"- repost_count: {tweet.retweet_count if tweet.retweet_count is not None else 'unknown'}",
        f"- reply_count: {tweet.reply_count if tweet.reply_count is not None else 'unknown'}",
        f"- external_urls: {', '.join(tweet.external_urls) if tweet.external_urls else 'none'}",
        "",
        "Post text:",
        tweet.text.strip(),
    ]
    return "\n".join(lines)


def _extract_agent_output(result: object) -> _XDigestFilterOutput:
    if hasattr(result, "output"):
        return result.output
    if hasattr(result, "data"):
        return result.data
    raise AttributeError("Agent result missing output/data attribute")
