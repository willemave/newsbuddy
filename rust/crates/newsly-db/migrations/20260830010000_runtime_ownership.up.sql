CREATE TABLE runtime_ownership (
    resource_kind VARCHAR(32) NOT NULL,
    resource_key VARCHAR(255) NOT NULL,
    active_owner VARCHAR(16) NOT NULL,
    active_version BIGINT NOT NULL,
    desired_owner VARCHAR(16),
    desired_version BIGINT,
    transition_state VARCHAR(16) NOT NULL DEFAULT 'active',
    transition_started_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_by TEXT NOT NULL,
    reason TEXT NOT NULL,
    PRIMARY KEY (resource_kind, resource_key),
    CONSTRAINT ck_runtime_ownership_resource_kind CHECK (
        resource_kind IN ('route_group', 'task_type', 'vm_namespace', 'state_writer')
    ),
    CONSTRAINT ck_runtime_ownership_active_owner CHECK (active_owner IN ('python', 'rust')),
    CONSTRAINT ck_runtime_ownership_desired_owner CHECK (
        desired_owner IS NULL OR desired_owner IN ('python', 'rust')
    ),
    CONSTRAINT ck_runtime_ownership_active_version CHECK (active_version > 0),
    CONSTRAINT ck_runtime_ownership_transition_state CHECK (
        transition_state IN ('active', 'preparing')
    ),
    CONSTRAINT ck_runtime_ownership_transition_shape CHECK (
        (
            transition_state = 'active'
            AND desired_owner IS NULL
            AND desired_version IS NULL
            AND transition_started_at IS NULL
        )
        OR
        (
            transition_state = 'preparing'
            AND desired_owner IS NOT NULL
            AND desired_owner <> active_owner
            AND desired_version = active_version + 1
            AND transition_started_at IS NOT NULL
        )
    ),
    CONSTRAINT ck_runtime_ownership_updated_by_nonempty CHECK (BTRIM(updated_by) <> ''),
    CONSTRAINT ck_runtime_ownership_reason_nonempty CHECK (BTRIM(reason) <> '')
);

CREATE TABLE runtime_ownership_ack (
    resource_kind VARCHAR(32) NOT NULL,
    resource_key VARCHAR(255) NOT NULL,
    desired_version BIGINT NOT NULL,
    replica_id VARCHAR(255) NOT NULL,
    readiness_state VARCHAR(32) NOT NULL,
    application_sha VARCHAR(64) NOT NULL,
    acknowledged_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (resource_kind, resource_key, desired_version, replica_id),
    CONSTRAINT fk_runtime_ownership_ack_resource
        FOREIGN KEY (resource_kind, resource_key)
        REFERENCES runtime_ownership (resource_kind, resource_key)
        ON DELETE CASCADE,
    CONSTRAINT ck_runtime_ownership_ack_version CHECK (desired_version > 0),
    CONSTRAINT ck_runtime_ownership_ack_replica_nonempty CHECK (BTRIM(replica_id) <> ''),
    CONSTRAINT ck_runtime_ownership_ack_readiness CHECK (
        readiness_state IN ('loaded', 'write_barrier', 'ready')
    ),
    CONSTRAINT ck_runtime_ownership_ack_sha CHECK (
        application_sha ~ '^[0-9A-Fa-f]{40}([0-9A-Fa-f]{24})?$'
    )
);

CREATE TABLE runtime_ownership_audit (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    batch_id UUID NOT NULL,
    action VARCHAR(32) NOT NULL,
    resource_kind VARCHAR(32) NOT NULL,
    resource_key VARCHAR(255) NOT NULL,
    old_owner VARCHAR(16) NOT NULL,
    old_version BIGINT NOT NULL,
    new_owner VARCHAR(16) NOT NULL,
    new_version BIGINT NOT NULL,
    application_sha VARCHAR(64) NOT NULL,
    actor TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_runtime_ownership_audit_action CHECK (
        action IN ('created', 'prepare', 'rollback_prepare', 'promote', 'rollback', 'acks_cleared')
    ),
    CONSTRAINT ck_runtime_ownership_audit_owner CHECK (
        old_owner IN ('python', 'rust') AND new_owner IN ('python', 'rust')
    ),
    CONSTRAINT ck_runtime_ownership_audit_version CHECK (
        old_version > 0 AND new_version > 0
    ),
    CONSTRAINT ck_runtime_ownership_audit_sha CHECK (
        application_sha ~ '^[0-9A-Fa-f]{40}([0-9A-Fa-f]{24})?$'
    ),
    CONSTRAINT ck_runtime_ownership_audit_actor_nonempty CHECK (BTRIM(actor) <> ''),
    CONSTRAINT ck_runtime_ownership_audit_reason_nonempty CHECK (BTRIM(reason) <> '')
);

CREATE INDEX ix_runtime_ownership_audit_resource_created
    ON runtime_ownership_audit (resource_kind, resource_key, created_at DESC, id DESC);

CREATE FUNCTION reject_runtime_ownership_audit_mutation()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'runtime ownership audit rows are append-only';
END;
$$;

CREATE TRIGGER runtime_ownership_audit_append_only
BEFORE UPDATE OR DELETE
ON runtime_ownership_audit
FOR EACH ROW
EXECUTE FUNCTION reject_runtime_ownership_audit_mutation();

INSERT INTO runtime_ownership (
    resource_kind,
    resource_key,
    active_owner,
    active_version,
    updated_by,
    reason
)
SELECT
    'task_type',
    task_type,
    'python',
    1,
    'sqlx-baseline',
    'Initial Python task ownership at Rust coexistence cutover'
FROM (
    VALUES
        ('analyze_url'),
        ('backfill_agent_data'),
        ('backfill_feeds'),
        ('briefing_refresh'),
        ('chat_turn'),
        ('delete_user_account'),
        ('dig_deeper'),
        ('discover_feeds'),
        ('download_tweet_video_audio'),
        ('enrich_news_item_article'),
        ('fetch_news_item_discussion'),
        ('generate_audio_episode'),
        ('generate_image'),
        ('index_agent_data'),
        ('onboarding_discover'),
        ('process_content'),
        ('process_news_item'),
        ('process_podcast_media'),
        ('reconcile_agent_data'),
        ('run_llm_task'),
        ('scrape'),
        ('summarize'),
        ('sync_agent_data'),
        ('sync_integration'),
        ('transcribe_tweet_video')
) AS registered_tasks(task_type);

INSERT INTO runtime_ownership (
    resource_kind,
    resource_key,
    active_owner,
    active_version,
    updated_by,
    reason
)
VALUES
    (
        'vm_namespace',
        'user:*',
        'python',
        1,
        'sqlx-baseline',
        'Initial Python user VM namespace ownership at Rust coexistence cutover'
    ),
    (
        'vm_namespace',
        'user:0',
        'python',
        1,
        'sqlx-baseline',
        'Initial Python system VM namespace ownership at Rust coexistence cutover'
    );

ALTER TABLE processing_tasks
    ADD COLUMN executor_runtime VARCHAR(16) NOT NULL DEFAULT 'python',
    ADD COLUMN executor_version BIGINT NOT NULL DEFAULT 1,
    ADD COLUMN executor_namespace VARCHAR(255) NOT NULL DEFAULT 'legacy:python',
    ADD CONSTRAINT ck_processing_tasks_executor_runtime
        CHECK (executor_runtime IN ('python', 'rust')),
    ADD CONSTRAINT ck_processing_tasks_executor_version
        CHECK (executor_version > 0),
    ADD CONSTRAINT ck_processing_tasks_executor_namespace_nonempty
        CHECK (BTRIM(executor_namespace) <> '');

UPDATE processing_tasks
SET executor_namespace = task_type
WHERE executor_namespace = 'legacy:python';

CREATE INDEX idx_task_executor_queue_status_available
    ON processing_tasks (
        executor_runtime,
        queue_name,
        status,
        retry_count,
        available_at,
        created_at,
        id
    );

CREATE FUNCTION reject_processing_task_executor_reassignment()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.executor_runtime IS DISTINCT FROM OLD.executor_runtime
        OR NEW.executor_version IS DISTINCT FROM OLD.executor_version
        OR NEW.executor_namespace IS DISTINCT FROM OLD.executor_namespace
    THEN
        RAISE EXCEPTION 'processing task executor ownership is immutable';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER processing_task_executor_immutable
BEFORE UPDATE OF executor_runtime, executor_version, executor_namespace
ON processing_tasks
FOR EACH ROW
EXECUTE FUNCTION reject_processing_task_executor_reassignment();
