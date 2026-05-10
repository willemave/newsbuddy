from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, func

from app.core.db import get_db
from app.core.logging import get_logger
from app.core.settings import get_settings
from app.models.contracts import ContentStatus, ContentType
from app.models.db import Content

logger = get_logger(__name__)
settings = get_settings()


class CheckoutManager:
    """Manages content checkout/checkin for workers."""

    def __init__(self):
        self.timeout_minutes = settings.checkout_timeout_minutes

    @contextmanager
    def checkout_content(
        self, worker_id: str, content_type: ContentType | None = None, batch_size: int = 1
    ):
        """
        Context manager for checking out content.

        Usage:
            with checkout_manager.checkout_content(worker_id) as content_ids:
                for content_id in content_ids:
                    # Process content using content_id

        The content is automatically checked back in when the context exits.
        """
        content_ids = self._checkout_batch(worker_id, content_type, batch_size)

        try:
            yield content_ids
            # If we get here, processing succeeded
            for content_id in content_ids:
                self._checkin(content_id, worker_id, ContentStatus.COMPLETED)
        except Exception as e:
            # On error, check in as failed
            logger.error(f"Error in checkout context: {e}")
            for content_id in content_ids:
                self._checkin(content_id, worker_id, ContentStatus.FAILED, str(e))
            raise

    def _checkout_batch(
        self, worker_id: str, content_type: ContentType | None = None, batch_size: int = 1
    ) -> list[int]:
        """Check out a batch of content for processing."""
        with get_db() as db:
            # Build query for available content
            query = db.query(Content).filter(
                and_(Content.status == ContentStatus.NEW.value, Content.checked_out_by.is_(None))
            )

            # Filter by content type if specified
            if content_type:
                query = query.filter(Content.content_type == content_type.value)

            # Order by priority (retry_count, created_at)
            query = query.order_by(Content.retry_count, Content.created_at).limit(batch_size)

            # Lock rows for update
            content_list = query.with_for_update(skip_locked=True).all()

            # Extract IDs while objects are still attached to session
            content_ids = [content.id for content in content_list if content.id is not None]

            # Check out each item
            for content in content_list:
                content.checked_out_by = worker_id
                content.checked_out_at = datetime.now(UTC)
                content.status = ContentStatus.PROCESSING.value

            db.commit()

            if content_list:
                logger.info(f"Worker {worker_id} checked out {len(content_list)} items")

            return content_ids

    def _checkin(
        self,
        content_id: int,
        worker_id: str,
        new_status: ContentStatus,
        error_message: str | None = None,
    ):
        """Check in a content item after processing."""
        with get_db() as db:
            content = (
                db.query(Content)
                .filter(and_(Content.id == content_id, Content.checked_out_by == worker_id))
                .first()
            )

            if not content:
                logger.error(f"Content {content_id} not found or not checked out by {worker_id}")
                return

            # Update status
            content.status = new_status.value
            content.checked_out_by = None
            content.checked_out_at = None

            if new_status == ContentStatus.COMPLETED:
                content.processed_at = datetime.now(UTC)
            elif new_status == ContentStatus.FAILED:
                content.error_message = error_message
                content.retry_count = (content.retry_count or 0) + 1

            db.commit()
            logger.debug(f"Content {content_id} checked in with status {new_status}")

    def release_stale_checkouts(self) -> int:
        """Release checkouts that have timed out."""
        with get_db() as db:
            timeout_threshold = datetime.now(UTC) - timedelta(minutes=self.timeout_minutes)

            stale_content = (
                db.query(Content)
                .filter(
                    and_(
                        Content.checked_out_by.isnot(None),
                        Content.checked_out_at < timeout_threshold,
                    )
                )
                .all()
            )

            for content in stale_content:
                logger.warning(
                    f"Releasing stale checkout for content {content.id} "
                    f"(worker: {content.checked_out_by})"
                )
                content.checked_out_by = None
                content.checked_out_at = None
                content.status = ContentStatus.NEW.value
                content.retry_count = (content.retry_count or 0) + 1

            db.commit()

            if stale_content:
                logger.info(f"Released {len(stale_content)} stale checkouts")

            return len(stale_content)

    def get_checkout_stats(self) -> dict[str, Any]:
        """Get checkout statistics."""
        with get_db() as db:
            stats = {
                "total_checked_out": db.query(Content)
                .filter(Content.checked_out_by.isnot(None))
                .count(),
                "by_worker": {},
            }

            # Group by worker
            worker_counts = (
                db.query(Content.checked_out_by, func.count(Content.id))
                .filter(Content.checked_out_by.isnot(None))
                .group_by(Content.checked_out_by)
                .all()
            )

            stats["by_worker"] = {worker: count for worker, count in worker_counts}

            return stats


# Global instance
_checkout_manager = None


def get_checkout_manager() -> CheckoutManager:
    """Get the global checkout manager instance."""
    global _checkout_manager
    if _checkout_manager is None:
        _checkout_manager = CheckoutManager()
    return _checkout_manager
