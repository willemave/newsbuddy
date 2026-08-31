use std::time::Duration;

use chrono::{DateTime, Utc};
use serde_json::{Value, json};
use sqlx::{Connection, FromRow, PgConnection, Postgres, Transaction};

use super::{ScheduledJobReport, SchedulerRepository, SchedulerRepositoryError};
use crate::{SchedulerConfig, SchedulerJob};

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct MaintenanceReport {
    pub misrouted: usize,
    pub orphaned_leases: usize,
    /// Complete expired leases remain in-place because the queue kernel claims them directly.
    pub expired_reclaimable: usize,
}

impl MaintenanceReport {
    pub const fn touched(self) -> usize {
        self.misrouted + self.orphaned_leases
    }
}

impl SchedulerRepository {
    pub(super) async fn repair_queue(
        &self,
        transaction: &mut Transaction<'static, Postgres>,
        orphan_grace: Duration,
    ) -> Result<MaintenanceReport, SchedulerRepositoryError> {
        let grace_seconds = i64::try_from(orphan_grace.as_secs())
            .map_err(|_| SchedulerRepositoryError::DurationOutOfRange)?;
        let misrouted = sqlx::query_scalar::<_, i64>(
            r"
            WITH task_specs(task_type, expected_queue) AS (
                VALUES
                    ('scrape', 'content'),
                    ('backfill_feeds', 'backfill'),
                    ('analyze_url', 'content'),
                    ('process_content', 'content'),
                    ('enrich_news_item_article', 'content'),
                    ('process_news_item', 'content'),
                    ('process_podcast_media', 'media'),
                    ('download_tweet_video_audio', 'media'),
                    ('transcribe_tweet_video', 'media'),
                    ('summarize', 'content'),
                    ('fetch_news_item_discussion', 'discussion'),
                    ('generate_image', 'image'),
                    ('discover_feeds', 'content'),
                    ('onboarding_discover', 'onboarding'),
                    ('dig_deeper', 'chat'),
                    ('chat_turn', 'chat'),
                    ('sync_integration', 'twitter'),
                    ('generate_audio_episode', 'audio_episode'),
                    ('run_llm_task', 'llm'),
                    ('briefing_refresh', 'llm'),
                    ('sync_agent_data', 'backfill'),
                    ('index_agent_data', 'backfill'),
                    ('backfill_agent_data', 'backfill'),
                    ('reconcile_agent_data', 'backfill'),
                    ('delete_user_account', 'backfill')
            ),
            repaired AS (
                UPDATE processing_tasks AS task
                SET queue_name = task_specs.expected_queue
                FROM task_specs
                WHERE task.task_type = task_specs.task_type
                  AND task.status IN ('pending', 'processing')
                  AND task.queue_name IS DISTINCT FROM task_specs.expected_queue
                RETURNING task.id
            )
            SELECT count(*)::bigint FROM repaired
            ",
        )
        .fetch_one(&mut **transaction)
        .await?;

        // A complete lease, even an old or expired one, is owned by the exact queue claim
        // protocol. Only structurally incomplete processing state is reset after a grace period.
        let orphaned_leases = sqlx::query_scalar::<_, i64>(
            r"
            WITH repaired AS (
                UPDATE processing_tasks
                SET
                    status = 'pending',
                    started_at = NULL,
                    completed_at = NULL,
                    available_at = timezone('UTC', now()),
                    locked_at = NULL,
                    locked_by = NULL,
                    lease_token = NULL,
                    lease_expires_at = NULL,
                    error_message = NULL,
                    retry_count = retry_count + 1
                WHERE status = 'processing'
                  AND (
                      lease_token IS NULL
                      OR locked_at IS NULL
                      OR locked_by IS NULL
                      OR lease_expires_at IS NULL
                  )
                  AND COALESCE(locked_at, started_at, created_at)
                      <= timezone('UTC', now()) - $1 * interval '1 second'
                RETURNING id
            )
            SELECT count(*)::bigint FROM repaired
            ",
        )
        .bind(grace_seconds)
        .fetch_one(&mut **transaction)
        .await?;

        let expired_reclaimable = sqlx::query_scalar::<_, i64>(
            r"
            SELECT count(*)::bigint
            FROM processing_tasks
            WHERE status = 'processing'
              AND lease_token IS NOT NULL
              AND locked_at IS NOT NULL
              AND locked_by IS NOT NULL
              AND lease_expires_at <= timezone('UTC', now())
            ",
        )
        .fetch_one(&mut **transaction)
        .await?;
        Ok(MaintenanceReport {
            misrouted: usize::try_from(misrouted).unwrap_or(usize::MAX),
            orphaned_leases: usize::try_from(orphaned_leases).unwrap_or(usize::MAX),
            expired_reclaimable: usize::try_from(expired_reclaimable).unwrap_or(usize::MAX),
        })
    }

    /// Delete terminal queue rows in bounded autocommit batches under one session advisory lock.
    ///
    /// A durable progress event suppresses a completed replacement run and resumes an interrupted
    /// run without resetting its deletion cap. Each deletion batch and progress checkpoint commit
    /// atomically while the session lock remains held.
    ///
    /// # Errors
    ///
    /// Returns a database error; the session advisory lock is still released best-effort.
    pub async fn run_terminal_cleanup(
        &self,
        scheduled_for: DateTime<Utc>,
        config: &SchedulerConfig,
    ) -> Result<Option<ScheduledJobReport>, SchedulerRepositoryError> {
        let job = SchedulerJob::TerminalTaskCleanup;
        let tick_key = job.advisory_key(scheduled_for);
        let mut connection = self.pool.acquire().await?;
        let acquired: bool = sqlx::query_scalar(
            "SELECT pg_catalog.pg_try_advisory_lock(pg_catalog.hashtextextended($1, 0))",
        )
        .bind(&tick_key)
        .fetch_one(&mut *connection)
        .await?;
        if !acquired {
            return Ok(None);
        }
        // A lost cleanup connection must release its session advisory lock rather than return a
        // lock-bearing connection to the pool. Cleanup runs once daily, so closing this one
        // connection after the tick is preferable to retaining hidden session state.
        connection.close_on_drop();

        let result = run_cleanup_with_lock(&mut connection, &tick_key, scheduled_for, config).await;
        let unlocked: bool = sqlx::query_scalar(
            "SELECT pg_catalog.pg_advisory_unlock(pg_catalog.hashtextextended($1, 0))",
        )
        .bind(&tick_key)
        .fetch_one(&mut *connection)
        .await?;
        if !unlocked {
            return Err(SchedulerRepositoryError::AdvisoryUnlockRejected);
        }
        result
    }
}

async fn run_cleanup_with_lock(
    connection: &mut PgConnection,
    tick_key: &str,
    scheduled_for: DateTime<Utc>,
    config: &SchedulerConfig,
) -> Result<Option<ScheduledJobReport>, SchedulerRepositoryError> {
    let Some(mut progress) =
        load_or_start_cleanup_tick(connection, tick_key, scheduled_for, config).await?
    else {
        return Ok(None);
    };

    while progress.deleted < config.terminal_cleanup_max_delete {
        let limit = config
            .terminal_cleanup_batch_size
            .min(config.terminal_cleanup_max_delete - progress.deleted);
        let deleted_in_batch = delete_terminal_batch(
            connection,
            progress.event_id,
            scheduled_for,
            config,
            progress,
            limit,
        )
        .await?;
        if deleted_in_batch == 0 {
            break;
        }
        progress.deleted += deleted_in_batch;
        progress.batches += 1;
        if deleted_in_batch < limit {
            break;
        }
    }
    let has_more = terminal_tasks_remain(connection, config.terminal_retention_days).await?;
    finish_cleanup_tick(
        connection,
        progress.event_id,
        scheduled_for,
        config,
        progress,
        has_more,
    )
    .await?;
    Ok(Some(ScheduledJobReport {
        job: SchedulerJob::TerminalTaskCleanup,
        considered: usize::try_from(progress.deleted).unwrap_or(usize::MAX),
        enqueued: 0,
        skipped: 0,
        detail: if has_more {
            "cleanup_cap_reached"
        } else {
            "terminal_tasks_cleaned"
        },
        maintenance: None,
    }))
}

async fn load_or_start_cleanup_tick(
    connection: &mut PgConnection,
    tick_key: &str,
    scheduled_for: DateTime<Utc>,
    config: &SchedulerConfig,
) -> Result<Option<CleanupProgress>, sqlx::Error> {
    let prior = sqlx::query_as::<_, CleanupTickRow>(
        r"
        SELECT id::bigint, status, data
        FROM event_logs
        WHERE event_type = 'scheduler_tick' AND event_name = $1
        ORDER BY id DESC
        LIMIT 1
        ",
    )
    .bind(tick_key)
    .fetch_optional(&mut *connection)
    .await?;
    if prior.as_ref().is_some_and(|row| row.status == "completed") {
        return Ok(None);
    }

    if let Some(prior) = prior {
        return Ok(Some(CleanupProgress {
            event_id: prior.id,
            deleted: prior
                .data
                .get("deleted")
                .and_then(Value::as_i64)
                .unwrap_or(0)
                .clamp(0, config.terminal_cleanup_max_delete),
            batches: prior
                .data
                .get("batches")
                .and_then(Value::as_i64)
                .unwrap_or(0)
                .max(0),
        }));
    }
    let event_id = sqlx::query_scalar::<_, i64>(
        r"
        INSERT INTO event_logs (event_type, event_name, status, data, created_at)
        VALUES ('scheduler_tick', $1, 'started', $2, timezone('UTC', now()))
        RETURNING id::bigint
        ",
    )
    .bind(tick_key)
    .bind(cleanup_event_data(scheduled_for, config, 0, 0, None))
    .fetch_one(&mut *connection)
    .await?;
    Ok(Some(CleanupProgress {
        event_id,
        deleted: 0,
        batches: 0,
    }))
}

async fn delete_terminal_batch(
    connection: &mut PgConnection,
    event_id: i64,
    scheduled_for: DateTime<Utc>,
    config: &SchedulerConfig,
    progress: CleanupProgress,
    limit: i64,
) -> Result<i64, sqlx::Error> {
    let mut transaction = connection.begin().await?;
    let deleted_in_batch = sqlx::query_scalar::<_, i64>(
        r"
        WITH victims AS (
            SELECT id
            FROM processing_tasks
            WHERE status IN ('completed', 'failed')
              AND (
                  completed_at < timezone('UTC', now()) - $1 * interval '1 day'
                  OR (
                      completed_at IS NULL
                      AND created_at < timezone('UTC', now()) - $1 * interval '1 day'
                  )
              )
            ORDER BY id
            FOR UPDATE SKIP LOCKED
            LIMIT $2
        ),
        deleted AS (
            DELETE FROM processing_tasks AS task
            USING victims
            WHERE task.id = victims.id
            RETURNING task.id
        )
        SELECT count(*)::bigint FROM deleted
        ",
    )
    .bind(config.terminal_retention_days)
    .bind(limit)
    .fetch_one(&mut *transaction)
    .await?;
    if deleted_in_batch == 0 {
        transaction.rollback().await?;
        return Ok(0);
    }
    sqlx::query("UPDATE event_logs SET data = $2 WHERE id::bigint = $1 AND status = 'started'")
        .bind(event_id)
        .bind(cleanup_event_data(
            scheduled_for,
            config,
            progress.deleted + deleted_in_batch,
            progress.batches + 1,
            None,
        ))
        .execute(&mut *transaction)
        .await?;
    transaction.commit().await?;
    Ok(deleted_in_batch)
}

async fn terminal_tasks_remain(
    connection: &mut PgConnection,
    retention_days: i64,
) -> Result<bool, sqlx::Error> {
    sqlx::query_scalar(
        r"
        SELECT EXISTS(
            SELECT 1
            FROM processing_tasks
            WHERE status IN ('completed', 'failed')
              AND (
                  completed_at < timezone('UTC', now()) - $1 * interval '1 day'
                  OR (
                      completed_at IS NULL
                      AND created_at < timezone('UTC', now()) - $1 * interval '1 day'
                  )
              )
        )
        ",
    )
    .bind(retention_days)
    .fetch_one(&mut *connection)
    .await
}

async fn finish_cleanup_tick(
    connection: &mut PgConnection,
    event_id: i64,
    scheduled_for: DateTime<Utc>,
    config: &SchedulerConfig,
    progress: CleanupProgress,
    has_more: bool,
) -> Result<(), sqlx::Error> {
    sqlx::query(
        r"
        UPDATE event_logs
        SET status = 'completed', data = $2
        WHERE id::bigint = $1 AND status = 'started'
        ",
    )
    .bind(event_id)
    .bind(cleanup_event_data(
        scheduled_for,
        config,
        progress.deleted,
        progress.batches,
        Some(has_more),
    ))
    .execute(&mut *connection)
    .await?;
    Ok(())
}

#[derive(Debug, FromRow)]
struct CleanupTickRow {
    id: i64,
    status: String,
    data: Value,
}

#[derive(Debug, Clone, Copy)]
struct CleanupProgress {
    event_id: i64,
    deleted: i64,
    batches: i64,
}

fn cleanup_event_data(
    scheduled_for: DateTime<Utc>,
    config: &SchedulerConfig,
    deleted: i64,
    batches: i64,
    has_more: Option<bool>,
) -> Value {
    json!({
        "job": SchedulerJob::TerminalTaskCleanup.as_str(),
        "scheduled_for": scheduled_for,
        "instance_id": config.instance_id,
        "deleted": deleted,
        "batches": batches,
        "has_more": has_more,
        "retention_days": config.terminal_retention_days,
        "max_delete": config.terminal_cleanup_max_delete,
    })
}
