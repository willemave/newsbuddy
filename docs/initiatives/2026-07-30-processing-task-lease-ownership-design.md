# Processing Task Lease Ownership

## Goal

Make the PostgreSQL processing queue safe under concurrent cold starts, lease expiry, worker
restarts, and overlapping deploys. Queue coordination must not depend on SQLAlchemy ORM session
synchronization or on positional adaptation of `UPDATE ... RETURNING` rows.

## Decision

Keep PostgreSQL as the queue and add a narrow SQLAlchemy Core repository for claim, lease renewal,
and finalization. The repository operates on `ProcessingTask.__table__` through short
`Engine.begin()` transactions, uses PostgreSQL's UTC transaction timestamp for lease decisions,
and returns validated internal models instead of raw row mappings.

Enqueue behavior, queue metrics, retention, and task handlers remain in their current owners. They
do not participate in lease ownership and are outside this boundary.

## Claim Contract

Every successful claim writes and returns:

- the stable diagnostic worker name in `locked_by`
- a new UUID in `lease_token` that identifies this exact claim attempt
- `locked_at` and `lease_expires_at`
- the complete immutable `ClaimedTask` ownership record needed by the processor

The `lease_token` column is nullable so historical and in-flight rows can pass through a rolling
deployment. Reclaiming an expired row always replaces the token. A database check constraint keeps
that compatibility narrow: tokenless rows remain valid for old workers, while a non-null token is
valid only on a `processing` row with `locked_at`, `locked_by`, and `lease_expires_at` set.

The processor retains the exact `ClaimedTask` for lease operations and finalization. It derives a
smaller handler-only `TaskEnvelope`, so optional presentation fields cannot weaken ownership proof.
Non-object JSON payloads are rejected instead of silently becoming empty tasks.

## Transition Contract

Lease renewal and finalization are compare-and-set updates. They match the task id, `processing`
status, worker name, lease token, and an unexpired lease. A stale worker therefore cannot renew,
complete, fail, defer, or retry work after another claim attempt owns the row, even if both process
instances use the same stable worker name.

Finalization remains one atomic update:

- success moves to `completed`
- an eligible retry moves to `pending` and increments `retry_count`
- deferral moves to `pending` without consuming retry budget
- a terminal failure moves to `failed`

Every outcome clears all lease fields. A rejected compare-and-set is reported as lost ownership;
the processor does not overwrite the current row.

`QueueService.finalize_task(claim, result, max_retries=...)` is the single policy boundary. It
decides the outcome, default backoff, retry count behavior, and fallback error exactly once, then
passes the typed claim plus the resolved outcome, error, and delay to the repository. The typed
`TaskTransition` returned by the repository is preserved through processor logging and return
handling.

The heartbeat retries SQLAlchemy database failures while the lease can still be renewed. A false
renewal, exhausted lease window, or unexpected heartbeat failure marks ownership lost; the
processor then refuses to finalize the handler result. Handlers that need an explicit lease check
call the claim-bound `TaskContext.renew_current_lease()` callback and never receive lease fields.

## Compatibility And Cleanup

The production processor passes the immutable claim directly into finalization. The old
unconditional `complete_task` followed by `retry_task` compatibility path is removed from the
production queue service and gateway so there is only one lifecycle write path. Watchdog, queue
control, and admin recovery paths use the same canonical lease-clearing helper.

## Migration And Rollout

The first migration adds nullable PostgreSQL UUID column `processing_tasks.lease_token`. A second
forward migration releases every inconsistent tokenized row, requeues malformed `processing` rows
so they cannot become permanently unclaimable, and adds the database check constraint. This
remains compatible with old code because tokenless claims are allowed. Once new code assigns a
token, old finalization SQL that changes status without clearing that token is rejected by
PostgreSQL. New finalization and every recovery path clear all lease fields atomically.

## Verification

Add coverage for:

- concurrent cold-cache claims returning correctly typed, correctly aligned task data
- unique task claims across worker threads
- lease renewal by the current token only
- heartbeat retry and observable ownership loss without unhandled daemon-thread exceptions
- expired-lease rejection
- stale finalization after the same worker name reclaims with a new token
- atomic success, terminal failure, retry, and deferral transitions
- database rejection of a tokenized old-style finalization
- full lease clearing in watchdog and queue-control recovery
- migration upgrade, downgrade, and re-upgrade

Run focused queue and processor tests with unhandled thread warnings promoted to errors, followed
by Ruff, format, type checking for touched modules, migration validation, and `git diff --check`.
