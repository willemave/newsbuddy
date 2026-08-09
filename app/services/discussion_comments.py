"""Shared Hacker News and Reddit discussion comment providers."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from html import unescape
from typing import Any
from urllib.parse import urlparse, urlunparse

import httpx
import praw
import prawcore
from bs4 import BeautifulSoup

from app.core.settings import get_settings
from app.services.http import (
    HttpFetcher,
    HttpService,
    NonRetryableError,
    fetch_quiet,
    get_http_service,
)
from app.utils.url_utils import is_domain_or_subdomain, normalize_http_url

HN_ALGOLIA_ITEM_URL = "https://hn.algolia.com/api/v1/items/{item_id}"
HN_FIREBASE_ITEM_URL = "https://hacker-news.firebaseio.com/v0/item/{item_id}.json"
HN_ITEM_PATTERN = re.compile(r"item\?id=(\d+)")
REDDIT_COMMENTS_PATTERN = re.compile(r"/comments/([a-z0-9]+)/?", re.IGNORECASE)
REDDIT_DEFAULT_USER_AGENT = "news_app.discussion/1.0 (by u/anonymous)"
URL_PATTERN = re.compile(r"https?://[^\s\"'<>]+")

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

_reddit_client: praw.Reddit | None = None


class DiscussionFetchError(Exception):
    """Discussion provider error with a worker retryability hint."""

    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


class TerminalDiscussionUnavailable(DiscussionFetchError):
    """Non-retryable provider state that should leave refresh rotation."""

    def __init__(self, message: str, *, status: str = "gone") -> None:
        super().__init__(message, retryable=False)
        self.status = status


@dataclass(frozen=True)
class NormalizedDiscussionComment:
    """Provider-independent comment representation."""

    comment_id: str
    parent_id: str | None
    author: str
    text: str
    compact_text: str
    depth: int
    created_at: str | None
    source_url: str

    def as_payload(self, *, source_url: str | None = None) -> dict[str, Any]:
        """Return the stable persisted comment shape."""
        return {
            "comment_id": self.comment_id,
            "parent_id": self.parent_id,
            "author": self.author,
            "text": self.text,
            "compact_text": self.compact_text,
            "depth": self.depth,
            "created_at": self.created_at,
            "source_url": source_url or self.source_url,
        }


@dataclass(frozen=True)
class NormalizedDiscussionLink:
    """Link discovered in a normalized discussion comment."""

    url: str
    comment_id: str | None
    title: str | None = None

    def as_payload(self) -> dict[str, Any]:
        """Return the stable persisted link shape."""
        payload: dict[str, Any] = {
            "url": self.url,
            "source": "comment",
            "comment_id": self.comment_id,
        }
        if self.title is not None:
            payload["title"] = self.title
        return payload


@dataclass(frozen=True)
class NormalizedDiscussionThread:
    """Provider-independent thread metadata."""

    title: str | None
    author: str | None
    score: int | None
    comment_count: int | None
    created_at: str | None
    subreddit: str | None = None


@dataclass(frozen=True)
class NormalizedDiscussion:
    """Normalized fetch result shared by both discussion persistence paths."""

    platform: str
    external_id: str
    discussion_url: str
    provider: str
    thread: NormalizedDiscussionThread
    comments: tuple[NormalizedDiscussionComment, ...]
    links: tuple[NormalizedDiscussionLink, ...]
    total_seen: int
    comment_cap: int
    cap_reached: bool

    @property
    def fetched_count(self) -> int:
        """Return the number of usable comments in this result."""
        return len(self.comments)


def fetch_hackernews_comments(
    *,
    external_id: str,
    discussion_url: str,
    comment_cap: int,
    http_service: HttpService | None = None,
) -> NormalizedDiscussion:
    """Fetch the HN story metadata and its Algolia comment tree."""
    service = http_service or get_http_service()
    firebase_item = _fetch_json(
        service,
        HN_FIREBASE_ITEM_URL.format(item_id=external_id),
    )
    if firebase_item.get("dead") is True or firebase_item.get("deleted") is True:
        raise TerminalDiscussionUnavailable("Hacker News discussion is gone")

    algolia_item = _fetch_json(
        service,
        HN_ALGOLIA_ITEM_URL.format(item_id=external_id),
    )
    return normalize_hackernews_comments(
        external_id=external_id,
        discussion_url=discussion_url,
        firebase_item=firebase_item,
        algolia_item=algolia_item,
        comment_cap=comment_cap,
    )


def normalize_hackernews_comments(
    *,
    external_id: str,
    discussion_url: str,
    firebase_item: dict[str, Any],
    algolia_item: dict[str, Any],
    comment_cap: int,
) -> NormalizedDiscussion:
    """Normalize one Firebase story plus one Algolia comment tree."""
    comments: list[NormalizedDiscussionComment] = []
    url_titles: dict[str, str] = {}
    total_seen = 0
    cap_reached = False

    def walk(nodes: Iterable[Any], depth: int, parent_id: str | None) -> None:
        nonlocal cap_reached, total_seen
        for node in nodes:
            if not isinstance(node, dict):
                continue
            if len(comments) >= comment_cap:
                cap_reached = True
                return

            total_seen += 1
            raw_html = str(node.get("text") or "")
            if raw_html:
                url_titles.update(extract_anchor_titles_from_html(raw_html))

            comment = _normalize_hackernews_comment(
                node,
                depth=depth,
                parent_id=parent_id,
            )
            next_parent_id = parent_id
            if comment is not None:
                comments.append(comment)
                next_parent_id = comment.comment_id

            children = node.get("children")
            if isinstance(children, list):
                walk(children, depth + 1, next_parent_id)
                if cap_reached:
                    return

    children = algolia_item.get("children")
    if isinstance(children, list):
        walk(children, depth=0, parent_id=None)

    return NormalizedDiscussion(
        platform="hackernews",
        external_id=external_id,
        discussion_url=discussion_url,
        provider="algolia",
        thread=NormalizedDiscussionThread(
            title=_clean_string(firebase_item.get("title"))
            or _clean_string(algolia_item.get("title")),
            author=_clean_string(firebase_item.get("by"))
            or _clean_string(algolia_item.get("author")),
            score=_coerce_non_negative_int(firebase_item.get("score"))
            or _coerce_non_negative_int(algolia_item.get("points")),
            comment_count=_coerce_non_negative_int(firebase_item.get("descendants")),
            created_at=unix_to_iso(firebase_item.get("time"))
            or _clean_string(algolia_item.get("created_at")),
        ),
        comments=tuple(comments),
        links=_normalized_links_from_comments(comments, url_titles=url_titles),
        total_seen=total_seen,
        comment_cap=comment_cap,
        cap_reached=cap_reached,
    )


def fetch_reddit_comments(
    *,
    external_id: str,
    discussion_url: str,
    comment_cap: int,
    reddit_client: Any | None = None,
) -> NormalizedDiscussion:
    """Fetch one Reddit submission through PRAW and normalize its comment tree."""
    canonical_url = normalize_reddit_discussion_url(discussion_url) or discussion_url
    client = reddit_client if reddit_client is not None else get_reddit_client()
    if client is None:
        raise DiscussionFetchError(
            "Reddit API credentials not configured",
            retryable=False,
        )

    try:
        submission = client.submission(id=external_id)
        submission.comment_sort = "top"
        _ = submission.title  # Force PRAW to surface API and authentication errors.
        submission.comments.replace_more(limit=0)
    except Exception as exc:  # noqa: BLE001
        raise DiscussionFetchError(
            f"Reddit API request failed: {exc}",
            retryable=is_retryable_reddit_error(exc),
        ) from exc

    return normalize_reddit_comments(
        external_id=external_id,
        discussion_url=canonical_url,
        submission=submission,
        comment_cap=comment_cap,
    )


def normalize_reddit_comments(
    *,
    external_id: str,
    discussion_url: str,
    submission: Any,
    comment_cap: int,
) -> NormalizedDiscussion:
    """Normalize one already-loaded PRAW submission."""
    comments: list[NormalizedDiscussionComment] = []
    url_titles: dict[str, str] = {}
    cap_reached = False
    total_seen = 0

    def walk(nodes: Iterable[Any], depth: int, parent_id: str | None) -> None:
        nonlocal cap_reached, total_seen
        for node in nodes:
            if len(comments) >= comment_cap:
                cap_reached = True
                return
            if is_reddit_more_comments(node):
                continue

            total_seen += 1
            body_html = getattr(node, "body_html", None) or ""
            if body_html:
                url_titles.update(extract_anchor_titles_from_html(str(body_html)))
            body = getattr(node, "body", None) or body_html or ""
            text = clean_html_text(str(body))
            comment_id = str(getattr(node, "id", "") or "").strip()

            if text and comment_id:
                author_obj = getattr(node, "author", None)
                comments.append(
                    NormalizedDiscussionComment(
                        comment_id=comment_id,
                        parent_id=parent_id,
                        author=getattr(author_obj, "name", None) or "unknown",
                        text=text,
                        compact_text=compact_text(text),
                        depth=depth,
                        created_at=unix_to_iso(getattr(node, "created_utc", None)),
                        source_url=discussion_url,
                    )
                )

            replies = getattr(node, "replies", None)
            if replies:
                walk(replies, depth + 1, comment_id or parent_id)
                if cap_reached:
                    return

    walk(submission.comments, depth=0, parent_id=None)
    declared_comment_count = _coerce_non_negative_int(getattr(submission, "num_comments", None))
    return NormalizedDiscussion(
        platform="reddit",
        external_id=external_id,
        discussion_url=discussion_url,
        provider="reddit",
        thread=NormalizedDiscussionThread(
            title=_clean_string(getattr(submission, "title", None)),
            author=_clean_string(getattr(getattr(submission, "author", None), "name", None)),
            score=_coerce_non_negative_int(getattr(submission, "score", None)),
            comment_count=declared_comment_count,
            created_at=unix_to_iso(getattr(submission, "created_utc", None)),
            subreddit=_clean_string(
                getattr(getattr(submission, "subreddit", None), "display_name", None)
            ),
        ),
        comments=tuple(comments),
        links=_normalized_links_from_comments(comments, url_titles=url_titles),
        total_seen=total_seen,
        comment_cap=comment_cap,
        cap_reached=cap_reached,
    )


def get_reddit_client() -> praw.Reddit | None:
    """Return the process-wide configured PRAW client, if credentials exist."""
    global _reddit_client
    if _reddit_client is not None:
        return _reddit_client

    settings = get_settings()
    if not settings.reddit_client_id or not settings.reddit_client_secret:
        return None

    reddit_kwargs: dict[str, Any] = {
        "client_id": settings.reddit_client_id,
        "client_secret": settings.reddit_client_secret,
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


def is_retryable_reddit_error(exc: Exception) -> bool:
    """Classify a PRAW error for the queue retry contract."""
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

    return "blocked by network security" not in str(exc).lower()


def is_reddit_more_comments(node: Any) -> bool:
    """Return whether a PRAW tree node is a MoreComments placeholder."""
    return node.__class__.__name__ == "MoreComments"


def normalize_reddit_discussion_url(url: str) -> str | None:
    """Return a stable HTTPS www.reddit.com form when possible."""
    normalized = normalize_http_url(url)
    if not normalized:
        return None

    parsed = urlparse(normalized)
    if (parsed.hostname or "").lower() in {"reddit.com", "old.reddit.com", "www.reddit.com"}:
        return urlunparse(parsed._replace(scheme="https", netloc="www.reddit.com"))
    return normalized


def extract_reddit_submission_id(url: str) -> str | None:
    """Extract a Reddit submission id from a comments URL."""
    match = REDDIT_COMMENTS_PATTERN.search(urlparse(url).path)
    return match.group(1).lower() if match else None


def extract_hackernews_item_id(url: str) -> str | None:
    """Extract an HN item id from its public or Firebase URL."""
    match = HN_ITEM_PATTERN.search(url)
    if match:
        return match.group(1)

    parsed = urlparse(url)
    if parsed.path.endswith(".json"):
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) >= 2 and parts[-2] == "item":
            item_id = parts[-1].removesuffix(".json")
            if item_id.isdigit():
                return item_id
    return None


def is_hackernews_discussion(platform: str, discussion_url: str | None) -> bool:
    """Return whether platform metadata or URL identifies Hacker News."""
    if platform in {"hackernews", "hn"}:
        return True
    if not discussion_url:
        return False
    host = urlparse(discussion_url).hostname
    return is_domain_or_subdomain(host, "news.ycombinator.com") and "item" in discussion_url


def is_reddit_discussion(platform: str, discussion_url: str | None) -> bool:
    """Return whether platform metadata or URL identifies Reddit."""
    if platform == "reddit":
        return True
    if not discussion_url:
        return False
    host = urlparse(discussion_url).hostname
    return is_domain_or_subdomain(host, "reddit.com") or is_domain_or_subdomain(host, "redd.it")


def extract_links_from_comments(
    comments: Iterable[dict[str, Any]],
    url_titles: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Extract the stable link payload from persisted comment dictionaries."""
    values = (
        (str(comment.get("comment_id") or "") or None, str(comment.get("text") or ""))
        for comment in comments
    )
    return [
        link.as_payload() for link in _normalized_links_from_values(values, url_titles=url_titles)
    ]


def extract_anchor_titles_from_html(html: str) -> dict[str, str]:
    """Extract meaningful normalized link titles from HTML anchors."""
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
        if text.lower() in _TRIVIAL_ANCHOR_TEXTS or is_url_like_text(text, href):
            continue
        normalized = normalize_http_url(href)
        if normalized and normalized not in titles:
            titles[normalized] = text
    return titles


def is_url_like_text(text: str, url: str) -> bool:
    """Return whether anchor text is effectively its target URL."""
    stripped = text.strip().rstrip("/")
    url_stripped = url.strip().rstrip("/")
    if stripped == url_stripped:
        return True
    for prefix in ("https://", "http://"):
        if not url_stripped.startswith(prefix):
            continue
        without_scheme = url_stripped[len(prefix) :]
        if stripped == without_scheme:
            return True
        if without_scheme.startswith("www.") and stripped == without_scheme[4:]:
            return True
    return False


def compact_text(text: str, max_chars: int = 400) -> str:
    """Collapse whitespace and cap a comment preview."""
    cleaned = " ".join(text.split())
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[: max_chars - 3].rstrip() + "..."


def clean_html_text(value: str) -> str:
    """Convert provider comment HTML into normalized plain text."""
    if not value:
        return ""
    soup = BeautifulSoup(unescape(value), "html.parser")
    return " ".join(soup.get_text(" ", strip=True).split())


def unix_to_iso(raw_timestamp: Any) -> str | None:
    """Convert a provider Unix timestamp to an ISO-8601 UTC string."""
    if raw_timestamp is None:
        return None
    try:
        return datetime.fromtimestamp(float(raw_timestamp), tz=UTC).isoformat()
    except (TypeError, ValueError, OSError):
        return None


def _fetch_json(http_service: HttpFetcher, url: str) -> dict[str, Any]:
    try:
        response = fetch_quiet(http_service, url)
    except Exception as exc:  # noqa: BLE001
        status_code = _http_status_code(exc)
        message = (
            f"HTTP {status_code} while fetching discussion" if status_code is not None else str(exc)
        )
        raise DiscussionFetchError(
            message,
            retryable=_is_retryable_http_error(exc, status_code=status_code),
        ) from exc

    try:
        payload = response.json()
    except (TypeError, ValueError) as exc:
        raise DiscussionFetchError(
            "Discussion endpoint returned invalid JSON",
            retryable=True,
        ) from exc
    if not isinstance(payload, dict):
        raise DiscussionFetchError("Discussion endpoint returned a non-object payload")
    return payload


def _http_status_code(exc: BaseException) -> int | None:
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, httpx.HTTPStatusError):
            return current.response.status_code
        response = getattr(current, "response", None)
        status_code = getattr(response, "status_code", None)
        if isinstance(status_code, int):
            return status_code
        current = current.__cause__ or current.__context__
    return None


def _is_retryable_http_error(exc: Exception, *, status_code: int | None) -> bool:
    if status_code is not None:
        return status_code == 429 or status_code >= 500
    if isinstance(exc, DiscussionFetchError):
        return exc.retryable
    return not isinstance(exc, NonRetryableError)


def _normalize_hackernews_comment(
    node: dict[str, Any],
    *,
    depth: int,
    parent_id: str | None,
) -> NormalizedDiscussionComment | None:
    comment_id = _clean_string(str(node.get("id"))) if node.get("id") is not None else None
    text = clean_html_text(str(node.get("text") or ""))
    if not comment_id or not text:
        return None
    return NormalizedDiscussionComment(
        comment_id=comment_id,
        parent_id=parent_id,
        author=_clean_string(node.get("author")) or "unknown",
        text=text,
        compact_text=compact_text(text),
        depth=depth,
        created_at=_clean_string(node.get("created_at")) or unix_to_iso(node.get("created_at_i")),
        source_url=f"https://news.ycombinator.com/item?id={comment_id}",
    )


def _normalized_links_from_comments(
    comments: Iterable[NormalizedDiscussionComment],
    *,
    url_titles: dict[str, str] | None = None,
) -> tuple[NormalizedDiscussionLink, ...]:
    return _normalized_links_from_values(
        ((comment.comment_id, comment.text) for comment in comments),
        url_titles=url_titles,
    )


def _normalized_links_from_values(
    values: Iterable[tuple[str | None, str]],
    *,
    url_titles: dict[str, str] | None,
) -> tuple[NormalizedDiscussionLink, ...]:
    links: list[NormalizedDiscussionLink] = []
    seen: set[str] = set()
    for comment_id, text in values:
        for raw_url in URL_PATTERN.findall(text):
            url = normalize_http_url(raw_url)
            if not url or url in seen:
                continue
            seen.add(url)
            links.append(
                NormalizedDiscussionLink(
                    url=url,
                    comment_id=comment_id,
                    title=url_titles.get(url) if url_titles else None,
                )
            )
    return tuple(links)


def _clean_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.split()).strip()
    return cleaned or None


def _coerce_non_negative_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float) and value.is_integer():
        integer = int(value)
        return integer if integer >= 0 else None
    if isinstance(value, str):
        cleaned = value.strip().replace(",", "")
        if cleaned.isdigit():
            return int(cleaned)
    return None
