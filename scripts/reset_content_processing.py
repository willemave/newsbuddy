#!/usr/bin/env python3
"""Reset or cancel content processing tasks with fine-grained controls."""

import argparse
import sys
from collections.abc import Iterable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import (
    create_engine,
    func,  # noqa: E402
)
from sqlalchemy.orm import Session, sessionmaker

from app.core.settings import get_settings  # noqa: E402
from app.models.contracts import (
    ContentStatus,  # noqa: E402
    ContentType,  # noqa: E402
    TaskQueue,  # noqa: E402
    TaskStatus,  # noqa: E402
    TaskType,  # noqa: E402
)
from app.models.db import Content, ProcessingTask
from app.services.queue import build_task_dedupe_key  # noqa: E402

PODCAST_RESET_METADATA_KEYS = frozenset(
    {
        "audio_url",
        "author",
        "description",
        "duration_seconds",
        "episode_number",
        "episode_title",
        "feed_config_id",
        "feed_description",
        "feed_name",
        "feed_title",
        "feed_url",
        "media_format",
        "platform",
        "publication_date",
        "source",
        "source_domain",
    }
)


class ResetOptions(BaseModel):
    """Validated options for the content reset workflow."""

    model_config = ConfigDict(extra="forbid")

    cancel_tasks_only: bool = Field(
        default=False,
        description="Delete matching processing tasks without resetting content.",
    )
    hours: float | None = Field(
        default=None,
        description="Limit operation to content touched within the last X hours.",
    )
    content_type: ContentType | None = Field(
        default=None,
        description="Restrict operation to the specified content type.",
    )

    @field_validator("hours")
    @classmethod
    def validate_hours(cls, value: float | None) -> float | None:
        """Ensure hour window is a positive value when provided."""

        if value is None:
            return None

        if value <= 0:
            msg = "--hours must be greater than zero"
            raise ValueError(msg)

        return value

    @property
    def targets_subset(self) -> bool:
        """Return whether the operation targets a subset of content."""

        return self.hours is not None or self.content_type is not None


class ResetResult(BaseModel):
    """Outcome metrics returned by the reset workflow."""

    model_config = ConfigDict(extra="forbid")

    deleted_tasks: int
    reset_contents: int
    created_tasks: int


def _build_content_query(session: Session, options: ResetOptions):
    """Return a query for the content rows affected by the options."""

    query = session.query(Content)

    if options.content_type is not None:
        query = query.filter(Content.content_type == options.content_type.value)

    if options.hours is not None:
        cutoff = datetime.utcnow() - timedelta(hours=options.hours)
        recent_timestamp = func.coalesce(
            Content.processed_at, Content.updated_at, Content.created_at
        )
        query = query.filter(recent_timestamp >= cutoff)

    return query


def _delete_processing_tasks(session: Session, content_ids: Iterable[int] | None) -> int:
    """Delete processing tasks referencing the provided content ids."""

    task_query = session.query(ProcessingTask)

    if content_ids is not None:
        ids = list(content_ids)
        if not ids:
            return 0

        task_query = task_query.filter(ProcessingTask.content_id.in_(ids))

    deleted_count = task_query.delete(synchronize_session=False)
    return int(deleted_count or 0)


def _reset_content_metadata(content: Content) -> dict[str, Any]:
    """Return metadata that should survive a content processing reset."""

    metadata = content.content_metadata if isinstance(content.content_metadata, dict) else {}
    if content.content_type != ContentType.PODCAST.value:
        return {}

    return {
        key: metadata[key]
        for key in PODCAST_RESET_METADATA_KEYS
        if key in metadata and metadata[key] not in (None, "", [], {})
    }


def perform_reset(options: ResetOptions) -> ResetResult:
    """Reset content processing state or cancel tasks based on provided options."""

    settings = get_settings()
    engine = create_engine(str(settings.database_url))
    session_factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    with session_factory() as session:
        try:
            content_rows: list[Content] = []

            if not options.cancel_tasks_only or options.targets_subset:
                content_rows = list(_build_content_query(session, options))

            content_ids: list[int] | None
            if options.targets_subset:
                content_ids = [content.id for content in content_rows if content.id is not None]
            else:
                content_ids = None

            deleted_tasks = _delete_processing_tasks(session, content_ids)

            reset_count = 0
            created_tasks = 0

            if options.cancel_tasks_only:
                session.commit()
                return ResetResult(
                    deleted_tasks=deleted_tasks,
                    reset_contents=reset_count,
                    created_tasks=created_tasks,
                )

            if content_ids is not None and not content_ids:
                session.commit()
                return ResetResult(
                    deleted_tasks=deleted_tasks,
                    reset_contents=reset_count,
                    created_tasks=created_tasks,
                )

            tasks_to_create = []
            for content in content_rows:
                tasks_to_create.append(
                    ProcessingTask(
                        task_type=TaskType.PROCESS_CONTENT.value,
                        content_id=content.id,
                        status=TaskStatus.PENDING.value,
                        queue_name=TaskQueue.CONTENT.value,
                        dedupe_key=build_task_dedupe_key(
                            task_type=TaskType.PROCESS_CONTENT,
                            content_id=content.id,
                            queue_name=TaskQueue.CONTENT,
                        ),
                        payload={
                            "content_type": content.content_type,
                            "url": content.url,
                            "source": content.source,
                        },
                    )
                )

                content.status = ContentStatus.NEW.value
                content.error_message = None
                content.retry_count = 0
                content.checked_out_by = None
                content.checked_out_at = None
                content.processed_at = None
                content.content_metadata = _reset_content_metadata(content)

            reset_count = len(content_rows)
            session.add_all(tasks_to_create)
            created_tasks = len(tasks_to_create)

            session.commit()
            return ResetResult(
                deleted_tasks=int(deleted_tasks),
                reset_contents=int(reset_count or 0),
                created_tasks=created_tasks,
            )
        except Exception:
            session.rollback()
            raise
        finally:
            engine.dispose()


def parse_args(argv: list[str]) -> ResetOptions:
    """Parse command-line arguments into validated options."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cancel-only",
        action="store_true",
        dest="cancel_tasks_only",
        help="Delete matching processing tasks without resetting content entries.",
    )
    parser.add_argument(
        "--hours",
        type=float,
        dest="hours",
        help="Only target content touched within the last X hours.",
    )
    parser.add_argument(
        "--content-type",
        choices=[member.value for member in ContentType],
        dest="content_type",
        help="Limit operation to a specific content type (article or podcast).",
    )

    namespace = parser.parse_args(argv)
    options = ResetOptions(**vars(namespace))
    return options


def main(argv: list[str] | None = None) -> None:
    """Entrypoint for the reset script."""

    cli_args = argv if argv is not None else sys.argv[1:]
    options = parse_args(cli_args)

    try:
        result = perform_reset(options)
    except Exception as exc:  # pragma: no cover - surfaced in CLI output
        print(f"Error: {exc}")
        raise

    print(f"Deleted {result.deleted_tasks} processing tasks")
    if not options.cancel_tasks_only:
        print(f"Reset {result.reset_contents} content items to 'new' status")
        print(f"Created {result.created_tasks} new processing tasks")

    if options.cancel_tasks_only:
        print("\nCancellation complete. No new tasks were enqueued.")
    else:
        print(
            "\nReset complete! You can now run 'python run_workers.py' "
            "to re-process targeted content."
        )


if __name__ == "__main__":
    main()
