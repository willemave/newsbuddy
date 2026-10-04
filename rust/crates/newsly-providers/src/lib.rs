//! External provider adapters.
//!
//! This crate intentionally has no dependency on `newsly-db`; provider calls operate on owned
//! work plans and return owned outcomes. Rig and `async-openai` stay private implementation
//! details and never appear in the public API or durable Newsly records.

#![forbid(unsafe_code)]

mod agent_support;
mod audio_episode;
mod briefing_composition;
mod briefing_dig;
mod content_analysis;
mod content_misc;
mod discussion_fetch;
mod elevenlabs;
mod exa;
mod feed_validation;
mod image_generation;
mod media;
mod model;
mod news_item;
mod object_storage;
mod onboarding_flow;
mod openai_background;
mod openai_transcription;
mod openrouter;
mod openrouter_embeddings;
mod provider_env;
mod public_http;
mod rig_engine;
mod scraping;
mod summarization;
mod token_cipher;
mod x_oauth;
mod x_sync;

pub use audio_episode::{
    AudioEpisodeGateway, AudioEpisodeGatewayConfig, AudioEpisodeGatewayError, AudioEpisodeScript,
    AudioEpisodeSpeaker, AudioEpisodeTtsChunkUsage, AudioEpisodeTtsUsageObserver, AudioEpisodeTurn,
    GeneratedAudioEpisodeScript, SynthesizedDialogue,
};
pub use briefing_composition::{
    BRIEFING_LENS_NAMING_MAX_CATEGORIES, BRIEFING_LENS_NAMING_MAX_INPUT_BYTES,
    BRIEFING_LENS_NAMING_MAX_OUTPUT_TOKENS, BRIEFING_LENS_NAMING_MAX_STORIES_PER_CATEGORY,
    BriefingCompositionBlock, BriefingCompositionGateway, BriefingCompositionGatewayError,
    BriefingCompositionLayout, BriefingCompositionRequest, BriefingCompositionSource,
    BriefingEmbeddingBatch, BriefingFigureAlignment, BriefingFigurePlacement, BriefingLensName,
    BriefingLensNamingBatch, BriefingLensNamingBatchRequest, BriefingLensNamingCategory,
    BriefingLensNamingResult, BriefingLensNamingStory, BriefingPassageWeight,
    BriefingSuggestedQuote, GeneratedBriefingLayout, GeneratedBriefingLensName,
    GeneratedBriefingLensNamingBatch,
};
pub use briefing_dig::{
    BriefingDigGateway, BriefingDigGatewayError, BriefingDigSummary, BriefingWebSearchOutcome,
    BriefingWebSearchResult,
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
    PodcastEpisodeHit, SynthesizedNarration,
};
pub use discussion_fetch::{
    hacker_news_discussion_id, infer_discussion_platform, normalize_discussion_external_id,
    normalize_discussion_http_url, normalize_reddit_discussion_url, reddit_discussion_id,
};
pub use exa::ExaSearchUsage;
pub use feed_validation::{
    FeedValidationError, FeedValidator, ValidatedFeed, ValidatedFeedFormat, ValidatedSharedItem,
    ValidatedSharedTarget,
};
pub use image_generation::{
    GeneratedImage, GoogleImageAuth, ImageGenerationError, ImageGenerationGateway,
    ImageGenerationUsage, ImageGenerationUsageObserver, InfographicProvider,
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
    OnboardingAudioLane, OnboardingAudioPlan, OnboardingDiscoveryOutcome, OnboardingDiscoverySeeds,
    OnboardingExaUsage, OnboardingGateway, OnboardingGatewayError, OnboardingLaneTarget,
    OnboardingSuggestionSeed,
};
pub use openai_background::{
    BackgroundBuiltInTools, BackgroundProviderError, BackgroundResponseStatus, BackgroundUsage,
    OpenAiBackgroundGateway, OpenAiBackgroundRequest, OpenAiBackgroundResult, OpenAiGatewayError,
};
pub use openai_transcription::{
    AudioDurationSource, OpenAiTranscriptionError, OpenAiTranscriptionGateway,
    TranscriptionChunkUsage, TranscriptionResult, TranscriptionUsageObserver,
};
pub use rig_engine::RigAgentEngine;
pub use scraping::{
    FeedEntrySelection, FeedScrapeTarget, RedditScrapeTarget, ScrapeFailure, ScrapeGateway,
    ScrapeGatewayError, ScrapeProviderOutcome, ScrapedContentItem, ScrapedItem, ScrapedNewsItem,
    normalize_feed_document,
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
pub mod briefing_links;

pub use object_storage::{
    LocalStorageError, delete_local_object, is_safe_object_key, is_safe_storage_root,
};

pub use object_storage::{ObjectStorageBackend, ObjectStorageConfigError};

pub use object_storage::{LocalObjectSlice, MAX_SOURCE_BODY_BYTES, read_local_object};
