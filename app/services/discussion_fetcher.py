"""Discussion ingestion service for news content."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.db import Content, ContentDiscussion, NewsItem
from app.services.agent_vm_runtime import SYSTEM_USER_ID
from app.services.content_metadata_merge import refresh_merge_content_metadata
from app.services.discussion_comments import (
    DiscussionFetchError,
    NormalizedDiscussion,
    TerminalDiscussionUnavailable,
    compact_text,
    extract_hackernews_item_id,
    extract_reddit_submission_id,
    fetch_hackernews_comments,
    fetch_reddit_comments,
    get_reddit_client,
    is_hackernews_discussion,
    is_reddit_discussion,
    normalize_reddit_discussion_url,
)
from app.services.feed_research_runtime import SandboxFeedHttpService, sandboxed_http_service
from app.utils.url_utils import is_domain_or_subdomain, normalize_http_url

logger = get_logger(__name__)

DEFAULT_DISCUSSION_COMMENT_CAP = 500
TECHMEME_TOKEN_PATTERN = re.compile(r"/(\d{6})/(p\d+)")
TECHMEME_ANCHOR_PATTERN = re.compile(r"a(\d{6}p\d+)")
TOP_COMMENT_SKIP_AUTHORS = {"AutoModerator", "[deleted]", "automoderator"}
TOP_COMMENT_SKIP_SUFFIXES = ("-ModTeam",)

SOCIAL_DOMAINS: frozenset[str] = frozenset(
    {
        "x.com",
        "twitter.com",
        "news.ycombinator.com",
        "reddit.com",
        "www.reddit.com",
        "old.reddit.com",
        "threads.net",
        "www.threads.net",
        "bsky.app",
        "mastodon.social",
        "linkedin.com",
        "www.linkedin.com",
    }
)


@dataclass(frozen=True)
class DiscussionFetchResult:
    """Outcome for one discussion ingestion attempt."""

    success: bool
    status: str
    error_message: str | None = None
    retryable: bool = True


@dataclass(frozen=True)
class DiscussionPayload:
    """Built discussion payload before persistence."""

    status: str
    mode: str
    payload: dict[str, Any]
    error_message: str | None = None


def fetch_and_store_discussion(
    db: Session,
    content_id: int,
    comment_cap: int = DEFAULT_DISCUSSION_COMMENT_CAP,
) -> DiscussionFetchResult:
    """Fetch and persist discussion payload for one content item.

    Args:
        db: Active SQLAlchemy session.
        content_id: Content identifier.
        comment_cap: Maximum number of comments to persist for comment-based platforms.

    Returns:
        DiscussionFetchResult describing persistence and retry behavior.
    """
    content = db.query(Content).filter(Content.id == content_id).first()
    if content is None:
        return DiscussionFetchResult(
            success=False,
            status="failed",
            error_message="Content not found",
            retryable=False,
        )

    base_metadata = dict(content.content_metadata or {})
    metadata = dict(base_metadata)
    discussion_url = _extract_discussion_url(metadata)
    platform = _normalize_platform(metadata.get("platform") or content.platform)

    try:
        payload = _build_discussion_payload(
            platform=platform,
            discussion_url=discussion_url,
            metadata=metadata,
            comment_cap=comment_cap,
        )
    except Exception as exc:  # noqa: BLE001
        return _handle_discussion_fetch_exception(
            exc,
            item_id=content_id,
            item_label="content",
            operation="fetch_and_store_discussion",
            platform=platform,
            discussion_url=discussion_url,
            persist_failure=lambda error_message: _upsert_content_discussion(
                db,
                content_id=content_id,
                platform=platform,
                status="failed",
                discussion_data=_empty_discussion_data(discussion_url),
                error_message=error_message,
                set_fetched_at=False,
            ),
        )

    _upsert_content_discussion(
        db,
        content_id=content_id,
        platform=platform,
        status=payload.status,
        discussion_data=payload.payload,
        error_message=payload.error_message,
        set_fetched_at=True,
    )

    did_change_metadata = False
    did_change_metadata |= _apply_discussion_preview_metadata(
        metadata,
        discussion_data=payload.payload,
        mode=payload.mode,
    )

    if did_change_metadata:
        content.content_metadata = refresh_merge_content_metadata(
            db,
            content_id=content_id,
            base_metadata=base_metadata,
            updated_metadata=metadata,
        )
        db.commit()

    return _result_from_payload(payload)


def fetch_and_store_news_item_discussion(
    db: Session,
    news_item_id: int,
    comment_cap: int = DEFAULT_DISCUSSION_COMMENT_CAP,
) -> DiscussionFetchResult:
    """Fetch and persist discussion payload directly on one news item."""
    item = db.query(NewsItem).filter(NewsItem.id == news_item_id).first()
    if item is None:
        return DiscussionFetchResult(
            success=False,
            status="failed",
            error_message="News item not found",
            retryable=False,
        )

    metadata = dict(item.raw_metadata or {})
    discussion_url = _extract_discussion_url(metadata) or normalize_http_url(item.discussion_url)
    platform = _normalize_platform(metadata.get("platform") or item.platform)

    try:
        payload = _build_discussion_payload(
            platform=platform,
            discussion_url=discussion_url,
            metadata=metadata,
            comment_cap=comment_cap,
        )
    except Exception as exc:  # noqa: BLE001
        return _handle_discussion_fetch_exception(
            exc,
            item_id=news_item_id,
            item_label="news item",
            operation="fetch_and_store_news_item_discussion",
            platform=platform,
            discussion_url=discussion_url,
            persist_failure=lambda error_message: _persist_news_item_discussion_metadata(
                db,
                news_item_id=news_item_id,
                status="failed",
                discussion_data=_empty_discussion_data(discussion_url),
                error_message=error_message,
                set_fetched_at=False,
            ),
        )

    _persist_news_item_discussion_metadata(
        db,
        news_item_id=news_item_id,
        status=payload.status,
        discussion_data=payload.payload,
        error_message=payload.error_message,
        set_fetched_at=True,
    )

    return _result_from_payload(payload)


def _build_discussion_payload(
    *,
    platform: str,
    discussion_url: str | None,
    metadata: dict[str, Any],
    comment_cap: int,
) -> DiscussionPayload:
    source_url = discussion_url or ""

    if _is_techmeme(platform, discussion_url):
        if not source_url:
            return _missing_techmeme_payload()
        with sandboxed_http_service(user_id=SYSTEM_USER_ID) as http_service:
            return _build_techmeme_payload(
                source_url,
                metadata,
                http_service=http_service,
            )

    if is_hackernews_discussion(platform, discussion_url):
        return _build_hackernews_payload(source_url, comment_cap)

    if is_reddit_discussion(platform, discussion_url):
        return _build_reddit_payload(source_url, comment_cap)

    return _unsupported_payload(source_url, platform)


def _unsupported_payload(source_url: str, platform: str) -> DiscussionPayload:
    """Return a partial payload for unsupported discussion platforms."""
    return DiscussionPayload(
        status="partial",
        mode="none",
        payload=_empty_discussion_data(source_url),
        error_message=f"Unsupported discussion platform: {platform or 'unknown'}",
    )


def _build_techmeme_payload(
    source_url: str,
    metadata: dict[str, Any],
    *,
    http_service: SandboxFeedHttpService,
) -> DiscussionPayload:
    """Build discussion payload for Techmeme.

    Fetches grouped discussion links and converts social/forum links into
    comment entries so the top_comment denormalization loop picks them up.
    """
    groups = _fetch_techmeme_discussion_groups(
        source_url,
        metadata,
        http_service=http_service,
    )
    all_links = _build_group_links(groups)
    social_comments = _extract_social_comments_from_groups(groups)
    status = "completed" if groups else "partial"
    return DiscussionPayload(
        status=status,
        mode="discussion_list",
        payload={
            "mode": "discussion_list",
            "source_url": source_url,
            "discussion_groups": groups,
            "comments": social_comments,
            "compact_comments": [c["compact_text"] for c in social_comments],
            "links": all_links,
            "stats": {
                "group_count": len(groups),
                "item_count": sum(len(group.get("items", [])) for group in groups),
            },
        },
        error_message=None if groups else "No Techmeme discussion groups found",
    )


def _missing_techmeme_payload() -> DiscussionPayload:
    return DiscussionPayload(
        status="partial",
        mode="discussion_list",
        payload={
            "mode": "discussion_list",
            "source_url": None,
            "discussion_groups": [],
            "comments": [],
            "compact_comments": [],
            "links": [],
            "stats": {
                "group_count": 0,
                "item_count": 0,
            },
        },
        error_message="Missing Techmeme discussion URL",
    )


def _build_hackernews_payload(discussion_url: str, comment_cap: int) -> DiscussionPayload:
    item_id = extract_hackernews_item_id(discussion_url)
    if not item_id:
        return DiscussionPayload(
            status="partial",
            mode="comments",
            payload={
                "mode": "comments",
                "source_url": discussion_url,
                "discussion_groups": [],
                "comments": [],
                "compact_comments": [],
                "links": [],
                "stats": {"cap": comment_cap, "fetched_count": 0, "cap_reached": False},
            },
            error_message="Unable to parse Hacker News item id",
        )

    try:
        discussion = fetch_hackernews_comments(
            external_id=item_id,
            discussion_url=discussion_url,
            comment_cap=comment_cap,
        )
    except TerminalDiscussionUnavailable:
        return _empty_provider_payload(
            source_url=discussion_url,
            comment_cap=comment_cap,
            error_message="No Hacker News comments found",
        )
    return _legacy_provider_payload(discussion)


def _build_reddit_payload(discussion_url: str, comment_cap: int) -> DiscussionPayload:
    canonical_url = normalize_reddit_discussion_url(discussion_url) or discussion_url
    submission_id = extract_reddit_submission_id(canonical_url)
    if not submission_id:
        return DiscussionPayload(
            status="partial",
            mode="comments",
            payload={
                "mode": "comments",
                "source_url": canonical_url,
                "discussion_groups": [],
                "comments": [],
                "compact_comments": [],
                "links": [],
                "stats": {"cap": comment_cap, "fetched_count": 0, "cap_reached": False},
            },
            error_message="Unable to parse Reddit submission id",
        )

    client = _get_reddit_client()
    if client is None:
        raise DiscussionFetchError(
            "Reddit API credentials not configured",
            retryable=False,
        )
    discussion = fetch_reddit_comments(
        external_id=submission_id,
        discussion_url=canonical_url,
        comment_cap=comment_cap,
        reddit_client=client,
    )
    return _legacy_provider_payload(discussion)


def _legacy_provider_payload(discussion: NormalizedDiscussion) -> DiscussionPayload:
    source_override = discussion.discussion_url if discussion.platform == "hackernews" else None
    comments = [comment.as_payload(source_url=source_override) for comment in discussion.comments]
    platform_label = "Hacker News" if discussion.platform == "hackernews" else "Reddit"
    return DiscussionPayload(
        status="completed" if comments else "partial",
        mode="comments",
        payload={
            "mode": "comments",
            "source_url": discussion.discussion_url,
            "discussion_groups": [],
            "comments": comments,
            "compact_comments": [comment["compact_text"] for comment in comments],
            "links": [link.as_payload() for link in discussion.links],
            "stats": {
                "cap": discussion.comment_cap,
                "fetched_count": discussion.fetched_count,
                "cap_reached": discussion.cap_reached,
                "total_seen": discussion.total_seen,
                "declared_comment_count": discussion.thread.comment_count,
            },
        },
        error_message=None if comments else f"No {platform_label} comments found",
    )


def _empty_provider_payload(
    *,
    source_url: str,
    comment_cap: int,
    error_message: str,
) -> DiscussionPayload:
    return DiscussionPayload(
        status="partial",
        mode="comments",
        payload={
            "mode": "comments",
            "source_url": source_url,
            "discussion_groups": [],
            "comments": [],
            "compact_comments": [],
            "links": [],
            "stats": {
                "cap": comment_cap,
                "fetched_count": 0,
                "cap_reached": False,
                "total_seen": 0,
            },
        },
        error_message=error_message,
    )


def _fetch_techmeme_discussion_groups(
    discussion_url: str,
    metadata: dict[str, Any],
    *,
    http_service: SandboxFeedHttpService,
) -> list[dict[str, Any]]:
    canonical_url = discussion_url.split("#", maxsplit=1)[0]
    response = http_service.fetch(
        canonical_url,
        headers={"User-Agent": "news_app.discussion/1.0"},
    )
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

    token_candidates = _derive_techmeme_token_candidates(discussion_url, metadata)
    target_span = None
    for token in token_candidates:
        target_span = soup.find("span", attrs={"pml": token})
        if target_span is not None:
            break

    if target_span is None:
        target_span = soup.find("span", attrs={"pml": True})
    if target_span is None:
        return []

    item_block = target_span.find_parent("div", class_="item")
    if item_block is None:
        return []

    grouped_items: dict[str, list[dict[str, str]]] = defaultdict(list)

    for header in item_block.find_all(class_="drhed"):
        label = _normalize_label(header.get_text(" ", strip=True))
        links_container = header.find_next_sibling("span", class_="bls")
        if not label or links_container is None:
            continue

        for anchor in links_container.find_all("a"):
            href = anchor.get("href")
            if not isinstance(href, str) or not href.strip():
                continue
            normalized_url = normalize_http_url(urljoin(canonical_url, href))
            if not normalized_url:
                continue
            grouped_items[label].append(
                {
                    "title": anchor.get_text(" ", strip=True) or normalized_url,
                    "url": normalized_url,
                }
            )

    groups: list[dict[str, Any]] = []
    for label, items in grouped_items.items():
        deduped_items = _dedupe_group_items(items)
        if not deduped_items:
            continue
        groups.append({"label": label, "items": deduped_items})

    return groups


def _derive_techmeme_token_candidates(
    discussion_url: str,
    metadata: dict[str, Any],
) -> list[str]:
    tokens: list[str] = []

    match = TECHMEME_TOKEN_PATTERN.search(discussion_url)
    if match:
        tokens.append(f"{match.group(1)}{match.group(2)}")

    fragment = urlparse(discussion_url).fragment
    anchor_match = TECHMEME_ANCHOR_PATTERN.search(fragment)
    if anchor_match:
        tokens.append(anchor_match.group(1))

    aggregator = metadata.get("aggregator")
    if isinstance(aggregator, dict):
        external_id = aggregator.get("external_id")
        if isinstance(external_id, str):
            cleaned = external_id.strip().lstrip("a").replace("#", "")
            if cleaned:
                tokens.append(cleaned)

    deduped: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        if token in seen:
            continue
        seen.add(token)
        deduped.append(token)
    return deduped


def _build_group_links(groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for group in groups:
        label = str(group.get("label") or "").strip()
        for item in group.get("items", []):
            if not isinstance(item, dict):
                continue
            url = normalize_http_url(item.get("url"))
            if not url or url in seen:
                continue
            seen.add(url)
            entries.append(
                {
                    "url": url,
                    "source": "discussion_group",
                    "group_label": label,
                    "title": item.get("title") or url,
                }
            )
    return entries


def _is_social_url(url: str) -> bool:
    """Return True if the URL belongs to a known social/forum domain."""
    host = urlparse(url).hostname
    return any(is_domain_or_subdomain(host, domain) for domain in SOCIAL_DOMAINS)


def _extract_social_comments_from_groups(
    groups: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Convert social/forum links from Techmeme groups into comment entries.

    This allows the top_comment denormalization loop to pick up a representative
    social comment for Techmeme items that would otherwise have no comments.
    """
    comments: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for group in groups:
        for item in group.get("items", []):
            if not isinstance(item, dict):
                continue
            url = item.get("url") or ""
            if not url or url in seen_urls:
                continue
            if not _is_social_url(url):
                continue
            seen_urls.add(url)
            domain = urlparse(url).netloc.lower().removeprefix("www.")
            title = item.get("title") or url
            comments.append(
                {
                    "comment_id": f"tm_{len(comments)}",
                    "parent_id": None,
                    "author": domain,
                    "text": title,
                    "compact_text": compact_text(title),
                    "depth": 0,
                    "created_at": None,
                    "source_url": url,
                }
            )
    return comments


def _normalize_label(raw: str) -> str:
    return raw.strip().rstrip(":")


def _dedupe_group_items(items: list[dict[str, str]]) -> list[dict[str, str]]:
    deduped: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in items:
        url = normalize_http_url(item.get("url"))
        if not url or url in seen:
            continue
        seen.add(url)
        deduped.append(
            {
                "title": item.get("title") or url,
                "url": url,
            }
        )
    return deduped


def _get_reddit_client() -> Any | None:
    """Compatibility seam for legacy tests and configured PRAW access."""
    return get_reddit_client()


def _extract_discussion_url(metadata: dict[str, Any]) -> str | None:
    raw_url = metadata.get("discussion_url")
    return normalize_http_url(raw_url)


def _normalize_platform(platform: Any) -> str:
    if not isinstance(platform, str):
        return ""
    return platform.strip().lower()


def _is_techmeme(platform: str, discussion_url: str | None) -> bool:
    if platform == "techmeme" and not discussion_url:
        return True
    if not discussion_url:
        return False
    parsed = urlparse(discussion_url)
    return parsed.scheme.lower() in {"http", "https"} and is_domain_or_subdomain(
        parsed.hostname,
        "techmeme.com",
    )


def _upsert_content_discussion(
    db: Session,
    *,
    content_id: int,
    platform: str,
    status: str,
    discussion_data: dict[str, Any],
    error_message: str | None,
    set_fetched_at: bool,
) -> None:
    row = db.query(ContentDiscussion).filter(ContentDiscussion.content_id == content_id).first()
    if row is None:
        row = ContentDiscussion(content_id=content_id)
        db.add(row)

    row.platform = platform or None
    row.status = status
    row.discussion_data = discussion_data
    row.error_message = error_message
    row.fetched_at = datetime.now(UTC).replace(tzinfo=None) if set_fetched_at else None
    db.commit()


def _handle_discussion_fetch_exception(
    exc: Exception,
    *,
    item_id: int,
    item_label: str,
    operation: str,
    platform: str,
    discussion_url: str | None,
    persist_failure: Callable[[str], None],
) -> DiscussionFetchResult:
    if isinstance(exc, httpx.TimeoutException):
        error_message = f"Discussion fetch timed out: {exc}"
        persist_failure(error_message)
        return DiscussionFetchResult(
            success=False,
            status="failed",
            error_message=error_message,
            retryable=True,
        )

    error_message = f"Discussion fetch failed: {exc}"
    if isinstance(exc, DiscussionFetchError):
        logger.error(
            "Discussion fetch failed for %s %s",
            item_label,
            item_id,
            extra={
                "component": "discussion_fetcher",
                "operation": operation,
                "item_id": str(item_id),
                "context_data": {
                    "platform": platform,
                    "discussion_url": discussion_url,
                    "retryable": exc.retryable,
                    "error": str(exc),
                },
            },
        )
        persist_failure(error_message)
        return DiscussionFetchResult(
            success=False,
            status="failed",
            error_message=error_message,
            retryable=exc.retryable,
        )

    logger.exception(
        "Discussion fetch failed for %s %s",
        item_label,
        item_id,
        extra={
            "component": "discussion_fetcher",
            "operation": operation,
            "item_id": str(item_id),
            "context_data": {
                "platform": platform,
                "discussion_url": discussion_url,
                "error": str(exc),
            },
        },
    )
    persist_failure(error_message)
    return DiscussionFetchResult(
        success=False,
        status="failed",
        error_message=error_message,
        retryable=True,
    )


def _persist_news_item_discussion_metadata(
    db: Session,
    *,
    news_item_id: int,
    status: str,
    discussion_data: dict[str, Any],
    error_message: str | None,
    set_fetched_at: bool,
) -> None:
    item = db.query(NewsItem).filter(NewsItem.id == news_item_id).first()
    if item is None:
        return

    metadata = dict(item.raw_metadata or {})
    metadata["discussion_payload"] = discussion_data
    metadata["discussion_status"] = status
    _set_metadata_key(metadata, key="discussion_error", value=error_message)
    _set_metadata_key(
        metadata,
        key="discussion_fetched_at",
        value=datetime.now(UTC).isoformat() if set_fetched_at else None,
    )
    _apply_discussion_preview_metadata(
        metadata,
        discussion_data=discussion_data,
        mode=str(discussion_data.get("mode") or "none"),
    )

    item.raw_metadata = metadata
    db.commit()


def _empty_discussion_data(source_url: str | None) -> dict[str, Any]:
    return {
        "mode": "none",
        "source_url": source_url,
        "comments": [],
        "compact_comments": [],
        "discussion_groups": [],
        "links": [],
        "stats": {},
    }


def _extract_discussion_preview_fields(
    discussion_data: dict[str, Any],
    *,
    mode: str,
) -> tuple[dict[str, str] | None, int | None]:
    comments = discussion_data.get("comments", [])

    top_comment: dict[str, str] | None = None
    stats = discussion_data.get("stats", {})

    if mode == "comments":
        for comment_entry in comments:
            if not isinstance(comment_entry, dict):
                continue
            author = str(comment_entry.get("author") or "unknown")
            if author in TOP_COMMENT_SKIP_AUTHORS or any(
                author.endswith(suffix) for suffix in TOP_COMMENT_SKIP_SUFFIXES
            ):
                continue
            text = comment_entry.get("compact_text") or comment_entry.get("text") or ""
            if text.strip():
                top_comment = {"author": author, "text": str(text)}
                break

    if mode == "comments":
        comment_count = stats.get("declared_comment_count")
    elif mode == "discussion_list":
        comment_count = stats.get("item_count")
        if comment_count is None and comments:
            comment_count = len(comments)
    else:
        comment_count = None

    return top_comment, comment_count


def _apply_discussion_preview_metadata(
    metadata: dict[str, Any],
    *,
    discussion_data: dict[str, Any],
    mode: str,
) -> bool:
    top_comment, comment_count = _extract_discussion_preview_fields(
        discussion_data,
        mode=mode,
    )
    did_change = False
    did_change |= _set_metadata_key(metadata, key="top_comment", value=top_comment)
    did_change |= _set_metadata_key(metadata, key="comment_count", value=comment_count)
    return did_change


def _result_from_payload(payload: DiscussionPayload) -> DiscussionFetchResult:
    if payload.status == "failed":
        return DiscussionFetchResult(
            success=False,
            status=payload.status,
            error_message=payload.error_message,
            retryable=True,
        )

    return DiscussionFetchResult(
        success=True,
        status=payload.status,
        error_message=payload.error_message,
        retryable=False,
    )


def _set_metadata_key(
    metadata: dict[str, Any],
    *,
    key: str,
    value: Any,
) -> bool:
    if value is None:
        if key in metadata:
            metadata.pop(key, None)
            return True
        return False
    if metadata.get(key) == value:
        return False
    metadata[key] = value
    return True
