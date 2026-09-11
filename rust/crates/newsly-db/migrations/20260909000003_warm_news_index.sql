-- no-transaction
CREATE INDEX CONCURRENTLY ix_processing_tasks_priority_claim
    ON processing_tasks(executor_runtime, queue_name, retry_count, priority DESC, available_at, created_at, id)
    WHERE status='pending';
