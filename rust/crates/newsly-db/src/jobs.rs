use chrono::{DateTime, NaiveDateTime, Utc};
use serde_json::{Map, Value};
use sqlx::{FromRow, PgPool};
use thiserror::Error;

#[derive(Debug, FromRow)]
struct JobStatusRow {
    id: i64,
    task_type: String,
    status: String,
    queue_name: String,
    content_id: Option<i64>,
    retry_count: i32,
    created_at: Option<NaiveDateTime>,
    started_at: Option<NaiveDateTime>,
    completed_at: Option<NaiveDateTime>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct JobStatusProjection {
    pub id: i64,
    pub task_type: String,
    pub status: String,
    pub queue_name: String,
    pub content_id: Option<i64>,
    pub payload: Map<String, Value>,
    pub retry_count: i32,
    pub created_at: Option<DateTime<Utc>>,
    pub started_at: Option<DateTime<Utc>>,
    pub completed_at: Option<DateTime<Utc>>,
    pub error_message: Option<String>,
}

/// Return a user-visible task projection only when the user has explicit
/// polling access.
///
/// # Errors
///
/// Returns [`JobRepositoryError::Sqlx`] when PostgreSQL cannot complete the
/// query.
pub async fn find_job_for_user(
    pool: &PgPool,
    job_id: i64,
    user_id: i64,
) -> Result<Option<JobStatusProjection>, JobRepositoryError> {
    let row = sqlx::query_as::<_, JobStatusRow>(
        r#"
        SELECT
            task.id::bigint AS id,
            task.task_type,
            task.status,
            task.queue_name,
            task.content_id::bigint AS content_id,
            task.retry_count,
            task.created_at,
            task.started_at,
            task.completed_at
        FROM processing_tasks AS task
        JOIN processing_task_user_access AS task_access
          ON task_access.task_id = task.id
        WHERE task.id = $1
          AND task_access.user_id = $2
        "#,
    )
    .bind(job_id)
    .bind(user_id)
    .fetch_optional(pool)
    .await?;

    Ok(row.map(|row| JobStatusProjection {
        id: row.id,
        task_type: row.task_type,
        error_message: (row.status == "failed").then(|| "Job failed".to_owned()),
        status: row.status,
        queue_name: row.queue_name,
        content_id: row.content_id,
        payload: Map::new(),
        retry_count: row.retry_count,
        created_at: row.created_at.map(|value| value.and_utc()),
        started_at: row.started_at.map(|value| value.and_utc()),
        completed_at: row.completed_at.map(|value| value.and_utc()),
    }))
}

#[derive(Debug, Error)]
pub enum JobRepositoryError {
    #[error("PostgreSQL job lookup failed")]
    Sqlx(#[from] sqlx::Error),
}
