# Briefing Lens scroll and read-mark correctness plan

Date: 2026-07-13

Status: implemented and verified locally

## Outcome

When read segments are retired and a Lens payload is refreshed, the refreshed Lens must start at
the top instead of retaining an offset into a structurally different document. Content must only
be marked read after it was genuinely visible and then passed completely above the viewport while
that Lens is the active reading surface.

The fix stays client-side. The server already has the correct ownership: read marks update source
state, fully read segments become retired, the global Briefing version advances, and Lens
presentation returns the remaining active segments.

## Reproduced failure

The Simulator reproduction on the Podcasts Lens established this sequence:

1. Scrolling marked sources read optimistically and the debounced request retired fully read
   segments on the server.
2. The current payload stayed visible and became stale, preserving the active reading session.
3. Leaving Briefing and returning revalidated the index and refetched the stale Podcasts payload.
4. SwiftUI replaced the segment collection inside the same `ScrollView` identity, so the old
   numeric scroll offset survived even though the ordered segment set had changed.
5. Newly inserted content landed above that retained offset. Existing geometry callbacks treated
   `maxY < 0` as proof that the reader had passed the content, even though it had never intersected
   the viewport, and submitted another read-mark batch.

## Required invariants

1. A source or segment is read only after it intersected the visible scroll viewport and later
   moved completely above the viewport.
2. An initial geometry observation above the viewport never marks content read.
3. A below-to-above layout jump that never intersects the viewport never marks content read.
4. Read tracking is disabled when Briefing is inactive or the Lens is not selected.
5. Replacing a Lens with a different ordered segment-ID set resets that Lens scroll view to the
   top.
6. A response that only changes read styling while retaining the same ordered segment IDs does
   not reset scroll position.
7. Pending read marks continue to keep the current stale payload visible until normal
   revalidation replaces it.

## Hand-computed fixed flow

```text
t0  client = {active: true, lensPayload: v1 [s1, s2, s3], offset: deep}
    reader scrolls s1 and s2 fully above the viewport

t1  client = {pendingReadKeys: K, s1/s2: read-visible, lensPayload: stale v1}
    server = {s1/s2: retired, version: v2}
    the active reader is not interrupted or jumped

t2  reader selects Knowledge
    client = {active: false, readTrackingEnabled: false, offset: deep}
    teardown geometry cannot emit read marks

t3  reader returns to Briefing; index v2 and Lens [s3, s4, s5] load
    ordered segment IDs changed, so the ScrollView identity changes
    client = {active: true, lensPayload: v2 [s3, s4, s5], offset: top}

t4  s3 intersects the viewport
    exposure[s3] = visible; no read mark is emitted

t5  reader scrolls s3 completely above the viewport
    exposure[s3] = exitedAfterVisible; its source keys are marked exactly once
```

The broken invariant cannot recur: structural replacement removes the obsolete offset, and the
exposure state machine independently rejects any item that begins above the viewport.

## Implementation

### 1. Model viewport exposure explicitly

- Add a small value-type exposure tracker in `BriefingReadMarking.swift`.
- Classify geometry as below, visible, or above the scroll viewport.
- Emit one read transition only for `visible -> above` (including visibility remembered across
  intermediate observations).
- Reset exposure when read tracking becomes disabled.
- Reuse the tracker for passage, figure/pullquote, floating-figure, and segment-backstop markers.

### 2. Gate tracking by lifecycle and selection

- Store the existing Briefing activity value as observable ViewModel state.
- Enable markers only when Briefing is active and their Lens is selected.
- Keep `markSourcesSeen` and the network flush semantics unchanged.

### 3. Reset only structurally changed Lens documents

- Define a stable Lens content identity from the Lens key and ordered segment IDs.
- Apply it to the Lens `ScrollView` so retirement, insertion, reordering, or compaction creates a
  fresh scroll container at the top.
- Do not include the global version or read flags in this identity; non-structural updates should
  preserve position.

## Tests

### Swift unit tests

- Initial observation above the viewport does not mark read.
- Below-to-above without visibility does not mark read.
- Visible-to-above marks once.
- Disabling tracking resets prior exposure.
- Lens content identity is stable across version/read-only changes and changes when ordered
  segment IDs change.
- Briefing activity state follows `setActive` without changing existing refresh ownership.

### Existing regression seams

- Keep `testReadMarkFlushKeepsAffectedLensVisibleAndOmitsItFromSnapshot` passing so the active
  reader still sees optimistic greyed content until revalidation.
- Run the focused Briefing ViewModel and read-marking test suites.

### Simulator verification

1. Open a populated Lens and scroll far enough to mark at least one complete segment read.
2. Wait for the read-mark flush.
3. Switch to Knowledge, then return to Briefing.
4. Confirm the refreshed Lens is at the top.
5. Confirm no read-mark request is emitted merely because the refreshed payload loaded.
6. Scroll the first refreshed segment fully above the viewport and confirm exactly that content
   is marked read.

## Primary files

- `client/newsly/newsly/Views/Briefing/BriefingReadMarking.swift`
- `client/newsly/newsly/Views/Briefing/BriefingLensContentViews.swift`
- `client/newsly/newsly/ViewModels/BriefingViewModel.swift`
- `client/newsly/newslyTests/BriefingReadMarkingTests.swift`
- `client/newsly/newslyTests/BriefingViewModelTests.swift`

## Verification result

- The focused Briefing read-marking and ViewModel suites passed: 37 tests, 0 failures.
- The complete native iOS suite passed: 293 tests, 0 failures.
- The app built and launched successfully on an iPhone 17 Pro Simulator running iOS 26.4.
- In the reproduced Podcasts flow, returning after a read-mark refresh loaded the remaining
  segment set at the top and did not send a read-mark request.
- Scrolling the first refreshed segment fully above the viewport then sent the expected read-mark
  request and reduced the unread count from 12 to 6.
