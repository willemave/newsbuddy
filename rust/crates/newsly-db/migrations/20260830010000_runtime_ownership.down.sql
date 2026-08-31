DROP TRIGGER IF EXISTS processing_task_executor_immutable ON processing_tasks;
DROP FUNCTION IF EXISTS reject_processing_task_executor_reassignment();

DROP TRIGGER IF EXISTS runtime_ownership_audit_append_only ON runtime_ownership_audit;
DROP FUNCTION IF EXISTS reject_runtime_ownership_audit_mutation();

DROP INDEX IF EXISTS idx_task_executor_queue_status_available;

ALTER TABLE processing_tasks
    DROP CONSTRAINT IF EXISTS ck_processing_tasks_executor_namespace_nonempty,
    DROP CONSTRAINT IF EXISTS ck_processing_tasks_executor_version,
    DROP CONSTRAINT IF EXISTS ck_processing_tasks_executor_runtime,
    DROP COLUMN IF EXISTS executor_namespace,
    DROP COLUMN IF EXISTS executor_version,
    DROP COLUMN IF EXISTS executor_runtime;

DROP INDEX IF EXISTS ix_runtime_ownership_audit_resource_created;
DROP TABLE IF EXISTS runtime_ownership_audit;
DROP TABLE IF EXISTS runtime_ownership_ack;
DROP TABLE IF EXISTS runtime_ownership;
