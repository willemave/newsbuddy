//! Shared aggregator scheduling; user subscriptions never own global fetches.
use chrono::{DateTime, Utc};
use sqlx::PgConnection;

pub const AGGREGATOR_KEYS: [&str; 7] = [
    "brutalist",
    "finurls",
    "hackernews",
    "mediagazer",
    "memeorandum",
    "sciurls",
    "techmeme",
];

pub fn is_aggregator(key: &str) -> bool {
    AGGREGATOR_KEYS.contains(&key)
}

pub async fn aggregator_due(
    connection: &mut PgConnection,
    key: &str,
    now: DateTime<Utc>,
) -> Result<bool, sqlx::Error> {
    sqlx::query_scalar(
        r#"
        SELECT NOT EXISTS (
            SELECT 1 FROM source_ingestion_health h
            WHERE h.source_key = 'aggregator:' || $1
              AND h.last_attempt_at > $2 - CASE WHEN EXISTS (
                  SELECT 1 FROM user_scraper_configs c JOIN users u ON u.id = c.user_id
                  WHERE c.is_active AND u.is_active AND c.scraper_type = 'aggregator'
                    AND lower(btrim(c.config::jsonb ->> 'key')) = $1
              ) THEN interval '1 hour' ELSE interval '2 hours' END
        )
    "#,
    )
    .bind(key)
    .bind(now)
    .fetch_one(connection)
    .await
}
