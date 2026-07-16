"""Test that summarization properly updates content metadata."""

from contextlib import contextmanager
from unittest.mock import MagicMock, Mock

import pytest

from app.models.db import Content
from app.models.metadata.state import (
    extract_share_and_chat_requests,
    remove_processing_fields,
)
from app.models.metadata.summaries import (
    ContentQuote,
    InterestingExternalLink,
    NewsSummary,
    StructuredSummary,
    SummaryBulletPoint,
)
from app.pipeline.handlers.summarize import SummarizeHandler
from app.pipeline.task_context import TaskContext
from app.pipeline.task_models import TaskEnvelope
from app.services.queue import TaskType
from app.utils.summarization_inputs import compute_summarization_input_fingerprint


@pytest.fixture
def db_session():
    """Fixture for mocked database session."""
    mock_session = MagicMock()
    mock_session.query.return_value = mock_session
    mock_session.filter.return_value = mock_session
    return mock_session


def _build_context(db_session, llm_service):
    @contextmanager
    def _db_context():
        yield db_session

    return TaskContext(
        queue_service=Mock(),
        settings=Mock(),
        llm_service=llm_service,
        worker_id="test-worker",
        db_factory=_db_context,
    )


def test_extract_share_and_chat_requests_preserves_initial_messages():
    requests = extract_share_and_chat_requests(
        {
            "share_and_chat_user_ids": [1, "2"],
            "share_and_chat_requests": [
                {"user_id": 1, "initial_message": "Explain the implications."},
                {"user_id": "3"},
            ],
        }
    )

    assert requests == [
        {"user_id": 1, "initial_message": "Explain the implications."},
        {"user_id": 3},
        {"user_id": 2},
    ]


def test_remove_processing_fields_clears_flat_and_structured_metadata():
    metadata = remove_processing_fields(
        {
            "share_and_chat_user_ids": [1],
            "share_and_chat_requests": [{"user_id": 1}],
            "processing": {
                "share_and_chat_user_ids": [1],
                "share_and_chat_requests": [{"user_id": 1}],
                "submitted_via": "share_sheet",
            },
        },
        "share_and_chat_user_ids",
        "share_and_chat_requests",
    )

    assert "share_and_chat_user_ids" not in metadata
    assert "share_and_chat_requests" not in metadata
    assert metadata["processing"] == {"submitted_via": "share_sheet"}


@pytest.fixture
def mock_structured_summary():
    """Create a mock structured summary."""
    return StructuredSummary(
        title="Test Summary Title",
        overview=(
            "This is a test overview of the content that provides detailed "
            "information about the main topics discussed in the material."
        ),
        bullet_points=[
            SummaryBulletPoint(text="First key point about the main topic", category="key_finding"),
            SummaryBulletPoint(
                text="Second key point describing the methodology", category="methodology"
            ),
            SummaryBulletPoint(
                text="Third key point with important conclusions", category="conclusion"
            ),
        ],
        quotes=[ContentQuote(text="This is an important quote", context="Author Name")],
        topics=["Technology", "Innovation"],
        classification="to_read",
        full_markdown="# Test Content\n\nFull markdown content here...",
    )


def test_summarize_task_updates_podcast_metadata(
    db_session,
    mock_structured_summary,
    _isolated_content_image_storage,
):
    """Test that summarize task properly updates podcast metadata."""
    content = Mock(spec=Content)
    content.id = 1
    content.content_type = "podcast"
    content.status = "processing"
    content.content_metadata = {
        "audio_url": "https://example.com/podcast.mp3",
        "transcript": "This is a test transcript of the podcast episode.",
        "source": "Test Podcast Feed",
    }

    db_session.first.return_value = content

    llm_service = Mock()
    llm_service.summarize.return_value = mock_structured_summary

    handler = SummarizeHandler()
    context = _build_context(db_session, llm_service)

    task = TaskEnvelope(
        id=1,
        task_type=TaskType.SUMMARIZE,
        content_id=1,
        payload={"content_id": 1},
    )

    result = handler.handle(task, context)

    assert result.success is True
    assert content.content_metadata != {
        "audio_url": "https://example.com/podcast.mp3",
        "transcript": "This is a test transcript of the podcast episode.",
        "source": "Test Podcast Feed",
    }

    assert "summary" in content.content_metadata
    assert "summarization_date" in content.content_metadata
    expected_summary = mock_structured_summary.model_dump(mode="json")
    expected_summary.pop("full_markdown", None)

    summary = content.content_metadata["summary"]
    assert summary == expected_summary

    assert content.content_metadata["audio_url"] == "https://example.com/podcast.mp3"
    assert "transcript" not in content.content_metadata
    assert content.content_metadata["has_transcript"] is True
    assert content.content_metadata["excerpt"]
    assert content.content_metadata["source"] == "Test Podcast Feed"

    assert content.status == "awaiting_image"
    assert content.processed_at is not None


def test_summarize_task_updates_article_metadata(
    db_session,
    mock_structured_summary,
    monkeypatch,
):
    """Test that summarize task properly updates article metadata."""
    content = Mock(spec=Content)
    content.id = 1
    content.content_type = "article"
    content.status = "processing"
    content.content_metadata = {
        "content": "This is the full text content of the article.",
        "author": "Test Author",
        "source": "Test Blog",
    }

    db_session.first.return_value = content

    llm_service = Mock()
    llm_service.summarize.return_value = mock_structured_summary
    monkeypatch.setattr(
        "app.pipeline.handlers.summarize.select_interesting_external_links",
        lambda *_args, **_kwargs: [],
    )

    handler = SummarizeHandler()
    context = _build_context(db_session, llm_service)

    task = TaskEnvelope(
        id=1,
        task_type=TaskType.SUMMARIZE,
        content_id=1,
        payload={"content_id": 1},
    )

    result = handler.handle(task, context)

    assert result.success is True
    assert "summary" in content.content_metadata
    expected_summary = mock_structured_summary.model_dump(mode="json")
    expected_summary.pop("full_markdown", None)
    assert content.content_metadata["summary"] == expected_summary

    assert content.content_metadata["author"] == "Test Author"
    assert content.content_metadata["source"] == "Test Blog"
    assert "content" not in content.content_metadata
    assert content.content_metadata["excerpt"]


def test_summarize_task_persists_interesting_external_links(
    db_session,
    mock_structured_summary,
    monkeypatch,
):
    """Article summarization should add curated links to metadata without body fields."""
    content = Mock(spec=Content)
    content.id = 1
    content.content_type = "article"
    content.status = "processing"
    content.content_metadata = {
        "content": "Read the [paper](https://papers.example.org/model) for methodology.",
        "source": "Test Blog",
    }
    content.url = "https://example.com/post"
    content.title = "Test Article"
    content.source = "Test Blog"
    content.platform = None
    content.publication_date = None
    content.processed_at = None
    content.error_message = None
    content.checked_out_by = None
    content.checked_out_at = None

    db_session.first.return_value = content

    llm_service = Mock()
    llm_service.summarize.return_value = mock_structured_summary
    monkeypatch.setattr(
        "app.pipeline.handlers.summarize.select_interesting_external_links",
        lambda *_args, **_kwargs: [
            InterestingExternalLink(
                url="https://papers.example.org/model",
                title="Original model paper",
                reason="Primary source for the methodology.",
                category="primary_source",
                confidence=0.95,
            )
        ],
    )

    handler = SummarizeHandler()
    context = _build_context(db_session, llm_service)

    task = TaskEnvelope(
        id=1,
        task_type=TaskType.SUMMARIZE,
        content_id=1,
        payload={"content_id": 1},
    )

    result = handler.handle(task, context)

    assert result.success is True
    assert content.content_metadata["interesting_external_links"] == [
        {
            "url": "https://papers.example.org/model",
            "title": "Original model paper",
            "reason": "Primary source for the methodology.",
            "category": "primary_source",
            "confidence": 0.95,
        }
    ]


def test_summarize_task_updates_news_metadata(db_session):
    """Test that summarize task properly updates news metadata with aggregator context."""
    news_summary = NewsSummary(
        title="Breaking: Tech Company Announces New Product",
        overview="Major tech company revealed their latest innovation today.",
        bullet_points=[
            "New product features AI integration",
            "Expected to ship Q1 2025",
        ],
        classification="to_read",
    )

    content = Mock(spec=Content)
    content.id = 1
    content.title = None
    content.content_type = "news"
    content.status = "processing"
    content.content_metadata = {
        "content": "Full article text about the new product announcement...",
        "article": {
            "title": "Tech Company Product Launch",
            "url": "https://example.com/article",
        },
        "aggregator": {
            "name": "HackerNews",
            "title": "Show HN: New Product",
            "metadata": {"score": 150, "comments_count": 42},
        },
        "discussion_url": "https://news.ycombinator.com/item?id=12345",
        "platform": "hackernews",
    }

    db_session.first.return_value = content

    llm_service = Mock()
    llm_service.summarize.return_value = news_summary

    handler = SummarizeHandler()
    context = _build_context(db_session, llm_service)

    task = TaskEnvelope(
        id=1,
        task_type=TaskType.SUMMARIZE,
        content_id=1,
        payload={"content_id": 1},
    )

    result = handler.handle(task, context)

    assert result.success is True

    llm_service.summarize.assert_called_once()
    call_kwargs = llm_service.summarize.call_args.kwargs
    assert call_kwargs["content_type"] == "longform_artifact"
    assert call_kwargs["provider_override"] is None
    assert call_kwargs["max_bullet_points"] == 8
    assert call_kwargs["max_quotes"] == 5

    call_args = llm_service.summarize.call_args.args
    assert "Context:" in call_args[0]
    assert "Article Title:" in call_args[0]
    assert "Aggregator Context:" in call_args[0]

    assert "summary" in content.content_metadata
    assert content.content_metadata["summary"]["classification"] == "to_read"
    assert content.title == "Breaking: Tech Company Announces New Product"
    assert content.status == "completed"


def test_summarize_task_handles_missing_content(db_session):
    """Test that summarize task handles missing content gracefully."""
    db_session.first.return_value = None

    handler = SummarizeHandler()
    context = _build_context(db_session, Mock())

    task = TaskEnvelope(
        id=1,
        task_type=TaskType.SUMMARIZE,
        content_id=99999,
        payload={"content_id": 99999},
    )

    result = handler.handle(task, context)
    assert result.success is False


def test_summarize_task_handles_missing_text(db_session):
    """Test that summarize task handles content without text gracefully."""
    content = Mock(spec=Content)
    content.id = 1
    content.content_type = "podcast"
    content.status = "processing"
    content.content_metadata = {"audio_url": "https://example.com/podcast.mp3"}

    db_session.first.return_value = content

    handler = SummarizeHandler()
    context = _build_context(db_session, Mock())

    task = TaskEnvelope(
        id=1,
        task_type=TaskType.SUMMARIZE,
        content_id=1,
        payload={"content_id": 1},
    )

    result = handler.handle(task, context)

    assert result.success is True
    assert content.status == "skipped"
    assert "No text to summarize" in (content.error_message or "")
    assert isinstance(content.content_metadata, dict)
    assert "processing_errors" in content.content_metadata
    processing_errors = content.content_metadata["processing_errors"]
    assert isinstance(processing_errors, list)
    assert processing_errors
    assert processing_errors[-1]["stage"] == "summarization"


def test_summarize_task_skips_llm_when_input_fingerprint_matches(
    db_session,
    _isolated_content_image_storage,
):
    """Matching fingerprints should reuse the existing summary without another model call."""
    article_text = "This is the full text content of the article."
    content = Mock(spec=Content)
    content.id = 1
    content.content_type = "article"
    content.status = "processing"
    content.content_metadata = {
        "content": article_text,
        "summary": {
            "title": "Existing Summary",
            "overview": "Already summarized.",
            "bullet_points": [],
            "topics": [],
            "classification": "to_read",
        },
        "summarization_input_fingerprint": compute_summarization_input_fingerprint(
            "article",
            article_text,
        ),
    }

    db_session.first.return_value = content

    llm_service = Mock()
    handler = SummarizeHandler()
    context = _build_context(db_session, llm_service)

    task = TaskEnvelope(
        id=1,
        task_type=TaskType.SUMMARIZE,
        content_id=1,
        payload={"content_id": 1},
    )

    result = handler.handle(task, context)

    assert result.success is True
    llm_service.summarize.assert_not_called()
    assert content.status == "awaiting_image"
    assert content.processed_at is not None
