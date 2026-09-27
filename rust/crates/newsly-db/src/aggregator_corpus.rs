//! Shared aggregator scheduling; user subscriptions never own global fetches.
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

pub async fn aggregator_due(connection: &mut PgConnection, key: &str) -> Result<bool, sqlx::Error> {
    sqlx::query_scalar(
        r#"
        SELECT NOT EXISTS (
            SELECT 1 FROM source_ingestion_health h
            WHERE h.source_key = 'aggregator:' || $1
              AND h.last_attempt_at > timezone('UTC', now()) - CASE WHEN EXISTS (
                  SELECT 1 FROM user_scraper_configs c JOIN users u ON u.id = c.user_id
                  WHERE c.is_active AND u.is_active AND c.scraper_type = 'aggregator'
                    AND lower(btrim(c.config::jsonb ->> 'key')) = $1
              ) THEN interval '10 minutes' ELSE interval '55 minutes' END
        )
    "#,
    )
    .bind(key)
    .fetch_one(connection)
    .await
}
