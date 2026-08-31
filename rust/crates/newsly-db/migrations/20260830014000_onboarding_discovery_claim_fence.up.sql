ALTER TABLE onboarding_discovery_runs
    ADD COLUMN discovery_task_id integer,
    ADD COLUMN discovery_retry_count integer;

ALTER TABLE onboarding_discovery_runs
    ADD CONSTRAINT ck_onboarding_discovery_retry_count_nonnegative
    CHECK (discovery_retry_count IS NULL OR discovery_retry_count >= 0);

CREATE INDEX idx_onboarding_discovery_runs_task_claim
    ON onboarding_discovery_runs (discovery_task_id, discovery_retry_count)
    WHERE discovery_task_id IS NOT NULL;

ALTER TABLE feed_discovery_runs
    ADD COLUMN discovery_task_id integer,
    ADD COLUMN discovery_retry_count integer;

ALTER TABLE feed_discovery_runs
    ADD CONSTRAINT ck_feed_discovery_retry_count_nonnegative
    CHECK (discovery_retry_count IS NULL OR discovery_retry_count >= 0);

CREATE INDEX idx_feed_discovery_runs_task_claim
    ON feed_discovery_runs (discovery_task_id, discovery_retry_count)
    WHERE discovery_task_id IS NOT NULL;
