"""Canonical domain contracts and enums shared across backend surfaces."""

from __future__ import annotations

from enum import IntEnum, StrEnum


class ContentType(StrEnum):
    """Supported content types in storage and API contracts."""

    ARTICLE = "article"
    PODCAST = "podcast"
    NEWS = "news"
    INSIGHT_REPORT = "insight_report"
    UNKNOWN = "unknown"


class ContentStatus(StrEnum):
    """Lifecycle statuses for content processing."""

    NEW = "new"
    PENDING = "pending"
    PROCESSING = "processing"
    AWAITING_IMAGE = "awaiting_image"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class ContentClassification(StrEnum):
    """User-visible read prioritization classification."""

    TO_READ = "to_read"
    SKIP = "skip"


class SavedSource(StrEnum):
    """Source that placed content in the user's saved library."""

    KNOWLEDGE = "knowledge"
    X_BOOKMARK = "x_bookmark"


class TaskType(StrEnum):
    """Queue task types."""

    SCRAPE = "scrape"
    BACKFILL_FEEDS = "backfill_feeds"
    ANALYZE_URL = "analyze_url"
    PROCESS_CONTENT = "process_content"
    ENRICH_NEWS_ITEM_ARTICLE = "enrich_news_item_article"
    PROCESS_NEWS_ITEM = "process_news_item"
    PROCESS_PODCAST_MEDIA = "process_podcast_media"
    DOWNLOAD_TWEET_VIDEO_AUDIO = "download_tweet_video_audio"
    TRANSCRIBE_TWEET_VIDEO = "transcribe_tweet_video"
    SUMMARIZE = "summarize"
    FETCH_NEWS_ITEM_DISCUSSION = "fetch_news_item_discussion"
    GENERATE_IMAGE = "generate_image"
    DISCOVER_FEEDS = "discover_feeds"
    ONBOARDING_DISCOVER = "onboarding_discover"
    DIG_DEEPER = "dig_deeper"
    CHAT_TURN = "chat_turn"
    SYNC_INTEGRATION = "sync_integration"
    GENERATE_AUDIO_EPISODE = "generate_audio_episode"
    RUN_LLM_TASK = "run_llm_task"
    BRIEFING_REFRESH = "briefing_refresh"
    SYNC_AGENT_DATA = "sync_agent_data"
    INDEX_AGENT_DATA = "index_agent_data"
    BACKFILL_AGENT_DATA = "backfill_agent_data"
    RECONCILE_AGENT_DATA = "reconcile_agent_data"
    DELETE_USER_ACCOUNT = "delete_user_account"


class AgentDataBackfillStage(StrEnum):
    """Finite stages in the bounded per-user corpus backfill."""

    KNOWLEDGE = "knowledge"
    CONTENT = "content"
    NEWS = "news"
    CHATS = "chats"
    BRIEFINGS = "briefings"


class TaskQueue(StrEnum):
    """Queue partitions used by workers."""

    CONTENT = "content"
    MEDIA = "media"
    AUDIO_EPISODE = "audio_episode"
    IMAGE = "image"
    ONBOARDING = "onboarding"
    BACKFILL = "backfill"
    DISCUSSION = "discussion"
    TWITTER = "twitter"
    CHAT = "chat"
    LLM = "llm"


class TaskStatus(StrEnum):
    """Task execution status values."""

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class LlmTaskKind(StrEnum):
    """High-level product family for one LLM task run."""

    SHARE_ACTION = "share_action"
    LEARNING_DECK = "learning_deck"
    ARTICLE_CHAT = "article_chat"
    ASSISTANT_CHAT = "assistant_chat"
    GENERIC = "generic"


class LlmTaskMode(StrEnum):
    """Specific mode inside an LLM task family."""

    ADD_CONTENT = "add_content"
    ADD_TO_BRIEFING = "add_to_briefing"
    ADD_LINKS = "add_links"
    ADD_FEED = "add_feed"
    CHAT = "chat"
    PRESENTATION = "presentation"
    BOOKMARK_ONLY = "bookmark_only"
    ARTICLE_CHAT = "article_chat"
    CONTEXTUAL_ASSISTANT = "contextual_assistant"
    LEARNING_DECK_PRESENTATION = "learning_deck_presentation"
    GENERIC = "generic"


class LlmTaskStatus(StrEnum):
    """Execution status for the generic LLM task ledger."""

    QUEUED = "queued"
    PREPARING = "preparing"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    APPLYING = "applying"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class LlmWorkflowState(StrEnum):
    """Workflow state exposed by host-mediated LLM tasks."""

    QUEUED = "queued"
    PREPARING = "preparing"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    APPLYING = "applying"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class LlmTaskActionStatus(StrEnum):
    """Lifecycle status for one host-mediated action requested by an LLM task."""

    PROPOSED = "proposed"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    APPLYING = "applying"
    APPLIED = "applied"
    REJECTED = "rejected"
    FAILED = "failed"
    CANCELLED = "cancelled"


class LlmTaskApprovalPolicy(StrEnum):
    """Action approval policy values accepted by workflow configuration."""

    AUTO_APPLY = "auto_apply"
    APPROVAL_REQUIRED = "approval_required"
    DRY_RUN = "dry_run"


class SummaryKind(StrEnum):
    """Canonical summary kind discriminators."""

    LONG_STRUCTURED = "long_structured"
    LONG_INTERLEAVED = "long_interleaved"
    LONG_BULLETS = "long_bullets"
    LONG_EDITORIAL_NARRATIVE = "long_editorial_narrative"
    SHORT_NEWS = "short_news"
    LONGFORM_ARTIFACT = "longform_artifact"


class SummaryVersion(IntEnum):
    """Supported summary schema versions."""

    V1 = 1
    V2 = 2


class OperationStatus(StrEnum):
    """Common success status used by mutation-style API responses."""

    SUCCESS = "success"


class KnowledgeMutationStatus(StrEnum):
    """Status values for Knowledge save/remove mutations."""

    SUCCESS = "success"
    NOT_FOUND = "not_found"


class ContentInteractionType(StrEnum):
    """Client-recorded content interaction kinds."""

    OPENED = "opened"


class NarrationTargetType(StrEnum):
    """Supported narration target families."""

    CONTENT = "content"


class SubmissionKind(StrEnum):
    """Semantic kind for a user-visible submission status row."""

    CONTENT = "content"
    FEED_SUBSCRIPTION = "feed_subscription"
    LEARNING_DECK = "learning_deck"


class SubmissionOutcome(StrEnum):
    """User-facing outcome for a submitted content or feed item."""

    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    NO_ACTION = "no_action"
    FAILED = "failed"
    SKIPPED = "skipped"
    SUBSCRIBED = "subscribed"
    ALREADY_SUBSCRIBED = "already_subscribed"
    FEED_NOT_FOUND = "feed_not_found"
    FEED_FETCH_FAILED = "feed_fetch_failed"
    FEED_SUBSCRIPTION_FAILED = "feed_subscription_failed"


class DiscussionMode(StrEnum):
    """Shape of discussion data returned for a content item."""

    NONE = "none"
    COMMENTS = "comments"
    DISCUSSION_LIST = "discussion_list"


class FeedType(StrEnum):
    """Supported feed/source categories exposed in client contracts."""

    ATOM = "atom"
    SUBSTACK = "substack"
    PODCAST_RSS = "podcast_rss"
    YOUTUBE = "youtube"


class FeedFormat(StrEnum):
    """Supported feed document formats."""

    RSS = "rss"
    ATOM = "atom"


class FeedSubscriptionOutcome(StrEnum):
    """Idempotent result of subscribing to a feed."""

    CREATED = "created"
    REACTIVATED = "reactivated"
    ALREADY_SUBSCRIBED = "already_subscribed"


class AudioEpisodeKind(StrEnum):
    """Generated audio episode categories."""

    FAST_NEWS_DIGEST = "fast_news_digest"
    CONTENT_COUNCIL_DISCUSSION = "content_council_discussion"
    NEWS_ITEM_DISCUSSION = "news_item_discussion"
    CUSTOM_NARRATION = "custom_narration"
    BRIEFING_NARRATION = "briefing_narration"


class AudioEpisodeStatus(StrEnum):
    """Generated audio episode lifecycle statuses."""

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class BriefingTier(StrEnum):
    """Top-level briefing lens tier."""

    AUDIO = "audio"
    LONGFORM = "longform"
    NEWS = "news"


class BriefingFirstRunPhase(StrEnum):
    """Visible state of the temporary Start Here briefing."""

    ACTIVE = "active"
    READY = "ready"
    WAITING_FOR_CONTENT = "waiting_for_content"


class BriefingFirstRunSourceOutcome(StrEnum):
    """Terminal result of processing one first-edition source."""

    PROCESSED = "processed"
    UNAVAILABLE = "unavailable"


class ReadingExperience(StrEnum):
    """Primary reading surface, retaining Classic for fallback compatibility."""

    CLASSIC = "classic"
    BRIEFING = "briefing"


class BriefingBlockType(StrEnum):
    """Briefing document block kinds."""

    PASSAGE = "passage"
    FIGURE = "figure"
    PULLQUOTE = "pullquote"


class BriefingFigurePlacement(StrEnum):
    """Editorial size hint for a Briefing figure."""

    INSET = "inset"
    FULL = "full"


class BriefingFigureAlignment(StrEnum):
    """Horizontal edge used by an inline Briefing figure."""

    LEFT = "left"
    RIGHT = "right"


class BriefingRunKind(StrEnum):
    """Inline run kinds inside server-normalized briefing prose."""

    TEXT = "text"
    SOURCE_LINK = "source_link"
    INSIGHT = "insight"


class CliLinkStatus(StrEnum):
    """Lifecycle statuses for CLI/mobile link sessions."""

    PENDING = "pending"
    APPROVED = "approved"
    CLAIMED = "claimed"
    EXPIRED = "expired"


class AgentLibraryDocumentVariant(StrEnum):
    """Available CLI library document variants."""

    SOURCE = "source"
    SUMMARY = "summary"


class AgentSearchResultKind(StrEnum):
    """Search result family for machine-facing agent search."""

    WEB = "web"
    PODCAST = "podcast"


class OnboardingSuggestionType(StrEnum):
    """Source types suggested during onboarding discovery."""

    SUBSTACK = "substack"
    ATOM = "atom"
    PODCAST_RSS = "podcast_rss"
    REDDIT = "reddit"


class OnboardingSelectedSourceType(StrEnum):
    """Source types the onboarding completion endpoint can subscribe to."""

    SUBSTACK = "substack"
    ATOM = "atom"
    PODCAST_RSS = "podcast_rss"


class IntegrationDisconnectStatus(StrEnum):
    """Status returned after disconnecting an integration."""

    DISCONNECTED = "disconnected"


class DeleteStatus(StrEnum):
    """Status returned after deleting a resource."""

    DELETED = "deleted"


class UserLlmProvider(StrEnum):
    """User-configurable LLM providers accepted by integration endpoints."""

    ANTHROPIC = "anthropic"
    OPENAI = "openai"


class NewsItemVisibilityScope(StrEnum):
    """Audience visibility for one short-form news item."""

    GLOBAL = "global"
    USER = "user"


class NewsItemStatus(StrEnum):
    """Lifecycle status for short-form news items."""

    NEW = "new"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"


class LearningDeckSourceKind(StrEnum):
    """Supported source kinds for Learning Deck generation."""

    CONTENT = "content"
    GITHUB_REPO = "github_repo"


class LearningDeckRunStatus(StrEnum):
    """Lifecycle statuses for one Learning Deck generation run."""

    QUEUED = "queued"
    PREPARING = "preparing"
    GENERATING = "generating"
    VALIDATING = "validating"
    PUBLISHING = "publishing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class LearningDeckStatus(StrEnum):
    """Client-facing Learning Deck status values."""

    READY = "ready"
    QUEUED = LearningDeckRunStatus.QUEUED.value
    PREPARING = LearningDeckRunStatus.PREPARING.value
    GENERATING = LearningDeckRunStatus.GENERATING.value
    VALIDATING = LearningDeckRunStatus.VALIDATING.value
    PUBLISHING = LearningDeckRunStatus.PUBLISHING.value
    COMPLETED = LearningDeckRunStatus.COMPLETED.value
    FAILED = LearningDeckRunStatus.FAILED.value
    CANCELLED = LearningDeckRunStatus.CANCELLED.value


class MessageProcessingStatus(StrEnum):
    """Processing status for async chat messages."""

    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class ChatMessageRole(StrEnum):
    """Role of a chat message."""

    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


class ChatMessageDisplayType(StrEnum):
    """Display type for a chat message row."""

    MESSAGE = "message"
    PROCESS_SUMMARY = "process_summary"


class LLMProvider(StrEnum):
    """Supported LLM providers."""

    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    CEREBRAS = "cerebras"
    OPENROUTER = "openrouter"
    DEEP_RESEARCH = "deep_research"


class TweetLength(StrEnum):
    """Tweet length preference."""

    SHORT = "short"  # 100-180 chars - concise, punchy
    MEDIUM = "medium"  # 180-280 chars - balanced
    LONG = "long"  # 280-400 chars - detailed
