# app/pipeline/handlers/

Source folder: `app/pipeline/handlers`

## Purpose
Concrete queue task handlers that translate task envelopes into service calls or worker actions for each supported task type.

## Runtime behavior
- Keeps task-specific orchestration out of the processor loop by giving each task type its own handler class.
- Bridges queue payloads into service and worker calls for content analysis, processing, discovery, onboarding, images, chat, and integrations.
- Provides the place where retryability and task-result mapping become explicit per task type.

## Inventory scope
- Direct file inventory for `app/pipeline/handlers`.

## Modules and files
| File | Key symbols | Notes |
|---|---|---|
| `app/pipeline/handlers/__init__.py` | n/a | Task handlers for the sequential pipeline processor. |
| `app/pipeline/handlers/analyze_url.py` | `FlowOutcome`, `FeedSubscriptionFlow`, `TwitterShareFlow`, `UrlAnalysisFlow`, `InstructionLinkFanout`, `InstructionPayloadCleaner`, `AnalyzeUrlHandler` | Analyze URL task handler. |
| `app/pipeline/handlers/dig_deeper.py` | `DigDeeperHandler` | Dig-deeper task handler. |
| `app/pipeline/handlers/discover_feeds.py` | `DiscoverFeedsHandler` | Feed discovery task handler. |
| `app/pipeline/handlers/download_audio.py` | `DownloadAudioHandler` | Podcast audio download task handler. |
| `app/pipeline/handlers/fetch_discussion.py` | `FetchDiscussionHandler` | Discussion fetch task handler. |
| `app/pipeline/handlers/generate_daily_news_digest.py` | `GenerateDailyNewsDigestHandler` | Task handler for per-user daily news digest generation. |
| `app/pipeline/handlers/generate_image.py` | `GenerateImageHandler` | Image generation task handler. |
| `app/pipeline/handlers/onboarding_discover.py` | `OnboardingDiscoverHandler` | Onboarding discovery enrichment task handler. |
| `app/pipeline/handlers/process_content.py` | `ProcessContentHandler` | Content processing task handler. |
| `app/pipeline/handlers/scrape.py` | `ScrapeHandler` | Scrape task handler. |
| `app/pipeline/handlers/summarize.py` | `SummarizeHandler` | Summarization task handler. |
| `app/pipeline/handlers/sync_integration.py` | `SyncIntegrationHandler` | Task handler for scheduled external integration sync jobs. |
| `app/pipeline/handlers/transcribe.py` | `TranscribeHandler` | Podcast transcription task handler. |
