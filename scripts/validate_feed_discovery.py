"""Validate feed discovery workflow in stub or live mode."""

from __future__ import annotations

import argparse

from sqlalchemy import func

from app.core.db import get_db
from app.core.logging import get_logger, setup_logging
from app.core.settings import get_settings
from app.models.feed_discovery import (
    DiscoveryCandidate,
    DiscoveryCandidateBatch,
    DiscoveryDirection,
    DiscoveryDirectionPlan,
    DiscoveryLane,
    DiscoveryLanePlan,
    DiscoveryQuery,
)
from app.models.schema import ContentKnowledgeSave, FeedDiscoveryRun
from app.services.exa_client import ExaSearchResult
from app.services.feed_discovery import FeedDiscoveryDeps, run_feed_discovery

logger = get_logger(__name__)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate feed discovery")
    parser.add_argument("--user-id", type=int, default=None)
    parser.add_argument("--live", action="store_true", help="Run with real LLM + Exa")
    return parser.parse_args()


def _stub_direction_selector(db_session, user_id: int) -> DiscoveryDirectionPlan:
    rows = (
        db_session.query(ContentKnowledgeSave).filter(ContentKnowledgeSave.user_id == user_id).all()
    )
    ids = [row.content_id for row in rows]
    midpoint = max(1, len(ids) // 2)
    return DiscoveryDirectionPlan(
        summary="Stub discovery directions",
        directions=[
            DiscoveryDirection(
                name="Primary themes",
                rationale="Derived from top favorites",
                favorite_ids=ids[:midpoint],
            ),
            DiscoveryDirection(
                name="Adjacent topics",
                rationale="Explore nearby interests",
                favorite_ids=ids[midpoint:],
            ),
        ],
    )


def _stub_lane_planner(plan: DiscoveryDirectionPlan) -> DiscoveryLanePlan:
    return DiscoveryLanePlan(
        lanes=[
            DiscoveryLane(
                name="Feeds",
                goal="Find relevant RSS feeds",
                target="feeds",
                queries=[
                    DiscoveryQuery(query="indie tech RSS feed", rationale="Stub query"),
                    DiscoveryQuery(query="small web newsletter feed", rationale="Stub query"),
                ],
            ),
            DiscoveryLane(
                name="Podcasts",
                goal="Find relevant podcast RSS feeds",
                target="podcasts",
                queries=[
                    DiscoveryQuery(query="technology podcast rss", rationale="Stub query"),
                    DiscoveryQuery(query="product podcast feed", rationale="Stub query"),
                ],
            ),
            DiscoveryLane(
                name="YouTube",
                goal="Find relevant YouTube channels",
                target="youtube",
                queries=[
                    DiscoveryQuery(
                        query="AI infrastructure youtube channel", rationale="Stub query"
                    ),
                    DiscoveryQuery(query="engineering leadership youtube", rationale="Stub query"),
                ],
            ),
        ]
    )


def _stub_exa_search(query: str, num_results: int) -> list[ExaSearchResult]:
    return [
        ExaSearchResult(
            title=f"Stub result for {query}",
            url="https://www.youtube.com/channel/UC1234567890",
            snippet="Stub snippet",
        ),
        ExaSearchResult(
            title="Example Feed",
            url="https://example.com/feed.xml",
            snippet="Example feed",
        ),
        ExaSearchResult(
            title="Example Podcast",
            url="https://example.com/podcast.xml",
            snippet="Example podcast",
        ),
    ]


def _stub_candidate_extractor(
    lane: DiscoveryLane,
    results: list[ExaSearchResult],
) -> DiscoveryCandidateBatch:
    return DiscoveryCandidateBatch(
        candidates=[
            DiscoveryCandidate(
                title="Stub YouTube",
                site_url="https://www.youtube.com/channel/UC1234567890",
                feed_url="https://www.youtube.com/channel/UC1234567890",
                suggestion_type="youtube",
                rationale="Stub YouTube candidate",
                evidence_urls=[results[0].url],
                score=0.9,
            ),
            DiscoveryCandidate(
                title="Stub Feed",
                site_url="https://example.com",
                feed_url="https://example.com/feed.xml",
                suggestion_type="atom",
                rationale="Stub feed candidate",
                evidence_urls=[results[1].url],
                score=0.7,
            ),
            DiscoveryCandidate(
                title="Stub Podcast",
                site_url="https://example.com",
                feed_url="https://example.com/podcast.xml",
                suggestion_type="podcast_rss",
                rationale="Stub podcast candidate",
                evidence_urls=[results[2].url],
                score=0.8,
            ),
        ]
    )


def _stub_candidate_validator(
    _db,
    _user_id: int,
    candidates: list[DiscoveryCandidate],
    _model_spec: str,
) -> list[DiscoveryCandidate]:
    return candidates


def _resolve_user_id(args: argparse.Namespace) -> int | None:
    if args.user_id:
        return args.user_id

    settings = get_settings()
    min_favorites = max(settings.discovery_min_favorites, 1)
    with get_db() as db:
        row = (
            db.query(ContentKnowledgeSave.user_id)
            .group_by(ContentKnowledgeSave.user_id)
            .having(func.count(ContentKnowledgeSave.id) >= min_favorites)
            .first()
        )
        return row[0] if row else None


def main() -> None:
    setup_logging()
    args = _parse_args()
    logger.info(
        "Starting discovery validation",
        extra={
            "component": "feed_discovery_validate",
            "operation": "start",
            "context_data": {"live": args.live, "user_id_arg": args.user_id},
        },
    )
    user_id = _resolve_user_id(args)
    if not user_id:
        logger.error("No eligible user found to validate discovery")
        return

    logger.info(
        "Resolved discovery user",
        extra={
            "component": "feed_discovery_validate",
            "operation": "resolve_user",
            "item_id": str(user_id),
        },
    )

    if args.live:
        logger.info(
            "Running discovery in live mode",
            extra={
                "component": "feed_discovery_validate",
                "operation": "run",
                "item_id": str(user_id),
                "context_data": {"mode": "live"},
            },
        )
        result = run_feed_discovery(user_id=user_id, trigger="manual")
    else:
        logger.info(
            "Running discovery in stub mode",
            extra={
                "component": "feed_discovery_validate",
                "operation": "run",
                "item_id": str(user_id),
                "context_data": {"mode": "stub"},
            },
        )
        deps = FeedDiscoveryDeps(
            direction_selector=lambda db, user_id: _stub_direction_selector(db, user_id),
            lane_planner=lambda db, user_id, plan: _stub_lane_planner(plan),
            candidate_extractor=lambda db, user_id, lane, results: _stub_candidate_extractor(
                lane, results
            ),
            exa_search_fn=_stub_exa_search,
            candidate_validator=lambda db, user_id, candidates, source_type: (
                _stub_candidate_validator(db, user_id, list(candidates), source_type)
            ),
        )
        result = run_feed_discovery(user_id=user_id, trigger="manual", deps=deps)

    run_metrics = None
    with get_db() as db:
        run_metrics = (
            db.query(FeedDiscoveryRun).filter(FeedDiscoveryRun.id == result.run_id).first()
        )

    logger.info(
        "Discovery validation complete | run_id=%s feeds=%s podcasts=%s youtube=%s "
        "status=%s tokens=%s duration_ms=%s",
        result.run_id,
        result.feeds,
        result.podcasts,
        result.youtube,
        result.status,
        run_metrics.token_total if run_metrics else None,
        run_metrics.duration_ms_total if run_metrics else None,
    )
    logger.info(
        "Discovery validation metrics",
        extra={
            "component": "feed_discovery_validate",
            "operation": "complete",
            "item_id": str(user_id),
            "context_data": {
                "run_id": result.run_id,
                "feeds": result.feeds,
                "podcasts": result.podcasts,
                "youtube": result.youtube,
                "status": result.status,
                "token_input": run_metrics.token_input if run_metrics else None,
                "token_output": run_metrics.token_output if run_metrics else None,
                "token_total": run_metrics.token_total if run_metrics else None,
                "duration_ms_total": run_metrics.duration_ms_total if run_metrics else None,
                "duration_ms_direction": run_metrics.duration_ms_direction if run_metrics else None,
                "duration_ms_lane": run_metrics.duration_ms_lane if run_metrics else None,
                "duration_ms_candidate_extract": run_metrics.duration_ms_candidate_extract
                if run_metrics
                else None,
                "duration_ms_candidate_validate": run_metrics.duration_ms_candidate_validate
                if run_metrics
                else None,
                "duration_ms_persist": run_metrics.duration_ms_persist if run_metrics else None,
            },
        },
    )


if __name__ == "__main__":
    main()
