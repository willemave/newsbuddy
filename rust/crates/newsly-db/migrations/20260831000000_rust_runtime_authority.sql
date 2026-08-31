-- Final, roll-forward-only authority transfer from the retired Python backend to Rust.
--
-- Existing Alembic databases reach this migration only through `newsly-db baseline` while the
-- deployment maintenance barrier is held. Fresh databases are also safe because no application
-- process can exist before the baseline has been installed. There is deliberately no down
-- migration: after Rust has accepted writes, restarting an older Python runtime would violate the
-- route, task, and VM fencing contracts.

DO $$
BEGIN
    -- A normally executed baseline row proves this database was created fresh by this SQLx
    -- graph. Adoption records that same audited baseline with `execution_time = -1`, so an
    -- existing database still requires the explicit maintenance-barrier session attestation.
    IF current_setting('newsly.maintenance_barrier_confirmed', true) IS DISTINCT FROM 'on'
       AND NOT EXISTS (
            SELECT 1
            FROM public._sqlx_migrations
            WHERE version = 20260830000000
              AND success
              AND execution_time >= 0
       )
    THEN
        RAISE EXCEPTION
            'Rust runtime authority cutover requires a confirmed maintenance barrier';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM runtime_ownership
        WHERE transition_state <> 'active'
           OR desired_owner IS NOT NULL
           OR desired_version IS NOT NULL
    ) THEN
        RAISE EXCEPTION
            'Rust runtime authority cutover cannot run during a prepared ownership transition';
    END IF;

    IF EXISTS (SELECT 1 FROM runtime_ownership_ack) THEN
        RAISE EXCEPTION
            'Rust runtime authority cutover requires ownership acknowledgements to be cleared';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM agent_vm_namespace_leases
        WHERE lease_expires_at > clock_timestamp()
    ) THEN
        RAISE EXCEPTION
            'Rust runtime authority cutover requires every VM namespace lease to be drained';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM processing_tasks AS task
        LEFT JOIN runtime_ownership AS ownership
          ON ownership.resource_kind = 'task_type'
         AND ownership.resource_key = task.task_type
        WHERE task.status IN ('pending', 'processing')
          AND ownership.resource_key IS NULL
    ) THEN
        RAISE EXCEPTION
            'Rust runtime authority cutover found active work with no registered task owner';
    END IF;
END;
$$;

-- Record the exact durable authority change before mutating the registry. A fixed migration batch
-- ID makes this event easy to identify and remains safe because SQLx applies the migration once.
INSERT INTO runtime_ownership_audit (
    batch_id,
    action,
    resource_kind,
    resource_key,
    old_owner,
    old_version,
    new_owner,
    new_version,
    application_sha,
    actor,
    reason
)
SELECT
    '52555354-4355-544f-5645-523230323630'::uuid,
    'promote',
    resource_kind,
    resource_key,
    active_owner,
    active_version,
    'rust',
    active_version + 1,
    repeat('0', 40),
    'sqlx-rust-authority-cutover',
    'Final Rust authority transfer under the SQLx adoption maintenance barrier'
FROM runtime_ownership
WHERE active_owner = 'python'
ORDER BY resource_kind, resource_key;

UPDATE runtime_ownership
SET
    active_owner = 'rust',
    active_version = active_version + 1,
    desired_owner = NULL,
    desired_version = NULL,
    transition_state = 'active',
    transition_started_at = NULL,
    updated_at = clock_timestamp(),
    updated_by = 'sqlx-rust-authority-cutover',
    reason = 'Rust is the sole production backend runtime'
WHERE active_owner = 'python';

-- Executor stamps are normally immutable. The maintenance-barrier migration is the sole exception:
-- active work is transferred in place so dedupe keys, retry generations, and workflow state remain
-- intact. Processing leases are expired, not cleared, so the ordinary Rust reclaim path performs
-- the same retry-generation handling as any other interrupted worker.
DROP TRIGGER processing_task_executor_immutable ON processing_tasks;

UPDATE processing_tasks AS task
SET
    executor_runtime = 'rust',
    executor_version = ownership.active_version,
    executor_namespace = task.task_type,
    lease_expires_at = CASE
        WHEN task.status = 'processing'
            THEN timezone('UTC', clock_timestamp())
        ELSE task.lease_expires_at
    END
FROM runtime_ownership AS ownership
WHERE ownership.resource_kind = 'task_type'
  AND ownership.resource_key = task.task_type
  AND ownership.active_owner = 'rust'
  AND task.executor_runtime = 'python'
  AND task.status IN ('pending', 'processing');

-- Refuse to commit a cutover that would strand even one live row behind a stale executor stamp.
-- Terminal rows retain their historical owner/version; only work a worker can still claim must
-- match the final registry exactly.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM processing_tasks AS task
        LEFT JOIN runtime_ownership AS ownership
          ON ownership.resource_kind = 'task_type'
         AND ownership.resource_key = task.task_type
        WHERE task.status IN ('pending', 'processing')
          AND (
              ownership.resource_key IS NULL
              OR task.executor_runtime <> ownership.active_owner
              OR task.executor_version <> ownership.active_version
              OR task.executor_namespace <> task.task_type
          )
    ) THEN
        RAISE EXCEPTION
            'Rust runtime authority cutover left active work with a stale executor stamp';
    END IF;
END;
$$;

CREATE TRIGGER processing_task_executor_immutable
BEFORE UPDATE OF executor_runtime, executor_version, executor_namespace
ON processing_tasks
FOR EACH ROW
EXECUTE FUNCTION reject_processing_task_executor_reassignment();

-- Every production enqueue path now supplies an ownership stamp loaded from runtime_ownership.
-- Removing legacy defaults makes an unstamped insert fail instead of silently creating work that
-- no runtime may safely claim.
ALTER TABLE processing_tasks
    ALTER COLUMN executor_runtime DROP DEFAULT,
    ALTER COLUMN executor_version DROP DEFAULT,
    ALTER COLUMN executor_namespace DROP DEFAULT;

COMMENT ON COLUMN processing_tasks.executor_runtime IS
    'Immutable runtime owner stamped explicitly from runtime_ownership when work is enqueued.';
COMMENT ON COLUMN processing_tasks.executor_version IS
    'Immutable ownership version stamped explicitly when work is enqueued.';
COMMENT ON COLUMN processing_tasks.executor_namespace IS
    'Immutable task ownership key stamped explicitly when work is enqueued.';
