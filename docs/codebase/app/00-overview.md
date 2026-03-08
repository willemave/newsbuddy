# Backend Reference

Folder-by-folder reference for the FastAPI backend, queue workers, scraper stack, and service layer.

## What this section covers
- Start here when you want the backend map before diving into a specific module group.
- Each linked document inventories direct files in the corresponding source folder unless noted otherwise.

## Documents
| Doc | Source folder | Focus |
|---|---|---|
| `10-root.md` | `app` | Application root wiring for the FastAPI server, shared constants, and the Jinja environment bridge used by admin pages. |
| `20-core.md` | `app/core` | Core runtime infrastructure: environment settings, database/session lifecycle, security primitives, FastAPI dependencies, and shared logging/timing helpers. |
| `30-domain.md` | `app/domain` | Thin domain translation layer between SQLAlchemy ORM rows and the normalized `ContentData` model used by presenters and pipeline code. |
| `40-http-client.md` | `app/http_client` | Resilient low-level HTTP access used by scrapers and URL processors when they need retries, headers, and failure classification outside of higher-level services. |
| `50-models.md` | `app/models` | Shared data model layer containing SQLAlchemy ORM tables, Pydantic request/response contracts, metadata payloads, enums, pagination types, and scraper/discovery DTOs. |
| `60-pipeline.md` | `app/pipeline` | Queue execution runtime: processor loop, task envelopes/results, dispatcher, checkout coordination, and the main content/podcast worker implementations. |
| `61-pipeline-handlers.md` | `app/pipeline/handlers` | Concrete queue task handlers that translate task envelopes into service calls or worker actions for each supported task type. |
| `62-pipeline-workflows.md` | `app/pipeline/workflows` | Focused workflow helpers that model multi-step state transitions inside larger queue handlers, especially URL analysis and content processing. |
| `70-presenters.md` | `app/presenters` | Presentation shaping layer that turns domain content into list/detail API responses with image URLs, readiness checks, and feed-subscription affordances. |
| `80-processing-strategies.md` | `app/processing_strategies` | Ordered URL-specific extraction strategies used by the content worker to turn raw URLs into normalized article, podcast, PDF, or discussion payloads. |
| `90-repositories.md` | `app/repositories` | Query composition helpers for content feeds and visibility rules used by list, search, stats, and recently-read endpoints. |
| `100-routers.md` | `app/routers` | Top-level FastAPI routers for authentication, admin pages, admin diagnostics, and the compatibility bridge that mounts the API router under legacy imports. |
| `101-routers-api.md` | `app/routers/api` | User-facing JSON API surface for content, chat, discovery, onboarding, voice, integrations, stats, submissions, and auxiliary OpenAI/realtime endpoints. |
| `110-scraping.md` | `app/scraping` | Scheduled feed and site scrapers plus the orchestration runner that inserts new content rows and enqueues downstream processing. |
| `120-services.md` | `app/services` | Business-logic layer for LLM access, content analysis and submission, chat, discovery, feeds, images, interactions, onboarding, event logging, and queue primitives. |
| `121-services-gateways.md` | `app/services/gateways` | Narrow gateway interfaces that isolate HTTP, LLM, and queue dependencies for higher-level services and workflows. |
| `122-services-voice.md` | `app/services/voice` | Live voice subsystem for streaming STT/TTS, session management, chat persistence, and assistant orchestration across the realtime voice experience. |
| `130-utils.md` | `app/utils` | Cross-cutting utility functions for URLs, pagination, dates, filesystem paths, error logging, summary normalization, and image path/URL handling. |

## Concat command
```bash
find docs/codebase/app -type f -name '*.md' | sort | xargs cat
```
