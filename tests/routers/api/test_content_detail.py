"""Tests for content detail chat URL generation."""

from copy import deepcopy
from datetime import UTC, datetime
from urllib.parse import parse_qs, unquote_plus, urlparse

from app.models.schema import Content
from app.queries.get_content_body import MAX_CONTENT_BODY_RESPONSE_CHARS, TRUNCATED_BODY_NOTICE


def _get_display_title(fixture_data: dict) -> str:
    """Get the display title the same way the API does."""
    summary = fixture_data.get("content_metadata", {}).get("summary", {})
    if summary and summary.get("title"):
        return summary["title"]
    return fixture_data.get("title") or "Untitled"


def test_chat_url_includes_user_prompt(
    client,
    create_sample_content,
    sample_article_long,
):
    """Ensure user-provided prompt is prepended to the generated ChatGPT URL."""

    content = create_sample_content(sample_article_long)
    expected_title = _get_display_title(sample_article_long)

    response = client.get(
        f"/api/content/{content.id}/chat-url",
        params={"user_prompt": "Corroborate key claims using the latest sources."},
    )

    assert response.status_code == 200
    data = response.json()
    chat_url = data["chat_url"]

    parsed = urlparse(chat_url)
    q_param = parse_qs(parsed.query).get("q")

    assert q_param, "Expected 'q' query parameter in generated URL"

    decoded_prompt = unquote_plus(q_param[0])

    assert "USER PROMPT:" in decoded_prompt
    assert "Corroborate key claims using the latest sources." in decoded_prompt
    assert expected_title in decoded_prompt


def test_chat_url_without_user_prompt(client, create_sample_content, sample_article_short):
    """Ensure legacy behavior still works when no user prompt is provided."""

    content = create_sample_content(sample_article_short)
    expected_title = _get_display_title(sample_article_short)

    response = client.get(f"/api/content/{content.id}/chat-url")

    assert response.status_code == 200
    data = response.json()

    parsed = urlparse(data["chat_url"])
    q_param = parse_qs(parsed.query).get("q")

    assert q_param, "Expected 'q' query parameter in generated URL"

    decoded_prompt = unquote_plus(q_param[0])

    assert "USER PROMPT:" not in decoded_prompt
    assert expected_title in decoded_prompt


def test_content_narration_returns_article_summary(
    client,
    create_sample_content,
    sample_article_long,
):
    """Unified narration endpoint should return narration text for articles."""

    content = create_sample_content(sample_article_long)

    response = client.get(f"/api/content/narration/content/{content.id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["target_type"] == "content"
    assert payload["target_id"] == content.id
    assert payload["title"]
    assert "Here is the full summary for" in payload["narration_text"]


def test_content_narration_returns_podcast_summary(
    client,
    create_sample_content,
    sample_podcast,
):
    """Unified narration endpoint should also work for podcasts."""

    content = create_sample_content(sample_podcast)

    response = client.get(f"/api/content/narration/content/{content.id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["target_type"] == "content"
    assert payload["target_id"] == content.id
    assert payload["title"]
    assert payload["narration_text"]


def test_content_narration_returns_audio_bytes(
    client,
    create_sample_content,
    sample_article_long,
    monkeypatch,
) -> None:
    """Unified narration endpoint should stream audio when audio is requested."""

    content = create_sample_content(sample_article_long)
    captured: dict[str, object] = {}

    class _FakeTtsService:
        def synthesize_mp3(
            self,
            *,
            text: str,
            item_id: int | None = None,
            user_id: int | None = None,
        ) -> bytes:
            captured["text"] = text
            captured["item_id"] = item_id
            captured["user_id"] = user_id
            return b"fake-content-mp3"

    monkeypatch.setattr(
        "app.routers.api.narration.get_digest_narration_tts_service",
        lambda: _FakeTtsService(),
    )

    response = client.get(
        f"/api/content/narration/content/{content.id}",
        headers={"Accept": "audio/mpeg"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("audio/mpeg")
    assert response.content == b"fake-content-mp3"
    assert captured["item_id"] == content.id
    assert captured["user_id"] is not None
    assert "Here is the full summary for" in str(captured["text"])


def test_content_body_requires_visible_content(
    client,
    create_sample_content,
    sample_article_long,
):
    """Canonical body endpoint should reject content outside the user's inbox."""
    content = create_sample_content(sample_article_long, visible=False)

    response = client.get(f"/api/content/{content.id}/body")

    assert response.status_code == 404


def test_content_body_returns_visible_content(client, create_sample_content, sample_article_long):
    """Canonical body endpoint should serve visible content bodies."""
    content = create_sample_content(sample_article_long)

    response = client.get(f"/api/content/{content.id}/body")

    assert response.status_code == 200
    payload = response.json()
    assert payload["content_id"] == content.id
    assert payload["variant"] == "source"
    assert payload["text"]


def test_content_body_truncates_oversized_visible_content(
    client,
    create_sample_content,
    sample_article_long,
):
    """Canonical body endpoint should bound oversized payloads for the app renderer."""
    fixture_data = deepcopy(sample_article_long)
    huge_body = ("Paragraph of very long article text.\n\n" * 6_000).strip()
    fixture_data["content_metadata"]["content_to_summarize"] = huge_body

    content = create_sample_content(fixture_data)

    response = client.get(f"/api/content/{content.id}/body")

    assert response.status_code == 200
    payload = response.json()
    assert payload["content_id"] == content.id
    assert payload["text"].endswith(TRUNCATED_BODY_NOTICE)
    assert len(payload["text"]) <= MAX_CONTENT_BODY_RESPONSE_CHARS
    assert huge_body.startswith(payload["text"].split(TRUNCATED_BODY_NOTICE, 1)[0])


def test_content_detail_redacts_oversized_internal_metadata(
    client,
    create_sample_content,
    sample_article_long,
):
    """Detail payload should omit oversized internal metadata blobs."""
    fixture_data = deepcopy(sample_article_long)
    fixture_data["content_metadata"]["domain"] = {"content": "x" * 20_000}
    fixture_data["content_metadata"]["processing"] = {"content": "y" * 20_000}
    fixture_data["content_metadata"]["summary"] = deepcopy(
        fixture_data["content_metadata"]["summary"]
    )
    fixture_data["content_metadata"]["summary"]["title"] = "Visible summary title"
    fixture_data["content_metadata"]["article"] = {
        "url": "https://example.com/story",
        "title": "Visible article title",
        "source_domain": "example.com",
    }
    fixture_data["content_metadata"]["keep_small"] = "ok"
    fixture_data["content_metadata"]["drop_big_misc"] = {"payload": "z" * 20_000}

    content = create_sample_content(fixture_data)

    response = client.get(f"/api/content/{content.id}")

    assert response.status_code == 200
    payload = response.json()
    metadata = payload["metadata"]
    assert metadata["summary"]["title"] == "Visible summary title"
    assert metadata["article"]["title"] == "Visible article title"
    assert metadata["keep_small"] == "ok"
    assert "domain" not in metadata
    assert "processing" not in metadata
    assert "drop_big_misc" not in metadata


def test_content_detail_falls_back_to_visible_news_item_when_legacy_content_is_missing(
    client,
    db_session,
    news_item_factory,
) -> None:
    """Unified content detail should serve visible news items when legacy content is unavailable."""
    legacy_news = Content(
        id=6227,
        content_type="news",
        url="https://legacy.example/news/6227",
        title="Legacy skipped row",
        status="skipped",
        content_metadata={},
    )
    news_item_factory(
        id=6227,
        ingest_key="news-item-6227",
        platform="hackernews",
        source_type="hackernews",
        source_label="Hacker News",
        source_external_id="6227",
        canonical_item_url="https://news.ycombinator.com/item?id=6227",
        canonical_story_url="https://example.com/story-6227",
        article_url="https://example.com/story-6227",
        article_title="Visible news story",
        article_domain="example.com",
        discussion_url="https://news.ycombinator.com/item?id=6227",
        summary_title="Visible news summary",
        summary_key_points=["Point one", "Point two"],
        summary_text="Visible short-form summary",
        raw_metadata={"cluster": {"related_titles": ["Visible news summary"]}},
        status="ready",
        ingested_at=datetime(2026, 4, 2, 14, 58, tzinfo=UTC).replace(tzinfo=None),
        processed_at=datetime(2026, 4, 2, 14, 58, tzinfo=UTC).replace(tzinfo=None),
    )
    db_session.add(legacy_news)
    db_session.commit()

    response = client.get("/api/content/6227")

    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == 6227
    assert payload["content_type"] == "news"
    assert payload["display_title"] == "Visible news summary"
    assert payload["summary"] == "Visible short-form summary"
    assert payload["metadata"]["article"]["title"] == "Visible news story"
    assert payload["metadata"]["summary"]["title"] == "Visible news summary"


def test_content_detail_rewrites_placeholder_news_metadata_titles(
    client,
    db_session,
    news_item_factory,
) -> None:
    news_item_factory(
        id=7331,
        ingest_key="news-item-7331",
        platform="hackernews",
        source_type="hackernews",
        source_label="Hacker News",
        source_external_id="7331",
        canonical_item_url="https://news.ycombinator.com/item?id=7331",
        canonical_story_url="https://example.com/story-7331",
        article_url="https://example.com/story-7331",
        article_title="SKILL0",
        article_domain="example.com",
        discussion_url="https://news.ycombinator.com/item?id=7331",
        summary_title="SKILL0",
        summary_key_points=["Point one"],
        summary_text="A concrete summary of the actual story.",
        raw_metadata={
            "article": {"title": "SKILL0"},
            "summary": {"title": "SKILL0", "summary": "A concrete summary of the actual story."},
        },
        status="ready",
        ingested_at=datetime(2026, 4, 2, 14, 58, tzinfo=UTC).replace(tzinfo=None),
        processed_at=datetime(2026, 4, 2, 14, 58, tzinfo=UTC).replace(tzinfo=None),
    )
    db_session.commit()

    response = client.get("/api/content/7331")

    assert response.status_code == 200
    payload = response.json()
    assert payload["title"] == "A concrete summary of the actual story."
    assert payload["display_title"] == "A concrete summary of the actual story."
    assert payload["metadata"]["article"]["title"] == "A concrete summary of the actual story."
    assert payload["metadata"]["summary"]["title"] == "A concrete summary of the actual story."
