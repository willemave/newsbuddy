"""Reviewed API contract surface for generated clients."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, Flag, auto

from pydantic import BaseModel

from app.models.api.agent import (
    AgentLibraryDocumentResponse,
    AgentLibraryFileResponse,
    AgentLibraryManifestResponse,
    AgentOnboardingCompleteRequest,
    AgentOnboardingStartRequest,
    AgentOnboardingStartResponse,
    AgentSearchRequest,
    AgentSearchResponse,
    AgentSearchResultResponse,
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
from app.models.api.chat import (
    AssistantScreenContextDto,
    AssistantTurnRequest,
    AssistantTurnResponse,
    ChatMessageDto,
    ChatSessionDetailDto,
    ChatSessionListResponse,
    ChatSessionSummaryDto,
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
    ContentSummaryResponse,
    DetectedFeed,
    MixedSearchFeedResultResponse,
    MixedSearchResponse,
    NarrationResponse,
    PodcastEpisodeSearchResponse,
    PodcastEpisodeSearchResultResponse,
    SubmissionFeedInitialDownloadResponse,
    SubmissionFeedSubscriptionResponse,
    SubmissionStatusListResponse,
    SubmissionStatusResponse,
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
    ProcessingCountResponse,
    TweetSuggestion,
    TweetSuggestionsRequest,
    TweetSuggestionsResponse,
    UnreadCountsResponse,
)
from app.models.api.content_discussions import (
    ContentDiscussionResponse,
    DiscussionCommentResponse,
    DiscussionGroupResponse,
    DiscussionItemResponse,
    DiscussionLinkResponse,
)
from app.models.api.discovery import (
    DiscoveryAddItemRequest,
    DiscoveryAddItemResponse,
    DiscoveryDismissRequest,
    DiscoveryDismissResponse,
    DiscoveryHistoryResponse,
    DiscoveryRefreshResponse,
    DiscoveryRunSuggestions,
    DiscoverySubscribeRequest,
    DiscoverySubscribeResponse,
    DiscoverySuggestionResponse,
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
    LearningDeckResponse,
    LearningDeckRunResponse,
    LearningDeckShareResponse,
    LearningDeckTimelineEntry,
    LearningDeckUrlResponse,
)
from app.models.api.news import ConvertNewsItemResponse
from app.models.api.onboarding import (
    OnboardingAudioDiscoverRequest,
    OnboardingAudioDiscoverResponse,
    OnboardingCompleteRequest,
    OnboardingCompleteResponse,
    OnboardingDiscoveryLaneStatus,
    OnboardingDiscoveryStatusResponse,
    OnboardingFastDiscoverRequest,
    OnboardingFastDiscoverResponse,
    OnboardingProfileRequest,
    OnboardingProfileResponse,
    OnboardingSelectedAggregator,
    OnboardingSelectedSource,
    OnboardingSuggestion,
    OnboardingTutorialResponse,
    OnboardingVoiceParseRequest,
    OnboardingVoiceParseResponse,
)
from app.models.api.openai import AudioTranscriptionHealthResponse, AudioTranscriptionResponse
from app.models.api.pagination import PaginationMetadata
from app.models.api.scraper_configs import (
    ScraperConfigResponse,
    ScraperConfigStatsResponse,
    SubscribeToFeedRequest,
)
from app.models.api.submissions import ContentSubmissionResponse, SubmitContentRequest
from app.models.api.users import UpdateUserProfileRequest, UserResponse
from app.models.contracts import (
    AgentLibraryDocumentVariant,
    AgentSearchResultKind,
    AudioEpisodeKind,
    AudioEpisodeStatus,
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
from app.models.domain.chat_render import AssistantFeedOption, CouncilCandidate
from app.models.domain.user_profile import CouncilPersonaConfig


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
    "should_shrink": frozenset(
        {
            "ContentDiscussionResponse.stats",
            "ContentDiscussionResponse.summary",
        }
    ),
}

CONTRACT_UNTYPED_FIELD_ALLOWLIST: frozenset[str] = frozenset(
    item
    for category_entries in CONTRACT_UNTYPED_FIELD_ALLOWLIST_BY_CATEGORY.values()
    for item in category_entries
)


CONTRACT_MODELS: list[ModelSpec] = [
    ModelSpec(PaginationMetadata, targets=Target.IOS | Target.CLI),
    ModelSpec(ContentSummaryResponse, targets=Target.IOS | Target.CLI),
    ModelSpec(ContentListResponse, targets=Target.IOS | Target.CLI),
    ModelSpec(ContentDetailResponse, targets=Target.IOS | Target.CLI),
    ModelSpec(ContentBodyResponse, targets=Target.IOS),
    ModelSpec(DetectedFeed, targets=Target.IOS | Target.CLI),
    ModelSpec(NarrationResponse, targets=Target.IOS),
    ModelSpec(SubmissionFeedInitialDownloadResponse, targets=Target.IOS | Target.CLI),
    ModelSpec(SubmissionFeedSubscriptionResponse, targets=Target.IOS | Target.CLI),
    ModelSpec(SubmissionStatusResponse, targets=Target.IOS | Target.CLI),
    ModelSpec(SubmissionStatusListResponse, targets=Target.IOS | Target.CLI),
    ModelSpec(PodcastEpisodeSearchResultResponse, targets=Target.IOS),
    ModelSpec(PodcastEpisodeSearchResponse, targets=Target.IOS),
    ModelSpec(MixedSearchFeedResultResponse, targets=Target.IOS),
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
    ModelSpec(UnreadCountsResponse, targets=Target.IOS),
    ModelSpec(ProcessingCountResponse, targets=Target.IOS),
    ModelSpec(BadgeStatsResponse, targets=Target.IOS),
    ModelSpec(LongFormStatsResponse, targets=Target.IOS),
    ModelSpec(TweetSuggestion, targets=Target.IOS),
    ModelSpec(TweetSuggestionsRequest, targets=Target.IOS),
    ModelSpec(TweetSuggestionsResponse, targets=Target.IOS),
    ModelSpec(DiscussionCommentResponse, targets=Target.IOS),
    ModelSpec(DiscussionItemResponse, targets=Target.IOS),
    ModelSpec(DiscussionGroupResponse, targets=Target.IOS),
    ModelSpec(DiscussionLinkResponse, targets=Target.IOS),
    ModelSpec(ContentDiscussionResponse, targets=Target.IOS),
    ModelSpec(AssistantFeedOption, targets=Target.IOS),
    ModelSpec(CouncilCandidate, targets=Target.IOS),
    ModelSpec(AssistantScreenContextDto, targets=Target.IOS),
    ModelSpec(RecordContentInteractionRequest, targets=Target.IOS),
    ModelSpec(RecordContentInteractionResponse, targets=Target.IOS),
    ModelSpec(CreateChatSessionRequest, targets=Target.IOS),
    ModelSpec(UpdateChatSessionRequest, targets=Target.IOS),
    ModelSpec(SendChatMessageRequest, targets=Target.IOS),
    ModelSpec(ChatMessageDto, targets=Target.IOS),
    ModelSpec(ChatSessionSummaryDto, targets=Target.IOS),
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
    ModelSpec(ScraperConfigStatsResponse, targets=Target.IOS | Target.CLI),
    ModelSpec(ScraperConfigResponse, targets=Target.IOS | Target.CLI),
    ModelSpec(SubscribeToFeedRequest, targets=Target.IOS | Target.CLI),
    ModelSpec(DiscoverySuggestionResponse, targets=Target.IOS),
    ModelSpec(DiscoverySuggestionsResponse, targets=Target.IOS),
    ModelSpec(DiscoveryRunSuggestions, targets=Target.IOS),
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
    ModelSpec(OnboardingDiscoveryLaneStatus, targets=Target.IOS | Target.CLI),
    ModelSpec(OnboardingSuggestion, targets=Target.IOS | Target.CLI),
    ModelSpec(OnboardingSelectedSource, targets=Target.IOS),
    ModelSpec(OnboardingSelectedAggregator, targets=Target.IOS | Target.CLI),
    ModelSpec(OnboardingAudioDiscoverRequest, targets=Target.IOS),
    ModelSpec(OnboardingAudioDiscoverResponse, targets=Target.IOS),
    ModelSpec(OnboardingFastDiscoverRequest, targets=Target.IOS),
    ModelSpec(OnboardingFastDiscoverResponse, targets=Target.IOS | Target.CLI),
    ModelSpec(OnboardingDiscoveryStatusResponse, targets=Target.IOS | Target.CLI),
    ModelSpec(OnboardingCompleteRequest, targets=Target.IOS),
    ModelSpec(OnboardingCompleteResponse, targets=Target.IOS | Target.CLI),
    ModelSpec(OnboardingTutorialResponse, targets=Target.IOS),
    ModelSpec(LearningDeckCreateRequest, targets=Target.IOS),
    ModelSpec(LearningDeckTimelineEntry, targets=Target.IOS),
    ModelSpec(LearningDeckRunResponse, targets=Target.IOS),
    ModelSpec(LearningDeckResponse, targets=Target.IOS),
    ModelSpec(LearningDeckListResponse, targets=Target.IOS),
    ModelSpec(LearningDeckUrlResponse, targets=Target.IOS),
    ModelSpec(LearningDeckShareResponse, targets=Target.IOS),
    ModelSpec(AudioTranscriptionResponse, targets=Target.IOS),
    ModelSpec(AudioTranscriptionHealthResponse, targets=Target.IOS),
    ModelSpec(CouncilPersonaConfig, targets=Target.IOS),
    ModelSpec(TokenResponse, targets=Target.IOS),
    ModelSpec(RefreshTokenRequest, targets=Target.IOS),
    ModelSpec(AccessTokenResponse, targets=Target.IOS),
    ModelSpec(UserResponse, targets=Target.IOS),
    ModelSpec(UpdateUserProfileRequest, targets=Target.IOS),
    ModelSpec(SubmitFeedbackRequest, targets=Target.IOS),
    ModelSpec(SubmitFeedbackResponse, targets=Target.IOS),
    ModelSpec(AgentSearchRequest, targets=Target.CLI),
    ModelSpec(AgentSearchResultResponse, targets=Target.CLI),
    ModelSpec(AgentSearchResponse, targets=Target.CLI),
    ModelSpec(AgentOnboardingStartRequest, targets=Target.CLI),
    ModelSpec(AgentOnboardingStartResponse, targets=Target.CLI),
    ModelSpec(AgentOnboardingCompleteRequest, targets=Target.CLI),
    ModelSpec(CliLinkStartRequest, targets=Target.CLI),
    ModelSpec(CliLinkStartResponse, targets=Target.CLI),
    ModelSpec(CliLinkApproveRequest, targets=Target.IOS | Target.CLI),
    ModelSpec(CliLinkApproveResponse, targets=Target.IOS | Target.CLI),
    ModelSpec(CliLinkPollResponse, targets=Target.CLI),
    ModelSpec(AgentLibraryDocumentResponse, targets=Target.CLI),
    ModelSpec(AgentLibraryManifestResponse, targets=Target.CLI),
    ModelSpec(AgentLibraryFileResponse, targets=Target.CLI),
    ModelSpec(SubmitContentRequest, targets=Target.IOS | Target.CLI),
    ModelSpec(ContentSubmissionResponse, targets=Target.IOS | Target.CLI),
    ModelSpec(ConvertNewsItemResponse, targets=Target.CLI),
]
