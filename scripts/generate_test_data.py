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
    SUMMARY_KIND_LONG_BULLETS,
    SUMMARY_KIND_LONG_INTERLEAVED,
    SUMMARY_KIND_LONG_STRUCTURED,
    SUMMARY_KIND_SHORT_NEWS_DIGEST,
    SUMMARY_VERSION_V1,
    SUMMARY_VERSION_V2,
)
from app.core.db import get_db, init_db
from app.models.metadata import (
    ArticleMetadata,
    BulletedSummary,
    ContentStatus,
    ContentType,
    InterleavedInsight,
    InterleavedSummary,
    InterleavedSummaryV2,
    InterleavedTopic,
    NewsSummary,
    PodcastMetadata,
    StructuredSummary,
    SummaryTextBullet,
)
from app.models.schema import Content, ContentReadStatus, ContentStatusEntry
from app.models.user import User

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
            "Having worked in this space, the regulatory angle "
            "is what most people miss entirely."
        ),
    },
    {
        "author": "dang",
        "text": (
            "We changed the title from the clickbait original. "
            "Please keep discussion substantive."
        ),
    },
    {
        "author": "rauchg",
        "text": (
            "We've been building toward this at Vercel. "
            "The DX implications are massive."
        ),
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
            "This confirms the trend I wrote about last month. "
            "The ecosystem is consolidating fast."
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
            "I built a quick prototype using this "
            "and the API ergonomics are surprisingly good."
        ),
    },
    {
        "author": "antirez",
        "text": (
            "Simple systems that work beat complex systems "
            "that don't. This gets that right."
        ),
    },
]

SUMMARY_FORMATS = ["bulleted", "interleaved_v2", "structured", "interleaved_v1"]
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


def generate_bullet_points(count: int = 4) -> list[dict[str, str]]:
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
        {"text": random.choice(points), "category": random.choice(categories)} for _ in range(count)
    ]


def generate_quotes(count: int = 2) -> list[dict[str, str | None]]:
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
        {"text": text, "context": ctx, "attribution": attribution}
        for text, ctx, attribution in random.sample(quotes, min(count, len(quotes)))
    ]


def generate_bulleted_points(count: int) -> list[dict[str, Any]]:
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
    points: list[dict[str, Any]] = []
    for text in selected:
        detail_focus = text.lower().rstrip(".")
        detail = random.choice(detail_templates).format(detail_focus=detail_focus)
        points.append(
            {
                "text": text,
                "detail": detail,
                "quotes": generate_quotes(random.randint(1, 3)),
            }
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


def resolve_summary_format(summary_format: str) -> str:
    """Normalize summary format selection."""
    if summary_format != "mixed":
        return summary_format
    return random.choices(
        SUMMARY_FORMATS,
        weights=[0.45, 0.25, 0.2, 0.1],
        k=1,
    )[0]


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
        summary_kind = SUMMARY_KIND_LONG_BULLETS
        summary_version = SUMMARY_VERSION_V1

        if selected_format == "interleaved_v1":
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
        metadata = ArticleMetadata(
            source=source,
            content="Full article text content with multiple paragraphs...",
            author=random.choice(["John Smith", "Jane Doe", "Alex Johnson"]),
            publication_date=random_datetime(30),
            content_type="html",
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
            "classification": summary.classification,
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
        summary_kind = SUMMARY_KIND_LONG_BULLETS
        summary_version = SUMMARY_VERSION_V1

        if selected_format == "interleaved_v1":
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
            "classification": summary.classification,
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
            "summary_kind": SUMMARY_KIND_SHORT_NEWS_DIGEST,
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
            "discovery_time": created_at,
            "summary": summary.model_dump(mode="json", exclude_none=True),
        }

        # Add discussion data for completed items (~70% chance)
        if status == ContentStatus.COMPLETED.value and random.random() < 0.7:
            metadata["top_comment"] = random.choice(DISCUSSION_COMMENTS)
            metadata["discussion_url"] = discussion_url

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
) -> list[dict[str, Any]]:
    """
    Generate a mix of test data across all content types.

    Args:
        num_articles: Number of articles to generate
        num_podcasts: Number of podcasts to generate
        num_news: Number of news items to generate
        include_pending: Include some items in pending/processing states
        news_days_back: Spread generated news across this many recent UTC days

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
        data.append(
            PodcastGenerator.generate(status=status, summary_format=podcast_summary_format)
        )

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

    if user_ids is None:
        user_ids = _fetch_user_ids(session)

    for item in data:
        content = Content(**item)
        session.add(content)
        session.flush()  # Get the ID
        inserted_ids.append(content.id)

        # SQLite can reuse primary keys for rows that were deleted earlier.
        # If the local dev DB contains orphaned per-user rows for an old content ID,
        # clear them before creating inbox entries for the new content row.
        session.query(ContentStatusEntry).filter(
            ContentStatusEntry.content_id == content.id
        ).delete(synchronize_session=False)
        session.query(ContentReadStatus).filter(
            ContentReadStatus.content_id == content.id
        ).delete(synchronize_session=False)

        # Add longform content to users' inboxes so it is visible in list endpoints.
        # News items are visible through the feed query without a content_status row.
        if item["content_type"] in ("article", "podcast") and user_ids:
            for user_id in user_ids:
                session.add(
                    ContentStatusEntry(
                        user_id=user_id,
                        content_id=content.id,
                        status="inbox",
                    )
                )

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
        choices=["mixed", "bulleted", "interleaved_v2", "interleaved_v1", "structured"],
        default="mixed",
        help="Summary format for articles (default: mixed)",
    )
    parser.add_argument(
        "--podcast-summary-format",
        choices=["mixed", "bulleted", "interleaved_v2", "interleaved_v1", "structured"],
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
            "Target only the inferred logged-in user "
            "(most recently updated active non-admin user)"
        ),
    )

    args = parser.parse_args()

    # Generate data
    print("Generating test data:")
    print(f"  - {args.articles} articles")
    print(f"  - {args.podcasts} podcasts")
    print(f"  - {args.news} news items")
    print(f"  - News spread across {args.news_days_back} day(s)")

    data = generate_test_data(
        num_articles=args.articles,
        num_podcasts=args.podcasts,
        num_news=args.news,
        include_pending=not args.no_pending,
        article_summary_format=args.article_summary_format,
        podcast_summary_format=args.podcast_summary_format,
        news_days_back=args.news_days_back,
    )

    if args.dry_run:
        print(f"\nDry run - generated {len(data)} items (not inserted)")
        print("\nSample article:")
        article_sample = next((d for d in data if d["content_type"] == "article"), None)
        if article_sample:
            print(f"  Title: {article_sample['title']}")
            print(f"  Source: {article_sample['source']}")
            print(f"  Status: {article_sample['status']}")
        return

    # Insert into database
    print("\nInserting data into database...")
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
