//! External provider adapters.
//!
//! This crate intentionally has no dependency on `newsly-db`; provider calls operate on owned
//! work plans and return owned outcomes. Rig and `async-openai` stay private implementation
//! details and never appear in the public API or durable Newsly records.

#![forbid(unsafe_code)]

mod audio_episode;
mod briefing_composition;
mod briefing_dig;
mod content_analysis;
mod content_misc;
mod discussion_fetch;
mod feed_validation;
mod image_generation;
mod media;
mod model;
mod news_item;
mod onboarding_flow;
mod openai_background;
mod openai_transcription;
mod openrouter;
mod public_http;
mod rig_engine;
mod scraping;
mod summarization;
mod token_cipher;
mod x_oauth;
mod x_sync;

pub use audio_episode::{
    AudioEpisodeGateway, AudioEpisodeGatewayConfig, AudioEpisodeGatewayError, AudioEpisodeScript,
    AudioEpisodeSpeaker, AudioEpisodeTurn, GeneratedAudioEpisodeScript, SynthesizedDialogue,
};
pub use briefing_composition::{
    BriefingCompositionBlock, BriefingCompositionGateway, BriefingCompositionGatewayError,
    BriefingCompositionLayout, BriefingCompositionRequest, BriefingCompositionSource,
    BriefingEmbeddingBatch, BriefingFigureAlignment, BriefingFigurePlacement, BriefingLensName,
    BriefingPassageWeight, BriefingSuggestedQuote, GeneratedBriefingLayout,
    GeneratedBriefingLensName,
};
pub use briefing_dig::{
    BriefingDigGateway, BriefingDigGatewayError, BriefingDigSummary, BriefingWebSearchResult,
};
pub use content_analysis::{
    AnalyzedContentType, ContentAnalysisGateway, ContentAnalysisGatewayError,
    ContentAnalysisResult, GeneratedContentAnalysis, InstructionLink, InstructionResult,
};
pub use content_misc::{
    ContentMiscGateway, ContentMiscGatewayError, DiscussionCommentHit, DiscussionLinkHit,
    DiscussionRefreshResult, DiscussionSummaryArtifact, DiscussionSummaryComment,
    DiscussionSummaryLink, DiscussionSummaryTopic, DiscussionThreadHit, FeedDiscoveryHit,
    GeneratedDiscussionSummary, GeneratedTweetSuggestion, GeneratedTweetSuggestions,
    PodcastEpisodeHit,
};
pub use feed_validation::{
    FeedValidationError, FeedValidator, ValidatedFeed, ValidatedFeedFormat, ValidatedSharedItem,
    ValidatedSharedTarget,
};
pub use image_generation::{
    GeneratedImage, GoogleImageAuth, ImageGenerationError, ImageGenerationGateway,
    ImageGenerationGatewayConfig, ImageGenerationUsage, InfographicProvider,
};
pub use media::{
    ApplePodcastResolution, DownloadedMedia, MediaGateway, MediaGatewayConfig, MediaGatewayError,
    YtDlpTarget, is_apple_podcasts_url, is_terminal_ytdlp_error, is_youtube_url,
};
pub use model::{ModelProvider, ModelSpec, ModelSpecError, ProviderCredentials};
pub use news_item::{
    EmbeddingBatch, GeneratedNewsSummary, LinkCandidate, NewsClassification, NewsItemGateway,
    NewsItemGatewayError, NewsSummary, RelevantLink, RelevantLinkCategory, SelectedRelevantLinks,
};
pub use onboarding_flow::{
    OnboardingAudioLane, OnboardingAudioPlan, OnboardingDiscoverySeeds, OnboardingGateway,
    OnboardingGatewayError, OnboardingLaneTarget, OnboardingProfile, OnboardingSuggestionSeed,
    OnboardingVoiceFields,
};
pub use openai_background::{
    BackgroundBuiltInTools, BackgroundProviderError, BackgroundReasoningSummary,
    BackgroundResponseStatus, BackgroundSource, BackgroundUsage, BoxBackgroundResponseFuture,
    OpenAiBackgroundGateway, OpenAiBackgroundRequest, OpenAiBackgroundResponses,
    OpenAiBackgroundResult, OpenAiGatewayError,
};
pub use openai_transcription::{
    OpenAiTranscriptionError, OpenAiTranscriptionGateway, TranscriptionResult,
};
pub use openrouter::{OpenRouterPrivacyPolicy, OpenRouterRoutingError};
pub use rig_engine::{RigAgentEngine, RigAgentEngineError};
pub use scraping::{
    AggregatorKey, FeedScrapeTarget, RedditScrapeTarget, ScrapeFailure, ScrapeGateway,
    ScrapeGatewayError, ScrapeProviderOutcome, ScrapedContentItem, ScrapedItem, ScrapedNewsItem,
};
pub use summarization::{
    ArtifactAsk, ArtifactKeyPoint, ArtifactQuote, ArtifactType, FeedPreview,
    GeneratedLongformSummary, LongformArtifactBody, LongformArtifactEnvelope, SelectionTrace,
    SourceContext, SummarizationGateway, SummarizationGatewayError, SummarizationSource,
};
pub use token_cipher::{IntegrationTokenCipher, IntegrationTokenCipherError};
pub use x_oauth::{
    X_DEFAULT_SCOPES, XAuthenticatedUser, XOAuthGateway, XOAuthGatewayError, XOAuthToken,
};
pub use x_sync::{
    XBookmarksPage, XLookupGateway, XSyncGateway, XSyncGatewayError, XSyncToken, XSyncUser, XTweet,
};
