"""Example tests demonstrating fixture usage.

This module shows how to use the content fixtures for testing different
parts of the processing pipeline.
"""

from typing import Any

import pytest

from app.models.db import Content


def test_load_article_fixture(sample_article_long: dict[str, Any]) -> None:
    """Test loading a long-form article fixture."""
    assert sample_article_long["content_type"] == "article"
    assert sample_article_long["status"] == "completed"
    assert "content_metadata" in sample_article_long

    # Verify summary structure
    summary = sample_article_long["content_metadata"]["summary"]
    assert "title" in summary
    assert "overview" in summary
    assert "bullet_points" in summary
    assert "quotes" in summary
    assert "topics" in summary


def test_load_podcast_fixture(sample_podcast: dict[str, Any]) -> None:
    """Test loading a podcast fixture."""
    assert sample_podcast["content_type"] == "podcast"
    assert sample_podcast["status"] == "completed"

    metadata = sample_podcast["content_metadata"]
    assert "transcript" in metadata
    assert "summary" in metadata
    assert "duration_seconds" in metadata


def test_unprocessed_content(sample_unprocessed_article: dict[str, Any]) -> None:
    """Test loading unprocessed content for pipeline testing."""
    assert sample_unprocessed_article["status"] == "new"
    assert "content" in sample_unprocessed_article["content_metadata"]

    # Unprocessed content should not have a summary
    metadata = sample_unprocessed_article["content_metadata"]
    assert "summary" not in metadata or not metadata.get("summary")


def test_create_content_in_db(
    create_sample_content,
    sample_article_long: dict[str, Any],
    db_session,
) -> None:
    """Test creating content in the database from a fixture."""
    content = create_sample_content(sample_article_long)

    # Verify it was created
    assert content.id is not None
    assert content.content_type == "article"
    assert content.status == "completed"

    # Verify it's in the database
    db_content = db_session.query(Content).filter_by(id=content.id).first()
    assert db_content is not None
    assert db_content.url == sample_article_long["url"]


def test_multiple_content_types(
    create_sample_content,
    sample_article_long: dict[str, Any],
    sample_podcast: dict[str, Any],
    db_session,
) -> None:
    """Test creating multiple content types in the database."""
    article = create_sample_content(sample_article_long)
    podcast = create_sample_content(sample_podcast)

    # Verify both were created
    assert article.content_type == "article"
    assert podcast.content_type == "podcast"

    # Verify we can query them
    articles = db_session.query(Content).filter_by(content_type="article").all()
    podcasts = db_session.query(Content).filter_by(content_type="podcast").all()

    assert len(articles) >= 1
    assert len(podcasts) >= 1


def test_content_metadata_structure(sample_article_long: dict[str, Any]) -> None:
    """Test that content metadata follows expected structure."""
    metadata = sample_article_long["content_metadata"]

    # Check required fields
    assert "source" in metadata
    assert "content_type" in metadata
    assert "content" in metadata
    assert "summary" in metadata

    # Check summary structure
    summary = metadata["summary"]
    required_summary_fields = [
        "title",
        "overview",
        "bullet_points",
        "quotes",
        "topics",
        "classification",
    ]
    for field in required_summary_fields:
        assert field in summary, f"Missing required summary field: {field}"

    # Check bullet points structure
    for bullet in summary["bullet_points"]:
        assert "text" in bullet
        assert "category" in bullet

    # Check quotes structure
    for quote in summary["quotes"]:
        assert "text" in quote
        assert "context" in quote


def test_podcast_metadata_structure(sample_podcast: dict[str, Any]) -> None:
    """Test that podcast metadata follows expected structure."""
    metadata = sample_podcast["content_metadata"]

    # Check podcast-specific fields
    podcast_fields = [
        "transcript",
        "audio_url",
        "duration_seconds",
        "feed_name",
        "episode_number",
    ]
    for field in podcast_fields:
        assert field in metadata, f"Missing required podcast field: {field}"


@pytest.mark.parametrize(
    "fixture_name",
    [
        "sample_article_long",
        "sample_article_short",
        "sample_podcast",
        "sample_unprocessed_article",
        "sample_unprocessed_podcast",
    ],
)
def test_all_fixtures_have_required_fields(fixture_name: str, request) -> None:
    """Test that all fixtures have required base fields."""
    fixture_data = request.getfixturevalue(fixture_name)

    required_fields = ["id", "content_type", "url", "title", "source", "status"]
    for field in required_fields:
        assert field in fixture_data, f"Missing required field '{field}' in {fixture_name}"
