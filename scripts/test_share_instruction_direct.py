#!/usr/bin/env python3
"""Test share-sheet instruction flow without HTTP endpoints."""

# ruff: noqa: E402
from __future__ import annotations

import argparse
import sys
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

from pydantic import HttpUrl, TypeAdapter

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.db import get_db
from app.models.content_submission import SubmitContentRequest
from app.models.metadata import ContentType
from app.models.schema import Content
from app.models.user import User
from app.pipeline.sequential_task_processor import SequentialTaskProcessor
from app.services.content_submission import submit_user_content
from app.services.queue import QueueService, TaskType


def _get_or_create_user(db, email: str) -> User:
    user = db.query(User).filter(User.email == email).first()
    if user:
        return user

    user = User(
        apple_id=f"local_{email}",
        email=email,
        full_name="Local Test",
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _process_analyze_tasks(queue: QueueService) -> list[int]:
    processed: list[int] = []
    processor = SequentialTaskProcessor()

    while True:
        task = queue.dequeue(TaskType.ANALYZE_URL)
        if not task:
            break
        processor.run_single_task(task)
        processed.append(task["id"])

    return processed


def main(urls: Iterable[str], instruction: str, email: str) -> None:
    queue = QueueService()
    submitted_ids: list[int] = []
    start_time = datetime.now(UTC)

    with get_db() as db:
        user = _get_or_create_user(db, email)

        for url in urls:
            payload = SubmitContentRequest(
                url=TypeAdapter(HttpUrl).validate_python(url),
                content_type=ContentType.ARTICLE,
                title=None,
                instruction=instruction,
                platform=None,
                crawl_links=False,
                subscribe_to_feed=False,
                share_and_chat=False,
                save_to_knowledge_and_mark_read=False,
            )
            response = submit_user_content(db, payload, user)
            print("submitted:", response.model_dump())
            if response.content_id is not None:
                submitted_ids.append(response.content_id)

    processed = _process_analyze_tasks(queue)
    print(f"processed {len(processed)} analyze_url tasks")

    with get_db() as db:
        if submitted_ids:
            submitted = db.query(User).filter(User.email == email).first()
            submitter_id = submitted.id if submitted else None
        else:
            submitter_id = None

        submitted_contents = (
            db.query(Content).filter(Content.id.in_(submitted_ids)).all() if submitted_ids else []
        )
        if submitted_contents:
            print("submitted content results:")
            for content in submitted_contents:
                metadata = content.content_metadata or {}
                print(
                    " -",
                    {
                        "id": content.id,
                        "url": content.url,
                        "content_type": content.content_type,
                        "status": content.status,
                        "platform": content.platform,
                        "metadata_keys": list(metadata.keys()),
                    },
                )

        derived_contents = []
        if submitter_id is not None:
            all_contents = db.query(Content).all()
            for content in all_contents:
                metadata = content.content_metadata or {}
                if metadata.get("submitted_via") != "share_sheet_instruction":
                    continue
                if metadata.get("submitted_by_user_id") != submitter_id:
                    continue
                if content.created_at and content.created_at < start_time:
                    continue
                derived_contents.append(content)

        if derived_contents:
            print(f"derived content results ({len(derived_contents)}):")
            for content in derived_contents:
                print(
                    " -",
                    {
                        "id": content.id,
                        "url": content.url,
                        "content_type": content.content_type,
                        "status": content.status,
                    },
                )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Direct share instruction flow test")
    parser.add_argument(
        "--url",
        action="append",
        required=True,
        help="URL to submit (can be provided multiple times)",
    )
    parser.add_argument(
        "--instruction",
        default="",
        help="Instruction for the analyzer",
    )
    parser.add_argument(
        "--email",
        default="local@test.dev",
        help="Email for test user",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    main(args.url, args.instruction, args.email)
