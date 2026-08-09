# app/routers/api/

Source folder: `app/routers/api`

## Purpose
User-facing and machine-facing JSON API modules for content, news, chat, onboarding, audio episodes, Learning Decks, integrations, feedback, stats, and OpenAI transcription.

## Runtime behavior
- Modules are narrow route groups; DTOs live in `app/models/api`, not in this folder.
- Some modules are mounted through `/api/content` for compatibility while others are mounted directly under `/api`.
- `scraper_configs.router` is mounted both under `/api/content/scrapers*` and `/api/scrapers*`.
- Learning Decks expose authenticated `/api/learning/decks*` endpoints and public/private hosted artifact routes under `/learning/share/{token}/*` and `/learning/signed/{token}/*`.
- Audio episodes expose authenticated content-adjacent endpoints and public `/audio/share/{token}/*` playback artifacts.
- `openai.py` currently exposes transcription health/upload routes; realtime token setup is not part of the live router.

## Important files
| File | Purpose |
|---|---|
| `agent.py` | Machine/CLI APIs for jobs, search, onboarding, CLI link, and personal markdown library. |
| `audio_episodes.py` | Audio episode create/list/share/playback routes. |
| `briefing.py` | Briefing index/lens/read-mark/refresh/dig/narration routes. |
| `chat.py` | Chat session/message lifecycle and initial suggestions. |
| `content_actions.py` | Convert, download-more, tweet suggestions, and related content actions. |
| `content_detail.py` | Content detail, discussion, body/chat URL reads. |
| `content_list.py` | Content list/search and podcast episode matching. |
| `content_responses.py` | Router-facing content response builders. |
| `feedback.py` | User feedback submission. |
| `integrations.py` | X OAuth/connection and user LLM integration endpoints. |
| `interactions.py` | Content interaction analytics. |
| `knowledge.py` | Save/remove/list Knowledge routes. |
| `learning_decks.py` | Learning Deck CRUD, share URLs, hosted artifact serving. |
| `narration.py` | Narration/audio availability and playback data. |
| `news.py` | Fast Reads list/detail/body/discussion/read-state/convert/audio surface. |
| `onboarding.py` | Onboarding profile, voice parse, fast/audio discovery, completion, tutorial state. |
| `openai.py` | Audio transcription availability/upload. |
| `read_status.py` | Read/unread/recently-read actions. |
| `scraper_configs.py` | Per-user source config CRUD and feed subscription. |
| `stats.py` | Unread, processing, and long-form count endpoints. |
| `submission.py` | One-off URL submission and submission-status listing. |

## Integration points
- Request/response models live under `app/models/api`.
- Use commands for write orchestration and queries for read projections.
