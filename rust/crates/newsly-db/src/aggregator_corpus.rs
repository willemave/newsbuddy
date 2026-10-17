//! Shared aggregator scheduling; user subscriptions never own global fetches.
use chrono::{DateTime, Duration, NaiveDateTime, Utc};
use newsly_domain::AggregatorKey;
use sqlx::{PgConnection, PgPool};

#[derive(Debug, sqlx::FromRow)]
pub struct AggregatorCollectedActivity {
    pub key: String,
    pub observed_count: Option<i64>,
    pub observed_year_count: Option<i64>,
    pub latest_title: Option<String>,
    pub latest_url: Option<String>,
    pub latest_collected_at: Option<NaiveDateTime>,
}

/// Global collected items are available before subscribing. These are collection counts,
/// not publisher totals or processing completions; clustered items retain source identity.
pub async fn load_aggregator_collected_activity(
    pool: &PgPool,
    keys: &[AggregatorKey],
    checked_at: DateTime<Utc>,
) -> Result<Vec<AggregatorCollectedActivity>, sqlx::Error> {
    let keys = keys.iter().map(|key| key.as_str()).collect::<Vec<_>>();
    sqlx::query_as(
        r#"
        WITH counts AS (
            SELECT lower(platform) AS key,
                   count(*) FILTER (WHERE ingested_at >= $3) AS quarter_count,
                   count(*) AS year_count
            FROM news_items
            WHERE visibility_scope = 'global' AND owner_user_id IS NULL
              AND lower(platform) = ANY($1::text[])
              AND ingested_at >= $4 AND ingested_at <= $2
            GROUP BY lower(platform)
        )
        SELECT requested.key,
               CASE WHEN latest.id IS NOT NULL THEN COALESCE(counts.quarter_count, 0) END AS observed_count,
               CASE WHEN latest.id IS NOT NULL THEN COALESCE(counts.year_count, 0) END AS observed_year_count,
               COALESCE(latest.raw_metadata::jsonb #>> '{article,title}',
                        latest.raw_metadata::jsonb #>> '{aggregator,title}',
                        latest.raw_metadata::jsonb #>> '{summary,title}') AS latest_title,
               COALESCE(latest.article_url, latest.canonical_story_url, latest.canonical_item_url) AS latest_url,
               latest.ingested_at AS latest_collected_at
        FROM unnest($1::text[]) AS requested(key)
        LEFT JOIN counts USING (key)
        LEFT JOIN LATERAL (
            SELECT id, raw_metadata, article_url, canonical_story_url, canonical_item_url, ingested_at
            FROM news_items
            WHERE visibility_scope = 'global' AND owner_user_id IS NULL
              AND lower(platform) = requested.key AND ingested_at <= $2
            ORDER BY ingested_at DESC, id DESC LIMIT 1
        ) latest ON true
        "#,
    )
    .bind(&keys)
    .bind(checked_at.naive_utc())
    .bind((checked_at - Duration::days(90)).naive_utc())
    .bind((checked_at - Duration::days(365)).naive_utc())
    .fetch_all(pool)
    .await
}

#[cfg(test)]
#[path = "aggregator_corpus/tests.rs"]
mod tests;

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
                    AND c.config::jsonb ->> 'key' = $1
              ) THEN CASE WHEN $1 IN ('arxiv','hfpapers') THEN interval '55 minutes' ELSE interval '10 minutes' END
                ELSE CASE WHEN $1 IN ('arxiv','hfpapers') THEN interval '115 minutes' ELSE interval '55 minutes' END END
        )
    "#,
    )
    .bind(key)
    .bind(now)
    .fetch_one(connection)
    .await
}

/// The canonical visibility predicate owns both topic selection and cross-listed stories.
pub async fn visible_active_subscriber(
    connection: &mut PgConnection,
    platform: &str,
    raw_metadata: &serde_json::Value,
) -> Result<bool, sqlx::Error> {
    sqlx::query_scalar(
        "SELECT EXISTS(SELECT 1 FROM user_scraper_configs config JOIN users subscriber ON subscriber.id=config.user_id AND subscriber.is_active WHERE config.is_active AND config.scraper_type='aggregator' AND aggregator_config_admits(config.config::jsonb,$1,$2::json))"
    ).bind(platform).bind(raw_metadata).fetch_one(connection).await
}

/// Shared durable marker for a free extraction whose paid fallback had no audience.
pub const FIRECRAWL_NO_DEMAND_ERROR_CODE: &str = "fallback_not_permitted";

/// Serializes audience transitions with denied-fallback publication and topic expansion.
/// Call only in a short transaction and before locking story rows.
pub async fn lock_aggregator_demand(connection: &mut PgConnection) -> Result<(), sqlx::Error> {
    sqlx::query("SELECT pg_advisory_xact_lock(hashtextextended('newsly:aggregator-demand', 0))")
        .execute(connection)
        .await?;
    Ok(())
}
