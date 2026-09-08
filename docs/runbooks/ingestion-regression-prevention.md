# Ingestion regression safeguards

Implemented locally on 2026-09-06. Deployment and a real alert destination are separate operator steps.

## Behavior

- Scheduled feeds stop at the first known usable URL. Repeated polls cannot walk through archive tails. Missing boundaries and oversized heads remain bounded; explicit catch-up handles entries beyond that head.
- Briefing validation, coverage, runs, and narration use one CommonMark citation parser. Links with bracketed titles count; bare URIs, code, and images do not.
- Briefing composition has three correction attempts by default, with feedback, rather than nested model and worker correction loops. Exhausted output is terminal for that task. A source/input/model/parser fingerprint cools down for 24 hours; changed input or version is immediately eligible. The previous edition remains readable.
- Observed usage commits under the original live claim before edition publication. Accepted siblings and rejected responses remain counted. Attempt UUIDs prevent duplicate recording. Usage emitted before a provider validation error is retained; an error without usage is explicitly recorded as unknown, not reconstructed.

## Monitoring

The existing five-minute watchdog retains seven days of queue and source observations. It detects three failed checks, a missing source check after 45 minutes (including no first check), growing media queues before the two-hour overdue limit, stalled ready work, repeated historical intake, exhausted Briefing output, and artwork with no remaining task.

Historical intake is a warning, never permission to delete or mark content read. The initial warning threshold is two polls each admitting at least ten items with at least 80% published over 30 days ago. Future-scheduled tasks are not ready backlog. Short queues use a 15-minute progress window; media, LLM, image, and audio queues use two hours.

`newsly-admin health snapshot` includes monitoring counters and intentional incident cancellations. `pipeline_alerts` retains pending delivery, revision, acknowledgement, last error, and next retry. Unchanged conditions are deduplicated; active alerts remind after six hours and recovery gets its own revision. Failed HTTP delivery retries on a later watchdog tick. An expired one-minute delivery claim is recoverable after restart. Delivery is at-least-once: HTTP acceptance followed by a crash can duplicate a message.

Configure `QUEUE_WATCHDOG_SLACK_WEBHOOK_URL` through the normal secret-management path. No real destination was configured or messaged during implementation. A missing destination is itself visible as degraded monitoring. An external host/scheduler heartbeat remains necessary: a stopped scheduler cannot alert on its own death.

## Bounded historical operations

Supply all creation-window and publication-cutoff timestamps in UTC.

Use `newsly-admin tasks artwork-backfill --help` or `newsly-admin tasks reconcile-archive --help`. Both are dry-run by default and accept at most 100 explicit IDs. Review the returned eligible IDs, then repeat the exact request with `--apply`. Every applied request requires a UUID batch ID, actor, and incident reason. Reusing an applied batch ID is a no-op; changing its parameters is rejected.

Artwork batches admit active users' inbox or saved content with summaries and no prior image task. Archived-only content, inactive users, and previously attempted work—including exhausted work—are excluded. A separate explicit retry policy is required for exhausted artifacts. Normal queue enqueue, readiness state, and audit commit together. The artwork migration retires ineligible segments but creates no historical tasks and leaves readable summaries intact.

Archive reconciliation also requires owner, feed configuration ID, a creation window, and a publication cutoff. Publication age only narrows the documented incident. It refuses saved/read content, another user's active inbox membership, completed content, or active related work outside the supplied IDs. Serializable selection and exact inventory comparison reject changed state. Cancellation clears the lease, archives only the accidental inbox membership, and retains content, read events, and the audit trail. Queue status remains `failed` with an explicit incident category.

## Verification and release

Run `bash scripts/test_ingestion_regressions.sh` with the normal local PostgreSQL environment. It checks exact test names before executing them, so a renamed/missing test cannot silently pass as zero tests. The canonical release gate invokes it before the full workspace suite.

The repeated-feed test uses Atom and RSS, real queue claims/finalization, an unchanged poll, a later new head, disabled configurations, preserved read/save/archive state, and exact child counts. Restoring `SkipKnown` for scheduled work made it fail with both archive URLs; restoring `StopAtKnown` removes that regression.

Focused tests also cover citation punctuation, fake-composer request counts across later jobs and changed input, usage idempotency and lease loss, early queue growth, durable alert retry/recovery and stale acknowledgement, fake HTTP 429/503/timeout, guarded incident replay, migration inventory, and real artwork file/readiness/fanout publication versus changed-summary rejection.

Before release, reconcile this checkout with the deployed hotfix ancestry and commit the reviewed changes. Run the full exact-SHA release gate with its required live smoke, deploy only when authorized, and verify the image SHA. Compare queue growth, source checks, historical intake, rejections, cancellations, and delivery across four source intervals and again after 24 hours. No provider concurrency increase is part of this repair.

Further coverage from the review remains useful: a complete discussion fetch/failure/later-input fake-provider lifecycle, feed identity changes beyond URL-based boundaries, and crash/concurrent-publisher fault injection. Existing discussion validation and backfill/fence tests remain in the suite; these extra scenarios are not claimed as newly implemented.
