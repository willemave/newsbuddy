# Ingestion regression review and prevention plan

Implementation follow-up: the core safeguards are now present locally. See
[the operational runbook](../runbooks/ingestion-regression-prevention.md) for
implemented behavior, validation, deployment prerequisites, and remaining
fault-injection coverage. The review below preserves its original evidence and
proposed scope; it is not a claim that every suggested scenario has been added.

Proposed on 2026-09-06. This is a review and implementation plan, not a release or a change to product behavior. The review covers the archive-import incident, its production cleanup, the related discussion and Briefing failures, the pending artwork changes, and operational detection. Unrelated iOS and artwork-composition changes are outside scope.

The archive regression is fixed in production. The remaining work is to integrate the stronger regression test, repair a separately reproduced Briefing citation defect, and make unexpected successful ingestion and repeated rejected generation visible. Increasing media concurrency would have processed more unwanted history; it would not have corrected the incident.

## Evidence and implementation state

Production workers and scheduler still run `e0e8d39a826273a3c00a03c4893e67438e1d3b2b`. At 13:35 UTC on September 6, operator health reported zero overdue tasks, zero failing sources, zero terminal-product mismatches, and a recent successful source sweep at 13:30 UTC. The configured queue-alert destination is still absent. These are point-in-time measurements.

| Work | What is actually present | Review conclusion |
| --- | --- | --- |
| Earlier ingestion repairs, `c3ec01dd`, `4e64d075`, `add3ffbe` | Exact ingest-key lookup, per-item persistence, failed-finalizer settlement, RSS enclosure support, and shared feed parsing/backfill | Preserve these fixes and their persistence, ownership, partial-progress, and metadata tests. They addressed missed ingestion rather than authorizing historical ingestion. |
| Pipeline resilience, `d1c49bf5` | Shared parser skips known entries before applying the intake limit; scheduled callers load known URLs. Also includes discussion validation constraints, terminal validation policy, source health, and truthful unknown-cost reporting | This commit introduced the archive traversal. The useful reliability fixes are separable from that selection-policy change. |
| Feed hotfix, `e0e8d39a` | Required `FeedEntrySelection` policy; scheduled callers use `StopAtKnown`, explicit backfill/download-more use `SkipKnown`; S13 amended | Correctly fixes the demonstrated traversal. The deployed added test exercises one parser invocation. |
| Production cleanup | 944 tasks bear `incident_2026_09_05_archival_feed_import_cancelled`; a fresh query confirms all 944 associated task/product pairs are failed | These are intentional incident cancellations encoded as failures. They must remain distinct from ordinary processing failures and completions in reports. |
| Cleanup audit in hotfix worktree | Records 1,363 accidental memberships archived, five real read events preserved, three recent arrivals retained, and 943 processing content rows settled | The 943 mutation count differs legitimately from the 944 current failed task/product pairs: do not collapse mutation counts, tasks, and final-state counts. Historical membership/read counts were not remeasured in this review. |
| Stronger feed integration test | Uncommitted in `/private/tmp/newsly-feed-hotfix.R2P0Wy`, absent from `main` and the deployed commit | Recover and integrate this existing work before writing a duplicate test. Its recorded negative control restored `SkipKnown` and exposed both archive URLs. |
| Artwork-gated Briefing changes | Uncommitted in the main checkout, including `20260905000000_briefing_requires_artwork.sql` | Not a deployed fix. Assess their migration scope and end-to-end tests before release. |
| Briefing coverage failures | No dedicated citation-parser repair in `e0e8d39a`; a disposable reproduction still fails on that revision | Recent successful refreshes establish recovery, not removal of this defect. |

The main checkout remains at `eb3d2f07` with concurrent local changes; the hotfix worktree is detached at `e0e8d39a`. Source, uncommitted tests, deployed code, and historical validation records must be reconciled explicitly during implementation.

Relevant source owners: [feed normalization](../../rust/crates/newsly-providers/src/scraping/feed.rs), [scheduled ingestion](../../rust/crates/newsly-worker/src/scrape.rs), [known-feed membership lookup](../../rust/crates/newsly-db/src/content_misc.rs), [source health](../../rust/crates/newsly-db/src/source_health.rs), [watchdog delivery](../../rust/crates/newsly-scheduler/src/runner.rs). Read deployed behavior with `git show e0e8d39a:<path>` because this checkout has not incorporated that commit.

## Causes, confidence, and why the checks missed them

### Confirmed: catch-up policy crossed into scheduled refresh

`d1c49bf5` moved the intake limit from the number of feed entries examined to the number of unseen usable items selected. That is useful for an explicit backfill. It also changed scheduled polling into a process that walks farther backward on every successful run. Deduplication prevented duplicate rows but kept exposing the next unseen historical batch.

The state transition is small enough to reproduce without a provider:

```text
Feed order = [N, K, A, B, C]; N is new, K is known, A/B/C are archive entries.
Initial membership = {K}; selected-item limit = 2; pending child work = {}.

Broken poll 1: select [N, A]; persist membership {K,N,A}; enqueue {N,A}.
Broken poll 2: select [B, C]; persist membership {K,N,A,B,C}; enqueue {B,C}.
All four jobs are valid and unique, but three violate scheduled intake intent.

Fixed poll 1: select [N], stop at K; persist {K,N}; enqueue {N}.
Fixed poll 2: stop at N; persist nothing; enqueue nothing.
Fixed poll 3 after a new head M: select [M], stop at N; enqueue only {M}.
Explicit backfill may scan past K, under its own bounded request.
```

The earlier S13 described catch-up but did not explicitly forbid scheduled archive traversal. Existing tests emphasized making progress past known/malformed entries. They tested the newly desired behavior without also asserting that ordinary polling remained unchanged. The change was bundled into an 80-file commit with 4,199 insertions and 1,025 deletions. That breadth is a plausible review contributor, not proof of any individual review mistake.

The release smoke covers authentication, chat, Share-to-chat, Share-to-deck, grounded chat, and failure/ownership boundaries. It does not currently simulate repeated feed polls. A large passing test count therefore did not cover the triggering workflow.

### Reproduced: Briefing validators disagree about bracketed titles

Provider validation finds source URI text in a passage. Worker normalization recognizes source links using a regular expression whose label cannot contain `]`. A valid nested or escaped square bracket in a title can pass the first check and fail the second.

```text
source = {key: content:1, title: "A study [Update]"}
layout = {markdown: "[A study [Update]](newsly://briefing/content/1) explains the result."}
provider validation: URI found; source accepted.
worker normalization: source-link regex finds no complete link; covered = {}.
publication: rejected with MissingCoverage(["content:1"]).
retry: regenerate the same source window; the same title can reproduce the defect.
```

Two temporary tests in a disposable copy of `e0e8d39a` demonstrated this: provider validation passed; worker normalization failed with `MissingCoverage(["content:1"])`. All five source IDs named in the incident failures—30108, 59546, 59498, 59499, and 59534—have square brackets in their persisted titles. This is a strong explanation for the incident, but the exact rejected model responses were not retained, so it is not a complete replay of those outputs.

See [provider layout validation](../../rust/crates/newsly-providers/src/briefing_composition.rs) and [worker normalization](../../rust/crates/newsly-worker/src/briefing_refresh/normalize.rs). Healthy later calls can result from different generated link labels; they do not falsify this deterministic parser defect.

### Confirmed amplification and monitoring gaps

Discussion generation now exposes character limits through schema and prompt and treats exhausted validation as terminal for the durable task. Its boundary tests also use multibyte characters. That is a good fix. It does not by itself test the whole fetch → failed generation → preserved prior summary → later scheduled refresh lifecycle.

Briefing still has nested budgets: up to three structured-generation requests inside a composition call, four composition attempts, queue retries, then future scheduler jobs. Exhausted composition errors are still returned as retryable. In addition, `compose_unit` retains usage only for accepted output; rejected output and otherwise successful units lost when another unit fails do not reach the publication usage path. The prior checkup's recorded call count cannot establish the true retry multiplier. Unknown prices and missing attempt records are different gaps.

The watchdog detects three consecutive source failures, pending work over two hours, and selected task/product mismatches. It does not detect a source that never obtains its first health row, stale successful checks, sustained queue growth, or unexpectedly old incoming content. Its missing Slack destination currently produces a warning in the same application logs. Even when configured, delivery has no durable pending state, deduplication, or recovery notification.

The pending artwork policy also reverses the earlier optional-artwork decision. That is an intentional product-policy change and needs its own review and tests. Its migration presently selects all matching completed content without a visibility constraint or bounded release batch. A read-only preflight matched 1,367 candidate rows: 1,162 articles and 205 podcasts; 1,330 had active inbox memberships. This is the content/work-selection predicate before runtime-ownership checks, not a count of jobs created. It could create another substantial backlog if shipped unchanged.

## What to retain and strengthen in tests and laws

| Existing protection | What it proves | Remaining assertion |
| --- | --- | --- |
| `scheduled_feed_stops_at_known_frontier_without_hiding_newer_usable_entries` | Malformed head, usable new item, known boundary, archive tail; correct parser selection | Repeated persisted cycles and actual caller policy |
| `scheduled_feed_cycles_never_cross_the_persisted_frontier` in the hotfix worktree | Real Atom/RSS normalization, archived known memberships, two cycles, only two `process_content` children, final source counters | Integrate it; add a new head on a later cycle, assert read/archive state directly, and exercise actual task claim/lease finalization |
| Feed-backfill PostgreSQL tests | Metadata, partial progress, cross-user reuse, archive preservation, atomic parent/child completion, config-change fencing | Pair the same archive fixture with explicit `SkipKnown`; do not accidentally disable requested catch-up |
| Discussion schema/retry tests and P26 | Generation and persisted length bounds agree; deterministic validation terminates; provider/deadline failures retry | Full worker lifecycle with a fake model, preserved good summary, exact request counts, and subsequent changed-input refresh |
| Briefing link/normalization tests and B3/B6/B9/B14 | Basic source identity, coverage, previous-edition preservation, and references | One shared accepted-link corpus across validation, normalization, narration, and final publication; include bracketed/escaped titles |
| Source/watchdog tests and P15 | Failed source differs from healthy empty source; a three-hour-old ready task is overdue | Healthy-looking runaway success, missed checks, first-attempt absence, alert delivery failure, restart, deduplication, recovery |
| Pending artwork tests and C5/P25 | Summary waits for artwork; helper enqueues Briefing for a pre-completed source; terminal image failure stays blocked | Real image finalizer + queue + files + fanout transaction, missing pointers, lease/fingerprint loss, terminal visibility, and migration scope |
| P27 usage reporting | Unknown cost remains unknown with a known subtotal | Every observed generation attempt counted, including rejection/failure, without duplicate billing records |

Extend the existing laws rather than adding a separate policy document that competes with them:

- S13: keep scheduled refresh and explicit catch-up distinct; an unchanged scheduled feed creates no new content, memberships, or paid downstream work after its admitted head settles. State the behavior for a missing boundary and more new entries than one intake batch.
- B3/B6/B14: source identity and coverage survive valid punctuation and Markdown escaping in titles; all stages agree on accepted references.
- P7/P26: deterministic output failures have one bounded correction policy; future jobs cannot create an immediate unlimited loop for unchanged input. Preserve controlled later recovery when input or the generator/parser version changes.
- P15: expected-but-missing checks, sustained backlog growth, and alert-delivery state are observable. Intentional incident cancellations remain auditable and separately counted.
- P27: extend attribution to observed unsuccessful/rejected attempts while retaining unknown costs; never reconstruct missing historical prices or raw outputs as fact.
- C5/P25: retain readable summary access while gating Briefing on the chosen artwork policy; blocked/failed artwork has a truthful operator and client state. Schema deployment must not silently authorize unbounded historical provider work.

These are proposed law amendments. The canonical law files were not edited during this review.

## Recommended implementation sequence

1. Integrate the prepared feed test and close its lifecycle gaps.

   Start from the deployed `e0e8d39a` lineage and bring over only the reviewed test and transport-free normalization export from the hotfix worktree. Preserve concurrent artwork changes separately. Exercise the scheduled production target builder, PostgreSQL known-state lookup, real claim/finalization, and child-task uniqueness. Retain a disposable negative control: selecting `SkipKnown` for scheduled work must expose both archive URLs. Add an unchanged second poll, a genuinely new third-poll head, explicit backfill, archived/read/saved preservation, and disabled/config-edited sources.

   Cover reordered or undated entries, a removed known item, changed canonical URL with stable feed identity, and a new-item window larger than the intake limit. These are boundary-risk scenarios, not claims that they caused this incident. If the membership-based boundary cannot preserve the intended behavior, introduce a small per-feed boundary/continuation only for that demonstrated case. A continuation may finish the bounded window ahead of the previous boundary; it must not turn into unrestricted archive scanning. Bootstrap remains an explicit bounded subscription/backfill operation.

2. Repair Briefing citation parsing with the reproduced failure as the first test.

   Make provider validation and worker coverage/rendering use one canonical interpretation of source links. Prefer an established Markdown parser, selected against the fixture corpus, over growing several regular expressions. The existing provider-owned composition boundary can expose a small Newsly-owned parsed-reference representation to its worker consumer; no new service is needed. Preserve allowlisted source identities and exact coverage rules.

   Test plain labels, nested and escaped brackets, Unicode, bold labels, punctuation, parentheses, IDs 1 versus 10, unknown IDs, duplicate News citations, bare URIs, and URI text inside code. Follow an accepted layout through normalized runs, narration, and database publication; malformed or unknown references must preserve the previous usable edition. The synthetic bracket test must pass without asking the model to rename the article.

3. Bound retry amplification and preserve attempt accounting.

   Classify composition transport failures separately from invalid generated layouts and deterministic local normalization defects. Feed actionable validation feedback into the existing correction budget rather than repeatedly regenerating an unchanged request. After exhaustion, settle the task and record the affected source/input fingerprint plus generator/parser version. Suppress immediate identical retries across newly scheduled jobs, with bounded later retry eligibility and immediate eligibility for materially changed input/version. Keep the prior edition readable.

   Record each observed provider request/result through the existing usage ledger with task, attempt, operation, response identity when available, input fingerprint, outcome, tokens, and known/unknown cost. Use a short ownership-checked transaction independent of publishing a successful segment; failure and cancellation paths must preserve already observed accounting. Idempotent recording must prevent duplicate records. An external acceptance followed by a crash can still leave unknown usage unless the provider offers a recoverable identity.

   Use fake-provider tests to assert request counts across validation corrections, durable retry, and later scheduling. Include one rejected unit alongside a successful unit so both attempts remain attributable even when the complete edition is not published.

4. Extend the existing watchdog and operator reports; deliver alerts reliably.

   Keep Rust, PostgreSQL, `newsly-admin`, and the existing alert transport. Persist compact five-minute observations with bounded retention so queue growth survives process restart; derive task activity from queue records and retain source-level counts that are currently overwritten on each check. Track queue-ready age separately from future-scheduled work and processing duration separately from expired leases. Compare health rows with the expected active source inventory to find missing observations.

   Suggested initial thresholds below are starting values for replay/calibration, not existing production settings. Use configurable per-task overrides where runtimes differ; do not infer source failure solely from no new articles or episodes.

   | Signal | Initial trigger | Expected response |
   | --- | --- | --- |
   | Missing scheduled source check | No completed attempt for three expected intervals: 45 minutes at the current 15-minute cadence | Alert with source ID, last attempt/success, next due work; account for disabled sources and startup grace |
   | Queue growth | At least 100 ready media jobs, positive net growth in two consecutive five-minute samples, and oldest ready age over 15 minutes | Alert before the current two-hour overdue threshold; include enqueued/completed/failed/cancelled counts and current throughput |
   | Zero progress | Ready work exists and no completion for 15 minutes for short tasks; longer explicit thresholds for media/decks | Inspect worker activity and deadlines; do not count a future Briefing timer as a stall |
   | Suspicious historical intake | A non-bootstrap scheduled source imports at least 10 items, at least 80% older than 30 days, in two consecutive polls | Warn with aggregate counts and provenance; age alone must not delete, mark read, or reject legitimate content |
   | Repeated output rejection | Same source/input fails validation three times in 30 minutes across attempts/jobs | Alert with stable reason, parser/model versions, and request count; apply the bounded recovery policy |
   | Artwork blocked | An image task has terminated unsuccessfully or eligible work lacks a next task beyond its deadline | Show a dedicated blocked-artwork signal; current generic mismatch detection omits `generate_image` |
   | Missing/failed alert delivery | No configured destination, failed bounded delivery, or overdue durable delivery | Show degraded monitoring in operator status; retry delivery with backoff and emit recovery |

   Persist an alert key and state: pending delivery, sent, next retry, last reminder, and recovered. Deduplicate unchanged signals, send bounded reminders, and record one recovery notification. A fake HTTP receiver must test timeout/429/5xx, scheduler restart, cooldown, and recovery. Configure and test the actual destination as a separate operator step. No Slack message was sent during this review. An external checker remains necessary to detect a stopped host/scheduler; document that residual gap rather than claiming the scheduler can alert on its own death.

5. Make incident reconciliation and artwork backfill explicit, bounded operations.

   Convert the successful cleanup approach into a narrow supported operator workflow: dry-run selection by incident/source provenance, creation window and expected state; immutable target IDs/counts; actor/reason; guarded transactional application; and post-state verification. Preserve true read events, saved state, canonical content, and recent legitimate arrivals. Reapplying the operation must be a no-op; a stale active lease must not republish cancelled work. The current `newsly-admin tasks` surface only lists failures, so this needs an explicit audited command rather than unrestricted SQL access.

   Keep intentional cancellations visibly classified even while stored queue status remains `failed`; prefer a stable reason/category over a broad task-status migration. Use publication age only to narrow a documented incident, never as a general rule that old content is invalid.

   Before releasing artwork gating, replace the migration's unbounded historical enqueue with a reviewed candidate inventory and bounded batches owned by the normal queue. Decide eligibility for inbox, saved, archived, inactive-user, and terminally failed artifacts. Test migration idempotency, pointer/timestamp disagreement, version bumps, preservation of active image work, and no repeat enqueue of exhausted work without explicit retry policy. Retiring visible segments must be consistent with the selected C5 policy and observable while replacement work runs.

6. Tie release evidence to these behaviors.

   Land each slice as a coherent end-to-end change with its law, regression, and operational check. Add the deterministic repeated-feed and citation corpus tests to the canonical local release gate. Inspect exact test names and results; raw total test counts are insufficient. The current GitHub deploy workflow does not rerun the source suite, so tests must be in the released tree and executed by the actual release gate before claiming enforcement.

   Keep the existing live-smoke release gate for relevant provider/runtime changes, and add feed-worker coverage using deterministic fixtures so exercising polling never depends on a private feed or fresh paid transcription. After an authorized release, verify the exact image and observe at least four 15-minute source intervals. Report baseline versus post-release ingestion, new versus historical content age, ready queue trend, terminal cancellations, validation attempts, and alert delivery. Use a 24-hour follow-up for confidence; healthy live feeds that happen to publish nothing do not replace the deterministic test.

## Approach and acceptance

The recommended approach is the existing typed feed policy plus integrated lifecycle tests, a shared citation parser, bounded attempt accounting, and the existing watchdog with durable delivery. Tests alone are cheaper but leave silent runaway success and absent notifications. A generalized feed ledger, automatic circuit breakers, or a new monitoring stack would add more state than the demonstrated incident requires; introduce those only when the boundary tests or operational evidence justify them. Higher worker concurrency is a separate capacity decision after validating intake.

The work is complete when the integrated feed test fails under the old policy and passes under the shipped policy; bracketed titles survive the entire Briefing path; malformed-generation request counts are bounded and accounted for; a simulated healthy-looking ingestion burst triggers one timely alert and a recovery; incident cleanup preserves user history; and any artwork backfill runs under an explicit bounded inventory. A release must include the tests, not merely the runtime fix.

Fresh validation in this review: the 15 deployed feed/provider tests and three discussion schema/retry tests passed. Temporary diagnostic tests reproduced provider acceptance followed by worker `MissingCoverage` for a bracketed title. The saved hotfix integration test passed against an isolated local PostgreSQL test database. Its first run using a shared Cargo target could not resolve the newly exported provider function; a fresh isolated target compiled and passed. The deliberate `SkipKnown` negative control is recorded in the hotfix worktree's prior validation, not rerun in this review. Full release validation, product implementation, production repair, commit, push, and deployment are outside this planning turn.
