"""Central task specifications for queue routing and payload validation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from app.models.contracts import TaskQueue, TaskType


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


class UserPayload(TaskPayload):
    user_id: int | None = None


@dataclass(frozen=True)
class TaskSpec:
    task_type: TaskType
    queue: TaskQueue
    payload_model: type[TaskPayload]
    handler_key: str
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
    TaskType.SCRAPE: TaskSpec(TaskType.SCRAPE, TaskQueue.CONTENT, TaskPayload, "scrape"),
    TaskType.BACKFILL_FEEDS: TaskSpec(
        TaskType.BACKFILL_FEEDS, TaskQueue.ONBOARDING, TaskPayload, "backfill_feeds"
    ),
    TaskType.ANALYZE_URL: TaskSpec(
        TaskType.ANALYZE_URL, TaskQueue.CONTENT, AnalyzeUrlPayload, "analyze_url"
    ),
    TaskType.PROCESS_CONTENT: TaskSpec(
        TaskType.PROCESS_CONTENT, TaskQueue.CONTENT, ContentIdPayload, "process_content", True
    ),
    TaskType.ENRICH_NEWS_ITEM_ARTICLE: TaskSpec(
        TaskType.ENRICH_NEWS_ITEM_ARTICLE,
        TaskQueue.CONTENT,
        TaskPayload,
        "enrich_news_item_article",
    ),
    TaskType.PROCESS_NEWS_ITEM: TaskSpec(
        TaskType.PROCESS_NEWS_ITEM, TaskQueue.CONTENT, TaskPayload, "process_news_item"
    ),
    TaskType.PROCESS_PODCAST_MEDIA: TaskSpec(
        TaskType.PROCESS_PODCAST_MEDIA,
        TaskQueue.MEDIA,
        ProcessPodcastMediaPayload,
        "process_podcast_media",
        True,
    ),
    TaskType.DOWNLOAD_AUDIO: TaskSpec(
        TaskType.DOWNLOAD_AUDIO, TaskQueue.MEDIA, ContentIdPayload, "download_audio"
    ),
    TaskType.TRANSCRIBE: TaskSpec(
        TaskType.TRANSCRIBE, TaskQueue.MEDIA, ContentIdPayload, "transcribe"
    ),
    TaskType.DOWNLOAD_TWEET_VIDEO_AUDIO: TaskSpec(
        TaskType.DOWNLOAD_TWEET_VIDEO_AUDIO,
        TaskQueue.MEDIA,
        ContentIdPayload,
        "download_tweet_video_audio",
        True,
    ),
    TaskType.TRANSCRIBE_TWEET_VIDEO: TaskSpec(
        TaskType.TRANSCRIBE_TWEET_VIDEO,
        TaskQueue.MEDIA,
        ContentIdPayload,
        "transcribe_tweet_video",
        True,
    ),
    TaskType.SUMMARIZE: TaskSpec(
        TaskType.SUMMARIZE, TaskQueue.CONTENT, ContentIdPayload, "summarize", True
    ),
    TaskType.FETCH_DISCUSSION: TaskSpec(
        TaskType.FETCH_DISCUSSION, TaskQueue.CONTENT, ContentIdPayload, "fetch_discussion", True
    ),
    TaskType.GENERATE_IMAGE: TaskSpec(
        TaskType.GENERATE_IMAGE, TaskQueue.IMAGE, GenerateImagePayload, "generate_image", True
    ),
    TaskType.DISCOVER_FEEDS: TaskSpec(
        TaskType.DISCOVER_FEEDS, TaskQueue.CONTENT, TaskPayload, "discover_feeds"
    ),
    TaskType.GENERATE_AGENT_DIGEST: TaskSpec(
        TaskType.GENERATE_AGENT_DIGEST, TaskQueue.CONTENT, UserPayload, "generate_agent_digest"
    ),
    TaskType.ONBOARDING_DISCOVER: TaskSpec(
        TaskType.ONBOARDING_DISCOVER, TaskQueue.ONBOARDING, UserPayload, "onboarding_discover"
    ),
    TaskType.DIG_DEEPER: TaskSpec(TaskType.DIG_DEEPER, TaskQueue.CHAT, UserPayload, "dig_deeper"),
    TaskType.SYNC_INTEGRATION: TaskSpec(
        TaskType.SYNC_INTEGRATION, TaskQueue.TWITTER, UserPayload, "sync_integration"
    ),
    TaskType.GENERATE_INSIGHT_REPORT: TaskSpec(
        TaskType.GENERATE_INSIGHT_REPORT,
        TaskQueue.CONTENT,
        UserPayload,
        "generate_insight_report",
    ),
}


def get_task_spec(task_type: TaskType) -> TaskSpec:
    return TASK_SPECS[task_type]
