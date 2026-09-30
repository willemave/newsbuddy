-- Durable per-user local-night scheduling and candidate state for news-category maintenance.
CREATE TABLE user_news_category_schedule (
    user_id integer PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    timezone text,
    timezone_revision bigint NOT NULL DEFAULT 0,
    next_due_at timestamp without time zone,
    next_local_date date,
    next_window_start_at timestamp without time zone,
    next_window_end_at timestamp without time zone,
    last_published_at timestamp without time zone,
    last_published_local_date date,
    updated_at timestamp without time zone NOT NULL DEFAULT timezone('UTC', now()),
    CONSTRAINT ck_user_news_category_timezone_nonempty
        CHECK (timezone IS NULL OR btrim(timezone) <> ''),
    CONSTRAINT ck_user_news_category_timezone_revision CHECK (timezone_revision >= 0),
    CONSTRAINT ck_user_news_category_due_shape CHECK (
        (timezone IS NULL AND next_due_at IS NULL AND next_local_date IS NULL
            AND next_window_start_at IS NULL AND next_window_end_at IS NULL)
        OR
        (timezone IS NOT NULL AND next_due_at IS NOT NULL AND next_local_date IS NOT NULL
            AND next_window_start_at IS NOT NULL AND next_window_end_at IS NOT NULL
            AND next_window_start_at <= next_due_at AND next_due_at < next_window_end_at)
    )
);

CREATE INDEX ix_user_news_category_schedule_next_due
    ON user_news_category_schedule (next_due_at, user_id)
    WHERE timezone IS NOT NULL;

CREATE TABLE news_category_maintenance_runs (
    id bigserial PRIMARY KEY,
    user_id integer NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    local_date date NOT NULL,
    timezone text NOT NULL,
    timezone_revision bigint NOT NULL,
    scheduled_at timestamp without time zone NOT NULL,
    window_start_at timestamp without time zone NOT NULL,
    window_end_at timestamp without time zone NOT NULL,
    mode text NOT NULL DEFAULT 'shadow',
    status text NOT NULL DEFAULT 'scheduled',
    task_id integer REFERENCES processing_tasks(id) ON DELETE SET NULL,
    naming_attempts integer NOT NULL DEFAULT 0,
    naming_reserved_tokens bigint NOT NULL DEFAULT 0,
    output jsonb NOT NULL DEFAULT '{}'::jsonb,
    started_at timestamp without time zone,
    completed_at timestamp without time zone,
    created_at timestamp without time zone NOT NULL DEFAULT timezone('UTC', now()),
    updated_at timestamp without time zone NOT NULL DEFAULT timezone('UTC', now()),
    CONSTRAINT uq_news_category_maintenance_user_date UNIQUE (user_id, local_date),
    CONSTRAINT uq_news_category_maintenance_task UNIQUE (task_id),
    CONSTRAINT ck_news_category_maintenance_window CHECK (
        window_start_at <= scheduled_at AND scheduled_at < window_end_at
    ),
    CONSTRAINT ck_news_category_maintenance_mode CHECK (mode IN ('shadow', 'publish')),
    CONSTRAINT ck_news_category_maintenance_status CHECK (
        status IN ('scheduled', 'running', 'no_op', 'shadowed', 'published', 'failed', 'skipped_window')
    ),
    CONSTRAINT ck_news_category_naming_attempts CHECK (naming_attempts BETWEEN 0 AND 2),
    CONSTRAINT ck_news_category_naming_tokens CHECK (naming_reserved_tokens >= 0)
);

CREATE INDEX ix_news_category_maintenance_runs_status
    ON news_category_maintenance_runs (status, scheduled_at, id);

CREATE TABLE news_category_candidates (
    user_id integer PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    input_hash text NOT NULL,
    candidate jsonb NOT NULL,
    naming_result jsonb,
    updated_at timestamp without time zone NOT NULL DEFAULT timezone('UTC', now()),
    CONSTRAINT ck_news_category_candidate_input_hash CHECK (length(input_hash) = 64)
);

CREATE TABLE news_category_naming_budget (
    utc_date date PRIMARY KEY,
    reserved_tokens bigint NOT NULL DEFAULT 0,
    updated_at timestamp without time zone NOT NULL DEFAULT timezone('UTC', now()),
    CONSTRAINT ck_news_category_naming_budget_tokens CHECK (reserved_tokens >= 0)
);

CREATE TABLE news_category_naming_attempts (
    attempt_id uuid PRIMARY KEY,
    run_id bigint NOT NULL REFERENCES news_category_maintenance_runs(id) ON DELETE CASCADE,
    input_hash text NOT NULL,
    outcome text NOT NULL,
    result jsonb,
    created_at timestamp without time zone NOT NULL DEFAULT timezone('UTC', now()),
    CONSTRAINT ck_news_category_naming_attempt_hash CHECK (length(input_hash) = 64),
    CONSTRAINT ck_news_category_naming_attempt_outcome CHECK (
        outcome IN ('succeeded', 'failed')
    )
);

CREATE INDEX ix_news_category_naming_attempts_cache
    ON news_category_naming_attempts (input_hash, created_at DESC, attempt_id)
    WHERE outcome='succeeded';

CREATE FUNCTION reject_news_category_naming_attempt_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'news_category_naming_attempts is append-only';
END;
$$;

CREATE TRIGGER news_category_naming_attempts_append_only
BEFORE UPDATE ON news_category_naming_attempts
FOR EACH ROW EXECUTE FUNCTION reject_news_category_naming_attempt_mutation();

ALTER TABLE briefing_lenses
    ADD COLUMN accepts_news boolean NOT NULL DEFAULT true;

COMMENT ON COLUMN briefing_lenses.accepts_news IS
    'Whether future news may route to this lens; false lenses remain visible while unread segments drain.';

INSERT INTO runtime_ownership (
    resource_kind, resource_key, active_owner, active_version, updated_by, reason
) VALUES (
    'task_type', 'recluster_news_lenses', 'rust', 1,
    'migration:20260927010000', 'Nightly user-scoped news-category maintenance'
);
