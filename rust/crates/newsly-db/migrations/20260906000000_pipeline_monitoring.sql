CREATE TABLE pipeline_queue_observations (
    observed_at timestamptz PRIMARY KEY DEFAULT now(),
    ready_media bigint NOT NULL,
    oldest_ready_seconds bigint NOT NULL
);
CREATE TABLE pipeline_alerts (
    alert_key text PRIMARY KEY,
    active boolean NOT NULL,
    revision bigint NOT NULL DEFAULT 1,
    delivered_revision bigint NOT NULL DEFAULT 0,
    message text NOT NULL,
    next_attempt_at timestamptz NOT NULL DEFAULT now(),
    last_delivered_at timestamptz,
    last_error text,
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE source_ingestion_observations (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_key text NOT NULL,
    observed_at timestamptz NOT NULL DEFAULT now(),
    new_count bigint NOT NULL,
    historical_count bigint NOT NULL DEFAULT 0,
    bootstrap boolean NOT NULL DEFAULT false,
    error_code text
);
CREATE INDEX source_ingestion_observations_source_time ON source_ingestion_observations(source_key, observed_at DESC);
