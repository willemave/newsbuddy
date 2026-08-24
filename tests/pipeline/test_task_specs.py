"""Tests for central task specifications."""

from pathlib import Path

import pytest

from app.models.contracts import TaskQueue, TaskType
from app.pipeline.task_specs import TASK_SPECS, get_task_spec


@pytest.mark.parametrize(
    "retired_task_type",
    ["download_audio", "transcribe", "generate_learning_deck", "fetch_discussion"],
)
def test_retired_compatibility_task_types_are_not_public_contracts(
    retired_task_type: str,
) -> None:
    with pytest.raises(ValueError):
        TaskType(retired_task_type)


def test_every_task_type_has_one_spec_and_no_empty_learning_queue() -> None:
    assert set(TASK_SPECS) == set(TaskType)
    assert all(spec.handler_module and spec.handler_class for spec in TASK_SPECS.values())
    with pytest.raises(ValueError):
        TaskQueue("learning")


def test_every_task_type_has_a_production_enqueue_path() -> None:
    """A registered handler without any producer is an unreachable queue path."""
    sources = {
        path: path.read_text()
        for path in [
            *Path("app").rglob("*.py"),
            *Path("scripts").rglob("*.py"),
        ]
    }

    missing_producers: list[str] = []
    for task_type, spec in TASK_SPECS.items():
        handler_path = Path(*spec.handler_module.split(".")).with_suffix(".py")
        task_reference = f"TaskType.{task_type.name}"
        producers = [
            path
            for path, source in sources.items()
            if path != handler_path
            and task_reference in source
            and (
                "enqueue(" in source
                or "TaskEnqueueRequest(" in source
                or "enqueue_agent_data_sync(" in source
            )
        ]
        if not producers:
            missing_producers.append(task_type.value)

    assert missing_producers == []


def test_task_specs_are_the_single_source_for_context_llm_dependencies() -> None:
    context_llm_task_types = {
        task_type for task_type, spec in TASK_SPECS.items() if spec.requires_context_llm_service
    }

    assert context_llm_task_types == {
        TaskType.PROCESS_NEWS_ITEM,
        TaskType.SUMMARIZE,
        TaskType.FETCH_NEWS_ITEM_DISCUSSION,
    }


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
    llm_task = get_task_spec(TaskType.RUN_LLM_TASK)

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
    assert audio_episode.normalize_payload({"audio_episode_id": "8", "user_id": "6"}) == {
        "user_id": 6,
        "audio_episode_id": 8,
    }
    assert backfill.queue == TaskQueue.BACKFILL
    assert backfill.normalize_payload(
        {"user_id": 1, "config_ids": [2], "count": 10, "first_edition_run_id": 7}
    ) == {
        "user_id": 1,
        "config_ids": [2],
        "count": 10,
        "first_edition_run_id": 7,
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
    assert llm_task.queue == TaskQueue.LLM
    assert llm_task.normalize_payload({"user_id": 6, "llm_task_id": 7}) == {
        "user_id": 6,
        "llm_task_id": 7,
    }


def test_task_spec_payload_validation_rejects_bad_types() -> None:
    spec = get_task_spec(TaskType.ANALYZE_URL)

    with pytest.raises(ValueError, match="Invalid payload"):
        spec.normalize_payload({"content_id": "not-an-int"})


def test_agent_data_task_payloads_reject_cross_task_fields() -> None:
    sync = get_task_spec(TaskType.SYNC_AGENT_DATA)
    index = get_task_spec(TaskType.INDEX_AGENT_DATA)
    backfill = get_task_spec(TaskType.BACKFILL_AGENT_DATA)
    reconcile = get_task_spec(TaskType.RECONCILE_AGENT_DATA)

    assert backfill.normalize_payload({"user_id": 7}) == {
        "user_id": 7,
        "stage": "knowledge",
    }
    assert reconcile.normalize_payload({"user_id": 7, "before_id": 12}) == {
        "user_id": 7,
        "before_id": 12,
    }
    with pytest.raises(ValueError, match="Invalid payload for sync_agent_data"):
        sync.normalize_payload({"user_id": 7, "before_id": 12})
    with pytest.raises(ValueError, match="Invalid payload for index_agent_data"):
        index.normalize_payload({"user_id": 7, "content_ids": [3]})
    with pytest.raises(ValueError, match="Invalid payload for backfill_agent_data"):
        backfill.normalize_payload({"user_id": 7, "stage": "unknown"})


def test_task_spec_payload_validation_rejects_missing_required_fields() -> None:
    with pytest.raises(ValueError, match="Invalid payload for backfill_feeds"):
        get_task_spec(TaskType.BACKFILL_FEEDS).normalize_payload(
            {"user_id": 1, "config_ids": [], "count": 10}
        )

    with pytest.raises(ValueError, match="Invalid payload for sync_integration"):
        get_task_spec(TaskType.SYNC_INTEGRATION).normalize_payload({"provider": "x"})

    with pytest.raises(ValueError, match="Invalid payload for onboarding_discover"):
        get_task_spec(TaskType.ONBOARDING_DISCOVER).normalize_payload({"run_id": 1})

    with pytest.raises(ValueError, match="Invalid payload for onboarding_discover"):
        get_task_spec(TaskType.ONBOARDING_DISCOVER).normalize_payload({"user_id": 1, "run_id": 0})

    with pytest.raises(ValueError, match="Invalid payload for generate_audio_episode"):
        get_task_spec(TaskType.GENERATE_AUDIO_EPISODE).normalize_payload(
            {"user_id": 1, "audio_episode_id": 0}
        )
