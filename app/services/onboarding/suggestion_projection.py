"""Projection and normalization of discovered onboarding suggestions."""

from __future__ import annotations

from typing import cast

from app.models.api.onboarding import OnboardingFastDiscoverResponse, OnboardingSuggestion
from app.models.contracts import OnboardingSuggestionType
from app.services.feed_resolution import resolve_feed_candidate
from app.services.onboarding.config import (
    DEFAULT_SOURCE_LIMITS,
    ONBOARDING_FEED_DETECTOR,
    ONBOARDING_FEED_SUGGESTION_LIMIT,
)
from app.services.onboarding.internal_models import (
    SuggestionType,
    _DiscoverOutput,
    _DiscoverSuggestion,
)
from app.services.onboarding.query_heuristics import _merge_topics


def _normalize_suggestion_type(value: str | None) -> SuggestionType | None:
    if value in {"substack", "atom", "podcast_rss", "reddit"}:
        return cast(SuggestionType, value)
    return None


def _normalize_score(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _build_discovery_response(
    output: _DiscoverOutput,
    profile_summary: str | None = None,
    inferred_topics: list[str] | None = None,
) -> OnboardingFastDiscoverResponse:
    feed_limit = ONBOARDING_FEED_SUGGESTION_LIMIT
    substacks = _dedupe_suggestions(
        _normalize_suggestions(output.substacks, "substack"),
        feed_limit,
    )
    podcasts = _dedupe_suggestions(
        _normalize_suggestions(output.podcasts, "podcast_rss"),
        DEFAULT_SOURCE_LIMITS["podcast_rss"],
    )
    subreddits = _dedupe_suggestions(
        _normalize_suggestions(output.subreddits, "reddit"),
        DEFAULT_SOURCE_LIMITS["reddit"],
    )

    response = OnboardingFastDiscoverResponse(
        recommended_pods=podcasts,
        recommended_substacks=substacks,
        recommended_subreddits=subreddits,
    )
    return _ensure_response_rationales(
        response,
        profile_summary=profile_summary,
        inferred_topics=inferred_topics,
    )


def _ensure_response_rationales(
    response: OnboardingFastDiscoverResponse,
    profile_summary: str | None = None,
    inferred_topics: list[str] | None = None,
) -> OnboardingFastDiscoverResponse:
    topic_list = list(inferred_topics or [])
    for item in (
        response.recommended_substacks + response.recommended_pods + response.recommended_subreddits
    ):
        if item.rationale and item.rationale.strip():
            continue
        item.rationale = _default_rationale(
            item,
            profile_summary=profile_summary,
            inferred_topics=topic_list,
        )
    return response


def _infer_feed_url_from_site(site_url: str | None) -> str | None:
    """Infer a likely feed URL from a candidate site URL without network calls."""
    if not site_url:
        return None
    normalized = site_url.strip()
    if not normalized:
        return None

    lowered = normalized.lower()
    feed_markers = ("/feed", ".xml", "rss", "atom", "podcast")
    if any(marker in lowered for marker in feed_markers):
        return normalized
    return None


def _normalize_suggestions(
    items: list[_DiscoverSuggestion], suggestion_type: SuggestionType
) -> list[OnboardingSuggestion]:
    normalized: list[OnboardingSuggestion] = []
    for item in items:
        feed_url: str | None = (item.feed_url or "").strip() or None
        candidate_feed_url = (item.candidate_feed_url or "").strip()
        site_url = (item.site_url or "").strip() or None
        subreddit = _normalize_subreddit_name((item.subreddit or "").strip())

        if not feed_url and candidate_feed_url:
            feed_url = candidate_feed_url
        if suggestion_type == "substack" and not feed_url and site_url:
            feed_url = site_url.rstrip("/") + "/feed"
        if not feed_url and item.is_likely_feed:
            feed_url = _infer_feed_url_from_site(site_url)
        if suggestion_type == "reddit" and not subreddit:
            subreddit = _normalize_subreddit_name(_extract_subreddit(site_url))

        if suggestion_type == "reddit":
            if not subreddit:
                continue
            normalized.append(
                OnboardingSuggestion(
                    suggestion_type=OnboardingSuggestionType.REDDIT,
                    title=item.title or subreddit,
                    site_url=site_url,
                    subreddit=subreddit,
                    rationale=item.rationale,
                    score=item.score,
                    is_default=False,
                )
            )
            continue

        resolved_feed = resolve_feed_candidate(
            detector=ONBOARDING_FEED_DETECTOR,
            title=item.title,
            site_url=site_url,
            candidate_feed_urls=[feed_url] if feed_url else [],
            source="onboarding",
            prefer_site_discovery=suggestion_type == "podcast_rss",
        )
        if not resolved_feed:
            continue

        normalized.append(
            OnboardingSuggestion(
                suggestion_type=OnboardingSuggestionType(suggestion_type),
                title=item.title or resolved_feed.get("title"),
                site_url=site_url,
                feed_url=resolved_feed["feed_url"],
                rationale=item.rationale,
                score=item.score,
                is_default=False,
            )
        )
    return normalized


def _dedupe_suggestions(
    suggestions: list[OnboardingSuggestion],
    limit: int,
) -> list[OnboardingSuggestion]:
    merged: list[OnboardingSuggestion] = []
    seen: set[str] = set()

    for item in suggestions:
        key = _suggestion_key(item)
        if not key or key in seen:
            continue
        seen.add(key)
        merged.append(item)
        if len(merged) >= limit:
            break
    return merged


def _suggestion_key(item: OnboardingSuggestion) -> str | None:
    if item.suggestion_type == "reddit":
        return _normalize_subreddit_name(item.subreddit)
    return item.feed_url


def _normalize_subreddit_name(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    cleaned = cleaned.removeprefix("r/").strip("/")
    return cleaned or None


def _extract_subreddit(site_url: str | None) -> str | None:
    if not site_url:
        return None
    lowered = site_url.lower()
    if "reddit.com/r/" not in lowered:
        return None
    try:
        parts = lowered.split("reddit.com/r/")
        if len(parts) < 2:
            return None
        name = parts[1].split("/")[0]
        return name.strip()
    except Exception:
        return None


def _suggestion_label(item: OnboardingSuggestion) -> str:
    if item.suggestion_type == "reddit":
        return _normalize_subreddit_name(item.subreddit) or (item.title or "subreddit")
    return item.title or "this source"


def _discovery_context_hint(
    profile_summary: str | None,
    inferred_topics: list[str] | None,
) -> str:
    merged = _merge_topics(inferred_topics or [], [profile_summary or ""], max_topics=3)
    if not merged:
        return "your interests"
    if len(merged) == 1:
        return merged[0]
    return ", ".join(merged[:2])


def _default_rationale(
    item: OnboardingSuggestion,
    profile_summary: str | None = None,
    inferred_topics: list[str] | None = None,
) -> str:
    label = _suggestion_label(item)
    context_hint = _discovery_context_hint(profile_summary, inferred_topics)

    if item.suggestion_type == "podcast_rss":
        return f"Podcast covering {label} with discussions relevant to {context_hint}."
    if item.suggestion_type == "reddit":
        return f"Active subreddit for {label} with ongoing threads related to {context_hint}."
    return f"Feed focused on {label} with updates tied to {context_hint}."
