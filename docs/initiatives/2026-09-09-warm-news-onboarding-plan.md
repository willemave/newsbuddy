# Warm news and faster onboarding

Date: 2026-09-09. Status: proposed implementation plan; no runtime changes.

Build on the existing global news pipeline. Keep all seven supported aggregators
prepared in the background, then use their ready stories to produce a new user's
news lenses. Process the user's article and podcast feeds concurrently, with
durable progress visible from the first Briefing response.

## Evidence and corrections to the earlier design

The September 2 session (`01a064de-f0bc-78c3-862c-7d799b06f054`) investigated
onboarding latency and proposed a warm corpus. Its historical example admitted
215 aggregator items despite a saved per-user limit of one. Those timings and
volumes have not been remeasured in production.

The later [September 2 design](2026-09-02-aggregator-corpus-first-run-design.md),
committed September 4 in `9d20758b`, corrected the premise: global ingestion and
ready-news reuse already existed. This plan revises that design against the
September 9 checkout:

| Current evidence | Implementation consequence |
| --- | --- |
| Scheduler requests `sources: ["all"]` every 15 minutes; workers expand all seven aggregator keys. | Add demand-sensitive eligibility to the existing scheduler/dispatch path. Preserve the other source schedules. |
| Scrape dispatch already creates independent source tasks. | Reuse these retry boundaries; do not rebuild fanout. |
| `source_ingestion_health` already records aggregator checks, successes, and yield. | Extend/reuse that state instead of adding the old proposed `aggregator_refresh_state` table. |
| Onboarding still adds aggregator keys to `sources_to_scrape`; first-run source rows begin queued. | Replace the redundant scrape with a truthful ready-corpus attachment and shared recovery when needed. |
| Briefing preparation already seeds visible, ready, unread global news. | Preserve visibility, topic filtering, representative deduplication, and read semantics. |
| `plan_semantic_lenses` requests embeddings for each user's pending stories. | Prepare and persist reusable lens-input embeddings globally. |
| News relation embeddings are temporary and use their own text preparation. | Equal model names do not establish interchangeable vectors. Match exact input, model, dimensions, and encoding version. |
| First-run progress currently stores source outcomes and counts. | Source fetch completion cannot stand in for article/podcast processing or Briefing publication. |
| Discussion catch-up already tests subscriber demand. | Audit all discussion entrypoints before adding another demand gate. |

Source owners: `newsly-scheduler/src/{schedule.rs,repository/fanout.rs}`;
`newsly-worker/src/{scrape.rs,scrape/dispatch.rs,news_item/handler.rs,feed_backfill.rs}`;
`newsly-api/src/onboarding_flow.rs`; and
`newsly-db/src/{source_health.rs,onboarding_flow.rs,briefing.rs,briefing_refresh/}`.
All Rust paths are under `rust/crates/`.

## Proposed behavior

**Refresh and readiness.** Fetch subscribed aggregators hourly and unsubscribed
aggregators every two hours. Define subscribed as at least one enabled matching
configuration belonging to an active user. These are starting defaults, not
measured optimal intervals. Keep all seven sources and their supported default
topic coverage warm, including sources with no subscribers. Do not apply the old
proposal to stop fetching unsubscribed Brutalist topics: that would leave the
first subscriber waiting again.

An hourly schedule describes fetch frequency, not a guarantee that every resulting
story is processed within an hour. Track last successful check, newest eligible
ready story, processing backlog age, and lens-embedding coverage separately.
Top lists can lose stories between polls; measure this particularly for Hacker
News before declaring two hours sufficient. Shorten an individual source's
interval if observed turnover warrants it.

Keep normal article extraction, summary generation, and story deduplication in
the shared pipeline. Prepare lens embeddings before declaring the warm-path
preparation complete. Discussion updates remain optional background enrichment.
Do not mark a title-only or failed extraction as fully prepared to improve a
readiness metric. Publisher extraction policy and summary quality remain intact.

**Onboarding.** Persist subscriptions and the first-run record, attach eligible
ready news, and enqueue personalized lens assignment/composition plus feed
backfills in the same durable transaction. Warm onboarding makes no aggregator
HTTP requests and creates no duplicate news-processing tasks. Lens naming and
Briefing composition still require per-user work; the warm pool does not make
those model calls disappear.

For an empty or stale source, display available eligible news immediately and
request one deduplicated global refresh. Never complete its source chip merely
because a subscription exists. A successful check yielding no eligible items is
a truthful empty result; a source failure is unavailable/retrying. A current
fetch with unfinished processing stays in progress. Shared task completion must
reconcile every active first-run waiter, including after a worker restart.
Expiry and subscription removal fence those updates.

**First readable edition.** Start with up to 40 unique ready stories total from
the existing eligible window, newest first with coverage across selected sources
and topics. This is a benchmark starting point. Apply the bound before admission
to the first batch; assign every admitted story. Persist the selected cohort or
equivalent durable first-batch identity so retries and concurrent sweeps cannot
continually refill it. Once that batch publishes or settles, resume normal
admission of remaining eligible stories. Do not mark deferred stories read,
delete them, or truncate the full pending set during semantic assignment.

Use the existing queue's priority and worker separation to get the first news
edition through promptly while preserving capacity for feed processing. Measure
content/media/LLM queue wait separately. Bound optional discussion work and cold
warming concurrency so they cannot consume all extraction or model capacity.
Priority alone does not increase capacity or preempt a running task.

**Articles and Podcasts.** Show enabled fixed lens pills from the first index
response. Before segments exist, tapping a pill shows its selected feeds and
processing progress. Render readable segments as they arrive. For a tier with no
subscriptions, show an add-source empty state rather than perpetual processing.
Keep existing readable content visible through failures and retries.

Distinguish discovery still running, item preparation, and publication. User copy
can say “5 articles ready. Preparing 11 more.” Only count an item as ready to read
when it is available in the relevant Briefing lens. A completed content summary
awaiting composition is still being prepared for this surface. Empty feeds,
failed items, and skipped items have explicit terminal outcomes. Avoid fabricated
percentages or completion-time estimates.

## Implementation slices

1. **Establish the baseline and source schedule.** Use read-only `newsly-admin`
   evidence to confirm the deployed SHA, source freshness, ingestion activity,
   queue wait, and ready-story coverage. Reuse source health for due calculations;
   enqueue one global task per due aggregator using a source-based dedupe key
   shared by scheduled and first-subscription refreshes. Recheck due state in the
   worker; honor retry backoff and preserve manual-refresh semantics. Keep feed,
   Reddit, and discussion scheduling separate from aggregator eligibility.
   Update monitoring to use the new expected intervals and resolve global
   aggregator health by source key: the current generic 45-minute/config-ID
   check does not fit hourly global sources. Deliverable: all seven sources
   refresh independently at the intended intervals, without duplicate work.

2. **Persist reusable lens embeddings.** Define one canonical lens-input encoder
   using the planner's actual title, summary, and key points. Store vectors in
   SQLx-owned persistence keyed by story identity, input hash, provider/model,
   dimensions, and encoder version. Integrate preparation into the existing news
   task/checkpoint path with exact-lease finalization. Reuse only matching vectors;
   regenerate on relevant input/model changes. Provide a bounded continuation
   task for preparing existing eligible ready news, coalescing concurrent misses.
   Keep original ready stories readable during this migration. Complete this
   bounded warm-up before switching the onboarding path. Deliverable: a second
   user requires no new embedding calls for the same prepared stories.

3. **Attach onboarding to prepared news.** Remove aggregator keys from the
   user-specific scrape request; preserve Reddit scraping and current feed
   backfill limits. Add the source-result reconciliation described above, the
   stable bounded first batch, and normal follow-on admission. Keep exact owner,
   lease, source, and version checks. Both stale lens assignment and stale
   composition must schedule bounded recovery through existing retry/sweep
   machinery; do not introduce an unbounded immediate loop or weaken fences.
   Reconcile durable older first-run tasks through the same shared source owner
   so a rollout does not strand their progress. Deliverable: warm onboarding
   reaches readable personalized news without waiting for source scraping.

4. **Expose reliable tier progress.** Extend the existing Briefing first-run
   contract with article/podcast counts and discovery state. Record a minimal
   run-to-content membership relation when backfill reuses or creates a content
   row; derive lifecycle from canonical content and Briefing coverage. This is
   membership, not a second processing state machine. The old proposal to count
   every currently visible row cannot freeze a run's denominator or exclude later
   scheduled arrivals. Use uniqueness to avoid double counting a story found in
   two feeds. Ensure source failures before item discovery are visible too.
   Include status/publication changes in first-run revision invalidation, and
   make the projection and validator consistent. A progress change must not be
   hidden by `304`. Regenerate OpenAPI and Swift models, preserve required keys,
   and update the existing SwiftUI first-run coordinator and lens surface.
   Deliverable: accurate progress survives restart, shared reuse, failure, and
   partial publication.

5. **Validate under load and roll out.** Update Briefing and processing laws,
   architecture notes, and the implementation log as each slice lands. Measure
   new-user completion-to-first-readable-news separately from discovery time,
   time to first article, and time to first podcast. Proposed performance target:
   warm first news p95 under 30 seconds, to be tested rather than promised.
   Require no aggregator fetch on a healthy warm attachment, no repeated matching
   embeddings, and no regression in article/podcast queue wait under equivalent
   load. Roll out schema and producers, perform bounded preparation, then switch
   consumers. Any temporary rollout switch has one owner and a removal task.
   Production deployment remains a separate explicitly requested release.

## Verification

Use focused Rust and PostgreSQL tests for concurrent scheduler/onboarding refresh,
duplicate story reuse, zero subscribers, topic filtering, empty/failed/stale
sources, retry backoff, cancellation, lease loss, changed subscriptions, and
deleted users. Exercise refresh boundaries and monitor thresholds with a fake
clock. Verify that source failure cannot roll back healthy sibling results.

Test embedding cache hits and invalidation, wrong dimensions, malformed provider
responses, interrupted preparation, and coalesced concurrent misses. Test first
batch bounds across retries and sweeps, eventual admission of the remaining
eligible stories, canonical duplicate read state, and unchanged coverage fences.

For progress, test shared pre-existing content, duplicate feeds, ongoing discovery,
terminal failure before discovery, summaries awaiting composition, skipped items,
app relaunch, expired runs, and ETag invalidation. Run Simulator scenarios with
warm news and slow podcasts, no feeds, one failed feed, and partially ready tiers.
Run affected formatting, warning-denied Clippy, SQLx metadata, contract drift,
Rust tests, and native client checks. Implementation validation is recorded in `docs/log.md`. Production rollout and
latency/load measurements remain separate release work.

## Scope and tradeoffs

Recommended: retain the fully prepared shared corpus and separate onboarding
attachment from refresh. Metadata-only caching saves background processing but
leaves extraction and summarization on the new user's path. Merely dropping the
extra onboarding scrape is a useful slice, but leaves repeated embeddings and
unbounded first-edition work.

Changing cadence reduces polling overhead; provider spend depends mainly on new
unique stories, extraction fallbacks, and repeated discussion enrichment. The
September 2 dollar estimates are historical and are not a current budget. Gather
seven days of actual intake and priced usage before changing extraction quality,
topic coverage, or provider plans. Keep optional enrichment tuning separate from
the first-news latency goal. No provider pricing or production state was checked
in this planning pass.

## Implementation status — September 9

Implemented locally: shared cadence and dedupe, durable model/input-specific lens
embeddings, bounded scheduler preparation, foreground task promotion, frozen
first-news admission, bounded stale recovery, and run-owned article/podcast
progress in the API and native client. The migration and consumers ship together;
a cold installation prepares missing vectors through the same durable queue and
may have slower first onboarding until the corpus is warm. Production release
must allow the bounded preparation queue to drain before measuring warm latency.
No production migration or paid performance canary has been run.

## Review corrections

The Fable review exposed cross-tier blocking, terminal progress, failure-isolation,
and client compatibility gaps. The corrected rollout uses a dedicated preparation
worker; cache misses leave only those news stories pending while other ready tiers
publish. Warm-up remains required before measuring warm latency, but cold warming
does not block article or podcast publication. Exact input checkpoints bound
terminal failure retries to six-hour recovery windows, and failed stories receive
isolated assignment. Aggregator check results are frozen per run and durable.
Abandoned runs expire after 24 hours. Native decoding tolerates older responses;
selection, error visibility, and relaunch behavior have regression coverage.
See `docs/log.md` for final validation and release status.

The existing-table indexes use separate nontransactional concurrent migrations.
If an index build is interrupted, inspect PostgreSQL index validity and migration
state before retrying. An invalid index left by the failed build must be dropped
concurrently before rerunning that migration; preserve valid indexes and do not
mark a failed migration complete without verifying its index.
