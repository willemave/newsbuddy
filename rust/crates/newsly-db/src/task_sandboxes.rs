use sqlx::PgPool;
use thiserror::Error;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TaskSandboxCleanupCandidate {
    pub task_id: i64,
    pub user_id: i64,
    pub sandbox_id: String,
}

pub async fn find_recorded_task_sandbox(
    pool: &PgPool,
    task_id: i64,
    user_id: i64,
) -> Result<Option<String>, TaskSandboxRepositoryError> {
    validate(task_id, user_id)?;
    let row = sqlx::query_as::<_, (Option<String>,)>(
        r#"
        SELECT sandbox_id
        FROM llm_tasks
        WHERE id::bigint = $1
          AND user_id::bigint = $2
          AND status NOT IN ('completed', 'failed', 'cancelled')
        "#,
    )
    .bind(task_id)
    .bind(user_id)
    .fetch_optional(pool)
    .await?
    .ok_or(TaskSandboxRepositoryError::AttemptUnavailable)?;
    Ok(row.0)
}

pub async fn record_task_sandbox(
    pool: &PgPool,
    task_id: i64,
    user_id: i64,
    sandbox_id: &str,
) -> Result<(), TaskSandboxRepositoryError> {
    validate_sandbox_identity(task_id, user_id, sandbox_id)?;
    let updated = sqlx::query(
        r#"
        UPDATE llm_tasks
        SET sandbox_provider = 'e2b',
            sandbox_id = $3,
            sandbox_cleanup_required = FALSE,
            updated_at = timezone('UTC', clock_timestamp())
        WHERE id::bigint = $1
          AND user_id::bigint = $2
          AND status NOT IN ('completed', 'failed', 'cancelled')
        "#,
    )
    .bind(task_id)
    .bind(user_id)
    .bind(sandbox_id)
    .execute(pool)
    .await?;
    if updated.rows_affected() == 1 {
        Ok(())
    } else {
        Err(TaskSandboxRepositoryError::AttemptUnavailable)
    }
}

pub async fn mark_task_sandbox_cleanup_required(
    pool: &PgPool,
    task_id: i64,
    user_id: i64,
    sandbox_id: &str,
) -> Result<(), TaskSandboxRepositoryError> {
    validate_sandbox_identity(task_id, user_id, sandbox_id)?;
    let updated = sqlx::query(
        r#"
        UPDATE llm_tasks
        SET sandbox_cleanup_required = TRUE,
            updated_at = timezone('UTC', clock_timestamp())
        WHERE id::bigint = $1
          AND user_id::bigint = $2
          AND sandbox_id = $3
        "#,
    )
    .bind(task_id)
    .bind(user_id)
    .bind(sandbox_id)
    .execute(pool)
    .await?;
    exact_update(updated.rows_affected())
}

pub async fn clear_task_sandbox(
    pool: &PgPool,
    task_id: i64,
    user_id: i64,
    sandbox_id: &str,
) -> Result<(), TaskSandboxRepositoryError> {
    validate_sandbox_identity(task_id, user_id, sandbox_id)?;
    let updated = sqlx::query(
        r#"
        UPDATE llm_tasks
        SET sandbox_provider = NULL,
            sandbox_id = NULL,
            sandbox_cleanup_required = FALSE,
            updated_at = timezone('UTC', clock_timestamp())
        WHERE id::bigint = $1
          AND user_id::bigint = $2
          AND sandbox_id = $3
        "#,
    )
    .bind(task_id)
    .bind(user_id)
    .bind(sandbox_id)
    .execute(pool)
    .await?;
    exact_update(updated.rows_affected())
}

pub async fn list_task_sandbox_cleanup_candidates(
    pool: &PgPool,
    limit: i64,
) -> Result<Vec<TaskSandboxCleanupCandidate>, TaskSandboxRepositoryError> {
    if !(1..=100).contains(&limit) {
        return Err(TaskSandboxRepositoryError::InvalidInput);
    }
    let rows = sqlx::query_as::<_, (i64, i64, String)>(
        r#"
        SELECT id::bigint, user_id::bigint, sandbox_id
        FROM llm_tasks
        WHERE sandbox_id IS NOT NULL
          AND (
              sandbox_cleanup_required = TRUE
              OR status IN ('completed', 'failed', 'cancelled')
          )
        ORDER BY updated_at, id
        LIMIT $1
        "#,
    )
    .bind(limit)
    .fetch_all(pool)
    .await?;
    Ok(rows
        .into_iter()
        .map(
            |(task_id, user_id, sandbox_id)| TaskSandboxCleanupCandidate {
                task_id,
                user_id,
                sandbox_id,
            },
        )
        .collect())
}

fn validate_sandbox_identity(
    task_id: i64,
    user_id: i64,
    sandbox_id: &str,
) -> Result<(), TaskSandboxRepositoryError> {
    validate(task_id, user_id)?;
    if sandbox_id.trim().is_empty() || sandbox_id.trim() != sandbox_id || sandbox_id.len() > 256 {
        Err(TaskSandboxRepositoryError::InvalidInput)
    } else {
        Ok(())
    }
}

fn exact_update(rows_affected: u64) -> Result<(), TaskSandboxRepositoryError> {
    if rows_affected == 1 {
        Ok(())
    } else {
        Err(TaskSandboxRepositoryError::AttemptUnavailable)
    }
}

fn validate(task_id: i64, user_id: i64) -> Result<(), TaskSandboxRepositoryError> {
    if task_id <= 0 || user_id <= 0 {
        Err(TaskSandboxRepositoryError::InvalidInput)
    } else {
        Ok(())
    }
}

#[derive(Debug, Error)]
pub enum TaskSandboxRepositoryError {
    #[error("task sandbox identity is invalid")]
    InvalidInput,
    #[error("LLM task attempt is no longer available")]
    AttemptUnavailable,
    #[error("task sandbox database operation failed")]
    Sqlx(#[from] sqlx::Error),
}

#[cfg(test)]
mod tests {
    use super::{TaskSandboxRepositoryError, validate};

    #[test]
    fn task_sandbox_attempt_requires_positive_identities() {
        assert!(validate(1, 1).is_ok());
        assert!(matches!(
            validate(0, 1),
            Err(TaskSandboxRepositoryError::InvalidInput)
        ));
        assert!(validate(1, -1).is_err());
    }
}
