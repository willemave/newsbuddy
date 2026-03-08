# app/routers/api/

Source folder: `app/routers/api`

## Purpose
User-facing JSON API surface for content, chat, discovery, onboarding, voice, integrations, stats, submissions, and auxiliary OpenAI/realtime endpoints.

## Runtime behavior
- Splits the mobile-facing API into narrow route modules so each endpoint group owns its request validation and response shaping.
- Coordinates content list/detail actions, chat session lifecycle, discovery suggestions, onboarding state, scraper settings, and live voice sessions.
- Defines the Pydantic DTO layer consumed by the iOS app and share extension.

## Inventory scope
- Direct file inventory for `app/routers/api`.

## Modules and files
| File | Key symbols | Notes |
|---|---|---|
| `app/routers/api/__init__.py` | n/a | API content routers organized by responsibility |
| `app/routers/api/chat.py` | `list_sessions`, `create_session`, `update_session`, `get_session`, `delete_session`, `send_message`, `get_message_status`, `get_initial_suggestions` | Chat session endpoints for deep-dive conversations. |
| `app/routers/api/chat_models.py` | `ChatMessageRole`, `ChatMessageDisplayType`, `MessageProcessingStatus`, `CreateChatSessionRequest`, `UpdateChatSessionRequest`, `SendChatMessageRequest`, `ChatMessageDto`, `ChatSessionSummaryDto`, `ChatSessionDetailDto`, `SendMessageResponse`, +3 more | Chat DTOs for API responses. |
| `app/routers/api/content_actions.py` | `convert_news_to_article`, `download_more_from_series`, `get_tweet_suggestions` | Content transformation and action endpoints. |
| `app/routers/api/content_detail.py` | `get_content_detail`, `get_content_discussion`, `get_chatgpt_url` | Content detail and chat URL endpoints. |
| `app/routers/api/content_list.py` | `list_contents`, `search_contents`, `search_podcast_episode_matches` | Content listing and search endpoints. |
| `app/routers/api/daily_news_digests.py` | `list_daily_news_digests`, `mark_daily_digest_read`, `mark_daily_digest_unread`, `get_daily_digest_voice_summary`, `get_daily_digest_voice_summary_audio`, `start_daily_digest_dig_deeper` | Daily news digest list and read-status endpoints. |
| `app/routers/api/discovery.py` | `get_discovery_suggestions`, `get_discovery_history`, `search_discovery_podcast_episodes`, `refresh_discovery`, `subscribe_discovery_suggestions`, `add_discovery_items`, `dismiss_discovery_suggestions`, `clear_discovery_suggestions` | Discovery suggestions endpoints. |
| `app/routers/api/favorites.py` | `toggle_favorite`, `unfavorite_content`, `get_favorites` | Favorites management endpoints. |
| `app/routers/api/integrations.py` | `get_x_connection`, `start_x_oauth_flow`, `exchange_x_oauth_code`, `disconnect_x` | Integration endpoints for external providers (X/Twitter). |
| `app/routers/api/interactions.py` | `post_content_interaction` | Interaction analytics endpoints. |
| `app/routers/api/models.py` | `ContentSummaryResponse`, `ContentListResponse`, `DailyNewsDigestResponse`, `DailyNewsDigestListResponse`, `DailyNewsDigestVoiceSummaryResponse`, `SubmissionStatusResponse`, `SubmissionStatusListResponse`, `DownloadMoreRequest`, `DownloadMoreResponse`, `DiscoverySuggestionResponse`, +54 more | Pydantic models for API endpoints. |
| `app/routers/api/onboarding.py` | `build_profile`, `parse_voice`, `run_fast_discover`, `start_audio_discovery_flow`, `onboarding_discovery_status`, `complete_onboarding_flow`, `tutorial_complete` | Onboarding endpoints. |
| `app/routers/api/openai.py` | `AudioTranscriptionResponse`, `create_realtime_token`, `transcribe_audio` | OpenAI-related endpoints. |
| `app/routers/api/read_status.py` | `mark_content_read`, `mark_content_unread`, `bulk_mark_read`, `get_recently_read` | Read status management endpoints. |
| `app/routers/api/scraper_configs.py` | `ScraperConfigResponse`, `SubscribeToFeedRequest`, `list_scraper_configs`, `create_scraper_config`, `update_scraper_config`, `delete_scraper_config_endpoint`, `subscribe_to_feed` | CRUD endpoints for per-user scraper configurations. |
| `app/routers/api/stats.py` | `get_unread_counts`, `get_processing_count`, `get_long_form_stats` | User-scoped content statistics endpoints. |
| `app/routers/api/submission.py` | `submit_content`, `list_submission_statuses` | Endpoint for one-off user submissions. |
| `app/routers/api/voice.py` | `create_or_resume_voice_session`, `voice_health`, `voice_websocket` | Realtime voice conversation endpoints. |
| `app/routers/api/voice_models.py` | `CreateVoiceSessionRequest`, `CreateVoiceSessionResponse`, `VoiceHealthResponse`, `VoiceClientSessionStartEvent`, `VoiceClientAudioFrameEvent`, `VoiceClientAudioCommitEvent`, `VoiceClientCancelEvent`, `VoiceClientSessionEndEvent`, `VoiceClientIntroAckEvent` | Pydantic DTOs for voice session endpoints and websocket events. |
