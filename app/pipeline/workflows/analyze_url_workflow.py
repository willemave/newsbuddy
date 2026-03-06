"""Workflow orchestration for ANALYZE_URL tasks."""

from __future__ import annotations

from typing import Any, Protocol

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.contracts import TaskType
from app.models.schema import Content
from app.pipeline.task_context import TaskContext
from app.pipeline.task_models import TaskEnvelope, TaskResult

logger = get_logger(__name__)


class FeedFlowProtocol(Protocol):
    """Protocol for feed subscription flow."""

    def run(
        self,
        db: Session,
        content: Content,
        metadata: dict[str, Any],
        url: str,
        subscribe_to_feed: bool,
    ) -> Any:
        """Execute flow and return outcome."""


class TwitterFlowProtocol(Protocol):
    """Protocol for Twitter share flow."""

    def run(
        self,
        db: Session,
        content: Content,
        metadata: dict[str, Any],
        url: str,
        task_queue_gateway: Any,
    ) -> Any:
        """Execute flow and return outcome."""


class AnalysisFlowProtocol(Protocol):
    """Protocol for URL analysis flow."""

    def run(
        self,
        db: Session,
        content: Content,
        metadata: dict[str, Any],
        url: str,
        analysis_instruction: str | None,
    ) -> Any | None:
        """Execute flow and return analysis result."""


class InstructionFanoutProtocol(Protocol):
    """Protocol for instruction-link fanout flow."""

    def run(self, db: Session, content: Content, analysis_result: Any) -> None:
        """Create child content from links."""


class PayloadCleanerProtocol(Protocol):
    """Protocol for task payload cleanup."""

    def run(self, db: Session, task_id: int) -> None:
        """Cleanup transient payload fields."""


class AnalyzeUrlWorkflow:
    """Coordinates optional flows for URL analysis tasks."""

    def __init__(
        self,
        *,
        feed_flow: FeedFlowProtocol,
        twitter_flow: TwitterFlowProtocol,
        analysis_flow: AnalysisFlowProtocol,
        instruction_fanout: InstructionFanoutProtocol,
        payload_cleaner: PayloadCleanerProtocol,
    ) -> None:
        self._feed_flow = feed_flow
        self._twitter_flow = twitter_flow
        self._analysis_flow = analysis_flow
        self._instruction_fanout = instruction_fanout
        self._payload_cleaner = payload_cleaner

    def run(
        self,
        *,
        task: TaskEnvelope,
        context: TaskContext,
        analysis_instruction: str | None,
        instruction: str | None,
        crawl_links: bool,
        subscribe_to_feed: bool,
    ) -> TaskResult:
        """Execute analyze-url workflow and enqueue processing when successful."""
        content_id = task.content_id or task.payload.get("content_id")
        if not content_id:
            return TaskResult.fail("No content_id provided")

        content_id = int(content_id)
        with context.db_factory() as db:
            content = db.query(Content).filter(Content.id == content_id).first()
            if not content:
                return TaskResult.fail("Content not found")

            url = content.url
            metadata = dict(content.content_metadata or {})
            effective_subscribe_to_feed = subscribe_to_feed or bool(
                metadata.get("subscribe_to_feed")
            )

            feed_result = self._feed_flow.run(
                db,
                content,
                metadata,
                str(url),
                effective_subscribe_to_feed,
            )
            if feed_result.handled:
                return TaskResult.ok() if feed_result.success else TaskResult.fail()

            twitter_result = self._twitter_flow.run(
                db,
                content,
                metadata,
                str(url),
                context.queue,
            )
            if twitter_result.handled and not twitter_result.success:
                return TaskResult.fail(
                    twitter_result.error_message or "Twitter share processing failed",
                    retryable=twitter_result.retryable,
                )

            analysis_result = None
            if not twitter_result.handled:
                analysis_result = self._analysis_flow.run(
                    db,
                    content,
                    metadata,
                    str(url),
                    analysis_instruction,
                )

            if (
                crawl_links
                and not twitter_result.handled
                and analysis_result
                and analysis_result.instruction
            ):
                self._instruction_fanout.run(db, content, analysis_result)

            if instruction and task.id:
                self._payload_cleaner.run(db, task.id)

        context.queue.enqueue(TaskType.PROCESS_CONTENT, content_id=content_id)
        logger.info("Enqueued PROCESS_CONTENT for content %s", content_id)
        return TaskResult.ok()
