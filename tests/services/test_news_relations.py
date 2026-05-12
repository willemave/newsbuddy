"""Tests for representative news-item clustering and enrichment."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

import numpy as np
import pytest

from app.core.settings import get_settings
from app.models.db import NewsItem, NewsItemReadStatus
from app.services.news_feed import count_unread_news_items
from app.services.news_relations import (
    SEMANTIC_PREFILTER_MAX_CANDIDATES,
    match_tokens_for_text,
    reconcile_news_item_relation,
)
from tests.services.news_relation_cluster_cases import PRODUCTION_CLUSTER_CASES


def _require_id(value: int | None) -> int:
    assert value is not None
    return value


def _metadata(value: object | None) -> dict[str, Any]:
    assert isinstance(value, dict)
    return cast(dict[str, Any], value)


def _news_item(
    db_session,
    *,
    ingest_key: str,
    source_external_id: str,
    title: str,
    story_url: str,
) -> NewsItem:
    item = NewsItem(
        ingest_key=ingest_key,
        visibility_scope="global",
        platform="hackernews",
        source_type="hackernews",
        source_label="Hacker News",
        source_external_id=source_external_id,
        canonical_item_url=f"https://news.ycombinator.com/item?id={source_external_id}",
        canonical_story_url=story_url,
        article_url=story_url,
        article_title=title,
        article_domain="example.com",
        discussion_url=f"https://news.ycombinator.com/item?id={source_external_id}",
        summary_title=title,
        summary_key_points=["Key point"],
        summary_text=f"{title} summary",
        raw_metadata={},
        status="ready",
        ingested_at=datetime.now(UTC).replace(tzinfo=None),
        processed_at=datetime.now(UTC).replace(tzinfo=None),
    )
    db_session.add(item)
    db_session.flush()
    return item


def _high_similarity_encode(texts: list[str]) -> np.ndarray:
    return np.ones((len(texts), 1), dtype=float)


def _uniform_similarity_encode(score: float):
    def _encode(texts: list[str]) -> np.ndarray:
        companion = max(0.0, 1.0 - score**2) ** 0.5
        rows = [[1.0, 0.0]]
        rows.extend([[score, companion] for _ in texts[1:]])
        return np.array(rows, dtype=float)

    return _encode


def _representative_first_titles(titles: list[str]) -> list[str]:
    tokenized = [match_tokens_for_text(title) for title in titles]
    best_index = 0
    best_overlap = -1
    for index, tokens in enumerate(tokenized):
        overlap = sum(
            len(tokens & other_tokens) for other_tokens in tokenized if other_tokens is not tokens
        )
        if overlap > best_overlap:
            best_index = index
            best_overlap = overlap
    if best_index == 0:
        return titles
    return [titles[best_index], *titles[:best_index], *titles[best_index + 1 :]]


def test_reconcile_news_item_relation_suppresses_exact_duplicate_and_keeps_unread_count_stable(
    db_session,
    test_user,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "app.services.news_relations.encode_news_texts",
        lambda texts: np.eye(len(texts), dtype=float),
    )

    representative = _news_item(
        db_session,
        ingest_key="rep",
        source_external_id="100",
        title="OpenAI ships new feature",
        story_url="https://example.com/story-1",
    )
    reconcile_news_item_relation(db_session, news_item_id=_require_id(representative.id))
    db_session.add(
        NewsItemReadStatus(
            user_id=_require_id(test_user.id),
            news_item_id=_require_id(representative.id),
            read_at=datetime.now(UTC).replace(tzinfo=None),
        )
    )
    db_session.commit()

    duplicate = _news_item(
        db_session,
        ingest_key="dup",
        source_external_id="101",
        title="OpenAI ships new feature again",
        story_url="https://example.com/story-1",
    )
    reconcile_news_item_relation(db_session, news_item_id=_require_id(duplicate.id))
    db_session.commit()

    db_session.refresh(representative)
    db_session.refresh(duplicate)
    assert duplicate.representative_news_item_id == representative.id
    assert representative.cluster_size == 2
    cluster = _metadata(_metadata(representative.raw_metadata)["cluster"])
    assert cluster["member_ids"] == [_require_id(representative.id), _require_id(duplicate.id)]
    assert count_unread_news_items(db_session, user_id=_require_id(test_user.id)) == 0


def test_reconcile_news_item_relation_suppresses_exact_duplicate_title_with_different_urls(
    db_session,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "app.services.news_relations.encode_news_texts",
        _high_similarity_encode,
    )

    representative = _news_item(
        db_session,
        ingest_key="latency-rep",
        source_external_id="110",
        title=(
            "Latency on our retrieval path dropped from 1.4s to 380ms after moving "
            "embedding refresh out of the request path. Biggest"
        ),
        story_url="https://x.com/i/status/925217336477287470",
    )
    reconcile_news_item_relation(db_session, news_item_id=_require_id(representative.id))

    duplicate = _news_item(
        db_session,
        ingest_key="latency-dup",
        source_external_id="111",
        title=(
            "Latency on our retrieval path dropped from 1.4s to 380ms after moving "
            "embedding refresh out of the request path. Biggest"
        ),
        story_url="https://x.com/i/status/155207873905759365",
    )
    duplicate.platform = "twitter"
    duplicate.source_label = "AI Researchers"
    reconcile_news_item_relation(db_session, news_item_id=_require_id(duplicate.id))
    db_session.commit()

    db_session.refresh(representative)
    db_session.refresh(duplicate)
    assert duplicate.representative_news_item_id == representative.id
    assert representative.cluster_size == 2


def test_reconcile_news_item_relation_ignores_blocked_placeholder_titles(
    db_session,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "app.services.news_relations.encode_news_texts",
        lambda texts: np.eye(len(texts), dtype=float),
    )

    representative = _news_item(
        db_session,
        ingest_key="wsj-placeholder-rep",
        source_external_id="120",
        title="wsj.com",
        story_url="https://www.wsj.com/tech/ai/story-1",
    )
    representative.summary_text = "Anthropic races to contain leak fallout."
    reconcile_news_item_relation(db_session, news_item_id=_require_id(representative.id))

    unrelated = _news_item(
        db_session,
        ingest_key="wsj-placeholder-unrelated",
        source_external_id="121",
        title="wsj.com",
        story_url="https://www.wsj.com/markets/story-2",
    )
    unrelated.summary_text = "Arm stock jumps after a new chip announcement."
    reconcile_news_item_relation(db_session, news_item_id=_require_id(unrelated.id))
    db_session.commit()

    db_session.refresh(representative)
    db_session.refresh(unrelated)
    assert representative.cluster_size == 1
    assert unrelated.representative_news_item_id is None


@pytest.mark.parametrize(
    "case",
    PRODUCTION_CLUSTER_CASES,
    ids=[cast(str, case["case_id"]) for case in PRODUCTION_CLUSTER_CASES],
)
def test_reconcile_news_item_relation_clusters_curated_production_families(
    db_session,
    monkeypatch,
    case: dict[str, object],
) -> None:
    monkeypatch.setattr("app.services.news_relations.encode_news_texts", _high_similarity_encode)

    titles = _representative_first_titles(cast(list[str], case["titles"]))
    label = cast(str, case["label"])

    representative = _news_item(
        db_session,
        ingest_key=f"{case['case_id']}-rep",
        source_external_id=f"{case['case_id']}-0",
        title=titles[0],
        story_url=f"https://example.com/{case['case_id']}/0",
    )
    representative.article_domain = "source0.example.com"
    representative.source_label = "Source 0"
    representative.summary_key_points = [label]
    representative.summary_text = f"{label} summary"
    reconcile_news_item_relation(db_session, news_item_id=_require_id(representative.id))

    created_ids = [_require_id(representative.id)]
    for index, title in enumerate(titles[1:], start=1):
        item = _news_item(
            db_session,
            ingest_key=f"{case['case_id']}-{index}",
            source_external_id=f"{case['case_id']}-{index}",
            title=title,
            story_url=f"https://example.com/{case['case_id']}/{index}",
        )
        item.article_domain = f"source{index}.example.com"
        item.source_label = f"Source {index}"
        item.summary_key_points = [label]
        item.summary_text = f"{label} summary"
        reconcile_news_item_relation(db_session, news_item_id=_require_id(item.id))
        created_ids.append(_require_id(item.id))

    db_session.commit()

    db_session.refresh(representative)
    assert representative.cluster_size == len(titles)

    for item_id in created_ids[1:]:
        item = db_session.get(NewsItem, item_id)
        assert item is not None
        assert item.representative_news_item_id == representative.id


def test_reconcile_news_item_relation_uses_secondary_threshold_with_lexical_guard(
    db_session,
    monkeypatch,
) -> None:
    def fake_encode(_texts: list[str]) -> np.ndarray:
        return np.array(
            [
                [1.0, 0.0],
                [0.77, 0.63],
            ],
            dtype=float,
        )

    monkeypatch.setattr("app.services.news_relations.encode_news_texts", fake_encode)

    representative = _news_item(
        db_session,
        ingest_key="rep-secondary",
        source_external_id="200",
        title="Nvidia launches Blackwell server",
        story_url="https://example.com/story-2",
    )
    reconcile_news_item_relation(db_session, news_item_id=_require_id(representative.id))

    related = _news_item(
        db_session,
        ingest_key="related-secondary",
        source_external_id="201",
        title="Blackwell server launch details",
        story_url="https://example.com/story-3",
    )
    related.article_domain = "example.com"
    reconcile_news_item_relation(db_session, news_item_id=_require_id(related.id))
    db_session.commit()

    db_session.refresh(related)
    assert related.representative_news_item_id == representative.id


def test_reconcile_news_item_relation_reranker_can_merge_below_embedding_threshold(
    db_session,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "app.services.news_relations.encode_news_texts",
        _uniform_similarity_encode(0.20),
    )
    monkeypatch.setattr(
        "app.services.news_relations._candidate_reranker_scores",
        lambda item, candidates: [0.93 for _ in candidates],
    )
    monkeypatch.setenv("NEWS_LIST_RERANKER_ENABLED", "true")
    monkeypatch.setenv("NEWS_LIST_RERANKER_SIMILARITY_THRESHOLD", "0.60")
    get_settings.cache_clear()

    try:
        representative = _news_item(
            db_session,
            ingest_key="rerank-merge-rep",
            source_external_id="210",
            title="TikTok forms U.S. joint venture with Oracle and Silver Lake",
            story_url="https://example.com/story-210",
        )
        reconcile_news_item_relation(db_session, news_item_id=_require_id(representative.id))

        related = _news_item(
            db_session,
            ingest_key="rerank-merge-related",
            source_external_id="211",
            title="US TikTok deal lets ByteDance keep algorithm and retain commercial control",
            story_url="https://example.com/story-211",
        )
        reconcile_news_item_relation(db_session, news_item_id=_require_id(related.id))
        db_session.commit()

        db_session.refresh(representative)
        db_session.refresh(related)
        assert related.representative_news_item_id == representative.id
    finally:
        get_settings.cache_clear()


def test_reconcile_news_item_relation_reranker_blocks_same_brand_false_merge(
    db_session,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "app.services.news_relations.encode_news_texts",
        _high_similarity_encode,
    )
    monkeypatch.setattr(
        "app.services.news_relations._candidate_reranker_scores",
        lambda item, candidates: [0.12 for _ in candidates],
    )
    monkeypatch.setenv("NEWS_LIST_RERANKER_ENABLED", "true")
    monkeypatch.setenv("NEWS_LIST_RERANKER_SIMILARITY_THRESHOLD", "0.60")
    get_settings.cache_clear()

    try:
        representative = _news_item(
            db_session,
            ingest_key="rerank-block-rep",
            source_external_id="220",
            title="TikTok forms U.S. joint venture with Oracle and Silver Lake",
            story_url="https://example.com/story-220",
        )
        reconcile_news_item_relation(db_session, news_item_id=_require_id(representative.id))

        unrelated = _news_item(
            db_session,
            ingest_key="rerank-block-unrelated",
            source_external_id="221",
            title="TikTok privacy update collects precise location and AI prompts",
            story_url="https://example.com/story-221",
        )
        reconcile_news_item_relation(db_session, news_item_id=_require_id(unrelated.id))
        db_session.commit()

        db_session.refresh(representative)
        db_session.refresh(unrelated)
        assert representative.cluster_size == 1
        assert unrelated.representative_news_item_id is None
    finally:
        get_settings.cache_clear()


def test_reconcile_news_item_relation_clusters_project_glasswing_launch_family(
    db_session,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "app.services.news_relations.encode_news_texts",
        _uniform_similarity_encode(0.77),
    )

    representative = _news_item(
        db_session,
        ingest_key="glasswing-rep",
        source_external_id="710",
        title=(
            "Anthropic announces Project Glasswing, a cybersecurity initiative that "
            "will use its Claude Mythos Preview model to help find and fix software vulnerabilities"
        ),
        story_url="https://anthropic.com/glasswing-launch-overview",
    )
    representative.article_domain = "anthropic.com"
    representative.source_label = "anthropic.com"
    representative.summary_key_points = [
        "Anthropic launches Project Glasswing",
        "Targets software vulnerability discovery and remediation",
    ]
    representative.summary_text = (
        "Anthropic launched Project Glasswing for AI-era software security."
    )
    reconcile_news_item_relation(db_session, news_item_id=_require_id(representative.id))

    partners = _news_item(
        db_session,
        ingest_key="glasswing-partners",
        source_external_id="711",
        title=(
            "Anthropic's Project Glasswing launch partners include AWS, Apple, Broadcom, "
            "Cisco, CrowdStrike, Google, Microsoft, Nvidia, and Palo Alto Networks"
        ),
        story_url="https://www.zdnet.com/article/project-glasswing-microsoft-google-apple-anthropic",
    )
    partners.article_domain = "zdnet.com"
    partners.source_label = "zdnet.com"
    partners.summary_key_points = [
        "Project Glasswing launches with major security and cloud partners",
    ]
    partners.summary_text = "Coverage of Anthropic's Project Glasswing launch partner roster."
    reconcile_news_item_relation(db_session, news_item_id=_require_id(partners.id))

    funding = _news_item(
        db_session,
        ingest_key="glasswing-funding",
        source_external_id="712",
        title=(
            "Anthropic commits up to $100M in usage credits for Project Glasswing, "
            "along with $4M in direct donations to open-source security organizations"
        ),
        story_url="https://cyberscoop.com/project-glasswing-anthropic-ai-open-source-software-vulnerabilities",
    )
    funding.article_domain = "cyberscoop.com"
    funding.source_label = "cyberscoop.com"
    funding.summary_key_points = [
        "Project Glasswing includes credits and direct funding for open-source security",
    ]
    funding.summary_text = (
        "Project Glasswing includes major credits and donations for security work."
    )
    reconcile_news_item_relation(db_session, news_item_id=_require_id(funding.id))
    db_session.commit()

    db_session.refresh(representative)
    db_session.refresh(partners)
    db_session.refresh(funding)
    assert partners.representative_news_item_id == representative.id
    assert funding.representative_news_item_id == representative.id
    assert representative.cluster_size == 3


def test_reconcile_news_item_relation_matches_against_related_cluster_titles(
    db_session,
    monkeypatch,
) -> None:
    def fake_encode(texts: list[str]) -> np.ndarray:
        first = texts[0]
        if first.startswith("Title: Arm Stock Jumps"):
            rows = [[1.0, 0.0]]
            for text in texts[1:]:
                score = 0.84 if "136-Core AGI CPU" in text else 0.68
                companion = max(0.0, 1.0 - score**2) ** 0.5
                rows.append([score, companion])
            return np.array(rows, dtype=float)
        if first.startswith("Title: "):
            return _uniform_similarity_encode(0.88)(texts)
        if first.startswith("Key points:"):
            return _uniform_similarity_encode(0.82)(texts)
        if first.startswith("Domain: "):
            return _uniform_similarity_encode(0.75)(texts)
        raise AssertionError(f"Unexpected texts: {texts}")

    monkeypatch.setattr("app.services.news_relations.encode_news_texts", fake_encode)

    representative = _news_item(
        db_session,
        ingest_key="arm-rep",
        source_external_id="720",
        title="Arm Debuts 'AGI CPU' Silicon with 136 Cores for AI Infrastructure",
        story_url="https://example.com/arm/0",
    )
    representative.summary_key_points = ["Arm launches AGI CPU for data centers"]
    representative.summary_text = "Arm launches AGI CPU for data centers summary"
    reconcile_news_item_relation(db_session, news_item_id=_require_id(representative.id))

    for index, title in enumerate(
        [
            "Arm Launches First In-House 'AGI CPU' for Agentic AI Infrastructure",
            "Arm Debuts First Proprietary AGI CPU for Data Centers with Up to 136 Cores",
            "Arm Enters Silicon Market with 136-Core AGI CPU for AI Infrastructure",
        ],
        start=1,
    ):
        item = _news_item(
            db_session,
            ingest_key=f"arm-variant-{index}",
            source_external_id=f"72{index}",
            title=title,
            story_url=f"https://example.com/arm/{index}",
        )
        item.summary_key_points = ["Arm launches AGI CPU for data centers"]
        item.summary_text = "Arm launches AGI CPU for data centers summary"
        reconcile_news_item_relation(db_session, news_item_id=_require_id(item.id))

    stock_reaction = _news_item(
        db_session,
        ingest_key="arm-stock",
        source_external_id="724",
        title="Arm Stock Jumps 6% as CEO Targets $25B in Revenue by 2031 with New In-House Chip",
        story_url="https://example.com/arm/4",
    )
    stock_reaction.summary_key_points = ["Arm launches AGI CPU for data centers"]
    stock_reaction.summary_text = "Arm launches AGI CPU for data centers summary"
    reconcile_news_item_relation(db_session, news_item_id=_require_id(stock_reaction.id))
    db_session.commit()

    db_session.refresh(representative)
    db_session.refresh(stock_reaction)
    assert stock_reaction.representative_news_item_id == representative.id
    assert representative.cluster_size == 5
    assert "Arm Debuts 'AGI CPU' Silicon with 136 Cores for AI Infrastructure" in cast(
        list[str],
        _metadata(_metadata(representative.raw_metadata)["cluster"])["related_titles"],
    )


def test_reconcile_news_item_relation_uses_multiview_primary_score(
    db_session,
    monkeypatch,
) -> None:
    def fake_encode(texts: list[str]) -> np.ndarray:
        first = texts[0]
        if first.startswith("Title: "):
            return np.array(
                [
                    [1.0, 0.0],
                    [0.88, 0.48],
                ],
                dtype=float,
            )
        if first.startswith("Key points:"):
            return np.array(
                [
                    [1.0, 0.0],
                    [0.84, 0.54],
                ],
                dtype=float,
            )
        if first.startswith("Domain: "):
            return np.array(
                [
                    [1.0, 0.0],
                    [0.74, 0.67],
                ],
                dtype=float,
            )
        raise AssertionError(f"Unexpected texts: {texts}")

    monkeypatch.setattr("app.services.news_relations.encode_news_texts", fake_encode)

    representative = _news_item(
        db_session,
        ingest_key="rep-primary",
        source_external_id="300",
        title="OpenAI ships new coding agent",
        story_url="https://example.com/story-300",
    )
    representative.summary_key_points = ["Launches a coding agent", "Targets code review"]
    representative.summary_text = "OpenAI launched a coding agent for code review."
    reconcile_news_item_relation(db_session, news_item_id=_require_id(representative.id))

    related = _news_item(
        db_session,
        ingest_key="related-primary",
        source_external_id="301",
        title="Coding agent launch expands to code review",
        story_url="https://example.com/story-301",
    )
    related.article_domain = "techmeme.com"
    related.source_label = "Techmeme"
    related.summary_key_points = ["Launches a coding agent", "Targets code review"]
    related.summary_text = "Coverage of the coding agent release for code review workflows."
    reconcile_news_item_relation(db_session, news_item_id=_require_id(related.id))
    db_session.commit()

    db_session.refresh(related)
    assert related.representative_news_item_id == representative.id


def test_reconcile_news_item_relation_clusters_claude_code_leak_family(
    db_session,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "app.services.news_relations.encode_news_texts",
        _uniform_similarity_encode(0.77),
    )

    representative = _news_item(
        db_session,
        ingest_key="claude-leak-rep",
        source_external_id="810",
        title="The Claude Code Leak",
        story_url="https://build.ms/2026/4/1/the-claude-code-leak",
    )
    representative.article_domain = "build.ms"
    representative.source_label = "build.ms"
    representative.summary_key_points = ["Claude Code leak becomes a major news cycle"]
    representative.summary_text = (
        "The Claude Code leak kicked off a wider story around internal features."
    )
    reconcile_news_item_relation(db_session, news_item_id=_require_id(representative.id))

    kairos = _news_item(
        db_session,
        ingest_key="claude-leak-kairos",
        source_external_id="811",
        title=(
            'Anthropic\'s Claude Code leak reveals its "Kairos" updates, including '
            'letting Claude work in the background and using a "dream mode" to '
            "consolidate its memories"
        ),
        story_url="https://www.theinformation.com/articles/claude-code-leak-reveals-always-kairos-agent",
    )
    kairos.article_domain = "theinformation.com"
    kairos.source_label = "theinformation.com"
    kairos.summary_key_points = ["Leak reveals Claude Code Kairos and dream mode details"]
    kairos.summary_text = (
        "Follow-up reporting on the Claude Code leak and its internal roadmap details."
    )
    reconcile_news_item_relation(db_session, news_item_id=_require_id(kairos.id))

    fallout = _news_item(
        db_session,
        ingest_key="claude-leak-fallout",
        source_external_id="812",
        title=(
            "Anthropic is racing to contain the fallout after accidentally leaking "
            "Claude Code's source code, issuing a copyright takedown request to "
            "remove 8,000+ copies"
        ),
        story_url="https://www.wsj.com/tech/ai/anthropic-races-to-contain-leak-of-code-behind-claude-ai-agent",
    )
    fallout.article_domain = "wsj.com"
    fallout.source_label = "wsj.com"
    fallout.summary_key_points = ["Leak fallout drives takedown efforts and broader coverage"]
    fallout.summary_text = "Coverage of the operational fallout from the Claude Code leak."
    reconcile_news_item_relation(db_session, news_item_id=_require_id(fallout.id))
    db_session.commit()

    db_session.refresh(representative)
    db_session.refresh(kairos)
    db_session.refresh(fallout)
    assert kairos.representative_news_item_id == representative.id
    assert fallout.representative_news_item_id == representative.id
    assert representative.cluster_size == 3


def test_reconcile_news_item_relation_rejects_topical_neighbor_under_multiview_scoring(
    db_session,
    monkeypatch,
) -> None:
    def fake_encode(texts: list[str]) -> np.ndarray:
        first = texts[0]
        if first.startswith("Title: "):
            return np.array(
                [
                    [1.0, 0.0],
                    [0.79, 0.61],
                ],
                dtype=float,
            )
        if first.startswith("Key points:"):
            return np.array(
                [
                    [1.0, 0.0],
                    [0.32, 0.95],
                ],
                dtype=float,
            )
        if first.startswith("Domain: "):
            return np.array(
                [
                    [1.0, 0.0],
                    [0.58, 0.81],
                ],
                dtype=float,
            )
        raise AssertionError(f"Unexpected texts: {texts}")

    monkeypatch.setattr("app.services.news_relations.encode_news_texts", fake_encode)

    representative = _news_item(
        db_session,
        ingest_key="rep-negative",
        source_external_id="400",
        title="OpenAI launches Codex plugin for Claude Code",
        story_url="https://example.com/story-400",
    )
    representative.article_domain = "x.com"
    representative.source_label = "X Following"
    representative.summary_key_points = ["Codex plugin launches for Claude Code"]
    representative.summary_text = "OpenAI launched a Codex plugin inside Claude Code."
    reconcile_news_item_relation(db_session, news_item_id=_require_id(representative.id))

    adjacent = _news_item(
        db_session,
        ingest_key="adjacent-negative",
        source_external_id="401",
        title="Analytics dashboard launches for Claude Code teams",
        story_url="https://example.com/story-401",
    )
    adjacent.article_domain = "github.com"
    adjacent.source_label = "Show HN"
    adjacent.summary_key_points = ["Dashboard product for Claude Code teams"]
    adjacent.summary_text = "A separate analytics dashboard for Claude Code engineering teams."
    reconcile_news_item_relation(db_session, news_item_id=_require_id(adjacent.id))
    db_session.commit()

    db_session.refresh(adjacent)
    assert adjacent.representative_news_item_id is None


def test_reconcile_news_item_relation_skips_embeddings_without_title_overlap(
    db_session,
    monkeypatch,
) -> None:
    calls: list[list[str]] = []

    def fake_encode(texts: list[str]) -> np.ndarray:
        calls.append(texts)
        return np.eye(len(texts), dtype=float)

    monkeypatch.setattr("app.services.news_relations.encode_news_texts", fake_encode)

    _news_item(
        db_session,
        ingest_key="rep-unrelated",
        source_external_id="500",
        title="OpenAI ships new coding agent",
        story_url="https://example.com/story-500",
    )
    unrelated = _news_item(
        db_session,
        ingest_key="unrelated",
        source_external_id="501",
        title="Mediterranean cooking guide for spring dinners",
        story_url="https://example.com/story-501",
    )

    reconcile_news_item_relation(db_session, news_item_id=_require_id(unrelated.id))
    db_session.commit()

    db_session.refresh(unrelated)
    assert unrelated.representative_news_item_id is None
    assert calls == []


def test_reconcile_news_item_relation_caps_semantic_prefilter_candidates(
    db_session,
    monkeypatch,
) -> None:
    call_lengths: list[int] = []

    def fake_encode(texts: list[str]) -> np.ndarray:
        call_lengths.append(len(texts))
        return np.eye(len(texts), dtype=float)

    monkeypatch.setattr("app.services.news_relations.encode_news_texts", fake_encode)

    for index in range(20):
        _news_item(
            db_session,
            ingest_key=f"rep-cap-{index}",
            source_external_id=f"6{index:02d}",
            title=f"OpenAI coding agent launch report {index}",
            story_url=f"https://example.com/story-cap-{index}",
        )

    related = _news_item(
        db_session,
        ingest_key="related-cap",
        source_external_id="699",
        title="OpenAI coding agent launch follow-up",
        story_url="https://example.com/story-cap-related",
    )

    reconcile_news_item_relation(db_session, news_item_id=_require_id(related.id))

    assert call_lengths
    assert max(call_lengths) == SEMANTIC_PREFILTER_MAX_CANDIDATES + 1
