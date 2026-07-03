"""Reviewed API contract surface for generated clients."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, Flag, auto

from pydantic import BaseModel

from app.models.api.agent import (
    AgentLibraryFileResponse,
    AgentLibraryManifestResponse,
    AgentOnboardingCompleteRequest,
    AgentOnboardingStartRequest,
    AgentOnboardingStartResponse,
    AgentSearchRequest,
    AgentSearchResponse,
)
from app.models.api.analytics import (
    RecordContentInteractionRequest,
    RecordContentInteractionResponse,
)
from app.models.api.audio_episodes import (
    AudioEpisodeResponse,
    AudioEpisodeShareResponse,
    CustomNarrationCreateRequest,
)
from app.models.api.auth import AccessTokenResponse, RefreshTokenRequest, TokenResponse
from app.models.api.briefing import (
    BriefingDigSearchRequest,
    BriefingDigSearchResponse,
    BriefingDigSearchResult,
    BriefingDigSummarizeRequest,
    BriefingDigSummarizeResponse,
    BriefingIndexResponse,
    BriefingLensResponse,
    BriefingLensSummary,
    BriefingNarrationRequest,
    BriefingReadMarkRequest,
    BriefingReadMarkResponse,
    BriefingRefreshResponse,
    BriefingSegmentDto,
    BriefingSourceDto,
)
from app.models.api.chat import (
    AssistantTurnRequest,
    AssistantTurnResponse,
    ChatSessionDetailDto,
    ChatSessionListResponse,
    CreateChatSessionRequest,
    CreateChatSessionResponse,
    MessageStatusResponse,
    SendChatMessageRequest,
    SendMessageResponse,
    UpdateChatSessionRequest,
)
from app.models.api.cli import (
    CliLinkApproveRequest,
    CliLinkApproveResponse,
    CliLinkPollResponse,
    CliLinkStartRequest,
    CliLinkStartResponse,
)
from app.models.api.content import (
    ContentBodyResponse,
    ContentDetailResponse,
    ContentListResponse,
    MixedSearchResponse,
    NarrationResponse,
    PodcastEpisodeSearchResponse,
    SubmissionStatusListResponse,
)
from app.models.api.content_actions import (
    BadgeStatsResponse,
    BulkMarkReadRequest,
    BulkMarkReadResponse,
    ChatGPTUrlResponse,
    ConvertNewsResponse,
    DownloadMoreRequest,
    DownloadMoreResponse,
    KnowledgeMutationResponse,
    LongFormStatsResponse,
    MarkReadResponse,
    MarkUnreadResponse,
    TweetSuggestionsRequest,
    TweetSuggestionsResponse,
)
from app.models.api.content_discussions import (
    ContentDiscussionResponse,
)
from app.models.api.discovery import (
    DiscoveryAddItemRequest,
    DiscoveryAddItemResponse,
    DiscoveryDismissRequest,
    DiscoveryDismissResponse,
    DiscoveryHistoryResponse,
    DiscoveryRefreshResponse,
    DiscoverySubscribeRequest,
    DiscoverySubscribeResponse,
    DiscoverySuggestionsResponse,
)
from app.models.api.feedback import SubmitFeedbackRequest, SubmitFeedbackResponse
from app.models.api.integrations import (
    DeleteUserLlmIntegrationResponse,
    IntegrationDisconnectResponse,
    UpsertUserLlmIntegrationRequest,
    UserLlmIntegrationResponse,
    UserLlmIntegrationTestResponse,
    XConnectionResponse,
    XOAuthExchangeRequest,
    XOAuthStartRequest,
    XOAuthStartResponse,
)
from app.models.api.jobs import JobStatusResponse
from app.models.api.learning_decks import (
    LearningDeckCreateRequest,
    LearningDeckListResponse,
    LearningDeckShareResponse,
    LearningDeckUrlResponse,
)
from app.models.api.news import ConvertNewsItemResponse
from app.models.api.onboarding import (
    OnboardingAudioDiscoverRequest,
    OnboardingAudioDiscoverResponse,
    OnboardingCompleteRequest,
    OnboardingCompleteResponse,
    OnboardingDiscoveryStatusResponse,
    OnboardingFastDiscoverRequest,
    OnboardingProfileRequest,
    OnboardingProfileResponse,
    OnboardingTutorialResponse,
    OnboardingVoiceParseRequest,
    OnboardingVoiceParseResponse,
)
from app.models.api.openai import AudioTranscriptionHealthResponse, AudioTranscriptionResponse
from app.models.api.scraper_configs import (
    ScraperConfigResponse,
    SubscribeToFeedRequest,
)
from app.models.api.submissions import ContentSubmissionResponse, SubmitContentRequest
from app.models.api.users import UpdateUserProfileRequest
from app.models.contracts import (
    AgentLibraryDocumentVariant,
    AgentSearchResultKind,
    AudioEpisodeKind,
    AudioEpisodeStatus,
    BriefingBlockType,
    BriefingRunKind,
    BriefingTier,
    ChatMessageDisplayType,
    ChatMessageRole,
    CliLinkStatus,
    ContentClassification,
    ContentInteractionType,
    ContentStatus,
    ContentType,
    DeleteStatus,
    DiscussionMode,
    FeedFormat,
    FeedType,
    IntegrationDisconnectStatus,
    KnowledgeMutationStatus,
    LearningDeckRunStatus,
    LearningDeckSourceKind,
    LearningDeckStatus,
    LLMProvider,
    MessageProcessingStatus,
    NarrationTargetType,
    NewsItemStatus,
    NewsItemVisibilityScope,
    OnboardingSelectedSourceType,
    OnboardingSuggestionType,
    OperationStatus,
    SavedSource,
    SubmissionKind,
    SubmissionOutcome,
    SummaryKind,
    SummaryVersion,
    TaskStatus,
    TaskType,
    TweetLength,
    UserLlmProvider,
)


class Target(Flag):
    """Generated-client targets for one contract artifact."""

    IOS = auto()
    CLI = auto()


@dataclass(frozen=True)
class EnumSpec:
    """One enum exposed to generated clients."""

    enum: type[Enum]
    targets: Target
    open: bool
    swift_name: str | None = None
    go_name: str | None = None


@dataclass(frozen=True)
class ModelSpec:
    """One Pydantic model exposed to generated clients."""

    model: type[BaseModel]
    targets: Target
    swift_name: str | None = None
    go_name: str | None = None


CONTRACT_ENUMS: list[EnumSpec] = [
    EnumSpec(ContentType, targets=Target.IOS | Target.CLI, open=True, swift_name="APIContentType"),
    EnumSpec(
        ContentStatus,
        targets=Target.IOS | Target.CLI,
        open=True,
        swift_name="APIContentStatus",
    ),
    EnumSpec(
        ContentClassification,
        targets=Target.IOS | Target.CLI,
        open=False,
        swift_name="APIContentClassification",
    ),
    EnumSpec(TaskType, targets=Target.IOS | Target.CLI, open=True, swift_name="APITaskType"),
    EnumSpec(TaskStatus, targets=Target.IOS | Target.CLI, open=True, swift_name="APITaskStatus"),
    EnumSpec(SummaryKind, targets=Target.IOS | Target.CLI, open=True, swift_name="APISummaryKind"),
    EnumSpec(
        SummaryVersion,
        targets=Target.IOS | Target.CLI,
        open=False,
        swift_name="APISummaryVersion",
    ),
    EnumSpec(SavedSource, targets=Target.IOS | Target.CLI, open=False),
    EnumSpec(OperationStatus, targets=Target.IOS | Target.CLI, open=False),
    EnumSpec(KnowledgeMutationStatus, targets=Target.IOS, open=False),
    EnumSpec(ContentInteractionType, targets=Target.IOS, open=False),
    EnumSpec(NarrationTargetType, targets=Target.IOS, open=False),
    EnumSpec(SubmissionKind, targets=Target.IOS | Target.CLI, open=False),
    EnumSpec(SubmissionOutcome, targets=Target.IOS | Target.CLI, open=True),
    EnumSpec(DiscussionMode, targets=Target.IOS, open=False),
    EnumSpec(FeedType, targets=Target.IOS, open=True),
    EnumSpec(FeedFormat, targets=Target.IOS, open=True),
    EnumSpec(AudioEpisodeKind, targets=Target.IOS, open=True),
    EnumSpec(AudioEpisodeStatus, targets=Target.IOS, open=True),
    EnumSpec(BriefingTier, targets=Target.IOS | Target.CLI, open=False),
    EnumSpec(BriefingBlockType, targets=Target.IOS | Target.CLI, open=False),
    EnumSpec(BriefingRunKind, targets=Target.IOS | Target.CLI, open=False),
    EnumSpec(CliLinkStatus, targets=Target.IOS | Target.CLI, open=True),
    EnumSpec(AgentLibraryDocumentVariant, targets=Target.CLI, open=False),
    EnumSpec(AgentSearchResultKind, targets=Target.CLI, open=True),
    EnumSpec(OnboardingSuggestionType, targets=Target.IOS | Target.CLI, open=True),
    EnumSpec(OnboardingSelectedSourceType, targets=Target.IOS, open=True),
    EnumSpec(IntegrationDisconnectStatus, targets=Target.IOS, open=False),
    EnumSpec(DeleteStatus, targets=Target.IOS, open=False),
    EnumSpec(UserLlmProvider, targets=Target.IOS, open=False),
    EnumSpec(NewsItemVisibilityScope, targets=Target.IOS, open=True),
    EnumSpec(NewsItemStatus, targets=Target.IOS, open=True),
    EnumSpec(LearningDeckSourceKind, targets=Target.IOS, open=True),
    EnumSpec(LearningDeckRunStatus, targets=Target.IOS, open=True),
    EnumSpec(LearningDeckStatus, targets=Target.IOS, open=True),
    EnumSpec(MessageProcessingStatus, targets=Target.IOS, open=True),
    EnumSpec(ChatMessageRole, targets=Target.IOS, open=False),
    EnumSpec(ChatMessageDisplayType, targets=Target.IOS, open=True),
    EnumSpec(LLMProvider, targets=Target.IOS, open=False),
    EnumSpec(TweetLength, targets=Target.IOS, open=False),
]


CONTRACT_UNTYPED_FIELD_ALLOWLIST_BY_CATEGORY: dict[str, frozenset[str]] = {
    "permanent": frozenset(
        {
            "ContentDetailResponse.feed_preview",
            "ContentDetailResponse.longform_artifact",
            "ContentDetailResponse.metadata",
            "ContentDetailResponse.structured_summary",
            "ContentSummaryResponse.feed_preview",
        }
    ),
    "intentional_escape_hatches": frozenset(
        {
            "AgentOnboardingStartRequest.preferences",
            "ContentDiscussionResponse.stats",
            "CreateUserScraperConfig.config",
            "JobStatusResponse.payload",
            "LearningDeckResponse.source_metadata",
            "LlmTaskActionResponse.action_input",
            "LlmTaskActionResponse.action_result",
            "OnboardingSelectedSource.config",
            "RecordContentInteractionRequest.context_data",
            "ScraperConfigResponse.config",
            "UpdateUserScraperConfig.config",
        }
    ),
    "should_shrink": frozenset(),
}

CONTRACT_UNTYPED_FIELD_ALLOWLIST: frozenset[str] = frozenset(
    item
    for category_entries in CONTRACT_UNTYPED_FIELD_ALLOWLIST_BY_CATEGORY.values()
    for item in category_entries
)


CONTRACT_MODELS: list[ModelSpec] = [
    ModelSpec(ContentListResponse, targets=Target.IOS | Target.CLI),
    ModelSpec(ContentDetailResponse, targets=Target.IOS | Target.CLI),
    ModelSpec(ContentBodyResponse, targets=Target.IOS),
    ModelSpec(NarrationResponse, targets=Target.IOS),
    ModelSpec(SubmissionStatusListResponse, targets=Target.IOS | Target.CLI),
    ModelSpec(PodcastEpisodeSearchResponse, targets=Target.IOS),
    ModelSpec(MixedSearchResponse, targets=Target.IOS),
    ModelSpec(DownloadMoreRequest, targets=Target.IOS),
    ModelSpec(DownloadMoreResponse, targets=Target.IOS),
    ModelSpec(BulkMarkReadRequest, targets=Target.IOS | Target.CLI),
    ModelSpec(BulkMarkReadResponse, targets=Target.IOS | Target.CLI),
    ModelSpec(MarkReadResponse, targets=Target.IOS),
    ModelSpec(MarkUnreadResponse, targets=Target.IOS),
    ModelSpec(KnowledgeMutationResponse, targets=Target.IOS),
    ModelSpec(ChatGPTUrlResponse, targets=Target.IOS),
    ModelSpec(ConvertNewsResponse, targets=Target.IOS),
    ModelSpec(BadgeStatsResponse, targets=Target.IOS),
    ModelSpec(LongFormStatsResponse, targets=Target.IOS),
    ModelSpec(TweetSuggestionsRequest, targets=Target.IOS),
    ModelSpec(TweetSuggestionsResponse, targets=Target.IOS),
    ModelSpec(ContentDiscussionResponse, targets=Target.IOS),
    ModelSpec(RecordContentInteractionRequest, targets=Target.IOS),
    ModelSpec(RecordContentInteractionResponse, targets=Target.IOS),
    ModelSpec(CreateChatSessionRequest, targets=Target.IOS),
    ModelSpec(UpdateChatSessionRequest, targets=Target.IOS),
    ModelSpec(SendChatMessageRequest, targets=Target.IOS),
    ModelSpec(ChatSessionDetailDto, targets=Target.IOS),
    ModelSpec(ChatSessionListResponse, targets=Target.IOS),
    ModelSpec(SendMessageResponse, targets=Target.IOS),
    ModelSpec(AssistantTurnRequest, targets=Target.IOS),
    ModelSpec(AssistantTurnResponse, targets=Target.IOS),
    ModelSpec(MessageStatusResponse, targets=Target.IOS),
    ModelSpec(CreateChatSessionResponse, targets=Target.IOS),
    ModelSpec(JobStatusResponse, targets=Target.IOS | Target.CLI),
    ModelSpec(CustomNarrationCreateRequest, targets=Target.IOS),
    ModelSpec(AudioEpisodeResponse, targets=Target.IOS),
    ModelSpec(AudioEpisodeShareResponse, targets=Target.IOS),
    ModelSpec(BriefingIndexResponse, targets=Target.IOS | Target.CLI),
    ModelSpec(BriefingLensSummary, targets=Target.IOS | Target.CLI),
    ModelSpec(BriefingLensResponse, targets=Target.IOS | Target.CLI),
    ModelSpec(BriefingSegmentDto, targets=Target.IOS | Target.CLI),
    ModelSpec(BriefingSourceDto, targets=Target.IOS | Target.CLI),
    ModelSpec(BriefingReadMarkRequest, targets=Target.IOS | Target.CLI),
    ModelSpec(BriefingReadMarkResponse, targets=Target.IOS | Target.CLI),
    ModelSpec(BriefingDigSearchRequest, targets=Target.IOS),
    ModelSpec(BriefingDigSearchResponse, targets=Target.IOS),
    ModelSpec(BriefingDigSearchResult, targets=Target.IOS),
    ModelSpec(BriefingDigSummarizeRequest, targets=Target.IOS),
    ModelSpec(BriefingDigSummarizeResponse, targets=Target.IOS),
    ModelSpec(BriefingNarrationRequest, targets=Target.IOS),
    ModelSpec(BriefingRefreshResponse, targets=Target.IOS | Target.CLI),
    ModelSpec(ScraperConfigResponse, targets=Target.IOS | Target.CLI),
    ModelSpec(SubscribeToFeedRequest, targets=Target.IOS | Target.CLI),
    ModelSpec(DiscoverySuggestionsResponse, targets=Target.IOS),
    ModelSpec(DiscoveryHistoryResponse, targets=Target.IOS),
    ModelSpec(DiscoveryRefreshResponse, targets=Target.IOS),
    ModelSpec(DiscoverySubscribeRequest, targets=Target.IOS),
    ModelSpec(DiscoverySubscribeResponse, targets=Target.IOS),
    ModelSpec(DiscoveryAddItemRequest, targets=Target.IOS),
    ModelSpec(DiscoveryAddItemResponse, targets=Target.IOS),
    ModelSpec(DiscoveryDismissRequest, targets=Target.IOS),
    ModelSpec(DiscoveryDismissResponse, targets=Target.IOS),
    ModelSpec(XOAuthStartRequest, targets=Target.IOS),
    ModelSpec(XOAuthStartResponse, targets=Target.IOS),
    ModelSpec(XOAuthExchangeRequest, targets=Target.IOS),
    ModelSpec(XConnectionResponse, targets=Target.IOS),
    ModelSpec(IntegrationDisconnectResponse, targets=Target.IOS),
    ModelSpec(UserLlmIntegrationResponse, targets=Target.IOS),
    ModelSpec(UpsertUserLlmIntegrationRequest, targets=Target.IOS),
    ModelSpec(UserLlmIntegrationTestResponse, targets=Target.IOS),
    ModelSpec(DeleteUserLlmIntegrationResponse, targets=Target.IOS),
    ModelSpec(OnboardingProfileRequest, targets=Target.IOS),
    ModelSpec(OnboardingProfileResponse, targets=Target.IOS),
    ModelSpec(OnboardingVoiceParseRequest, targets=Target.IOS),
    ModelSpec(OnboardingVoiceParseResponse, targets=Target.IOS),
    ModelSpec(OnboardingAudioDiscoverRequest, targets=Target.IOS),
    ModelSpec(OnboardingAudioDiscoverResponse, targets=Target.IOS),
    ModelSpec(OnboardingFastDiscoverRequest, targets=Target.IOS),
    ModelSpec(OnboardingDiscoveryStatusResponse, targets=Target.IOS | Target.CLI),
    ModelSpec(OnboardingCompleteRequest, targets=Target.IOS),
    ModelSpec(OnboardingCompleteResponse, targets=Target.IOS | Target.CLI),
    ModelSpec(OnboardingTutorialResponse, targets=Target.IOS),
    ModelSpec(LearningDeckCreateRequest, targets=Target.IOS),
    ModelSpec(LearningDeckListResponse, targets=Target.IOS),
    ModelSpec(LearningDeckUrlResponse, targets=Target.IOS),
    ModelSpec(LearningDeckShareResponse, targets=Target.IOS),
    ModelSpec(AudioTranscriptionResponse, targets=Target.IOS),
    ModelSpec(AudioTranscriptionHealthResponse, targets=Target.IOS),
    ModelSpec(TokenResponse, targets=Target.IOS),
    ModelSpec(RefreshTokenRequest, targets=Target.IOS),
    ModelSpec(AccessTokenResponse, targets=Target.IOS),
    ModelSpec(UpdateUserProfileRequest, targets=Target.IOS),
    ModelSpec(SubmitFeedbackRequest, targets=Target.IOS),
    ModelSpec(SubmitFeedbackResponse, targets=Target.IOS),
    ModelSpec(AgentSearchRequest, targets=Target.CLI),
    ModelSpec(AgentSearchResponse, targets=Target.CLI),
    ModelSpec(AgentOnboardingStartRequest, targets=Target.CLI),
    ModelSpec(AgentOnboardingStartResponse, targets=Target.CLI),
    ModelSpec(AgentOnboardingCompleteRequest, targets=Target.CLI),
    ModelSpec(CliLinkStartRequest, targets=Target.CLI),
    ModelSpec(CliLinkStartResponse, targets=Target.CLI),
    ModelSpec(CliLinkApproveRequest, targets=Target.IOS | Target.CLI),
    ModelSpec(CliLinkApproveResponse, targets=Target.IOS | Target.CLI),
    ModelSpec(CliLinkPollResponse, targets=Target.CLI),
    ModelSpec(AgentLibraryManifestResponse, targets=Target.CLI),
    ModelSpec(AgentLibraryFileResponse, targets=Target.CLI),
    ModelSpec(SubmitContentRequest, targets=Target.IOS | Target.CLI),
    ModelSpec(ContentSubmissionResponse, targets=Target.IOS | Target.CLI),
    ModelSpec(ConvertNewsItemResponse, targets=Target.CLI),
]
