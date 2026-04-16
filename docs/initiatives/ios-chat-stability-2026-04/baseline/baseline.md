# iOS Chat Stability Baseline Trace Notes

**Updated:** 2026-04-16
**Trace status:** Pending manual capture

## Available Signposts

All signposts use subsystem `com.newsly.chat` and category `perf`.

| Signpost | Type | Source |
| --- | --- | --- |
| `load-session` | Interval | `ChatSessionViewModel.loadSession()` |
| `send-message` | Interval | `ChatSessionViewModel.sendMessage(text:)` |
| `poll-cycle` | Interval | `ChatSessionViewModel.pollUntilComplete(messageId:)` |
| `reconcile-detail` | Interval | `ChatSessionViewModel.applyDetail(_:)` |
| `start-council` | Interval | `ChatSessionViewModel.startCouncil(message:)` |
| `select-council-branch` | Interval | `ChatSessionViewModel.selectCouncilBranch(childSessionId:)` |
| `apply-voice-transcript` | Interval | `ChatSessionViewModel.applyVoiceTranscript(_:)` |
| `audio-session-activate` | Event | `AudioRecordingSessionLease.activate()` |
| `audio-session-deactivate` | Event | `AudioRecordingSessionLease.deactivate()` |

## Capture Matrix

Capture these in Instruments with the SwiftUI, Time Profiler, and Points of Interest instruments enabled:

1. Cold open of an existing 50-message chat session.
2. Send a message and poll through a short assistant reply.
3. Start a three-voice council response and switch branches once it completes.
4. Record roughly 10 seconds of dictation, stop, and confirm transcription fills the composer.

## Metrics To Record

| Metric | Baseline | Notes |
| --- | --- | --- |
| View body count during one send + poll | Pending | Use the SwiftUI instrument for `ChatSessionView` and `ChatMessageList`. |
| `reconcile-detail` duration on a long transcript | Pending | Capture min / p50 / max from Points of Interest. |
| Time from `start-council` to first candidate render | Pending | Use `start-council` plus visible candidate render timing. |
| `audio-session-activate` / `audio-session-deactivate` parity | Pending | Counts should match after every recording path. |

## Notes

- Trace files are intentionally not committed until captured from a representative simulator or device run.
- Physical-device capture is still required for audio interruption, route-change, and background/foreground verification.
