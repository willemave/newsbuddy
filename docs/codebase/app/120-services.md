# app/services/

Source folder: `app/services`

## Purpose
Business-logic layer for LLM access, content analysis and submission, chat, discovery, feeds, images, interactions, onboarding, and queue primitives.

## Runtime behavior
- Holds the orchestration-heavy logic that routers and handlers call into, including URL analysis, summarization, chat turns, discovery, and image generation.
- Contains adapter services for multiple model providers, telemetry/tracing, prompt construction, metadata merging, and provider usage accounting.
- Implements end-user features such as favorites, read state, feed subscription, tweet suggestions, daily digests, and onboarding workflows.

## Inventory scope
- Direct file inventory for `app/services`.

## Modules and files
| File | Key symbols | Notes |
|---|---|---|
| `app/services/__init__.py` | n/a | Service layer modules. |
| `app/services/admin_conversational_agent.py` | `SessionTurn`, `SessionState`, `AgentConversationRuntime`, `KnowledgeHit`, `WebHit`, `elevenlabs_sdk_available`, `build_health_flags`, `create_or_get_session_state`, `append_turn`, `get_turn_history`, +10 more | ElevenLabs-backed admin conversational streaming service. |
| `app/services/admin_eval.py` | `ModelPricing`, `AdminEvalRunRequest`, `EvalSourcePayload`, `get_default_pricing`, `select_eval_samples`, `run_admin_eval`, `build_eval_source_payload` | Admin-only LLM eval helpers for summary and title comparison. |
| `app/services/anthropic_llm.py` | `AnthropicSummarizationService`, `get_anthropic_summarization_service` | Anthropic summarization via pydantic-ai. |
| `app/services/apple_podcasts.py` | `ApplePodcastResolution`, `resolve_apple_podcast_episode` | Helpers for resolving Apple Podcasts episode metadata. |
| `app/services/chat_agent.py` | `ChatDeps`, `ChatRunResult`, `get_chat_agent`, `build_article_context`, `load_message_history`, `save_messages`, `create_processing_message`, `update_message_completed`, `update_message_failed`, `run_chat_turn`, +2 more | Chat agent service using pydantic-ai for deep-dive conversations. |
| `app/services/content_analyzer.py` | `ContentAnalysisResult`, `InstructionLink`, `InstructionResult`, `ContentAnalysisOutput`, `AnalysisError`, `ContentAnalyzer`, `get_content_analyzer` | Content analysis service using page fetching and LLM analysis |
| `app/services/content_interactions.py` | `RecordContentInteractionInput`, `RecordContentInteractionResult`, `ContentInteractionContentNotFoundError`, `record_content_interaction` | Service functions for recording user content interaction analytics. |
| `app/services/content_metadata_merge.py` | `compute_metadata_patch`, `refresh_merge_content_metadata` | Helpers for safe content metadata writes under concurrent task updates. |
| `app/services/content_submission.py` | `normalize_url`, `submit_user_content` | Helpers for user-submitted one-off content. |
| `app/services/deep_research.py` | `DeepResearchResult`, `DeepResearchClient`, `get_deep_research_client`, `close_deep_research_client`, `process_deep_research_message` | Deep research service using OpenAI's o4-mini-deep-research model |
| `app/services/dig_deeper.py` | `resolve_display_title`, `build_dig_deeper_prompt`, `get_or_create_dig_deeper_session`, `create_dig_deeper_message`, `run_dig_deeper_message`, `enqueue_dig_deeper_task` | Helpers for auto-starting dig-deeper chats. |
| `app/services/discussion_fetcher.py` | `DiscussionFetchError`, `DiscussionFetchResult`, `DiscussionPayload`, `DiscussionTarget`, `fetch_and_store_discussion` | Discussion ingestion service for news content. |
| `app/services/exa_client.py` | `ExaSearchResult`, `get_exa_client`, `exa_search`, `format_exa_results_for_context` | Exa search client service for chat agent web search tool. |
| `app/services/favorites.py` | `toggle_favorite`, `add_favorite`, `remove_favorite`, `get_favorite_content_ids`, `is_content_favorited`, `clear_favorites` | Repository for content favorites operations. |
| `app/services/feed_backfill.py` | `FeedBackfillRequest`, `FeedBackfillResult`, `resolve_feed_config_for_content`, `backfill_feed_for_config` | Helpers for on-demand feed backfills ("download more from this series"). |
| `app/services/feed_detection.py` | `FeedClassificationResult`, `FeedDetector`, `extract_feed_links`, `extract_feed_links_from_anchors`, `classify_feed_type_with_llm`, `detect_feeds_from_html` | RSS/Atom feed detection service |
| `app/services/feed_discovery.py` | `FeedDiscoveryRequest`, `FeedDiscoveryDeps`, `DiscoveryToolDeps`, `run_feed_discovery` | Feed/podcast/YouTube discovery workflow using favorites + Exa. |
| `app/services/feed_subscription.py` | `is_feed_already_subscribed`, `can_subscribe_to_feed`, `subscribe_to_detected_feed` | Helpers for subscribing to detected RSS/Atom feeds. |
| `app/services/google_flash.py` | `GoogleFlashService`, `get_google_flash_service` | Google Gemini summarization via pydantic-ai. |
| `app/services/http.py` | `NonRetryableError`, `HttpService`, `should_bypass_ssl`, `is_ssl_error`, `categorize_http_error`, `get_http_service` | Types: `NonRetryableError`, `HttpService`. Functions: `should_bypass_ssl`, `is_ssl_error`, `categorize_http_error`, `get_http_service` |
| `app/services/image_generation.py` | `ImageGenerationResult`, `InterestingScore`, `ImageGenerationService`, `get_image_generation_service` | AI image generation service using Google Gemini |
| `app/services/instruction_links.py` | `create_contents_from_instruction_links` | Helpers for creating content from instruction-derived links. |
| `app/services/langfuse_tracing.py` | `initialize_langfuse_tracing`, `flush_langfuse_tracing`, `extract_google_usage_details`, `langfuse_trace_context`, `langfuse_generation_context` | Langfuse bootstrap and tracing helpers. |
| `app/services/llm_agents.py` | `get_basic_agent`, `get_summarization_agent` | Factory helpers for pydantic-ai agents. |
| `app/services/llm_models.py` | `LLMProvider`, `resolve_model`, `build_pydantic_model`, `is_deep_research_provider`, `is_deep_research_model` | Shared pydantic-ai model construction helpers. |
| `app/services/llm_prompts.py` | `generate_summary_prompt`, `creativity_to_style_hints`, `length_to_char_range`, `get_tweet_generation_prompt` | Shared LLM prompt generation for content summarization |
| `app/services/llm_summarization.py` | `SummarizationRequest`, `ContentSummarizer`, `get_content_summarizer`, `summarize_content` | Shared summarization flow using pydantic-ai agents. |
| `app/services/llm_usage.py` | `start_usage_context`, `end_usage_context`, `snapshot_usage`, `record_usage` | Shared LLM usage tracking for per-run aggregation. |
| `app/services/long_form_images.py` | `QueueEnqueuer`, `is_long_form_image_content_type`, `has_summary_for_generated_image`, `has_generated_long_form_image`, `has_active_generate_image_task`, `is_visible_in_any_long_form_inbox`, `is_visible_long_form_image_candidate`, `enqueue_visible_long_form_image_if_needed`, `enqueue_visible_long_form_images_for_content_ids`, `cancel_ineligible_pending_generate_image_tasks`, +1 more | Shared rules for long-form generated image eligibility and cleanup. |
| `app/services/onboarding.py` | `build_onboarding_profile`, `parse_onboarding_voice`, `preview_audio_lane_plan`, `start_audio_discovery`, `get_onboarding_discovery_status`, `fast_discover`, `complete_onboarding`, `run_discover_enrich`, `run_audio_discovery`, `mark_tutorial_complete` | Service helpers for agentic onboarding. |
| `app/services/openai_llm.py` | `OpenAISummarizationService`, `OpenAITranscriptionService`, `get_openai_transcription_service`, `get_openai_summarization_service` | OpenAI services (summarization via pydantic-ai, transcription via Whisper). |
| `app/services/openai_realtime.py` | `create_realtime_client_secret`, `build_transcription_session_config`, `create_transcription_session_token` | OpenAI Realtime helpers. |
| `app/services/podcast_search.py` | `PodcastEpisodeSearchHit`, `search_podcast_episodes` | Provider-aggregated podcast episode search service. |
| `app/services/prompt_debug_report.py` | `SyncOptions`, `PromptReportOptions`, `LogRecord`, `FailureRecord`, `PromptSnapshot`, `PromptDebugReport`, `run_remote_sync`, `collect_log_records`, `select_failure_records`, `reconstruct_summarize_prompt`, +5 more | Build local prompt-debug reports from synced JSONL logs. |
| `app/services/queue.py` | `QueueService`, `get_queue_service` | Types: `QueueService`. Functions: `get_queue_service` |
| `app/services/read_status.py` | `mark_content_as_read`, `mark_contents_as_read`, `get_read_content_ids`, `is_content_read`, `clear_read_status` | Repository for content read status operations. |
| `app/services/scraper_configs.py` | `CreateUserScraperConfig`, `UpdateUserScraperConfig`, `list_user_scraper_configs`, `list_active_configs_by_type`, `create_user_scraper_config`, `update_user_scraper_config`, `delete_user_scraper_config`, `build_feed_payloads`, `ensure_inbox_status`, `should_add_to_inbox`, +1 more | Service helpers for per-user scraper configurations. |
| `app/services/token_crypto.py` | `encrypt_token`, `decrypt_token` | Helpers for encrypting and decrypting integration tokens at rest. |
| `app/services/tweet_suggestions.py` | `TweetSuggestionLLM`, `TweetSuggestionsPayload`, `TweetSuggestionData`, `TweetSuggestionsResult`, `TweetSuggestionService`, `creativity_to_temperature`, `get_tweet_suggestion_service`, `generate_tweet_suggestions` | Tweet suggestions service using Gemini via pydantic-ai |
| `app/services/twitter_share.py` | `TwitterCredentials`, `TwitterCredentialsParams`, `TwitterCredentialsResult`, `TweetExternalUrl`, `TweetInfo`, `TweetFetchParams`, `TweetFetchResult`, `QueryIdSnapshot`, `extract_tweet_id`, `is_tweet_url`, +4 more | Tweet-only GraphQL client and URL helpers for share-sheet ingestion. |
| `app/services/url_detection.py` | `UrlHandler`, `UrlHandlerMatch`, `get_url_handler_name`, `infer_content_type_and_platform`, `should_use_llm_analysis` | URL detection utilities for content type inference |
| `app/services/whisper_local.py` | `WhisperLocalTranscriptionService`, `get_whisper_local_service` | Types: `WhisperLocalTranscriptionService`. Functions: `get_whisper_local_service` |
| `app/services/x_api.py` | `XUser`, `XTweet`, `XTokenResponse`, `XTweetFetchResult`, `XBookmarksPage`, `is_tweet_url`, `build_oauth_authorize_url`, `exchange_oauth_code`, `refresh_oauth_token`, `get_authenticated_user`, +4 more | Official X API v2 helpers for OAuth, tweets, and bookmarks. |
| `app/services/x_integration.py` | `XConnectionView`, `BookmarkSyncSummary`, `normalize_twitter_username`, `is_x_oauth_configured`, `has_active_x_connection`, `get_x_user_access_token`, `get_x_connection_view`, `start_x_oauth`, `exchange_x_oauth`, `disconnect_x_connection`, +1 more | Service layer for user-specific X integration state and sync. |
