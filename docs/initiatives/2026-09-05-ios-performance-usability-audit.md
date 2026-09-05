# iOS performance and usability audit — September 5, 2026

The main navigation paths work, but Knowledge removal is not ready for a smoothness sign-off. Three issues were reproduced: removing a saved item leaves an actionable stale row, delayed deck deletion restores the normal-looking row while waiting, and Extra Large app text truncates the selected Knowledge tab label. Existing automated tests all pass despite these defects.

This is a current-checkout code and Simulator audit. It does not establish physical-device frame rate, hitch frequency, battery cost, or production-network performance.

## Tested environment

- Branch `main`, base commit `add3ffbe868abf50734d3edf1452b3730011fb3e`, including the pre-existing uncommitted workspace changes. This was not a clean release artifact.
- Fresh XcodeBuildMCP build, scheme `newsly`, project `client/newsly/newsly.xcodeproj`, derived data `/tmp/newsly-usability-audit`.
- iPhone 17 Pro, iOS 26.3, Simulator `1AB428FC-4416-4FC7-B5FB-2BA9EDCEA94A`.
- Native temporary PostgreSQL on port 55439, freshly migrated from this checkout; freshly built Rust API on port 8049. No production data or production mutations.
- Deterministic `newsly-admin e2e seed` fixtures `usability-audit` and `audit-accessibility`. Populated Knowledge included a saved article, narration, deck, and chat. Briefing had one populated lens.
- A local proxy on port 8050 delayed DELETE requests by two seconds. It logged methods, paths, status codes, and elapsed time, excluding authorization headers and bodies.
- Dark mode at Standard app text; light mode at Standard and Extra Large app text. Extra Large and light appearance were restored after inspection.

Evidence lives in [test-results/ios-usability-audit-2026-09-05](../../test-results/ios-usability-audit-2026-09-05/). Generated evidence is local and ignored by Git.

## Reproduced findings

### 1. P1 — Removing a saved item leaves it visible; repeating the action saves it again

Steps: open Knowledge, long-press the saved article, choose “Remove from Knowledge,” then return from a chat or search. The row remains and still says “SAVED.” The API's Knowledge list is empty, and searching for the same title returns no results. The detail screen correctly offers “Save to Knowledge,” while the timeline still presents the stale saved row.

A second fixture confirmed the consequence: choosing “Remove from Knowledge” twice produced `DELETE /api/content/8/knowledge` (200, 2013 ms through the delay proxy), followed by `POST /api/content/8/knowledge` (200, 2 ms). The same removal label reverses the first action.

Cause: `ContentListViewModel.toggleKnowledgeSave` updates `isSavedToKnowledge` in the existing array but does not remove the item from the Knowledge list. `KnowledgeTimelineItem.merged` includes every supplied saved-content item without filtering its flag. `KnowledgeSavedContentButton` always offers a removal action, but its callback invokes a toggle. The row's “SAVED” kicker does not reflect the changed flag.

Code: [mutation](../../client/newsly/newsly/ViewModels/ContentListViewModel.swift#L160), [timeline merge](../../client/newsly/newsly/Models/KnowledgeTimelineItem.swift#L36), [row action](../../client/newsly/newsly/Views/KnowledgeView.swift#L178), [timeline callback](../../client/newsly/newsly/Views/KnowledgeTimelineView.swift#L344).

Fix direction: make removal an explicit idempotent command, remove the item from the visible Knowledge projection immediately, and restore its previous position with an actionable error if the command fails. Keep Recently Read behavior separate. Cover repeated removal and failure rollback through the aggregate timeline, not just the saved flag.

Evidence: [stale row screenshot](../../test-results/ios-usability-audit-2026-09-05/saved-removal-still-visible.jpg), `latency-proxy.log`.

### 2. P2 — Delayed deletion leaves a normal-looking row while the command is pending

Steps: swipe the deck row left, tap Delete, and hold the DELETE response for two seconds. The swipe affordance closes and the normal deck row is visible again with no pending feedback. The row disappears after the response. The captured request took 2019 ms and returned 204. A separate normal-latency chat deletion removed the chat successfully with no permanent gap.

Cause: `LearningDecksViewModel.delete` and `KnowledgeChatViewModel.deleteSession` wait for the server before removing their entries. The row callbacks start unstructured tasks, and the aggregate timeline republishes through another MainActor task. There is no explicit animation transaction around that publication. The list also represents dividers as separate rows, making the disappearance involve more than the content row alone. A persistent orphan divider was not reproduced.

Code: [deck deletion](../../client/newsly/newsly/ViewModels/LearningDecksViewModel.swift#L240), [chat deletion](../../client/newsly/newsly/ViewModels/KnowledgeChatViewModel.swift#L159), [swipe actions](../../client/newsly/newsly/Views/KnowledgeTimelineView.swift#L365), [source observation](../../client/newsly/newsly/ViewModels/KnowledgeTimelineViewModel.swift#L295), [list structure](../../client/newsly/newsly/Views/KnowledgeTimelineView.swift#L282).

Fix direction: use one immediate removal transaction with stable identity and failure rollback, or keep the row explicitly pending until the server replies. Ensure the aggregate projection participates in the intended transaction. Test first, middle, last, and last-in-day removal with delayed success and failure, including a refresh in flight.

Evidence: [delayed-delete recording](../../test-results/ios-usability-audit-2026-09-05/delayed-delete.mp4), `latency-proxy.log`. Simulator video is variable-rate and is evidence of visible states, not a frame-rate benchmark. The initial XcodeBuildMCP recording failed to save; the retained recording used `simctl`.

### 3. P2 — The Knowledge tab label clips at a supported text size

Steps: Settings → Display → App Text Size → Extra Large, close Settings, select Knowledge. On the iPhone 17 Pro, the selected label becomes “Knowled…”.

Cause: `CompactTabBar` has a fixed maximum width of 200 points while its selected label scales with app text size and is restricted to one line.

Code: [tab width](../../client/newsly/newsly/Shared/AppChrome.swift#L134), [label and padding](../../client/newsly/newsly/Shared/AppChrome.swift#L181).

Fix direction: let the bar accommodate its scaled label within the available safe width. Verify both tabs at every supported app text size on the smallest supported phone width. Keep the 44-point hit targets.

Evidence: [Extra Large light-mode screenshot](../../test-results/ios-usability-audit-2026-09-05/knowledge-extra-large-light.jpg).

## Code findings requiring targeted follow-up

These are not presented as reproduced hitches or measured regressions.

| Area | Evidence and consequence | Next verification |
| --- | --- | --- |
| Deck opening | `KnowledgeView.openDeck` awaits a viewer URL before presenting anything. The row has no opening indicator. `withDeckBusy` records activity but does not guard or coalesce concurrent calls, and the row remains enabled. Rapid taps can launch overlapping requests and publish destinations after the original tap context changes. | Delay viewer-URL GETs; double-tap; navigate away before completion; test reversed response order. Present an owned loading destination or coalesce/fence the opening task. |
| Knowledge projection cost | Every observed child-list change rebuilds the complete merged array, reparses all chat previews from Markdown, sorts all entries, and groups all dates on MainActor. Cached Briefing rendering is more incremental. | Profile 50, 500, and 2,000 mixed items during polling, pagination, and deletion; cache preview/presentation work if the measured cost exceeds the interaction budget. |
| Reduce Motion | Briefing category selection and masthead/category changes use unconditional animations at `BriefingView.swift:345,376–378`. The compact tab bar and scroll-to-top helper explicitly respect Reduce Motion. | Exercise category transitions with Reduce Motion enabled and apply the shared motion policy consistently. No runtime Reduce Motion sweep was completed. |
| System accessibility sizes | App chrome is explicitly set from four app sizes ending at `.xxLarge`. This follows the existing app-size policy but does not preserve the system's accessibility-size category. | Decide whether accessibility categories should override the in-app choice, then verify the intended contract with VoiceOver and larger accessibility text. |

## Coverage and results

| Surface or check | Result |
| --- | --- |
| Fresh iOS build | Passed, 19.1 seconds; no reported warnings or errors. |
| Native unit suite | 633 passed, zero failed/skipped; 28.3 seconds for the tool's test run. Results in `unit-tests.xcresult`. |
| Authenticated lifecycle UI suite | Three passed: launch, warm resume without relaunch, process-reclaimed relaunch. Results in `lifecycle-tests.xcresult`. |
| Knowledge initial data load | One logged activation read ran from 07:53:42.409 to 07:53:42.518: 109 ms for the small local fixture. This excludes launch, rendering, and production latency. |
| Warm tab switching | Eight alternating switches reached the expected screen. The captured logs show Briefing skipping a fresh index reload and Knowledge skipping an already-handled activation. No persistent blank state was observed. Automation round-trip time was deliberately not treated as app latency. |
| Chat push/back | Opened populated chat, returned to Knowledge successfully. |
| Saved article push/back | Opened detail; edge-swipe returned to Knowledge successfully. |
| Knowledge search and keyboard | Search focused the field and returned the expected no-results state after removal; back navigation returned to the timeline. |
| Settings and nested navigation | Presented Settings, opened Recently Read, returned and dismissed successfully. |
| Deck reader | Opened the fixture HTML, expanded the secondary chat panel, closed and returned successfully. The simple fixture's content appeared close to the overlay controls; this is not evidence about current generated production decks. |
| Deletion | Saved removal defect reproduced twice; normal chat deletion succeeded; delayed deck deletion feedback defect reproduced. |
| Light/dark and app text | Populated screens inspected in both appearances; supported Extra Large setting exposed tab-label truncation. Single-line timeline title truncation is intentional under law K5. |

The native tests establish functional coverage at their existing seams. They do not cover the visible deletion transition, repeated removal semantics through the timeline, or the largest-size tab layout.

## Required before a full performance sign-off

1. Fix the three reproduced issues and rerun those exact interactions, including delayed and failed mutations and day-group boundaries.
2. Profile a Release build on a physical phone using Instruments for animation hitches, main-thread work, memory, and launch. Test a representative large library and long Briefing/chat content. Simulator captures cannot establish sustained 60/120 Hz behavior or thermal performance.
3. Complete rapid/cancelled navigation, full-swipe deletion, repeated tab reselection while scrolled, pagination under latency, offline recovery, and overlapping refresh/mutation tests with large fixtures.
4. Finish VoiceOver, Reduce Motion, smallest-phone, landscape/iPad, and keyboard-interruption checks. Exercise production-shaped generated deck HTML and audio interruption/resume; the fixture reader is not a generation-quality test.
5. Audit onboarding, Share Extension, source management, and provider-backed creation separately under their real permission and lifecycle conditions. This pass did not send paid generation requests or exercise microphone capture.

No app implementation was changed, committed, pushed, or deployed in this audit. Unrelated workspace edits were preserved. The temporary API/proxy and PostgreSQL instance were stopped after evidence capture.
