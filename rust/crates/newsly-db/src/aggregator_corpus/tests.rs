use chrono::{DateTime, Duration, Utc};
use newsly_domain::AggregatorKey;
use sqlx::PgPool;

use super::load_aggregator_collected_activity;

async fn item(pool: &PgPool, key: &str, id: &str, collected: DateTime<Utc>, scope: &str) {
    sqlx::query(
        "INSERT INTO news_items(ingest_key, visibility_scope, platform, raw_metadata,
         article_url, ingested_at, created_at, published_at)
         VALUES ($1,$2,$3,$4,$5,$6,$6,NULL)",
    )
    .bind(format!("activity-{id}"))
    .bind(scope)
    .bind(key)
    .bind(serde_json::json!({"article": {"title": id}}))
    .bind(format!("https://example.com/{id}"))
    .bind(collected.naive_utc())
    .execute(pool)
    .await
    .unwrap();
}

#[sqlx::test(migrations = false)]
async fn activity_collection_counts_preserve_scope_windows_and_unknown_history(pool: PgPool) {
    crate::run_migrations(&pool).await.unwrap();
    let now = "2026-10-05T12:00:00Z".parse::<DateTime<Utc>>().unwrap();
    for (id, age) in [("latest", 0), ("cutoff", 90), ("year", 365), ("old", 366)] {
        item(&pool, "arxiv", id, now - Duration::days(age), "global").await;
    }
    item(&pool, "arxiv", "future", now + Duration::days(1), "global").await;
    item(&pool, "arxiv", "private", now, "user").await;
    item(&pool, "hfpapers", "other", now, "global").await;
    item(
        &pool,
        "techmeme",
        "old-only",
        now - Duration::days(500),
        "global",
    )
    .await;
    let rows = load_aggregator_collected_activity(
        &pool,
        &[
            AggregatorKey::Arxiv,
            AggregatorKey::Techmeme,
            AggregatorKey::HackerNews,
        ],
        now,
    )
    .await
    .unwrap();
    let arxiv = rows.iter().find(|row| row.key == "arxiv").unwrap();
    assert_eq!(arxiv.observed_count, Some(2));
    assert_eq!(arxiv.observed_year_count, Some(3));
    assert_eq!(arxiv.latest_title.as_deref(), Some("latest"));
    assert_eq!(
        arxiv.latest_url.as_deref(),
        Some("https://example.com/latest")
    );
    assert_eq!(arxiv.latest_collected_at, Some(now.naive_utc()));
    let old = rows.iter().find(|row| row.key == "techmeme").unwrap();
    assert_eq!(old.observed_count, Some(0));
    assert_eq!(old.observed_year_count, Some(0));
    assert!(old.latest_collected_at.is_some());
    let unknown = rows.iter().find(|row| row.key == "hackernews").unwrap();
    assert_eq!(unknown.observed_count, None);
    assert_eq!(unknown.observed_year_count, None);
    assert!(unknown.latest_collected_at.is_none());
}

#[sqlx::test(migrations = false)]
async fn aggregator_due_uses_subscriber_and_source_specific_cadence(pool: PgPool) {
    crate::run_migrations(&pool).await.unwrap();
    let now = "2026-10-17T12:00:00Z".parse::<DateTime<Utc>>().unwrap();
    let user_id: i64 = sqlx::query_scalar(
        "INSERT INTO users (apple_id, email, is_active, is_admin) VALUES ('cadence', 'cadence@example.com', true, false) RETURNING id::bigint",
    ).fetch_one(&pool).await.unwrap();
    for (key, active_interval, inactive_interval) in [
        ("hackernews", 10, 55),
        ("techmeme", 10, 55),
        ("arxiv", 55, 115),
        ("hfpapers", 55, 115),
    ] {
        let mut connection = pool.acquire().await.unwrap();
        assert!(
            super::aggregator_due(&mut connection, key, now)
                .await
                .unwrap()
        );
        drop(connection);
        let config_id: i64 = sqlx::query_scalar("INSERT INTO user_scraper_configs (user_id, scraper_type, config, is_active) VALUES ($1, 'aggregator', $2, true) RETURNING id::bigint")
            .bind(user_id).bind(serde_json::json!({"key":key})).fetch_one(&pool).await.unwrap();
        for (user_active, config_active, interval) in [
            (true, true, active_interval),
            (false, true, inactive_interval),
            (true, false, inactive_interval),
        ] {
            sqlx::query("UPDATE users SET is_active=$2 WHERE id::bigint=$1")
                .bind(user_id)
                .bind(user_active)
                .execute(&pool)
                .await
                .unwrap();
            sqlx::query("UPDATE user_scraper_configs SET is_active=$2 WHERE id::bigint=$1")
                .bind(config_id)
                .bind(config_active)
                .execute(&pool)
                .await
                .unwrap();
            for (age_seconds, expected) in [
                (interval * 60 - 1, false),
                (interval * 60, true),
                (interval * 60 + 1, true),
            ] {
                sqlx::query("INSERT INTO source_ingestion_health (source_key, last_attempt_at) VALUES ($1, $2) ON CONFLICT (source_key) DO UPDATE SET last_attempt_at=excluded.last_attempt_at")
                    .bind(format!("aggregator:{key}")).bind(now - Duration::seconds(age_seconds)).execute(&pool).await.unwrap();
                let mut connection = pool.acquire().await.unwrap();
                assert_eq!(
                    super::aggregator_due(&mut connection, key, now)
                        .await
                        .unwrap(),
                    expected,
                    "key={key}, user_active={user_active}, config_active={config_active}, age_seconds={age_seconds}"
                );
            }
        }
        sqlx::query("DELETE FROM user_scraper_configs WHERE id::bigint=$1")
            .bind(config_id)
            .execute(&pool)
            .await
            .unwrap();
        sqlx::query("UPDATE users SET is_active=true WHERE id::bigint=$1")
            .bind(user_id)
            .execute(&pool)
            .await
            .unwrap();
    }
}
