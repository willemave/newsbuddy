# iOS Modernization Performance Verification

Status: deterministic hot-path regressions are covered; a valid physical-device Instruments
comparison is still not captured.

## Manual verification

Capture before-and-after traces manually in Instruments on a physical iOS device. The removed
simulator-driven Maestro trace harness did not produce exportable traces and added no reliable
release signal.

## Current interactions

- Briefing lens read: open a populated lens, scroll through passages, switch category, and page to
  an adjacent lens.
- Detail push + drag: open detail from a Briefing source, scroll vertically, then perform a
  horizontal detail drag.
- Knowledge image list: scroll a mixed-size image list through cache misses, repeated URLs, and
  pagination.

Capture each interaction with the `SwiftUI` and `Time Profiler` Instruments templates.

## Current Local Limitation

The old Fast Read/Long Read interactions no longer exist after Classic-shell retirement. The local
simulator does not support the `SwiftUI` Instruments template reliably, and CLI `Time Profiler`
captures did not finalize exportable trace bundles. Use a physical device and save comparison
`.trace` bundles for the current interactions above.

Do not mark the modernization performance gate complete until valid before/after traces exist for both interactions and the trace review confirms:

- stable view-body invocation counts while Briefing scroll and lens selection state changes,
- no JSON decoding in the detail drag hot path,
- one raw image transfer per URL across concurrent size variants,
- no timer wakes while backgrounded.
