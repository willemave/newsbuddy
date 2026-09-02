use newsly_queue::{EnqueueRequest, TaskType};
use serde_json::{Map, Value};
use sqlx::{Postgres, Transaction};

use super::{ScheduledJobReport, SchedulerRepository, SchedulerRepositoryError};
use crate::{SchedulerConfig, SchedulerJob};

impl SchedulerRepository {
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
        if pending_content >= config.queue_backpressure_max_pending_content
            || pending_news >= config.queue_backpressure_max_pending_process_news_item
        {
            tracing::warn!(
                pending_content,
                pending_process_news_item = pending_news,
                max_pending_content = config.queue_backpressure_max_pending_content,
                max_pending_process_news_item =
                    config.queue_backpressure_max_pending_process_news_item,
                "scheduled scrape skipped due to queue backpressure"
            );
            return Ok(ScheduledJobReport::skipped(
                SchedulerJob::Scrape,
                "queue_backpressure",
            ));
        }

        let mut request = EnqueueRequest::new(TaskType::Scrape);
        request.payload = Some(Map::from_iter([(
            "sources".to_owned(),
            Value::Array(vec![Value::from("all")]),
        )]));
        request.dedupe = Some(true);
        request.dedupe_key = Some("scheduled-scrape".to_owned());
        let result = self
            .queue
            .enqueue_many_in_transaction(transaction, vec![request])
            .await?;
        Ok(ScheduledJobReport {
            job: SchedulerJob::Scrape,
            considered: 1,
            enqueued: result.inserted_task_ids.len(),
            skipped: usize::from(result.inserted_task_ids.is_empty()),
            detail: "scrape_enqueued",
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
