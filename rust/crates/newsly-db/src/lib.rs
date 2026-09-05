//! `SQLx` connection, query, migration, and backfill infrastructure.

#![forbid(unsafe_code)]
// Repository functions already expose concrete typed error enums, and the consistent `r#"..."#`
// spelling keeps hundreds of multiline SQL statements mechanically uniform even when a query does
// not currently contain a double quote. SQL projection functions are deliberately explicit and can
// be long or take several bind values; short-lived outcome enums stay inline instead of adding heap
// allocation. Production targets are 64-bit, and all count-to-`usize` conversions are bounded by
// application query limits long before pointer width.
#![allow(
    clippy::cast_possible_truncation,
    clippy::doc_markdown,
    clippy::large_enum_variant,
    clippy::missing_errors_doc,
    clippy::needless_raw_string_hashes,
    clippy::similar_names,
    clippy::struct_excessive_bools,
    clippy::too_many_arguments,
    clippy::too_many_lines
)]

mod admin;
mod adoption;
mod agent_library;
mod api_keys;
mod audio_episodes;
mod auth;
mod briefing;
mod briefing_refresh;
mod chat;
mod chat_council;
mod chat_tasks;
mod chat_tooling;
mod chat_transcripts;
mod cli_link;
mod config;
mod content_actions;
mod content_bodies;
mod content_feeds;
mod content_misc;
mod content_read;
mod content_submission;
mod discussions;
mod e2e_fixtures;
mod fingerprint;
mod integrations;
mod interactions;
mod jobs;
mod learning_deck_tasks;
mod learning_decks;
mod llm_task_dispatch;
mod llm_tasks;
mod media_tasks;
mod migrations;
mod news_actions;
mod onboarding;
mod onboarding_flow;
mod onboarding_tasks;
mod ownership;
mod pool;
mod refresh_tokens;
mod route_fence;
mod scrape;
mod scraper_configs;
mod scraper_stats;
mod share_actions;
mod stats;
mod task_sandboxes;
mod users;
mod vendor_usage;
mod x_sync;

pub use admin::{
    AdminCountRow, AdminDashboardSnapshot, AdminEvalCandidate, AdminFeedbackRow,
    AdminProviderCostRow, AdminQueueCountRow, AdminRecentFailureRow, AdminRepositoryError,
    AdminUserStats, AdminVendorUsageDailyRow, AdminVendorUsageFilter, AdminVendorUsageRow,
    AdminVendorUsageSnapshot, AdminVendorUsageTotals, list_admin_eval_candidates,
    list_admin_feedback, load_admin_dashboard, load_admin_vendor_usage,
};
pub use adoption::{
    AdoptionError, AdoptionReport, MigrationHistoryError, adopt_existing_database,
    verify_existing_baseline,
};
pub use agent_library::{
    AgentKnowledgeItem, AgentLibraryBodyPointer, AgentLibraryContentProjection,
    AgentLibraryRepositoryError, find_agent_knowledge_items, list_agent_library_content,
};
pub use api_keys::{
    ApiKeyRepositoryError, ApiKeySummaryProjection, ApiKeyTargetUser, CreatedApiKey,
    GeneratedApiKey, create_api_key, ensure_system_admin_user, extract_api_key_prefix,
    generate_api_key, hash_api_key, list_api_key_target_users, list_api_keys, revoke_api_key,
    verify_api_key_hash,
};
pub use audio_episodes::{
    AudioEpisodeReadTrigger, AudioEpisodeRecord, AudioEpisodeRepositoryError,
    AudioEpisodeScriptUsage, AudioEpisodeShareOutcome, AudioEpisodeTtsUsage,
    CheckpointAudioEpisodeScript, CompleteAudioEpisodeGeneration, NewAudioEpisode,
    PrepareAudioEpisodeGenerationOutcome, checkpoint_audio_episode_script,
    complete_audio_episode_generation, disable_audio_episode_share, enable_audio_episode_share,
    fail_audio_episode_generation, find_shared_audio_episode, find_user_audio_episode,
    find_user_audio_episode_for_update, list_user_custom_narrations,
    mark_audio_episode_sources_read, prepare_audio_episode_generation,
    reset_audio_episode_for_generation, upsert_audio_episode,
};
pub use auth::{
    AuthenticatedUserRow, AuthenticationRepositoryError, find_user_by_api_key, find_user_by_id,
    is_api_key_token,
};
pub use briefing::{
    AudioEpisodeProjection, BriefingDiscussionProjection, BriefingFirstRunProjection,
    BriefingIndexProjection, BriefingIndexValidatorProjection, BriefingLensCursorProjection,
    BriefingLensPageProjection, BriefingLensProjection, BriefingNarrationSelection,
    BriefingReadMarkProjection, BriefingRepositoryError, BriefingSegmentMetadataProjection,
    BriefingSegmentProjection, BriefingSourceProjection, BriefingStateProjection,
    ContentBriefingSourceProjection, FirstRunSourceProjection, NewsBriefingSourceProjection,
    PrepareNarrationOutcome, ensure_briefing_state_version, expedite_pending_briefing_refresh,
    load_briefing_index, load_briefing_index_validator, load_briefing_lens_page,
    load_briefing_narration, mark_briefing_lens_read, mark_briefing_sources_read,
    prepare_briefing_narration, public_audio_episode_error_message, recent_briefing_dig_count,
    record_briefing_dig_usage,
};
pub use briefing_refresh::{
    ApplyBriefingLensAssignmentOutcome, BriefingAppendBatch, BriefingCompactionBatch,
    BriefingDonorIdentity, BriefingEmbeddingUsage, BriefingLensAssignmentPlan,
    BriefingLensAssignmentSnapshot, BriefingLensAssignmentUsage, BriefingLensCentroidMutation,
    BriefingPendingIdentity, BriefingPendingLensAssignment, BriefingPlannedLens,
    BriefingRefreshApplyOutcome, BriefingRefreshClaimFence, BriefingRefreshConfig,
    BriefingRefreshLens, BriefingRefreshMode, BriefingRefreshPublication,
    BriefingRefreshRepositoryError, BriefingRefreshSource, BriefingSegmentUsage,
    BriefingSemanticLens, BriefingUnassignedSource, ComposedBriefingAppend,
    ComposedBriefingCompaction, ComposedBriefingSegment, PrepareBriefingRefreshOutcome,
    PreparedBriefingRefresh, PreparedBriefingRefreshSeed, apply_briefing_lens_assignment,
    apply_briefing_refresh, prepare_briefing_refresh,
};
pub use chat::{
    ChatListCursor, ChatMessageProjection, ChatMessageStatusProjection, ChatMutationOutcome,
    ChatRecordAccess, ChatRepositoryError, ChatSessionDetailProjection, ChatSessionProjection,
    ChatToolProgressProjection, CreateChatSessionInput, CreateChatSessionOutcome,
    StageAssistantTurnInput, StageChatMessageInput, StageChatTurnOutcome, StagedChatTurn,
    UpdateChatSessionInput, archive_chat_session, create_chat_session, get_chat_message_status,
    get_chat_session_detail, get_chat_session_summary, list_chat_sessions, stage_assistant_turn,
    stage_chat_message, update_chat_session,
};
pub use chat_council::{
    CouncilCandidateCompletion, CouncilPersonaSeed, CouncilRepositoryError, CouncilRunContext,
    CouncilRunKind, CouncilSelectOutcome, CouncilStageOutcome, SelectCouncilBranchInput,
    StageCouncilRetryInput, StageCouncilStartInput, StagedCouncilWork, finalize_council_candidate,
    finalize_failed_council_candidate, select_council_branch, stage_council_retry,
    stage_council_start, visible_council_session_id,
};
pub use chat_tasks::{
    AssistantScreenContext, ChatAdvisoryWriteOutcome, ChatContentMaterial,
    ChatTaskPreparationOutcome, ChatTaskRejection, ChatTaskRepositoryError, ChatTaskSnapshot,
    ChatTerminalMutationOutcome, ChatToolProgress, ChatTurnKind, ChatTurnProcessingContext,
    ChatTurnPublication, ChatTurnSessionSnapshot, PrepareChatTask, QueuedChatTaskKind,
    cancel_chat_llm_task_attempt, fail_chat_turn, persist_deep_research_response_id,
    prepare_chat_task, publish_chat_turn, write_chat_partial, write_chat_tool_progress,
};
pub use chat_tooling::{
    ChatArticleConversionSource, ChatContentHit, ChatNewsHit, ChatToolRepositoryError,
    ChatUnreadNewsPage, create_deep_research_handoff, list_unread_chat_news,
    prepare_chat_article_conversion, search_agent_knowledge, search_chat_content, search_chat_news,
    search_chat_subscription_content,
};
pub use cli_link::{
    ApprovedCliLink, CliLinkPollStatus, CliLinkRepositoryError, PolledCliLink, StartedCliLink,
    approve_cli_link, poll_cli_link, start_cli_link,
};
pub use config::{DatabaseConfig, DatabaseConfigError, normalize_database_url};
pub use content_actions::{
    BulkReadResult, ContentActionRepositoryError, content_exists, mark_content_read,
    mark_content_unread, mark_contents_read, remove_content_from_knowledge,
    save_content_to_knowledge,
};
pub use content_bodies::{
    ContentBodyPointer, ContentBodyProjection, ContentBodyRepositoryError, ContentBodyVariant,
    find_visible_content_body, find_visible_news_item_body,
};
pub use content_feeds::{
    ContentCardProjection, ContentFeedCursor, ContentFeedPage, ContentFeedReadFilter,
    ContentFeedRepositoryError, list_content_feed, list_knowledge_content,
    list_recently_read_content, search_visible_content,
};
pub use content_misc::{
    ContentConversionPlan, ContentMiscRepositoryError, ContentNarrationPlan, ConvertedArticle,
    DiscussionRefreshPlan, DiscussionTargetKind, FeedBackfillEntry, FeedBackfillOrigin,
    FeedBackfillPersistence, FeedBackfillPlan, FeedBackfillPreparation, NewsConversionPlan,
    SubmissionPage, SubmissionProjection, TweetContentPlan, finalize_article_conversion,
    known_feed_urls, list_submission_projections, persist_content_discussion,
    persist_feed_backfill, persist_news_discussion, prepare_content_conversion,
    prepare_content_discussion_refresh, prepare_content_narration, prepare_feed_backfill,
    prepare_news_conversion, prepare_news_discussion_refresh, prepare_tweet_content,
};
pub use content_read::{
    ContentDetailProjection, ContentReadRepositoryError, NewsItemProjection, NewsListCursor,
    NewsListPage, NewsReadFilter, find_visible_content_detail, find_visible_news_item_detail,
    list_active_feed_urls, list_visible_news_items,
};
pub use content_submission::{
    AppliedContentSubmission, ContentSubmissionInput, ContentSubmissionRepositoryError,
    SubmissionTaskResolution, apply_content_submission,
};
pub use discussions::{
    ContentDiscussionProjection, DiscussionRepositoryError, NewsDiscussionProjection,
    find_visible_content_discussion, find_visible_news_discussion,
};
pub use e2e_fixtures::{
    E2eDatabaseIdentity, IosE2eBriefingFixtures, IosE2eChatFixtures, IosE2eContentFixtures,
    IosE2eFixtureError, IosE2eFixtureNamespace, IosE2eFixtureSeedReceipt, IosE2eLearningFixtures,
    IosE2eLocalArtifactPlan, IosE2eTaskFixtures, inspect_e2e_database_identity,
    ios_e2e_local_artifact_plan, seed_ios_e2e_fixture,
};
pub use fingerprint::FingerprintError;
pub use integrations::{
    IntegrationRepositoryError, PrepareXOAuthExchangeOutcome, PreparedXOAuthExchange,
    UserLlmIntegrationProjection, XConnectionProjection, XDisconnectPlan,
    delete_user_llm_integration, finalize_x_disconnect, finalize_x_oauth_exchange,
    find_x_connection, list_user_llm_integrations, prepare_x_disconnect, prepare_x_oauth_exchange,
    store_x_oauth_pending, upsert_user_llm_integration, user_llm_integration_configured,
};
pub use interactions::{
    ContentInteractionInsertResult, InteractionRepositoryError, NewContentInteraction, NewFeedback,
    insert_content_interaction, insert_feedback,
};
pub use jobs::{JobRepositoryError, JobStatusProjection, find_job_for_user};
pub use learning_deck_tasks::{
    ContentBodyMaterial, LearningDeckModelUsage, LearningDeckPreparationOutcome,
    LearningDeckSourceMaterial, LearningDeckSourceSettlement, LearningDeckTaskRepositoryError,
    LearningDeckTaskSnapshot, MarkLearningDeckRunningOutcome, PublishLearningDeck,
    PublishLearningDeckOutcome, StoredLearningDeckArtifact, begin_learning_deck_preparation,
    fail_learning_deck_task, mark_learning_deck_running, publish_learning_deck,
    settle_learning_deck_source_missing,
};
pub use learning_decks::{
    ContentSourceOutcome, ConvertedNewsSource, CreateLearningDeckOutcome, DeletedLearningDeck,
    DisableLearningDeckShareOutcome, EnableLearningDeckShareOutcome, HostedLearningDeckProjection,
    LearningDeckAttemptProjection, LearningDeckProjection, LearningDeckRepositoryError,
    LearningDeckSourceProjection, LearningDeckTimelineProjection, RetryLearningDeckOutcome,
    VisibleNewsItemProjection, convert_news_item_to_learning_deck_source,
    create_or_rerun_learning_deck, delete_learning_deck, disable_learning_deck_share,
    find_visible_news_item_for_learning_deck, get_hosted_learning_deck, get_learning_deck,
    is_active_learning_deck_conflict, list_learning_decks,
    load_submitted_content_learning_deck_source, persist_learning_deck_share,
    prepare_enable_learning_deck_share, resolve_content_learning_deck_source, retry_learning_deck,
};
pub use llm_task_dispatch::{
    LlmTaskDispatchKind, LlmTaskDispatchRepositoryError, UnsupportedLlmTaskOutcome,
    classify_llm_task, fail_unsupported_llm_task,
};
pub use llm_tasks::{
    LlmTaskActionProjection, LlmTaskRepositoryError, RejectLlmTaskActionOutcome,
    list_llm_task_actions_for_user, reject_llm_task_action,
};
pub use media_tasks::{
    MediaApplyOutcome, MediaContentSnapshot, MediaMutation, MediaNextTask,
    MediaTaskRepositoryError, MediaTranscriptPointer, MediaTranscriptionUsage,
    apply_media_mutation, prepare_media_content, record_media_transcription_usage,
};
pub use migrations::{
    BASELINE_VERSION, MigrationError, embedded_migration_count, run_migrations,
    run_migrations_with_barrier,
};
pub use news_actions::{
    BulkNewsReadResult, NewsActionRepositoryError, mark_visible_news_items_read,
};
pub use onboarding::{
    OnboardingDiscoveryLaneProjection, OnboardingDiscoveryStatusProjection,
    OnboardingRepositoryError, OnboardingSuggestionProjection, complete_onboarding_tutorial,
    find_onboarding_discovery_status,
};
pub use onboarding_flow::{
    AgentOnboardingSuggestionProjection, ExistingOnboardingFeedConfig, OnboardingAudioLaneInput,
    OnboardingAudioLaneProjection, OnboardingAudioRunInput, OnboardingAudioRunProjection,
    OnboardingCompletionAggregator, OnboardingCompletionInput, OnboardingCompletionProjection,
    OnboardingCompletionSource, OnboardingFlowRepositoryError, complete_onboarding_selection,
    create_onboarding_audio_run, list_existing_onboarding_feed_configs,
    load_agent_onboarding_suggestions, load_onboarding_completion_suggestions,
};
pub use onboarding_tasks::{
    FeedDiscoveryFavorite, FeedDiscoveryTaskSnapshot, NewOnboardingSuggestion,
    OnboardingAttemptStatus, OnboardingTaskLane, OnboardingTaskRepositoryError,
    OnboardingTaskSnapshot, PrepareFeedDiscoveryTaskOutcome, PrepareOnboardingTaskOutcome,
    WeeklyDiscoverySessionOutcome, complete_feed_discovery_task,
    complete_onboarding_discovery_task, ensure_weekly_discovery_session,
    prepare_feed_discovery_task, prepare_onboarding_discovery_task, settle_feed_discovery_attempt,
    settle_onboarding_discovery_attempt,
};
pub use ownership::{
    OwnershipAcknowledgement, OwnershipDrainStatus, OwnershipMutationContext, OwnershipRepository,
    OwnershipRepositoryError, OwnershipSeed, PreparedRouteTransition,
};
pub use pool::{Database, DatabaseError};
pub use refresh_tokens::{
    RefreshRotationClaim, RefreshTokenRepositoryError, begin_refresh_rotation, store_refresh_replay,
};
pub use route_fence::{RouteWriteFenceError, verify_route_write_fence};
pub use scrape::{
    PersistedContentRecord, PersistedNewsRecord, PreparedScrapeSources, ScrapeConfigSnapshot,
    ScrapeRepositoryError, ScrapedContentRecord, ScrapedNewsRecord, due_discussion_refresh_ids,
    matching_scrape_config_ids, persist_scraped_content, persist_scraped_news,
    prepare_scrape_sources, record_first_edition_scrape_result,
};
pub use scraper_configs::{
    AppliedFeedSubscription, FeedSubscriptionMutation, NewScraperConfig, ScraperConfigPatch,
    ScraperConfigProjection, ScraperConfigRepositoryError, apply_validated_feed_subscription,
    canonicalize_feed_url, create_scraper_config, delete_scraper_config, find_scraper_config,
    list_scraper_configs, scraper_config_identity_exists, update_scraper_config,
};
pub use scraper_stats::{
    ScraperConfigStatsProjection, ScraperStatsRepositoryError, get_scraper_config_stats,
};
pub use share_actions::{
    ApproveShareActionOutcome, CreatedShareActionTask, NewShareActionTask,
    PreparedContentProjection, PreparedShareSource, RequestedActionStatus, RequestedShareAction,
    ShareActionAgentSnapshot, ShareActionFinalizationTask, ShareActionPreparation,
    ShareActionPreparationDraft, ShareActionRepositoryError, ShareActionTaskProjection,
    approve_share_action, begin_share_action_preparation, enrich_prepared_share_content,
    fail_share_action_approval, fail_share_action_task, find_prepared_share_content,
    find_share_action_for_user, finish_share_action_preparation, finish_share_action_task,
    get_or_create_share_chat_session, insert_share_action_task, load_share_action_action,
    lock_share_action_for_finalization, mark_share_action_applied, mark_share_action_applying,
    mark_share_action_failed, persist_share_action_agent_output, request_share_action,
};
pub use stats::{
    ProcessingCountsProjection, StatsRepositoryError, UnreadCountsProjection,
    get_long_form_unread_count, get_processing_counts, get_unread_counts,
};
pub use task_sandboxes::{
    TaskSandboxCleanupCandidate, TaskSandboxRepositoryError, clear_task_sandbox,
    find_recorded_task_sandbox, list_task_sandbox_cleanup_candidates,
    mark_task_sandbox_cleanup_required, record_task_sandbox,
};
pub use users::{
    AppleUserUpsert, DebugUserPatch, UserProfilePatch, UserProfileProjection,
    UserProfileRepositoryError, create_or_update_debug_user, deactivate_active_user,
    find_or_create_apple_user, find_user_profile, update_user_profile,
};
pub use vendor_usage::{
    NewTranscriptionUsage, NewXUserLookupUsage, VendorUsageRepositoryError,
    record_transcription_usage, record_x_user_lookup_usage,
};
pub use x_sync::{
    NewXSyncUsage, PrepareXSyncOutcome, PreparedXSync, XSyncConnectionUpdate, XSyncRepositoryError,
    complete_x_sync, find_reusable_x_bookmark_content, lock_current_x_sync_connection,
    mark_x_sync_failed, mark_x_sync_reauth_required, persist_x_bookmark_snapshot,
    persist_x_sync_connection_update, prepare_x_sync, record_x_sync_usage,
    remove_stale_x_bookmark_save, resolve_x_bookmark_destination, save_x_bookmark_destination,
    upsert_x_bookmark_ledger, x_bookmark_destination_needs_image,
};

mod task_failure;
pub use task_failure::settle_failed_task;

mod source_health;
pub use source_health::{PipelineHealthCounts, pipeline_health_counts, record_source_health};

mod artifact_cleanup;
pub use artifact_cleanup::{
    artifact_cleanup_candidates, forget_cleaned_artifact, track_artifact, track_image_artifact,
};
