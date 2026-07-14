# Briefing timeline cadence and segment read threshold plan

Date: 2026-07-13

Status: implemented and verified locally

## Outcome

Briefing timeline separators should divide a newest-first Lens into useful time bands rather than
appearing before every segment. Read state should advance once per segment, only after the reader
has intentionally moved the viewport's top edge past that segment's midpoint.

This remains a client-only change. The existing optimistic read state, debounced batching, retry,
server persistence, stale-Lens invalidation, and structural scroll reset remain unchanged.

## Timeline separator policy

- The first segment is the implicit anchor and has no separator.
- Walk later segments in their existing newest-to-oldest order.
- A segment receives a separator when it is at least four hours older than the current anchor.
- A qualifying segment becomes the next anchor.
- Exactly four hours qualifies.
- Crossing a calendar-day boundary does not override the four-hour minimum.
- Empty and single-segment inputs produce no separators.

The policy returns segment indices so the existing stable segment identity remains unchanged.

## Read-marking policy

- `segment.sourceKeys` is the single canonical read batch.
- Passage, figure, pullquote, and floating-figure blocks do not own geometry-based read triggers.
- A segment crosses the threshold only when `segmentFrame.midY < 0`, meaning the viewport's top
  edge has passed its midpoint. Exactly at the midpoint is not yet crossed.
- A read mark requires an observed before-threshold state followed by an after-threshold state
  while tracking is enabled.
- Disabling tracking clears that observed transition state, so returning to an already-scrolled
  Lens cannot mark content merely because its initial geometry is above the threshold.
- A segment marks at most once during the lifetime of its rendered identity.

## Hand-computed behavior

```text
timeline dates = [12:00, 11:00, 10:00, 09:00, 08:00, 07:00, 04:00]
anchor = 12:00
11:00, 10:00, 09:00 -> less than four hours; no separator
08:00 -> exactly four hours; separator at index 4; anchor = 08:00
07:00 -> one hour from anchor; no separator
04:00 -> exactly four hours from anchor; separator at index 6; anchor = 04:00

t0  segment = {midY: +180, enabled: true, observedBefore: false, didMark: false}
    predicate = false -> observedBefore = true; no read mark

t1  reader scrolls; segment = {midY: 0, enabled: true, observedBefore: true}
    predicate = false; no read mark

t2  reader scrolls farther; segment = {midY: -1, enabled: true, observedBefore: true}
    predicate = true -> mark all segment.sourceKeys; didMark = true

t3  later geometry remains negative
    didMark = true -> no duplicate mark

reactivation guard:
t0  Briefing becomes inactive -> enabled = false; observedBefore = false
t1  Briefing returns with an old segment already at midY -100
    initial predicate = true, but observedBefore = false -> no read mark
t2  a structurally refreshed Lens starts at the top with midY +180
    predicate = false -> observedBefore = true
t3  intentional scrolling moves it to midY -1 -> one read mark
```

## Implementation

1. Add a pure greedy four-hour anchor policy next to `BriefingTimelineStamp`.
2. Compute separator indices once for the Lens payload and use them in the segment loop.
3. Replace the full-viewport exposure helper with a pure segment-midpoint predicate and a small
   transition tracker.
4. Attach one read marker to each complete segment and call `markSegmentSeen` at the crossing.
5. Remove block-level marker modifiers, wrapper views, tracking inputs, and source callback
   plumbing while preserving read-opacity calculations.
6. Keep `BriefingLensContentIdentity` and ViewModel activity tracking from the Lens refresh fix.

## Tests

- Timeline: under four hours, exactly four hours, cumulative hourly segments, repeated anchors,
  crossing midnight, empty input, and a single segment.
- Read threshold: just before, exactly at, and just after the midpoint.
- Read transition: initial crossed geometry does not mark, before-to-after marks once, and
  disabling tracking clears the prior before-threshold observation.
- Existing Briefing ViewModel read batching and stale-Lens tests remain green.

## Verification

1. Run `BriefingTimelineStampTests`, `BriefingReadMarkingTests`, and `BriefingViewModelTests`.
2. Build and launch the app on the configured iOS Simulator.
3. Open a Lens containing multiple segments and inspect separator cadence.
4. Scroll the first segment to just before its midpoint and confirm counts remain unchanged.
5. Cross the midpoint and confirm the segment's source batch marks together.
6. Leave and return to Briefing and confirm no read mark occurs from initial geometry alone.

## Primary files

- `client/newsly/newsly/Views/Briefing/BriefingLensContentViews.swift`
- `client/newsly/newsly/Views/Briefing/BriefingReadMarking.swift`
- `client/newsly/newslyTests/BriefingTimelineStampTests.swift`
- `client/newsly/newslyTests/BriefingReadMarkingTests.swift`

## Verification result

- The focused timeline, read-marking, and Briefing ViewModel suites passed: 46 tests, 0 failures.
- The complete native iOS suite passed: 300 tests, 0 failures.
- The app built and launched successfully on an iPhone 17 Pro Simulator running iOS 26.4.
- A small Podcasts scroll before the segment midpoint left the unread count at 6 and emitted no
  read-mark request.
- Crossing the segment midpoint changed all 6 canonical segment sources to read together and
  emitted one read-mark request.
- Leaving Briefing and returning emitted no additional read-mark request from initial geometry.
- A populated Science & Nature Lens rendered a separator at the computed four-hour anchor rather
  than applying the previous unconditional separator rule.
