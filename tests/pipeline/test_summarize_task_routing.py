"""Tests for summarize task routing."""

from contextlib import contextmanager
from unittest.mock import Mock

from sqlalchemy.orm import sessionmaker

from app.constants import SUMMARY_KIND_LONG_EDITORIAL_NARRATIVE, SUMMARY_VERSION_V1
from app.models.metadata import EditorialNarrativeSummary, NewsSummary
from app.models.schema import Content, ContentStatusEntry
from app.pipeline.handlers.summarize import SummarizeHandler
from app.pipeline.task_context import TaskContext
from app.pipeline.task_models import TaskEnvelope
from app.services.queue import TaskType


def _override_get_db(db_session):
    @contextmanager
    def _get_db():
        yield db_session

    return _get_db


class DummySummarizer:
    """Minimal summarizer stub for task routing tests."""

    def summarize(
        self,
        content: str,
        content_type: str,
        content_id: int,
        max_bullet_points: int,
        max_quotes: int,
        provider_override: str | None = None,
    ):
        if content_type == "news_digest":
            return NewsSummary(
                title="News Title",
                article_url="https://example.com",
                key_points=["Point 1"],
                summary="Overview",
            )
        return EditorialNarrativeSummary(
            title="Article Title",
            editorial_narrative=(
                "First paragraph with concrete details, entities, metrics, and a clear thesis "
                "about why execution quality, governance controls, and measurable impact matter "
                "more than isolated benchmark gains.\n\n"
                "Second paragraph with implications, constraints, and evidence-driven guidance "
                "that outlines near-term tradeoffs, implementation risks, and practical actions."
            ),
            quotes=[
                {"text": "Quote one with enough detail for validation.", "attribution": "Source A"},
                {"text": "Quote two with enough detail for validation.", "attribution": "Source B"},
            ],
            archetype_reactions=[
                {
                    "archetype": "Paul Graham",
                    "paragraphs": [
                        (
                            "Paragraph one about user pull, founder leverage, and "
                            "overlooked opportunity."
                        ),
                        (
                            "Paragraph two about what a small team could build faster "
                            "than incumbents."
                        ),
                    ],
                },
                {
                    "archetype": "Andy Grove",
                    "paragraphs": [
                        "Paragraph one about strategic inflection points and chokepoints.",
                        "Paragraph two about execution risk and what leaders should monitor next.",
                    ],
                },
                {
                    "archetype": "Charlie Munger",
                    "paragraphs": [
                        "Paragraph one about incentives and misjudgment shaping behavior.",
                        "Paragraph two about second-order effects, moats, and misunderstanding.",
                    ],
                },
            ],
            key_points=[
                {"point": "Key point one with concrete detail and consequence."},
                {"point": "Key point two with concrete detail and consequence."},
                {"point": "Key point three with concrete detail and consequence."},
                {"point": "Key point four with concrete detail and consequence."},
            ],
        )


def _create_content(db_session, content_type: str) -> Content:
    content = Content(
        content_type=content_type,
        url="https://example.com",
        status="processing",
        content_metadata={
            "content": "Some content",
            "article": {"url": "https://example.com"},
        }
        if content_type == "news"
        else {"content": "Some content"},
    )
    db_session.add(content)
    db_session.commit()
    db_session.refresh(content)
    return content


def _add_inbox_status(db_session, user_id: int, content_id: int) -> None:
    db_session.add(
        ContentStatusEntry(
            user_id=user_id,
            content_id=content_id,
            status="inbox",
        )
    )
    db_session.commit()


def _build_context(db_session, queue_service, llm_service) -> TaskContext:
    return TaskContext(
        queue_service=queue_service,
        settings=Mock(),
        llm_service=llm_service,
        worker_id="test-worker",
        db_factory=_override_get_db(db_session),
    )


def test_summarize_news_does_not_enqueue_image_tasks(db_session) -> None:
    content = _create_content(db_session, "news")
    queue_service = Mock()
    handler = SummarizeHandler()
    context = _build_context(db_session, queue_service, DummySummarizer())

    task = TaskEnvelope(
        id=1,
        task_type=TaskType.SUMMARIZE,
        content_id=content.id,
    )

    assert handler.handle(task, context).success is True
    queue_service.enqueue.assert_not_called()


def test_summarize_article_enqueues_image_when_visible_in_inbox(db_session, test_user) -> None:
    content = _create_content(db_session, "article")
    _add_inbox_status(db_session, test_user.id, content.id)
    queue_service = Mock()
    handler = SummarizeHandler()
    context = _build_context(db_session, queue_service, DummySummarizer())

    task = TaskEnvelope(
        id=2,
        task_type=TaskType.SUMMARIZE,
        content_id=content.id,
    )

    assert handler.handle(task, context).success is True
    queue_service.enqueue.assert_called_once_with(
        task_type=TaskType.GENERATE_IMAGE,
        content_id=content.id,
    )
    db_session.refresh(content)
    assert content.content_metadata["summary_kind"] == SUMMARY_KIND_LONG_EDITORIAL_NARRATIVE
    assert content.content_metadata["summary_version"] == SUMMARY_VERSION_V1


def test_summarize_article_does_not_enqueue_image_when_not_visible(db_session) -> None:
    content = _create_content(db_session, "article")
    queue_service = Mock()
    handler = SummarizeHandler()
    context = _build_context(db_session, queue_service, DummySummarizer())

    task = TaskEnvelope(
        id=21,
        task_type=TaskType.SUMMARIZE,
        content_id=content.id,
    )

    assert handler.handle(task, context).success is True
    queue_service.enqueue.assert_not_called()


def test_summarize_article_falls_back_to_content_to_summarize(db_session) -> None:
    content = Content(
        content_type="article",
        url="https://example.com/fallback",
        status="processing",
        content_metadata={"content": "", "content_to_summarize": "Fallback content"},
    )
    db_session.add(content)
    db_session.commit()
    db_session.refresh(content)

    queue_service = Mock()
    llm_service = Mock()
    llm_service.summarize.return_value = {
        "title": "Article Title",
        "overview": "Summary",
        "bullet_points": [],
    }
    handler = SummarizeHandler()
    context = _build_context(db_session, queue_service, llm_service)

    task = TaskEnvelope(
        id=3,
        task_type=TaskType.SUMMARIZE,
        content_id=content.id,
    )

    assert handler.handle(task, context).success is True
    llm_service.summarize.assert_called_once()
    assert llm_service.summarize.call_args[0][0] == "Fallback content"


def test_summarize_preserves_top_comment_from_concurrent_discussion_update(db_session) -> None:
    content = _create_content(db_session, "article")

    class ConcurrentUpdatingSummarizer:
        def summarize(
            self,
            content: str,
            content_type: str,
            content_id: int,
            max_bullet_points: int,
            max_quotes: int,
            provider_override: str | None = None,
        ) -> dict[str, object]:
            external_session_factory = sessionmaker(
                autocommit=False,
                autoflush=False,
                bind=db_session.get_bind(),
            )
            external_session = external_session_factory()
            try:
                external_content = (
                    external_session.query(Content).filter(Content.id == content_id).first()
                )
                assert external_content is not None
                external_metadata = dict(external_content.content_metadata or {})
                external_metadata["top_comment"] = {
                    "author": "alice",
                    "text": "Great write-up",
                }
                external_content.content_metadata = external_metadata
                external_session.commit()
            finally:
                external_session.close()

            return {
                "title": "Article Title",
                "overview": "Summary",
                "bullet_points": [],
            }

    queue_service = Mock()
    handler = SummarizeHandler()
    context = _build_context(db_session, queue_service, ConcurrentUpdatingSummarizer())

    task = TaskEnvelope(
        id=31,
        task_type=TaskType.SUMMARIZE,
        content_id=content.id,
    )

    result = handler.handle(task, context)

    assert result.success is True
    db_session.refresh(content)
    assert content.content_metadata.get("top_comment") == {
        "author": "alice",
        "text": "Great write-up",
    }
    assert isinstance(content.content_metadata.get("summary"), dict)


def test_summarize_no_text_marks_content_skipped_without_retry(db_session) -> None:
    content = Content(
        content_type="news",
        url="https://example.com/no-text",
        status="processing",
        content_metadata={"article": {"url": "https://example.com/no-text"}},
    )
    db_session.add(content)
    db_session.commit()
    db_session.refresh(content)

    queue_service = Mock()
    llm_service = Mock()
    handler = SummarizeHandler()
    context = _build_context(db_session, queue_service, llm_service)

    task = TaskEnvelope(
        id=4,
        task_type=TaskType.SUMMARIZE,
        content_id=content.id,
    )

    result = handler.handle(task, context)

    assert result.success is True
    db_session.refresh(content)
    assert content.status == "skipped"
    queue_service.enqueue.assert_not_called()
    llm_service.summarize.assert_not_called()


def test_summarize_skips_terminal_content_status(db_session) -> None:
    content = Content(
        content_type="news",
        url="https://example.com/terminal",
        status="failed",
        content_metadata={"processing_errors": [{"stage": "process_content"}]},
    )
    db_session.add(content)
    db_session.commit()
    db_session.refresh(content)

    queue_service = Mock()
    llm_service = Mock()
    handler = SummarizeHandler()
    context = _build_context(db_session, queue_service, llm_service)

    task = TaskEnvelope(
        id=40,
        task_type=TaskType.SUMMARIZE,
        content_id=content.id,
    )

    result = handler.handle(task, context)

    assert result.success is True
    queue_service.enqueue.assert_not_called()
    llm_service.summarize.assert_not_called()


def test_summarize_none_result_is_non_retryable_failure(db_session) -> None:
    content = _create_content(db_session, "article")
    queue_service = Mock()
    llm_service = Mock()
    llm_service.summarize.return_value = None
    handler = SummarizeHandler()
    context = _build_context(db_session, queue_service, llm_service)

    task = TaskEnvelope(
        id=5,
        task_type=TaskType.SUMMARIZE,
        content_id=content.id,
    )

    result = handler.handle(task, context)

    assert result.success is False
    assert result.retryable is False


def test_summarize_transient_exception_is_retryable(db_session) -> None:
    content = _create_content(db_session, "article")
    queue_service = Mock()
    llm_service = Mock()
    llm_service.summarize.side_effect = TimeoutError("request timed out")
    handler = SummarizeHandler()
    context = _build_context(db_session, queue_service, llm_service)

    task = TaskEnvelope(
        id=6,
        task_type=TaskType.SUMMARIZE,
        content_id=content.id,
    )

    result = handler.handle(task, context)

    assert result.success is False
    assert result.retryable is True
    db_session.refresh(content)
    assert content.status == "processing"


def test_summarize_non_retryable_exception_marks_failed(db_session) -> None:
    content = _create_content(db_session, "article")
    queue_service = Mock()
    llm_service = Mock()
    llm_service.summarize.side_effect = ValueError("schema validation failed")
    handler = SummarizeHandler()
    context = _build_context(db_session, queue_service, llm_service)

    task = TaskEnvelope(
        id=7,
        task_type=TaskType.SUMMARIZE,
        content_id=content.id,
    )

    result = handler.handle(task, context)

    assert result.success is False
    assert result.retryable is False
    db_session.refresh(content)
    assert content.status == "failed"
