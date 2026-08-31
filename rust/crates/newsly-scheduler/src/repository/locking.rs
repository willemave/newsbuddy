use chrono::{DateTime, Utc};
use serde_json::json;
use sqlx::{PgPool, Postgres, Transaction};

use super::{ScheduledJobReport, SchedulerRepositoryError};
use crate::SchedulerJob;

pub(super) async fn begin_tick(
    pool: &PgPool,
    job: SchedulerJob,
    scheduled_for: DateTime<Utc>,
) -> Result<Option<Transaction<'static, Postgres>>, SchedulerRepositoryError> {
    let tick_key = job.advisory_key(scheduled_for);
    let mut transaction = pool.begin().await?;
    let acquired: bool = sqlx::query_scalar(
        "SELECT pg_catalog.pg_try_advisory_xact_lock(pg_catalog.hashtextextended($1, 0))",
    )
    .bind(&tick_key)
    .fetch_one(&mut *transaction)
    .await?;
    if !acquired {
        transaction.rollback().await?;
        return Ok(None);
    }
    if tick_completed(&mut transaction, &tick_key).await? {
        transaction.rollback().await?;
        return Ok(None);
    }
    Ok(Some(transaction))
}

pub(super) async fn tick_completed(
    transaction: &mut Transaction<'_, Postgres>,
    tick_key: &str,
) -> Result<bool, sqlx::Error> {
    sqlx::query_scalar(
        r"
        SELECT EXISTS(
            SELECT 1
            FROM event_logs
            WHERE event_type = 'scheduler_tick'
              AND event_name = $1
              AND status = 'completed'
        )
        ",
    )
    .bind(tick_key)
    .fetch_one(&mut **transaction)
    .await
}

pub(super) async fn mark_tick_completed(
    transaction: &mut Transaction<'_, Postgres>,
    job: SchedulerJob,
    scheduled_for: DateTime<Utc>,
    instance_id: &str,
    report: &ScheduledJobReport,
) -> Result<(), sqlx::Error> {
    sqlx::query(
        r"
        INSERT INTO event_logs (event_type, event_name, status, data, created_at)
        VALUES ('scheduler_tick', $1, 'completed', $2, timezone('UTC', now()))
        ",
    )
    .bind(job.advisory_key(scheduled_for))
    .bind(json!({
        "job": job.as_str(),
        "scheduled_for": scheduled_for,
        "instance_id": instance_id,
        "considered": report.considered,
        "enqueued": report.enqueued,
        "skipped": report.skipped,
        "detail": report.detail,
        "maintenance": report.maintenance.as_ref().map(|maintenance| json!({
            "misrouted": maintenance.misrouted,
            "orphaned_leases": maintenance.orphaned_leases,
            "expired_reclaimable": maintenance.expired_reclaimable,
        })),
    }))
    .execute(&mut **transaction)
    .await?;
    Ok(())
}
