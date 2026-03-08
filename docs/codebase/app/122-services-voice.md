# app/services/voice/

Source folder: `app/services/voice`

## Purpose
Live voice subsystem for streaming STT/TTS, session management, chat persistence, and assistant orchestration across the realtime voice experience.

## Runtime behavior
- Creates and manages live voice sessions, including intro-state tracking, persistence, and reconnection behavior.
- Bridges audio capture/playback, ElevenLabs streaming, narration TTS, and agent orchestration into the websocket-based voice API.
- Stores or reconstructs live conversation context so voice turns can continue from content detail or chat session state.

## Inventory scope
- Direct file inventory for `app/services/voice`.

## Modules and files
| File | Key symbols | Notes |
|---|---|---|
| `app/services/voice/__init__.py` | n/a | Voice conversation services package. |
| `app/services/voice/agent_streaming.py` | `VoiceAgentDeps`, `VoiceAgentResult`, `get_voice_agent`, `stream_voice_agent_turn` | Haiku-based streaming agent for in-house voice conversations. |
| `app/services/voice/elevenlabs_streaming.py` | `ElevenLabsSttCallbacks`, `elevenlabs_sdk_available`, `build_voice_health_flags`, `open_realtime_stt_connection`, `send_audio_frame`, `commit_audio`, `close_stt_connection`, `build_realtime_tts_stream`, `next_tts_chunk` | ElevenLabs streaming helpers for realtime STT/TTS. |
| `app/services/voice/narration_tts.py` | `DigestNarrationTtsService`, `get_digest_narration_tts_service` | One-shot narration helpers for digest audio playback. |
| `app/services/voice/orchestrator.py` | `TurnOutcome`, `VoiceConversationOrchestrator` | Realtime voice turn orchestration for STT -> LLM -> TTS streaming. |
| `app/services/voice/persistence.py` | `VoiceContentContext`, `load_voice_content_context`, `format_voice_content_context`, `resolve_or_create_voice_chat_session`, `persist_voice_turn`, `mark_live_voice_onboarding_complete`, `build_live_intro_text` | Persistence and context helpers for live voice sessions. |
| `app/services/voice/session_manager.py` | `VoiceSessionState`, `create_voice_session`, `configure_voice_session`, `set_voice_session_intro_pending`, `get_voice_session`, `get_message_history`, `append_message_history`, `prune_voice_sessions`, `clear_voice_sessions` | In-memory session manager for realtime voice conversations. |
