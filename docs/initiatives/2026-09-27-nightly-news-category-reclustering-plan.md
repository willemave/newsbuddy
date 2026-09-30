# Nightly news-category reclustering

Status: implemented and validated locally; production release gate and rollout pending.
Date: 2026-09-27. Base: detached `776317d2`. Production is unchanged until release verification.

## Outcome

News categories should reflect the user's recent eligible news, while remaining
recognizable and preserving every unread source. Keep immediate assignment for
new arrivals. Periodically reconsider the category partition using cached story
embeddings, in a background job during the user's local night. Use an LLM only
for a bounded number of new or materially changed category names.

The experiment report is [the replay report](2026-09-27-news-category-replay-results.md).
Raw public-news samples, configs, assignments and checksums remain in ignored
`test-results/news-lens-replay-20260927/`.

## Pre-implementation evidence and boundaries

- `newsly-worker/src/briefing_refresh/semantic_lenses.rs` assigns unassigned
  pending news, maintains bounded-weight centroids, and names new lenses. At the
  active cap it skips novel-topic clustering; low-score forced assignments can
  also move the chosen centroid. Existing names do not evolve.
- `newsly-db/src/news_lens_embeddings.rs` already caches shared story vectors by
  model, encoder and exact input hash. Category membership remains user-scoped.
- Category retirement waits for no active segments or pending sources and an
  old `updated_at`. Assignments refresh that timestamp, so a category may remain
  indefinitely while its contents and meaning drift.
- `users` and `/auth/me` currently have no authoritative timezone. The legacy
  digest timezone is insufficient evidence of a user's current zone.
- Published news segments may combine multiple stories. Reassigning individual
  sources would break coverage or require paid recomposition. The first release
  changes future routing and uncomposed pending assignments; published segments
  retain their full source batches and remain readable in their original lens.

## Nightly scheduling

### Timezone ownership

Extend the existing authenticated `PATCH /auth/me` contract with an optional
IANA timezone identifier and expose the effective value in `GET /auth/me`.
iOS reports `TimeZone.autoupdatingCurrent.identifier` at sign-in and foreground
activation when the zone changes. The server records the accepted value and a
timezone revision using server time; device-provided timestamps are not trusted.
Reports carry a compare-and-set timezone revision. A stale device refreshes its
profile after a conflict and waits for its next real foreground activation before
trying again; idle background callbacks do not overwrite another device.

Validate against the timezone database; resolve supported aliases consistently.
An omitted field preserves the value, explicit null is rejected, and an unknown
zone produces a validation error. Existing users with no zone keep ordinary
arrival routing and skip nightly maintenance until a client supplies one. Do
not quietly treat UTC as the user's night. Existing clients remain compatible.

### Window and due time

Proposed default: nominal **03:00 local**, with a deterministic 0–30 minute
per-user jitter, within a **03:00–05:00 local publication window**. This hour is
a routine initial choice, not an empirically optimized result.

Persist an indexed UTC `next_due_at` and the corresponding local date/timezone
revision. A bounded scan from the existing UTC scheduler tick enqueues due users;
do not create one scheduler job per timezone. Limit claims per tick and concurrent
maintenance separately from ingestion and normal Briefing composition.

- Compute the next local calendar date, then resolve it through IANA rules;
  adding 86,400 seconds is incorrect across DST changes.
- A repeated local time chooses the earlier instant. The later occurrence does
  not produce another publication for that local date.
- A missing 03:00 resolves to the first valid instant before 05:00. Apply jitter
  as elapsed time after resolution. If an entire date/window is skipped, skip
  that date and schedule the next valid local night.
- A timezone change recomputes future due times and invalidates an uncommitted
  plan's timezone revision. A date already published in another zone does not
  run again after travel. Also impose a minimum 12-hour successful-publication
  separation to avoid near-back-to-back maintenance after crossing the date line.
- Start and final publication must both be inside the planned window. A delayed
  worker may retry within the window; after 05:00 it discards the candidate and
  schedules the next permissible night. There is no daytime catch-up publication.
- An unchanged corpus or inactive user skips clustering/naming while advancing
  the due time; empty input never removes unread coverage.

Use the existing Chrono stack plus a pinned IANA-capable adapter such as
`chrono-tz`. Explicitly handle `Single`, `Ambiguous` and `None` conversion results;
see [Chrono's timezone conversion contract](https://docs.rs/chrono/latest/chrono/offset/enum.LocalResult.html).
The implementation uses `chrono-tz` and explicit local-time resolution.

## Category maintenance and publication

### Bounded preparation

Take a short transaction to snapshot the user's eligible canonical sources,
subscription/visibility state, current routing categories, immutable source
fingerprints, Briefing version, timezone revision and job lease. Load only
compatible cached vectors; missing/stale vectors remain in the normal existing
preparation path and do not trigger a maintenance-owned archive backfill.

Start shadow evaluation with the replay's strongest mixed-source nightly candidate:
**14 days, warm starts, no time decay, at most 1,500 compatible vectors and 20
iterations, targeting at most 10 routing categories**. Keep seven-day decay as a
shadow challenger. These are provisional limits, not a requirement to publish
ten categories or change them every night. The replay showed little benefit for
narrow technology feeds and unsupported tiny clusters in the plain fitter.
Do not fit solely on unread items: recent read stories may support a stable
topic, but never restore their unread coverage. Use canonical events where the
existing relation model can establish them and cap duplicate coverage weight.
The current experiment's canonical IDs are only a proxy for distinct events.

### Candidate partition

Start from the prior centers when continuity is useful, run bounded spherical
clustering, and explicitly model low-confidence items as mixed coverage. Compare
variable category counts separately; a fixed number of centers does not discover
the appropriate topic count automatically. Weak matches must not contaminate
centroids. Reserve reachable mixed coverage instead of forcing unrelated stories
into an established topic when capacity is full.

Match new and old clusters using member overlap and a semantic-similarity guard.
For production use optimal one-to-one matching at the small category count and
record many-to-many lineage for splits/merges. The offline harness uses disclosed
deterministic greedy matching, so its ID churn is only an estimate. Preserve a
key for true semantic continuity; the largest supported successor may inherit
a split parent's key. Other successors receive new keys. Retired keys are never
reused for unrelated topics.

Require adequate distinct-event support and persistence before changing a name
or introducing a category. Do not choose thresholds solely by maximizing fitting
cohesion: clustering directly optimizes that measure. Shadow evaluation must
score candidates on sources excluded from fitting, compare next-night routing
quality, and set a churn budget before visible rollout. A validation holdout
must not also be included in the candidate's training set.

Test a provisional minimum of three distinct events across two nightly candidates
before promoting a new category. Evaluate merging or withholding undersupported
centers; do not expose singleton categories merely to fill the cap. This added
support policy has not yet been ablated. Keep weak stories reachable in mixed
coverage and calibrate its threshold against reviewed examples; the replay's .50
gate left 16.2% mixed and is not selected as a production default. Track category
size distribution and semantic movement from the last naming profile, not just
from yesterday's center. A sequence of small changes must not preserve a stale
name forever.

### Preserve reading and audio

The initial release publishes a routing partition for future arrivals and may
move only uncomposed pending news. Existing composed segments drain in their
original lenses. Distinguish the bounded number of categories accepting new
stories from categories still containing unread segments. The client keeps those
older categories accessible, even if that temporarily exceeds the routing cap.
Moving complete compatible segments is a separately validated follow-up; splitting
roundups or full-edition regeneration is not part of nightly maintenance.

Finalize in a fresh exact-lease transaction. Revalidate source fingerprints,
eligibility, ownership/generation, Briefing version, timezone revision, window and
dedup identity before changing anything. Swap routing centroids, weights, names,
lineage and pending membership atomically. Every source remains reachable exactly
once. Increment the user-scoped representation version once for the whole visible
change. Existing read identities, historical creation times and narration snapshots
remain intact. A stale plan leaves the current edition usable.

The durable success identity is `(user_id, local_date)`, with a unique constraint
and explicit scheduled/running/no-op/published/failed/skipped-window states. Queue
retries reuse that identity and cannot publish twice. The no-op outcome may advance
scheduling but must not masquerade as a successful repartition.

## Naming and cost bounds

On a materially changed nightly partition, review all active routing categories
together in one Luna naming request, up to 10 categories. Target **25 representative
stories per category** from its current 14-day fitting window, or all stories when
fewer are available: at most 250 story examples per review. This is a per-review
sample, not a weekly quota. Skip the review when its meaningful inputs are unchanged.

Include each category's stable ID, current name, routing description and story
count, plus the selected stories' titles, dates, source names and compact existing
summaries/key points. Select diverse canonical events across the window, balancing
central examples with recent and emerging subtopics; do not select only the 25
nearest duplicates. Bound text per story and the total request tokens while
preserving the target story count where possible. Do not send full article bodies
or regenerate summaries for naming.

Ask Luna to retain names that still describe their category and return revised
names/descriptions for any that have changed meaning. Reviewing the complete set
also permits distinguishing overlapping names. All supported categories may be
renamed in one review; there is no two-name limit. New-category promotion still
requires the support/persistence checks above, but existing names can be corrected
at each nightly review. Frequent review does not require gratuitous name changes.

Cache the batch response by model, prompt version and the complete naming input,
including category IDs, existing profiles, counts and selected story text. Cache
category profile vectors by the full profile text, model and encoder as well; the
profile includes the routing rule.

Set both per-user and global limits on naming requests and tokens, including retry
attempts. If the budget is exhausted or naming fails, preserve the current usable
partition and retry in a later permitted night. Never silently substitute a paid
model or invoke full-history classification. Idempotent candidate checkpoints should
reuse completed naming work when a late version check requires a local replan.

For the first pilot, allow one batch review plus one retry per user/night, with
per-request and global token ceilings. Size structured output for all 10 category
results; the current single-category naming response limit cannot be reused
unchanged. Measure input/output tokens, naming quality and observed spend on the
25-story samples before rollout. The replay made no naming calls and does not
establish a dollar estimate for this policy.

Local vector work has zero marginal provider spend, but CPU, memory, database reads,
new-story embeddings and ordinary news composition still have costs. The offline
run measures clustering compute and label-work proxies; it does not measure the
price or quality of proposed generated names.

## Implementation slices and acceptance

1. **Timezone and schedule state:** contract migration, account preference, iOS
   reporting, indexed due scan, date dedup and window fencing. Initially enqueue
   no-op/shadow maintenance. Test Los Angeles, UTC, Helsinki, Lord Howe, Chatham,
   a skipped calendar date, travel, aliases, concurrent devices and late completion.
2. **Pure planner and faithful evaluation seam:** extract current routing's pure
   vector/clock decision core without changing behavior; use it in production and
   `newsly-eval-driver`. The present control is explicitly only a surrogate.
3. **Shadow nightly candidates:** implement the selected bounded fitter, vector
   compatibility checks, lineage and holdout diagnostics. Collect at least two
   continuous weeks per pilot user, including actual read/compose timing. No visible
   move occurs in shadow mode.
4. **Fenced routing publication:** atomic category replacement with old unread lenses
   retained, bounded naming cache/budget, pending-source ownership and version updates.
   Verify success, malformed vectors, cancellation, lease loss, version/source/timezone
   changes, retries, partial provider failure and one-publication-per-date semantics.
5. **Small pilot and wider rollout:** inspect coherent sample topics and misroutes,
   held-out fit, mixed share, births/renames, genuine ID churn, CPU/memory, and observed
   naming/embedding usage. Set thresholds from shadow evidence; the offline geometric
   metrics alone are insufficient to authorize broad category replacement.

Update Briefing laws B5–B10 and B15 and processing law P28 when the behavior is
implemented, with durable ownership notes in `docs/architecture.md`. Validate
Rust formatting, warning-denied Clippy, pure and isolated-PostgreSQL tests,
contract drift and relevant Swift model/UI tests. A release requires separate
authorization and the canonical release gate.

## Review resolution

The read-only Claude Opus 5.5 review identified temporal leakage, circular cohesion
scoring, insufficient exact historical state, source/read eligibility drift, the
limited ability to move already-composed content, and DST windows that disappear.
This plan uses explicitly labeled final-text experiments, pre-update arrival metrics,
a source-only sample rather than invented user history, future-routing publication,
and a 03:00–05:00 window with explicit gap/fold handling. The current-vector cache
does contain older backfilled texts; they support a retrospective 28-day sensitivity
arm, but do not establish a faithful August production replay.

## Local implementation and rollout status

The durable scheduler, dedicated worker, timezone API/iOS reporting, cached-vector fitter, bounded naming provider, and fenced publication are implemented. Local validation covers timezone/calendar behavior, duplicate scheduling, stale input, lease loss, late windows, canonical event support, naming-cache reuse, preserved read/composed coverage, account deletion, and client lifecycle reporting. The read-only architecture review findings were addressed; no production changes or live provider evaluations were made.

The first release defaults to shadow mode. After authorized commit/push and the clean release gate, deploy through the normal blue-green path and verify the dedicated worker, migrated task ownership, and scheduler health. Initialize only the explicitly identified account's timezone through the authenticated profile contract or a bounded operator action; verify its next local date, due time, and timezone revision. Do not infer other accounts' zones from the operator's location. A first shadow run produces a proposal and diagnostic evidence without changing visible categories. Enabling publication is a separate rollout decision informed by that evidence.
