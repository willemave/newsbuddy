"""Create a realistic unread news digest run for local UI testing."""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.db import get_db
from app.core.logging import setup_logging
from app.models.schema import (
    NewsDigest,
    NewsDigestBullet,
    NewsDigestBulletSource,
    NewsItem,
    NewsItemDigestCoverage,
)
from app.models.user import User


@dataclass(frozen=True)
class SeedCitation:
    """One news item to create and attach to a seeded bullet."""

    source_label: str
    title: str
    article_domain: str
    article_url: str
    discussion_url: str
    summary_text: str
    key_points: list[str]


@dataclass(frozen=True)
class SeedBullet:
    """One seeded digest bullet with backing citations."""

    topic: str
    details: str
    citations: list[SeedCitation]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seed local news digest preview data")
    parser.add_argument(
        "--user-id",
        type=int,
        default=None,
        help="Target an existing user by ID.",
    )
    parser.add_argument(
        "--email",
        type=str,
        default=None,
        help="Target an existing user by email.",
    )
    parser.add_argument(
        "--create-demo-user",
        action="store_true",
        help="Create a demo user when no matching user exists.",
    )
    return parser.parse_args()


def _resolve_target_user(
    *,
    args: argparse.Namespace,
    db,
) -> User:
    query = db.query(User)
    user: User | None = None

    if args.user_id is not None:
        user = query.filter(User.id == args.user_id).first()
    elif args.email:
        user = query.filter(User.email == args.email).first()
    else:
        user = query.filter(User.is_active.is_(True)).order_by(User.id.desc()).first()

    if user is not None:
        return user

    if not args.create_demo_user and (args.user_id is not None or args.email):
        raise SystemExit("Target user not found. Re-run with --create-demo-user to create one.")

    if not args.create_demo_user and args.user_id is None and not args.email:
        raise SystemExit(
            "No active users found. Re-run with --create-demo-user or pass --email/--user-id."
        )

    suffix = uuid4().hex[:8]
    user = User(
        apple_id=f"digest-preview-{suffix}",
        email=args.email or f"digest-preview-{suffix}@example.com",
        full_name="Digest Preview User",
        is_active=True,
        news_digest_timezone="US/Pacific",
    )
    db.add(user)
    db.flush()
    return user


def _seed_bullets() -> list[SeedBullet]:
    return [
        SeedBullet(
            topic="Enterprise AI pilots are turning into workflow fights",
            details=(
                "OpenAI's latest enterprise push is landing, but buyers still care more about "
                "workflow fit and reliability than raw benchmark gains."
            ),
            citations=[
                SeedCitation(
                    source_label="Hacker News",
                    title="OpenAI's new enterprise agents are winning cautious pilots",
                    article_domain="openai.com",
                    article_url="https://openai.com/index/enterprise-agents-pilots",
                    discussion_url="https://news.ycombinator.com/item?id=44100001",
                    summary_text=(
                        "Early customers are moving from experiments into narrow production "
                        "rollouts with strict ROI checks."
                    ),
                    key_points=[
                        "Enterprise buyers want measurable automation gains before widening scope.",
                        "Reliability and controls remain bigger blockers than model quality.",
                    ],
                ),
                SeedCitation(
                    source_label="Techmeme",
                    title="Vendors are reframing copilots as task-specific operators",
                    article_domain="techmeme.com",
                    article_url="https://www.techmeme.com/2603/p12",
                    discussion_url="https://www.techmeme.com/2603/p12#a2603p12",
                    summary_text=(
                        "The market is shifting away from generic chat surfaces toward embedded "
                        "operators inside existing SaaS workflows."
                    ),
                    key_points=[
                        (
                            "Embedded automation is outperforming standalone copilots in "
                            "buyer interest."
                        ),
                    ],
                ),
            ],
        ),
        SeedBullet(
            topic="AI infra demand is still boxed in by packaging capacity",
            details=(
                "Advanced packaging remains the hard bottleneck for AI infra, keeping 2026 "
                "supply tight even as more wafer capacity comes online."
            ),
            citations=[
                SeedCitation(
                    source_label="SemiAnalysis",
                    title="CoWoS and HBM packaging are still the gating factor",
                    article_domain="semianalysis.com",
                    article_url="https://semianalysis.com/2026/03/30/cowos-capacity-tightens/",
                    discussion_url="https://news.ycombinator.com/item?id=44100002",
                    summary_text=(
                        "Back-end packaging timelines, not front-end wafer starts, are still "
                        "driving deployment delays for large GPU clusters."
                    ),
                    key_points=[
                        "Packaging lead times remain elevated.",
                        "Cluster operators are reordering around delivery certainty.",
                    ],
                ),
                SeedCitation(
                    source_label="Stratechery",
                    title="AI capex keeps pulling supply chains into longer contracts",
                    article_domain="stratechery.com",
                    article_url="https://stratechery.com/2026/ai-capex-supply-chain/",
                    discussion_url="https://news.ycombinator.com/item?id=44100003",
                    summary_text=(
                        "Cloud buyers are accepting longer commitments to lock in limited "
                        "packaging and memory supply."
                    ),
                    key_points=[
                        "Longer-term supply deals are becoming a strategic advantage.",
                    ],
                ),
                SeedCitation(
                    source_label="The Information",
                    title="Cloud providers are still rationing premium GPU access",
                    article_domain="theinformation.com",
                    article_url="https://www.theinformation.com/articles/gpu-rationing-continues",
                    discussion_url="https://news.ycombinator.com/item?id=44100004",
                    summary_text=(
                        "Top-tier customers still get priority allocation, leaving smaller teams "
                        "to absorb delays and pricing volatility."
                    ),
                    key_points=[
                        "Priority allocation is shaping who can ship AI products on schedule.",
                    ],
                ),
            ],
        ),
        SeedBullet(
            topic="Figma's filing puts design-software pricing back on trial",
            details=(
                "Figma's IPO filing sharpens the debate over seat expansion, platform depth, and "
                "whether Adobe can keep defending bundle economics."
            ),
            citations=[
                SeedCitation(
                    source_label="The Verge",
                    title="Figma files publicly and reframes the design stack battle",
                    article_domain="theverge.com",
                    article_url="https://www.theverge.com/2026/03/30/figma-ipo-filing",
                    discussion_url="https://news.ycombinator.com/item?id=44100005",
                    summary_text=(
                        "The filing emphasizes product breadth and enterprise expansion rather "
                        "than pure designer-seat growth."
                    ),
                    key_points=[
                        (
                            "Figma is positioning itself as a cross-functional platform, "
                            "not a point tool."
                        ),
                    ],
                ),
                SeedCitation(
                    source_label="Techmeme",
                    title="Adobe and Figma are back in open competition",
                    article_domain="techmeme.com",
                    article_url="https://www.techmeme.com/2603/p18",
                    discussion_url="https://www.techmeme.com/2603/p18#a2603p18",
                    summary_text=(
                        "The public filing reopens scrutiny on pricing power and how much bundle "
                        "advantage Adobe still has."
                    ),
                    key_points=[
                        "Investors are watching net expansion and multi-product adoption closely.",
                    ],
                ),
            ],
        ),
    ]


def _create_news_item(
    *,
    citation: SeedCitation,
    user: User,
    position: int,
    generated_at: datetime,
) -> NewsItem:
    slug = uuid4().hex[:10]
    return NewsItem(
        ingest_key=f"digest-preview-{user.id}-{position}-{slug}",
        visibility_scope="global",
        owner_user_id=None,
        platform="seeded",
        source_type="seeded",
        source_label=citation.source_label,
        source_external_id=f"seeded-{slug}",
        canonical_item_url=citation.discussion_url,
        canonical_story_url=citation.article_url,
        article_url=citation.article_url,
        article_title=citation.title,
        article_domain=citation.article_domain,
        discussion_url=citation.discussion_url,
        summary_title=citation.title,
        summary_key_points=citation.key_points,
        summary_text=citation.summary_text,
        raw_metadata={},
        status="ready",
        ingested_at=generated_at - timedelta(minutes=45 - position),
        processed_at=generated_at - timedelta(minutes=40 - position),
    )


def main() -> None:
    """Seed one digest run plus backing citations for local testing."""
    setup_logging()
    args = _parse_args()
    seed_bullets = _seed_bullets()
    generated_at = datetime.now(UTC).replace(tzinfo=None)

    with get_db() as db:
        user = _resolve_target_user(args=args, db=db)

        digest = NewsDigest(
            user_id=user.id,
            timezone=user.news_digest_timezone or "US/Pacific",
            title="Test digest for source tags",
            summary=(
                "Three high-signal bullets for previewing shorter digest copy and inline "
                "source tags."
            ),
            source_count=sum(len(bullet.citations) for bullet in seed_bullets),
            group_count=len(seed_bullets),
            embedding_model="seeded-preview",
            llm_model="seeded-preview",
            pipeline_version="manual-test-seed",
            trigger_reason="manual_test_seed",
            generated_at=generated_at,
            window_start_at=generated_at - timedelta(hours=3),
            window_end_at=generated_at,
            build_metadata={"seeded_by": "scripts/seed_news_digest_test_data.py"},
        )
        db.add(digest)
        db.flush()

        created_item_ids: list[int] = []
        position = 0
        for bullet_index, seed_bullet in enumerate(seed_bullets, start=1):
            bullet = NewsDigestBullet(
                digest_id=digest.id,
                position=bullet_index,
                topic=seed_bullet.topic,
                details=seed_bullet.details,
                source_count=len(seed_bullet.citations),
            )
            db.add(bullet)
            db.flush()

            for citation_index, citation in enumerate(seed_bullet.citations, start=1):
                position += 1
                item = _create_news_item(
                    citation=citation,
                    user=user,
                    position=position,
                    generated_at=generated_at,
                )
                db.add(item)
                db.flush()
                created_item_ids.append(item.id)

                db.add(
                    NewsDigestBulletSource(
                        bullet_id=bullet.id,
                        news_item_id=item.id,
                        position=citation_index,
                    )
                )
                db.add(
                    NewsItemDigestCoverage(
                        user_id=user.id,
                        news_item_id=item.id,
                        digest_id=digest.id,
                    )
                )

        db.flush()

        print(
            f"Created digest {digest.id} for user {user.id} ({user.email}) with "
            f"{len(seed_bullets)} bullets and {len(created_item_ids)} cited items."
        )


if __name__ == "__main__":
    main()
