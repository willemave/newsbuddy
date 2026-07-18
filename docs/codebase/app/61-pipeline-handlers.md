# app/pipeline/handlers/

Source folder: `app/pipeline/handlers`

## Purpose
Concrete queue task handlers that translate normalized task envelopes into service calls, worker calls, and workflow transitions.

## Runtime behavior
- Each handler owns one `TaskType` and returns `TaskResult.ok()` or `TaskResult.fail(...)` to control completion and retry behavior.
- URL analysis handles feed subscription, tweet resolution, instruction link fanout, and content-processing handoff.
- Tweet video download/transcription failures degrade to text summarization instead of retrying forever.
- Learning Deck generation retries only when the generation service asks the queue to wait; validation and unexpected generation failures are nonretryable.
- Integration sync rejects missing users/unsupported providers as nonretryable and cleanly skips when X sync is disabled.

## Important files
| File | Purpose |
|---|---|
| `analyze_url.py` | URL analysis handler with feed subscription, tweet resolution, instruction cleanup, and fanout flows. |
| `backfill_feeds.py` | Feed backfill task handler. |
| `briefing_refresh.py` | Briefing append/sweep/full refresh task handler. |
| `dig_deeper.py` | Chat/dig-deeper message processing. |
| `discover_feeds.py` | Feed discovery task handler. |
| `download_audio.py`, `transcribe.py`, `process_podcast_media.py` | Podcast/audio media pipeline handlers. |
| `download_tweet_video.py`, `transcribe_tweet_video.py` | Tweet video/audio media handlers with graceful degradation. |
| `enrich_news_item_article.py`, `process_news_item.py` | Short-form news enrichment and processing handlers. |
| `fetch_discussion.py`, `fetch_news_item_discussion.py` | Content and news-item discussion refresh/summary handlers. |
| `generate_audio_episode.py` | On-demand audio episode generation. |
| `generate_image.py` | Long-form generated image handler. |
| `generate_learning_deck.py` | Learning Deck generation handler. |
| `onboarding_discover.py` | Onboarding discovery enrichment. |
| `process_content.py`, `summarize.py` | Long-form content processing and summarization. |
| `scrape.py` | Scheduled scraper task handler. |
| `sync_integration.py` | External integration sync jobs. |

## Integration points
- Handler construction lives in `SequentialTaskProcessor._build_handlers`.
- Workflow helpers in `app/pipeline/workflows` keep complex state transitions out of individual handler methods.
