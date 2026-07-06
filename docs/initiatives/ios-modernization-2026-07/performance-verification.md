# iOS Modernization Performance Verification

Status: trace harness added; valid Phase 0/Phase 4 Instruments comparison is still not captured.

## Harness

Use the opt-in performance wrapper:

```bash
DATABASE_URL='postgresql+psycopg://newsly:newsly@127.0.0.1:5432/newsly' \
JWT_SECRET_KEY=test-secret \
ADMIN_PASSWORD=test-admin \
NEWSLY_MAESTRO_SIMULATOR_NAME='iPhone 17 Pro' \
tests/scripts/ios_performance_traces.sh
```

The wrapper builds and installs the iOS app through the existing Maestro harness, seeds deterministic fixture data in pytest, runs the performance Maestro flows, and writes attempted trace output under `tmp/ios-performance-traces/<timestamp>/`.
Captured trace bundles are validated with `xcrun xctrace export --toc`; a directory on disk does not count as evidence unless Instruments can export a non-empty table of contents for the measured `newsly` process.

Useful options:

```bash
NEWSLY_PERF_TRACE_SECONDS=60
NEWSLY_PERF_XCTRACE_SAVE_TIMEOUT_SECONDS=180
NEWSLY_PERF_STRICT_XCTRACE=1
NEWSLY_PERF_TRACE_OUTPUT_DIR=/absolute/output/dir
```

## Measured Interactions

- Fast Read scroll: seeds 200 deterministic visible news rows and scrolls the Fast Read feed through pagination.
- Detail open + drag: seeds deterministic long-form rows, opens detail from the Long Read feed, scrolls vertically, then performs a horizontal detail drag.

Each interaction is parameterized for the `SwiftUI` and `Time Profiler` Instruments templates.

## Current Local Limitation

On the current local Xcode/simulator runtime, the `SwiftUI` Instruments template reports that it is unsupported on Simulator. The `Time Profiler` template attaches to the simulator app and the Maestro interaction runs, but `xctrace` does not finalize a valid trace bundle from non-interactive CLI execution. Letting `xctrace` finish via `--time-limit` still times out, and an isolated `--all-processes --time-limit 5s` check also times out before leaving a bundle that `export --toc` can read.

The remaining verification gate therefore requires either:

- running the same harness with a physical iOS device target that supports the SwiftUI template, or
- capturing the same two interactions manually in Instruments and saving the before/after `.trace` bundles.

Do not mark the modernization performance gate complete until valid before/after traces exist for both interactions and the trace review confirms:

- fewer view-body invocations per Fast Read scroll frame,
- no JSON decoding in the detail drag hot path,
- no timer wakes while backgrounded.
