# iOS Modernization Performance Verification

Status: valid Phase 0/Phase 4 Instruments comparison is still not captured.

## Manual verification

Capture before-and-after traces manually in Instruments on a physical iOS device. The removed
simulator-driven Maestro trace harness did not produce exportable traces and added no reliable
release signal.

## Measured Interactions

- Fast Read scroll: seeds 200 deterministic visible news rows and scrolls the Fast Read feed through pagination.
- Detail open + drag: seeds deterministic long-form rows, opens detail from the Long Read feed, scrolls vertically, then performs a horizontal detail drag.

Capture each interaction with the `SwiftUI` and `Time Profiler` Instruments templates.

## Current Local Limitation

The local simulator does not support the `SwiftUI` Instruments template reliably, and CLI
`Time Profiler` captures did not finalize exportable trace bundles. Use a physical device and save
the before/after `.trace` bundles.

Do not mark the modernization performance gate complete until valid before/after traces exist for both interactions and the trace review confirms:

- fewer view-body invocations per Fast Read scroll frame,
- no JSON decoding in the detail drag hot path,
- no timer wakes while backgrounded.
