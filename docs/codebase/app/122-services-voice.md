# app/services/voice/

Source folder: `app/services/voice`

## Purpose
Backend narration text-to-speech helpers. This folder no longer contains the older live-voice websocket/orchestration stack.

## Runtime behavior
- `narration_tts.py` provides `ContentNarrationTtsService` for one-shot MP3 generation from plain summary text or multi-speaker dialogue turns.
- The service uses ElevenLabs settings from `app/core/settings.py`, records structured logs, and writes vendor usage/cost telemetry.
- Public narration/audio episode APIs call higher-level services in `app/services/audio_episodes.py`, `summary_narration.py`, and related modules.

## Important files
| File | Purpose |
|---|---|
| `narration_tts.py` | ElevenLabs-backed one-shot narration and dialogue MP3 synthesis. |
| `__init__.py` | Package marker. |

## Integration points
- Client-side voice dictation/transcription lives in the iOS client services; backend OpenAI transcription endpoints live in `app/routers/api/openai.py` and `app/services/openai_llm.py`.
