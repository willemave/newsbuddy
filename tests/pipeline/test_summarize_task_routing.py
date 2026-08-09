"""Tests for summarize task routing."""

from contextlib import contextmanager
from unittest.mock import Mock

from sqlalchemy.orm import sessionmaker

from app.models.contracts import SummaryKind, SummaryVersion
from app.models.db import Content, ContentStatusEntry, ProcessingTask
from app.models.metadata.longform_artifacts import LongformArtifactEnvelope
from app.models.metadata.state import extract_share_and_chat_requests
from app.models.metadata.summaries import NewsSummary
from app.pipeline.handlers.summarize import SummarizeHandler
from app.pipeline.task_context import TaskContext
from app.pipeline.task_models import TaskEnvelope
from app.services.queue import TaskType


def _override_get_db(db_session):
    @contextmanager
    def _get_db():
        yield db_session

    return _get_db


def _artifact_summary(
    *,
    title: str = "Article Title",
    artifact_type: str = "argument",
    ask: str = "judge",
) -> LongformArtifactEnvelope:
    extras_by_type = {
        "argument": {
            "thesis": (
                "The article argues that execution quality matters more than isolated "
                "benchmark wins."
            ),
            "counterpoint": (
                "A careful reader could object that benchmarks still capture useful "
                "progress signals."
            ),
        },
        "findings": {
            "question": (
                "The paper asks whether the proposed method improves real benchmark outcomes."
            ),
            "method": (
                "The authors compare model outputs across benchmark suites and report "
                "measured differences."
            ),
            "limits": (
                "The data does not prove that the result generalizes to every downstream "
                "deployment."
            ),
        },
    }
    return LongformArtifactEnvelope.model_validate(
        {
            "title": title,
            "one_line": "This artifact explains the source's central claim and why it matters now.",
            "ask": ask,
            "artifact": {
                "type": artifact_type,
                "payload": {
                    "overview": (
                        "The source lays out a concrete position about execution quality, "
                        "governance, and measurable impact. It grounds the claim in practical "
                        "tradeoffs and explains what readers should watch next."
                    ),
                    "quotes": [
                        {
                            "text": (
                                "Quote one with enough detail for validation and useful "
                                "reader context."
                            ),
                            "attribution": "Source A",
                        },
                        {
                            "text": (
                                "Quote two with enough detail for validation and useful "
                                "reader context."
                            ),
                            "attribution": "Source B",
                        },
                    ],
                    "extras": extras_by_type[artifact_type],
                    "key_points": [
                        {
                            "heading": "Execution quality",
                            "content": (
                                "The piece says execution quality determines whether technical "
                                "progress turns into usable results."
                            ),
                        },
                        {
                            "heading": "Governance controls",
                            "content": (
                                "It connects governance controls to practical operating "
                                "constraints rather than abstract policy."
                            ),
                        },
                        {
                            "heading": "Measurable impact",
                            "content": (
                                "The source emphasizes outcomes that can be observed, "
                                "measured, and compared over time."
                            ),
                        },
                        {
                            "heading": "Near-term tradeoffs",
                            "content": (
                                "It closes by naming the tradeoffs teams need to manage as "
                                "conditions change."
                            ),
                        },
                    ],
                    "takeaway": (
                        "The useful read is the structure of the claim, not a generic recap."
                    ),
                },
            },
            "source_context": {
                "url": "https://example.com",
                "source_name": "Example",
                "publication_date": None,
                "platform": None,
            },
            "selection_trace": {
                "source_hint": "test",
                "candidates": [artifact_type],
                "selected": artifact_type,
                "reason": "The source is shaped around a concrete artifact contract.",
                "confidence": 0.91,
            },
            "feed_preview": {
                "title": title,
                "one_line": "A concise preview of the artifact and why it matters now.",
                "preview_bullets": [
                    "Execution quality is the main thread.",
                    "Governance and measurement provide the support.",
                    "The takeaway names the near-term tradeoff.",
                ],
                "reason_to_read": (
                    "Open this to judge the argument structure rather than scan a recap."
                ),
                "artifact_type": artifact_type,
            },
        }
    )


class DummySummarizer:
    """Minimal summarizer stub for task routing tests."""

    def summarize(
        self,
        content: str,
        content_type: str,
        content_id: int,
        max_bullet_points: int,
        max_quotes: int,
        title: str | None = None,
        provider_override: str | None = None,
        url: str | None = None,
        platform: str | None = None,
        source_name: str | None = None,
        publication_date: str | None = None,
        metadata=None,
        db=None,
        usage_persist=None,
    ):
        del (
            content,
            content_id,
            max_bullet_points,
            max_quotes,
            title,
            provider_override,
            url,
            platform,
            source_name,
            publication_date,
            metadata,
            db,
            usage_persist,
        )
        if content_type == "news":
            return NewsSummary(
                title="News Title",
                article_url="https://example.com",
                key_points=["Point 1"],
                summary="Overview",
            )
        return _artifact_summary()


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


def _add_inbox_status(db_session, user_id: int | None, content_id: int | None) -> None:
    assert user_id is not None
    assert content_id is not None
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


def test_summarize_article_enqueues_image_when_visible_in_inbox(
    db_session,
    test_user,
    _isolated_content_image_storage,
) -> None:
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
    assert content.content_metadata is not None
    assert content.content_metadata["summary_kind"] == SummaryKind.LONGFORM_ARTIFACT.value
    assert content.content_metadata["summary_version"] == SummaryVersion.V1.value
    assert content.content_metadata["feed_preview"]["artifact_type"] == "argument"


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


def test_summarize_preserves_chat_intent_when_task_enqueue_fails(
    db_session,
    test_user,
    monkeypatch,
) -> None:
    content = Content(
        content_type="article",
        url="https://example.com/chat-intent-retry",
        status="processing",
        content_metadata={
            "content": "Some content",
            "share_and_chat_requests": [
                {"user_id": test_user.id, "initial_message": "Explain this."}
            ],
        },
    )
    db_session.add(content)
    db_session.commit()
    monkeypatch.setattr(
        "app.pipeline.handlers.summarize.enqueue_dig_deeper_task",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("queue insert failed")),
    )
    handler = SummarizeHandler()
    context = _build_context(db_session, Mock(), DummySummarizer())
    task = TaskEnvelope(
        id=23,
        task_type=TaskType.SUMMARIZE,
        content_id=content.id,
    )

    result = handler.handle(task, context)

    assert result.success is False
    db_session.rollback()
    db_session.refresh(content)
    assert extract_share_and_chat_requests(content.content_metadata) == [
        {"user_id": test_user.id, "initial_message": "Explain this."}
    ]
    assert (
        db_session.query(ProcessingTask)
        .filter(ProcessingTask.task_type == TaskType.DIG_DEEPER.value)
        .count()
        == 0
    )


def test_summarize_commits_chat_task_before_fallible_image_followup(
    db_session,
    test_user,
    _isolated_content_image_storage,
) -> None:
    content = Content(
        content_type="article",
        url="https://example.com/chat-before-image",
        status="processing",
        content_metadata={
            "content": "Some content",
            "share_and_chat_requests": [{"user_id": test_user.id}],
        },
    )
    db_session.add(content)
    db_session.commit()
    _add_inbox_status(db_session, test_user.id, content.id)
    queue_service = Mock()
    queue_service.enqueue.side_effect = RuntimeError("image queue unavailable")
    handler = SummarizeHandler()
    context = _build_context(db_session, queue_service, DummySummarizer())
    task = TaskEnvelope(
        id=24,
        task_type=TaskType.SUMMARIZE,
        content_id=content.id,
    )

    result = handler.handle(task, context)

    assert result.success is False
    db_session.rollback()
    db_session.refresh(content)
    assert extract_share_and_chat_requests(content.content_metadata) == []
    dig_deeper_task = (
        db_session.query(ProcessingTask)
        .filter(
            ProcessingTask.task_type == TaskType.DIG_DEEPER.value,
            ProcessingTask.content_id == content.id,
            ProcessingTask.owner_user_id == test_user.id,
        )
        .one()
    )
    assert dig_deeper_task.payload["user_id"] == test_user.id


def test_summarize_pdf_article_writes_longform_artifact(db_session) -> None:
    content = Content(
        content_type="article",
        url="https://example.com/paper.pdf",
        status="processing",
        content_metadata={
            "content": "Some research content",
            "content_type": "pdf",
        },
    )
    db_session.add(content)
    db_session.commit()
    db_session.refresh(content)

    queue_service = Mock()
    llm_service = Mock()
    llm_service.summarize.return_value = _artifact_summary(
        title="Paper Title",
        artifact_type="findings",
        ask="update",
    )
    handler = SummarizeHandler()
    context = _build_context(db_session, queue_service, llm_service)

    task = TaskEnvelope(
        id=22,
        task_type=TaskType.SUMMARIZE,
        content_id=content.id,
    )

    assert handler.handle(task, context).success is True
    llm_service.summarize.assert_called_once()
    assert llm_service.summarize.call_args.kwargs["content_type"] == "longform_artifact"
    db_session.refresh(content)
    assert content.content_metadata is not None
    assert content.content_metadata["summary_kind"] == SummaryKind.LONGFORM_ARTIFACT.value
    assert content.content_metadata["summary_version"] == SummaryVersion.V1.value
    assert content.content_metadata["selection_trace"]["selected"] == "findings"


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
    llm_service.summarize.return_value = _artifact_summary()
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
            title: str | None = None,
            provider_override: str | None = None,
            url: str | None = None,
            platform: str | None = None,
            source_name: str | None = None,
            publication_date: str | None = None,
            metadata=None,
            db=None,
            usage_persist=None,
        ) -> LongformArtifactEnvelope:
            del content, content_type, max_bullet_points, max_quotes, title, provider_override
            del url, platform, source_name, publication_date, metadata, db, usage_persist
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

            return _artifact_summary()

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
    assert content.content_metadata is not None
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
