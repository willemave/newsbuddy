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
- the complete typed task envelope needed by the processor

The `lease_token` column is nullable so historical and in-flight rows can pass through a rolling
deployment. Reclaiming an expired row always replaces the token. No existing task data needs to be
rewritten.

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

## Compatibility And Cleanup

The production processor passes claim ownership directly into finalization. The old unconditional
`complete_task` followed by `retry_task` compatibility path is removed from the production queue
service and gateway so there is only one lifecycle write path. Test doubles may continue to model
the finalization result directly.

## Migration And Rollout

Add nullable PostgreSQL UUID column `processing_tasks.lease_token`. The migration is additive and
does not block old code from finishing existing claims. Rows claimed by the new code receive a
token; old `processing` rows become eligible for a tokenized claim when their current lease
expires.

## Verification

Add coverage for:

- concurrent cold-cache claims returning correctly typed, correctly aligned task data
- unique task claims across worker threads
- lease renewal by the current token only
- expired-lease rejection
- stale finalization after the same worker name reclaims with a new token
- atomic success, terminal failure, retry, and deferral transitions
- migration upgrade, downgrade, and re-upgrade

Run focused queue and processor tests with unhandled thread warnings promoted to errors, followed
by Ruff, format, type checking for touched modules, migration validation, and `git diff --check`.
