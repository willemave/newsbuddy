//! Public wire types shared by Newsly HTTP services and contract tooling.

#![forbid(unsafe_code)]

mod agent;
mod api_keys;
mod audio_episodes;
mod auth;
mod briefing;
mod chat;
mod chat_council;
mod cli;
mod client_compat;
mod content_bodies;
mod content_misc;
mod content_read;
mod content_submission;
mod discussions;
mod error;
mod health;
mod integrations;
mod jobs;
mod learning_decks;
mod llm_tasks;
mod mutations;
mod news_read;
mod onboarding;
mod onboarding_flow;
mod openai;
mod scraper_configs;
mod share_actions;
mod stats;
mod users;

pub use agent::{
    AgentLibraryDocumentResponse, AgentLibraryDocumentVariant, AgentLibraryFileResponse,
    AgentLibraryManifestResponse, AgentSearchRequest, AgentSearchResponse, AgentSearchResultKind,
    AgentSearchResultResponse,
};
pub use api_keys::{ApiKeyCreateResponse, ApiKeySummaryResponse};
pub use audio_episodes::{
    AudioEpisodeDelivery, AudioEpisodeDeliveryQuery, AudioEpisodeListQuery,
    AudioEpisodeShareResponse, CUSTOM_NARRATION_MAX_SOURCES, CustomNarrationCreateRequest,
};
pub use auth::{
    AccessTokenResponse, AdminLoginRequest, AdminLoginResponse, AppleSignInRequest,
    DebugUserSessionRequest, DeleteAccountRequest, DeleteAccountResponse, RefreshTokenRequest,
    TokenResponse,
};
pub use briefing::{
    AudioEpisodeKind, AudioEpisodeResponse, AudioEpisodeStatus, BRIEFING_DIG_FRAGMENT_MAX_LENGTH,
    BriefingBlockDto, BriefingBlockType, BriefingDigSearchRequest, BriefingDigSearchResponse,
    BriefingDigSearchResult, BriefingDigSummarizeRequest, BriefingDigSummarizeResponse,
    BriefingDiscussionDto, BriefingFigureAlignment, BriefingFigurePlacement, BriefingFirstRunPhase,
    BriefingFirstRunProgress, BriefingFirstRunSourceOutcome, BriefingFirstRunSourceProgress,
    BriefingIndexResponse, BriefingLensResponse, BriefingLensSummary, BriefingNarrationRequest,
    BriefingNarrationResponse, BriefingParagraphDto, BriefingReadMarkRequest,
    BriefingReadMarkResponse, BriefingRefreshResponse, BriefingRunDto, BriefingRunKind,
    BriefingSegmentDto, BriefingSourceDto, BriefingTier,
};
pub use chat::{
    AssistantFeedOption, AssistantScreenContextDto, AssistantTurnRequest, AssistantTurnResponse,
    ChatMessageDisplayType, ChatMessageDto, ChatMessageRole, ChatSessionDetailDto,
    ChatSessionListResponse, ChatSessionSummaryDto, ChatToolProgressDto, CouncilCandidate,
    CreateChatSessionRequest, CreateChatSessionResponse, FeedFormat, FeedType, LlmProvider,
    MessageProcessingStatus, MessageStatusResponse, SendChatMessageRequest, SendMessageResponse,
    UpdateChatSessionRequest,
};
pub use chat_council::{CouncilRetryRequest, CouncilSelectRequest, CouncilStartRequest};
pub use cli::{
    CliLinkApproveRequest, CliLinkApproveResponse, CliLinkPollResponse, CliLinkStartRequest,
    CliLinkStartResponse, CliLinkStatus,
};
pub use client_compat::{
    NewsItemStatus, NewsItemVisibilityScope, SummaryVersion, TaskStatus, TaskType,
};
pub use content_bodies::{ChatGptUrlResponse, ContentBodyResponse};
pub use content_misc::{
    ConvertNewsItemResponse, ConvertNewsResponse, DownloadMoreRequest, DownloadMoreResponse,
    MixedSearchFeedResultResponse, MixedSearchResponse, NarrationResponse, NarrationTargetType,
    PodcastEpisodeSearchResponse, PodcastEpisodeSearchResultResponse, SubmissionContentResult,
    SubmissionFeedInitialDownloadResponse, SubmissionFeedSubscriptionResponse,
    SubmissionFeedSubscriptionResult, SubmissionKind, SubmissionLearningDeckResult,
    SubmissionNoActionResult, SubmissionOutcome, SubmissionResult, SubmissionStatusListResponse,
    SubmissionStatusResponse, TweetLength, TweetSuggestion, TweetSuggestionsRequest,
    TweetSuggestionsResponse,
};
pub use content_read::{
    ContentClassification, ContentDetailResponse, ContentListResponse, ContentSummaryBulletPoint,
    ContentSummaryQuote, ContentSummaryResponse, DetectedFeed, PaginationMetadata, SavedSource,
    SummaryKind,
};
pub use content_submission::{
    ContentStatus, ContentSubmissionResponse, ContentType, SubmitContentRequest,
    UnknownContentValue,
};
pub use discussions::{
    ContentDiscussionResponse, DiscussionCommentResponse, DiscussionGroupResponse,
    DiscussionItemResponse, DiscussionLinkResponse, DiscussionMode,
    DiscussionSummaryCommentResponse, DiscussionSummaryLinkResponse, DiscussionSummaryResponse,
    DiscussionSummaryTopicResponse,
};
pub use error::ErrorEnvelope;
pub use health::{HealthChecks, HealthResponse, HealthStatus};
pub use integrations::{
    DeleteStatus, DeleteUserLlmIntegrationResponse, IntegrationDisconnectResponse,
    IntegrationDisconnectStatus, UpsertUserLlmIntegrationRequest, UserLlmIntegrationResponse,
    UserLlmIntegrationTestResponse, UserLlmProvider, XConnectionResponse, XOAuthExchangeRequest,
    XOAuthStartRequest, XOAuthStartResponse,
};
pub use jobs::JobStatusResponse;
pub use learning_decks::{
    LearningDeckCreateRequest, LearningDeckListResponse, LearningDeckResponse,
    LearningDeckRunResponse, LearningDeckRunStatus, LearningDeckShareResponse,
    LearningDeckSourceKind, LearningDeckStatus, LearningDeckTimelineEntry, LearningDeckUrlResponse,
};
pub use llm_tasks::{
    LlmTaskActionListResponse, LlmTaskActionRejectRequest, LlmTaskActionResponse,
    LlmTaskActionStatus, LlmTaskApprovalPolicy,
};
pub use mutations::{
    BulkMarkReadRequest, BulkMarkReadResponse, ContentInteractionType, KnowledgeMutationResponse,
    KnowledgeMutationStatus, MarkReadResponse, MarkUnreadResponse, OperationStatus,
    RecordContentInteractionRequest, RecordContentInteractionResponse, SubmitFeedbackRequest,
    SubmitFeedbackResponse,
};
pub use news_read::{NewsItemDetailResponse, NewsItemListResponse, NewsItemSummaryResponse};
pub use onboarding::{
    OnboardingDiscoveryLaneStatus, OnboardingDiscoveryStatusResponse,
    OnboardingFastDiscoverResponse, OnboardingSuggestion, OnboardingSuggestionType,
    OnboardingTutorialResponse,
};
pub use onboarding_flow::{
    AgentOnboardingCompleteRequest, AgentOnboardingStartRequest, AgentOnboardingStartResponse,
    AgentOnboardingSuggestionsResponse, OnboardingAudioDiscoverRequest,
    OnboardingAudioDiscoverResponse, OnboardingAudioLanePreview,
    OnboardingAudioLanePreviewResponse, OnboardingAudioLaneTarget, OnboardingCompleteRequest,
    OnboardingCompleteResponse, OnboardingFastDiscoverRequest, OnboardingProfileRequest,
    OnboardingProfileResponse, OnboardingSelectedAggregator, OnboardingVoiceParseRequest,
    OnboardingVoiceParseResponse,
};
pub use openai::{AudioTranscriptionHealthResponse, AudioTranscriptionResponse};
pub use scraper_configs::{
    CreateUserScraperConfig, FeedSubscriptionOutcome, ScraperConfigResponse,
    ScraperConfigStatsResponse, ScraperType, SubscribeToFeedRequest, UpdateUserScraperConfig,
};
pub use share_actions::{
    LlmTaskMode, LlmTaskStatus, ShareActionAgentResult, ShareActionBriefingTarget,
    ShareActionCandidate, ShareActionChatCandidate, ShareActionCreateRequest,
    ShareActionPresentationCandidate, ShareActionResponse,
};
pub use stats::{
    BadgeStatsResponse, LongFormStatsResponse, ProcessingCountResponse, UnreadCountsResponse,
};
pub use users::{
    CouncilPersonaConfig, CouncilPersonaInput, ReadingExperience, UpdateUserProfileRequest,
    UserResponse,
};
