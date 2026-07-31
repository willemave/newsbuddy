# app/services/voice/

Source folder: `app/services/voice`

## Purpose
Backend narration text-to-speech helpers. This folder no longer contains the older live-voice websocket/orchestration stack.

## Runtime behavior
- `narration_tts.py` provides `ContentNarrationTtsService` for one-shot MP3 generation from plain summary text or multi-speaker dialogue turns.
- The service uses ElevenLabs speech settings from `app/core/settings.py`, records structured logs, and writes vendor usage/cost telemetry.
- Flash v2.5 accepts 40,000 characters per provider request. The service uses lossless 35,000-character chunks, so long preauthored Briefings and generated dialogue turns remain below that boundary.
- Multi-chunk MP3 stitching scales its ffmpeg timeout with chunk count, capped at five minutes.
- Public narration/audio episode APIs call higher-level services in `app/services/audio_episodes/__init__.py`, `summary_narration.py`, and related modules.

## Important files
| File | Purpose |
|---|---|
| `narration_tts.py` | ElevenLabs Flash v2.5 one-shot narration and dialogue MP3 synthesis. |
| `__init__.py` | Package marker. |

## Integration points
- Client-side voice dictation/transcription lives in the iOS client services; backend OpenAI transcription endpoints live in `app/routers/api/openai.py` and `app/services/openai_llm.py`.
