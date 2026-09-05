CREATE TABLE artifact_cleanup_candidates (
    object_key text PRIMARY KEY,
    llm_task_id bigint,
    processing_task_id bigint,
    kind text NOT NULL DEFAULT 'deck',
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX artifact_cleanup_age ON artifact_cleanup_candidates(created_at);
