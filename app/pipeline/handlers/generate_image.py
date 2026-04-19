"""Image generation task handler."""

from __future__ import annotations

from datetime import UTC, datetime

from app.core.logging import get_logger
from app.models.metadata import ContentType
from app.models.schema import Content
from app.pipeline.task_context import TaskContext
from app.pipeline.task_models import TaskEnvelope, TaskResult
from app.services.content_metadata_merge import refresh_merge_content_metadata
from app.services.content_status_state_machine import ContentStatusStateMachine
from app.services.queue import TaskType

logger = get_logger(__name__)


class GenerateImageHandler:
    """Handle AI image generation tasks."""

    task_type = TaskType.GENERATE_IMAGE

    def handle(self, task: TaskEnvelope, context: TaskContext) -> TaskResult:
        """Generate an AI image for content."""
        try:
            content_id = task.content_id or task.payload.get("content_id")
            if not content_id:
                logger.error("No content_id provided for image generation task")
                return TaskResult.fail("No content_id provided")

            content_id = int(content_id)
            logger.info("Generating image for content %s", content_id)

            with context.db_factory() as db:
                content = db.query(Content).filter(Content.id == content_id).first()
                if not content:
                    logger.error("Content %s not found for image generation", content_id)
                    return TaskResult.fail("Content not found")
                if content.content_type == ContentType.NEWS.value:
                    logger.info(
                        "Skipping AI image generation for news content %s",
                        content_id,
                    )
                    return TaskResult.ok()

                from app.models.content_mapper import content_to_domain
                from app.services.image_generation import get_image_generation_service
                from app.utils.image_urls import build_content_image_url, build_thumbnail_url

                domain_content = content_to_domain(content)
                image_service = get_image_generation_service()
                result = image_service.generate_image(domain_content)

                if result.success:
                    content_type = content.content_type
                    if not content_type:
                        logger.error(
                            "Cannot complete generated image for content %s without content_type",
                            content_id,
                        )
                        return TaskResult.fail("Missing content type for generated image update")

                    base_metadata = dict(content.content_metadata or {})
                    metadata = dict(base_metadata)
                    metadata["image_generated_at"] = datetime.now(UTC).isoformat()
                    metadata["image_url"] = build_content_image_url(content_id)
                    if result.thumbnail_path:
                        metadata["thumbnail_url"] = build_thumbnail_url(content_id)
                    content.content_metadata = refresh_merge_content_metadata(
                        db,
                        content_id=content.id,
                        base_metadata=base_metadata,
                        updated_metadata=metadata,
                    )
                    content.status = ContentStatusStateMachine.status_after_generated_artwork(
                        content_type=content_type,
                        current_status=content.status,
                    ).value
                    content.processed_at = datetime.now(UTC).replace(tzinfo=None)
                    db.commit()

                    logger.info(
                        "Successfully generated image for content %s at %s",
                        content_id,
                        result.image_path,
                    )
                    return TaskResult.ok()

                if result.error_message and "Skipped" in result.error_message:
                    logger.info(
                        "Image generation skipped for %s: %s",
                        content_id,
                        result.error_message,
                    )
                    return TaskResult.ok()

                logger.error(
                    "Image generation failed for %s: %s",
                    content_id,
                    result.error_message,
                    extra={
                        "component": "image_generation",
                        "operation": "generate_image",
                        "item_id": content_id,
                    },
                )
                return TaskResult.fail(result.error_message)

        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "Image generation error for content %s: %s",
                task.content_id or task.payload.get("content_id") or "unknown",
                exc,
                extra={
                    "component": "image_generation",
                    "operation": "generate_image_task",
                    "item_id": task.content_id or task.payload.get("content_id") or "unknown",
                },
            )
            return TaskResult.fail(str(exc))
