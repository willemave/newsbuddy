---
name: ios-debugging
description: Debug iOS apps in Simulator with XcodeBuildMCP, durable simulator log capture, screenshots, UI inspection, and small verified fixes. Use when reproducing UI bugs, navigation issues, crashes, hangs, tap/navigation lag, or when simulator logs need to be captured reliably.
---

# iOS Debugging

## Purpose
Use this skill for the full reproduce-debug-fix-verify loop in iOS Simulator.

Prefer:
- `XcodeBuildMCP` for project discovery, build/run, screenshots, UI snapshots, gestures, and structured app log capture
- `xcrun simctl spawn ... log ...` for durable simulator-local log streaming and retrospective log reads

Use this skill when the task involves:
- a bug that must be reproduced by driving the app in Simulator
- simulator logs that need to be filtered and saved reliably
- screenshots, UI tree state, or debugger evidence before editing code

## Core Loop
1. Check current XcodeBuildMCP session defaults first.
2. If needed, discover the Xcode project/workspace, scheme, and simulator and keep them stable for the session.
3. Build and launch the app in Simulator.
4. Confirm the visible screen with `snapshot_ui` or `screenshot` before interacting.
5. Reproduce the bug yourself. Prefer accessibility labels and IDs over coordinates.
6. Capture evidence as you go:
   - screenshots for UI state
   - simulator logs around the failure
   - LLDB state if the issue looks like a crash or hang
7. Make the smallest fix that explains the observed failure.
8. Rebuild, rerun the same flow, and prove the fix with the same evidence surface.

## Mode and Scope
- For interaction-lag reports, start with the exact SwiftUI tap/navigation path and Simulator evidence before assuming backend latency.
- For product-shape changes, preserve the requested interaction contract. Examples: do not auto-start a chat unless the action is explicitly prompt-seeded; keep selection-based actions attached to the selection affordance.
- If the user asks only to investigate, stop after evidence, root cause, and a proposed patch.
- If the user asks to fix, implement the narrowest change that matches the reproduced failure and rerun the same flow.
- Commit, push, and deploy are explicit follow-on requests, not implied by a successful simulator fix.

## Preferred Tooling

### XcodeBuildMCP
Use these tools first when available:
- `session_show_defaults`, `discover_projs`, `list_schemes`, `list_sims`, `session_set_defaults`
- `build_run_sim`, `launch_app_sim`, `stop_app_sim`
- `snapshot_ui`, `screenshot`, `tap`, `swipe`, `type_text`, `gesture`
- `start_sim_log_cap`, `stop_sim_log_cap`

Ask the tool for a fresh UI snapshot after navigation or layout changes. Do not keep tapping stale coordinates if the screen changed.

### Durable Simulator Logs
Host `log stream` is easy to misuse and may miss simulator-local app logs. Prefer reading logs from inside the booted simulator:

```bash
xcrun simctl spawn booted log stream \
  --style compact \
  --level debug \
  --predicate 'subsystem == "org.willemaw.newsly"'
```

Retrospective read:

```bash
xcrun simctl spawn booted log show \
  --style compact \
  --last 5m \
  --predicate 'subsystem == "org.willemaw.newsly"' \
  --info --debug
```

If you need a file-backed trace during a manual or long-running flow:

```bash
.agents/skills/ios-debugging/scripts/sim-log-stream.sh \
  'subsystem == "org.willemaw.newsly" AND category == "RootTabFlow"' \
  /tmp/root-tab-flow.log
```

Then read it back with:

```bash
tail -n 100 /tmp/root-tab-flow.log
```

## Logging Guidance
- Prefer `Logger` from `OSLog`, not `print`
- Use one clear subsystem/category pair per feature or flow
- Keep permanent `info` logs stable and high-signal
- Use `debug` for noisy step detail only
- Do not log secrets, auth tokens, user content, or raw document bodies
- If an identifier must be logged, use the safest privacy annotation that still makes the trace useful

## UI Driving Rules
- Prefer accessibility labels and identifiers over raw coordinates
- If you must use coordinates, call out that the UI lacks a stable selector
- Re-read the UI tree before the next action when the layout changes
- Verify outcomes with `snapshot_ui` or `screenshot`; simulator input tools confirm dispatch, not app-level success
- When visible behavior is the requirement, capture before/after screenshots or UI snapshots rather than relying only on build success.
- If a bug may cross backend and iOS state, trace both sides before patching, especially when IDs, cached detail state, or background refreshes are involved.

## Evidence Hierarchy
Start with:
1. Screenshot or UI tree showing the state before and after the bug
2. Filtered simulator logs for the relevant subsystem/category

Escalate to debugger evidence when needed:
1. Attach LLDB
2. Inspect backtraces, frames, and locals
3. Re-run with narrower reproduction steps

## Recommended Prompts

### Reproduce and fix a bug
Use the Build iOS Apps plugin and XcodeBuildMCP to reproduce this bug directly in Simulator, diagnose the root cause, and implement a small fix. First check whether a project, scheme, and simulator are already selected. If not, discover them and reuse that setup for the rest of the session. Confirm the visible screen with a UI snapshot before interacting, prefer accessibility labels over coordinates, capture screenshots and simulator logs around the failure, and rerun the exact flow after the fix to verify it.

### Investigate logs without changing code
Use XcodeBuildMCP to launch the app in Simulator and reproduce this flow. Capture a focused simulator log trace for subsystem `[subsystem]` and category `[category]`. Prefer simulator-local logs via `simctl spawn ... log ...` or `start_sim_log_cap` over host `log stream`. Save the trace to a local file if the flow is long or easier to reproduce by hand, then summarize the event timeline.

## Bundled Scripts
- `scripts/sim-log-stream.sh`
  - Streams simulator-local logs with a predicate and saves them to a file
- `scripts/sim-log-show.sh`
  - Reads recent simulator-local logs with a predicate and optional time window

## Deliverables
When using this skill, finish with:
- the simulator and scheme used
- the exact reproduction steps executed
- the key screenshots, logs, or debugger evidence
- the code fix and why it works, if code changed
- the verification path used after the fix
- any validation blocker, such as an unrelated compile error or simulator/runtime mismatch
