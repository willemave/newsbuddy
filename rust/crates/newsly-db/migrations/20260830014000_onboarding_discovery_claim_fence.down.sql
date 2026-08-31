DROP INDEX IF EXISTS idx_onboarding_discovery_runs_task_claim;

DROP INDEX IF EXISTS idx_feed_discovery_runs_task_claim;

ALTER TABLE feed_discovery_runs
    DROP CONSTRAINT IF EXISTS ck_feed_discovery_retry_count_nonnegative,
    DROP COLUMN IF EXISTS discovery_retry_count,
    DROP COLUMN IF EXISTS discovery_task_id;

ALTER TABLE onboarding_discovery_runs
    DROP CONSTRAINT IF EXISTS ck_onboarding_discovery_retry_count_nonnegative,
    DROP COLUMN IF EXISTS discovery_retry_count,
    DROP COLUMN IF EXISTS discovery_task_id;
