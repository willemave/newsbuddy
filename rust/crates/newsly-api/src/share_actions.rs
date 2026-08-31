use axum::extract::rejection::{JsonRejection, PathRejection};
use axum::extract::{Extension, Path, State};
use axum::http::{HeaderMap, StatusCode};
use axum::routing::{get, post};
use axum::{Json, Router};
use newsly_contracts::{
    LlmTaskActionResponse, LlmTaskActionStatus, LlmTaskApprovalPolicy, LlmTaskMode, LlmTaskStatus,
    ShareActionCreateRequest, ShareActionResponse,
};
use newsly_db::{
    ApproveShareActionOutcome, LlmTaskActionProjection, NewShareActionTask,
    ShareActionRepositoryError, ShareActionTaskProjection,
    approve_share_action as persist_share_action_approval, fail_share_action_approval,
    fail_share_action_task, find_share_action_for_user, finish_share_action_task,
    insert_share_action_task, load_share_action_action, mark_share_action_applied,
    mark_share_action_applying, mark_share_action_failed,
};
use newsly_queue::{EnqueueRequest, QueueKernel, TaskType};
use newsly_worker::share_actions::{apply_share_action_host_action, parse_stored_host_input};
use serde_json::{Map, Value, json};

use crate::auth::AuthenticatedUser;
use crate::error::ApiError;
use crate::gateway::RouteOwnershipStamp;
use crate::write_support::{decode_json, internal_error, require_operation, verify_stamp};
use crate::{AppState, request_id_from_headers};

const CREATE_SHARE_ACTION_OPERATION_ID: &str = "createShareAction";
const APPROVE_ACTION_OPERATION_ID: &str = "approveLlmTaskAction";
const MAX_HTTP_URL_CHARS: usize = 2_083;
const SANDBOX_ROOT: &str = "/data/workspace";

pub(super) fn router() -> Router<AppState> {
    Router::new()
        .route("/api/share-actions", post(create_share_action))
        .route("/api/share-actions/{task_id}", get(get_share_action))
}

#[utoipa::path(
    post,
    path = "/api/share-actions",
    operation_id = "createShareAction",
    tag = "share-actions",
    summary = "Create a VM-backed ShareSheet action",
    request_body = ShareActionCreateRequest,
    security(("HTTPBearer" = [])),
    responses(
        (status = 202, description = "Share Action accepted", body = ShareActionResponse),
        (status = 400, description = "Share Action could not be created", body = newsly_contracts::ErrorEnvelope),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 409, description = "Stale runtime owner", body = newsly_contracts::ErrorEnvelope),
        (status = 422, description = "Validation Error", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn create_share_action(
    State(state): State<AppState>,
    headers: HeaderMap,
    current_user: AuthenticatedUser,
    Extension(stamp): Extension<RouteOwnershipStamp>,
    payload: Result<Json<ShareActionCreateRequest>, JsonRejection>,
) -> Result<(StatusCode, Json<ShareActionResponse>), ApiError> {
    let request_id = request_id_from_headers(&headers);
    require_operation(&stamp, CREATE_SHARE_ACTION_OPERATION_ID, &request_id)?;
    let Json(payload) = decode_json(payload, &request_id)?;
    let normalized = NormalizedShareAction::try_from_request(payload, &request_id)?;

    let mut transaction = state
        .database
        .pool()
        .begin()
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    verify_stamp(&mut transaction, &stamp, &request_id).await?;
    let created = insert_share_action_task(
        &mut transaction,
        &NewShareActionTask {
            user_id: current_user.id,
            mode: normalized.mode.as_str(),
            approval_policy: &normalized.approval_policy,
            allowed_action: host_action_name(normalized.mode),
            input: &normalized.input,
            sandbox_root: SANDBOX_ROOT,
        },
    )
    .await
    .map_err(|error| repository_error(error, &request_id))?;

    let queue = QueueKernel::new(state.database.pool().clone());
    let mut request = EnqueueRequest::new(TaskType::RunLlmTask);
    request.payload = json!({
        "llm_task_id": created.id,
        "user_id": current_user.id,
    })
    .as_object()
    .cloned();
    request.owner_user_id = Some(current_user.id);
    queue
        .enqueue_many_in_transaction(&mut transaction, vec![request])
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    transaction
        .commit()
        .await
        .map_err(|error| internal_error(error, &request_id))?;

    Ok((
        StatusCode::ACCEPTED,
        Json(ShareActionResponse {
            task_id: created.id,
            mode: normalized.mode,
            status: LlmTaskStatus::Queued,
            workflow_state: "queued".to_owned(),
            created_at: created.created_at,
            actions: Vec::new(),
        }),
    ))
}

#[utoipa::path(
    get,
    path = "/api/share-actions/{task_id}",
    operation_id = "getShareAction",
    tag = "share-actions",
    summary = "Get one ShareSheet action task",
    params(("task_id" = i64, Path, description = "Share Action task ID")),
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, description = "Successful Response", body = ShareActionResponse),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 404, description = "Share Action not found", body = newsly_contracts::ErrorEnvelope),
        (status = 422, description = "Validation Error", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn get_share_action(
    State(state): State<AppState>,
    headers: HeaderMap,
    path: Result<Path<i64>, PathRejection>,
    current_user: AuthenticatedUser,
) -> Result<Json<ShareActionResponse>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    let Path(task_id) =
        path.map_err(|rejection| validation_error(rejection.body_text(), &request_id))?;
    if task_id <= 0 {
        return Err(validation_error(
            "task_id must be greater than zero",
            &request_id,
        ));
    }
    let task = find_share_action_for_user(state.database.pool(), current_user.id, task_id)
        .await
        .map_err(|error| internal_error(error, &request_id))?
        .ok_or_else(|| not_found(&request_id))?;
    Ok(Json(present_share_action(task, &request_id)?))
}

/// Approve an action that is waiting for user confirmation.
#[utoipa::path(
    post,
    path = "/api/llm-tasks/{task_id}/actions/{action_id}/approve",
    operation_id = "approveLlmTaskAction",
    tag = "llm-tasks",
    params(
        ("task_id" = i64, Path, description = "LLM task ID"),
        ("action_id" = i64, Path, description = "LLM task action ID")
    ),
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, description = "Successful Response", body = LlmTaskActionResponse),
        (status = 400, description = "Share Action application failed", body = newsly_contracts::ErrorEnvelope),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 404, description = "LLM task or action not found", body = newsly_contracts::ErrorEnvelope),
        (status = 409, description = "Invalid action state or stale runtime owner", body = newsly_contracts::ErrorEnvelope),
        (status = 422, description = "Validation Error", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
#[expect(
    clippy::too_many_lines,
    reason = "approval keeps action validation and exact task-attempt fencing in one visible flow"
)]
pub(super) async fn approve_share_action_callback(
    State(state): State<AppState>,
    headers: HeaderMap,
    path: Result<Path<(i64, i64)>, PathRejection>,
    current_user: AuthenticatedUser,
    Extension(stamp): Extension<RouteOwnershipStamp>,
) -> Result<Json<LlmTaskActionResponse>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    require_operation(&stamp, APPROVE_ACTION_OPERATION_ID, &request_id)?;
    let Path((task_id, action_id)) =
        path.map_err(|rejection| validation_error(rejection.body_text(), &request_id))?;
    if task_id <= 0 || action_id <= 0 {
        return Err(validation_error(
            "task_id and action_id must be greater than zero",
            &request_id,
        ));
    }

    let mut transaction = state
        .database
        .pool()
        .begin()
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    verify_stamp(&mut transaction, &stamp, &request_id).await?;
    let approved =
        persist_share_action_approval(&mut transaction, current_user.id, task_id, action_id)
            .await
            .map_err(|error| internal_error(error, &request_id))?;
    let (task, action) = match approved {
        ApproveShareActionOutcome::Approved { task, action } => (task, action),
        ApproveShareActionOutcome::ApprovedGeneric { action } => {
            transaction
                .commit()
                .await
                .map_err(|error| internal_error(error, &request_id))?;
            return Ok(Json(present_action(action, &request_id)?));
        }
        ApproveShareActionOutcome::InvalidStatus(status) => {
            return Err(conflict(
                format!("Action cannot be approved from status {status:?}"),
                &request_id,
            ));
        }
        ApproveShareActionOutcome::ActionNotFound => {
            return Err(resource_not_found("LLM task action", &request_id));
        }
        ApproveShareActionOutcome::TaskNotFound => {
            return Err(resource_not_found("LLM task", &request_id));
        }
    };

    let input = match parse_stored_host_input(&task.mode, &action.action_name, &action.action_input)
    {
        Ok(input) => input,
        Err(error) => {
            drop(transaction);
            return persist_approval_failure(
                &state,
                &stamp,
                current_user.id,
                task_id,
                action_id,
                "ShareActionResultValidationError",
                &error.to_string(),
                None,
                &request_id,
            )
            .await;
        }
    };
    mark_share_action_applying(&mut transaction, action_id)
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    let queue = QueueKernel::new(state.database.pool().clone());
    let applied = apply_share_action_host_action(
        &mut transaction,
        &queue,
        SANDBOX_ROOT,
        &task,
        &action.action_name,
        &input,
    )
    .await;
    let result = match applied {
        Ok(result) => result,
        Err(error) => {
            drop(transaction);
            return persist_approval_failure(
                &state,
                &stamp,
                current_user.id,
                task_id,
                action_id,
                "ShareActionApplicationError",
                &error.to_string(),
                None,
                &request_id,
            )
            .await;
        }
    };
    if result.get("outcome").and_then(Value::as_str) == Some("failed") {
        let message = result
            .get("error")
            .and_then(Value::as_str)
            .unwrap_or("Share Action applied no items")
            .to_owned();
        mark_share_action_failed(&mut transaction, action_id, Some(&result), &message)
            .await
            .map_err(|error| internal_error(error, &request_id))?;
        fail_share_action_task(
            &mut transaction,
            task_id,
            current_user.id,
            "ShareActionApplicationError",
            &message,
            None,
            None,
        )
        .await
        .map_err(|error| internal_error(error, &request_id))?;
        transaction
            .commit()
            .await
            .map_err(|error| internal_error(error, &request_id))?;
        return Err(application_error(message, &request_id));
    }
    mark_share_action_applied(&mut transaction, action_id, &result)
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    finish_share_action_task(&mut transaction, task_id)
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    let projection = load_share_action_action(&mut transaction, task_id, action_id)
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    transaction
        .commit()
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    Ok(Json(present_action(projection, &request_id)?))
}

#[allow(clippy::too_many_arguments)]
async fn persist_approval_failure(
    state: &AppState,
    stamp: &RouteOwnershipStamp,
    user_id: i64,
    task_id: i64,
    action_id: i64,
    error_type: &str,
    message: &str,
    action_result: Option<&Map<String, Value>>,
    request_id: &str,
) -> Result<Json<LlmTaskActionResponse>, ApiError> {
    let mut transaction = state
        .database
        .pool()
        .begin()
        .await
        .map_err(|error| internal_error(error, request_id))?;
    verify_stamp(&mut transaction, stamp, request_id).await?;
    fail_share_action_approval(
        &mut transaction,
        user_id,
        task_id,
        action_id,
        error_type,
        message,
        action_result,
    )
    .await
    .map_err(|error| internal_error(error, request_id))?;
    transaction
        .commit()
        .await
        .map_err(|error| internal_error(error, request_id))?;
    Err(application_error(message, request_id))
}

#[derive(Debug)]
struct NormalizedShareAction {
    mode: LlmTaskMode,
    approval_policy: Map<String, Value>,
    input: Map<String, Value>,
}

impl NormalizedShareAction {
    fn try_from_request(
        payload: ShareActionCreateRequest,
        request_id: &str,
    ) -> Result<Self, ApiError> {
        if !payload.mode.is_share_action() {
            return Err(validation_error(
                format!("Unsupported share action mode: {}", payload.mode.as_str()),
                request_id,
            ));
        }
        let url = normalize_http_url(&payload.url, request_id)?;
        validate_max_chars(
            payload.instruction.as_deref(),
            4_000,
            "instruction",
            request_id,
        )?;
        validate_max_chars(
            payload.chat_initial_message.as_deref(),
            2_000,
            "chat_initial_message",
            request_id,
        )?;
        validate_max_chars(
            payload.interests_prompt.as_deref(),
            4_000,
            "interests_prompt",
            request_id,
        )?;
        let approval_policy = payload.approval_policy.map_or_else(
            || Map::from_iter([("default".to_owned(), Value::from("auto_apply"))]),
            |policy| {
                policy
                    .into_iter()
                    .map(|(key, value)| (key, Value::from(approval_policy_value(value))))
                    .collect()
            },
        );
        let input = Map::from_iter([
            ("url".to_owned(), Value::from(url)),
            ("mode".to_owned(), Value::from(payload.mode.as_str())),
            ("instruction".to_owned(), option_string(payload.instruction)),
            (
                "chat_initial_message".to_owned(),
                option_string(payload.chat_initial_message),
            ),
            (
                "interests_prompt".to_owned(),
                option_string(payload.interests_prompt),
            ),
        ]);
        Ok(Self {
            mode: payload.mode,
            approval_policy,
            input,
        })
    }
}

fn present_share_action(
    task: ShareActionTaskProjection,
    request_id: &str,
) -> Result<ShareActionResponse, ApiError> {
    Ok(ShareActionResponse {
        task_id: task.id,
        mode: LlmTaskMode::try_from(task.mode.as_str())
            .map_err(|error| internal_error(error, request_id))?,
        status: LlmTaskStatus::try_from(task.status.as_str())
            .map_err(|error| internal_error(error, request_id))?,
        workflow_state: task.workflow_state,
        created_at: task.created_at,
        actions: task
            .actions
            .into_iter()
            .map(|action| present_action(action, request_id))
            .collect::<Result<Vec<_>, _>>()?,
    })
}

fn present_action(
    action: LlmTaskActionProjection,
    request_id: &str,
) -> Result<LlmTaskActionResponse, ApiError> {
    Ok(LlmTaskActionResponse {
        id: action.id,
        llm_task_id: action.llm_task_id,
        action_name: action.action_name,
        action_status: LlmTaskActionStatus::try_from(action.action_status.as_str())
            .map_err(|error| internal_error(error, request_id))?,
        approval_policy: LlmTaskApprovalPolicy::try_from(action.approval_policy.as_str())
            .map_err(|error| internal_error(error, request_id))?,
        approval_required: action.approval_required,
        action_input: action.action_input,
        action_result: action.action_result,
        rationale: action.rationale,
        idempotency_key: action.idempotency_key,
        approved_by_user_id: action.approved_by_user_id,
        error_message: action.error_message,
        created_at: action.created_at,
        approved_at: action.approved_at,
        started_at: action.started_at,
        completed_at: action.completed_at,
    })
}

fn host_action_name(mode: LlmTaskMode) -> &'static str {
    match mode {
        LlmTaskMode::AddContent => "add_content",
        LlmTaskMode::AddToBriefing => "add_to_briefing",
        LlmTaskMode::AddLinks => "add_links",
        LlmTaskMode::AddFeed => "subscribe_to_feed",
        LlmTaskMode::Chat => "enqueue_chat",
        LlmTaskMode::Presentation => "create_learning_deck",
        LlmTaskMode::BookmarkOnly => "save_to_knowledge",
        LlmTaskMode::ArticleChat
        | LlmTaskMode::ContextualAssistant
        | LlmTaskMode::LearningDeckPresentation
        | LlmTaskMode::Generic => unreachable!("validated Share Action mode"),
    }
}

const fn approval_policy_value(policy: LlmTaskApprovalPolicy) -> &'static str {
    match policy {
        LlmTaskApprovalPolicy::AutoApply => "auto_apply",
        LlmTaskApprovalPolicy::ApprovalRequired => "approval_required",
        LlmTaskApprovalPolicy::DryRun => "dry_run",
    }
}

fn normalize_http_url(value: &str, request_id: &str) -> Result<String, ApiError> {
    let value = value.trim();
    if value.chars().count() > MAX_HTTP_URL_CHARS {
        return Err(validation_error(
            "url must contain at most 2083 characters",
            request_id,
        ));
    }
    let parsed = reqwest::Url::parse(value)
        .map_err(|_| validation_error("url must be a valid HTTP URL", request_id))?;
    if !matches!(parsed.scheme(), "http" | "https") || parsed.host().is_none() {
        return Err(validation_error(
            "url must use http or https and include a host",
            request_id,
        ));
    }
    Ok(parsed.to_string())
}

fn validate_max_chars(
    value: Option<&str>,
    maximum: usize,
    field: &str,
    request_id: &str,
) -> Result<(), ApiError> {
    if value.is_some_and(|value| value.chars().count() > maximum) {
        return Err(validation_error(
            format!("{field} must contain at most {maximum} characters"),
            request_id,
        ));
    }
    Ok(())
}

fn option_string(value: Option<String>) -> Value {
    value.map_or(Value::Null, Value::from)
}

fn repository_error(error: ShareActionRepositoryError, request_id: &str) -> ApiError {
    match error {
        ShareActionRepositoryError::UserMissingOrInactive => ApiError::new(
            StatusCode::BAD_REQUEST,
            "bad_request",
            "Share Action user not found",
            request_id.to_owned(),
        ),
        other => internal_error(other, request_id),
    }
}

fn validation_error(message: impl Into<String>, request_id: &str) -> ApiError {
    ApiError::new(
        StatusCode::UNPROCESSABLE_ENTITY,
        "validation_error",
        "Request validation failed",
        request_id.to_owned(),
    )
    .with_details(
        json!({"errors": [{"message": message.into()}]})
            .as_object()
            .expect("validation details are an object")
            .clone(),
    )
}

fn not_found(request_id: &str) -> ApiError {
    ApiError::new(
        StatusCode::NOT_FOUND,
        "not_found",
        "Share Action not found",
        request_id.to_owned(),
    )
}

fn resource_not_found(resource: &str, request_id: &str) -> ApiError {
    ApiError::new(
        StatusCode::NOT_FOUND,
        "not_found",
        format!("{resource} not found"),
        request_id.to_owned(),
    )
}

fn conflict(message: impl Into<String>, request_id: &str) -> ApiError {
    ApiError::new(
        StatusCode::CONFLICT,
        "invalid_state",
        message,
        request_id.to_owned(),
    )
}

fn application_error(message: impl Into<String>, request_id: &str) -> ApiError {
    ApiError::new(
        StatusCode::BAD_REQUEST,
        "bad_request",
        message,
        request_id.to_owned(),
    )
}
