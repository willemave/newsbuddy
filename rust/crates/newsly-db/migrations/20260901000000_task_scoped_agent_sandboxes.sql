-- Hard cutover from persistent per-user VM replicas to disposable task sandboxes.
--
-- This migration is intentionally destructive and roll-forward-only. The removed mirror and VM
-- state are derived; rollback requires the pre-cutover database backup and application image.

DELETE FROM processing_tasks
WHERE task_type IN (
    'sync_agent_data',
    'index_agent_data',
    'backfill_agent_data',
    'reconcile_agent_data'
)
  AND status IN ('pending', 'processing', 'failed');

DELETE FROM runtime_ownership
WHERE (resource_kind = 'task_type' AND resource_key IN (
    'sync_agent_data',
    'index_agent_data',
    'backfill_agent_data',
    'reconcile_agent_data'
))
   OR resource_kind = 'vm_namespace';

DROP TABLE IF EXISTS agent_vm_namespace_leases;
DROP TABLE IF EXISTS agent_vm_system_state;
DROP TABLE IF EXISTS agent_data_files;

DROP INDEX IF EXISTS ix_llm_tasks_vm_namespace;

ALTER TABLE llm_tasks
    DROP COLUMN IF EXISTS vm_namespace,
    DROP COLUMN IF EXISTS shared_workspace_path,
    ADD COLUMN IF NOT EXISTS sandbox_cleanup_required BOOLEAN NOT NULL DEFAULT FALSE;

CREATE INDEX IF NOT EXISTS ix_llm_tasks_sandbox_cleanup_required
    ON llm_tasks (updated_at, id)
    WHERE sandbox_cleanup_required = TRUE AND sandbox_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_llm_tasks_terminal_sandbox_cleanup
    ON llm_tasks (updated_at, id)
    WHERE sandbox_id IS NOT NULL AND status IN ('completed', 'failed', 'cancelled');

ALTER TABLE users
    DROP COLUMN IF EXISTS agent_vm_sandbox_id,
    DROP COLUMN IF EXISTS agent_vm_template_revision,
    DROP COLUMN IF EXISTS agent_vm_snapshot_id,
    DROP COLUMN IF EXISTS agent_vm_snapshot_template_revision,
    DROP COLUMN IF EXISTS agent_data_revision;
