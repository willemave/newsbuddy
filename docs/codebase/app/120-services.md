# app/services/

Source folder: `app/services`

## Purpose
Business-logic layer for content ingestion/lifecycle, LLM use, chat, discovery, feeds, news, audio, Learning Decks, integrations, prompt loading, cost/usage telemetry, queue primitives, and external provider adapters.

## Runtime behavior
- Routers, commands, queries, handlers, scripts, and admin pages call services for orchestration that should not live in HTTP handlers.
- Services coordinate repositories, models, queueing, prompt templates, external APIs, object storage, and vendor usage/cost recording.
- External-provider code is intentionally isolated behind focused modules or gateways so tests can patch narrow seams.

## Major service groups
| Group | Files | Purpose |
|---|---|---|
| Content ingestion and lifecycle | `content_submission.py`, `content_analyzer.py`, `content_lifecycle.py`, `content_status_state_machine.py`, `content_metadata_merge.py`, `content_bodies.py`, `source_metadata.py`, `long_form_images.py`, `interesting_external_links.py`, `instruction_links.py`, `url_detection.py` | Submit, analyze, process, persist body storage pointers, merge metadata, infer URL types, and manage generated image eligibility. |
| Summarization and prompts | `llm_agents.py`, `llm_models.py`, `llm_prompts.py`, `llm_summarization.py`, `summarization_templates.py`, `prompt_library.py`, `prompt_debug_report.py`, `summary_eval.py` | LLM model construction, prompt loading, summarization orchestration, eval helpers, and prompt diagnostics. |
| Chat and assistant surfaces | `chat_agent.py`, `council_chat.py`, `deep_research.py`, `dig_deeper.py`, `assistant_router.py`, `assistant_feed_finder.py`, `assistant_eval.py`, `weekly_discovery_chat.py`, `knowledge_search.py` | Article/Knowledge chat, council branches, deep research, feed-finding, dig-deeper, and assistant evals. |
| Discovery, feeds, and onboarding | `feed_detection.py`, `feed_resolution.py`, `feed_discovery.py`, `feed_subscription.py`, `feed_backfill.py`, `scraper_configs.py`, `scraper_config_validation.py`, `onboarding.py`, `podcast_search.py`, `apple_podcasts.py` | Feed detection/resolution/subscription, backfills, onboarding discovery, podcast lookup, and user scraper configuration. |
| News pipeline | `news_feed.py`, `news_ingestion.py`, `news_processing.py`, `news_article_bodies.py`, `news_article_enrichment.py`, `news_discussion_summaries.py`, `news_item_discussions.py`, `news_relevant_links.py`, `news_relations.py`, `news_embeddings.py`, `news_reranker.py` | Fast Reads visibility, ingestion, enrichment, discussion summaries, search/ranking context, embeddings, and reranking. |
| Audio and narration | `audio_episodes.py`, `audio_episode_kinds.py`, `audio_episode_sources.py`, `audio_episode_tokens.py`, `audio_pipeline.py`, `custom_narrations.py`, `summary_narration.py`, `whisper_local.py` | Audio episode lifecycle, scripts/sources/tokens, media download/transcription, custom narrations, and local Whisper support. |
| Learning Decks | `learning_decks.py`, `learning_deck_agent.py`, `learning_deck_artifacts.py`, `learning_deck_common.py`, `learning_deck_generation.py`, `learning_deck_hosting.py`, `learning_deck_sandbox.py`, `learning_deck_sources.py`, `learning_deck_theme.py`, `learning_deck_tokens.py`, `learning_deck_viewer.py` | Deck creation/reruns, source extraction, generation, sandbox execution, artifact storage, hosting, private/share tokens, and viewer assets. |
| External providers | `openai_llm.py`, `firecrawl_client.py`, `exa_client.py`, `image_generation.py`, `arxiv_metadata.py`, `pdf_text_extraction.py`, `twitter_share.py`, `x_api.py`, `x_integration.py`, `x_tweet_metadata.py`, `youtube_equivalent_resolver.py`, `http.py` | OpenAI transcription, Firecrawl, Exa, image generation, arXiv/PDF helpers, X/Twitter, YouTube equivalence, and HTTP error classification. |
| User state and integrations | `knowledge.py`, `read_status.py`, `content_interactions.py`, `cli_link.py`, `personal_markdown_library.py`, `token_crypto.py`, `tweet_suggestions.py` | Knowledge/read state, interaction analytics, CLI QR linking, personal markdown library, encrypted tokens, and tweet suggestions. |
| Queue, usage, and cost | `queue.py`, `vendor_usage.py`, `vendor_costs.py`, `langfuse_tracing.py` | Queue persistence and status, model/storage usage aggregation, vendor cost calculations, and tracing helpers. |
| Admin and reports | `admin_eval.py`, `insight_report.py`, `longform_artifact_prompts.py`, `longform_artifact_routing.py`, `sandbox_runtime.py` | Admin evals, insight reports, long-form artifact routing/prompts, and sandbox runtime helpers. |

## Runtime dependencies
- `yt_dlp` and YouTube config support audio/video extraction.
- ElevenLabs is used by backend narration TTS in `app/services/voice`.
- Learning Decks can use local or E2B-style sandbox execution and object storage.
- News embeddings/reranking depend on optional local ML packages such as `sentence-transformers`, `torch`, and `transformers`.
- Provider costs and usage flow through `vendor_usage.py` and `vendor_costs.py`.

## Integration points
- Queue handlers call services for most production work.
- Gateways under `app/services/gateways` isolate HTTP, LLM, queue, and object storage dependencies.
- Prompt text is stored under `app/prompts`.
