# Feed status

Approved direction: replace Processing Stats with a compact list of article and
podcast feeds. The list shows statistics only; item previews belong in feed detail.

Each feed shows its last successful item completion, successful processing total,
running and queued item counts, refresh failure, and paused state. Keep last feed
check distinct from item completion. Remove publication predictions from this screen.

Tap a feed for its paginated processed history, including read and archived items,
plus current work and status filters. Completed items open the existing reader.
Show publication and completion dates separately, recorded podcast duration, and
estimated article reading time from the stored source body size. Missing measurements
remain absent; RSS excerpt word counts are not full-article measurements.

Use the authenticated user's memberships and the existing unambiguous feed matcher
for both totals and history. Count each item once, even with multiple pipeline tasks.
Only unexpired task leases indicate running work. Expired leases remain queued for
recovery. Scope activity to content preparation, media, summarization, and artwork.

Implementation follows the existing scraper API and Swift generated contract path.
History pages contain 30 items; returning one extra row determines whether another
page exists. Refresh retains visible data on failure and ignores stale filter
responses. Background activity updates must not discard loaded history pages.

Validation: PostgreSQL coverage for archives, ownership, stable ordering, activity,
and expired leases; Swift tests for filter races, pagination retries, and history
preservation; native build and Simulator navigation through feed list and detail.
