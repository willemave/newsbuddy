use serde_json::json;
use sqlx::{FromRow, Postgres, Transaction};
use thiserror::Error;

const TERMINAL_STATUSES: [&str; 3] = ["completed", "failed", "cancelled"];

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum LlmTaskDispatchKind {
    ShareAction,
    LearningDeck,
    Terminal,
    Unsupported { task_kind: String },
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum UnsupportedLlmTaskOutcome {
    Failed { message: String },
    Terminal,
}

#[derive(Debug, FromRow)]
struct DispatchRow {
    user_id: i64,
    task_kind: String,
    status: String,
}

/// Classifies one LLM ledger row in a bounded transaction before external dispatch.
pub async fn classify_llm_task(
    transaction: &mut Transaction<'_, Postgres>,
    task_id: i64,
    user_id: i64,
) -> Result<LlmTaskDispatchKind, LlmTaskDispatchRepositoryError> {
    let row = load_for_update(transaction, task_id).await?;
    if row.user_id != user_id {
        return Err(LlmTaskDispatchRepositoryError::OwnershipMismatch);
    }
    if TERMINAL_STATUSES.contains(&row.status.as_str()) {
        return Ok(LlmTaskDispatchKind::Terminal);
    }
    Ok(match row.task_kind.as_str() {
        "share_action" => LlmTaskDispatchKind::ShareAction,
        "learning_deck" => LlmTaskDispatchKind::LearningDeck,
        _ => LlmTaskDispatchKind::Unsupported {
            task_kind: row.task_kind,
        },
    })
}

/// Marks an unsupported LLM task failed inside the queue kernel's exact finalization fence.
pub async fn fail_unsupported_llm_task(
    transaction: &mut Transaction<'_, Postgres>,
    task_id: i64,
    user_id: i64,
) -> Result<UnsupportedLlmTaskOutcome, LlmTaskDispatchRepositoryError> {
    let row = load_for_update(transaction, task_id).await?;
    if row.user_id != user_id {
        return Err(LlmTaskDispatchRepositoryError::OwnershipMismatch);
    }
    if TERMINAL_STATUSES.contains(&row.status.as_str()) {
        return Ok(UnsupportedLlmTaskOutcome::Terminal);
    }
    let message = format!("Unsupported LLM task kind: {}", row.task_kind);
    let history = json!({
        "status": "failed",
        "workflow_state": "failed",
        "note": "LLM task execution failed",
        "created_at": chrono::Utc::now().to_rfc3339(),
    });
    sqlx::query(
        r#"
        UPDATE llm_tasks
        SET status = 'failed',
            workflow_state = 'failed',
            error_type = 'unsupported_task_kind',
            error_message = $2,
            completed_at = timezone('UTC', clock_timestamp()),
            updated_at = timezone('UTC', clock_timestamp()),
            status_history = COALESCE(status_history, '[]'::jsonb) || jsonb_build_array($3::jsonb)
        WHERE id::bigint = $1
          AND status <> ALL($4)
        "#,
    )
    .bind(task_id)
    .bind(&message)
    .bind(history)
    .bind(TERMINAL_STATUSES.as_slice())
    .execute(&mut **transaction)
    .await?;
    Ok(UnsupportedLlmTaskOutcome::Failed { message })
}

async fn load_for_update(
    transaction: &mut Transaction<'_, Postgres>,
    task_id: i64,
) -> Result<DispatchRow, LlmTaskDispatchRepositoryError> {
    sqlx::query_as::<_, DispatchRow>(
        r#"
        SELECT user_id::bigint AS user_id, task_kind, status
        FROM llm_tasks
        WHERE id::bigint = $1
        FOR UPDATE
        "#,
    )
    .bind(task_id)
    .fetch_optional(&mut **transaction)
    .await?
    .ok_or(LlmTaskDispatchRepositoryError::TaskNotFound)
}

#[derive(Debug, Error)]
pub enum LlmTaskDispatchRepositoryError {
    #[error("LLM task not found")]
    TaskNotFound,
    #[error("LLM task ownership mismatch")]
    OwnershipMismatch,
    #[error("PostgreSQL LLM task dispatch operation failed")]
    Sqlx(#[from] sqlx::Error),
}
