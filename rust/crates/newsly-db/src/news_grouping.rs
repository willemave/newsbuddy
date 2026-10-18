//! Short, nonblocking publication guards for the exact News visibility/owner pool.

use sqlx::PgConnection;

/// Bound grouping conflicts independently from paid-provider retries.
pub const MAX_DEFERRALS: i32 = 1200;
/// One hour covers twice the captured 29.9-minute ingestion burst.
pub const MAX_CONFLICT_AGE_SECONDS: i32 = 3600;

#[derive(Debug, Clone, Copy)]
pub struct GroupingDeferral {
    pub count: i32,
    pub age_seconds: f64,
    pub exhausted: bool,
    pub delay_seconds: i64,
}

/// Record recovery under the caller's exact task lease, after rolling back product writes.
/// Callers map the owned result to their task outcome and feature-specific diagnostics.
pub async fn record_deferral(
    connection: &mut PgConnection,
    task_id: i64,
) -> Result<GroupingDeferral, sqlx::Error> {
    let (count, age_seconds): (i32, f64) = sqlx::query_as(
        "INSERT INTO news_grouping_deferrals(task_id,retry_count)
         SELECT id,retry_count FROM processing_tasks WHERE id::bigint=$1
         ON CONFLICT(task_id) DO UPDATE SET
           first_deferred_at=CASE WHEN news_grouping_deferrals.retry_count<>EXCLUDED.retry_count
             THEN clock_timestamp() ELSE news_grouping_deferrals.first_deferred_at END,
           deferral_count=CASE WHEN news_grouping_deferrals.retry_count<>EXCLUDED.retry_count
             THEN 1 ELSE news_grouping_deferrals.deferral_count+1 END,
           retry_count=EXCLUDED.retry_count
         RETURNING deferral_count,extract(epoch FROM clock_timestamp()-first_deferred_at)::double precision"
    ).bind(task_id).fetch_one(connection).await?;
    Ok(GroupingDeferral {
        count,
        age_seconds,
        exhausted: count >= MAX_DEFERRALS || age_seconds >= f64::from(MAX_CONFLICT_AGE_SECONDS),
        delay_seconds: 1 + task_id.rem_euclid(3),
    })
}

/// A non-grouping outcome ends the current conflict episode, including credit deferrals
/// that intentionally retain the same provider retry count.
pub async fn clear_deferral(
    connection: &mut PgConnection,
    task_id: i64,
) -> Result<(), sqlx::Error> {
    sqlx::query("DELETE FROM news_grouping_deferrals WHERE task_id::bigint=$1")
        .bind(task_id)
        .execute(connection)
        .await?;
    Ok(())
}

/// Try the transaction-scoped guard before discovering candidates or locking story rows.
/// Callers must defer and roll back product writes when the scope is busy.
pub async fn try_lock_scope(
    connection: &mut PgConnection,
    scope: &str,
    owner: Option<i64>,
) -> Result<bool, sqlx::Error> {
    sqlx::query_scalar("SELECT newsly_try_lock_news_grouping_scope($1, $2)")
        .bind(scope)
        .bind(owner)
        .fetch_one(connection)
        .await
}

/// Recognize only our trigger's conflict; unrelated serialization errors retain their policy.
pub fn is_contention(error: &sqlx::Error) -> bool {
    error.as_database_error().is_some_and(|database| {
        database.code().as_deref() == Some("40001")
            && database.constraint() == Some("news_grouping_scope_busy")
    })
}

/// Follow repository/finalizer wrappers without confusing unrelated database failures.
pub fn contention_source(mut error: &(dyn std::error::Error + 'static)) -> bool {
    loop {
        if error
            .downcast_ref::<sqlx::Error>()
            .is_some_and(is_contention)
        {
            return true;
        }
        let Some(source) = error.source() else {
            return false;
        };
        error = source;
    }
}

#[cfg(test)]
mod tests;
