"""Tests for the end-to-end news pipeline eval harness."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from app.models.metadata import NewsSummary
from app.models.news_digest_models import NewsDigestBulletDraft, NewsDigestHeaderDraft
from app.models.news_pipeline_eval_models import (
    NewsPipelineEvalCase,
    NewsPipelineEvalUserContext,
)
from app.services import news_digests
from app.services.news_pipeline_eval import load_eval_case, run_eval_case

FIXTURE_PATH = (
    Path(__file__).resolve().parents[1]
    / "evals"
    / "news_shortform"
    / "pipeline_cases"
    / "synthetic_smoke.json"
)


class _FakeSummarizer:
    def summarize(
        self,
        prompt: str,
        *,
        content_type: str,
        title: str | None = None,
        content_id: str | int | None = None,
    ) -> NewsSummary:
        del content_type, content_id
        lowered = prompt.casefold()
        if "retrieval path dropped from 1.4s to 380ms" in lowered:
            return NewsSummary(
                title="Retrieval latency drops after moving embedding refresh",
                article_url="https://x.com/i/status/x-3001",
                key_points=[
                    "Latency dropped from 1.4s to 380ms after removing embedding "
                    "refresh from the hot path."
                ],
                summary=(
                    "The team improved retrieval latency by deleting work from "
                    "the request path."
                ),
            )
        if "ai workflow accelerator" in lowered:
            return NewsSummary(
                title="Vendor announces connector expansion",
                article_url=None,
                key_points=["The post is a thin connector announcement without meaningful data."],
                summary="A vendor promoted new enterprise connectors with little concrete signal.",
            )
        if "insanely bullish" in lowered:
            return NewsSummary(
                title="Low-signal AI hype post",
                article_url="https://x.com/i/status/x-3002",
                key_points=["The post is mostly hype without a concrete update."],
                summary="A vague reaction post offered no new factual information.",
            )
        return NewsSummary(
            title=title or "Synthetic summary",
            article_url=None,
            key_points=["Synthetic key point."],
            summary="Synthetic summary body.",
        )


def _fake_curated_bullet_generator(user, clusters):  # noqa: ANN001
    del user
    curated = []
    for cluster in clusters:
        titles = [
            (item.summary_title or item.article_title or "").casefold()
            for item in cluster.items
        ]
        if any("gpt-5 mini" in title for title in titles):
            curated.append(
                news_digests.NewsDigestCuratedBulletDraft(
                    cluster=cluster,
                    draft=NewsDigestBulletDraft(
                        topic="GPT-5 Mini launch becomes the dominant AI product story",
                        details=(
                            "OpenAI's GPT-5 Mini launch stood out for lower latency, "
                            "broader availability, and practical coding use."
                        ),
                        news_item_ids=[item.id for item in cluster.items],
                    ),
                )
            )
            continue
        if any("retrieval latency drops" in title for title in titles):
            representative = cluster.items[0]
            curated.append(
                news_digests.NewsDigestCuratedBulletDraft(
                    cluster=cluster,
                    draft=NewsDigestBulletDraft(
                        topic="Engineering teams keep winning by deleting hot-path work",
                        details=(
                            "A product team cut retrieval latency sharply by moving embedding "
                            "refresh out of the request path."
                        ),
                        news_item_ids=[representative.id],
                    ),
                )
            )
    return curated, True


def _fake_header_draft_generator(_bullets):  # noqa: ANN001
    return NewsDigestHeaderDraft(
        title="AI launches and concrete engineering wins dominate the run",
        summary=(
            "The digest focused on the GPT-5 Mini launch and a high-signal "
            "retrieval latency win."
        ),
    )


def test_run_eval_case_synthetic_end_to_end(monkeypatch) -> None:
    case = load_eval_case(FIXTURE_PATH)
    monkeypatch.setattr(
        news_digests,
        "encode_news_texts",
        lambda texts: np.eye(len(texts), dtype=np.float32),
    )

    result = run_eval_case(
        case=case,
        allow_summary_generation=True,
        summarizer=_FakeSummarizer(),
        curated_bullet_generator=_fake_curated_bullet_generator,
        header_draft_generator=_fake_header_draft_generator,
    )

    cited_ids = {news_item_id for bullet in result.bullets for news_item_id in bullet.news_item_ids}

    assert result.passed is True
    assert result.failures == []
    assert result.ingest_created_count == 5
    assert result.processed_count == 5
    assert result.generated_summary_count >= 2
    assert result.generated_summary_count + result.reused_summary_count == result.processed_count
    assert result.digest_id is not None
    assert result.curated_group_count == 2
    assert result.citation_validity == 1.0
    assert len(result.bullets) == 2
    assert len(cited_ids) == 3
    assert any(len(bullet.news_item_ids) == 2 for bullet in result.bullets)


def test_run_eval_case_snapshot_skips_incomplete_items_without_summary_generation() -> None:
    case = NewsPipelineEvalCase(
        case_id="snapshot_skip_incomplete",
        description=(
            "Snapshot case that should skip incomplete items when summary generation "
            "is disabled."
        ),
        mode="snapshot",
        user=NewsPipelineEvalUserContext(
            apple_id="eval.snapshot.skip",
            email="eval.snapshot.skip@example.com",
            full_name="Snapshot Skip",
        ),
        input_mode="news_item_records",
        news_item_records=[
            {
                "visibility_scope": "global",
                "platform": "hackernews",
                "source_type": "hackernews",
                "source_label": "Hacker News",
                "source_external_id": "skip-1",
                "canonical_item_url": "https://news.ycombinator.com/item?id=skip-1",
                "canonical_story_url": "https://example.com/ready-story",
                "article_url": "https://example.com/ready-story",
                "article_title": "Ready story",
                "article_domain": "example.com",
                "discussion_url": "https://news.ycombinator.com/item?id=skip-1",
                "summary_title": "Ready story",
                "summary_key_points": ["Existing summary point."],
                "summary_text": "Existing summary body.",
                "raw_metadata": {"discussion_payload": {"compact_comments": []}},
                "status": "ready",
                "ingested_at": "2026-03-29T05:00:00Z",
            },
            {
                "visibility_scope": "global",
                "platform": "techmeme",
                "source_type": "techmeme",
                "source_label": "Techmeme",
                "source_external_id": "skip-2",
                "canonical_item_url": "https://www.techmeme.com/260329/p7",
                "canonical_story_url": "https://example.com/incomplete-story",
                "article_url": "https://example.com/incomplete-story",
                "article_title": "Incomplete story",
                "article_domain": "example.com",
                "discussion_url": "https://www.techmeme.com/260329/p7",
                "summary_title": None,
                "summary_key_points": [],
                "summary_text": None,
                "raw_metadata": {"discussion_payload": {"compact_comments": []}},
                "status": "ready",
                "ingested_at": "2026-03-29T05:10:00Z",
            },
        ],
    )

    result = run_eval_case(
        case=case,
        allow_summary_generation=False,
        summarizer=_FakeSummarizer(),
        curated_bullet_generator=lambda _user, clusters: (
            [
                news_digests.NewsDigestCuratedBulletDraft(
                    cluster=clusters[0],
                    draft=NewsDigestBulletDraft(
                        topic="Ready story remains eligible",
                        details="Only the already summarized story should reach the digest.",
                        news_item_ids=[item.id for item in clusters[0].items],
                    ),
                )
            ],
            True,
        ),
        header_draft_generator=_fake_header_draft_generator,
    )

    assert result.passed is True
    assert result.skipped_processing_count == 1
    assert result.generated_summary_count + result.reused_summary_count == 1
    assert result.digest_id is not None
    assert len(result.bullets) == 1
    assert [item.skipped for item in result.items] == [False, True]
    assert result.items[1].skipped_reason == "missing_existing_summary"
