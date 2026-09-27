-- Durable E2B lifecycle intent. The intent exists before the control-plane create call so a
-- worker crash cannot erase the fact that a sandbox may have been created and billed.

CREATE TABLE task_sandbox_sessions (
    id uuid PRIMARY KEY,
    llm_task_id integer NOT NULL REFERENCES llm_tasks(id) ON DELETE CASCADE,
    user_id integer NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    feature character varying(100) NOT NULL,
    template_id character varying(255) NOT NULL,
    template_revision character varying(255) NOT NULL,
    timeout_seconds integer NOT NULL CHECK (timeout_seconds > 0),
    requested_at timestamp without time zone NOT NULL,
    sandbox_id character varying(255) UNIQUE,
    provider_started_at timestamp with time zone,
    provider_end_at timestamp with time zone,
    cpu_count integer CHECK (cpu_count IS NULL OR cpu_count > 0),
    memory_mb integer CHECK (memory_mb IS NULL OR memory_mb > 0),
    cleanup_required boolean NOT NULL DEFAULT FALSE,
    ended_at timestamp without time zone,
    end_basis character varying(32),
    created_at timestamp without time zone NOT NULL DEFAULT timezone('UTC', clock_timestamp()),
    updated_at timestamp without time zone NOT NULL DEFAULT timezone('UTC', clock_timestamp()),
    CONSTRAINT task_sandbox_sessions_end_state CHECK (
        (ended_at IS NULL AND end_basis IS NULL)
        OR (ended_at IS NOT NULL AND end_basis IS NOT NULL)
    )
);

CREATE INDEX ix_task_sandbox_sessions_open
    ON task_sandbox_sessions (requested_at, id)
    WHERE ended_at IS NULL;

CREATE INDEX ix_task_sandbox_sessions_user
    ON task_sandbox_sessions (user_id, requested_at);
