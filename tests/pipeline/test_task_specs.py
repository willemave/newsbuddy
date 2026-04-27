"""Tests for central task specifications."""

import pytest

from app.models.contracts import TaskQueue, TaskType
from app.pipeline.task_specs import get_task_spec


def test_task_spec_defines_queue_payload_and_dedupe_for_core_tasks() -> None:
    analyze = get_task_spec(TaskType.ANALYZE_URL)
    summarize = get_task_spec(TaskType.SUMMARIZE)
    image = get_task_spec(TaskType.GENERATE_IMAGE)

    assert analyze.queue == TaskQueue.CONTENT
    assert analyze.normalize_payload({"content_id": 1, "instruction": "Read links"}) == {
        "content_id": 1,
        "instruction": "Read links",
        "crawl_links": False,
        "subscribe_to_feed": False,
    }
    assert summarize.dedupe_by_content is True
    assert image.queue == TaskQueue.IMAGE


def test_task_spec_payload_validation_rejects_bad_types() -> None:
    spec = get_task_spec(TaskType.ANALYZE_URL)

    with pytest.raises(ValueError, match="Invalid payload"):
        spec.normalize_payload({"content_id": "not-an-int"})
