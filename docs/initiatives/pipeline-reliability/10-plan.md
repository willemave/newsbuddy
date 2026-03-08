# Pipeline Reliability Plan (A + B + C)

## Scope
This plan implements three reliability workstreams in the current codebase:

- **A. Deploy integrity + drift prevention**
- **B. Automated queue recovery watchdog + admin observability**
- **C. Retry fairness + summarize transient retry classification**

This phase intentionally avoids schema migrations. Schema-backed hard guarantees (heartbeat leases, active-task unique indexes, dead-letter queues) are deferred to a follow-up phase.

## Problem Summary
Recent incidents showed systemic reliability gaps:

- Deploy path allowed runtime drift (failed shell step, missing worker group assumptions, no strict post-deploy health gate).
- Queue recovery depended on manual interventions (`move-transcribe`, stale requeue).
- Retry behavior could strand work under load due to strict retry-count-first dequeue ordering.
- Summarize treated transient provider failures as terminal in too many cases.
- Recovery actions were not surfaced as a first-class operational signal in admin.

## Goals

- Guarantee required worker groups are active after deploy.
- Make queue recovery automatic on a 5-minute cadence.
- Make watchdog actions observable in admin and EventLog.
- Reduce retry starvation risk in dequeue ordering.
- Convert transient summarize errors into retryable task outcomes.

## Non-Goals

- No schema changes in this phase.
- No new external job orchestrator dependency; watchdog should run with existing deployment patterns (cron/supervisor loop).
- No broad redesign of all task handlers; changes are limited to critical reliability paths.

## Workstream A: Deploy Integrity

### Deliverables

- Update `scripts/deploy/push_app.sh` defaults so `news_app_workers_transcribe` is always part of managed programs.
- Add a strict post-deploy supervisor validation gate that fails deploy if:
  - required groups are missing, or
  - required groups are present but not `RUNNING`, or
  - expected programs are missing from supervisor status output.
- Keep checks explicit for: `news_app_server`, `news_app_workers_content`, `news_app_workers_transcribe`, `news_app_workers_onboarding`, `news_app_workers_chat`.

### Acceptance Criteria

- Deploy with missing transcribe group fails post-check.
- Deploy with group present but not running fails post-check.
- Healthy deploy passes with clear status output.

### Risks / Mitigation

- **Risk:** temporary startup lag causes false failures.
- **Mitigation:** bounded wait loop before hard fail, with final status snapshot.

## Workstream B: Automated Watchdog + Dashboard Tracking

### Deliverables

- New watchdog script that executes every 5 minutes (one-shot mode + loop mode):
  - move transcribe tasks to transcribe queue
  - requeue stale transcribe processing tasks
  - requeue stale process_content processing tasks
- Configurable thresholds via env/CLI for stale hours and alert threshold.
- Event logging for every watchdog run and each action (counts + metadata).
- Optional Slack alert when touched-task count crosses threshold.
- Admin dashboard readouts for watchdog:
  - runs in 24h
  - tasks touched in 24h
  - alerts sent/failed/skipped
  - recent action history and per-action totals

### Scheduling Model

- Primary: run under supervisor in loop mode with `--interval-seconds 300`.
- Alternative: cron running one-shot every 5 minutes.

### Acceptance Criteria

- Watchdog run produces EventLog entries even when no tasks are touched.
- When stale or misrouted tasks exist, watchdog mutates state and logs touched counts.
- Admin dashboard shows watchdog KPIs and recent watchdog actions.

### Risks / Mitigation

- **Risk:** noisy alerts.
- **Mitigation:** thresholded alerts and summary payloads.
- **Risk:** accidental over-requeue.
- **Mitigation:** strict task-type filters and stale-time cutoffs.

## Workstream C: Retry Fairness + Summarize Retryability

### Deliverables

- Replace strict `retry_count -> created_at` dequeue priority with **retry-bucket rotation** to avoid starvation.
- Keep scheduled retry delay semantics intact (`created_at <= now` gate stays).
- Add summarize transient error classifier and return `TaskResult.fail(retryable=True)` for transient provider/network/timeout/rate-limit errors.
- Preserve terminal handling for non-retryable summarize errors.

### Transient Classifier Rules (initial)

Retryable when error indicates one of:

- timeout / timed out
- rate limiting (`429`, `rate limit`, `too many requests`)
- upstream service unavailable / overloaded (`5xx`, `bad gateway`, `gateway timeout`, `temporarily unavailable`)
- transient network failures (`connection reset/refused/aborted`)
- provider precondition-style temporary failures (`precondition`, `resource exhausted`)

### Acceptance Criteria

- Under mixed retry buckets, dequeue rotates across available buckets instead of permanently preferring the lowest retry count.
- Summarize timeout/rate-limit style failures are retried by queue processor.
- Non-retryable summarize failures remain terminal and persist failure metadata.

### Risks / Mitigation

- **Risk:** classifier false positives.
- **Mitigation:** token set is conservative and can be tuned via logs.

## Files Planned

- `scripts/deploy/push_app.sh`
- `scripts/watchdog_queue_recovery.py` (new)
- `app/routers/admin.py`
- `templates/admin_dashboard.html`
- `app/services/queue.py`
- `app/pipeline/handlers/summarize.py`
- tests:
  - `app/tests/services/test_queue_service.py`
  - `app/tests/pipeline/test_summarize_task_routing.py`
  - `app/tests/routers/test_admin_dashboard_readouts.py`

## Validation Plan

- Lint touched Python files with `ruff check`.
- Run targeted tests for changed behaviors.
- Manual smoke checks:
  - `python scripts/watchdog_queue_recovery.py --dry-run`
  - `python scripts/watchdog_queue_recovery.py`
  - admin dashboard shows watchdog section.

## Rollout Sequence

1. Merge code and deploy with post-check enabled.
2. Enable watchdog under supervisor or cron at 5-minute cadence.
3. Observe dashboard + logs for 24h.
4. Tune stale thresholds/alert thresholds based on real queue behavior.

## Rollback Strategy

- Watchdog can be disabled by stopping its supervisor/cron entry.
- Deploy post-check can be bypassed by not using `--restart-supervisor` in emergencies.
- Queue dequeue/summarize logic is isolated and can be reverted file-by-file.

## Follow-Up (Schema Phase)

- Worker heartbeat + task lease expiry model.
- Partial unique index for active tasks to prevent duplicate active rows.
- Dead-letter/quarantine status with targeted replay tooling.
