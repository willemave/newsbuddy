use chrono::{DateTime, Utc};
use newsly_queue::{EnqueueRequest, TaskType};
use serde_json::{Map, Value};
use sqlx::{Postgres, Transaction};

use super::{ScheduledJobReport, SchedulerRepository, SchedulerRepositoryError};
use crate::{SchedulerConfig, SchedulerJob};

impl SchedulerRepository {
    pub(super) async fn enqueue_news_category_maintenance(
        &self,
        transaction: &mut Transaction<'static, Postgres>,
        now: DateTime<Utc>,
        config: &SchedulerConfig,
    ) -> Result<ScheduledJobReport, SchedulerRepositoryError> {
        let runs = newsly_db::prepare_due_news_category_runs(
            transaction,
            now,
            config.news_category_maintenance_batch_size,
            config.news_category_maintenance_mode,
        )
        .await?;
        let requests = runs
            .iter()
            .map(|run| {
                let mut request = EnqueueRequest::new(TaskType::ReclusterNewsLenses);
                request.priority = -10;
                request.payload = Some(Map::from_iter([
                    ("user_id".to_owned(), Value::from(run.user_id)),
                    ("run_id".to_owned(), Value::from(run.run_id)),
                    (
                        "local_date".to_owned(),
                        Value::from(run.local_date.to_string()),
                    ),
                    ("timezone".to_owned(), Value::from(run.timezone.clone())),
                    (
                        "timezone_revision".to_owned(),
                        Value::from(run.timezone_revision),
                    ),
                    (
                        "window_start_at".to_owned(),
                        Value::from(run.window_start_at.to_rfc3339()),
                    ),
                    (
                        "window_end_at".to_owned(),
                        Value::from(run.window_end_at.to_rfc3339()),
                    ),
                    ("mode".to_owned(), Value::from(run.mode.as_str())),
                ]));
                request.owner_user_id = Some(run.user_id);
                request.dedupe = Some(true);
                request.dedupe_key = Some(format!(
                    "news-lens-recluster:{}:{}",
                    run.user_id, run.local_date
                ));
                request
            })
            .collect();
        let result = self
            .queue
            .enqueue_many_in_transaction(transaction, requests)
            .await?;
        for (run, task_id) in runs.iter().zip(&result.task_ids) {
            if !newsly_db::attach_news_category_run_task(transaction, run.run_id, *task_id).await? {
                return Err(SchedulerRepositoryError::RunTaskAttach(run.run_id));
            }
        }
        Ok(ScheduledJobReport {
            job: SchedulerJob::NewsCategoryMaintenance,
            considered: runs.len(),
            enqueued: result.inserted_task_ids.len(),
            skipped: runs.len().saturating_sub(result.inserted_task_ids.len()),
            detail: "news_category_maintenance_enqueued",
            maintenance: None,
        })
    }

    pub(super) async fn enqueue_scrape(
        &self,
        transaction: &mut Transaction<'static, Postgres>,
        config: &SchedulerConfig,
    ) -> Result<ScheduledJobReport, SchedulerRepositoryError> {
        let (pending_content, pending_news): (i64, i64) = sqlx::query_as(
            r"
            SELECT
                count(*)::bigint,
                count(*) FILTER (WHERE task_type = 'process_news_item')::bigint
            FROM processing_tasks
            WHERE status = 'pending' AND queue_name = 'content'
            ",
        )
        .fetch_one(&mut **transaction)
        .await?;
        let backpressure = pending_content >= config.queue_backpressure_max_pending_content
            || pending_news >= config.queue_backpressure_max_pending_process_news_item;
        if backpressure {
            tracing::warn!(
                pending_content,
                pending_process_news_item = pending_news,
                max_pending_content = config.queue_backpressure_max_pending_content,
                max_pending_process_news_item =
                    config.queue_backpressure_max_pending_process_news_item,
                "scheduled scrape skipped due to queue backpressure"
            );
        }

        let mut request = EnqueueRequest::new(TaskType::Scrape);
        request.payload = Some(Map::from_iter([
            ("sources".to_owned(), Value::Array(vec![Value::from("all")])),
            ("due_only".to_owned(), Value::Bool(true)),
        ]));
        request.dedupe = Some(true);
        request.dedupe_key = Some("scheduled-scrape".to_owned());
        let ids = sqlx::query_scalar::<_, i64>(r"
            SELECT n.id::bigint FROM news_items n
            WHERE n.status = 'ready' AND n.visibility_scope = 'global'
              AND n.representative_news_item_id IS NULL
              AND COALESCE(n.published_at, n.ingested_at) > timezone('UTC', now()) - interval '7 days'
              AND NOT EXISTS (SELECT 1 FROM news_lens_embeddings e WHERE e.news_item_id = n.id AND e.model=$1 AND e.encoder_version=$2 AND timezone('UTC',e.checked_at) >= COALESCE(n.updated_at,n.created_at))
              AND NOT EXISTS (SELECT 1 FROM processing_tasks t WHERE t.task_type='prepare_news_lens' AND t.payload->>'news_item_id'=n.id::text AND t.status='failed' AND t.payload->>'embedding_model'=$1 AND t.completed_at>=COALESCE(n.updated_at,n.created_at) AND t.completed_at>=timezone('UTC',now())-interval '6 hours')
            ORDER BY n.ingested_at DESC, n.id DESC LIMIT 128
        ").bind(&config.lens_embedding_model).bind(newsly_db::news_lens_embeddings::ENCODER_VERSION).fetch_all(&mut **transaction).await?;
        let mut requests = if backpressure {
            Vec::new()
        } else {
            vec![request]
        };
        for id in ids {
            let mut warm = EnqueueRequest::new(TaskType::PrepareNewsLens);
            warm.priority = -10;
            warm.payload = Some(Map::from_iter([(
                "news_item_id".to_owned(),
                Value::from(id),
            )]));
            warm.dedupe = Some(true);
            warm.dedupe_key = Some(format!("news-lens:{id}"));
            requests.push(warm);
        }
        let considered = requests.len();
        let result = self
            .queue
            .enqueue_many_in_transaction(transaction, requests)
            .await?;
        Ok(ScheduledJobReport {
            job: SchedulerJob::Scrape,
            considered,
            enqueued: result.inserted_task_ids.len(),
            skipped: considered.saturating_sub(result.inserted_task_ids.len()),
            detail: if backpressure {
                "queue_backpressure"
            } else {
                "scrape_enqueued"
            },
            maintenance: None,
        })
    }

    pub(super) async fn enqueue_integration_sync(
        &self,
        transaction: &mut Transaction<'static, Postgres>,
        config: &SchedulerConfig,
    ) -> Result<ScheduledJobReport, SchedulerRepositoryError> {
        if !config.x_sync_enabled {
            return Ok(ScheduledJobReport::skipped(
                SchedulerJob::IntegrationSync,
                "x_sync_disabled",
            ));
        }
        let user_ids = sqlx::query_scalar::<_, i64>(
            r"
            SELECT app_user.id::bigint
            FROM users AS app_user
            WHERE app_user.is_active IS TRUE
              AND EXISTS (
                  SELECT 1
                  FROM user_integration_connections AS connection
                  WHERE connection.user_id = app_user.id
                    AND connection.provider = 'x'
                    AND connection.is_active IS TRUE
              )
            ORDER BY app_user.id
            FOR SHARE
            ",
        )
        .fetch_all(&mut **transaction)
        .await?;
        let requests = user_ids
            .iter()
            .map(|user_id| {
                let mut request = EnqueueRequest::new(TaskType::SyncIntegration);
                request.payload = Some(Map::from_iter([
                    ("user_id".to_owned(), Value::from(*user_id)),
                    ("provider".to_owned(), Value::from("x")),
                    ("trigger".to_owned(), Value::from("cron")),
                ]));
                request.owner_user_id = Some(*user_id);
                request.dedupe = Some(true);
                request.dedupe_key = Some(format!("scheduled-x-sync:user:{user_id}"));
                request
            })
            .collect();
        self.enqueue_fanout(
            transaction,
            SchedulerJob::IntegrationSync,
            user_ids.len(),
            requests,
            "integration_sync_enqueued",
        )
        .await
    }

    pub(super) async fn enqueue_briefing_sweeps(
        &self,
        transaction: &mut Transaction<'static, Postgres>,
    ) -> Result<ScheduledJobReport, SchedulerRepositoryError> {
        let user_ids = sqlx::query_scalar::<_, i64>(
            r"
            SELECT app_user.id::bigint
            FROM users AS app_user
            WHERE app_user.is_active IS TRUE
              AND EXISTS (
                  SELECT 1
                  FROM briefing_states AS state
                  WHERE state.user_id = app_user.id
              )
              AND NOT EXISTS (
                  SELECT 1
                  FROM processing_tasks AS task
                  WHERE task.owner_user_id = app_user.id
                    AND task.task_type = 'briefing_refresh'
                    AND task.status IN ('pending', 'processing')
                    AND COALESCE(task.payload ->> 'mode', 'append') = 'sweep'
              )
            ORDER BY app_user.id
            FOR SHARE
            ",
        )
        .fetch_all(&mut **transaction)
        .await?;
        let requests = user_ids
            .iter()
            .map(|user_id| {
                let mut request = EnqueueRequest::new(TaskType::BriefingRefresh);
                request.payload = Some(Map::from_iter([
                    ("user_id".to_owned(), Value::from(*user_id)),
                    ("mode".to_owned(), Value::from("sweep")),
                ]));
                request.owner_user_id = Some(*user_id);
                request.dedupe = Some(true);
                request.dedupe_key = Some(format!("briefing_refresh:{user_id}:sweep"));
                request
            })
            .collect();
        self.enqueue_fanout(
            transaction,
            SchedulerJob::BriefingSweepReconcile,
            user_ids.len(),
            requests,
            "missing_briefing_sweeps_enqueued",
        )
        .await
    }

    pub(super) async fn enqueue_feed_discovery(
        &self,
        transaction: &mut Transaction<'static, Postgres>,
        config: &SchedulerConfig,
    ) -> Result<ScheduledJobReport, SchedulerRepositoryError> {
        let user_ids = sqlx::query_scalar::<_, i64>(
            r"
            SELECT app_user.id::bigint
            FROM users AS app_user
            WHERE app_user.is_active IS TRUE
              AND app_user.has_completed_onboarding IS TRUE
              AND (
                  SELECT count(*)
                  FROM content_read_status AS read_status
                  WHERE read_status.user_id = app_user.id
              ) >= $1
            ORDER BY app_user.id
            FOR SHARE
            ",
        )
        .bind(config.feed_discovery_min_reads)
        .fetch_all(&mut **transaction)
        .await?;
        let requests = user_ids
            .iter()
            .map(|user_id| {
                let mut request = EnqueueRequest::new(TaskType::DiscoverFeeds);
                request.payload = Some(Map::from_iter([
                    ("user_id".to_owned(), Value::from(*user_id)),
                    ("trigger".to_owned(), Value::from("cron")),
                ]));
                request.owner_user_id = Some(*user_id);
                request.dedupe = Some(true);
                request.dedupe_key = Some(format!("scheduled-feed-discovery:user:{user_id}"));
                request
            })
            .collect();
        self.enqueue_fanout(
            transaction,
            SchedulerJob::FeedDiscovery,
            user_ids.len(),
            requests,
            "feed_discovery_enqueued",
        )
        .await
    }

    async fn enqueue_fanout(
        &self,
        transaction: &mut Transaction<'static, Postgres>,
        job: SchedulerJob,
        considered: usize,
        requests: Vec<EnqueueRequest>,
        detail: &'static str,
    ) -> Result<ScheduledJobReport, SchedulerRepositoryError> {
        let result = self
            .queue
            .enqueue_many_in_transaction(transaction, requests)
            .await?;
        let enqueued = result.inserted_task_ids.len();
        Ok(ScheduledJobReport {
            job,
            considered,
            enqueued,
            skipped: considered.saturating_sub(enqueued),
            detail,
            maintenance: None,
        })
    }
}

#[cfg(test)]
mod tests {
    use std::time::Duration;

    use chrono::TimeZone;
    use newsly_db::{DatabaseConfig, NewsCategoryMaintenanceMode};
    use secrecy::SecretString;
    use sqlx::PgPool;

    use super::*;
    use crate::SchedulerLogFormat;

    fn config() -> SchedulerConfig {
        SchedulerConfig {
            database: DatabaseConfig::new(
                SecretString::from("postgres://test.invalid/newsly"),
                "scheduler-test",
            ),
            instance_id: "scheduler-test".to_owned(),
            poll_interval: Duration::from_secs(15),
            lens_embedding_model: "test-embedding".to_owned(),
            x_sync_enabled: false,
            feed_discovery_min_reads: 0,
            news_category_maintenance_batch_size: 8,
            news_category_maintenance_mode: NewsCategoryMaintenanceMode::Shadow,
            queue_backpressure_max_pending_content: 150,
            queue_backpressure_max_pending_process_news_item: 75,
            orphan_lease_grace: Duration::from_secs(600),
            terminal_retention_days: 14,
            terminal_cleanup_batch_size: 5_000,
            terminal_cleanup_max_delete: 50_000,
            watchdog_alert_threshold: 1,
            watchdog_slack_webhook_url: None,
            log_filter: "info".to_owned(),
            log_format: SchedulerLogFormat::Pretty,
        }
    }

    #[sqlx::test(migrations = "../newsly-db/migrations")]
    async fn due_news_category_run_enqueues_owned_llm_task_and_attaches_identity(pool: PgPool) {
        let user_id = sqlx::query_scalar::<_, i64>(
            "INSERT INTO users(apple_id,email,is_admin,is_active) VALUES('scheduler-nightly','scheduler-nightly@example.com',false,true) RETURNING id::bigint",
        )
        .fetch_one(&pool)
        .await
        .unwrap();
        let mut transaction = pool.begin().await.unwrap();
        newsly_db::set_user_news_category_timezone(
            &mut transaction,
            user_id,
            "America/Los_Angeles",
            None,
        )
        .await
        .unwrap();
        transaction.commit().await.unwrap();

        let now = Utc.with_ymd_and_hms(2026, 9, 28, 10, 10, 0).unwrap();
        sqlx::query("UPDATE user_news_category_schedule SET next_due_at=$2,next_local_date='2026-09-28',next_window_start_at=$3,next_window_end_at=$4 WHERE user_id::bigint=$1")
            .bind(user_id)
            .bind(now.naive_utc())
            .bind(Utc.with_ymd_and_hms(2026, 9, 28, 10, 0, 0).unwrap().naive_utc())
            .bind(Utc.with_ymd_and_hms(2026, 9, 28, 12, 0, 0).unwrap().naive_utc())
            .execute(&pool)
            .await
            .unwrap();

        let repository = SchedulerRepository::new(pool.clone());
        let mut transaction = pool.begin().await.unwrap();
        let report = repository
            .enqueue_news_category_maintenance(&mut transaction, now, &config())
            .await
            .unwrap();
        transaction.commit().await.unwrap();
        assert_eq!(
            (report.considered, report.enqueued, report.skipped),
            (1, 1, 0)
        );

        let row = sqlx::query_as::<_, (String, String, i64, i64, String, i32)>(
            r"
            SELECT task.task_type, task.queue_name, task.owner_user_id::bigint,
                   run.id, task.executor_runtime, task.priority
            FROM news_category_maintenance_runs AS run
            JOIN processing_tasks AS task ON task.id=run.task_id
            WHERE run.user_id::bigint=$1 AND run.local_date='2026-09-28'
            ",
        )
        .bind(user_id)
        .fetch_one(&pool)
        .await
        .unwrap();
        assert_eq!(row.0, "recluster_news_lenses");
        assert_eq!(row.1, "llm");
        assert_eq!(row.2, user_id);
        assert!(row.3 > 0);
        assert_eq!(row.4, "rust");
        assert_eq!(row.5, -10);
    }
}
