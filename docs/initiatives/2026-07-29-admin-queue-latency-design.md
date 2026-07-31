# Admin Queue Latency Metrics

## Goal

Extend the existing admin Queue Health section with rolling operational latency metrics so an
operator can distinguish worker congestion, intentional scheduling or retry delay, and task
execution time.

## Scope

Use the existing `processing_tasks` timestamps over a fixed rolling 24-hour window. Group
terminal tasks by queue and task type and report:

- ready wait p50 and p95: `started_at - available_at`
- total wait p50 and p95: `started_at - created_at`
- run time p50 and p95: `completed_at - started_at`
- terminal-task sample count

Completed and failed tasks are both included. Rows without all required timestamps are excluded
because they cannot produce comparable latency samples. Negative durations are clamped to zero to
protect the operator readout from inconsistent historical timestamps.

## Semantics

Ready wait measures how long a task waited after it became eligible to run. Total wait includes
scheduled deferrals and retry backoff. Run time measures only the final attempt because retry and
deferral transitions clear `started_at`; the dashboard must state this limitation explicitly.

## Implementation

Add one grouped PostgreSQL percentile query to `app/queries/queue_health.py` and expose its typed
rows through `QueueHealthSnapshot`. Format the values in `app/admin_web/dashboard.py` and render a
latency table inside the existing Queue Health section. This requires no migration, worker change,
or new telemetry store.

## Verification

Add query tests with deterministic timestamp samples that prove the percentile values and exclude
incomplete or out-of-window rows. Extend the admin dashboard test to prove the table, labels, and
final-attempt explanation render. Run focused pytest and Ruff checks for the touched files.

## Deferred Work

Accurate per-attempt and full retry-lifecycle metrics require an append-only task-attempt table and
worker transition instrumentation. Historical charts and configurable windows are also separate
follow-ups.
