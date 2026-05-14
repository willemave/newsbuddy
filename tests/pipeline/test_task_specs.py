"""Tests for central task specifications."""

import pytest

from app.models.contracts import TaskQueue, TaskType
from app.pipeline.task_specs import get_task_spec


def test_task_spec_defines_queue_payload_and_dedupe_for_core_tasks() -> None:
    analyze = get_task_spec(TaskType.ANALYZE_URL)
    summarize = get_task_spec(TaskType.SUMMARIZE)
    news_discussion = get_task_spec(TaskType.FETCH_NEWS_ITEM_DISCUSSION)
    audio_episode = get_task_spec(TaskType.GENERATE_AUDIO_EPISODE)
    backfill = get_task_spec(TaskType.BACKFILL_FEEDS)
    image = get_task_spec(TaskType.GENERATE_IMAGE)
    sync_integration = get_task_spec(TaskType.SYNC_INTEGRATION)
    onboarding = get_task_spec(TaskType.ONBOARDING_DISCOVER)
    dig_deeper = get_task_spec(TaskType.DIG_DEEPER)
    insight_report = get_task_spec(TaskType.GENERATE_INSIGHT_REPORT)

    assert analyze.queue == TaskQueue.CONTENT
    assert analyze.normalize_payload({"content_id": 1, "instruction": "Read links"}) == {
        "content_id": 1,
        "instruction": "Read links",
        "crawl_links": False,
        "subscribe_to_feed": False,
    }
    assert summarize.dedupe_by_content is True
    assert news_discussion.queue == TaskQueue.DISCUSSION
    assert news_discussion.dedupe_by_content is True
    assert news_discussion.normalize_payload({"news_item_id": 12}) == {"news_item_id": 12}
    assert audio_episode.queue == TaskQueue.AUDIO_EPISODE
    assert audio_episode.dedupe_by_content is True
    assert backfill.queue == TaskQueue.BACKFILL
    assert backfill.normalize_payload({"user_id": 1, "config_ids": [2], "count": 10}) == {
        "user_id": 1,
        "config_ids": [2],
        "count": 10,
    }
    assert image.queue == TaskQueue.IMAGE
    assert sync_integration.normalize_payload({"user_id": 3}) == {
        "user_id": 3,
        "provider": "x",
        "trigger": "cron",
    }
    assert onboarding.queue == TaskQueue.ONBOARDING
    assert onboarding.normalize_payload({"user_id": 3, "run_id": 9}) == {
        "user_id": 3,
        "run_id": 9,
    }
    assert dig_deeper.queue == TaskQueue.CHAT
    assert dig_deeper.normalize_payload({"user_id": 4, "initial_message": "why?"}) == {
        "user_id": 4,
        "initial_message": "why?",
    }
    assert insight_report.normalize_payload({"user_id": 5}) == {"user_id": 5}


def test_task_spec_payload_validation_rejects_bad_types() -> None:
    spec = get_task_spec(TaskType.ANALYZE_URL)

    with pytest.raises(ValueError, match="Invalid payload"):
        spec.normalize_payload({"content_id": "not-an-int"})


def test_task_spec_payload_validation_rejects_missing_required_fields() -> None:
    with pytest.raises(ValueError, match="Invalid payload for backfill_feeds"):
        get_task_spec(TaskType.BACKFILL_FEEDS).normalize_payload(
            {"user_id": 1, "config_ids": [], "count": 10}
        )

    with pytest.raises(ValueError, match="Invalid payload for sync_integration"):
        get_task_spec(TaskType.SYNC_INTEGRATION).normalize_payload({"provider": "x"})

    with pytest.raises(ValueError, match="Invalid payload for onboarding_discover"):
        get_task_spec(TaskType.ONBOARDING_DISCOVER).normalize_payload({"run_id": 1})

    with pytest.raises(ValueError, match="Invalid payload for generate_insight_report"):
        get_task_spec(TaskType.GENERATE_INSIGHT_REPORT).normalize_payload({})
