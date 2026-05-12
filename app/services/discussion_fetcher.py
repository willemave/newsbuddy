"""Discussion ingestion service for news content."""

from __future__ import annotations

import re
from collections import defaultdict, deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from html import unescape
from typing import Any
from urllib.parse import urljoin, urlparse, urlunparse

import httpx
import praw
import prawcore
from bs4 import BeautifulSoup
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.core.settings import get_settings
from app.models.db import Content, ContentDiscussion, NewsItem
from app.services.content_metadata_merge import refresh_merge_content_metadata
from app.utils.url_utils import normalize_http_url

logger = get_logger(__name__)
settings = get_settings()

DEFAULT_DISCUSSION_COMMENT_CAP = 500
HN_API_BASE = "https://hacker-news.firebaseio.com/v0"
URL_PATTERN = re.compile(r"https?://[^\s\"'<>]+")
TECHMEME_TOKEN_PATTERN = re.compile(r"/(\d{6})/(p\d+)")
TECHMEME_ANCHOR_PATTERN = re.compile(r"a(\d{6}p\d+)")
HN_ITEM_PATTERN = re.compile(r"item\?id=(\d+)")
REDDIT_COMMENTS_PATTERN = re.compile(r"/comments/([a-z0-9]+)/?", re.IGNORECASE)
TOP_COMMENT_SKIP_AUTHORS = {"AutoModerator", "[deleted]", "automoderator"}
TOP_COMMENT_SKIP_SUFFIXES = ("-ModTeam",)
REDDIT_DEFAULT_USER_AGENT = "news_app.discussion/1.0 (by u/anonymous)"

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

_reddit_client: praw.Reddit | None = None


class DiscussionFetchError(Exception):
    """Error wrapper with retryability hints for discussion fetches."""

    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


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
        return _build_techmeme_payload(source_url, metadata)

    if _is_hackernews(platform, discussion_url):
        return _build_hackernews_payload(source_url, comment_cap)

    if _is_reddit(platform, discussion_url):
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
) -> DiscussionPayload:
    """Build discussion payload for Techmeme.

    Fetches grouped discussion links and converts social/forum links into
    comment entries so the top_comment denormalization loop picks them up.
    """
    if not source_url:
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

    groups = _fetch_techmeme_discussion_groups(source_url, metadata)
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


def _build_hackernews_payload(discussion_url: str, comment_cap: int) -> DiscussionPayload:
    item_id = _extract_hn_item_id(discussion_url)
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

    timeout = httpx.Timeout(timeout=settings.http_timeout_seconds, connect=10.0)
    comments: list[dict[str, Any]] = []
    url_titles: dict[str, str] = {}
    fetched_count = 0
    cap_reached = False
    total_seen = 0

    with httpx.Client(timeout=timeout) as client:
        root_item = _fetch_hn_item(client, item_id)
        if not isinstance(root_item, dict):
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
                    "stats": {
                        "cap": comment_cap,
                        "fetched_count": 0,
                        "cap_reached": False,
                        "total_seen": 0,
                    },
                },
                error_message="Unable to load Hacker News story",
            )

        queue: deque[tuple[int, int, int | None]] = deque(
            (int(child_id), 0, None) for child_id in root_item.get("kids", [])
        )

        while queue:
            comment_id, depth, parent_id = queue.popleft()
            total_seen += 1

            if fetched_count >= comment_cap:
                cap_reached = True
                break

            comment_item = _fetch_hn_item(client, str(comment_id))
            if not isinstance(comment_item, dict):
                continue
            if comment_item.get("type") != "comment":
                continue
            if comment_item.get("deleted") or comment_item.get("dead"):
                continue

            raw_html = str(comment_item.get("text") or "")
            url_titles.update(_extract_anchor_titles_from_html(raw_html))
            text = _clean_html_text(raw_html)
            if not text:
                continue

            comments.append(
                {
                    "comment_id": str(comment_item.get("id") or comment_id),
                    "parent_id": str(parent_id) if parent_id is not None else None,
                    "author": comment_item.get("by") or "unknown",
                    "text": text,
                    "compact_text": _compact_text(text),
                    "depth": depth,
                    "created_at": _unix_to_iso(comment_item.get("time")),
                    "source_url": discussion_url,
                }
            )
            fetched_count += 1

            for child_id in comment_item.get("kids", []):
                with_id = int(child_id)
                queue.append((with_id, depth + 1, int(comment_item.get("id") or comment_id)))

    links = _extract_links_from_comments(comments, url_titles=url_titles)
    status = "completed" if comments else "partial"
    return DiscussionPayload(
        status=status,
        mode="comments",
        payload={
            "mode": "comments",
            "source_url": discussion_url,
            "discussion_groups": [],
            "comments": comments,
            "compact_comments": [item["compact_text"] for item in comments],
            "links": links,
            "stats": {
                "cap": comment_cap,
                "fetched_count": fetched_count,
                "cap_reached": cap_reached,
                "total_seen": total_seen,
                "declared_comment_count": root_item.get("descendants"),
            },
        },
        error_message=None if comments else "No Hacker News comments found",
    )


def _build_reddit_payload(discussion_url: str, comment_cap: int) -> DiscussionPayload:
    canonical_url = _normalize_reddit_discussion_url(discussion_url) or discussion_url
    submission_id = _extract_reddit_submission_id(canonical_url)
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

    comments: list[dict[str, Any]] = []
    url_titles: dict[str, str] = {}
    cap_reached = False
    total_seen = 0

    try:
        submission = client.submission(id=submission_id)
        submission.comment_sort = "top"
        _ = submission.title  # Force fetch to surface API/auth errors.
        submission.comments.replace_more(limit=0)
    except Exception as exc:  # noqa: BLE001
        raise DiscussionFetchError(
            f"Reddit API request failed: {exc}",
            retryable=_is_retryable_reddit_error(exc),
        ) from exc

    def walk(nodes: Iterable[Any], depth: int, parent_id: str | None) -> None:
        nonlocal cap_reached, total_seen
        for node in nodes:
            if len(comments) >= comment_cap:
                cap_reached = True
                return

            if _is_reddit_more_comments(node):
                continue

            total_seen += 1
            body_html = getattr(node, "body_html", None) or ""
            if body_html:
                url_titles.update(_extract_anchor_titles_from_html(str(body_html)))
            body = getattr(node, "body", None) or body_html or ""
            text = _clean_html_text(str(body))
            comment_id = str(getattr(node, "id", "") or "").strip()

            if text and comment_id:
                author_obj = getattr(node, "author", None)
                author = getattr(author_obj, "name", None) or "unknown"
                comments.append(
                    {
                        "comment_id": comment_id,
                        "parent_id": parent_id,
                        "author": author,
                        "text": text,
                        "compact_text": _compact_text(text),
                        "depth": depth,
                        "created_at": _unix_to_iso(getattr(node, "created_utc", None)),
                        "source_url": canonical_url,
                    }
                )

            replies = getattr(node, "replies", None)
            if replies:
                next_parent_id = comment_id or parent_id
                walk(replies, depth + 1, next_parent_id)
                if cap_reached:
                    return

    walk(submission.comments, depth=0, parent_id=None)

    links = _extract_links_from_comments(comments, url_titles=url_titles)
    status = "completed" if comments else "partial"
    return DiscussionPayload(
        status=status,
        mode="comments",
        payload={
            "mode": "comments",
            "source_url": canonical_url,
            "discussion_groups": [],
            "comments": comments,
            "compact_comments": [item["compact_text"] for item in comments],
            "links": links,
            "stats": {
                "cap": comment_cap,
                "fetched_count": len(comments),
                "cap_reached": cap_reached,
                "total_seen": total_seen,
                "declared_comment_count": getattr(submission, "num_comments", None),
            },
        },
        error_message=None if comments else "No Reddit comments found",
    )


def _fetch_hn_item(client: httpx.Client, item_id: str) -> dict[str, Any] | None:
    url = f"{HN_API_BASE}/item/{item_id}.json"
    response = client.get(url)
    response.raise_for_status()
    payload = response.json()
    if isinstance(payload, dict):
        return payload
    return None


def _fetch_techmeme_discussion_groups(
    discussion_url: str,
    metadata: dict[str, Any],
) -> list[dict[str, Any]]:
    canonical_url = discussion_url.split("#", maxsplit=1)[0]
    response = httpx.get(
        canonical_url,
        timeout=httpx.Timeout(timeout=settings.http_timeout_seconds, connect=10.0),
        headers={"User-Agent": "news_app.discussion/1.0"},
        follow_redirects=True,
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
    host = urlparse(url).netloc.lower()
    return any(host == domain or host.endswith("." + domain) for domain in SOCIAL_DOMAINS)


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
                    "compact_text": _compact_text(title),
                    "depth": 0,
                    "created_at": None,
                    "source_url": url,
                }
            )
    return comments


def _extract_links_from_comments(
    comments: list[dict[str, Any]],
    url_titles: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    links: list[dict[str, Any]] = []
    seen: set[str] = set()

    for comment in comments:
        text = str(comment.get("text") or "")
        comment_id = str(comment.get("comment_id") or "") or None
        for url in _extract_urls(text):
            normalized_url = normalize_http_url(url)
            if not normalized_url or normalized_url in seen:
                continue
            seen.add(normalized_url)
            entry: dict[str, Any] = {
                "url": normalized_url,
                "source": "comment",
                "comment_id": comment_id,
            }
            if url_titles and normalized_url in url_titles:
                entry["title"] = url_titles[normalized_url]
            links.append(entry)

    return links


def _extract_urls(text: str) -> list[str]:
    if not text:
        return []
    return URL_PATTERN.findall(text)


_TRIVIAL_ANCHOR_TEXTS = frozenset(
    {
        "here",
        "link",
        "click here",
        "this",
        "source",
        "this link",
        "more",
        "read more",
        "article",
        "url",
    }
)


def _is_url_like_text(text: str, url: str) -> bool:
    """Return True if anchor text is essentially just the URL itself."""
    stripped = text.strip().rstrip("/")
    url_stripped = url.strip().rstrip("/")
    if stripped == url_stripped:
        return True
    # Also match when anchor text is URL without scheme.
    for prefix in ("https://", "http://"):
        if url_stripped.startswith(prefix) and stripped == url_stripped[len(prefix) :]:
            return True
        without_scheme = url_stripped[len(prefix) :]
        if (
            url_stripped.startswith(prefix)
            and without_scheme.startswith("www.")
            and stripped == without_scheme[4:]
        ):
            return True
    return False


def _extract_anchor_titles_from_html(html: str) -> dict[str, str]:
    """Extract {normalized_url: anchor_text} from <a> tags in HTML.

    Skips anchors with trivial text (e.g. "here", "link") or text that is
    just the URL itself.
    """
    if not html:
        return {}
    soup = BeautifulSoup(html, "html.parser")
    titles: dict[str, str] = {}
    for anchor in soup.find_all("a", href=True):
        href_value = anchor.get("href")
        href = href_value if isinstance(href_value, str) else ""
        text = anchor.get_text(" ", strip=True)
        if not href or not text:
            continue
        if text.lower() in _TRIVIAL_ANCHOR_TEXTS:
            continue
        if _is_url_like_text(text, href):
            continue
        normalized = normalize_http_url(href)
        if normalized and normalized not in titles:
            titles[normalized] = text
    return titles


def _compact_text(text: str, max_chars: int = 400) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[: max_chars - 3].rstrip() + "..."


def _clean_html_text(value: str) -> str:
    if not value:
        return ""
    soup = BeautifulSoup(unescape(value), "html.parser")
    return " ".join(soup.get_text(" ", strip=True).split())


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


def _get_reddit_client() -> praw.Reddit | None:
    global _reddit_client
    if _reddit_client is not None:
        return _reddit_client

    client_id = settings.reddit_client_id
    client_secret = settings.reddit_client_secret
    if not client_id or not client_secret:
        return None

    reddit_kwargs: dict[str, Any] = {
        "client_id": client_id,
        "client_secret": client_secret,
        "user_agent": settings.reddit_user_agent or REDDIT_DEFAULT_USER_AGENT,
        "check_for_updates": False,
        "timeout": settings.http_timeout_seconds,
    }

    if not settings.reddit_read_only and settings.reddit_username and settings.reddit_password:
        reddit_kwargs["username"] = settings.reddit_username
        reddit_kwargs["password"] = settings.reddit_password

    client = praw.Reddit(**reddit_kwargs)
    client.read_only = settings.reddit_read_only or not (
        settings.reddit_username and settings.reddit_password
    )
    _reddit_client = client
    return _reddit_client


def _is_retryable_reddit_error(exc: Exception) -> bool:
    if isinstance(exc, prawcore.exceptions.TooManyRequests):
        return True

    if isinstance(
        exc,
        (
            prawcore.exceptions.Forbidden,
            prawcore.exceptions.NotFound,
            prawcore.exceptions.BadRequest,
            prawcore.exceptions.OAuthException,
        ),
    ):
        return False

    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None)
    if isinstance(status_code, int):
        if status_code == 429 or status_code >= 500:
            return True
        if 400 <= status_code < 500:
            return False

    message = str(exc).lower()
    return "blocked by network security" not in message


def _is_reddit_more_comments(node: Any) -> bool:
    return node.__class__.__name__ == "MoreComments"


def _normalize_reddit_discussion_url(url: str) -> str | None:
    normalized = normalize_http_url(url)
    if not normalized:
        return None

    parsed = urlparse(normalized)
    host = parsed.netloc.lower()
    if host in {"reddit.com", "old.reddit.com", "www.reddit.com"}:
        rebuilt = parsed._replace(scheme="https", netloc="www.reddit.com")
        return urlunparse(rebuilt)
    return normalized


def _extract_reddit_submission_id(url: str) -> str | None:
    parsed = urlparse(url)
    match = REDDIT_COMMENTS_PATTERN.search(parsed.path)
    if not match:
        return None
    return match.group(1).lower()


def _extract_discussion_url(metadata: dict[str, Any]) -> str | None:
    raw_url = metadata.get("discussion_url")
    return normalize_http_url(raw_url)


def _normalize_platform(platform: Any) -> str:
    if not isinstance(platform, str):
        return ""
    return platform.strip().lower()


def _is_hackernews(platform: str, discussion_url: str | None) -> bool:
    if platform in {"hackernews", "hn"}:
        return True
    if not discussion_url:
        return False
    host = urlparse(discussion_url).netloc.lower()
    return "ycombinator.com" in host and "item" in discussion_url


def _is_reddit(platform: str, discussion_url: str | None) -> bool:
    if platform == "reddit":
        return True
    if not discussion_url:
        return False
    host = urlparse(discussion_url).netloc.lower()
    return "reddit.com" in host or host.endswith("redd.it")


def _is_techmeme(platform: str, discussion_url: str | None) -> bool:
    if platform == "techmeme":
        return True
    if not discussion_url:
        return False
    host = urlparse(discussion_url).netloc.lower()
    return "techmeme.com" in host


def _extract_hn_item_id(url: str) -> str | None:
    match = HN_ITEM_PATTERN.search(url)
    if match:
        return match.group(1)

    parsed = urlparse(url)
    if parsed.path.endswith(".json"):
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) >= 2 and parts[-2] == "item":
            item_id = parts[-1].replace(".json", "")
            if item_id.isdigit():
                return item_id
    return None


def _unix_to_iso(raw_timestamp: Any) -> str | None:
    if raw_timestamp is None:
        return None
    try:
        return datetime.fromtimestamp(float(raw_timestamp), tz=UTC).isoformat()
    except Exception:  # noqa: BLE001
        return None


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
