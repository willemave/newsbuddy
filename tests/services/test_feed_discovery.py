from __future__ import annotations

from contextlib import contextmanager

from app.models.feed_discovery import (
    DiscoveryCandidate,
    DiscoveryCandidateBatch,
    DiscoveryDirection,
    DiscoveryDirectionPlan,
    DiscoveryLane,
    DiscoveryLanePlan,
    DiscoveryQuery,
)
from app.models.schema import (
    Content,
    ContentKnowledgeSave,
    FeedDiscoveryRun,
    FeedDiscoverySuggestion,
)
from app.services.exa_client import ExaSearchResult
from app.services.feed_discovery import FeedDiscoveryDeps, run_feed_discovery
from app.services.llm_usage import record_usage


class _FakeUsage:
    input_tokens = 12
    output_tokens = 8
    total_tokens = 20


class _FakeResult:
    def usage(self):
        return _FakeUsage()


def _stub_direction_selector(db_session, user_id: int) -> DiscoveryDirectionPlan:
    record_usage("direction_select", _FakeResult(), model_spec="test-model")
    rows = (
        db_session.query(ContentKnowledgeSave, Content)
        .join(Content, Content.id == ContentKnowledgeSave.content_id)
        .filter(ContentKnowledgeSave.user_id == user_id)
        .all()
    )
    ids = [content.id for _fav, content in rows]
    return DiscoveryDirectionPlan(
        summary="Stub directions",
        directions=[
            DiscoveryDirection(
                name="Primary",
                rationale="Top favorites",
                favorite_ids=ids[:2] or ids,
            ),
            DiscoveryDirection(
                name="Secondary",
                rationale="More favorites",
                favorite_ids=ids[2:] or ids,
            ),
        ],
    )


def _stub_lane_planner(
    _db_session,
    _user_id: int,
    plan: DiscoveryDirectionPlan,
) -> DiscoveryLanePlan:
    assert plan.directions
    return DiscoveryLanePlan(
        lanes=[
            DiscoveryLane(
                name="Feeds",
                goal="Find RSS feeds",
                target="feeds",
                queries=[
                    DiscoveryQuery(query="tech rss feed", rationale="Stub rationale"),
                    DiscoveryQuery(query="indie blog rss", rationale="Stub rationale"),
                ],
            ),
            DiscoveryLane(
                name="Podcasts",
                goal="Find podcasts",
                target="podcasts",
                queries=[
                    DiscoveryQuery(query="tech podcast rss", rationale="Stub rationale"),
                    DiscoveryQuery(query="product podcast", rationale="Stub rationale"),
                ],
            ),
            DiscoveryLane(
                name="YouTube",
                goal="Find YouTube channels",
                target="youtube",
                queries=[
                    DiscoveryQuery(query="ai youtube channel", rationale="Stub rationale"),
                    DiscoveryQuery(query="engineering youtube", rationale="Stub rationale"),
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
        )
    ]


def _stub_candidate_extractor(
    _db_session,
    _user_id: int,
    lane: DiscoveryLane,
    results: list[ExaSearchResult],
) -> DiscoveryCandidateBatch:
    assert lane.name
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
                evidence_urls=["https://example.com"],
                score=0.7,
            ),
            DiscoveryCandidate(
                title="Stub Podcast",
                site_url="https://example.com",
                feed_url="https://example.com/podcast.xml",
                suggestion_type="podcast_rss",
                rationale="Stub podcast candidate",
                evidence_urls=["https://example.com"],
                score=0.8,
            ),
        ]
    )


def _stub_candidate_validator(_db, _user_id, candidates, _model_spec):
    return candidates


def test_run_feed_discovery_creates_run_and_suggestions(db_session, test_user, monkeypatch):
    contents = []
    for i in range(5):
        content = Content(
            content_type="article",
            url=f"https://example.com/{i}",
            title=f"Example {i}",
            source="example.com",
            status="completed",
        )
        db_session.add(content)
        contents.append(content)
    db_session.commit()

    for content in contents:
        db_session.add(ContentKnowledgeSave(user_id=test_user.id, content_id=content.id))
    db_session.commit()

    @contextmanager
    def _override_get_db():
        yield db_session

    monkeypatch.setattr("app.services.feed_discovery.get_db", _override_get_db)

    deps = FeedDiscoveryDeps(
        direction_selector=lambda db, user_id: _stub_direction_selector(db, user_id),
        lane_planner=_stub_lane_planner,
        candidate_extractor=_stub_candidate_extractor,
        exa_search_fn=_stub_exa_search,
        candidate_validator=_stub_candidate_validator,
    )

    result = run_feed_discovery(user_id=test_user.id, deps=deps)
    assert result.status == "completed"

    runs = db_session.query(FeedDiscoveryRun).all()
    assert len(runs) == 1
    assert runs[0].token_total == 20
    assert runs[0].token_usage
    assert runs[0].duration_ms_total is not None
    assert runs[0].timing_json
    suggestions = db_session.query(FeedDiscoverySuggestion).all()
    assert len(suggestions) == 3
