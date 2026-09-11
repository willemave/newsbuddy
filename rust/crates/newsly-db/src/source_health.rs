use sqlx::{Postgres, Transaction};

pub async fn record_source_health(
    tx: &mut Transaction<'_, Postgres>,
    source_key: &str,
    config_id: Option<i64>,
    persisted: i64,
    new_items: i64,
    error_code: Option<&str>,
) -> Result<i64, sqlx::Error> {
    let observation = sqlx::query_scalar("INSERT INTO source_ingestion_observations (source_key, new_count, error_code) VALUES ($1, $2, $3) RETURNING id")
        .bind(source_key).bind(new_items).bind(error_code).fetch_one(&mut **tx).await?;
    sqlx::query(
        r"
        INSERT INTO source_ingestion_health (
            source_key, config_id, last_attempt_at, last_success_at, last_new_item_at,
            consecutive_failures, persisted_count, new_count, error_code
        ) VALUES (
            $1, $2::bigint::integer, now(),
            CASE WHEN $5::text IS NULL THEN now() END,
            CASE WHEN $4 > 0 THEN now() END,
            CASE WHEN $5::text IS NULL THEN 0 ELSE 1 END,
            $3, $4, $5
        )
        ON CONFLICT (source_key) DO UPDATE
        SET last_attempt_at = now(),
            last_success_at = COALESCE(EXCLUDED.last_success_at, source_ingestion_health.last_success_at),
            last_new_item_at = COALESCE(EXCLUDED.last_new_item_at, source_ingestion_health.last_new_item_at),
            consecutive_failures = CASE WHEN EXCLUDED.error_code IS NULL THEN 0 ELSE source_ingestion_health.consecutive_failures + 1 END,
            persisted_count = EXCLUDED.persisted_count,
            new_count = EXCLUDED.new_count,
            error_code = EXCLUDED.error_code
        ",
    )
    .bind(source_key)
    .bind(config_id)
    .bind(persisted)
    .bind(new_items)
    .bind(error_code)
    .execute(&mut **tx)
    .await?;
    Ok(observation)
}

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, serde::Serialize, sqlx::FromRow)]
pub struct PipelineHealthCounts {
    pub failing_sources: i64,
    pub overdue_tasks: i64,
    pub terminal_product_mismatches: i64,
    pub missing_source_checks: i64,
    pub blocked_artwork: i64,
    pub growing_media_backlog: i64,
    pub undelivered_alerts: i64,
    pub other_active_alerts: i64,
    pub intentional_cancellations: i64,
}

impl PipelineHealthCounts {
    #[must_use]
    pub fn total(self) -> usize {
        usize::try_from(
            self.failing_sources
                + self.overdue_tasks
                + self.terminal_product_mismatches
                + self.missing_source_checks
                + self.blocked_artwork
                + self.growing_media_backlog
                + self.other_active_alerts,
        )
        .unwrap_or(usize::MAX)
    }
}

/// Read-only detection: repairs remain owned by their normal fenced task handlers.
pub async fn pipeline_health_counts(
    connection: &mut sqlx::PgConnection,
) -> Result<PipelineHealthCounts, sqlx::Error> {
    sqlx::query_as(r#"
        SELECT
          (SELECT count(*) FROM pipeline_alerts WHERE active AND alert_key IN ('historical_intake','repeated_output_rejection','queue_no_progress','alert_destination_missing')) AS other_active_alerts,
          (SELECT count(*) FROM processing_tasks WHERE status = 'failed' AND (error_message LIKE 'incident_cancelled:%' OR error_message = 'incident_2026_09_05_archival_feed_import_cancelled')) AS intentional_cancellations,
          (SELECT count(*) FROM user_scraper_configs c JOIN users u ON u.id = c.user_id
           WHERE c.is_active AND u.is_active AND c.scraper_type IN ('atom', 'rss', 'podcast_rss', 'substack', 'reddit', 'aggregator')
             AND COALESCE(c.updated_at,c.created_at) < timezone('UTC', now()) - interval '45 minutes'
             AND NOT EXISTS (SELECT 1 FROM source_ingestion_health h WHERE (h.config_id = c.id OR (c.scraper_type='aggregator' AND h.source_key='aggregator:' || lower(btrim(c.config::jsonb->>'key'))))
                 AND h.last_attempt_at >= timezone('UTC', now()) - CASE WHEN c.scraper_type='aggregator' THEN interval '90 minutes' ELSE interval '45 minutes' END)) AS missing_source_checks,
          (SELECT count(*) FROM contents c WHERE c.status = 'awaiting_image'
             AND c.updated_at < timezone('UTC', now()) - interval '30 minutes'
             AND NOT EXISTS (SELECT 1 FROM processing_tasks t WHERE t.content_id = c.id
                 AND t.task_type = 'generate_image' AND t.status IN ('pending', 'processing'))) AS blocked_artwork,
          (SELECT count(*) FROM pipeline_alerts WHERE alert_key = 'media_growth' AND active) AS growing_media_backlog,
          (SELECT count(*) FROM pipeline_alerts WHERE delivered_revision < revision) AS undelivered_alerts,
          (SELECT count(*) FROM source_ingestion_health AS health
           LEFT JOIN user_scraper_configs AS config ON config.id = health.config_id
           WHERE consecutive_failures >= 3 AND (health.config_id IS NULL OR config.is_active IS TRUE)) AS failing_sources,
          (SELECT count(*) FROM processing_tasks WHERE
            (status = 'pending' AND available_at < timezone('UTC', now()) - interval '2 hours') OR
            (status = 'processing' AND lease_expires_at < timezone('UTC', now()) - interval '2 hours')) AS overdue_tasks,
          (SELECT count(*) FROM llm_tasks AS product WHERE product.status NOT IN ('completed', 'failed', 'cancelled')
            AND (SELECT task.status FROM processing_tasks AS task WHERE task.task_type = 'run_llm_task' AND task.payload ->> 'llm_task_id' = product.id::text AND task.owner_user_id = product.user_id ORDER BY task.id DESC LIMIT 1) = 'failed'
            AND NOT EXISTS (SELECT 1 FROM processing_tasks AS task WHERE task.task_type = 'run_llm_task' AND task.payload ->> 'llm_task_id' = product.id::text AND task.owner_user_id = product.user_id AND task.status IN ('pending', 'processing')))
          + (SELECT count(*) FROM contents AS product WHERE product.status IN ('new', 'pending', 'processing', 'awaiting_image')
            AND (SELECT task.status FROM processing_tasks AS task WHERE task.content_id = product.id AND task.task_type IN ('analyze_url','process_content','summarize','process_podcast_media','download_tweet_video_audio','transcribe_tweet_video') ORDER BY task.id DESC LIMIT 1) = 'failed'
            AND NOT EXISTS (SELECT 1 FROM processing_tasks AS task WHERE task.content_id = product.id AND task.status IN ('pending', 'processing'))) AS terminal_product_mismatches
    "#).fetch_one(connection).await
}
