use chrono::{DateTime, NaiveDateTime, Utc};
use serde_json::{Map, Value};
use sqlx::{FromRow, PgPool, Postgres, Transaction};
use thiserror::Error;

#[derive(Debug, Clone, PartialEq, FromRow)]
struct LlmTaskActionRow {
    id: i64,
    llm_task_id: i64,
    action_name: String,
    action_status: String,
    approval_policy: String,
    approval_required: bool,
    action_input: Value,
    action_result: Value,
    rationale: Option<String>,
    idempotency_key: Option<String>,
    approved_by_user_id: Option<i64>,
    error_message: Option<String>,
    created_at: NaiveDateTime,
    approved_at: Option<NaiveDateTime>,
    started_at: Option<NaiveDateTime>,
    completed_at: Option<NaiveDateTime>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct LlmTaskActionProjection {
    pub id: i64,
    pub llm_task_id: i64,
    pub action_name: String,
    pub action_status: String,
    pub approval_policy: String,
    pub approval_required: bool,
    pub action_input: Map<String, Value>,
    pub action_result: Map<String, Value>,
    pub rationale: Option<String>,
    pub idempotency_key: Option<String>,
    pub approved_by_user_id: Option<i64>,
    pub error_message: Option<String>,
    pub created_at: DateTime<Utc>,
    pub approved_at: Option<DateTime<Utc>>,
    pub started_at: Option<DateTime<Utc>>,
    pub completed_at: Option<DateTime<Utc>>,
}

#[derive(Debug, Clone, PartialEq)]
pub enum RejectLlmTaskActionOutcome {
    Rejected(LlmTaskActionProjection),
    InvalidStatus(String),
    ActionNotFound,
    TaskNotFound,
}

/// Lists actions for a user-owned LLM task. `None` means the task is absent or belongs to another
/// user; an empty vector means the owned task has no actions.
///
/// # Errors
///
/// Returns [`LlmTaskRepositoryError::Sqlx`] when PostgreSQL cannot complete the lookup.
pub async fn list_llm_task_actions_for_user(
    pool: &PgPool,
    user_id: i64,
    task_id: i64,
) -> Result<Option<Vec<LlmTaskActionProjection>>, LlmTaskRepositoryError> {
    if !owned_task_exists(pool, user_id, task_id).await? {
        return Ok(None);
    }

    let rows = sqlx::query_as::<_, LlmTaskActionRow>(ACTION_PROJECTION_SQL)
        .bind(task_id)
        .fetch_all(pool)
        .await?;
    Ok(Some(rows.into_iter().map(Into::into).collect()))
}

/// Rejects an approval-pending or proposed action while holding the row lock and caller-supplied
/// route-ownership transaction.
///
/// # Errors
///
/// Returns [`LlmTaskRepositoryError::Sqlx`] when PostgreSQL cannot complete the transition.
pub async fn reject_llm_task_action(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
    task_id: i64,
    action_id: i64,
    reason: Option<&str>,
) -> Result<RejectLlmTaskActionOutcome, LlmTaskRepositoryError> {
    if !owned_task_exists(&mut **transaction, user_id, task_id).await? {
        return Ok(RejectLlmTaskActionOutcome::TaskNotFound);
    }

    let current_status = sqlx::query_scalar::<_, String>(
        r#"
        SELECT action_status
        FROM llm_task_actions
        WHERE id = $1
          AND llm_task_id = $2
        FOR UPDATE
        "#,
    )
    .bind(action_id)
    .bind(task_id)
    .fetch_optional(&mut **transaction)
    .await?;

    let Some(current_status) = current_status else {
        return Ok(RejectLlmTaskActionOutcome::ActionNotFound);
    };
    if current_status != "awaiting_approval" && current_status != "proposed" {
        return Ok(RejectLlmTaskActionOutcome::InvalidStatus(current_status));
    }

    let row = sqlx::query_as::<_, LlmTaskActionRow>(
        r#"
        UPDATE llm_task_actions
        SET action_status = 'rejected',
            error_message = $3,
            completed_at = timezone('UTC', clock_timestamp()),
            updated_at = timezone('UTC', clock_timestamp())
        WHERE id = $1
          AND llm_task_id = $2
        RETURNING
            id::bigint AS id,
            llm_task_id::bigint AS llm_task_id,
            action_name,
            action_status,
            approval_policy,
            approval_required,
            action_input,
            action_result,
            rationale,
            idempotency_key,
            approved_by_user_id::bigint AS approved_by_user_id,
            error_message,
            created_at,
            approved_at,
            started_at,
            completed_at
        "#,
    )
    .bind(action_id)
    .bind(task_id)
    .bind(reason)
    .fetch_one(&mut **transaction)
    .await?;

    Ok(RejectLlmTaskActionOutcome::Rejected(row.into()))
}

async fn owned_task_exists<'e, E>(
    executor: E,
    user_id: i64,
    task_id: i64,
) -> Result<bool, sqlx::Error>
where
    E: sqlx::Executor<'e, Database = Postgres>,
{
    sqlx::query_scalar::<_, bool>(
        "SELECT EXISTS(SELECT 1 FROM llm_tasks WHERE id = $1 AND user_id = $2)",
    )
    .bind(task_id)
    .bind(user_id)
    .fetch_one(executor)
    .await
}

const ACTION_PROJECTION_SQL: &str = r#"
    SELECT
        id::bigint AS id,
        llm_task_id::bigint AS llm_task_id,
        action_name,
        action_status,
        approval_policy,
        approval_required,
        action_input,
        action_result,
        rationale,
        idempotency_key,
        approved_by_user_id::bigint AS approved_by_user_id,
        error_message,
        created_at,
        approved_at,
        started_at,
        completed_at
    FROM llm_task_actions
    WHERE llm_task_id = $1
    ORDER BY created_at, id
"#;

impl From<LlmTaskActionRow> for LlmTaskActionProjection {
    fn from(row: LlmTaskActionRow) -> Self {
        Self {
            id: row.id,
            llm_task_id: row.llm_task_id,
            action_name: row.action_name,
            action_status: row.action_status,
            approval_policy: row.approval_policy,
            approval_required: row.approval_required,
            action_input: json_object_or_default(row.action_input),
            action_result: json_object_or_default(row.action_result),
            rationale: row.rationale,
            idempotency_key: row.idempotency_key,
            approved_by_user_id: row.approved_by_user_id,
            error_message: row.error_message,
            created_at: row.created_at.and_utc(),
            approved_at: row.approved_at.map(|value| value.and_utc()),
            started_at: row.started_at.map(|value| value.and_utc()),
            completed_at: row.completed_at.map(|value| value.and_utc()),
        }
    }
}

fn json_object_or_default(value: Value) -> Map<String, Value> {
    match value {
        Value::Object(object) => object,
        _ => Map::new(),
    }
}

#[derive(Debug, Error)]
pub enum LlmTaskRepositoryError {
    #[error("PostgreSQL LLM task operation failed")]
    Sqlx(#[from] sqlx::Error),
}
