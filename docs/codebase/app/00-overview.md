# Backend Reference

Folder-by-folder reference for the FastAPI backend, DB-backed queue workers, scraper stack, prompt library, and service layer.

## What this section covers
- Start here when you need the backend map before diving into a module group.
- Each linked document describes ownership, important files, integration points, and runtime dependencies for the matching source folder or focused surface.

## Documents
| Doc | Source folder | Focus |
|---|---|---|
| `10-root.md` | `app` | FastAPI bootstrap, static mounts, request middleware, health check, OpenAPI operation IDs, and router registration. |
| `20-core.md` | `app/core` | Runtime infrastructure: settings, DB/session lifecycle, auth/security, FastAPI dependencies, logging, observability, redaction, model defaults, and timing helpers. |
| `30-domain.md` | `app/models/domain` | Internal domain transfer objects, content mappers, display/form helpers, chat render metadata, discovery/user-profile objects, and scraper run stats. |
| `35-commands.md` | `app/commands` | Router-facing write/use-case entrypoints for submissions, read/Knowledge actions, onboarding, API keys, integrations, tweet suggestions, and content conversion. |
| `36-queries.md` | `app/queries` | Router-facing read entrypoints for content cards/details, Knowledge, discussions, narration, jobs, queue health, stats, search, integrations, and submission status. |
| `40-http-client.md` | `app/http_client` | Synchronous robust HTTP wrapper used by URL processing strategies and gateways. |
| `50-models.md` | `app/models` | SQLAlchemy ORM rows, public API DTOs, shared contracts, metadata payloads, internal payloads, and LLM schemas. |
| `55-prompts.md` | `app/prompts` | Markdown prompt library grouped by feature, used through prompt-loading helpers and eval/report scripts. |
| `60-pipeline.md` | `app/pipeline` | Queue execution runtime, task specs, processor loop, dispatch, task envelopes/results, and content/podcast worker implementations. |
| `61-pipeline-handlers.md` | `app/pipeline/handlers` | Concrete queue task handlers for scraping, URL analysis, processing, summarization, discussions, media, integrations, audio episodes, and learning decks. |
| `62-pipeline-workflows.md` | `app/pipeline/workflows` | Focused workflow helpers for analyze-URL and content-lifecycle transitions inside larger handlers. |
| `70-presenters.md` | `app/routers/api/content_responses.py`, `app/models/domain/content_display.py` | Content response builders, display helpers, and router-facing projection adapters. |
| `80-processing-strategies.md` | `app/processing_strategies` | Ordered URL-specific extraction strategies for HN, arXiv, PubMed, YouTube, tweets, PDFs, images, plain text, and HTML. |
| `90-repositories.md` | `app/repositories` | SQLAlchemy query and persistence helpers for content, Knowledge, read state, stats, API keys, search, and integrations. |
| `95-admin-web.md` | `app/admin_web` | Server-rendered admin controllers, templates, static assets, auth routes, logs/errors, evals, onboarding preview, usage, and API-key pages. |
| `100-routers.md` | `app/routers` | Top-level auth router and compatibility content router aggregation. |
| `101-routers-api.md` | `app/routers/api` | JSON API modules for content, news, chat, discovery, onboarding, audio episodes, learning decks, integrations, feedback, agent APIs, OpenAI transcription, and stats. |
| `110-scraping.md` | `app/scraping` | Scheduled scraper runner, base scraper persistence, feed/site scrapers, discussion comment scraping, and YAML/DB-backed scraper inputs. |
| `111-scraping-aggregators.md` | `app/scraping/aggregators` | YAML-backed news aggregator scraper registry for HN, Techmeme-network feeds, SciURLs/FinURLs, and Brutalist Report. |
| `120-services.md` | `app/services` | Business-logic layer for content, news, chat, discovery, feeds, audio, learning decks, prompts, integrations, cost/usage telemetry, and queue primitives. |
| `121-services-gateways.md` | `app/services/gateways` | Narrow gateway interfaces for HTTP, LLM, queue, and object storage dependencies. |
| `122-services-voice.md` | `app/services/voice` | Backend narration TTS helper surface. |
| `130-utils.md` | `app/utils` | Cross-cutting URL, date, pagination, path, title, metadata, summary, JSON-repair, image, and error helpers. |
| `140-testing.md` | `app/testing` | Backend test harness helpers, currently PostgreSQL fixture support. |

## Concat command
```bash
find docs/codebase/app -type f -name '*.md' | sort | xargs cat
```
