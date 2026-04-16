# iOS Chat Stability Verification

**Updated:** 2026-04-16
**Scope:** `10-design.md` manual flow matrix for the core chat stability implementation.

## Automated Validation

- Focused simulator slice: `newslyTests/ChatSessionViewModelTests`, `newslyTests/ChatTimelineReconcilerTests`, `newslyTests/QuickMicViewModelTests`, `newslyTests/APIClientAuthTests` — passed `26/26`.
- Focused simulator slice after preview expansion — passed `26/26`.
- Focused simulator slice after signpost instrumentation — passed `26/26`.
- Focused simulator slice after per-voice council retry — passed `27/27`.
- Backend council API slice: `pytest tests/routers/test_api_chat.py -k 'council' -v` — passed `8/8`.
- Backend chat DTO slice: `pytest tests/routers/api/test_chat_models.py -v` — passed `7/7`.
- Backend chat router/model suite: `pytest tests/routers/test_api_chat.py tests/routers/api/test_chat_models.py -v` — passed `37/37`.
- Backend chat router/model suite after `display_key`: `pytest tests/routers/api/test_chat_models.py tests/routers/test_api_chat.py -v` — passed `39/39`.
- Backend lint: `ruff check app/services/council_chat.py app/routers/api/chat.py app/models/api/chat.py tests/routers/test_api_chat.py tests/routers/api/test_chat_models.py` — passed.
- Focused simulator timeline identity slice after `display_key`: `newslyTests/ChatTimelineReconcilerTests` — passed `7/7`.
- Focused simulator feed-option ownership slice: `newslyTests/ChatMessageDisplayTests`, `newslyTests/QuickMicViewModelTests`, `newslyTests/ChatTimelineReconcilerTests` — passed `17/17`.
- Focused simulator strict Phase 6 extraction/timestamp slice after toolbar/share/selectable/preview extraction and cached chat timestamp formatting: `newslyTests/ChatMessageDisplayTests`, `newslyTests/QuickMicViewModelTests`, `newslyTests/ChatTimelineReconcilerTests` — passed `17/17`.
- Focused simulator route-identity slice after `ContentView` switched chat destinations to `route.stableKey` and `ChatSessionView` dropped its dead nested destination state: `newslyTests/ChatMessageDisplayTests`, `newslyTests/QuickMicViewModelTests`, `newslyTests/ChatTimelineReconcilerTests` — passed `17/17`.
- Focused simulator lifecycle slice after moving view-triggered chat actions to VM-owned task handles and cancellation-safe disappear cleanup: `newslyTests/ChatSessionViewModelTests`, `newslyTests/ChatMessageDisplayTests`, `newslyTests/QuickMicViewModelTests`, `newslyTests/ChatTimelineReconcilerTests` — passed `27/27`.
- Focused simulator row-extraction slice after moving row dispatch and failed-send retry rendering into `MessageRow.swift`: `newslyTests/ChatSessionViewModelTests`, `newslyTests/ChatMessageDisplayTests`, `newslyTests/QuickMicViewModelTests`, `newslyTests/ChatTimelineReconcilerTests` — passed `27/27`.
- Focused simulator polling-ownership slice after wiring `handleDisappear()` to hand content-backed in-flight sessions to `ActiveChatSessionManager`: `newslyTests/ChatSessionViewModelTests`, `newslyTests/ChatMessageDisplayTests`, `newslyTests/QuickMicViewModelTests`, `newslyTests/ChatTimelineReconcilerTests` — passed `28/28`.
- Focused simulator background-tracker idempotence slice after making `ActiveChatSessionManager.startTracking` ignore duplicate session/message pairs and cancel replaced pollers before restarting: `newslyTests/ChatSessionViewModelTests`, `newslyTests/ChatMessageDisplayTests`, `newslyTests/QuickMicViewModelTests`, `newslyTests/ChatTimelineReconcilerTests` — passed `28/28`.
- iOS E2E long-transcript scroll matrix: `tests/ios_e2e/flows/chat_session_long_transcript_scroll.yaml` plus `test_chat_session_long_transcript_scroll_preserves_jump_to_latest` added; `pytest tests/ios_e2e/test_maestro_chat_session_flows.py -k long_transcript -v` collected and skipped locally because no Java runtime is installed for Maestro.
- Full simulator suite after preview expansion: `newslyTests` — passed `99/99`.
- Full simulator suite after per-voice council retry: `newslyTests` — passed `100/100`.
- `git diff --check` — passed.

## Manual Flow Matrix

| # | Flow | Status | Evidence / Notes |
| --- | --- | --- | --- |
| A | Fresh chat from Share Extension with pending first message | Not run manually | Covered structurally by route pending-message seeding and timeline reconciliation. Requires simulator/app handoff walkthrough. |
| B | Fresh chat from "Dig Deeper" on an article; VM auto-sends topic | Not run manually | `ChatSessionViewModel.loadSession()` still auto-sends topic when detail is empty. Requires simulator walkthrough. |
| C | Re-enter an existing chat mid-polling | Not run manually | Processing message polling remains view-owned; `ActiveChatSessionManager.stopTracking` runs on chat open. Requires simulator walkthrough. |
| D | Rapid send: three messages in three seconds | Automated partial | `ChatTimelineReconcilerTests.testReconcileKeepsRapidPendingSendsInOrderAndLocalIdentity` covers identity/order. Manual UI pass still needed. |
| E | Start council (3 voices) | Not run manually | Council start flow compiles; candidate card status rendering exists. Requires backend-backed simulator walkthrough. |
| E' | Double-tap two different council branches while first is selecting | Automated partial | `ChatSessionViewModelTests.testCancelCouncilSelectionClearsInFlightState` covers cancellation cleanup. Manual double-tap UI pass still needed. |
| F | Dictate -> silence auto-stop -> edit transcript -> send | Automated partial | `ChatSessionViewModelTests.testSilenceAutoStopPopulatesDraftWithoutManualStop` covers VM state. Device audio release check still needed. |
| G | Dictate -> receive a phone call | Not run manually | Interruption observer cancels recording and surfaces error. Requires physical device or simulator interruption workflow. |
| H | Dictate -> background the app -> foreground | Not run manually | Audio route cleanup exists; foreground/background behavior needs manual validation. |
| I | Navigate away from a chat during polling | Not run manually | Current policy is view-owned polling cancellation plus active-session manager for background sessions. Requires simulator walkthrough. |
| J | Logout while a message is polling | Automated partial | `APIClientAuthTests.testRequestThrowsUnauthorizedWhenRefreshUnavailable` covers terminal refresh logout notification; `ActiveChatSessionManager.reset()` observes it. Manual in-flight chat logout still needed. |
| K | Airplane mode during send | Automated partial | Failed local rows preserve retry text and render retry UI. Requires simulator network-off walkthrough. |
| L | Keyboard up, assistant response arrives | Not run manually | Chat relies on SwiftUI keyboard avoidance and bottom tracking. Requires simulator walkthrough. |
| M | Long transcript (50+ messages), scroll up, new message arrives | Automated E2E added; local execution blocked | Maestro flow seeds a long transcript, scrolls to an older message, sends while scrolled up, verifies "Jump to latest", then asserts the follow-up reply and backend persistence. Local run collected and skipped because no Java runtime is installed for Maestro. |
| N | Share a message via context menu | Not run manually | Share sheet path unchanged. Requires simulator walkthrough. |
| O | Switch provider mid-session | Not run manually | Provider update uses injected `ChatDependencies.chatService`. Requires simulator walkthrough. |
| P | Deep-link into a chat from outside the app | Not run manually | Route-based `ChatSessionView` and `ChatNavigationCoordinator` paths compile. Requires notification/deep-link walkthrough. |

## Blocked Items

- Baseline trace capture is not complete. Phase 0 signposts are implemented and the capture checklist lives in `baseline/baseline.md`, but `.trace` files require a manual Instruments run.

## Manual Follow-Up

Run the matrix above on an iOS Simulator for UI flows and on a physical device for audio-session release, interruption, route-change, and background/foreground behavior.
