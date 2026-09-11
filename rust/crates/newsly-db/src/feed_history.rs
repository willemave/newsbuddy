use chrono::{DateTime, Utc};
use serde_json::Value;
use sqlx::{FromRow, PgPool};

use crate::scraper_stats::{ConfigIndex, load_content_rows};
use crate::{ScraperConfigProjection, ScraperStatsRepositoryError};

#[derive(Debug, FromRow)]
pub struct FeedHistoryProjection {
    pub id: i64,
    pub title: String,
    pub content_type: String,
    pub status: String,
    pub stage: Option<String>,
    pub processed_at: Option<DateTime<Utc>>,
    pub publication_at: Option<DateTime<Utc>>,
    pub metadata: Value,
    pub source_char_count: Option<i32>,
}

/// Uses the same user-visible content and unambiguous feed attribution as source totals.
/// Archived and read memberships remain part of processing history.
///
/// # Errors
/// Returns an error when a database read fails.
pub async fn load_feed_history(
    pool: &PgPool,
    user_id: i64,
    configs: &[ScraperConfigProjection],
    config_id: i64,
    filter: &str,
    offset: i64,
    limit: i64,
) -> Result<Vec<FeedHistoryProjection>, ScraperStatsRepositoryError> {
    let index = ConfigIndex::new(configs);
    let ids = load_content_rows(pool, user_id)
        .await?
        .iter()
        .filter(|content| index.match_content(content) == Some(config_id))
        .map(|content| content.id)
        .collect::<Vec<_>>();
    Ok(sqlx::query_as::<_, FeedHistoryProjection>(r"
        WITH history AS (
            SELECT content.id::bigint AS id,
                   COALESCE(NULLIF(content.title, ''), 'Untitled item') AS title,
                   content.content_type,
                   CASE
                     WHEN content.classification = 'skip' THEN 'skipped'
                     WHEN content.status = 'completed' THEN 'completed'
                     WHEN content.status IN ('new', 'pending', 'processing', 'awaiting_image') AND task.status = 'processing' AND task.lease_expires_at > timezone('UTC', now()) THEN 'running'
                     WHEN content.status IN ('new', 'pending', 'processing', 'awaiting_image') AND task.status IN ('pending', 'processing') THEN 'queued'
                     WHEN content.status = 'failed' OR task.status = 'failed' THEN 'failed'
                     WHEN task.status = 'cancelled' THEN 'cancelled'
                     ELSE 'waiting'
                   END AS status,
                   CASE WHEN task.status IN ('pending', 'processing') THEN task.task_type END AS stage,
                   CASE WHEN content.status = 'completed' AND content.classification IS DISTINCT FROM 'skip'
                        THEN content.processed_at AT TIME ZONE 'UTC' END AS processed_at,
                   content.publication_date AT TIME ZONE 'UTC' AS publication_at,
                   jsonb_build_object('duration_seconds', content.content_metadata -> 'duration_seconds') AS metadata,
                   (SELECT char_count FROM content_bodies WHERE content_id = content.id AND variant = 'source') AS source_char_count,
                   content.created_at
            FROM contents AS content
            LEFT JOIN LATERAL (
                SELECT status, task_type, lease_expires_at
                FROM processing_tasks
                WHERE content_id = content.id
                  AND task_type IN ('process_content', 'process_podcast_media', 'summarize', 'generate_image')
                ORDER BY CASE WHEN status = 'processing' AND lease_expires_at > timezone('UTC', now()) THEN 0
                              WHEN status IN ('pending', 'processing') THEN 1 ELSE 2 END,
                         id DESC
                LIMIT 1
            ) AS task ON true
            WHERE content.id::bigint = ANY($1)
              AND EXISTS (SELECT 1 FROM content_status WHERE content_id = content.id AND user_id::bigint = $2)
        )
        SELECT id, title, content_type, status, stage, processed_at, publication_at, metadata, source_char_count
        FROM history
        WHERE $3 = 'all' OR status = $3
           OR ($3 = 'active' AND status IN ('running', 'queued'))
           OR ($3 = 'failed' AND status IN ('failed', 'cancelled'))
        ORDER BY COALESCE(processed_at, created_at AT TIME ZONE 'UTC') DESC, id DESC
        LIMIT $4 OFFSET $5
    ")
    .bind(ids).bind(user_id).bind(filter).bind(limit).bind(offset)
    .fetch_all(pool).await?)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{NewScraperConfig, create_scraper_config, get_scraper_config_stats};
    use serde_json::json;

    #[sqlx::test]
    async fn history_includes_archives_and_keeps_activity_and_totals_consistent(pool: PgPool) {
        let user = sqlx::query_scalar::<_, i64>("INSERT INTO users (apple_id, email, is_admin, is_active) VALUES ('history-owner', 'owner@example.com', false, true) RETURNING id::bigint")
            .fetch_one(&pool).await.unwrap();
        let other_user = sqlx::query_scalar::<_, i64>("INSERT INTO users (apple_id, email, is_admin, is_active) VALUES ('history-other', 'other@example.com', false, true) RETURNING id::bigint")
            .fetch_one(&pool).await.unwrap();
        let mut transaction = pool.begin().await.unwrap();
        let config = create_scraper_config(
            &mut transaction,
            &NewScraperConfig {
                user_id: user,
                scraper_type: "podcast_rss",
                display_name: Some("Test feed"),
                feed_url: "https://example.com/feed",
                config: &json!({"feed_url": "https://example.com/feed"}),
                is_active: true,
            },
        )
        .await
        .unwrap();
        transaction.commit().await.unwrap();
        let configs = vec![config.clone()];
        let mut ids = Vec::new();
        for (index, status, membership, task_status) in [
            (0, "completed", "archived", None),
            (1, "completed", "inbox", None),
            (2, "processing", "inbox", Some("pending")),
            (3, "processing", "inbox", Some("processing")),
            (4, "failed", "inbox", Some("failed")),
            (5, "pending", "archived", Some("cancelled")),
            (6, "processing", "inbox", Some("failed")),
        ] {
            let id = sqlx::query_scalar::<_, i64>("INSERT INTO contents (content_type, url, title, status, is_aggregate, content_metadata, processed_at) VALUES ('podcast', $1, $2, $3, false, $4, '2026-09-01 12:00:00') RETURNING id::bigint")
                .bind(format!("https://example.com/{index}"))
                .bind(format!("Episode {index}"))
                .bind(status).bind(if index == 4 {
                    json!({"feed_config_id": config.id, "duration_seconds": 1200, "processing": {"extraction_error_code": "access_gate"}})
                } else {
                    json!({"feed_config_id": config.id, "duration_seconds": 1200})
                })
                .fetch_one(&pool).await.unwrap();
            sqlx::query(
                "INSERT INTO content_status (user_id, content_id, status) VALUES ($1, $2, $3)",
            )
            .bind(user)
            .bind(id)
            .bind(membership)
            .execute(&pool)
            .await
            .unwrap();
            if let Some(task_status) = task_status {
                sqlx::query("INSERT INTO processing_tasks (content_id, task_type, status, lease_expires_at, executor_runtime, executor_version, executor_namespace) VALUES ($1, 'process_podcast_media', $2, timezone('UTC', now()) + interval '1 hour', 'rust', 1, 'process_podcast_media')")
                    .bind(id).bind(task_status).execute(&pool).await.unwrap();
            }
            ids.push(id);
        }
        let stats = get_scraper_config_stats(&pool, user, &configs)
            .await
            .unwrap();
        let stats = &stats[&config.id];
        assert_eq!(
            (
                stats.completed_count,
                stats.unread_count,
                stats.failed_count,
                stats.access_gate_count,
                stats.running_count,
                stats.queued_count
            ),
            (2, 1, 2, 1, 1, 1)
        );
        sqlx::query("INSERT INTO processing_tasks (content_id, task_type, status, executor_runtime, executor_version, executor_namespace) VALUES ($1, 'process_content', 'failed', 'rust', 1, 'process_content')")
            .bind(ids[1]).execute(&pool).await.unwrap();
        let stats = get_scraper_config_stats(&pool, user, &configs)
            .await
            .unwrap();
        assert_eq!(stats[&config.id].failed_count, 2);
        let first = load_feed_history(&pool, user, &configs, config.id, "completed", 0, 1)
            .await
            .unwrap();
        let second = load_feed_history(&pool, user, &configs, config.id, "completed", 1, 1)
            .await
            .unwrap();
        assert_eq!(first[0].id, ids[1]);
        assert_eq!(second[0].id, ids[0]);
        assert!(
            load_feed_history(&pool, other_user, &configs, config.id, "all", 0, 30)
                .await
                .unwrap()
                .is_empty()
        );
        let active = load_feed_history(&pool, user, &configs, config.id, "active", 0, 30)
            .await
            .unwrap();
        assert_eq!(active.len(), 2);
        assert_eq!(
            active
                .iter()
                .filter(|item| item.status == "running")
                .count(),
            1
        );
        assert!(active.iter().all(|item| item.processed_at.is_none()));
        let issues = load_feed_history(&pool, user, &configs, config.id, "failed", 0, 30)
            .await
            .unwrap();
        assert_eq!(issues.len(), 3);
        // A second unrelated task must not double-count an item or become its visible stage.
        sqlx::query("INSERT INTO processing_tasks (content_id, task_type, status, executor_runtime, executor_version, executor_namespace) VALUES ($1, 'dig_deeper', 'pending', 'rust', 1, 'dig_deeper')")
            .bind(ids[3]).execute(&pool).await.unwrap();
        sqlx::query("UPDATE processing_tasks SET lease_expires_at = timezone('UTC', now()) - interval '1 minute' WHERE content_id::bigint = $1")
            .bind(ids[3]).execute(&pool).await.unwrap();
        let stats = get_scraper_config_stats(&pool, user, &configs)
            .await
            .unwrap();
        assert_eq!(
            (
                stats[&config.id].running_count,
                stats[&config.id].queued_count
            ),
            (0, 2)
        );
        let active = load_feed_history(&pool, user, &configs, config.id, "active", 0, 30)
            .await
            .unwrap();
        assert!(active.iter().all(|item| item.status == "queued"
            && item.stage.as_deref() == Some("process_podcast_media")));
    }
}
