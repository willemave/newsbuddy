"""Discussion fixture payloads and persistence helpers for local test data."""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models.db import NewsItem, NewsItemDiscussion

UTC = getattr(datetime, "UTC", timezone.utc)  # noqa: UP017

DISCUSSION_COMMENTS = [
    {
        "author": "tptacek",
        "text": (
            "This is more nuanced than the headline suggests. "
            "The real impact depends on adoption rates across the industry."
        ),
    },
    {
        "author": "patio11",
        "text": (
            "Having worked in this space, the regulatory angle is what most people miss entirely."
        ),
    },
    {
        "author": "dang",
        "text": (
            "We changed the title from the clickbait original. Please keep discussion substantive."
        ),
    },
    {
        "author": "rauchg",
        "text": ("We've been building toward this at Vercel. The DX implications are massive."),
    },
    {
        "author": "karpathy",
        "text": (
            "The architecture is interesting but the real bottleneck "
            "is data quality, not model size."
        ),
    },
    {
        "author": "swyx",
        "text": (
            "This confirms the trend I wrote about last month. The ecosystem is consolidating fast."
        ),
    },
    {
        "author": "gergely",
        "text": (
            "From a pragmatic engineering perspective, "
            "the migration path is what matters most here."
        ),
    },
    {
        "author": "id_aa_carmack",
        "text": (
            "The latency numbers are impressive but I'd want "
            "to see sustained throughput benchmarks."
        ),
    },
    {
        "author": "simonw",
        "text": (
            "I built a quick prototype using this and the API ergonomics are surprisingly good."
        ),
    },
    {
        "author": "antirez",
        "text": ("Simple systems that work beat complex systems that don't. This gets that right."),
    },
]

DISCUSSION_LINKS = [
    {
        "url": "https://example.com/operational-maturity",
        "title": "Operational maturity checklist",
    },
    {
        "url": "https://example.com/reliability-postmortem",
        "title": "Reliability postmortem examples",
    },
    {
        "url": "https://example.com/platform-governance",
        "title": "Platform governance patterns",
    },
]


def generate_comments_discussion_payload(
    *,
    discussion_url: str | None,
    comment_count: int = 4,
) -> dict[str, Any]:
    """Generate a full comments-mode discussion payload."""

    comments = generate_discussion_comments(source_url=discussion_url, count=comment_count)
    links = generate_discussion_links(comments)
    return {
        "mode": "comments",
        "source_url": discussion_url,
        "comments": comments,
        "compact_comments": [
            f"{comment.get('author')}: {comment.get('compact_text') or comment.get('text')}"
            for comment in comments
        ],
        "discussion_groups": [],
        "links": links,
        "stats": {
            "declared_comment_count": max(comment_count, random.randint(8, 80)),
            "fetched_comment_count": len(comments),
            "link_count": len(links),
        },
    }


def generate_discussion_list_payload(*, discussion_url: str | None) -> dict[str, Any]:
    """Generate a Techmeme-style discussion-list payload."""

    comments = [
        {
            "comment_id": f"social-{random.randint(1000, 9999)}",
            "author": "news.ycombinator.com",
            "text": "Hacker News discussion",
            "compact_text": "Hacker News discussion",
            "depth": 0,
            "source_url": f"https://news.ycombinator.com/item?id={random.randint(100000, 999999)}",
        },
        {
            "comment_id": f"social-{random.randint(1000, 9999)}",
            "author": "reddit.com",
            "text": "Reddit thread",
            "compact_text": "Reddit thread",
            "depth": 0,
            "source_url": (
                f"https://www.reddit.com/r/technology/comments/{random.randint(1000, 9999)}/thread/"
            ),
        },
    ]
    discussion_groups: list[dict[str, Any]] = [
        {
            "label": "Forums",
            "items": [
                {"title": "Hacker News", "url": comments[0]["source_url"]},
                {"title": "Reddit", "url": comments[1]["source_url"]},
            ],
        },
        {
            "label": "Social",
            "items": [
                {"title": "X discussion", "url": "https://x.com/search?q=newsly-fixture"},
            ],
        },
    ]
    links: list[dict[str, Any]] = []
    for group in discussion_groups:
        for item in group["items"]:
            links.append(
                {
                    "url": item["url"],
                    "title": item["title"],
                    "source": "discussion_group",
                    "group_label": group["label"],
                }
            )
    return {
        "mode": "discussion_list",
        "source_url": discussion_url,
        "comments": comments,
        "compact_comments": [
            f"{comment.get('author')}: {comment.get('compact_text') or comment.get('text')}"
            for comment in comments
        ],
        "discussion_groups": discussion_groups,
        "links": links,
        "stats": {
            "item_count": len(links),
            "group_count": len(discussion_groups),
            "fetched_comment_count": len(comments),
        },
    }


def discussion_preview_fields(payload: dict[str, Any]) -> tuple[dict[str, str] | None, int | None]:
    """Return top-comment and count fields denormalized into metadata."""

    mode = payload.get("mode")
    raw_stats = payload.get("stats")
    stats: dict[str, Any] = raw_stats if isinstance(raw_stats, dict) else {}
    top_comment = None
    if mode == "comments":
        for comment in payload.get("comments", []):
            if not isinstance(comment, dict):
                continue
            text = str(comment.get("compact_text") or comment.get("text") or "").strip()
            if text:
                top_comment = {
                    "author": str(comment.get("author") or "unknown"),
                    "text": text,
                }
                break
        return top_comment, int(stats.get("declared_comment_count") or 0) or None
    if mode == "discussion_list":
        return None, int(stats.get("item_count") or 0) or None
    return None, None


def insert_news_item_discussions(session: Session, *, content_ids: list[int]) -> None:
    """Create current-schema news-item discussion rows for generated news fixtures."""

    if not content_ids:
        return

    news_items = session.query(NewsItem).filter(NewsItem.legacy_content_id.in_(content_ids)).all()
    now = _utc_now_naive()
    for item in news_items:
        raw_metadata: dict[str, Any] = (
            item.raw_metadata if isinstance(item.raw_metadata, dict) else {}
        )
        payload = raw_metadata.get("discussion_payload")
        if not isinstance(payload, dict):
            discussion_url = item.discussion_url or item.canonical_item_url
            payload = (
                generate_discussion_list_payload(discussion_url=discussion_url)
                if item.platform == "techmeme"
                else generate_comments_discussion_payload(discussion_url=discussion_url)
            )
            top_comment, comment_count = discussion_preview_fields(payload)
            raw_metadata = dict(raw_metadata)
            raw_metadata["discussion_status"] = "completed"
            raw_metadata["discussion_fetched_at"] = datetime.now(UTC).isoformat()
            raw_metadata["discussion_payload"] = payload
            if top_comment is not None:
                raw_metadata["top_comment"] = top_comment
            if comment_count is not None:
                raw_metadata["comment_count"] = comment_count
            item.raw_metadata = raw_metadata

        discussion_url = item.discussion_url or raw_metadata.get("discussion_url")
        summary = _discussion_summary_from_payload(
            discussion_url=discussion_url,
            payload=payload,
        )
        if summary is None:
            continue

        raw_stats = payload.get("stats")
        stats: dict[str, Any] = raw_stats if isinstance(raw_stats, dict) else {}
        raw_comments = payload.get("comments")
        comments: list[Any] = raw_comments if isinstance(raw_comments, list) else []
        comment_count = (
            raw_metadata.get("comment_count")
            or stats.get("declared_comment_count")
            or stats.get("item_count")
            or len(comments)
        )
        row = (
            session.query(NewsItemDiscussion)
            .filter(NewsItemDiscussion.news_item_id == item.id)
            .first()
        )
        if row is None:
            row = NewsItemDiscussion(news_item_id=item.id)
            session.add(row)

        row.platform = item.platform or "unknown"
        row.external_id = item.source_external_id
        row.discussion_url = discussion_url
        row.title = item.summary_title or item.article_title or item.source_label
        row.author = None
        row.score = None
        row.comment_count = int(comment_count) if comment_count is not None else len(comments)
        row.raw_comments_ref = {
            "storage": "fixture",
            "comment_ids": [
                str(comment.get("comment_id"))
                for comment in comments
                if isinstance(comment, dict) and comment.get("comment_id")
            ],
        }
        row.raw_comments_sha256 = f"fixture-{item.legacy_content_id or item.id}"
        row.fetched_comment_count = len(comments)
        row.last_count_checked_at = now
        row.last_comments_fetched_at = now
        row.next_refresh_after = now + timedelta(hours=random.randint(6, 24))
        row.summary = summary
        row.summary_status = "completed"
        row.summary_version = 1
        row.summary_model = "fixture"
        row.summary_generated_at = now
        row.last_refresh_status = "completed"
        row.last_refresh_error = None


def generate_discussion_comments(
    *,
    source_url: str | None,
    count: int = 4,
) -> list[dict[str, Any]]:
    """Generate normalized comment payloads for discussion endpoints."""

    selected: list[dict[str, str]] = []
    pool = DISCUSSION_COMMENTS.copy()
    while len(selected) < count:
        if not pool:
            pool = DISCUSSION_COMMENTS.copy()
        take = min(count - len(selected), len(pool))
        chunk = random.sample(pool, take)
        selected.extend(chunk)
        for item in chunk:
            pool.remove(item)

    comments: list[dict[str, Any]] = []
    root_id = f"c-{random.randint(1000, 9999)}"
    for index, comment in enumerate(selected):
        comment_id = root_id if index == 0 else f"{root_id}-{index}"
        text = comment["text"]
        comments.append(
            {
                "comment_id": comment_id,
                "parent_id": None if index == 0 else root_id,
                "author": comment["author"],
                "text": text,
                "compact_text": text[:240],
                "depth": 0 if index == 0 else random.choice([1, 1, 2]),
                "created_at": _random_datetime(5).isoformat(),
                "source_url": source_url,
            }
        )
    return comments


def generate_discussion_links(comments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Generate links surfaced from discussion comments."""

    links: list[dict[str, Any]] = []
    for raw_link, comment in zip(DISCUSSION_LINKS, comments, strict=False):
        links.append(
            {
                "url": raw_link["url"],
                "title": raw_link["title"],
                "source": "comment",
                "comment_id": str(comment.get("comment_id")),
            }
        )
    return links


def generate_discussion_summary(
    *,
    discussion_url: str | None,
    comments: list[dict[str, Any]],
    links: list[dict[str, Any]],
) -> dict[str, Any]:
    """Generate the structured discussion summary persisted for news items."""

    representative_comments = [
        {
            "comment_id": str(comment.get("comment_id")),
            "author": str(comment.get("author")),
            "text": str(comment.get("compact_text") or comment.get("text")),
            "reason": "Representative of the main discussion thread.",
        }
        for comment in comments[:3]
    ]
    notable_links = [
        {
            "url": str(link["url"]),
            "title": str(link.get("title") or "Discussion link"),
            "reason": "Commenters used this link to add context.",
            "source_comment_id": str(link.get("comment_id")),
        }
        for link in links[:3]
    ]
    return {
        "overview": (
            "Commenters focused on whether the announcement is operationally meaningful, "
            "how hard it will be to deploy in real workflows, and what evidence would make "
            "the claim more credible."
        ),
        "topics": [
            {
                "title": "Deployment reality",
                "summary": (
                    "Several comments separate the headline claim from the practical work "
                    "required to make the system reliable in production."
                ),
                "stance": "Mostly pragmatic and skeptical.",
            },
            {
                "title": "Cost and governance",
                "summary": (
                    "The thread ties adoption to cost visibility, security review, and clear "
                    "ownership rather than raw model capability alone."
                ),
                "stance": "Commenters agree these constraints matter.",
            },
        ],
        "notable_links": notable_links,
        "representative_comments": representative_comments,
        "external_discussion_url": discussion_url,
        "generated_at": datetime.now(UTC).isoformat(),
    }


def _discussion_summary_from_payload(
    *,
    discussion_url: str | None,
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    """Build a news-item discussion summary from a fixture payload."""

    raw_comments = payload.get("comments")
    comments: list[Any] = raw_comments if isinstance(raw_comments, list) else []
    raw_links = payload.get("links")
    links: list[Any] = raw_links if isinstance(raw_links, list) else []
    if not comments and not links:
        return None
    return generate_discussion_summary(
        discussion_url=discussion_url,
        comments=[comment for comment in comments if isinstance(comment, dict)],
        links=[link for link in links if isinstance(link, dict)],
    )


def _random_datetime(days_back: int = 30) -> datetime:
    delta = timedelta(days=random.randint(0, days_back))
    return _utc_now_naive() - delta


def _utc_now_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)
