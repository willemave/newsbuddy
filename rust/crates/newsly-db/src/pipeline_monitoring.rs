//! Durable watchdog observations and at-least-once alert delivery. No product repair.
use sqlx::{PgConnection, PgPool};

pub async fn record_intake(
    connection: &mut PgConnection,
    observation: i64,
    historical: i64,
    bootstrap: bool,
) -> Result<(), sqlx::Error> {
    sqlx::query("UPDATE source_ingestion_observations SET historical_count = $2, bootstrap = $3 WHERE id = $1")
        .bind(observation).bind(historical).bind(bootstrap).execute(connection).await?;
    Ok(())
}

pub async fn observe_health(
    connection: &mut PgConnection,
    alert_threshold: i64,
) -> Result<crate::PipelineHealthCounts, sqlx::Error> {
    observe(connection).await?;
    let pipeline = crate::pipeline_health_counts(connection).await?;
    for (key, count) in [
        ("source_failures", pipeline.failing_sources),
        ("missing_source_checks", pipeline.missing_source_checks),
        ("overdue_tasks", pipeline.overdue_tasks),
        ("blocked_artwork", pipeline.blocked_artwork),
        (
            "terminal_product_mismatches",
            pipeline.terminal_product_mismatches,
        ),
    ] {
        set_signal(
            connection,
            key,
            count >= alert_threshold.max(1),
            &format!("{key}: {count}"),
        )
        .await?;
    }
    Ok(pipeline)
}

pub async fn observe(connection: &mut PgConnection) -> Result<(), sqlx::Error> {
    let historical: bool = sqlx::query_scalar("WITH ranked AS (SELECT *, row_number() OVER(PARTITION BY source_key ORDER BY id DESC) AS rank FROM source_ingestion_observations WHERE observed_at > now() - interval '45 minutes' AND source_key LIKE 'config:%') SELECT EXISTS(SELECT source_key FROM ranked WHERE rank <= 2 GROUP BY source_key HAVING count(*) = 2 AND bool_and(NOT bootstrap AND new_count >= 10 AND historical_count * 5 >= new_count * 4))")
        .fetch_one(&mut *connection).await?;
    set_signal(connection, "historical_intake", historical, "A scheduled source admitted at least 80% historical content in two successive polls; inspect provenance, do not delete by age").await?;
    let rejected: bool = sqlx::query_scalar("SELECT EXISTS(SELECT 1 FROM briefing_composition_attempts WHERE observed_at > now() - interval '30 minutes' AND outcome IN ('rejected','rejected_usage_unknown') GROUP BY user_id,fingerprint HAVING count(*) >= 3)")
        .fetch_one(&mut *connection).await?;
    set_signal(
        connection,
        "repeated_output_rejection",
        rejected,
        "Identical Briefing input exhausted three validation attempts in 30 minutes",
    )
    .await?;
    let stalled: bool = sqlx::query_scalar("SELECT EXISTS(SELECT 1 FROM processing_tasks ready WHERE ready.status = 'pending' AND ready.available_at < timezone('UTC',now()) - CASE WHEN ready.queue_name IN ('media','llm','image','audio_episode') THEN interval '2 hours' ELSE interval '15 minutes' END AND NOT EXISTS(SELECT 1 FROM processing_tasks done WHERE done.queue_name = ready.queue_name AND done.status = 'completed' AND done.completed_at > timezone('UTC',now()) - CASE WHEN ready.queue_name IN ('media','llm','image','audio_episode') THEN interval '2 hours' ELSE interval '15 minutes' END))")
        .fetch_one(&mut *connection).await?;
    set_signal(
        connection,
        "queue_no_progress",
        stalled,
        "Ready work exceeded its queue-specific progress window with no completions",
    )
    .await?;
    sqlx::query("INSERT INTO pipeline_queue_observations (ready_media, oldest_ready_seconds) SELECT count(*), COALESCE(EXTRACT(EPOCH FROM (timezone('UTC', now()) - min(available_at)))::bigint, 0) FROM processing_tasks WHERE queue_name = 'media' AND status = 'pending' AND available_at <= timezone('UTC', now()) ON CONFLICT DO NOTHING")
        .execute(&mut *connection).await?;
    let growing: bool = sqlx::query_scalar("WITH recent AS (SELECT *, lag(ready_media) OVER (ORDER BY observed_at) AS previous FROM (SELECT * FROM pipeline_queue_observations WHERE observed_at > now() - interval '16 minutes' ORDER BY observed_at DESC LIMIT 3) s) SELECT count(*) = 3 AND count(*) FILTER (WHERE ready_media > previous) = 2 AND max(ready_media) >= 100 AND max(oldest_ready_seconds) > 900 FROM recent")
        .fetch_one(&mut *connection).await?;
    set_signal(connection, "media_growth", growing, "Ready media queue grew in two successive watchdog intervals; at least 100 jobs and oldest over 15 minutes").await?;
    sqlx::query(
        "DELETE FROM pipeline_queue_observations WHERE observed_at < now() - interval '7 days'",
    )
    .execute(&mut *connection)
    .await?;
    sqlx::query(
        "DELETE FROM source_ingestion_observations WHERE observed_at < now() - interval '7 days'",
    )
    .execute(&mut *connection)
    .await?;
    Ok(())
}

pub async fn set_signal(
    connection: &mut PgConnection,
    key: &str,
    active: bool,
    message: &str,
) -> Result<(), sqlx::Error> {
    sqlx::query("INSERT INTO pipeline_alerts (alert_key, active, message, delivered_revision) VALUES ($1, $2, $3, CASE WHEN $2 THEN 0 ELSE 1 END) ON CONFLICT (alert_key) DO UPDATE SET active = EXCLUDED.active, message = EXCLUDED.message, revision = pipeline_alerts.revision + 1, next_attempt_at = now(), updated_at = now() WHERE pipeline_alerts.active IS DISTINCT FROM EXCLUDED.active OR (EXCLUDED.active AND pipeline_alerts.delivered_revision = pipeline_alerts.revision AND pipeline_alerts.last_delivered_at < now() - interval '6 hours')")
        .bind(key).bind(active).bind(message).execute(connection).await?;
    Ok(())
}

#[derive(Debug, sqlx::FromRow)]
pub struct AlertDelivery {
    pub alert_key: String,
    pub revision: i64,
    pub active: bool,
    pub message: String,
}

/// Claim for one minute, then retry after crashes. HTTP acceptance followed by a crash may duplicate delivery.
pub async fn claim_alert(pool: &PgPool) -> Result<Option<AlertDelivery>, sqlx::Error> {
    sqlx::query_as("UPDATE pipeline_alerts SET next_attempt_at = now() + interval '1 minute' WHERE alert_key = (SELECT alert_key FROM pipeline_alerts WHERE delivered_revision < revision AND next_attempt_at <= now() ORDER BY next_attempt_at FOR UPDATE SKIP LOCKED LIMIT 1) RETURNING alert_key, revision, active, message")
        .fetch_optional(pool).await
}

pub async fn settle_alert(
    pool: &PgPool,
    alert: &AlertDelivery,
    error: Option<&str>,
) -> Result<(), sqlx::Error> {
    sqlx::query("UPDATE pipeline_alerts SET delivered_revision = CASE WHEN $3::text IS NULL THEN $2 ELSE delivered_revision END, last_delivered_at = CASE WHEN $3::text IS NULL THEN now() ELSE last_delivered_at END, last_error = $3, next_attempt_at = now() + interval '5 minutes' WHERE alert_key = $1 AND revision = $2")
        .bind(&alert.alert_key).bind(alert.revision).bind(error).execute(pool).await?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[sqlx::test]
    async fn missing_first_source_check_is_not_a_healthy_empty_check(pool: PgPool) {
        crate::run_migrations(&pool).await.unwrap();
        let user: i64 = sqlx::query_scalar("INSERT INTO users(apple_id,email,is_active,is_admin) VALUES('missing-source','missing-source@example.com',true,false) RETURNING id::bigint").fetch_one(&pool).await.unwrap();
        let config: i64 = sqlx::query_scalar("INSERT INTO user_scraper_configs(user_id,scraper_type,feed_url,config,is_active,created_at,updated_at) VALUES($1::bigint::integer,'atom','https://example.com/feed','{}',true,now() - interval '1 hour',now() - interval '1 hour') RETURNING id::bigint").bind(user).fetch_one(&pool).await.unwrap();
        let mut tx = pool.begin().await.unwrap();
        assert_eq!(
            crate::pipeline_health_counts(&mut tx)
                .await
                .unwrap()
                .missing_source_checks,
            1
        );
        crate::record_source_health(
            &mut tx,
            &format!("config:{config}"),
            Some(config),
            0,
            0,
            None,
        )
        .await
        .unwrap();
        assert_eq!(
            crate::pipeline_health_counts(&mut tx)
                .await
                .unwrap()
                .missing_source_checks,
            0
        );
    }

    #[sqlx::test]
    async fn historical_intake_requires_two_polls_and_recovers_without_product_mutation(
        pool: PgPool,
    ) {
        crate::run_migrations(&pool).await.unwrap();
        let mut tx = pool.begin().await.unwrap();
        for _ in 0..2 {
            let id = crate::record_source_health(&mut tx, "config:test", None, 10, 10, None)
                .await
                .unwrap();
            record_intake(&mut tx, id, 8, false).await.unwrap();
        }
        observe(&mut tx).await.unwrap();
        let active: bool = sqlx::query_scalar(
            "SELECT active FROM pipeline_alerts WHERE alert_key = 'historical_intake'",
        )
        .fetch_one(&mut *tx)
        .await
        .unwrap();
        assert!(active);
        crate::record_source_health(&mut tx, "config:test", None, 0, 0, None)
            .await
            .unwrap();
        observe(&mut tx).await.unwrap();
        let active: bool = sqlx::query_scalar(
            "SELECT active FROM pipeline_alerts WHERE alert_key = 'historical_intake'",
        )
        .fetch_one(&mut *tx)
        .await
        .unwrap();
        assert!(!active);
        let contents: i64 = sqlx::query_scalar("SELECT count(*) FROM contents")
            .fetch_one(&mut *tx)
            .await
            .unwrap();
        assert_eq!(contents, 0);
    }

    #[sqlx::test]
    async fn durable_alert_deduplicates_retries_and_recovers(pool: PgPool) {
        crate::run_migrations(&pool).await.unwrap();
        let mut connection = pool.acquire().await.unwrap();
        set_signal(&mut connection, "test", true, "broken")
            .await
            .unwrap();
        let first = claim_alert(&pool).await.unwrap().unwrap();
        assert!(claim_alert(&pool).await.unwrap().is_none());
        settle_alert(&pool, &first, Some("http_429")).await.unwrap();
        set_signal(&mut connection, "test", true, "still broken")
            .await
            .unwrap();
        assert!(
            claim_alert(&pool).await.unwrap().is_none(),
            "unchanged signal respects backoff"
        );
        sqlx::query("UPDATE pipeline_alerts SET next_attempt_at = now()")
            .execute(&pool)
            .await
            .unwrap();
        let retry = claim_alert(&pool).await.unwrap().unwrap();
        assert_eq!(retry.revision, first.revision);
        settle_alert(&pool, &retry, None).await.unwrap();
        set_signal(&mut connection, "test", true, "same")
            .await
            .unwrap();
        assert!(claim_alert(&pool).await.unwrap().is_none());
        set_signal(&mut connection, "test", false, "healthy")
            .await
            .unwrap();
        let recovery = claim_alert(&pool).await.unwrap().unwrap();
        assert!(!recovery.active);
        assert_eq!(recovery.revision, first.revision + 1);
        settle_alert(&pool, &first, None).await.unwrap();
        let remaining: i64 = sqlx::query_scalar(
            "SELECT count(*) FROM pipeline_alerts WHERE delivered_revision < revision",
        )
        .fetch_one(&pool)
        .await
        .unwrap();
        assert_eq!(
            remaining, 1,
            "stale acknowledgement cannot consume recovery"
        );
        settle_alert(&pool, &recovery, None).await.unwrap();
        assert!(claim_alert(&pool).await.unwrap().is_none());
    }

    #[sqlx::test]
    async fn growing_ready_media_is_detected_before_two_hours(pool: PgPool) {
        crate::run_migrations(&pool).await.unwrap();
        sqlx::query("INSERT INTO pipeline_queue_observations VALUES (now() - interval '10 minutes', 100, 1200), (now() - interval '5 minutes', 150, 1500)").execute(&pool).await.unwrap();
        sqlx::query("INSERT INTO processing_tasks(task_type,queue_name,status,available_at,executor_runtime,executor_version,executor_namespace) SELECT 'process_podcast_media','media','pending',timezone('UTC',now()) - interval '30 minutes','rust',1,'process_podcast_media' FROM generate_series(1,200)").execute(&pool).await.unwrap();
        let mut connection = pool.acquire().await.unwrap();
        observe(&mut connection).await.unwrap();
        let health = crate::pipeline_health_counts(&mut connection)
            .await
            .unwrap();
        assert_eq!(health.growing_media_backlog, 1);
        assert_eq!(health.overdue_tasks, 0);
    }
}
