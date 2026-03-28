"""Test that summarization properly updates content metadata."""

from contextlib import contextmanager
from unittest.mock import MagicMock, Mock

import pytest

from app.models.metadata import (
    ContentQuote,
    NewsSummary,
    StructuredSummary,
    SummaryBulletPoint,
)
from app.models.schema import Content
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


def test_summarize_task_updates_podcast_metadata(db_session, mock_structured_summary):
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
    llm_service.summarize_content.return_value = mock_structured_summary

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

    summary = content.content_metadata["summary"]
    assert summary == expected_summary

    assert content.content_metadata["audio_url"] == "https://example.com/podcast.mp3"
    assert content.content_metadata["transcript"] == (
        "This is a test transcript of the podcast episode."
    )
    assert content.content_metadata["source"] == "Test Podcast Feed"

    assert content.status == "completed"
    assert content.processed_at is not None


def test_summarize_task_updates_article_metadata(db_session, mock_structured_summary):
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
    llm_service.summarize_content.return_value = mock_structured_summary

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
    assert content.content_metadata["summary"] == expected_summary

    assert content.content_metadata["author"] == "Test Author"
    assert content.content_metadata["source"] == "Test Blog"


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
    llm_service.summarize_content.return_value = news_summary

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

    llm_service.summarize_content.assert_called_once()
    call_kwargs = llm_service.summarize_content.call_args.kwargs
    assert call_kwargs["content_type"] == "news_digest"
    assert call_kwargs["provider_override"] is None
    assert call_kwargs["max_bullet_points"] == 4
    assert call_kwargs["max_quotes"] == 0

    call_args = llm_service.summarize_content.call_args.args
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
    content.content_metadata = {
        "audio_url": "https://example.com/podcast.mp3"
    }

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


def test_summarize_task_skips_llm_when_input_fingerprint_matches(db_session):
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
    llm_service.summarize_content.assert_not_called()
    assert content.status == "completed"
    assert content.processed_at is not None
