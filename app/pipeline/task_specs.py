"""Central task specifications for queue routing and payload validation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.models.contracts import TaskQueue, TaskType
from app.models.internal.feed_backfill import MAX_BACKFILL_COUNT


class TaskPayload(BaseModel):
    model_config = ConfigDict(extra="allow")


class AnalyzeUrlPayload(TaskPayload):
    content_id: int | None = None
    instruction: str | None = None
    crawl_links: bool = False
    subscribe_to_feed: bool = False


class ContentIdPayload(TaskPayload):
    content_id: int | None = None


class ProcessPodcastMediaPayload(ContentIdPayload):
    media_url: str | None = None


class GenerateImagePayload(ContentIdPayload):
    force: bool = False


class RequiredUserPayload(TaskPayload):
    user_id: int


class NewsItemIdPayload(TaskPayload):
    news_item_id: int


class BackfillFeedsPayload(RequiredUserPayload):
    config_ids: list[int] = Field(..., min_length=1)
    count: int = Field(..., ge=1, le=MAX_BACKFILL_COUNT)


class DigDeeperPayload(RequiredUserPayload):
    initial_message: str | None = None


class AudioEpisodePayload(TaskPayload):
    audio_episode_id: int


class LearningDeckRunPayload(RequiredUserPayload):
    learning_deck_run_id: int


class LlmTaskRunPayload(RequiredUserPayload):
    llm_task_id: int


class SyncIntegrationPayload(RequiredUserPayload):
    provider: str = "x"
    trigger: str = "cron"


class InsightReportPayload(RequiredUserPayload):
    synthesis_model: str | None = None
    effort: str | None = None


class BriefingRefreshPayload(RequiredUserPayload):
    mode: str = "append"


@dataclass(frozen=True)
class TaskSpec:
    task_type: TaskType
    queue: TaskQueue
    payload_model: type[TaskPayload]
    dedupe_by_content: bool = False

    def normalize_payload(self, payload: dict[str, Any] | None) -> dict[str, Any]:
        try:
            return self.payload_model.model_validate(payload or {}).model_dump(
                mode="json",
                exclude_none=True,
            )
        except ValidationError as exc:
            raise ValueError(f"Invalid payload for {self.task_type.value}: {exc}") from exc


TASK_SPECS: dict[TaskType, TaskSpec] = {
    TaskType.SCRAPE: TaskSpec(TaskType.SCRAPE, TaskQueue.CONTENT, TaskPayload),
    TaskType.BACKFILL_FEEDS: TaskSpec(
        TaskType.BACKFILL_FEEDS,
        TaskQueue.BACKFILL,
        BackfillFeedsPayload,
    ),
    TaskType.ANALYZE_URL: TaskSpec(TaskType.ANALYZE_URL, TaskQueue.CONTENT, AnalyzeUrlPayload),
    TaskType.PROCESS_CONTENT: TaskSpec(
        TaskType.PROCESS_CONTENT, TaskQueue.CONTENT, ContentIdPayload, True
    ),
    TaskType.ENRICH_NEWS_ITEM_ARTICLE: TaskSpec(
        TaskType.ENRICH_NEWS_ITEM_ARTICLE,
        TaskQueue.CONTENT,
        TaskPayload,
    ),
    TaskType.PROCESS_NEWS_ITEM: TaskSpec(
        TaskType.PROCESS_NEWS_ITEM,
        TaskQueue.CONTENT,
        NewsItemIdPayload,
        True,
    ),
    TaskType.PROCESS_PODCAST_MEDIA: TaskSpec(
        TaskType.PROCESS_PODCAST_MEDIA,
        TaskQueue.MEDIA,
        ProcessPodcastMediaPayload,
        True,
    ),
    TaskType.DOWNLOAD_AUDIO: TaskSpec(TaskType.DOWNLOAD_AUDIO, TaskQueue.MEDIA, ContentIdPayload),
    TaskType.TRANSCRIBE: TaskSpec(TaskType.TRANSCRIBE, TaskQueue.MEDIA, ContentIdPayload),
    TaskType.DOWNLOAD_TWEET_VIDEO_AUDIO: TaskSpec(
        TaskType.DOWNLOAD_TWEET_VIDEO_AUDIO,
        TaskQueue.MEDIA,
        ContentIdPayload,
        True,
    ),
    TaskType.TRANSCRIBE_TWEET_VIDEO: TaskSpec(
        TaskType.TRANSCRIBE_TWEET_VIDEO,
        TaskQueue.MEDIA,
        ContentIdPayload,
        True,
    ),
    TaskType.SUMMARIZE: TaskSpec(TaskType.SUMMARIZE, TaskQueue.CONTENT, ContentIdPayload, True),
    TaskType.FETCH_DISCUSSION: TaskSpec(
        TaskType.FETCH_DISCUSSION, TaskQueue.DISCUSSION, ContentIdPayload, True
    ),
    TaskType.FETCH_NEWS_ITEM_DISCUSSION: TaskSpec(
        TaskType.FETCH_NEWS_ITEM_DISCUSSION,
        TaskQueue.DISCUSSION,
        NewsItemIdPayload,
        True,
    ),
    TaskType.GENERATE_IMAGE: TaskSpec(
        TaskType.GENERATE_IMAGE, TaskQueue.IMAGE, GenerateImagePayload, True
    ),
    TaskType.DISCOVER_FEEDS: TaskSpec(TaskType.DISCOVER_FEEDS, TaskQueue.CONTENT, TaskPayload),
    TaskType.ONBOARDING_DISCOVER: TaskSpec(
        TaskType.ONBOARDING_DISCOVER,
        TaskQueue.ONBOARDING,
        RequiredUserPayload,
        True,
    ),
    TaskType.DIG_DEEPER: TaskSpec(TaskType.DIG_DEEPER, TaskQueue.CHAT, DigDeeperPayload),
    TaskType.SYNC_INTEGRATION: TaskSpec(
        TaskType.SYNC_INTEGRATION,
        TaskQueue.TWITTER,
        SyncIntegrationPayload,
        True,
    ),
    TaskType.GENERATE_INSIGHT_REPORT: TaskSpec(
        TaskType.GENERATE_INSIGHT_REPORT,
        TaskQueue.CONTENT,
        InsightReportPayload,
    ),
    TaskType.GENERATE_AUDIO_EPISODE: TaskSpec(
        TaskType.GENERATE_AUDIO_EPISODE,
        TaskQueue.AUDIO_EPISODE,
        AudioEpisodePayload,
        True,
    ),
    TaskType.GENERATE_LEARNING_DECK: TaskSpec(
        TaskType.GENERATE_LEARNING_DECK,
        TaskQueue.LEARNING,
        LearningDeckRunPayload,
    ),
    TaskType.RUN_LLM_TASK: TaskSpec(
        TaskType.RUN_LLM_TASK,
        TaskQueue.LLM,
        LlmTaskRunPayload,
    ),
    TaskType.BRIEFING_REFRESH: TaskSpec(
        TaskType.BRIEFING_REFRESH,
        TaskQueue.LLM,
        BriefingRefreshPayload,
    ),
}


def get_task_spec(task_type: TaskType) -> TaskSpec:
    return TASK_SPECS[task_type]
