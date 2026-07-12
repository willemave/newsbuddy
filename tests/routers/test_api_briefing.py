from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.settings import get_settings
from app.models.contracts import ContentClassification, ContentType, TaskType
from app.models.db import AudioEpisode, ProcessingTask, VendorUsageRecord
from app.models.db.users import User
from app.services.briefing.first_run import start_first_edition
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

    not_modified = client.get("/api/briefing", headers={"If-None-Match": etag})

    assert not_modified.status_code == 304
    assert not_modified.headers["etag"] == etag
    assert not_modified.headers["cache-control"] == "private, no-cache"
    assert "Authorization" in not_modified.headers["vary"].split(", ")


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

    first = client.post("/api/briefing/narration", json={"lens_key": "articles"})
    second = client.post("/api/briefing/narration", json={"lens_key": "articles"})

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    episode = db_session.query(AudioEpisode).one()
    assert episode.kind == "briefing_narration"
    assert episode.source_snapshot is not None
    assert episode.source_snapshot["read_on_play"]["content_ids"]


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
) -> None:
    assert user.id is not None
    user_id = user.id
    for index in range(3):
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
