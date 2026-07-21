from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.core.settings import get_settings
from app.models.contracts import ContentClassification, ContentType, TaskType
from app.models.db import (
    AudioEpisode,
    BriefingLens,
    BriefingSegment,
    BriefingState,
    NewsItemReadStatus,
    ProcessingTask,
    VendorUsageRecord,
)
from app.models.db.users import User
from app.services.briefing.first_run import complete_first_edition, start_first_edition
from app.services.briefing.presentation import (
    InvalidBriefingLensCursor,
    _active_segments,
    get_briefing_index_validator,
    get_briefing_lens,
)
from app.services.briefing.refresh import run_briefing_refresh
from app.services.exa_client import ExaSearchResult


def test_briefing_index_honors_etag(
    client: TestClient,
    db_session: Session,
    test_user: User,
    content_factory,
    status_entry_factory,
    monkeypatch,
) -> None:
    settings = get_settings()
    assert test_user.id is not None
    user_id = test_user.id
    monkeypatch.setattr(settings, "briefing_enabled_user_ids", [user_id])
    _seed_content_edition(
        db_session,
        test_user,
        content_factory=content_factory,
        status_entry_factory=status_entry_factory,
        settings=settings,
    )

    response = client.get("/api/briefing")

    assert response.status_code == 200
    payload = response.json()
    assert payload["version"] == 1
    assert payload["lenses"][1]["key"] == "articles"
    etag = response.headers["etag"]
    assert response.headers["cache-control"] == "private, no-cache"
    assert "Authorization" in response.headers["vary"].split(", ")

    def fail_full_index(*_args, **_kwargs):
        raise AssertionError("matching ETag must not build the full Briefing index")

    monkeypatch.setattr("app.routers.api.briefing.get_briefing_index", fail_full_index)

    not_modified = client.get("/api/briefing", headers={"If-None-Match": etag})

    assert not_modified.status_code == 304
    assert not_modified.headers["etag"] == etag
    assert not_modified.headers["cache-control"] == "private, no-cache"
    assert "Authorization" in not_modified.headers["vary"].split(", ")


def test_briefing_index_validator_does_not_create_missing_state(
    client: TestClient,
    db_session: Session,
    test_user: User,
) -> None:
    from app.routers.api.briefing import _briefing_etag

    assert test_user.id is not None
    assert db_session.get(BriefingState, test_user.id) is None
    etag = _briefing_etag(user_id=test_user.id, version=0)

    response = client.get("/api/briefing", headers={"If-None-Match": etag})

    assert response.status_code == 304
    assert db_session.get(BriefingState, test_user.id) is None


def test_briefing_index_validator_matches_active_first_run(
    db_session: Session,
    test_user: User,
) -> None:
    assert test_user.id is not None
    run = start_first_edition(db_session, user_id=test_user.id)
    db_session.commit()

    validator = get_briefing_index_validator(db_session, user_id=test_user.id)

    assert validator.version == 0
    assert validator.first_run_id == run.id
    assert validator.first_run_revision == run.revision
    assert db_session.get(BriefingState, test_user.id) is None


def test_briefing_index_validator_excludes_completed_first_run(
    db_session: Session,
    test_user: User,
) -> None:
    assert test_user.id is not None
    start_first_edition(db_session, user_id=test_user.id)
    assert complete_first_edition(db_session, user_id=test_user.id) is True
    db_session.commit()

    validator = get_briefing_index_validator(db_session, user_id=test_user.id)

    assert validator.version == 0
    assert validator.first_run_id == 0
    assert validator.first_run_revision == 0
    assert db_session.get(BriefingState, test_user.id) is None


def test_briefing_index_segment_query_projects_only_summary_fields(
    db_session: Session,
    test_user: User,
) -> None:
    assert test_user.id is not None
    statements: list[str] = []

    def capture_statement(
        _connection,
        _cursor,
        statement: str,
        _parameters,
        _context,
        _executemany,
    ) -> None:
        statements.append(statement)

    bind = db_session.get_bind()
    event.listen(bind, "before_cursor_execute", capture_statement)
    try:
        assert _active_segments(db_session, user_id=test_user.id) == []
    finally:
        event.remove(bind, "before_cursor_execute", capture_statement)

    query = next(statement for statement in statements if "briefing_segments" in statement)
    assert "briefing_segments.lens_id" in query
    assert "briefing_segments.source_keys" in query
    assert "briefing_segments.created_at" in query
    assert "briefing_segments.blocks" not in query
    assert "briefing_segments.markdown_raw" not in query
    assert "briefing_segments.narration_text" not in query


def test_briefing_etag_is_scoped_to_authenticated_user(
    client_factory,
    user_factory,
) -> None:
    first_user = user_factory(email="briefing-etag-one@example.com")
    second_user = user_factory(email="briefing-etag-two@example.com")

    with client_factory(user=first_user) as first_client:
        first_response = first_client.get("/api/briefing")
    with client_factory(user=second_user) as second_client:
        second_response = second_client.get(
            "/api/briefing",
            headers={"If-None-Match": first_response.headers["etag"]},
        )

    assert first_response.json()["version"] == second_response.json()["version"] == 0
    assert first_response.headers["etag"] != second_response.headers["etag"]
    assert second_response.status_code == 200


def test_briefing_etag_changes_when_first_run_is_replaced(
    client: TestClient,
    db_session: Session,
    test_user: User,
) -> None:
    assert test_user.id is not None
    first_run = start_first_edition(db_session, user_id=test_user.id)
    db_session.commit()

    first_response = client.get("/api/briefing")

    assert first_response.status_code == 200
    assert first_response.json()["first_run"]["run_id"] == first_run.id

    replacement_run = start_first_edition(db_session, user_id=test_user.id)
    db_session.commit()

    replacement_response = client.get(
        "/api/briefing",
        headers={"If-None-Match": first_response.headers["etag"]},
    )

    assert replacement_response.status_code == 200
    assert replacement_response.json()["first_run"]["run_id"] == replacement_run.id
    assert replacement_run.id != first_run.id
    assert replacement_response.headers["etag"] != first_response.headers["etag"]


def test_briefing_refresh_endpoint_enqueues_append_task(
    client: TestClient,
    db_session: Session,
    test_user: User,
    monkeypatch,
) -> None:
    settings = get_settings()
    assert test_user.id is not None
    user_id = test_user.id
    monkeypatch.setattr(settings, "briefing_enabled_user_ids", [user_id])

    response = client.post("/api/briefing/refresh")

    assert response.status_code == 200
    assert response.json() == {"enqueued": True, "version": 0}
    task = (
        db_session.query(ProcessingTask)
        .filter(ProcessingTask.task_type == TaskType.BRIEFING_REFRESH.value)
        .one()
    )
    assert task.dedupe_key == f"briefing_refresh:{user_id}:append"
    assert task.payload == {"user_id": user_id, "mode": "append"}


def test_briefing_lens_and_narration_endpoints_reuse_episode(
    client: TestClient,
    db_session: Session,
    test_user: User,
    content_factory,
    status_entry_factory,
    monkeypatch,
) -> None:
    settings = get_settings()
    assert test_user.id is not None
    user_id = test_user.id
    monkeypatch.setattr(settings, "briefing_enabled_user_ids", [user_id])
    _seed_content_edition(
        db_session,
        test_user,
        content_factory=content_factory,
        status_entry_factory=status_entry_factory,
        settings=settings,
    )

    lens_response = client.get("/api/briefing/lenses/articles")

    assert lens_response.status_code == 200
    lens_payload = lens_response.json()
    assert lens_payload["lens"]["key"] == "articles"
    assert len(lens_payload["segments"]) == 1
    assert len(lens_payload["sources"]) == 3
    assert lens_payload["next_cursor"] is None
    assert lens_payload["has_more"] is False

    first = client.post("/api/briefing/narrations", json={"lens_key": "articles"})
    second = client.post("/api/briefing/narrations", json={"lens_key": "articles"})

    assert first.status_code == 200
    assert second.status_code == 200
    first_payload = first.json()
    second_payload = second.json()
    assert first_payload["episode_group_id"] == second_payload["episode_group_id"]
    assert first_payload["chapters"][0]["id"] == second_payload["chapters"][0]["id"]
    assert first_payload["playable"] is False
    assert first_payload["status"] == "pending"
    assert len(first_payload["chapters"]) == 1
    episode = (
        db_session.query(AudioEpisode)
        .filter(AudioEpisode.episode_group_id == first_payload["episode_group_id"])
        .one()
    )
    assert episode.kind == "briefing_narration"
    assert episode.episode_group_id == first_payload["episode_group_id"]
    assert episode.chapter_index == 0
    assert episode.source_snapshot is not None
    assert episode.source_snapshot["read_on_play"]["content_ids"]

    status_response = client.get(f"/api/briefing/narrations/{first_payload['episode_group_id']}")

    assert status_response.status_code == 200
    assert status_response.json()["episode_group_id"] == first_payload["episode_group_id"]
    assert status_response.json()["chapters"][0]["id"] == episode.id

    legacy_response = client.post(
        "/api/briefing/narration?delivery=stream",
        json={"lens_key": "articles"},
    )

    assert legacy_response.status_code == 200
    assert "id" in legacy_response.json()
    assert "chapters" not in legacy_response.json()
    legacy_episode = (
        db_session.query(AudioEpisode).filter(AudioEpisode.episode_group_id.is_(None)).one()
    )
    assert legacy_response.json()["id"] == legacy_episode.id
    assert legacy_episode.chapter_index is None


def test_briefing_narration_manifest_is_scoped_to_owner(
    client: TestClient,
    client_factory,
    db_session: Session,
    test_user: User,
    user_factory,
) -> None:
    episode = AudioEpisode(
        user_id=test_user.id,
        kind="briefing_narration",
        status="completed",
        title="Articles briefing — Chapter 1",
        input_hash="owner-scoped-chapter",
        episode_group_id="a" * 64,
        chapter_index=0,
        source_item_ids=[],
        source_snapshot={
            "lens_key": "articles",
            "lens_title": "Articles",
            "source_count": 0,
            "source_keys": [],
        },
        prompt_version=3,
        duration_seconds=300,
    )
    db_session.add(episode)
    db_session.commit()

    owner_response = client.get(f"/api/briefing/narrations/{episode.episode_group_id}")
    other_user = user_factory(email="other-briefing-listener@example.com")
    with client_factory(user=other_user) as other_client:
        other_response = other_client.get(f"/api/briefing/narrations/{episode.episode_group_id}")

    assert owner_response.status_code == 200
    assert other_response.status_code == 404


def test_briefing_lens_pagination_matches_legacy_response(
    client: TestClient,
    db_session: Session,
    test_user: User,
    content_factory,
    status_entry_factory,
    monkeypatch,
) -> None:
    settings = get_settings()
    assert test_user.id is not None
    monkeypatch.setattr(settings, "briefing_enabled_user_ids", [test_user.id])
    _seed_content_edition(
        db_session,
        test_user,
        content_factory=content_factory,
        status_entry_factory=status_entry_factory,
        settings=settings,
        count=13,
    )

    legacy = client.get("/api/briefing/lenses/articles").json()
    cursor = None
    paged_segments: list[dict] = []
    paged_sources: dict[str, dict] = {}
    while True:
        params = {"limit": 1}
        if cursor is not None:
            params["cursor"] = cursor
        response = client.get("/api/briefing/lenses/articles", params=params)
        assert response.status_code == 200
        page = response.json()
        assert page["version"] == legacy["version"]
        assert page["lens"] == legacy["lens"]
        page_segment_keys = {
            source_key for segment in page["segments"] for source_key in segment["source_keys"]
        }
        assert {source["source_key"] for source in page["sources"]} == page_segment_keys
        paged_segments.extend(page["segments"])
        paged_sources.update({source["source_key"]: source for source in page["sources"]})
        cursor = page["next_cursor"]
        if not page["has_more"]:
            assert cursor is None
            break
        assert cursor is not None

    assert paged_segments == legacy["segments"]
    assert paged_sources == {source["source_key"]: source for source in legacy["sources"]}


def test_briefing_lens_pagination_orders_equal_timestamps_by_descending_id(
    client: TestClient,
    db_session: Session,
    test_user: User,
    content_factory,
    status_entry_factory,
    monkeypatch,
) -> None:
    settings = get_settings()
    assert test_user.id is not None
    monkeypatch.setattr(settings, "briefing_enabled_user_ids", [test_user.id])
    _seed_content_edition(
        db_session,
        test_user,
        content_factory=content_factory,
        status_entry_factory=status_entry_factory,
        settings=settings,
        count=13,
    )
    segments = (
        db_session.query(BriefingSegment).filter(BriefingSegment.user_id == test_user.id).all()
    )
    tied_at = datetime(2026, 7, 13, 12, 0, 0)
    for segment in segments:
        segment.created_at = tied_at
    db_session.commit()

    ids: list[int] = []
    cursor = None
    while True:
        params: dict[str, int | str] = {"limit": 1}
        if cursor:
            params["cursor"] = cursor
        response = client.get("/api/briefing/lenses/articles", params=params)
        assert response.status_code == 200
        payload = response.json()
        ids.extend(segment["id"] for segment in payload["segments"])
        cursor = payload["next_cursor"]
        if not payload["has_more"]:
            break

    assert ids == sorted(ids, reverse=True)
    assert len(ids) == len(set(ids)) == len(segments)


def test_briefing_lens_pagination_handles_empty_and_single_segment_lenses(
    client: TestClient,
    db_session: Session,
    test_user: User,
    content_factory,
    status_entry_factory,
    monkeypatch,
) -> None:
    settings = get_settings()
    assert test_user.id is not None
    monkeypatch.setattr(settings, "briefing_enabled_user_ids", [test_user.id])
    _seed_content_edition(
        db_session,
        test_user,
        content_factory=content_factory,
        status_entry_factory=status_entry_factory,
        settings=settings,
        count=3,
    )

    empty = client.get("/api/briefing/lenses/podcasts", params={"limit": 12})
    single = client.get("/api/briefing/lenses/articles", params={"limit": 12})

    assert empty.status_code == 200
    assert empty.json()["segments"] == []
    assert empty.json()["sources"] == []
    assert empty.json()["has_more"] is False
    assert empty.json()["next_cursor"] is None
    assert single.status_code == 200
    assert len(single.json()["segments"]) == 1
    assert single.json()["has_more"] is False
    assert single.json()["next_cursor"] is None


def test_briefing_bounded_page_has_constant_queries_and_payload_ceiling(
    client: TestClient,
    db_session: Session,
    test_user: User,
    content_factory,
    status_entry_factory,
    monkeypatch,
) -> None:
    settings = get_settings()
    assert test_user.id is not None
    monkeypatch.setattr(settings, "briefing_enabled_user_ids", [test_user.id])
    bind = db_session.get_bind()

    def fetch_with_statements() -> tuple[Response, list[str]]:
        statements: list[str] = []

        def capture_statement(
            _connection,
            _cursor,
            statement: str,
            _parameters,
            _context,
            _executemany,
        ) -> None:
            statements.append(statement)

        event.listen(bind, "before_cursor_execute", capture_statement)
        try:
            response = client.get("/api/briefing/lenses/articles", params={"limit": 12})
        finally:
            event.remove(bind, "before_cursor_execute", capture_statement)
        return response, statements

    _seed_content_edition(
        db_session,
        test_user,
        content_factory=content_factory,
        status_entry_factory=status_entry_factory,
        settings=settings,
        count=3,
    )
    small_response, small_statements = fetch_with_statements()
    _seed_content_edition(
        db_session,
        test_user,
        content_factory=content_factory,
        status_entry_factory=status_entry_factory,
        settings=settings,
        count=77,
    )
    response, statements = fetch_with_statements()

    assert small_response.status_code == 200
    assert response.status_code == 200
    assert len(response.json()["segments"]) == 12
    assert len(response.content) < 300_000
    assert len(statements) == len(small_statements)
    content_query = next(statement for statement in statements if "FROM contents" in statement)
    assert "contents.search_text" not in content_query
    assert "contents.error_message" not in content_query
    segment_body_query = next(
        statement
        for statement in statements
        if "FROM briefing_segments" in statement and "briefing_segments.blocks" in statement
    )
    assert "briefing_segments.markdown_raw" not in segment_body_query
    assert "briefing_segments.model" not in segment_body_query
    lens_query = next(statement for statement in statements if "FROM briefing_lenses" in statement)
    assert "briefing_lenses.centroid" not in lens_query
    assert "briefing_lenses.routing_rule" not in lens_query


def test_briefing_lens_rejects_invalid_and_cross_lens_cursors(
    client: TestClient,
    db_session: Session,
    test_user: User,
    content_factory,
    status_entry_factory,
    monkeypatch,
) -> None:
    settings = get_settings()
    assert test_user.id is not None
    monkeypatch.setattr(settings, "briefing_enabled_user_ids", [test_user.id])
    _seed_content_edition(
        db_session,
        test_user,
        content_factory=content_factory,
        status_entry_factory=status_entry_factory,
        settings=settings,
        count=13,
    )
    with pytest.raises(InvalidBriefingLensCursor, match="limit is out of range"):
        get_briefing_lens(
            db_session,
            user_id=test_user.id,
            lens_key="articles",
            limit=0,
        )
    first = client.get("/api/briefing/lenses/articles", params={"limit": 1}).json()
    assert first["next_cursor"] is not None

    malformed = client.get(
        "/api/briefing/lenses/articles",
        params={"limit": 1, "cursor": "not-a-cursor"},
    )
    cross_lens = client.get(
        "/api/briefing/lenses/podcasts",
        params={"limit": 1, "cursor": first["next_cursor"]},
    )

    assert malformed.status_code == 400
    assert cross_lens.status_code == 400

    anchor_id = first["segments"][0]["id"]
    db_session.query(BriefingSegment).filter(BriefingSegment.id == anchor_id).update(
        {BriefingSegment.status: "compacted"},
        synchronize_session=False,
    )
    db_session.commit()
    stale = client.get(
        "/api/briefing/lenses/articles",
        params={"limit": 1, "cursor": first["next_cursor"]},
    )
    assert stale.status_code == 409


def test_briefing_read_mark_response_reports_retirement_count(
    client: TestClient,
    db_session: Session,
    test_user: User,
    content_factory,
    status_entry_factory,
    monkeypatch,
) -> None:
    settings = get_settings()
    assert test_user.id is not None
    monkeypatch.setattr(settings, "briefing_enabled_user_ids", [test_user.id])
    _seed_content_edition(
        db_session,
        test_user,
        content_factory=content_factory,
        status_entry_factory=status_entry_factory,
        settings=settings,
    )
    lens = client.get("/api/briefing/lenses/articles").json()

    response = client.post(
        "/api/briefing/read-marks",
        json={"source_keys": [lens["sources"][0]["source_key"]]},
    )

    assert response.status_code == 200
    assert response.json() == {"marked": 1, "retired": 0, "version": 2}


def test_briefing_lens_read_marks_every_source_in_the_category(
    client: TestClient,
    db_session: Session,
    test_user: User,
    news_item_factory,
) -> None:
    assert test_user.id is not None
    user_id = test_user.id
    first = news_item_factory(
        article_title="First category story",
        visibility_scope="user",
        owner_user_id=user_id,
    )
    second = news_item_factory(
        article_title="Second category story",
        visibility_scope="user",
        owner_user_id=user_id,
    )
    untouched = news_item_factory(
        article_title="Other category story",
        visibility_scope="user",
        owner_user_id=user_id,
    )
    category = BriefingLens(
        user_id=user_id,
        key="technology",
        tier="news",
        title="Technology",
        deck="Technology updates",
        position=0,
        status="active",
    )
    other_category = BriefingLens(
        user_id=user_id,
        key="science",
        tier="news",
        title="Science",
        deck="Science updates",
        position=1,
        status="active",
    )
    db_session.add_all([category, other_category])
    db_session.flush()
    category_segment = BriefingSegment(
        lens_id=category.id,
        user_id=user_id,
        blocks=[],
        source_keys=[f"news:{first.id}", f"news:{second.id}"],
        status="active",
        model="test",
        prompt_version="test",
    )
    other_segment = BriefingSegment(
        lens_id=other_category.id,
        user_id=user_id,
        blocks=[],
        source_keys=[f"news:{untouched.id}"],
        status="active",
        model="test",
        prompt_version="test",
    )
    db_session.add_all(
        [
            category_segment,
            other_segment,
            BriefingState(
                user_id=user_id,
                version=4,
                masthead_title="The Unread Times",
                masthead_deck="Existing edition",
            ),
        ]
    )
    db_session.commit()

    response = client.post("/api/briefing/lenses/technology/read-marks")
    db_session.refresh(category_segment)
    db_session.refresh(other_segment)
    read_news_item_ids = set(
        db_session.execute(
            select(NewsItemReadStatus.news_item_id).where(NewsItemReadStatus.user_id == user_id)
        ).scalars()
    )

    assert response.status_code == 200
    assert response.json() == {"marked": 2, "retired": 1, "version": 5}
    assert read_news_item_ids == {first.id, second.id}
    assert category_segment.status == "retired"
    assert other_segment.status == "active"


def test_briefing_lens_read_returns_not_found_for_an_unknown_category(
    client: TestClient,
) -> None:
    response = client.post("/api/briefing/lenses/missing/read-marks")

    assert response.status_code == 404
    assert response.json() == {"detail": "Briefing lens not found"}


def test_briefing_dig_endpoints_are_mockable_and_rate_limited(
    client: TestClient,
    db_session: Session,
    test_user: User,
    monkeypatch,
) -> None:
    assert test_user.id is not None
    user_id = test_user.id
    monkeypatch.setattr(
        "app.services.briefing.dig.exa_search",
        lambda *_args, **_kwargs: [
            ExaSearchResult(
                title="Useful result",
                url="https://example.com/useful",
                snippet="Useful context.",
                published_date="2026-07-01",
            )
        ],
    )

    class FakeAgent:
        def run_sync(self, _prompt: str):
            return type("Result", (), {"output": "A concise grounded digest."})()

    monkeypatch.setattr("app.services.briefing.dig.get_basic_agent", lambda *_args: FakeAgent())
    monkeypatch.setattr(
        "app.services.briefing.dig.record_model_usage",
        lambda *_args, **_kwargs: None,
    )

    search = client.post("/api/briefing/dig/search", json={"fragment": "AI chips"})
    summary = client.post(
        "/api/briefing/dig/summarize",
        json={
            "fragment": "AI chips",
            "passage_context": "A passage about AI chips.",
            "results": search.json()["results"],
        },
    )

    assert search.status_code == 200
    assert search.json()["results"][0]["title"] == "Useful result"
    assert summary.status_code == 200
    assert summary.json()["summary"] == "A concise grounded digest."

    settings = get_settings()
    monkeypatch.setattr(settings, "briefing_dig_hourly_limit", 1)
    db_session.add(
        VendorUsageRecord(
            provider="openrouter",
            model="deepseek",
            feature="briefing_dig",
            operation="briefing_dig.summarize",
            user_id=user_id,
        )
    )
    db_session.commit()

    limited = client.post(
        "/api/briefing/dig/summarize",
        json={
            "fragment": "AI chips",
            "passage_context": "A passage about AI chips.",
            "results": search.json()["results"],
        },
    )

    assert limited.status_code == 429


def test_briefing_dig_accepts_long_selected_fragment(
    client: TestClient,
    monkeypatch,
) -> None:
    long_fragment = " ".join(["long selected briefing sentence"] * 16)
    assert len(long_fragment) > 300
    captured_search: dict[str, str] = {}
    captured_prompt: dict[str, str] = {}

    def fake_exa_search(query: str, *_args, **_kwargs):
        captured_search["query"] = query
        return [
            ExaSearchResult(
                title="Useful result",
                url="https://example.com/useful",
                snippet="Useful context.",
                published_date=None,
            )
        ]

    class FakeAgent:
        def run_sync(self, prompt: str):
            captured_prompt["prompt"] = prompt
            return type("Result", (), {"output": "A concise grounded digest."})()

    monkeypatch.setattr("app.services.briefing.dig.exa_search", fake_exa_search)
    monkeypatch.setattr("app.services.briefing.dig.get_basic_agent", lambda *_args: FakeAgent())
    monkeypatch.setattr(
        "app.services.briefing.dig.record_model_usage",
        lambda *_args, **_kwargs: None,
    )

    search = client.post("/api/briefing/dig/search", json={"fragment": long_fragment})
    summary = client.post(
        "/api/briefing/dig/summarize",
        json={
            "fragment": long_fragment,
            "passage_context": "A passage about the selected sentence.",
            "results": search.json()["results"],
        },
    )

    assert search.status_code == 200
    assert summary.status_code == 200
    assert captured_search["query"] == long_fragment[:200]
    assert f"Selected fragment: {long_fragment[:300]}" in captured_prompt["prompt"]


def _seed_content_edition(
    db_session: Session,
    user: User,
    *,
    content_factory,
    status_entry_factory,
    settings,
    count: int = 3,
) -> None:
    assert user.id is not None
    user_id = user.id
    for index in range(count):
        content = content_factory(
            content_type=ContentType.ARTICLE,
            title=f"Router briefing article {index}",
            classification=ContentClassification.TO_READ.value,
            content_metadata={
                "summary": {
                    "overview": f"Router summary {index}",
                    "key_points": [f"Router point {index}"],
                }
            },
        )
        status_entry_factory(user=user, content=content, status="inbox")
    run_briefing_refresh(
        db_session,
        user_id=user_id,
        mode="full",
        use_llm=False,
        settings=settings,
    )
    db_session.commit()
