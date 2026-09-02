use chrono::{DateTime, NaiveDateTime, Utc};
use serde_json::{Map, Value, json};
use sqlx::{FromRow, PgPool, Postgres, Transaction};
use thiserror::Error;

use crate::chat::{CreateChatSessionInput, CreateChatSessionOutcome, create_chat_session};
use crate::llm_tasks::LlmTaskActionProjection;

const TERMINAL_STATUSES: [&str; 3] = ["completed", "failed", "cancelled"];

#[derive(Debug, Clone, PartialEq)]
pub struct ShareActionTaskProjection {
    pub id: i64,
    pub user_id: i64,
    pub mode: String,
    pub status: String,
    pub workflow_state: String,
    pub created_at: DateTime<Utc>,
    pub actions: Vec<LlmTaskActionProjection>,
}

#[derive(Debug, Clone)]
pub struct NewShareActionTask<'a> {
    pub user_id: i64,
    pub mode: &'a str,
    pub approval_policy: &'a Map<String, Value>,
    pub allowed_action: &'a str,
    pub input: &'a Map<String, Value>,
    pub sandbox_root: &'a str,
}

#[derive(Debug, Clone, PartialEq)]
pub struct CreatedShareActionTask {
    pub id: i64,
    pub created_at: DateTime<Utc>,
}

#[derive(Debug, Clone, PartialEq)]
pub enum ShareActionPreparation {
    Terminal,
    Ready(ShareActionPreparationDraft),
}

#[derive(Debug, Clone, PartialEq)]
pub struct ShareActionPreparationDraft {
    pub id: i64,
    pub user_id: i64,
    pub mode: String,
    pub workflow_key: String,
    pub approval_policy: Map<String, Value>,
    pub allowed_actions: Vec<String>,
    pub tool_policy: Map<String, Value>,
    pub input: Map<String, Value>,
    pub workspace_path: String,
    pub prepare_shared_source: bool,
    pub deterministic_chat: bool,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct PreparedShareSource {
    pub content_id: i64,
    pub task_id: Option<i64>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct ShareActionAgentSnapshot {
    pub id: i64,
    pub user_id: i64,
    pub mode: String,
    pub workflow_key: String,
    pub approval_policy: Map<String, Value>,
    pub allowed_actions: Vec<String>,
    pub tool_policy: Map<String, Value>,
    pub input: Map<String, Value>,
    pub workspace_path: String,
}

#[derive(Debug, Clone, PartialEq)]
pub struct ShareActionFinalizationTask {
    pub id: i64,
    pub user_id: i64,
    pub mode: String,
    pub approval_policy: Map<String, Value>,
    pub allowed_actions: Vec<String>,
    pub input: Map<String, Value>,
    pub status: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum RequestedActionStatus {
    Approved,
    Applying,
    Applied,
    AwaitingApproval,
    Proposed,
    Rejected,
    Failed,
}

#[derive(Debug, Clone, PartialEq)]
pub struct RequestedShareAction {
    pub id: i64,
    pub status: RequestedActionStatus,
}

#[derive(Debug, Clone, PartialEq)]
pub enum ApproveShareActionOutcome {
    Approved {
        task: ShareActionFinalizationTask,
        action: LlmTaskActionProjection,
    },
    ApprovedGeneric {
        action: LlmTaskActionProjection,
    },
    InvalidStatus(String),
    ActionNotFound,
    TaskNotFound,
}

#[derive(Debug, Clone, PartialEq)]
pub struct PreparedContentProjection {
    pub id: i64,
    pub url: String,
    pub source_url: Option<String>,
    pub title: Option<String>,
    pub platform: Option<String>,
}

#[derive(Debug, Clone, FromRow)]
struct TaskRow {
    id: i64,
    user_id: i64,
    task_kind: String,
    mode: String,
    workflow_key: String,
    status: String,
    approval_policy: Value,
    allowed_actions: Value,
    tool_policy: Value,
    input_json: Value,
    workspace_path: Option<String>,
    user_is_active: bool,
}

#[derive(Debug, Clone, FromRow)]
struct ActionRow {
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

/// Inserts the generic LLM ledger row. The caller enqueues `run_llm_task` in the same transaction.
pub async fn insert_share_action_task(
    transaction: &mut Transaction<'_, Postgres>,
    input: &NewShareActionTask<'_>,
) -> Result<CreatedShareActionTask, ShareActionRepositoryError> {
    let active = sqlx::query_scalar::<_, bool>(
        "SELECT EXISTS(SELECT 1 FROM users WHERE id::bigint = $1 AND is_active = TRUE)",
    )
    .bind(input.user_id)
    .fetch_one(&mut **transaction)
    .await?;
    if !active {
        return Err(ShareActionRepositoryError::UserMissingOrInactive);
    }

    let now = Utc::now();
    let history = Value::Array(vec![history_entry(
        "queued",
        "queued",
        "LLM task created",
        now,
    )]);
    let (id, created_at) = sqlx::query_as::<_, (i64, NaiveDateTime)>(
        r#"
        INSERT INTO llm_tasks (
            user_id, task_kind, mode, workflow_key, workflow_version,
            workflow_state, status, approval_policy, allowed_actions, tool_policy,
            prompt_pack, input_json, output_json, artifact_manifest, usage_json,
            status_history, created_at, updated_at
        )
        VALUES (
            $1::bigint::integer, 'share_action', $2, $3, 1,
            'queued', 'queued', $4, jsonb_build_array($5::text),
            '{"execute_bash":true,"web_search":true,"files":"read_write"}'::jsonb,
            $6, $7, '{}'::jsonb, '{}'::jsonb, '{}'::jsonb,
            $8, $9, $9
        )
        RETURNING id::bigint, created_at
        "#,
    )
    .bind(input.user_id)
    .bind(input.mode)
    .bind(format!("share_action.{}.v1", input.mode))
    .bind(Value::Object(input.approval_policy.clone()))
    .bind(input.allowed_action)
    .bind(format!("share_action.{}", input.mode))
    .bind(Value::Object(input.input.clone()))
    .bind(history)
    .bind(now.naive_utc())
    .fetch_one(&mut **transaction)
    .await?;

    let root = input.sandbox_root.trim_end_matches('/');
    sqlx::query(
        r#"
        UPDATE llm_tasks
        SET workspace_path = $2
        WHERE id::bigint = $1
        "#,
    )
    .bind(id)
    .bind(format!("{root}/tasks/{id}"))
    .execute(&mut **transaction)
    .await?;

    Ok(CreatedShareActionTask {
        id,
        created_at: created_at.and_utc(),
    })
}

/// Loads a user-owned Share Action and all action rows without conflating task keyspaces.
pub async fn find_share_action_for_user(
    pool: &PgPool,
    user_id: i64,
    task_id: i64,
) -> Result<Option<ShareActionTaskProjection>, ShareActionRepositoryError> {
    let task = sqlx::query_as::<_, (i64, i64, String, String, String, NaiveDateTime)>(
        r#"
        SELECT id::bigint, user_id::bigint, mode, status, workflow_state, created_at
        FROM llm_tasks
        WHERE id::bigint = $1 AND user_id::bigint = $2 AND task_kind = 'share_action'
        "#,
    )
    .bind(task_id)
    .bind(user_id)
    .fetch_optional(pool)
    .await?;
    let Some((id, user_id, mode, status, workflow_state, created_at)) = task else {
        return Ok(None);
    };
    let actions = load_actions(pool, id).await?;
    Ok(Some(ShareActionTaskProjection {
        id,
        user_id,
        mode,
        status,
        workflow_state,
        created_at: created_at.and_utc(),
        actions,
    }))
}

/// Opens and commits only the database-owned preparation phase; no provider resource escapes it.
pub async fn begin_share_action_preparation(
    transaction: &mut Transaction<'_, Postgres>,
    task_id: i64,
    user_id: i64,
) -> Result<ShareActionPreparation, ShareActionRepositoryError> {
    let row = load_task_for_update(transaction, task_id).await?;
    let Some(row) = row else {
        return Err(ShareActionRepositoryError::TaskNotFound);
    };
    validate_share_task(&row, user_id)?;
    if TERMINAL_STATUSES.contains(&row.status.as_str()) {
        return Ok(ShareActionPreparation::Terminal);
    }
    if !row.user_is_active {
        return Err(ShareActionRepositoryError::UserMissingOrInactive);
    }
    set_task_state(
        transaction,
        task_id,
        "preparing",
        "preparing",
        "Preparing Share Action workflow",
    )
    .await?;

    let prepare_shared_source = !matches!(row.mode.as_str(), "add_feed" | "add_to_briefing")
        && json_positive_i64(&row.input_json, "knowledge_content_id").is_none();
    Ok(ShareActionPreparation::Ready(ShareActionPreparationDraft {
        id: row.id,
        user_id: row.user_id,
        mode: row.mode.clone(),
        workflow_key: row.workflow_key.clone(),
        approval_policy: json_object(row.approval_policy),
        allowed_actions: json_strings(row.allowed_actions),
        tool_policy: json_object(row.tool_policy),
        input: json_object(row.input_json),
        workspace_path: required_text(row.workspace_path, "workspace_path")?,
        prepare_shared_source,
        deterministic_chat: row.mode == "chat",
    }))
}

pub async fn finish_share_action_preparation(
    transaction: &mut Transaction<'_, Postgres>,
    draft: ShareActionPreparationDraft,
    source: Option<PreparedShareSource>,
) -> Result<ShareActionAgentSnapshot, ShareActionRepositoryError> {
    let mut task_input = draft.input.clone();
    if let Some(source) = source {
        task_input.insert(
            "knowledge_content_id".to_owned(),
            Value::from(source.content_id),
        );
        match source.task_id {
            Some(task_id) => {
                task_input.insert("knowledge_task_id".to_owned(), Value::from(task_id));
            }
            None => {
                task_input.remove("knowledge_task_id");
            }
        }
    }
    let (status, state, note) = if draft.deterministic_chat {
        (
            "applying",
            "applying",
            "Applying deterministic chat handoff",
        )
    } else {
        ("running", "running", "Running Share Action agent")
    };
    let entry = history_entry(status, state, note, Utc::now());
    sqlx::query(
        r#"
        UPDATE llm_tasks
        SET input_json = $2,
            status = $3,
            workflow_state = $4,
            started_at = COALESCE(started_at, timezone('UTC', clock_timestamp())),
            updated_at = timezone('UTC', clock_timestamp()),
            status_history = COALESCE(status_history, '[]'::jsonb) || jsonb_build_array($5::jsonb)
        WHERE id::bigint = $1
        "#,
    )
    .bind(draft.id)
    .bind(Value::Object(task_input.clone()))
    .bind(status)
    .bind(state)
    .bind(entry)
    .execute(&mut **transaction)
    .await?;

    Ok(ShareActionAgentSnapshot {
        id: draft.id,
        user_id: draft.user_id,
        mode: draft.mode,
        workflow_key: draft.workflow_key,
        approval_policy: draft.approval_policy,
        allowed_actions: draft.allowed_actions,
        tool_policy: draft.tool_policy,
        input: task_input,
        workspace_path: draft.workspace_path,
    })
}

pub async fn lock_share_action_for_finalization(
    transaction: &mut Transaction<'_, Postgres>,
    task_id: i64,
    user_id: i64,
) -> Result<ShareActionFinalizationTask, ShareActionRepositoryError> {
    let row = load_task_for_update(transaction, task_id)
        .await?
        .ok_or(ShareActionRepositoryError::TaskNotFound)?;
    validate_share_task(&row, user_id)?;
    Ok(ShareActionFinalizationTask {
        id: row.id,
        user_id: row.user_id,
        mode: row.mode,
        approval_policy: json_object(row.approval_policy),
        allowed_actions: json_strings(row.allowed_actions),
        input: json_object(row.input_json),
        status: row.status,
    })
}

#[allow(clippy::too_many_arguments)]
pub async fn persist_share_action_agent_output(
    transaction: &mut Transaction<'_, Postgres>,
    task_id: i64,
    output: &Value,
    usage: &Value,
    model_provider: &str,
    model_name: &str,
    sandbox_provider: &str,
    sandbox_id: Option<&str>,
) -> Result<(), ShareActionRepositoryError> {
    let entry = history_entry(
        "applying",
        "applying",
        "Applying Share Action result",
        Utc::now(),
    );
    sqlx::query(
        r#"
        UPDATE llm_tasks
        SET status = 'applying', workflow_state = 'applying', output_json = $2,
            usage_json = $3, model_provider = $4, model_name = $5,
            sandbox_provider = $6, sandbox_id = $7,
            updated_at = timezone('UTC', clock_timestamp()),
            status_history = COALESCE(status_history, '[]'::jsonb) || jsonb_build_array($8::jsonb)
        WHERE id::bigint = $1
        "#,
    )
    .bind(task_id)
    .bind(output)
    .bind(usage)
    .bind(model_provider)
    .bind(model_name)
    .bind(sandbox_provider)
    .bind(sandbox_id)
    .bind(entry)
    .execute(&mut **transaction)
    .await?;
    Ok(())
}

pub async fn request_share_action(
    transaction: &mut Transaction<'_, Postgres>,
    task: &ShareActionFinalizationTask,
    action_name: &str,
    action_input: &Map<String, Value>,
    rationale: Option<&str>,
    idempotency_key: &str,
) -> Result<RequestedShareAction, ShareActionRepositoryError> {
    if !task.allowed_actions.is_empty()
        && !task
            .allowed_actions
            .iter()
            .any(|allowed| allowed == action_name)
    {
        return Err(ShareActionRepositoryError::ActionNotAllowed(
            action_name.to_owned(),
        ));
    }
    if let Some(existing) = sqlx::query_as::<_, (i64, String)>(
        r#"
        SELECT id::bigint, action_status
        FROM llm_task_actions
        WHERE llm_task_id::bigint = $1 AND action_name = $2 AND idempotency_key = $3
        FOR UPDATE
        "#,
    )
    .bind(task.id)
    .bind(action_name)
    .bind(idempotency_key)
    .fetch_optional(&mut **transaction)
    .await?
    {
        return Ok(RequestedShareAction {
            id: existing.0,
            status: requested_status(&existing.1)?,
        });
    }

    let policy = approval_policy(&task.approval_policy, action_name)?;
    let (action_status, approval_required, requested_status) = match policy {
        "auto_apply" => ("approved", false, RequestedActionStatus::Approved),
        "dry_run" => ("proposed", false, RequestedActionStatus::Proposed),
        "approval_required" => (
            "awaiting_approval",
            true,
            RequestedActionStatus::AwaitingApproval,
        ),
        _ => unreachable!("approval_policy returns a closed value"),
    };
    let id = sqlx::query_scalar::<_, i64>(
        r#"
        INSERT INTO llm_task_actions (
            llm_task_id, action_name, action_status, approval_policy,
            approval_required, action_input, action_result, rationale,
            idempotency_key, created_at, updated_at
        )
        VALUES (
            $1::bigint::integer, $2, $3, $4, $5, $6, '{}'::jsonb, $7, $8,
            timezone('UTC', clock_timestamp()), timezone('UTC', clock_timestamp())
        )
        RETURNING id::bigint
        "#,
    )
    .bind(task.id)
    .bind(action_name)
    .bind(action_status)
    .bind(policy)
    .bind(approval_required)
    .bind(Value::Object(action_input.clone()))
    .bind(rationale)
    .bind(idempotency_key)
    .fetch_one(&mut **transaction)
    .await?;
    Ok(RequestedShareAction {
        id,
        status: requested_status,
    })
}

pub async fn mark_share_action_applying(
    transaction: &mut Transaction<'_, Postgres>,
    action_id: i64,
) -> Result<(), ShareActionRepositoryError> {
    sqlx::query(
        r#"
        UPDATE llm_task_actions
        SET action_status = 'applying', started_at = timezone('UTC', clock_timestamp()),
            updated_at = timezone('UTC', clock_timestamp())
        WHERE id::bigint = $1
        "#,
    )
    .bind(action_id)
    .execute(&mut **transaction)
    .await?;
    Ok(())
}

pub async fn mark_share_action_applied(
    transaction: &mut Transaction<'_, Postgres>,
    action_id: i64,
    result: &Map<String, Value>,
) -> Result<(), ShareActionRepositoryError> {
    sqlx::query(
        r#"
        UPDATE llm_task_actions
        SET action_status = 'applied', action_result = $2,
            completed_at = timezone('UTC', clock_timestamp()),
            updated_at = timezone('UTC', clock_timestamp())
        WHERE id::bigint = $1
        "#,
    )
    .bind(action_id)
    .bind(Value::Object(result.clone()))
    .execute(&mut **transaction)
    .await?;
    Ok(())
}

pub async fn mark_share_action_failed(
    transaction: &mut Transaction<'_, Postgres>,
    action_id: i64,
    result: Option<&Map<String, Value>>,
    message: &str,
) -> Result<(), ShareActionRepositoryError> {
    sqlx::query(
        r#"
        UPDATE llm_task_actions
        SET action_status = 'failed', action_result = COALESCE($2, action_result),
            error_message = $3, completed_at = timezone('UTC', clock_timestamp()),
            updated_at = timezone('UTC', clock_timestamp())
        WHERE id::bigint = $1
        "#,
    )
    .bind(action_id)
    .bind(result.map(|value| Value::Object(value.clone())))
    .bind(truncate_chars(message, 4_000))
    .execute(&mut **transaction)
    .await?;
    Ok(())
}

pub async fn finish_share_action_task(
    transaction: &mut Transaction<'_, Postgres>,
    task_id: i64,
) -> Result<(), ShareActionRepositoryError> {
    let pending = sqlx::query_scalar::<_, bool>(
        r#"
        SELECT EXISTS(
            SELECT 1 FROM llm_task_actions
            WHERE llm_task_id::bigint = $1
              AND action_status IN ('awaiting_approval', 'proposed')
        )
        "#,
    )
    .bind(task_id)
    .fetch_one(&mut **transaction)
    .await?;
    if pending {
        set_task_state(
            transaction,
            task_id,
            "awaiting_approval",
            "awaiting_approval",
            "Awaiting Share Action approval",
        )
        .await
    } else {
        set_task_terminal(
            transaction,
            task_id,
            "completed",
            "completed",
            "Share Action completed",
            None,
            None,
        )
        .await
    }
}

pub async fn fail_share_action_task(
    transaction: &mut Transaction<'_, Postgres>,
    task_id: i64,
    user_id: i64,
    error_type: &str,
    message: &str,
    sandbox_provider: Option<&str>,
    sandbox_id: Option<&str>,
) -> Result<(), ShareActionRepositoryError> {
    let current = sqlx::query_scalar::<_, String>(
        r#"
        SELECT status FROM llm_tasks
        WHERE id::bigint = $1 AND user_id::bigint = $2 AND task_kind = 'share_action'
        FOR UPDATE
        "#,
    )
    .bind(task_id)
    .bind(user_id)
    .fetch_optional(&mut **transaction)
    .await?;
    let Some(current) = current else {
        return Err(ShareActionRepositoryError::TaskNotFound);
    };
    if TERMINAL_STATUSES.contains(&current.as_str()) {
        return Ok(());
    }
    let entry = history_entry("failed", "failed", "LLM task execution failed", Utc::now());
    sqlx::query(
        r#"
        UPDATE llm_tasks
        SET status = 'failed', workflow_state = 'failed', error_type = $2,
            error_message = $3, sandbox_provider = COALESCE($4, sandbox_provider),
            sandbox_id = COALESCE($5, sandbox_id), completed_at = timezone('UTC', clock_timestamp()),
            updated_at = timezone('UTC', clock_timestamp()),
            status_history = COALESCE(status_history, '[]'::jsonb) || jsonb_build_array($6::jsonb)
        WHERE id::bigint = $1
        "#,
    )
    .bind(task_id)
    .bind(truncate_chars(error_type, 128))
    .bind(truncate_chars(message, 4_000))
    .bind(sandbox_provider)
    .bind(sandbox_id)
    .bind(entry)
    .execute(&mut **transaction)
    .await?;
    Ok(())
}

pub async fn approve_share_action(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
    task_id: i64,
    action_id: i64,
) -> Result<ApproveShareActionOutcome, ShareActionRepositoryError> {
    let Some(row) = load_task_for_update(transaction, task_id).await? else {
        return Ok(ApproveShareActionOutcome::TaskNotFound);
    };
    if row.user_id != user_id {
        return Ok(ApproveShareActionOutcome::TaskNotFound);
    }
    let action = load_action_for_update(transaction, task_id, action_id).await?;
    let Some(action) = action else {
        return Ok(ApproveShareActionOutcome::ActionNotFound);
    };
    if action.action_status != "awaiting_approval" {
        return Ok(ApproveShareActionOutcome::InvalidStatus(
            action.action_status,
        ));
    }
    sqlx::query(
        r#"
        UPDATE llm_task_actions
        SET action_status = 'approved', approved_by_user_id = $3::bigint::integer,
            approved_at = timezone('UTC', clock_timestamp()),
            updated_at = timezone('UTC', clock_timestamp())
        WHERE id::bigint = $1 AND llm_task_id::bigint = $2
        "#,
    )
    .bind(action_id)
    .bind(task_id)
    .bind(user_id)
    .execute(&mut **transaction)
    .await?;
    let action = load_action_for_update(transaction, task_id, action_id)
        .await?
        .ok_or(ShareActionRepositoryError::ActionMissingAfterWrite)?;
    if row.task_kind != "share_action" {
        return Ok(ApproveShareActionOutcome::ApprovedGeneric {
            action: action.into(),
        });
    }
    Ok(ApproveShareActionOutcome::Approved {
        task: ShareActionFinalizationTask {
            id: row.id,
            user_id: row.user_id,
            mode: row.mode,
            approval_policy: json_object(row.approval_policy),
            allowed_actions: json_strings(row.allowed_actions),
            input: json_object(row.input_json),
            status: row.status,
        },
        action: action.into(),
    })
}

pub async fn load_share_action_action(
    transaction: &mut Transaction<'_, Postgres>,
    task_id: i64,
    action_id: i64,
) -> Result<LlmTaskActionProjection, ShareActionRepositoryError> {
    load_action_for_update(transaction, task_id, action_id)
        .await?
        .map(Into::into)
        .ok_or(ShareActionRepositoryError::ActionNotFound)
}

/// Records the durable terminal state after a synchronous approval applicator rolls back.
///
/// The callback first attempts approval and application in one transaction. If any applicator SQL
/// fails, that transaction is discarded and this fresh transaction preserves the user's approval
/// plus the failed action/task state, matching the legacy observable behavior without reusing an
/// aborted PostgreSQL transaction.
pub async fn fail_share_action_approval(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
    task_id: i64,
    action_id: i64,
    error_type: &str,
    message: &str,
    action_result: Option<&Map<String, Value>>,
) -> Result<LlmTaskActionProjection, ShareActionRepositoryError> {
    let row = load_task_for_update(transaction, task_id)
        .await?
        .ok_or(ShareActionRepositoryError::TaskNotFound)?;
    validate_share_task(&row, user_id)?;
    let action = load_action_for_update(transaction, task_id, action_id)
        .await?
        .ok_or(ShareActionRepositoryError::ActionNotFound)?;
    if !matches!(
        action.action_status.as_str(),
        "awaiting_approval" | "approved" | "applying"
    ) {
        return Err(ShareActionRepositoryError::InvalidActionStatus(
            action.action_status,
        ));
    }
    sqlx::query(
        r#"
        UPDATE llm_task_actions
        SET action_status = 'failed',
            approved_by_user_id = $4::bigint::integer,
            approved_at = COALESCE(approved_at, timezone('UTC', clock_timestamp())),
            started_at = COALESCE(started_at, timezone('UTC', clock_timestamp())),
            action_result = COALESCE($5, action_result),
            error_message = $3,
            completed_at = timezone('UTC', clock_timestamp()),
            updated_at = timezone('UTC', clock_timestamp())
        WHERE id::bigint = $1 AND llm_task_id::bigint = $2
        "#,
    )
    .bind(action_id)
    .bind(task_id)
    .bind(truncate_chars(message, 4_000))
    .bind(user_id)
    .bind(action_result.map(|value| Value::Object(value.clone())))
    .execute(&mut **transaction)
    .await?;
    let durable_error_type = truncate_chars(error_type, 128);
    let durable_message = truncate_chars(message, 4_000);
    set_task_terminal(
        transaction,
        task_id,
        "failed",
        "failed",
        "Share Action application failed",
        Some(&durable_error_type),
        Some(&durable_message),
    )
    .await?;
    load_share_action_action(transaction, task_id, action_id).await
}

pub async fn find_prepared_share_content(
    transaction: &mut Transaction<'_, Postgres>,
    task: &ShareActionFinalizationTask,
) -> Result<Option<PreparedContentProjection>, ShareActionRepositoryError> {
    let Some(content_id) = task
        .input
        .get("knowledge_content_id")
        .and_then(Value::as_i64)
        .filter(|id| *id > 0)
    else {
        return Ok(None);
    };
    Ok(
        sqlx::query_as::<_, (i64, String, Option<String>, Option<String>, Option<String>)>(
            r#"
        SELECT id::bigint, url, source_url, title, platform
        FROM contents WHERE id::bigint = $1 FOR UPDATE
        "#,
        )
        .bind(content_id)
        .fetch_optional(&mut **transaction)
        .await?
        .map(
            |(id, url, source_url, title, platform)| PreparedContentProjection {
                id,
                url,
                source_url,
                title,
                platform,
            },
        ),
    )
}

pub async fn enrich_prepared_share_content(
    transaction: &mut Transaction<'_, Postgres>,
    content: &PreparedContentProjection,
    title: Option<&str>,
    platform: Option<&str>,
) -> Result<(), ShareActionRepositoryError> {
    sqlx::query(
        r#"
        UPDATE contents
        SET title = CASE WHEN NULLIF(title, '') IS NULL THEN NULLIF($2, '') ELSE title END,
            platform = CASE WHEN NULLIF(platform, '') IS NULL THEN NULLIF($3, '') ELSE platform END,
            updated_at = timezone('UTC', clock_timestamp())
        WHERE id::bigint = $1
        "#,
    )
    .bind(content.id)
    .bind(title)
    .bind(platform)
    .execute(&mut **transaction)
    .await?;
    Ok(())
}

pub async fn get_or_create_share_chat_session(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
    content_id: i64,
) -> Result<(i64, bool), ShareActionRepositoryError> {
    if let Some(session_id) = sqlx::query_scalar::<_, i64>(
        r#"
        SELECT id::bigint FROM chat_sessions
        WHERE user_id::bigint = $1 AND content_id::bigint = $2 AND is_archived = FALSE
        ORDER BY id LIMIT 1 FOR UPDATE
        "#,
    )
    .bind(user_id)
    .bind(content_id)
    .fetch_optional(&mut **transaction)
    .await?
    {
        return Ok((session_id, false));
    }
    let outcome = create_chat_session(
        transaction,
        &CreateChatSessionInput {
            user_id,
            content_id: Some(content_id),
            news_item_id: None,
            topic: None,
            initial_message: None,
            session_type: "knowledge_chat",
            llm_provider: "openai",
            llm_model: "openai:gpt-5.6-terra",
        },
    )
    .await?;
    match outcome {
        CreateChatSessionOutcome::Created { session_id } => Ok((session_id, true)),
        CreateChatSessionOutcome::UserInactive => {
            Err(ShareActionRepositoryError::UserMissingOrInactive)
        }
        CreateChatSessionOutcome::ContentNotFound => {
            Err(ShareActionRepositoryError::PreparedContentMissing)
        }
        CreateChatSessionOutcome::NewsItemNotFound => unreachable!("chat source is content"),
    }
}

async fn load_task_for_update(
    transaction: &mut Transaction<'_, Postgres>,
    task_id: i64,
) -> Result<Option<TaskRow>, sqlx::Error> {
    sqlx::query_as::<_, TaskRow>(
        r#"
        SELECT task.id::bigint, task.user_id::bigint, task.task_kind, task.mode,
               task.workflow_key,
               task.status, task.approval_policy, task.allowed_actions,
               task.tool_policy, task.input_json, task.workspace_path,
               users.is_active AS user_is_active
        FROM llm_tasks AS task
        JOIN users ON users.id = task.user_id
        WHERE task.id::bigint = $1
        FOR UPDATE OF task, users
        "#,
    )
    .bind(task_id)
    .fetch_optional(&mut **transaction)
    .await
}

fn validate_share_task(row: &TaskRow, user_id: i64) -> Result<(), ShareActionRepositoryError> {
    if row.user_id != user_id {
        return Err(ShareActionRepositoryError::OwnershipMismatch);
    }
    if row.task_kind != "share_action" || !row.workflow_key.starts_with("share_action.") {
        return Err(ShareActionRepositoryError::WrongTaskKind);
    }
    Ok(())
}

async fn load_actions<'e, E>(
    executor: E,
    task_id: i64,
) -> Result<Vec<LlmTaskActionProjection>, sqlx::Error>
where
    E: sqlx::Executor<'e, Database = Postgres>,
{
    Ok(sqlx::query_as::<_, ActionRow>(ACTION_SQL)
        .bind(task_id)
        .fetch_all(executor)
        .await?
        .into_iter()
        .map(Into::into)
        .collect())
}

async fn load_action_for_update(
    transaction: &mut Transaction<'_, Postgres>,
    task_id: i64,
    action_id: i64,
) -> Result<Option<ActionRow>, sqlx::Error> {
    sqlx::query_as::<_, ActionRow>(ACTION_FOR_UPDATE_SQL)
        .bind(task_id)
        .bind(action_id)
        .fetch_optional(&mut **transaction)
        .await
}

async fn set_task_state(
    transaction: &mut Transaction<'_, Postgres>,
    task_id: i64,
    status: &str,
    workflow_state: &str,
    note: &str,
) -> Result<(), ShareActionRepositoryError> {
    let entry = history_entry(status, workflow_state, note, Utc::now());
    sqlx::query(
        r#"
        UPDATE llm_tasks
        SET status = $2, workflow_state = $3,
            started_at = CASE WHEN $2 IN ('preparing', 'running', 'applying')
                THEN COALESCE(started_at, timezone('UTC', clock_timestamp())) ELSE started_at END,
            updated_at = timezone('UTC', clock_timestamp()),
            status_history = COALESCE(status_history, '[]'::jsonb) || jsonb_build_array($4::jsonb)
        WHERE id::bigint = $1
        "#,
    )
    .bind(task_id)
    .bind(status)
    .bind(workflow_state)
    .bind(entry)
    .execute(&mut **transaction)
    .await?;
    Ok(())
}

#[allow(clippy::too_many_arguments)]
async fn set_task_terminal(
    transaction: &mut Transaction<'_, Postgres>,
    task_id: i64,
    status: &str,
    workflow_state: &str,
    note: &str,
    error_type: Option<&str>,
    error_message: Option<&str>,
) -> Result<(), ShareActionRepositoryError> {
    let entry = history_entry(status, workflow_state, note, Utc::now());
    sqlx::query(
        r#"
        UPDATE llm_tasks
        SET status = $2, workflow_state = $3, error_type = $4, error_message = $5,
            completed_at = timezone('UTC', clock_timestamp()),
            updated_at = timezone('UTC', clock_timestamp()),
            status_history = COALESCE(status_history, '[]'::jsonb) || jsonb_build_array($6::jsonb)
        WHERE id::bigint = $1
        "#,
    )
    .bind(task_id)
    .bind(status)
    .bind(workflow_state)
    .bind(error_type)
    .bind(error_message)
    .bind(entry)
    .execute(&mut **transaction)
    .await?;
    Ok(())
}

fn approval_policy<'a>(
    policy: &'a Map<String, Value>,
    action_name: &str,
) -> Result<&'a str, ShareActionRepositoryError> {
    let value = policy
        .get("overrides")
        .and_then(Value::as_object)
        .and_then(|overrides| overrides.get(action_name))
        .or_else(|| policy.get("default"))
        .and_then(Value::as_str)
        .unwrap_or("approval_required");
    if matches!(value, "auto_apply" | "approval_required" | "dry_run") {
        Ok(value)
    } else {
        Err(ShareActionRepositoryError::UnsupportedApprovalPolicy(
            value.to_owned(),
        ))
    }
}

fn requested_status(value: &str) -> Result<RequestedActionStatus, ShareActionRepositoryError> {
    match value {
        "approved" => Ok(RequestedActionStatus::Approved),
        "applying" => Ok(RequestedActionStatus::Applying),
        "applied" => Ok(RequestedActionStatus::Applied),
        "awaiting_approval" => Ok(RequestedActionStatus::AwaitingApproval),
        "proposed" => Ok(RequestedActionStatus::Proposed),
        "rejected" => Ok(RequestedActionStatus::Rejected),
        "failed" => Ok(RequestedActionStatus::Failed),
        other => Err(ShareActionRepositoryError::InvalidActionStatus(
            other.to_owned(),
        )),
    }
}

fn history_entry(status: &str, state: &str, note: &str, now: DateTime<Utc>) -> Value {
    json!({
        "status": status,
        "workflow_state": state,
        "created_at": now.naive_utc().format("%Y-%m-%dT%H:%M:%S%.6f").to_string(),
        "note": note,
    })
}

fn json_object(value: Value) -> Map<String, Value> {
    match value {
        Value::Object(object) => object,
        _ => Map::new(),
    }
}

fn json_strings(value: Value) -> Vec<String> {
    match value {
        Value::Array(values) => values
            .into_iter()
            .filter_map(|value| value.as_str().map(str::to_owned))
            .collect(),
        _ => Vec::new(),
    }
}

fn json_positive_i64(value: &Value, key: &str) -> Option<i64> {
    value.get(key).and_then(Value::as_i64).filter(|id| *id > 0)
}

fn required_text(
    value: Option<String>,
    field: &'static str,
) -> Result<String, ShareActionRepositoryError> {
    value
        .filter(|value| !value.trim().is_empty())
        .ok_or(ShareActionRepositoryError::MissingTaskField(field))
}

fn truncate_chars(value: &str, maximum: usize) -> String {
    value.chars().take(maximum).collect()
}

const ACTION_SQL: &str = r#"
    SELECT id::bigint AS id, llm_task_id::bigint AS llm_task_id, action_name,
           action_status, approval_policy, approval_required, action_input, action_result,
           rationale, idempotency_key, approved_by_user_id::bigint AS approved_by_user_id,
           error_message, created_at, approved_at, started_at, completed_at
    FROM llm_task_actions
    WHERE llm_task_id::bigint = $1
    ORDER BY created_at, id
"#;

const ACTION_FOR_UPDATE_SQL: &str = r#"
    SELECT id::bigint AS id, llm_task_id::bigint AS llm_task_id, action_name,
           action_status, approval_policy, approval_required, action_input, action_result,
           rationale, idempotency_key, approved_by_user_id::bigint AS approved_by_user_id,
           error_message, created_at, approved_at, started_at, completed_at
    FROM llm_task_actions
    WHERE id::bigint = $2 AND llm_task_id::bigint = $1
    FOR UPDATE
"#;

impl From<ActionRow> for LlmTaskActionProjection {
    fn from(row: ActionRow) -> Self {
        Self {
            id: row.id,
            llm_task_id: row.llm_task_id,
            action_name: row.action_name,
            action_status: row.action_status,
            approval_policy: row.approval_policy,
            approval_required: row.approval_required,
            action_input: json_object(row.action_input),
            action_result: json_object(row.action_result),
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

#[derive(Debug, Error)]
pub enum ShareActionRepositoryError {
    #[error("Share Action PostgreSQL operation failed")]
    Sqlx(#[from] sqlx::Error),
    #[error("Share Action chat persistence failed")]
    Chat(#[from] crate::chat::ChatRepositoryError),
    #[error("Share Action user is missing or inactive")]
    UserMissingOrInactive,
    #[error("Share Action task was not found")]
    TaskNotFound,
    #[error("LLM task is not a Share Action")]
    WrongTaskKind,
    #[error("Share Action task ownership does not match the queue owner")]
    OwnershipMismatch,
    #[error("Share Action task is missing {0}")]
    MissingTaskField(&'static str),
    #[error("Share Action action is not allowed: {0}")]
    ActionNotAllowed(String),
    #[error("unsupported Share Action approval policy: {0}")]
    UnsupportedApprovalPolicy(String),
    #[error("invalid persisted Share Action status: {0}")]
    InvalidActionStatus(String),
    #[error("Share Action row disappeared after it was written")]
    ActionMissingAfterWrite,
    #[error("Share Action action was not found")]
    ActionNotFound,
    #[error("prepared Share Action content is missing")]
    PreparedContentMissing,
}
