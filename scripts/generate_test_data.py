#!/usr/bin/env python3
# ruff: noqa: E402
"""
Generate test data for the news_app database.

This script creates realistic test data that exercises all fields in the metadata models
(ArticleMetadata, PodcastMetadata, NewsMetadata) with properly structured summaries.

Features:
- Generates articles, podcasts, and news items with complete metadata
- Creates structured summaries with bullet points, quotes, topics, questions, and counter-arguments
- Mimics the structure from tests/fixtures/content_samples.json
- Supports flexible configuration via command-line arguments
- Includes items in various states (new, processing, completed) by default

Usage:
    # Generate default amounts (10 articles, 5 podcasts, 15 news items)
    python scripts/generate_test_data.py

    # Custom amounts
    python scripts/generate_test_data.py --articles 20 --podcasts 10 --news 30

    # Only completed items (no pending/processing states)
    python scripts/generate_test_data.py --no-pending

    # Dry run (generate but don't insert)
    python scripts/generate_test_data.py --dry-run

Examples:
    # Large dataset for performance testing
    python scripts/generate_test_data.py --articles 100 --podcasts 50 --news 200

    # Minimal dataset for quick testing
    python scripts/generate_test_data.py --articles 2 --podcasts 1 --news 3
"""

from __future__ import annotations

import os
import random
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

# Add parent directory so we can import from app
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENV_PYTHON = os.path.join(PROJECT_ROOT, ".venv", "bin", "python")

if os.path.exists(VENV_PYTHON):
    current_executable = os.path.realpath(sys.executable)
    target_executable = VENV_PYTHON
    target_realpath = os.path.realpath(target_executable)
    if current_executable != target_realpath:
        os.execv(target_executable, [target_executable, __file__, *sys.argv[1:]])

sys.path.insert(0, PROJECT_ROOT)

from sqlalchemy.orm import Session

from app.constants import (
    CONTENT_STATUS_INBOX,
    SUMMARY_KIND_LONG_BULLETS,
    SUMMARY_KIND_LONG_EDITORIAL_NARRATIVE,
    SUMMARY_KIND_LONG_INTERLEAVED,
    SUMMARY_KIND_LONG_STRUCTURED,
    SUMMARY_KIND_LONGFORM_ARTIFACT,
    SUMMARY_KIND_SHORT_NEWS,
    SUMMARY_VERSION_V1,
    SUMMARY_VERSION_V2,
)
from app.core.db import get_db, init_db
from app.models.contracts import ContentStatus, ContentType, NewsItemStatus, NewsItemVisibilityScope
from app.models.db import (
    Content,
    ContentDiscussion,
    ContentReadStatus,
    ContentStatusEntry,
    NewsItem,
    NewsItemDiscussion,
)
from app.models.db.users import User
from app.models.metadata.articles import ArticleMetadata
from app.models.metadata.longform_artifacts import (
    ARTIFACT_ASK_BY_TYPE,
    ArtifactType,
    LongformArtifactEnvelope,
)
from app.models.metadata.podcasts import PodcastMetadata
from app.models.metadata.summaries import (
    BulletedSummary,
    BulletSummaryPoint,
    ContentQuote,
    EditorialKeyPoint,
    EditorialNarrativeSummary,
    EditorialQuote,
    InterleavedInsight,
    InterleavedSummary,
    InterleavedSummaryV2,
    InterleavedTopic,
    NewsSummary,
    StructuredSummary,
    SummaryBulletPoint,
    SummaryPayload,
    SummaryTextBullet,
)
from app.services.news_ingestion import backfill_news_items_from_contents

# Sample data pools
ARTICLE_SOURCES = [
    "Import AI",
    "Stratechery",
    "hackernews",
    "Benedict Evans",
    "Lex Fridman Blog",
]

PODCAST_SOURCES = [
    "Lenny's Podcast",
    "BG2 Pod",
    "Acquired",
    "All-In Podcast",
    "The Knowledge Project",
]

NEWS_PLATFORMS = ["hackernews", "techmeme", "reddit"]

TOPICS = [
    ["AI", "Machine Learning", "Technology"],
    ["Startups", "Venture Capital", "Business"],
    ["Software Engineering", "DevOps", "Cloud"],
    ["Cybersecurity", "Privacy", "Ethics"],
    ["Product Management", "Design", "UX"],
    ["Leadership", "Management", "Career"],
    ["Economics", "Finance", "Markets"],
]

ARTICLE_TITLES = [
    "Understanding Modern Machine Learning Architectures",
    "The Future of Distributed Systems at Scale",
    "Building Resilient Microservices with Kubernetes",
    "How AI is Transforming Software Development",
    "The Economics of Open Source Software",
    "Scaling Engineering Teams: Lessons Learned",
    "Deep Dive into Rust's Memory Safety Model",
    "The Evolution of NoSQL Databases",
]

PODCAST_TITLES = [
    "Building the Next Generation of AI Products",
    "From Startup to IPO: The Journey",
    "Mastering Product-Market Fit",
    "The Art of Engineering Leadership",
    "Investing in Early-Stage Startups",
    "Building Developer Tools That Scale",
]

NEWS_HEADLINES = [
    "OpenAI Announces GPT-5 with Enhanced Reasoning Capabilities",
    "Major Tech Company Acquires AI Startup for $2B",
    "New Breakthrough in Quantum Computing Stability",
    "Security Flaw Discovered in Popular Open Source Library",
    "Federal Reserve Announces Interest Rate Decision",
    "Apple Unveils Next-Generation M5 Chip Architecture",
    "EU Passes Comprehensive AI Regulation Framework",
    "Rust Overtakes Go in Cloud Infrastructure Adoption",
    "Google DeepMind Achieves Breakthrough in Protein Folding",
    "GitHub Copilot Now Generates Full Pull Requests Autonomously",
    "Tesla Robotaxi Fleet Launches in Three US Cities",
    "Cloudflare Reports Record DDoS Attack Mitigated at 5 Tbps",
    "YC-Backed Startup Raises $500M for Open Source LLM Training",
    "Signal Protocol Adopted as Industry Standard for E2E Encryption",
    "NVIDIA H200 GPU Shortage Drives Cloud Compute Prices Up 40%",
]

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

SUMMARY_FORMATS = [
    "longform_artifact",
    "editorial_narrative",
    "bulleted",
    "interleaved_v2",
    "structured",
    "interleaved_v1",
]
UTC = getattr(datetime, "UTC", timezone.utc)  # noqa: UP017


def utc_now_naive() -> datetime:
    """Return the current UTC timestamp without tzinfo for DB writes."""
    return datetime.now(UTC).replace(tzinfo=None)


def random_datetime(days_back: int = 30) -> datetime:
    """Generate a random datetime within the last N days."""
    delta = timedelta(days=random.randint(0, days_back))
    return utc_now_naive() - delta


def random_datetime_for_day_offset(day_offset: int) -> datetime:
    """Generate a random timestamp within one UTC day offset from today."""
    base_day = (utc_now_naive() - timedelta(days=max(day_offset, 0))).replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )
    return base_day + timedelta(
        hours=random.randint(0, 23),
        minutes=random.randint(0, 59),
        seconds=random.randint(0, 59),
    )


def generate_bullet_points(count: int = 4) -> list[SummaryBulletPoint]:
    """Generate sample bullet points with categories."""
    categories = ["key_finding", "methodology", "conclusion", "insight", "context", "review"]
    points = [
        "The research introduces a novel approach to solving the problem.",
        "Experimental results demonstrate significant improvements over baseline methods.",
        "The methodology combines existing frameworks with new optimization strategies.",
        "Key findings suggest a paradigm shift in how we approach this domain.",
        "Implementation details reveal important trade-offs between performance and complexity.",
        "The author provides comprehensive analysis backed by empirical evidence.",
    ]

    return [
        SummaryBulletPoint(text=random.choice(points), category=random.choice(categories))
        for _ in range(count)
    ]


def generate_quotes(count: int = 2) -> list[ContentQuote]:
    """Generate sample quotes with context and attribution."""
    quotes = [
        (
            "The future belongs to those who understand the implications of AI.",
            "Author's perspective",
            "Author",
        ),
        (
            "We're not just building technology; we're shaping how humans interact with machines.",
            "CEO Interview",
            "CEO",
        ),
        (
            "The key to success in this field is relentless iteration and learning from failure.",
            "Industry Expert",
            "Industry Expert",
        ),
    ]

    return [
        ContentQuote(text=text, context=ctx, attribution=attribution)
        for text, ctx, attribution in random.sample(quotes, min(count, len(quotes)))
    ]


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
                "created_at": random_datetime(5).isoformat(),
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


def generate_bulleted_points(count: int) -> list[BulletSummaryPoint]:
    """Generate bullet points with details and supporting quotes."""
    bullet_texts = [
        "Organizations are standardizing tools to reduce operational overhead.",
        "The approach delivers measurable performance gains across benchmarks.",
        "Adoption depends on integration with existing workflows and governance.",
        "Cost visibility is reshaping procurement decisions for AI tooling.",
        "Teams report faster iteration cycles once the workflow is in place.",
        "Reliability improves when monitoring and feedback loops are formalized.",
        "Security reviews now gate most production deployments of new models.",
        "The market is consolidating around a few dominant platform providers.",
        "Talent needs are shifting toward systems and infrastructure expertise.",
        "Long-term ROI is tied to data quality and operational maturity.",
        "Early pilots show uneven outcomes depending on domain complexity.",
        "Product roadmaps increasingly prioritize automation and orchestration.",
    ]

    detail_templates = [
        (
            "Evidence points to {detail_focus} as a deciding factor in adoption. "
            "Teams that address this early report smoother rollouts and clearer outcomes."
        ),
        (
            "The data suggests {detail_focus} is a leading indicator of success. "
            "Executives are monitoring this closely to justify continued investment."
        ),
        (
            "Practitioners highlight {detail_focus} when describing the biggest shifts. "
            "These changes are already influencing roadmap and staffing decisions."
        ),
    ]

    selected: list[str] = []
    pool = bullet_texts.copy()
    while len(selected) < count:
        if not pool:
            pool = bullet_texts.copy()
        take = min(count - len(selected), len(pool))
        chunk = random.sample(pool, take)
        selected.extend(chunk)
        for item in chunk:
            pool.remove(item)
    points: list[BulletSummaryPoint] = []
    for text in selected:
        detail_focus = text.lower().rstrip(".")
        detail = random.choice(detail_templates).format(detail_focus=detail_focus)
        points.append(
            BulletSummaryPoint(
                text=text,
                detail=detail,
                quotes=generate_quotes(random.randint(1, 3)),
            )
        )
    return points


def generate_questions(count: int = 2) -> list[str]:
    """Generate thought-provoking questions."""
    questions = [
        "How might this technology impact existing industry practices?",
        "What are the potential ethical implications of widespread adoption?",
        "Could this approach be applied to other domains effectively?",
        "What barriers exist to implementing this at scale?",
    ]
    return random.sample(questions, min(count, len(questions)))


def generate_counter_arguments(count: int = 2) -> list[str]:
    """Generate counter-arguments or alternative perspectives."""
    arguments = [
        "Critics argue that improvements may not generalize beyond specific benchmarks.",
        "Alternative approaches might offer better explainability at the cost of performance.",
        "The methodology's reliance on proprietary data limits reproducibility.",
        "Some researchers question whether the results justify the computational costs.",
    ]
    return random.sample(arguments, min(count, len(arguments)))


def generate_interleaved_insights(count: int = 5) -> list[InterleavedInsight]:
    """Generate interleaved insights with topics, insights, and supporting quotes."""
    insight_data = [
        {
            "topic": "Performance Improvements",
            "insight": (
                "The new approach demonstrates a 40% improvement in processing speed "
                "while maintaining accuracy levels comparable to previous methods. "
                "This represents a significant breakthrough for real-world applications."
            ),
            "quote": (
                "We were genuinely surprised by the magnitude of these improvements. "
                "The results exceeded our initial expectations and suggest there's still "
                "significant room for optimization in this space."
            ),
            "attribution": "Lead Researcher",
        },
        {
            "topic": "Adoption Challenges",
            "insight": (
                "Organizations face significant hurdles when implementing these technologies, "
                "primarily around integration with existing systems and team training. "
                "Early adopters report a 6-month average time to full productivity."
            ),
            "quote": (
                "The technology works as advertised, but getting our entire team up to speed "
                "took longer than expected. The learning curve is real, even for engineers."
            ),
            "attribution": "Engineering Director at Fortune 500",
        },
        {
            "topic": "Market Implications",
            "insight": (
                "Industry analysts predict this development could reshape competitive dynamics "
                "in the sector over the next 2-3 years. Companies slow to adopt risk losing ground."
            ),
            "quote": (
                "This isn't just an incremental improvement—it's a paradigm shift that will "
                "force every major player to reevaluate their technology roadmap."
            ),
            "attribution": "Industry Analyst",
        },
        {
            "topic": "Technical Architecture",
            "insight": (
                "The underlying architecture leverages distributed computing and edge processing "
                "to achieve its performance gains. This hybrid approach minimizes latency "
                "while maximizing throughput."
            ),
            "quote": (
                "We spent two years refining the architecture before it achieved our goals. "
                "The key insight was moving critical processing closer to the edge."
            ),
            "attribution": "Chief Architect",
        },
        {
            "topic": "Future Directions",
            "insight": (
                "The research team is already working on next-generation improvements that could "
                "further enhance capabilities by another 30%. Preliminary results are promising."
            ),
            "quote": (
                "What we've released today is just the beginning. Our roadmap includes features "
                "that will make current limitations seem quaint by comparison."
            ),
            "attribution": "Product Lead",
        },
        {
            "topic": "Cost Considerations",
            "insight": (
                "While initial implementation costs can be substantial, organizations report "
                "achieving ROI within 12-18 months. The long-term cost savings are significant."
            ),
            "quote": (
                "The upfront investment was significant, but we've already seen a 25% reduction "
                "in operational costs that more than justifies the expense."
            ),
            "attribution": "CFO of Tech Startup",
        },
    ]

    selected = random.sample(insight_data, min(count, len(insight_data)))
    return [
        InterleavedInsight(
            topic=item["topic"],
            insight=item["insight"],
            supporting_quote=item["quote"] if random.random() > 0.2 else None,
            quote_attribution=item["attribution"] if random.random() > 0.2 else None,
        )
        for item in selected
    ]


def generate_interleaved_key_points(count: int = 4) -> list[SummaryTextBullet]:
    """Generate key points for interleaved v2 summaries."""
    candidates = [
        "Benchmark accuracy improves by roughly 35-40% across tasks.",
        "Training costs fall as teams optimize the new pipeline.",
        "Deployment timelines shrink from months to weeks.",
        "Adoption accelerates in teams with strong data tooling.",
        "Operational risk drops when monitoring is integrated early.",
    ]
    return [SummaryTextBullet(text=text) for text in random.sample(candidates, count)]


def generate_interleaved_topics(count: int = 2) -> list[InterleavedTopic]:
    """Generate topics for interleaved v2 summaries."""
    topic_names = [
        "Performance Gains",
        "Operational Impact",
        "Adoption Patterns",
        "Architecture",
        "Cost Considerations",
        "Market Implications",
    ]
    selected = random.sample(topic_names, count)
    topics: list[InterleavedTopic] = []
    for name in selected:
        bullets = [
            SummaryTextBullet(text="Teams see consistent improvements across workflows."),
            SummaryTextBullet(text="Investments in tooling reduce long-term overhead."),
        ]
        if random.random() > 0.5:
            bullets.append(SummaryTextBullet(text="Early wins unlock broader buy-in."))
        topics.append(InterleavedTopic(topic=name, bullets=bullets[:3]))
    return topics


def generate_editorial_quotes(count: int = 3) -> list[EditorialQuote]:
    """Generate editorial quotes with attribution."""
    quotes = generate_quotes(count)
    return [
        EditorialQuote(
            text=item.text,
            attribution=item.attribution,
        )
        for item in quotes
    ]


def generate_editorial_key_points(count: int = 5) -> list[EditorialKeyPoint]:
    """Generate editorial key points for long-form summaries."""
    candidates = [
        "Reliability work is becoming the gating factor for production rollouts.",
        "Teams that formalize feedback loops report faster iteration and cleaner outcomes.",
        "Security and governance requirements are shaping adoption earlier in the cycle.",
        "Cost visibility is pushing buyers toward fewer, more operationally mature vendors.",
        "Integration quality matters more than raw model novelty in enterprise settings.",
        "Workflow discipline is emerging as a stronger moat than access to the latest model.",
        "Operational ownership is moving from experimentation teams into core platform groups.",
        "Evaluation standards are turning ad hoc pilots into repeatable delivery processes.",
    ]
    selected = random.sample(candidates, count)
    return [EditorialKeyPoint(point=point) for point in selected]


def generate_editorial_narrative(title: str, topic: str) -> EditorialNarrativeSummary:
    """Generate an editorial narrative summary for long-form content."""
    paragraphs = [
        (
            f"{title} argues that {topic.lower()} is no longer a side topic for curious teams but "
            "an operating constraint for anyone shipping production systems. The core claim is "
            "that reliability, observability, and governance now shape whether ambitious projects "
            "survive beyond the pilot phase."
        ),
        (
            "Rather than celebrating raw capability in isolation, the piece emphasizes how teams "
            "turn progress into dependable workflow gains. It ties concrete implementation choices "
            "to organizational behavior, showing that tighter feedback loops, clearer ownership, "
            "and stronger deployment discipline are what make the technology economically useful."
        ),
        (
            "The most persuasive evidence comes from practitioners describing smoother rollouts "
            "once monitoring, security review, and cost visibility are designed in upfront. That "
            "shifts the narrative from breakthrough demos to operating maturity, and suggests the "
            "next winners will be the teams that can absorb complexity without passing it to users."
        ),
    ]
    return EditorialNarrativeSummary(
        title=title,
        editorial_narrative="\n\n".join(paragraphs),
        quotes=generate_editorial_quotes(random.randint(2, 3)),
        key_points=generate_editorial_key_points(random.randint(4, 6)),
        source_details=None,
        classification="to_read" if random.random() > 0.15 else "skip",
        summarization_date=random_datetime(7),
    )


def generate_artifact_quotes(source: str) -> list[dict[str, str]]:
    """Generate source-style quotes for typed long-form artifacts."""
    return [
        {
            "text": (
                "The teams getting durable value are the ones turning experiments into "
                "repeatable operating practices."
            ),
            "attribution": source,
        },
        {
            "text": (
                "Capability matters, but the deployment system around it determines whether "
                "people can trust the result."
            ),
            "attribution": source,
        },
    ]


def generate_artifact_key_points(topic: str, count: int = 5) -> list[dict[str, str]]:
    """Generate headed key points for typed long-form artifacts."""
    candidates = [
        (
            "Operating Constraint",
            (
                f"{topic} is framed as an operating constraint, not a side experiment, "
                "because teams now need reliable ownership, monitoring, and review loops."
            ),
        ),
        (
            "Adoption Test",
            (
                "The useful test is whether the workflow survives repeated use by real teams, "
                "not whether a demo works once under ideal conditions."
            ),
        ),
        (
            "Governance Pressure",
            (
                "Security, procurement, and finance teams are moving earlier into the process "
                "because model behavior is becoming a production dependency."
            ),
        ),
        (
            "Execution Moat",
            (
                "The source suggests that implementation discipline can become a stronger moat "
                "than access to the newest technical primitive."
            ),
        ),
        (
            "Cost Visibility",
            (
                "Clear usage and cost reporting changes buyer behavior by exposing which systems "
                "create durable throughput and which only create activity."
            ),
        ),
        (
            "User Trust",
            (
                "Trust depends on understandable failures, clear escalation paths, and product "
                "surfaces that make system limits visible without forcing users to debug them."
            ),
        ),
    ]
    return [{"heading": heading, "content": content} for heading, content in candidates[:count]]


def generate_longform_artifact(
    title: str,
    topic: str,
    *,
    url: str,
    source: str,
    platform: str,
) -> LongformArtifactEnvelope:
    """Generate a typed long-form artifact summary matching the current app renderer."""
    artifact_type: ArtifactType = random.choice(
        ["argument", "mental_model", "playbook", "briefing", "findings"]
    )
    shared_extras = {
        "evidence": [
            (
                "The source points to implementation details, organizational behavior, "
                "and repeated workflow use."
            ),
            "The examples emphasize reliability, ownership, governance, and cost visibility.",
        ],
        "mental_model": [
            (
                "Judge technology by the operating system it creates around teams, not only "
                "by its frontier capability."
            )
        ],
        "counter_arguments": [
            (
                "Early capability improvements can still matter even when operational maturity "
                "is incomplete."
            )
        ],
        "supporting_arguments": [
            (
                "Teams that define ownership and measurement earlier tend to convert pilots "
                "into production usage."
            )
        ],
    }
    extras_by_type: dict[str, dict[str, Any]] = {
        "argument": {
            **shared_extras,
            "thesis": (
                f"{title} argues that {topic.lower()} only matters when it becomes a "
                "reliable operating practice rather than an isolated demo."
            ),
            "counterpoint": (
                "A fair objection is that raw technical gains can still unlock new behavior "
                "before teams have mature process around them."
            ),
        },
        "mental_model": {
            **shared_extras,
            "what_it_explains": (
                "It explains why teams with similar tools get different outcomes once usage, "
                "ownership, and governance pressure enter the system."
            ),
            "when_to_use_it": (
                "Use it when evaluating whether a new capability is ready for production "
                "workflows rather than a contained pilot."
            ),
        },
        "playbook": {
            **shared_extras,
            "situation": (
                "A team has a promising workflow but needs to make it reliable enough for "
                "daily production use."
            ),
            "outcome": (
                "The target outcome is a workflow with clear owners, visible costs, measurable "
                "quality, and failure paths users can understand."
            ),
        },
        "briefing": {
            **shared_extras,
            "timeline": [
                {
                    "when": "Pilot phase",
                    "what": (
                        "Teams prove the workflow can create value under controlled conditions."
                    ),
                },
                {
                    "when": "Production phase",
                    "what": (
                        "Ownership, monitoring, cost controls, and governance decide whether "
                        "usage scales."
                    ),
                },
            ],
            "key_actors": [
                {
                    "name": "Platform teams",
                    "stake": "They need reusable infrastructure and clear operational boundaries.",
                },
                {
                    "name": "Product teams",
                    "stake": (
                        "They need workflow gains without absorbing hidden reliability problems."
                    ),
                },
            ],
            "what_to_watch": (
                "Watch whether the next iteration adds clearer ownership, better observability, "
                "and more explicit quality gates."
            ),
        },
        "findings": {
            **shared_extras,
            "question": (
                f"The source asks whether {topic.lower()} is creating durable workflow gains "
                "or just more visible experimentation."
            ),
            "method": (
                "It compares implementation patterns, organizational constraints, and the "
                "difference between demos and repeated operational use."
            ),
            "limits": (
                "The generated fixture is representative test content, so it is useful for UI "
                "exercise but not evidence for a real-world claim."
            ),
        },
    }
    one_line = (
        f"A typed {artifact_type.replace('_', ' ')} artifact about turning {topic.lower()} "
        "from demo energy into dependable production practice."
    )
    payload = {
        "title": title,
        "one_line": one_line,
        "ask": ARTIFACT_ASK_BY_TYPE[artifact_type],
        "artifact": {
            "type": artifact_type,
            "payload": {
                "overview": (
                    f"{title} treats {topic.lower()} as a test of operating maturity. "
                    "The useful question is not whether a demo looks impressive, but whether "
                    "teams can make the workflow reliable, governable, and economically legible "
                    "once it becomes part of everyday work."
                ),
                "quotes": generate_artifact_quotes(source),
                "key_points": generate_artifact_key_points(topic),
                "takeaway": (
                    "Treat the artifact as a production-readiness lens: capability only matters "
                    "when the surrounding workflow can carry it."
                ),
                "extras": extras_by_type[artifact_type],
            },
        },
        "generated_at": datetime.now(UTC).isoformat(),
        "source_context": {
            "url": url,
            "source_name": source,
            "publication_date": random_datetime(30).date().isoformat(),
            "platform": platform,
        },
        "selection_trace": {
            "source_hint": f"article:{platform}",
            "candidates": ["argument", "mental_model", "playbook", "briefing", "findings"],
            "selected": artifact_type,
            "reason": (
                "The fixture is designed to exercise the current typed long-form artifact "
                "renderer with enough structure for detail and feed preview states."
            ),
            "confidence": round(random.uniform(0.72, 0.92), 2),
        },
        "feed_preview": {
            "title": title,
            "one_line": one_line,
            "preview_bullets": [
                "Exercises the new typed artifact summary shape.",
                "Includes headed key points, source quotes, extras, and a feed preview.",
                "Visible in the local long-form inbox for app testing.",
            ],
            "reason_to_read": (
                "Use this fixture to inspect the new long-form card and detail rendering "
                "without waiting for a live ingestion run."
            ),
            "artifact_type": artifact_type,
        },
    }
    return LongformArtifactEnvelope.model_validate(payload)


def resolve_summary_format(summary_format: str) -> str:
    """Normalize summary format selection."""
    if summary_format != "mixed":
        return summary_format
    return random.choices(
        SUMMARY_FORMATS,
        weights=[0.3, 0.25, 0.2, 0.1, 0.1, 0.05],
        k=1,
    )[0]


def summary_classification(summary: SummaryPayload) -> str:
    """Return a content classification for summary payloads without a classification field."""
    classification = getattr(summary, "classification", None)
    if isinstance(classification, str) and classification in {"to_read", "skip"}:
        return classification
    return "to_read"


def generate_article_markdown_body(*, title: str, source: str, topics: list[str]) -> str:
    """Generate markdown body text for reader-mode fixture articles."""
    primary_topic = topics[0] if topics else "applied AI"
    secondary_topic = topics[1] if len(topics) > 1 else "operational quality"
    sections = [
        f"# {title}",
        (
            f"{source} frames **{primary_topic.lower()}** as a practical operating problem, "
            "not a demo-stage curiosity. The fixture body is intentionally structured to "
            "exercise the mobile markdown reader with headings, lists, quotes, links, "
            "inline code, and a compact table."
        ),
        "## The Operating Context",
        (
            "Teams are moving from isolated experiments toward repeated workflows. That shift "
            "changes the core question: the important issue is no longer whether the model can "
            "produce an impressive answer once, but whether the surrounding system can make "
            "that answer traceable, reviewable, and cheap enough to run every day."
        ),
        "\n".join(
            [
                "The strongest implementations tend to share three traits:",
                "",
                "- They keep human review close to the decision that carries risk.",
                (
                    "- They measure the boring path, including retries, queue delay, "
                    "and fallback rates."
                ),
                "- They document when automation should stop and hand control back to an operator.",
            ]
        ),
        (
            "> The pattern that survives is rarely the flashiest one. It is the one that makes "
            "the handoff between software and judgment explicit."
        ),
        "## Where the Friction Shows Up",
        (
            "The work becomes harder when teams try to turn prototypes into shared "
            "infrastructure. A local script can hide messy assumptions. A production workflow "
            "cannot. The useful test is whether a team can explain the failure mode without "
            "opening five dashboards or reconstructing a prompt from logs."
        ),
        (
            "For example, a fixture workflow might track `summary_kind`, `content_body_ref`, "
            "and `reader_variant` as separate fields. That looks verbose, but it gives the UI "
            "a stable contract and gives backend operators a place to inspect what happened."
        ),
        "\n".join(
            [
                "### Practical Signals",
                "",
                "1. The generated output has a clear owner.",
                "2. The source material remains available after summarization.",
                "3. The UI can distinguish a missing body from a pending body.",
                "4. The retry path does not silently change the content contract.",
            ]
        ),
        "\n".join(
            [
                "| Signal | Healthy shape | Reader impact |",
                "| --- | --- | --- |",
                (
                    "| Source body | Stored separately from summary metadata | "
                    "Full article mode opens reliably |"
                ),
                (
                    "| Rendered body | Markdown is preserved when available | "
                    "Headings and quotes remain readable |"
                ),
                "| Summary artifact | Typed payload, small enough for lists | Cards stay fast |",
            ]
        ),
        "## What To Watch",
        (
            "The next phase will be less about model capability and more about system "
            f"boundaries. Teams that treat **{secondary_topic.lower()}** as a product feature "
            "will have an easier time making the work feel dependable."
        ),
        (
            "Read the original context at "
            "[the source article](https://example.com/reader-fixture) when testing link "
            "styling in the reader."
        ),
    ]
    return "\n\n".join(sections)


class ArticleGenerator:
    """Generate article test data with full metadata."""

    @staticmethod
    def generate(
        url_base: str = "https://example.com/article",
        status: str = ContentStatus.COMPLETED.value,
        summary_format: str = "mixed",
    ) -> dict[str, Any]:
        """Generate a complete article with metadata using multiple summary formats."""
        article_id = random.randint(1000, 999999)
        url = f"{url_base}-{article_id}"
        title = random.choice(ARTICLE_TITLES)
        source = random.choice(ARTICLE_SOURCES)
        topics = random.choice(TOPICS)

        selected_format = resolve_summary_format(summary_format)
        summary: SummaryPayload
        summary_kind = SUMMARY_KIND_LONG_BULLETS
        summary_version = SUMMARY_VERSION_V1

        if selected_format == "longform_artifact":
            summary = generate_longform_artifact(
                title=title,
                topic=topics[0],
                url=url,
                source=source,
                platform="web",
            )
            summary_kind = SUMMARY_KIND_LONGFORM_ARTIFACT
            summary_version = SUMMARY_VERSION_V1
        elif selected_format == "editorial_narrative":
            summary = generate_editorial_narrative(title=title, topic=topics[0])
            summary_kind = SUMMARY_KIND_LONG_EDITORIAL_NARRATIVE
            summary_version = SUMMARY_VERSION_V1
        elif selected_format == "interleaved_v1":
            summary = InterleavedSummary(
                summary_type="interleaved",
                title=title,
                hook=(
                    f"This article explores {topics[0].lower()} with a focus on practical "
                    f"applications and future implications. It provides comprehensive analysis "
                    "backed by research and real-world examples demonstrating the impact."
                ),
                insights=generate_interleaved_insights(random.randint(5, 6)),
                takeaway=(
                    "Understanding these developments is crucial for anyone looking to stay ahead "
                    "in the rapidly evolving landscape. The implications extend beyond immediate "
                    "applications to reshape how we think about solving complex problems."
                ),
                classification="to_read" if random.random() > 0.2 else "skip",
                summarization_date=random_datetime(7),
            )
            summary_kind = SUMMARY_KIND_LONG_INTERLEAVED
            summary_version = SUMMARY_VERSION_V1
        elif selected_format == "interleaved_v2":
            summary = InterleavedSummaryV2(
                title=title,
                hook=(
                    f"This article explores {topics[0].lower()} with a focus on practical "
                    "applications and future implications. It provides comprehensive analysis "
                    "backed by research and real-world examples demonstrating the impact."
                ),
                key_points=generate_interleaved_key_points(random.randint(3, 5)),
                topics=generate_interleaved_topics(2),
                quotes=generate_quotes(random.randint(1, 2)),
                takeaway=(
                    "Understanding these developments is crucial for anyone looking to stay ahead "
                    "in the rapidly evolving landscape. The implications extend beyond immediate "
                    "applications to reshape how we think about solving complex problems."
                ),
                classification="to_read" if random.random() > 0.2 else "skip",
                summarization_date=random_datetime(7),
            )
            summary_kind = SUMMARY_KIND_LONG_INTERLEAVED
            summary_version = SUMMARY_VERSION_V2
        elif selected_format == "structured":
            summary = StructuredSummary(
                title=title,
                overview=(
                    "This article summarizes key developments, tying together evidence "
                    "from recent research and practitioner feedback."
                ),
                bullet_points=generate_bullet_points(random.randint(4, 6)),
                quotes=generate_quotes(random.randint(1, 3)),
                topics=topics,
                questions=generate_questions(random.randint(2, 3)),
                counter_arguments=generate_counter_arguments(random.randint(1, 2)),
                summarization_date=random_datetime(7),
                classification="to_read" if random.random() > 0.2 else "skip",
            )
            summary_kind = SUMMARY_KIND_LONG_STRUCTURED
            summary_version = SUMMARY_VERSION_V1
        else:
            summary = BulletedSummary(
                title=title,
                points=generate_bulleted_points(random.randint(10, 20)),
                classification="to_read" if random.random() > 0.2 else "skip",
                summarization_date=random_datetime(7),
            )
            summary_kind = SUMMARY_KIND_LONG_BULLETS
            summary_version = SUMMARY_VERSION_V1

        # Generate article metadata
        article_body = generate_article_markdown_body(title=title, source=source, topics=topics)
        metadata = ArticleMetadata(
            source=source,
            content=article_body,
            author=random.choice(["John Smith", "Jane Doe", "Alex Johnson"]),
            publication_date=random_datetime(30),
            content_type="markdown",
            final_url_after_redirects=url,
            word_count=random.randint(500, 3000),
            summary=summary,
            summary_kind=summary_kind,
            summary_version=summary_version,
        )

        return {
            "content_type": ContentType.ARTICLE.value,
            "url": url,
            "title": title,
            "source": source,
            "platform": "web",
            "status": status,
            "classification": summary_classification(summary),
            "content_metadata": metadata.model_dump(mode="json", exclude_none=True),
            "publication_date": metadata.publication_date,
            "processed_at": random_datetime(5) if status == ContentStatus.COMPLETED.value else None,
        }


class PodcastGenerator:
    """Generate podcast test data with full metadata."""

    @staticmethod
    def generate(
        url_base: str = "https://example.com/podcast",
        status: str = ContentStatus.COMPLETED.value,
        summary_format: str = "mixed",
    ) -> dict[str, Any]:
        """Generate a complete podcast with metadata using multiple summary formats."""
        episode_id = random.randint(1000, 999999)
        url = f"{url_base}/episode-{episode_id}.mp3"
        title = random.choice(PODCAST_TITLES)
        source = random.choice(PODCAST_SOURCES)
        topics = random.choice(TOPICS)
        episode_number = random.randint(1, 200)

        selected_format = resolve_summary_format(summary_format)
        summary: SummaryPayload
        summary_kind = SUMMARY_KIND_LONG_BULLETS
        summary_version = SUMMARY_VERSION_V1

        if selected_format == "longform_artifact":
            summary = generate_longform_artifact(
                title=title,
                topic=topics[0],
                url=url,
                source=source,
                platform="podcast",
            )
            summary_kind = SUMMARY_KIND_LONGFORM_ARTIFACT
            summary_version = SUMMARY_VERSION_V1
        elif selected_format == "editorial_narrative":
            summary = generate_editorial_narrative(title=title, topic=topics[0])
            summary_kind = SUMMARY_KIND_LONG_EDITORIAL_NARRATIVE
            summary_version = SUMMARY_VERSION_V1
        elif selected_format == "interleaved_v1":
            summary = InterleavedSummary(
                summary_type="interleaved",
                title=title,
                hook=(
                    f"In this episode, the hosts discuss {topics[0].lower()} "
                    "and share insights from their experiences. The conversation "
                    "covers key strategies, common pitfalls, and actionable advice "
                    "that listeners can apply immediately to their own work."
                ),
                insights=generate_interleaved_insights(random.randint(5, 6)),
                takeaway=(
                    "This episode offers valuable perspectives for practitioners at all levels. "
                    "The guests' combined experience provides a nuanced view that challenges "
                    "conventional thinking while offering practical next steps for listeners."
                ),
                classification="to_read" if random.random() > 0.15 else "skip",
                summarization_date=random_datetime(7),
            )
            summary_kind = SUMMARY_KIND_LONG_INTERLEAVED
            summary_version = SUMMARY_VERSION_V1
        elif selected_format == "interleaved_v2":
            summary = InterleavedSummaryV2(
                title=title,
                hook=(
                    f"In this episode, the hosts discuss {topics[0].lower()} "
                    "and share insights from their experiences. The conversation "
                    "covers key strategies, common pitfalls, and actionable advice "
                    "that listeners can apply immediately to their own work."
                ),
                key_points=generate_interleaved_key_points(random.randint(3, 5)),
                topics=generate_interleaved_topics(2),
                quotes=generate_quotes(random.randint(1, 2)),
                takeaway=(
                    "This episode offers valuable perspectives for practitioners at all levels. "
                    "The guests' combined experience provides a nuanced view that challenges "
                    "conventional thinking while offering practical next steps for listeners."
                ),
                classification="to_read" if random.random() > 0.15 else "skip",
                summarization_date=random_datetime(7),
            )
            summary_kind = SUMMARY_KIND_LONG_INTERLEAVED
            summary_version = SUMMARY_VERSION_V2
        elif selected_format == "structured":
            summary = StructuredSummary(
                title=title,
                overview=(
                    "This episode focuses on practical lessons and strategies "
                    "shared by the guests, supported by specific examples."
                ),
                bullet_points=generate_bullet_points(random.randint(4, 6)),
                quotes=generate_quotes(random.randint(1, 3)),
                topics=topics,
                questions=generate_questions(random.randint(2, 3)),
                counter_arguments=generate_counter_arguments(random.randint(1, 2)),
                summarization_date=random_datetime(7),
                classification="to_read" if random.random() > 0.15 else "skip",
            )
            summary_kind = SUMMARY_KIND_LONG_STRUCTURED
            summary_version = SUMMARY_VERSION_V1
        else:
            summary = BulletedSummary(
                title=title,
                points=generate_bulleted_points(random.randint(10, 20)),
                classification="to_read" if random.random() > 0.15 else "skip",
                summarization_date=random_datetime(7),
            )
            summary_kind = SUMMARY_KIND_LONG_BULLETS
            summary_version = SUMMARY_VERSION_V1

        # Generate podcast metadata
        metadata = PodcastMetadata(
            source=source,
            audio_url=url,
            transcript="Welcome to the podcast. Today we're discussing... [full transcript]",
            duration=random.randint(1200, 7200),
            episode_number=episode_number,
            video_url=None,
            video_id=None,
            channel_name=None,
            thumbnail_url=None,
            view_count=None,
            like_count=None,
            has_transcript=True,
            word_count=random.randint(3000, 10000),
            summary=summary,
            summary_kind=summary_kind,
            summary_version=summary_version,
        )

        return {
            "content_type": ContentType.PODCAST.value,
            "url": url,
            "title": title,
            "source": source,
            "platform": "podcast",
            "status": status,
            "classification": summary_classification(summary),
            "content_metadata": metadata.model_dump(mode="json", exclude_none=True),
            "publication_date": random_datetime(60),
            "processed_at": random_datetime(5) if status == ContentStatus.COMPLETED.value else None,
        }


class NewsGenerator:
    """Generate news test data with full metadata."""

    @staticmethod
    def generate(
        url_base: str = "https://example.com/news",
        status: str = ContentStatus.COMPLETED.value,
        day_offset: int = 0,
    ) -> dict[str, Any]:
        """Generate a complete news item with metadata."""
        news_id = random.randint(1000, 999999)
        article_url = f"{url_base}/story-{news_id}"
        headline = random.choice(NEWS_HEADLINES)
        platform = random.choice(NEWS_PLATFORMS)
        source_domain = "example.com"
        created_at = random_datetime_for_day_offset(day_offset)
        processed_at = None
        if status == ContentStatus.COMPLETED.value:
            processed_at = min(
                created_at + timedelta(minutes=random.randint(5, 180)),
                utc_now_naive(),
            )

        # Generate news summary
        summary = NewsSummary(
            title=headline,
            article_url=article_url,
            key_points=[
                "Major announcement reveals significant industry impact",
                "Experts predict long-term implications for the sector",
                "Initial reactions from market analysts are mixed",
            ],
            summary="Breaking news with significant implications for tech and broader markets.",
            classification="to_read" if random.random() > 0.3 else "skip",
            summarization_date=processed_at or created_at,
        )

        # Build discussion URL based on platform
        if platform == "hackernews":
            discussion_url = f"https://news.ycombinator.com/item?id={news_id}"
            aggregator_name = "Hacker News"
        elif platform == "reddit":
            discussion_url = f"https://reddit.com/r/technology/comments/{news_id}"
            aggregator_name = "Reddit"
        else:
            discussion_url = f"https://techmeme.com/{news_id}"
            aggregator_name = "Techmeme"

        # Generate news metadata
        metadata: dict[str, Any] = {
            "source": source_domain,
            "platform": platform,
            "summary_kind": SUMMARY_KIND_SHORT_NEWS,
            "summary_version": SUMMARY_VERSION_V1,
            "article": {
                "url": article_url,
                "title": headline,
                "source_domain": source_domain,
            },
            "aggregator": {
                "name": aggregator_name,
                "url": discussion_url,
                "external_id": str(news_id),
                "metadata": {"score": random.randint(50, 500)} if platform == "hackernews" else {},
            },
            "discovery_time": created_at.isoformat(),
            "summary": summary.model_dump(mode="json", exclude_none=True),
        }

        # Add discussion data for completed items (~70% chance)
        if status == ContentStatus.COMPLETED.value and random.random() < 0.7:
            metadata["discussion_url"] = discussion_url
            discussion_payload = (
                generate_discussion_list_payload(discussion_url=discussion_url)
                if platform == "techmeme"
                else generate_comments_discussion_payload(discussion_url=discussion_url)
            )
            top_comment, comment_count = discussion_preview_fields(discussion_payload)
            metadata["discussion_status"] = "completed"
            metadata["discussion_fetched_at"] = datetime.now(UTC).isoformat()
            metadata["discussion_payload"] = discussion_payload
            if top_comment is not None:
                metadata["top_comment"] = top_comment
            if comment_count is not None:
                metadata["comment_count"] = comment_count

        return {
            "content_type": ContentType.NEWS.value,
            "url": article_url,
            "title": headline,
            "source": source_domain,
            "platform": platform,
            "status": status,
            "classification": summary.classification,
            "content_metadata": metadata,
            "created_at": created_at,
            "publication_date": created_at - timedelta(minutes=random.randint(15, 360)),
            "processed_at": processed_at,
        }


def generate_test_data(
    num_articles: int = 10,
    num_podcasts: int = 5,
    num_news: int = 30,
    include_pending: bool = True,
    article_summary_format: str = "mixed",
    podcast_summary_format: str = "mixed",
    news_days_back: int = 5,
    target_user_ids: list[int] | None = None,
) -> list[dict[str, Any]]:
    """
    Generate a mix of test data across all content types.

    Args:
        num_articles: Number of articles to generate
        num_podcasts: Number of podcasts to generate
        num_news: Number of news items to generate
        include_pending: Include some items in pending/processing states
        news_days_back: Spread generated news across this many recent UTC days
        target_user_ids: Optional target users for inbox assignment

    Returns:
        List of content dictionaries ready for database insertion
    """
    data = []

    # Generate articles
    for i in range(num_articles):
        if include_pending and i % 5 == 0:
            status = random.choice([ContentStatus.NEW.value, ContentStatus.PROCESSING.value])
        else:
            status = ContentStatus.COMPLETED.value
        data.append(ArticleGenerator.generate(status=status, summary_format=article_summary_format))

    # Generate podcasts
    for i in range(num_podcasts):
        if include_pending and i % 4 == 0:
            status = random.choice([ContentStatus.NEW.value, ContentStatus.PROCESSING.value])
        else:
            status = ContentStatus.COMPLETED.value
        data.append(PodcastGenerator.generate(status=status, summary_format=podcast_summary_format))

    # Generate news
    for i in range(num_news):
        if include_pending and i % 6 == 0:
            status = random.choice([ContentStatus.NEW.value, ContentStatus.PROCESSING.value])
        else:
            status = ContentStatus.COMPLETED.value
        data.append(
            NewsGenerator.generate(
                status=status,
                day_offset=i % max(news_days_back, 1),
            )
        )

    return data


def _fetch_user_ids(session: Session) -> list[int]:
    """Fetch all user IDs from the database."""
    return [row[0] for row in session.query(User.id).all()]


def _resolve_logged_in_user_id(session: Session) -> int | None:
    """Resolve the most likely logged-in user ID.

    This is a best-effort resolver because JWT sessions are stateless and not persisted.
    It prefers the most recently updated active non-admin user.
    """
    user = (
        session.query(User)
        .filter(User.is_active.is_(True))
        .filter(User.is_admin.is_(False))
        .order_by(User.updated_at.desc())
        .first()
    )
    if user is not None:
        return user.id

    fallback_user = (
        session.query(User)
        .filter(User.is_active.is_(True))
        .order_by(User.updated_at.desc())
        .first()
    )
    return fallback_user.id if fallback_user is not None else None


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


def _insert_news_item_discussions(session: Session, *, content_ids: list[int]) -> None:
    """Create current-schema news-item discussion rows for generated news fixtures."""
    if not content_ids:
        return

    news_items = session.query(NewsItem).filter(NewsItem.legacy_content_id.in_(content_ids)).all()
    now = utc_now_naive()
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


def _scope_generated_news_items(
    session: Session,
    *,
    content_ids: list[int],
    user_ids: list[int] | None,
) -> None:
    """Make generated news app-visible when the script targets one user."""
    if not content_ids or user_ids is None or len(user_ids) != 1:
        return

    target_user_id = user_ids[0]
    news_items = session.query(NewsItem).filter(NewsItem.legacy_content_id.in_(content_ids)).all()
    for item in news_items:
        item.visibility_scope = NewsItemVisibilityScope.USER.value
        item.owner_user_id = target_user_id
        if item.status == NewsItemStatus.NEW.value:
            item.status = NewsItemStatus.READY.value


def insert_test_data(
    session: Session,
    data: list[dict[str, Any]],
    user_ids: list[int] | None = None,
) -> list[int]:
    """
    Insert test data into the database.

    Args:
        session: SQLAlchemy session
        data: List of content dictionaries
        user_ids: User IDs to add articles/podcasts to inbox for. Defaults to all users.

    Returns:
        List of inserted content IDs
    """
    inserted_ids = []
    inserted_news_content_ids: list[int] = []

    if user_ids is None:
        user_ids = _fetch_user_ids(session)

    for item in data:
        target_status = item.get("target_status")
        target_user_id = item.get("target_user_id")
        content_payload = {
            key: value
            for key, value in item.items()
            if key not in {"target_status", "target_user_id"}
        }
        content = Content(**content_payload)
        session.add(content)
        session.flush()  # Get the ID
        content_id = content.id
        if content_id is None:
            raise ValueError("Inserted content missing id")
        inserted_ids.append(content_id)
        if content.content_type == ContentType.NEWS.value:
            inserted_news_content_ids.append(content_id)

        # SQLite can reuse primary keys for rows that were deleted earlier.
        # If the local dev DB contains orphaned per-user rows for an old content ID,
        # clear them before creating inbox entries for the new content row.
        session.query(ContentStatusEntry).filter(
            ContentStatusEntry.content_id == content.id
        ).delete(synchronize_session=False)
        session.query(ContentReadStatus).filter(ContentReadStatus.content_id == content.id).delete(
            synchronize_session=False
        )

        metadata = content.content_metadata if isinstance(content.content_metadata, dict) else {}
        discussion_url = metadata.get("discussion_url")
        discussion_payload = metadata.get("discussion_payload")
        if not isinstance(discussion_payload, dict) and (
            discussion_url and content.status == ContentStatus.COMPLETED.value
        ):
            discussion_payload = (
                generate_discussion_list_payload(discussion_url=discussion_url)
                if content.platform == "techmeme"
                else generate_comments_discussion_payload(discussion_url=discussion_url)
            )

        if isinstance(discussion_payload, dict):
            session.add(
                ContentDiscussion(
                    content_id=content.id,
                    platform=content.platform,
                    status="completed",
                    discussion_data=discussion_payload,
                    fetched_at=utc_now_naive(),
                )
            )

        if target_status and target_user_id is not None:
            session.add(
                ContentStatusEntry(
                    user_id=target_user_id,
                    content_id=content.id,
                    status=target_status,
                )
            )
            continue

        # Add longform content to users' inboxes so it is visible in list endpoints.
        # News items are visible through the feed query without a content_status row.
        if item["content_type"] in ("article", "podcast") and user_ids:
            for user_id in user_ids:
                session.add(
                    ContentStatusEntry(
                        user_id=user_id,
                        content_id=content.id,
                        status=CONTENT_STATUS_INBOX,
                    )
                )

    if inserted_news_content_ids:
        backfill_news_items_from_contents(
            session,
            content_ids=inserted_news_content_ids,
            only_missing=False,
        )
        _scope_generated_news_items(
            session,
            content_ids=inserted_news_content_ids,
            user_ids=user_ids,
        )
        _insert_news_item_discussions(session, content_ids=inserted_news_content_ids)

    session.commit()
    return inserted_ids


def _parse_user_ids(raw_value: str | None) -> list[int] | None:
    """Parse comma-separated user IDs into a list."""
    if not raw_value:
        return None
    user_ids: list[int] = []
    for chunk in raw_value.split(","):
        cleaned = chunk.strip()
        if not cleaned:
            continue
        try:
            user_ids.append(int(cleaned))
        except ValueError:
            continue
    return user_ids or None


def resolve_target_user_ids(
    session: Session,
    raw_user_ids: str | None,
    use_logged_in_user: bool,
) -> list[int] | None:
    """Resolve user IDs for content visibility entries.

    Args:
        session: SQLAlchemy session.
        raw_user_ids: Optional comma-separated user IDs from CLI.
        use_logged_in_user: Whether to target the inferred logged-in user.

    Returns:
        User ID list for inbox entries, or None to target all users.

    Raises:
        ValueError: If both targeting modes are set or logged-in user can't be resolved.
    """
    if raw_user_ids and use_logged_in_user:
        raise ValueError("Use either --user-ids or --logged-in-user, not both.")

    parsed_user_ids = _parse_user_ids(raw_user_ids)
    if parsed_user_ids is not None:
        return parsed_user_ids

    if not use_logged_in_user:
        return None

    resolved_user_id = _resolve_logged_in_user_id(session)
    if resolved_user_id is None:
        raise ValueError("Could not resolve a logged-in user ID from the database.")
    return [resolved_user_id]


def main():
    """Main entry point for the script."""
    import argparse

    parser = argparse.ArgumentParser(description="Generate test data for news_app")
    parser.add_argument("--articles", type=int, default=10, help="Number of articles to generate")
    parser.add_argument("--podcasts", type=int, default=5, help="Number of podcasts to generate")
    parser.add_argument("--news", type=int, default=30, help="Number of news items to generate")
    parser.add_argument(
        "--news-days-back",
        type=int,
        default=5,
        help="Spread generated news across this many recent UTC days",
    )
    parser.add_argument(
        "--no-pending",
        action="store_true",
        help="Don't include items in pending/processing states",
    )
    parser.add_argument("--dry-run", action="store_true", help="Generate but don't insert data")
    parser.add_argument(
        "--article-summary-format",
        choices=[
            "mixed",
            "longform_artifact",
            "editorial_narrative",
            "bulleted",
            "interleaved_v2",
            "interleaved_v1",
            "structured",
        ],
        default="mixed",
        help="Summary format for articles (default: mixed)",
    )
    parser.add_argument(
        "--podcast-summary-format",
        choices=[
            "mixed",
            "longform_artifact",
            "editorial_narrative",
            "bulleted",
            "interleaved_v2",
            "interleaved_v1",
            "structured",
        ],
        default="mixed",
        help="Summary format for podcasts (default: mixed)",
    )
    parser.add_argument(
        "--user-ids",
        help="Comma-separated user IDs to receive article/podcast inbox entries",
    )
    parser.add_argument(
        "--logged-in-user",
        action="store_true",
        help=(
            "Target only the inferred logged-in user (most recently updated active non-admin user)"
        ),
    )

    args = parser.parse_args()

    print("Generating test data:")
    print(f"  - {args.articles} articles")
    print(f"  - {args.podcasts} podcasts")
    print(f"  - {args.news} news items")
    print(f"  - News spread across {args.news_days_back} day(s)")

    init_db()
    with get_db() as session:
        try:
            user_ids = resolve_target_user_ids(
                session=session,
                raw_user_ids=args.user_ids,
                use_logged_in_user=args.logged_in_user,
            )
        except ValueError as exc:
            parser.error(str(exc))
        if user_ids is None:
            print("  - Inbox assignment user IDs: all users")
        else:
            print(f"  - Inbox assignment user IDs: {', '.join(map(str, user_ids))}")
        data = generate_test_data(
            num_articles=args.articles,
            num_podcasts=args.podcasts,
            num_news=args.news,
            include_pending=not args.no_pending,
            article_summary_format=args.article_summary_format,
            podcast_summary_format=args.podcast_summary_format,
            news_days_back=args.news_days_back,
            target_user_ids=user_ids,
        )

        if args.dry_run:
            print(f"\nDry run - generated {len(data)} items (not inserted)")
            article_sample = next((d for d in data if d["content_type"] == "article"), None)
            if article_sample:
                print("\nSample article:")
                print(f"  Title: {article_sample['title']}")
                print(f"  Source: {article_sample['source']}")
                print(f"  Status: {article_sample['status']}")
            return

        # Insert into database
        print("\nInserting data into database...")
        inserted_ids = insert_test_data(session, data, user_ids=user_ids)

    print(f"\nSuccessfully inserted {len(inserted_ids)} items")
    print(f"  IDs: {min(inserted_ids)} - {max(inserted_ids)}")

    # Print summary by type
    articles = sum(1 for d in data if d["content_type"] == "article")
    podcasts = sum(1 for d in data if d["content_type"] == "podcast")
    news = sum(1 for d in data if d["content_type"] == "news")

    print("\nBreakdown:")
    print(f"  Articles: {articles}")
    print(f"  Podcasts: {podcasts}")
    print(f"  News: {news}")


if __name__ == "__main__":
    main()
