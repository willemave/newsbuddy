# Speech-First Onboarding (iOS) - OpenAI Realtime

## Summary
Replace the profile form fields in iOS onboarding with a speech-first capture experience powered by the OpenAI Realtime API. Users speak about the kinds of news they like to read while a live transcript streams on-screen. When they stop, the transcript is sent to the backend for LLM extraction of `first_name` and `interest_topics`, then the onboarding pipeline uses those interests to create query lanes.

## Goals
- Make profile capture feel fast, interactive, and conversational.
- Live, streaming transcript while the microphone is active.
- Convert transcript into onboarding fields (name + interests) and build query lanes.
- Preserve existing onboarding endpoints and downstream flow where possible.
- Provide a text-input fallback if microphone access fails.

## Non-Goals
- Changing fast-discover or completion logic.
- Web onboarding changes.
- Server-side audio streaming or realtime proxying.

## Scope
- iOS onboarding profile step (`OnboardingFlowView` / `OnboardingViewModel`).
- New iOS realtime transcription client.
- New backend endpoint(s) to mint ephemeral tokens and to parse transcript into fields.

## UX Flow Changes (Screen 2 - Profile)
**Current**: Text fields for first name + Twitter/LinkedIn handle.

**New**:
1) **Mic CTA card**
   - Title: "Tell us about yourself"
   - Subtitle: "Say your name and what kinds of news you want"
   - Primary action: "Start recording"
   - Secondary action: "Use text input" (fallback)

2) **Recording state**
   - Mic icon with animated glow + timer (mm:ss).
   - Live transcript area that updates as speech is recognized.
   - Small helper text: "You can edit the transcript while you speak."
   - Action: "Stop and use this".

3) **Processing state**
   - Message: "Turning that into your profile..."

4) **Review state**
   - Show extracted fields (editable):
     - First name
     - Interest topics (tags or short phrases)
   - Inline validation: require first name + at least 1 topic.
   - CTA: "Find sources" (existing flow)
   - Secondary: "Record again"

5) **Fallback**
   - If mic permission denied, show text fields immediately with a short note.

## Data Flow
1) User taps **Start recording**.
2) Client requests ephemeral Realtime token from backend.
3) Client opens WebRTC session directly to OpenAI Realtime and streams microphone audio.
4) Realtime sends partial transcripts; UI updates continuously.
5) User taps **Stop**; session closes and final transcript is captured.
6) Client sends transcript to backend `/api/onboarding/parse-voice`.
7) Backend returns structured fields + confidence + missing-fields list.
8) Client displays extracted fields for review, then calls existing `buildProfileAndDiscover` (now using interests to form query lanes).

## Query Lanes
- Derived from `interest_topics` (and optionally `inferred_topics`).
- Represent short query strings used to power personalized feed lanes.
- Persist in the same place current onboarding personalization is stored (exact storage TBD during implementation).

## Backend API Additions
1) **Ephemeral Realtime Token**
- `POST /api/openai/realtime/token`
- Response:
  ```json
  {
    "token": "...",
    "expires_at": "2026-01-23T00:00:00Z",
    "model": "<realtime-model-name>"
  }
  ```
- Uses server OpenAI API key to mint short-lived token.

2) **Transcript Parsing**
- `POST /api/onboarding/parse-voice`
- Request:
  ```json
  {
    "transcript": "I'm Ada. I like AI policy, climate tech, and startup funding news.",
    "locale": "en-US"
  }
  ```
- Response:
  ```json
  {
    "first_name": "Ada",
    "interest_topics": ["AI policy", "climate tech", "startup funding"],
    "confidence": 0.86,
    "missing_fields": []
  }
  ```
- Notes:
  - Normalize topics by trimming whitespace and de-duplicating.
  - If required fields missing, return `missing_fields` and let client prompt a re-record.

3) **Profile Build (Existing Endpoint Update)**
- `POST /api/onboarding/profile`
- Update request schema to accept interests instead of social handles:
  ```json
  {
    "first_name": "Ada",
    "interest_topics": ["AI policy", "climate tech", "startup funding"]
  }
  ```
- Response remains:
  ```json
  {
    "profile_summary": "...",
    "inferred_topics": ["..."],
    "candidate_sources": []
  }
  ```
- Backend uses `interest_topics` to create query lanes and to seed `fast-discover`.

## iOS Client Architecture
### New Components
- `RealtimeTranscriptionService`
  - Manages microphone permissions, audio session config, WebRTC connection, and data channel events.
  - Emits `partialTranscript` and `finalTranscript` updates.

- `OnboardingSpeechState`
  - `idle | recording | processing | review | error`
  - `transcript`, `duration`, `errorMessage`

### ViewModel Updates
- Add speech state and transcript fields to `OnboardingViewModel`.
- Add actions:
  - `startSpeechCapture()`
  - `stopSpeechCapture()`
  - `parseTranscript()`
  - `resetSpeechCapture()`
- On successful parse, populate `firstName`, `interestTopics`.

### UI Updates
- Replace text fields in `profileView` with the mic CTA + live transcript UI.
- Show editable fields only in **review** state or on **Use text input** fallback.

## LLM Parsing Logic
- Use backend LLM with structured output schema:
  ```json
  {
    "first_name": "string | null",
    "interest_topics": "array[string] | null",
    "confidence": "number 0..1",
    "missing_fields": "array"
  }
  ```
- Prompt guidance: extract only explicit values from transcript, do not guess.
- If transcript is too short or ambiguous, return missing fields.

## Error Handling
- **Mic denied**: show text input fallback.
- **Realtime session failure**: show retry + fallback.
- **Empty transcript**: prompt re-record.
- **LLM parse failure**: show error and allow re-record or manual entry.

## Security & Privacy
- No OpenAI API keys in the client.
- Use short-lived ephemeral tokens only.
- Do not store transcripts server-side; log only high-level metrics.

## Performance Targets
- Start recording within 1s of tapping.
- Live transcript latency < 500ms.
- Parse turnaround < 2s after stop.

## Acceptance Criteria
- Profile step is speech-first with live transcript streaming.
- Extracted fields are shown for review and are editable.
- Existing onboarding pipeline continues after fields are populated, with query lanes derived from interests.
- If mic permission is denied or fails, text input fallback works.

## Open Questions
- Confirm exact Realtime model name and availability at implementation time.
- Decide whether transcript editing during recording is editable or read-only.
