"""Pure query construction and selection heuristics for onboarding."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Literal, cast

from app.models.api.onboarding import OnboardingFastDiscoverRequest, OnboardingProfileRequest
from app.services.onboarding.config import (
    DISCOVERY_PROMPT_MAX_WEB_RESULTS,
    DISCOVERY_PROMPT_SNIPPET_CHARS,
    FAST_DISCOVER_MAX_QUERIES,
)
from app.services.onboarding.internal_models import _DiscoveryWebResult


def _normalize_lane_target(value: str | None) -> Literal["feeds", "podcasts", "reddit"] | None:
    if value in {"feeds", "podcasts", "reddit"}:
        return cast(Literal["feeds", "podcasts", "reddit"], value)
    return None


def _build_profile_queries(request: OnboardingProfileRequest) -> list[str]:
    topics = _merge_topics(request.interest_topics)
    queries: list[str] = []
    for topic in topics:
        queries.append(f"{topic} newsletter")
        queries.append(f"{topic} podcast")
        queries.append(f"{topic} substack")
        if len(queries) >= 4:
            break
    if not queries:
        queries.append(f"{request.first_name} newsletter")
    return queries[:4]


def _build_discovery_queries(
    request: OnboardingFastDiscoverRequest, max_queries: int = FAST_DISCOVER_MAX_QUERIES
) -> list[str]:
    topics = [topic.strip() for topic in request.inferred_topics if topic.strip()]
    topics = topics[:4] if topics else []

    queries: list[str] = []
    if request.profile_summary:
        queries.append(f"{request.profile_summary} newsletter")

    for topic in topics:
        queries.append(f"{topic} substack")
        queries.append(f"{topic} podcast rss")
        queries.append(f"{topic} best newsletters")
        if len(queries) >= max_queries:
            break

    return queries[:max_queries]


def _select_prompt_results(
    results: list[_DiscoveryWebResult],
    *,
    lane_balanced: bool = False,
) -> list[_DiscoveryWebResult]:
    """Select and deduplicate discovery results for prompt construction."""
    deduped: list[_DiscoveryWebResult] = []
    seen_urls: set[str] = set()
    for result in results:
        url_key = result.url.strip().lower()
        if not url_key or url_key in seen_urls:
            continue
        seen_urls.add(url_key)
        deduped.append(result)

    if not lane_balanced:
        return deduped[:DISCOVERY_PROMPT_MAX_WEB_RESULTS]

    grouped: dict[str, list[_DiscoveryWebResult]] = {}
    group_order: list[str] = []
    for result in deduped:
        lane_key = result.lane_name or "general"
        if lane_key not in grouped:
            grouped[lane_key] = []
            group_order.append(lane_key)
        grouped[lane_key].append(result)

    selected: list[_DiscoveryWebResult] = []
    indices = {lane_key: 0 for lane_key in group_order}
    while len(selected) < DISCOVERY_PROMPT_MAX_WEB_RESULTS:
        advanced = False
        for lane_key in group_order:
            lane_results = grouped[lane_key]
            lane_index = indices[lane_key]
            if lane_index >= len(lane_results):
                continue
            selected.append(lane_results[lane_index])
            indices[lane_key] = lane_index + 1
            advanced = True
            if len(selected) >= DISCOVERY_PROMPT_MAX_WEB_RESULTS:
                break
        if not advanced:
            break

    return selected


def _prompt_snippet(snippet: str | None) -> str:
    if not snippet:
        return ""
    return snippet.strip().replace("\n", " ")[:DISCOVERY_PROMPT_SNIPPET_CHARS]


def _clean_queries(queries: Iterable[str]) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for query in queries:
        if not isinstance(query, str):
            continue
        normalized = query.strip().strip(".")
        if not normalized:
            continue
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(normalized)
        if len(cleaned) >= 4:
            break
    return cleaned


def _refine_lane_queries(
    *,
    target: Literal["feeds", "podcasts", "reddit"],
    queries: Iterable[str],
    lane_goal: str,
    inferred_topics: list[str],
    topic_summary: str,
) -> list[str]:
    cleaned = _clean_queries(queries)
    keyword_pool = _merge_topics(inferred_topics, max_topics=6)
    if not keyword_pool:
        keyword_pool = _merge_topics([lane_goal], [topic_summary], max_topics=4)
    patterns = _query_patterns_for_target(target)

    if not cleaned:
        cleaned = [keyword_pool[0] if keyword_pool else lane_goal or "current developments"]

    refined: list[str] = []
    for idx, query in enumerate(cleaned[:4]):
        template = patterns[idx % len(patterns)]
        focus = _query_focus_phrase(query, keyword_pool, idx)
        candidate = template.format(focus=focus)
        refined.append(_enforce_query_word_range(candidate, target))

    while len(refined) < 3:
        idx = len(refined)
        template = patterns[idx % len(patterns)]
        focus_seed = keyword_pool[idx % len(keyword_pool)] if keyword_pool else lane_goal
        focus = _query_focus_phrase(focus_seed, keyword_pool, idx)
        candidate = template.format(focus=focus)
        refined.append(_enforce_query_word_range(candidate, target))

    normalized = _clean_queries(refined)
    if len(normalized) >= 2:
        return normalized[:4]

    fallback_focus = keyword_pool[0] if keyword_pool else "high-signal sources"
    return [
        _enforce_query_word_range(
            f"best {_target_query_keyword(target)} for {fallback_focus}",
            target,
        ),
        _enforce_query_word_range(
            f"top {_target_query_keyword(target)} about {fallback_focus}",
            target,
        ),
    ]


def _query_focus_phrase(query: str, keyword_pool: list[str], index: int) -> str:
    focus = query.strip().strip(".,;:!?")
    if not focus:
        focus = keyword_pool[index % len(keyword_pool)] if keyword_pool else "current trends"

    focus_tokens = [token for token in focus.split() if token]
    while focus_tokens and focus_tokens[0].lower() in {
        "best",
        "top",
        "popular",
        "weekly",
        "find",
        "search",
        "discover",
        "identify",
    }:
        focus_tokens.pop(0)

    deduped_focus_tokens: list[str] = []
    seen_focus: set[str] = set()
    for token in focus_tokens:
        lowered = token.lower()
        if lowered in seen_focus:
            continue
        seen_focus.add(lowered)
        deduped_focus_tokens.append(token)
    focus_tokens = deduped_focus_tokens

    if len(focus_tokens) < 2 and keyword_pool:
        keyword = keyword_pool[index % len(keyword_pool)]
        keyword_tokens = [token for token in keyword.split() if token]
        for token in keyword_tokens:
            if len(focus_tokens) >= 4:
                break
            if token.lower() in {existing.lower() for existing in focus_tokens}:
                continue
            focus_tokens.append(token)

    if not focus_tokens:
        return "current developments"
    return " ".join(focus_tokens[:4])


def _query_patterns_for_target(
    target: Literal["feeds", "podcasts", "reddit"],
) -> list[str]:
    if target == "podcasts":
        return [
            "best {focus} podcast episodes",
            "top {focus} podcast rss feeds",
            "weekly {focus} interview podcasts",
            "{focus} long-form educational podcasts",
        ]

    if target == "reddit":
        return [
            "best subreddits for {focus}",
            "active reddit communities about {focus}",
            "top reddit threads on {focus}",
            "{focus} subreddit recommendations and discussions",
        ]

    return [
        "best {focus} newsletters and rss feeds",
        "top {focus} substack and atom feeds",
        "weekly {focus} analysis newsletter feeds",
        "credible {focus} editorial rss sources",
    ]


def _target_query_keyword(target: Literal["feeds", "podcasts", "reddit"]) -> str:
    if target == "podcasts":
        return "podcasts"
    if target == "reddit":
        return "reddit communities"
    return "newsletters and rss feeds"


def _enforce_query_word_range(query: str, target: Literal["feeds", "podcasts", "reddit"]) -> str:
    tokens = [token.strip(".,;:!?") for token in query.split()]
    tokens = [token for token in tokens if token]

    deduped_tokens: list[str] = []
    seen_tokens: set[str] = set()
    for token in tokens:
        lowered = token.lower()
        if lowered in seen_tokens:
            continue
        seen_tokens.add(lowered)
        deduped_tokens.append(token)
    tokens = deduped_tokens

    if len(tokens) > 10:
        tokens = tokens[:10]

    target_fillers = {
        "feeds": ["newsletter", "rss", "feeds"],
        "podcasts": ["podcast", "episodes"],
        "reddit": ["reddit", "communities"],
    }
    fillers = target_fillers[target]
    filler_index = 0
    while len(tokens) < 5:
        tokens.append(fillers[filler_index % len(fillers)])
        filler_index += 1

    return " ".join(tokens)


def _merge_topics(*topic_lists: Iterable[str], max_topics: int = 8) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for topics in topic_lists:
        for topic in topics:
            if not isinstance(topic, str):
                continue
            normalized = topic.strip().strip(".,;:")
            if not normalized:
                continue
            key = normalized.lower()
            if key in seen:
                continue
            seen.add(key)
            merged.append(normalized)
            if len(merged) >= max_topics:
                return merged
    return merged


def _build_profile_fallback_summary(first_name: str, topics: list[str]) -> str:
    cleaned_topics = _merge_topics(topics, max_topics=3)
    if cleaned_topics:
        return f"{first_name} interested in {', '.join(cleaned_topics)}"
    return first_name
