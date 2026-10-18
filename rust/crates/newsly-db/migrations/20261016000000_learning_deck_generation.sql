-- One immutable generation budget per LLM job, shared by every automatic retry.
CREATE TABLE learning_deck_generation_state (
    llm_task_id integer PRIMARY KEY REFERENCES llm_tasks(id) ON DELETE CASCADE,
    input_fingerprint text NOT NULL CHECK (input_fingerprint ~ '^[0-9a-f]{64}$'),
    deadline_at timestamptz NOT NULL,
    draft_object_key text CHECK (length(draft_object_key) BETWEEN 1 AND 2048)
);
