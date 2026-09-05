CREATE TABLE source_ingestion_health (
    source_key text PRIMARY KEY,
    config_id integer REFERENCES user_scraper_configs(id) ON DELETE CASCADE,
    last_attempt_at timestamptz NOT NULL,
    last_success_at timestamptz,
    last_new_item_at timestamptz,
    consecutive_failures integer NOT NULL DEFAULT 0,
    persisted_count bigint NOT NULL DEFAULT 0,
    new_count bigint NOT NULL DEFAULT 0,
    error_code text
);
CREATE INDEX source_ingestion_health_config ON source_ingestion_health(config_id);
