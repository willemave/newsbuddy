"""Central task specifications for queue routing and payload validation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.models.contracts import TaskQueue, TaskType
from app.models.internal.feed_backfill import FeedBatchBackfillRequest


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


class OnboardingDiscoverPayload(RequiredUserPayload):
    run_id: int | None = Field(default=None, gt=0)


class NewsItemIdPayload(TaskPayload):
    news_item_id: int


class ScrapePayload(TaskPayload):
    sources: list[str] = Field(default_factory=lambda: ["all"], min_length=1)
    first_edition_run_id: int | None = Field(default=None, gt=0)


class DigDeeperPayload(RequiredUserPayload):
    initial_message: str | None = None
    session_id: int | None = Field(default=None, gt=0)
    message_id: int | None = Field(default=None, gt=0)
    prompt: str | None = None


class AudioEpisodePayload(RequiredUserPayload):
    user_id: int = Field(gt=0)
    audio_episode_id: int = Field(gt=0)


class ChatTurnPayload(RequiredUserPayload):
    session_id: int
    message_id: int


class LlmTaskRunPayload(RequiredUserPayload):
    llm_task_id: int


class SyncIntegrationPayload(RequiredUserPayload):
    provider: str = "x"
    trigger: str = "cron"


class BriefingRefreshPayload(RequiredUserPayload):
    mode: str = "append"


@dataclass(frozen=True)
class TaskSpec:
    task_type: TaskType
    queue: TaskQueue
    payload_model: type[BaseModel]
    handler_module: str
    handler_class: str
    dedupe_by_content: bool = False
    requires_context_llm_service: bool = False
    requires_owner: bool = False

    def normalize_payload(self, payload: dict[str, Any] | None) -> dict[str, Any]:
        try:
            return self.payload_model.model_validate(payload or {}).model_dump(
                mode="json",
                exclude_none=True,
            )
        except ValidationError as exc:
            raise ValueError(f"Invalid payload for {self.task_type.value}: {exc}") from exc


_TASK_SPEC_SEQUENCE: tuple[TaskSpec, ...] = (
    TaskSpec(
        TaskType.SCRAPE,
        TaskQueue.CONTENT,
        ScrapePayload,
        "app.pipeline.handlers.scrape",
        "ScrapeHandler",
    ),
    TaskSpec(
        TaskType.BACKFILL_FEEDS,
        TaskQueue.BACKFILL,
        FeedBatchBackfillRequest,
        "app.pipeline.handlers.backfill_feeds",
        "BackfillFeedsHandler",
        requires_owner=True,
    ),
    TaskSpec(
        TaskType.ANALYZE_URL,
        TaskQueue.CONTENT,
        AnalyzeUrlPayload,
        "app.pipeline.handlers.analyze_url",
        "AnalyzeUrlHandler",
    ),
    TaskSpec(
        TaskType.PROCESS_CONTENT,
        TaskQueue.CONTENT,
        ContentIdPayload,
        "app.pipeline.handlers.process_content",
        "ProcessContentHandler",
        dedupe_by_content=True,
    ),
    TaskSpec(
        TaskType.ENRICH_NEWS_ITEM_ARTICLE,
        TaskQueue.CONTENT,
        TaskPayload,
        "app.pipeline.handlers.enrich_news_item_article",
        "EnrichNewsItemArticleHandler",
    ),
    TaskSpec(
        TaskType.PROCESS_NEWS_ITEM,
        TaskQueue.CONTENT,
        NewsItemIdPayload,
        "app.pipeline.handlers.process_news_item",
        "ProcessNewsItemHandler",
        dedupe_by_content=True,
        requires_context_llm_service=True,
    ),
    TaskSpec(
        TaskType.PROCESS_PODCAST_MEDIA,
        TaskQueue.MEDIA,
        ProcessPodcastMediaPayload,
        "app.pipeline.handlers.process_podcast_media",
        "ProcessPodcastMediaHandler",
        dedupe_by_content=True,
    ),
    TaskSpec(
        TaskType.DOWNLOAD_TWEET_VIDEO_AUDIO,
        TaskQueue.MEDIA,
        ContentIdPayload,
        "app.pipeline.handlers.download_tweet_video",
        "DownloadTweetVideoAudioHandler",
        dedupe_by_content=True,
    ),
    TaskSpec(
        TaskType.TRANSCRIBE_TWEET_VIDEO,
        TaskQueue.MEDIA,
        ContentIdPayload,
        "app.pipeline.handlers.transcribe_tweet_video",
        "TranscribeTweetVideoHandler",
        dedupe_by_content=True,
    ),
    TaskSpec(
        TaskType.SUMMARIZE,
        TaskQueue.CONTENT,
        ContentIdPayload,
        "app.pipeline.handlers.summarize",
        "SummarizeHandler",
        dedupe_by_content=True,
        requires_context_llm_service=True,
    ),
    TaskSpec(
        TaskType.FETCH_NEWS_ITEM_DISCUSSION,
        TaskQueue.DISCUSSION,
        NewsItemIdPayload,
        "app.pipeline.handlers.fetch_news_item_discussion",
        "FetchNewsItemDiscussionHandler",
        dedupe_by_content=True,
        requires_context_llm_service=True,
    ),
    TaskSpec(
        TaskType.GENERATE_IMAGE,
        TaskQueue.IMAGE,
        GenerateImagePayload,
        "app.pipeline.handlers.generate_image",
        "GenerateImageHandler",
        dedupe_by_content=True,
    ),
    TaskSpec(
        TaskType.DISCOVER_FEEDS,
        TaskQueue.CONTENT,
        RequiredUserPayload,
        "app.pipeline.handlers.discover_feeds",
        "DiscoverFeedsHandler",
        requires_owner=True,
    ),
    TaskSpec(
        TaskType.ONBOARDING_DISCOVER,
        TaskQueue.ONBOARDING,
        OnboardingDiscoverPayload,
        "app.pipeline.handlers.onboarding_discover",
        "OnboardingDiscoverHandler",
        dedupe_by_content=True,
        requires_owner=True,
    ),
    TaskSpec(
        TaskType.DIG_DEEPER,
        TaskQueue.CHAT,
        DigDeeperPayload,
        "app.pipeline.handlers.dig_deeper",
        "DigDeeperHandler",
        requires_owner=True,
    ),
    TaskSpec(
        TaskType.CHAT_TURN,
        TaskQueue.CHAT,
        ChatTurnPayload,
        "app.pipeline.handlers.chat_turn",
        "ChatTurnHandler",
        requires_owner=True,
    ),
    TaskSpec(
        TaskType.SYNC_INTEGRATION,
        TaskQueue.TWITTER,
        SyncIntegrationPayload,
        "app.pipeline.handlers.sync_integration",
        "SyncIntegrationHandler",
        dedupe_by_content=True,
        requires_owner=True,
    ),
    TaskSpec(
        TaskType.GENERATE_AUDIO_EPISODE,
        TaskQueue.AUDIO_EPISODE,
        AudioEpisodePayload,
        "app.pipeline.handlers.generate_audio_episode",
        "GenerateAudioEpisodeHandler",
        dedupe_by_content=True,
        requires_owner=True,
    ),
    TaskSpec(
        TaskType.RUN_LLM_TASK,
        TaskQueue.LLM,
        LlmTaskRunPayload,
        "app.pipeline.handlers.run_llm_task",
        "RunLlmTaskHandler",
        requires_owner=True,
    ),
    TaskSpec(
        TaskType.BRIEFING_REFRESH,
        TaskQueue.LLM,
        BriefingRefreshPayload,
        "app.pipeline.handlers.briefing_refresh",
        "BriefingRefreshHandler",
        requires_owner=True,
    ),
    TaskSpec(
        TaskType.DELETE_USER_ACCOUNT,
        TaskQueue.BACKFILL,
        RequiredUserPayload,
        "app.pipeline.handlers.delete_user_account",
        "DeleteUserAccountHandler",
    ),
)

TASK_SPECS: dict[TaskType, TaskSpec] = {spec.task_type: spec for spec in _TASK_SPEC_SEQUENCE}
if len(TASK_SPECS) != len(_TASK_SPEC_SEQUENCE):
    raise ValueError("Duplicate task specifications")


def get_task_spec(task_type: TaskType) -> TaskSpec:
    return TASK_SPECS[task_type]
