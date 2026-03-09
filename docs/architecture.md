# News App Architecture

> Technical reference for the FastAPI backend, content pipeline, chat system, and SwiftUI client contracts.

**Last Updated:** 2026-03-08  
**Runtime:** Python 3.13, FastAPI + SQLAlchemy 2, Pydantic v2, pydantic-ai  
**Database:** SQLite (first-class) / PostgreSQL-ready seams  
**Clients:** SwiftUI (iOS 17+), Jinja admin views, remote `newsly-agent` CLI

## Documentation Layout
- `docs/codebase/` is the folder-by-folder reference for `app/`, `client/`, and `config/`.
- `docs/initiatives/` holds historical plans, specs, and research grouped by initiative rather than document type.
- `docs/library/` holds durable operational, integration, deploy, feature, and reference docs.

## System Overview
- Unified content ingestion (scrapers + user submissions) feeding a DB-backed task queue (`analyze → process → summarize → image`) plus feed/discovery and dig-deeper follow-up task flows.
- URL analysis stage uses pattern matching + LLM page analysis to detect content type/platform and embedded media.
- Processing workers apply URL strategies, fetch/extract content, summarize with LLMs (editorial narrative by default with interleaved variants), and persist typed metadata.
- Post-summary imagery uses AI infographics for articles/podcasts; news currently skips post-summary image generation.
- API surface covers auth, feed/list/search, read/favorite state, conversions, tweet ideas, chat, and user-managed scrapers.
- Deep-dive chat uses pydantic-ai agents with Exa web search; conversations are stored server-side.
- Deep Research uses OpenAI's `o4-mini-deep-research-2025-06-26` model for comprehensive async research (up to ~10 minutes, with 2-second polling).
- Admin/Jinja web UI shares the same services as the mobile API.
- Agent CLI is a remote HTTP client of the FastAPI server. Mutating and long-running operations remain async and return task handles; client-side polling is optional convenience only.

```mermaid
flowchart LR
  iOS[SwiftUI App] -->|JWT| API[FastAPI app\napp/main.py]
  Admin[Jinja Admin] --> API
  CLI[newsly-agent CLI] -->|API key| API
  API --> DB[(Postgres/SQLite)]
  API --> Queue[(processing_tasks)]
  Scrapers -->|enqueue PROCESS_CONTENT| Queue
  UserSubmit[User /submit] -->|normalize + enqueue ANALYZE_URL| Queue
  Queue -->|dequeue| Worker[SequentialTaskProcessor\n+ ContentWorker]
  Worker --> Analyzer[ContentAnalyzer\ntrafilatura + LLM]
  Analyzer -->|enqueue PROCESS_CONTENT| Queue
  Worker --> Strategies[StrategyRegistry\nHTML/PDF/YT/HN/etc]
  Strategies --> HTTP[RobustHttpClient\nCrawl4AI]
  Worker --> LLM[ContentSummarizer\npydantic-ai]
  Worker --> ImageGen[Image Generation\nGemini]
  Worker --> Media[(media/logs dirs)]
  API <--> Chat[Chat Agent\nExa search]
  API <--> DeepResearch[Deep Research\nOpenAI o4-mini-deep-research]
```

## Dependency Direction
- Routers and API adapters call `app/application/commands` and `app/application/queries`, not repositories or low-level services directly.
- Pipeline handlers stay thin: parse payload, call one workflow/use-case, map outcome to `TaskResult`.
- Repositories own SQLAlchemy query composition only.
- Presenters map prepared rows/domain objects to DTOs only.
- Infrastructure owns DB capability seams, HTTP implementations, queue implementations, config authority, and auth/security implementations.
- Domain stays small and stable. `content_item` is the internal canonical noun in new code, while public DTO names remain compatibility boundaries.

## New Boundary Packages
- `app/application/commands/` and `app/application/queries/` provide router-facing entrypoints for content cards/detail, read/favorite state, submission/ingest, jobs, onboarding, API keys, digests, and user LLM integrations.
- `app/infrastructure/db/search/` isolates SQLite FTS branching behind search backends (`sqlite_search_backend.py`, `generic_search_backend.py`).
- `app/infrastructure/db/capabilities.py` exposes the current DB feature seams used by repositories without introducing Postgres-specific behavior.
- `app/infrastructure/security/` owns API-key generation/parsing and hashing.
- `app/infrastructure/http/` and `app/infrastructure/queue/` contain implementation adapters behind application-facing gateways.

## Auth Modes
- Mobile app auth remains JWT-based and unchanged.
- Remote CLI auth uses bearer API keys stored as hash + prefix in `user_api_keys`; raw keys are revealed only once at creation time.
- Selected API routes accept either JWT bearer tokens or bearer API keys through `app/core/deps.py`.
- Admin key management is server-rendered for simplicity:
  - `GET /admin/api-keys`
  - `POST /admin/api-keys/create`
  - `POST /admin/api-keys/{id}/revoke`

## Agent API Surface
- The additive machine-facing routes live under the existing `/api` namespace, not `/api/v2`.
- New endpoints:
  - `GET /api/jobs/{id}`
  - `POST /api/agent/search`
  - `POST /api/agent/onboarding`
  - `GET /api/agent/onboarding/{run_id}`
  - `POST /api/agent/onboarding/{run_id}/complete`
  - `POST /api/agent/digests`
- Existing mobile-facing content, onboarding, digest, and submission routes stay intact.

## Config Authority
- User-editable subscriptions are DB-authoritative. File config is seed/default input only.
- Reddit runtime subscriptions are DB-authoritative; file defaults are bootstrap-only and must be explicitly included if needed.
- Techmeme remains file-authoritative until a DB-backed runtime/admin surface exists.
- Twitter and YouTube file configs remain disabled/ad hoc inputs and are not silently treated as live scheduled runtime config.

## User-Provided LLM Keys
- User-managed provider keys live in `user_integration_connections` and reuse the existing token encryption helpers.
- Supported BYO providers in this refactor: `anthropic`, `openai`, `google`.
- Runtime credential resolution prefers a user-managed provider key when present, then falls back to platform credentials.
- Current user-scoped chat and voice model construction already resolves effective provider credentials through `app/services/llm_models.py`.

## Performance Note
- The list/detail/stats refactor now has dedicated query entrypoints (`content_card_repository.py`, `content_detail_repository.py`, `stats_repository.py`) and additive regression coverage around the affected routes.
- Local regression coverage in this refactor validated:
  - boundary-cleanup slice: `51 passed`
  - additive auth/agent/admin/integration slice: `18 passed`
- The intended query-count guardrail remains: card endpoints share a narrow projection path, detail uses a dedicated detail path, and stats uses a dedicated repository/query layer.

## Codebase Map
- `app/main.py` – FastAPI creation, middleware (CORS *), router mounting, lifespan bootstrap, request logging.
- `app/core/` – settings (`Settings`), DB bootstrap, JWT/security, dependencies, logging helpers.
- `app/models/` – SQLAlchemy tables (`schema.py`, `user.py`), Pydantic metadata/types, pagination helpers, scraper stats dataclass.
- `app/domain/` – converters between ORM and `ContentData` domain model.
- `app/http_client/` – resilient low-level HTTP client used by processing strategies and scraper-adjacent fetch paths.
- `app/repositories/` – shared feed/search/visibility query builders used by list and stats endpoints.
- `app/routers/` – auth, admin/content Jinja pages, logs, and API routers under `app/routers/api/`.
- `app/services/` – queue, LLM models/agents/prompts, summarization, chat agent, event logging, HTTP client, scraper config management, content analyzer, feed detection, image generation, tweet suggestions, Exa client, content submission helpers.
- `app/services/voice/` – live voice session orchestration, persistence, streaming, and ElevenLabs/TTS glue.
- `app/services/gateways/` – small infrastructure interfaces for HTTP, LLM, and queue dependencies.
- `app/pipeline/` – checkout manager, sequential processor, task dispatcher/handlers, content worker, podcast download/transcribe workers.
- `app/pipeline/workflows/` – focused state-transition helpers used by larger task handlers.
- `app/processing_strategies/` – URL strategy implementations + registry.
- `app/presenters/` – API response shaping and list/detail readiness rules.
- `app/scraping/` – scrapers (HN, Reddit, Substack, Techmeme, podcasts, Atom), runner, base class.
- `app/utils/` – shared helpers for URLs, paths, pagination, dates, summaries, and image paths.
- `client/newsly/` – SwiftUI app + ShareExtension consuming the API.
- `config/` – file-backed feed defaults plus tooling guardrails.
- `docs/codebase/` – generated reference inventory for the folders above.

## Core Runtime & Infrastructure
- **FastAPI app (`app/main.py`)**: CORS `*`, static files at `/static`, routers for auth/content/admin/logs/api. Lifespan startup initializes Langfuse tracing and the database. Health at `/health`.
- **Settings (`app/core/settings.py`)**: DB URL + pool tuning, JWT settings, worker timeouts, content length limits, API keys (OpenAI/Anthropic/Google/Exa), HTTP timeouts, Reddit creds, media/log paths, Crawl4AI toggles.
- **Database (`app/core/db.py`)**: lazy engine/session creation, `get_db()` context manager, `get_db_session()` dependency, optional `run_migrations()` helper.
- **Security (`app/core/security.py`)**: JWT create/verify; refresh/access expiries come from settings. Apple token verification currently skips signature validation (dev-only). Admin password check against env.
- **Dependencies (`app/core/deps.py`)**: `get_current_user` via JWT, `get_optional_user`, admin session guard (`require_admin`) using in-memory cookie store `admin_sessions`.
- **Logging (`app/core/logging.py`)**: root logger with structured format; `get_logger()` shortcut.

## Key Classes & Services
| Class | Location | Responsibilities | Key Methods |
|---|---|---|---|
| `Settings` | app/core/settings.py | Env-driven config (DB, JWT, API keys, paths, worker limits) | properties `podcast_media_dir`, `substack_media_dir`, `logs_dir` |
| `QueueService` | app/services/queue.py | DB-backed task queue (`scrape`/`analyze_url`/`process_content`/`download_audio`/`transcribe`/`summarize`/`generate_image`/`discover_feeds`/`onboarding_discover`/`dig_deeper`) with retries | `enqueue`, `dequeue`, `complete_task`, `retry_task`, `get_queue_stats`, `cleanup_old_tasks` |
| `CheckoutManager` | app/pipeline/checkout.py | Row-level locking for content checkout/release | `checkout_content` context, `release_stale_checkouts`, `get_checkout_stats` |
| `ContentWorker` | app/pipeline/worker.py | Process articles/news/podcasts: choose strategy, download/extract, summarize, persist | `process_content`, `_process_article`, `_process_podcast` |
| `PodcastDownloadWorker` / `PodcastTranscribeWorker` | app/pipeline/podcast_workers.py | Download audio (with retries) then transcribe via Whisper; queue follow-up tasks | `process_download_task`, `process_transcribe_task` |
| `ContentSummarizer` | app/services/llm_summarization.py | pydantic-ai summarization with per-type defaults (news digest + editorial narrative + interleaved variants) | `summarize`, `summarize_content` |
| `ContentAnalyzer` | app/services/content_analyzer.py | Fetch page text (trafilatura), detect embedded media/RSS, LLM classify type/platform | `analyze_url`, `_detect_media_in_html` |
| `StrategyRegistry` | app/processing_strategies/registry.py | Ordered URL strategy matching | `get_strategy`, `register`, `list_strategies` |
| `RobustHttpClient` | app/http_client/robust_http_client.py | Resilient HTTP GET/HEAD with retries/logging | `get`, `head`, `close` |
| `ScraperRunner` | app/scraping/runner.py | Orchestrates scrapers, logs stats to EventLog | `run_all(_with_stats)`, `run_scraper`, `list_scrapers` |
| `Chat Agent` | app/services/chat_agent.py | pydantic-ai agent with Exa tool, message persistence | `get_chat_agent`, `run_chat_turn`, `generate_initial_suggestions` |
| `DeepResearchClient` | app/services/deep_research.py | OpenAI Responses API client for async deep research | `start_research`, `poll_result`, `wait_for_completion`, `process_deep_research_message` |
| `ImageGenerationService` | app/services/image_generation.py | Gemini image generation (16:9 infographics) + local thumbnailing for generated assets | `generate_image`, `generate_thumbnail`, `get_image_url` |
| `Feed Detection` | app/services/feed_detection.py | Extract RSS/Atom links and classify feed type with LLM | `extract_feed_links`, `classify_feed_type_with_llm` |
| `Event Logger` | app/services/event_logger.py | Structured event logging to DB | `log_event`, `track_event`, `get_recent_events` |

## Database Schema (ORM in `app/models/schema.py`)
| Table | Purpose | Key Columns/Constraints |
|---|---|---|
| `users` | Accounts from Apple Sign In + admin | `id`, `apple_id` UQ, `email` UQ, `full_name`, `is_admin`, `is_active`, timestamps |
| `contents` | Core content records | `id`, `content_type` (`article/podcast/news/unknown`), `url` (canonical, UQ per type), `source_url` (original scraped/submitted URL), `title`, `source`, `platform`, `is_aggregate` (legacy, always false), `status`, `classification`, `error_message`, `retry_count`, checkout fields, `content_metadata` JSON (summary/interleaved, detected_feed, image_generated_at, thumbnail_url), timestamps, indexes on type/status/created_at |
| `processing_tasks` | Task queue | `task_type` (`scrape`/`analyze_url`/`process_content`/`download_audio`/`transcribe`/`summarize`/`generate_image`/`discover_feeds`/`onboarding_discover`/`dig_deeper`), `content_id`, `payload` JSON, `status`, retry counters, timestamps, idx on status+created_at |
| `content_read_status` | User read marks | UQ `(user_id, content_id)`, `read_at` |
| `content_favorites` | User favorites | UQ `(user_id, content_id)` |
| `content_unlikes` | User unlikes | UQ `(user_id, content_id)` |
| `content_status` | Per-user feed membership (inbox/archive) | UQ `(user_id, content_id)`, `status`, timestamps |
| `user_scraper_configs` | User-managed feeds (substack/atom/podcast_rss/youtube) | UQ `(user_id, scraper_type, feed_url)`, `config` JSON, `is_active` |
| `event_logs` | Structured event telemetry | `event_type`, `event_name`, `status`, `data` JSON, `created_at` |
| `chat_sessions` | Stored chat threads | `user_id`, `content_id` (optional), `title`, `session_type` (`article_brain/topic/ad_hoc/deep_research`), `topic`, `llm_model`, `llm_provider`, `last_message_at`, `is_archived` |
| `chat_messages` | Persisted pydantic-ai messages | `session_id`, `message_list` JSON (ModelMessagesTypeAdapter), `created_at` |

## Domain & Pydantic Types
- **Enums (app/models/metadata.py)**: `ContentType` (`ARTICLE/PODCAST/NEWS/UNKNOWN`), `ContentStatus` (`new/pending/processing/completed/failed/skipped`), `ContentClassification` (`to_read/skip`).
- **Metadata models**: `ArticleMetadata` (author, publication_date, content, word_count, summary), `PodcastMetadata` (audio_url, transcript, duration, episode_number, YouTube fields, thumbnail_url, summary), `NewsMetadata` (article: url/title/source_domain; aggregator info + `discussion_url`; discovery_time; summary `NewsSummary`).
- **Summaries**: `InterleavedSummary` (summary_type, hook, insights w/ supporting quotes, takeaway), `InterleavedSummaryV2` (key_points, topics, quotes, takeaway), `StructuredSummary` (title, overview, bullet_points, quotes, topics, questions, counter_arguments, classification, full_markdown), `EditorialNarrativeSummary` (headline + structured narrative), `BulletedSummary`, `NewsSummary` (title, article_url, key_points, summary, classification, summarization_date).
- **Domain wrapper**: `ContentData` (id, content_type, url, source_url, title, status, metadata dict + computed `display_title`, `short_summary`, `structured_summary`, `interleaved_summary`, `bullet_points`, `quotes`, `topics`, `transcript`; timestamps, retry/error fields). Interleaved summaries are normalized into bullets/quotes/topics for list views.
- **API schemas (app/routers/api/models.py)**: `ContentSummaryResponse`/`ContentDetailResponse` (includes `image_url`, `thumbnail_url`, `detected_feed`), `ContentListResponse`, `UnreadCountsResponse`, `SubmitContentRequest` + `ContentSubmissionResponse`, `ConvertNewsResponse`, `TweetSuggestionsRequest/Response`, chat DTOs (`ChatSessionSummaryResponse`, `ChatSessionDetailResponse`, `ChatMessageResponse`, `CreateChatSessionRequest/Response`, `SendChatMessageRequest`).

## API Surface (routers)
- **Auth (`/auth`, app/routers/auth.py)**: `POST /apple` (Apple Sign In, upsert user, returns JWT + optional OpenAI key), `POST /refresh`, admin login/logout pages (Jinja, in-memory sessions).
- **Content list/search (`/api/content`, app/routers/api/content_list.py)**: `GET /` (filters type/date/read, cursor pagination, only summarized/visible items), `GET /search`, `GET /unread-counts` (per type). Responses include `image_url` + `thumbnail_url` when available.
- **Content detail/actions**: `GET /{id}` (detail with validated metadata, `detected_feed`, `image_url`, `thumbnail_url`), `GET /{id}/chat-url` (ChatGPT deeplink), `POST /{id}/convert-to-article`, `POST /{id}/tweet-suggestions` (Gemini model defined in `TWEET_SUGGESTION_MODEL`).
- **State**: `POST /{id}/mark-read`, `POST /bulk-mark-read` (read_status), `POST /{id}/favorites/toggle` + `GET /favorites` (favorites).
- **User submissions**: `POST /submit` creates or reuses content with `content_type=unknown`, enqueues `ANALYZE_URL` (then `PROCESS_CONTENT`); returns `task_id` and `already_exists` flag.
- **User scraper configs (`/api/scrapers`)**: CRUD for per-user feed configs; validates `feed_url` and allowed types. Supports type filtering (`?type=podcast_rss` or `?types=substack,atom`) and returns derived `feed_url`/`limit` fields (limit optional 1–100, default 10).
- **Chat (`/api/chat`)**: list/create sessions, get session detail, send message (runs agent and persists), initial suggestions for article sessions. Session list includes `is_favorite`, `has_pending_message`, and `has_messages`.
- **Compatibility**: `app/routers/api_content.py` re-exports the API router for older imports; admin/content/logs routers serve Jinja pages.

## Ingestion & Processing Pipeline
- **Queueing**: Scrapers enqueue `PROCESS_CONTENT`; `/submit` enqueues `ANALYZE_URL` which then enqueues `PROCESS_CONTENT`. Tasks stored in `processing_tasks` with retry counts and `TaskStatus`.
- **Processor (`app/pipeline/sequential_task_processor.py`)**: polls queue, dispatches by `TaskType` via `TaskDispatcher` and per-task handlers in `app/pipeline/handlers/`, exponential backoff retries, graceful signal handling.
- **Analyze URL (`ANALYZE_URL`)**: `ContentAnalyzer` fetches page text (trafilatura), detects embedded media/RSS, and uses LLM classification; falls back to pattern-based detection for known platforms; writes platform/media metadata before `PROCESS_CONTENT`.
- **Checkout (`app/pipeline/checkout.py`)**: optional row-level locks for multi-worker safety; releases stale checkouts back to `new`.
- **ContentWorker flow (`app/pipeline/worker.py`)**:
  1) Load ORM → domain `ContentData`.
  2) Select strategy via `StrategyRegistry` (ordered; HackerNews → Arxiv → PubMed → YouTube → PDF → Image → HTML fallback).
  3) Download (sync/async) via strategy/RobustHttpClient; handle non-retryable HTTP errors.
  4) Extract structured data; handle delegation (`next_url_to_process`), skips (images), or aggregator normalization.
  5) Prepare LLM payload; enqueue `SUMMARIZE` task (pydantic-ai prompts in `app/services/llm_prompts.py`).
  6) Persist extraction metadata, set `status=processing`, set `processed_at`; summarization task later writes summary + final status and enqueues image generation.
- **Podcasts**: `PodcastDownloadWorker` saves audio under `settings.podcast_media_dir`, skips YouTube audio, enqueues transcribe; `PodcastTranscribeWorker` runs Whisper (`app/services/whisper_local.py`), updates metadata, sets status to `completed`.
- **Summarization defaults**: per-type default models (news → `anthropic:claude-haiku-4-5-20251001`; article/podcast/editorial_narrative → `openai:gpt-5.2`; interleaved/long_bullets fallback hint `google-gla:gemini-3-pro-preview`; fallback model `google-gla:gemini-2.5-flash-preview-09-2025`), fallback Gemini Flash for failures; truncates content above 220k chars and prunes empty quotes.
- **Image generation**: post-summary tasks call Gemini for article/podcast infographics (`GENERATE_IMAGE`); news currently skips post-summary image generation. Generated images are stored under `IMAGES_BASE_DIR` (default `/data/images`) and served at `/static/images/...`.

## Processing Strategies (`app/processing_strategies/`)
- **HackerNewsProcessorStrategy**: handles HN item URLs, extracts linked article, metadata.
- **ArxivProcessorStrategy**: converts `/abs/` to PDF, feeds PDF strategy.
- **PubMedProcessorStrategy**: domain-specific extraction, may delegate.
- **YouTubeProcessorStrategy**: extracts transcript/metadata; skips summarization when no transcript; stores `thumbnail_url` when available.
- **PdfProcessorStrategy**: fetches bytes, base64 encodes for multimodal LLM prompt.
- **ImageProcessorStrategy**: detects image URLs and marks `skip_processing`.
- **HtmlProcessorStrategy**: Crawl4AI render + BeautifulSoup metadata; extracts RSS/Atom feed links for user submissions; fallback for general web pages.

## Scrapers & Dynamic Feeds
- **BaseScraper (`app/scraping/base.py`)**: ensures `platform` (scraper name) and immutable `source` (feed name/domain); dedupes by URL+type; sets `status=new`, queues processing; ensures per-user inbox rows for articles/podcasts.
- **Runner (`app/scraping/runner.py`)**: sequentially runs `HackerNewsUnifiedScraper`, `RedditUnifiedScraper`, `SubstackScraper`, `TechmemeScraper`, `PodcastUnifiedScraper`, `AtomScraper` (Twitter/YouTube currently disabled); logs stats via `EventLog`.
- **User-managed scrapers**: configs stored in `user_scraper_configs`; `build_feed_payloads` converts configs into feed inputs for scrapers.
- **File-backed configs**: `app/utils/paths.py` resolves `config/` defaults and env overrides such as `NEWSAPP_CONFIG_DIR`, while `config/twitter.yml` and `config/youtube.yml` remain available even though those scheduled scrapers are disabled in the default runner.

## User-Driven Content & State
- **Submissions (`POST /api/submit`)**: normalizes URL, creates `content_type=unknown`, sets `source="self submission"`, enqueues `ANALYZE_URL` then `PROCESS_CONTENT`, ensures `content_status` inbox row for the submitter. HTML extraction may detect RSS/Atom feeds and stores `detected_feed` metadata.
- **Per-user status**: `ContentStatusEntry` controls visibility in list/search (articles/podcasts require inbox entry; news always visible); `classification` column mirrors summary classification for filtering skips.
- **Read/Favorite/Unlike**: services in `app/services/read_status.py` and `app/services/favorites.py` manage idempotent inserts; favoriting also creates a ChatSession (and unfavorite removes empty sessions); list/search include state flags.
- **Conversions/Tweets**: `convert-to-article` clones news article URL into a new article; tweet suggestions generated via Gemini 3 Pro (`google-gla:gemini-3-pro-preview`, `app/services/tweet_suggestions.py`).

## Deep-Dive Chat
- **Data model**: `chat_sessions` + `chat_messages` (serialized pydantic-ai `ModelMessage` lists). Sessions store provider/model, topic/session_type, archive flag; messages track async `status` + `error`. List responses surface `is_favorite`, `has_messages`, `has_pending_message`.
- **Agent**: `app/services/chat_agent.py` builds pydantic-ai `Agent` with system prompt + Exa search tool (`exa_web_search`); article context pulled from summaries/content; model resolution via `app/services/llm_models.py` (OpenAI/Anthropic/Google with API-key aware construction).
- **Deep Research**: `app/services/deep_research.py` uses OpenAI's Responses API with `o4-mini-deep-research-2025-06-26` model for comprehensive research. Runs as a background task with web search and code interpreter tools. Uses `background=True` mode with ~2-second polling via `AsyncOpenAI` SDK (up to 10 minutes by default).
- **Session Types**: `article_brain` (dig deeper into article), `topic` (search/corroborate), `ad_hoc` (general chat), `deep_research` (comprehensive async research).
- **Endpoints**: create/list/get sessions, send messages (runs agent, appends DB message list), initial suggestions for article sessions; list includes favorite/pending flags. Deep research sessions route to `process_deep_research_message` background task instead of pydantic-ai agent. Message displays extract user/assistant text from stored message lists and may add a compact `process_summary` display row for intermediate tool/thinking activity without changing session previews.

## iOS Client (high level)
- Located in `client/newsly/`; SwiftUI app uses Apple Sign In → `POST /auth/apple`, stores JWT in Keychain, refreshes via `/auth/refresh`.
- API client attaches Bearer tokens, retries on 401 with refresh token; consumes list/search/detail/read/favorites, submission, chat, tweet suggestions endpoints.
- Views model feed (list/search with cursor), detail, favorites, chat sessions/messages; supports share-sheet submissions and topic/ad-hoc chats; Knowledge tab unifies favorites + chat sessions.
- **Rendering**: content detail renders interleaved summaries when `summary_type=interleaved`; list views prefer `thumbnail_url` for progressive image loading with local caching.
- **Share Extension**: `client/newsly/ShareExtension` target reads shared auth token (Keychain/app group) and submits URLs to `POST /api/content/submit`.
- **Chat UI**: Chat list shows session type icons (dig deeper, search, deep research, chat). Article detail "Start a Chat" sheet offers Dig Deeper, Corroborate, Deep Research, and voice input options. `ChatModelProvider` enum defines available providers (OpenAI, Anthropic, Google, Deep Research).

## Security & Observability Notes
- **Gaps**: Apple token signature verification disabled (dev only) in `app/core/security.py`; admin sessions stored in-memory (`app/routers/auth.py`); CORS allows all origins; JWT secret/ADMIN_PASSWORD must be provided via env.
- **Logging/Telemetry**: request logging middleware; structured `EventLog` via `log_event/track_event`; processing errors use `logger.error()`/`logger.exception()` with structured `extra` fields (`component`, `operation`, `item_id`, `context_data`); errors at ERROR+ level auto-written to JSONL files in `logs/errors/` with sensitive data redaction.
- **Storage**: media/log paths default to `./data/media` and `./logs` (settings override); podcast downloads sanitized to filesystem-safe names.

## Data Flow Cheat Sheet
1) Scrapers create `contents` rows → enqueue `PROCESS_CONTENT`; `/submit` creates `content_type=unknown` → enqueue `ANALYZE_URL` → `PROCESS_CONTENT`.
2) `SequentialTaskProcessor` dequeues → `ContentWorker` selects strategy, downloads, extracts, summarizes (interleaved/news digest) → updates `contents.status` + metadata/summary.
3) Podcasts: download → transcribe → summarize; tasks chained via queue.
4) Summaries enqueue `GENERATE_IMAGE` for article/podcast content; news does not enqueue a post-summary image task.
5) API list/search filters: only summarized articles/podcasts + completed news, excludes `classification=skip`, requires `content_status` inbox rows for per-user feeds.
6) Chat sessions reference `contents` (optional) and persist assistant/user messages; Exa search tool available when `EXA_API_KEY` is set.
7) Deep research sessions use OpenAI Responses API: message sent → background task polls for completion (up to ~10 min) → result persisted to `chat_messages`.
