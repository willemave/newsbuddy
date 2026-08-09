# app/pipeline/handlers/

Source folder: `app/pipeline/handlers`

## Purpose
Concrete queue task handlers that translate normalized task envelopes into service calls, worker calls, and workflow transitions.

## Runtime behavior
- Each handler owns one `TaskType` and returns `TaskResult.ok()` or `TaskResult.fail(...)` to control completion and retry behavior.
- URL analysis handles feed subscription, tweet resolution, instruction link fanout, and content-processing handoff.
- Tweet video download/transcription failures degrade to text summarization instead of retrying forever.
- Learning Deck generation runs through the generic `run_llm_task` workflow handler.
- Integration sync rejects missing users/unsupported providers as nonretryable and cleanly skips when X sync is disabled.

## Important files
| File | Purpose |
|---|---|
| `analyze_url.py` | URL analysis handler with feed subscription, tweet resolution, instruction cleanup, and fanout flows. |
| `backfill_feeds.py` | Feed backfill task handler. |
| `briefing_refresh.py` | Briefing append/sweep/full refresh task handler. |
| `dig_deeper.py` | Chat/dig-deeper message processing. |
| `discover_feeds.py` | Feed discovery task handler. |
| `process_podcast_media.py` | Unified podcast download, normalization, and transcription handler. |
| `download_tweet_video.py`, `transcribe_tweet_video.py` | Tweet video/audio media handlers with graceful degradation. |
| `enrich_news_item_article.py`, `process_news_item.py` | Short-form news enrichment and processing handlers. |
| `fetch_news_item_discussion.py` | News-item discussion refresh/summary handler. |
| `generate_audio_episode.py` | On-demand audio episode generation. |
| `generate_image.py` | Long-form generated image handler. |
| `onboarding_discover.py` | Onboarding discovery enrichment. |
| `process_content.py`, `summarize.py` | Long-form content processing and summarization. |
| `scrape.py` | Scheduled scraper task handler. |
| `sync_integration.py` | External integration sync jobs. |

## Integration points
- Queue-scoped handler construction lives in `app/pipeline/handler_registry.py`.
- Workflow helpers in `app/pipeline/workflows` keep complex state transitions out of individual handler methods.
