use axum::extract::rejection::{JsonRejection, PathRejection};
use axum::extract::{Extension, Path, State};
use axum::http::{HeaderMap, StatusCode};
use axum::routing::{get, post};
use axum::{Json, Router};
use newsly_contracts::{
    LlmTaskActionListResponse, LlmTaskActionRejectRequest, LlmTaskActionResponse,
    LlmTaskActionStatus, LlmTaskApprovalPolicy,
};
use newsly_db::{
    LlmTaskActionProjection, RejectLlmTaskActionOutcome, list_llm_task_actions_for_user,
    reject_llm_task_action as persist_rejection,
};

use crate::auth::AuthenticatedUser;
use crate::error::ApiError;
use crate::gateway::RouteOwnershipStamp;
use crate::write_support::{decode_json, internal_error, require_operation, verify_stamp};
use crate::{AppState, request_id_from_headers};

const REJECT_ACTION_OPERATION_ID: &str = "rejectLlmTaskAction";

pub(super) fn router() -> Router<AppState> {
    Router::new()
        .route("/api/llm-tasks/{task_id}/actions", get(list_task_actions))
        .route(
            "/api/llm-tasks/{task_id}/actions/{action_id}/approve",
            post(crate::share_actions::approve_share_action_callback),
        )
        .route(
            "/api/llm-tasks/{task_id}/actions/{action_id}/reject",
            post(reject_task_action),
        )
}

#[utoipa::path(
    get,
    path = "/api/llm-tasks/{task_id}/actions",
    operation_id = "listLlmTaskActions",
    tag = "llm-tasks",
    params(("task_id" = i64, Path, description = "LLM task ID")),
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, description = "Successful Response", body = LlmTaskActionListResponse),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 404, description = "LLM task not found", body = newsly_contracts::ErrorEnvelope),
        (status = 422, description = "Validation Error", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn list_task_actions(
    State(state): State<AppState>,
    headers: HeaderMap,
    path: Result<Path<i64>, PathRejection>,
    current_user: AuthenticatedUser,
) -> Result<Json<LlmTaskActionListResponse>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    let Path(task_id) = valid_task_path(path, &request_id)?;
    let actions = list_llm_task_actions_for_user(state.database.pool(), current_user.id, task_id)
        .await
        .map_err(|error| internal_error(error, &request_id))?
        .ok_or_else(|| not_found("LLM task", &request_id))?;
    let actions = actions
        .into_iter()
        .map(|action| present_action(action, &request_id))
        .collect::<Result<Vec<_>, _>>()?;
    Ok(Json(LlmTaskActionListResponse { actions }))
}

#[utoipa::path(
    post,
    path = "/api/llm-tasks/{task_id}/actions/{action_id}/reject",
    operation_id = "rejectLlmTaskAction",
    tag = "llm-tasks",
    params(
        ("task_id" = i64, Path, description = "LLM task ID"),
        ("action_id" = i64, Path, description = "LLM task action ID")
    ),
    request_body = LlmTaskActionRejectRequest,
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, description = "Successful Response", body = LlmTaskActionResponse),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 404, description = "LLM task or action not found", body = newsly_contracts::ErrorEnvelope),
        (status = 409, description = "Invalid action state or stale runtime owner", body = newsly_contracts::ErrorEnvelope),
        (status = 422, description = "Validation Error", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn reject_task_action(
    State(state): State<AppState>,
    headers: HeaderMap,
    path: Result<Path<(i64, i64)>, PathRejection>,
    current_user: AuthenticatedUser,
    Extension(stamp): Extension<RouteOwnershipStamp>,
    payload: Result<Json<LlmTaskActionRejectRequest>, JsonRejection>,
) -> Result<Json<LlmTaskActionResponse>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    require_operation(&stamp, REJECT_ACTION_OPERATION_ID, &request_id)?;
    let Path((task_id, action_id)) = valid_action_path(path, &request_id)?;
    let Json(payload) = decode_json(payload, &request_id)?;
    let reason = validate_reason(payload.reason, &request_id)?;

    let mut transaction = state
        .database
        .pool()
        .begin()
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    verify_stamp(&mut transaction, &stamp, &request_id).await?;
    let outcome = persist_rejection(
        &mut transaction,
        current_user.id,
        task_id,
        action_id,
        reason.as_deref(),
    )
    .await
    .map_err(|error| internal_error(error, &request_id))?;

    let projection = match outcome {
        RejectLlmTaskActionOutcome::Rejected(action) => action,
        RejectLlmTaskActionOutcome::InvalidStatus(status) => {
            return Err(ApiError::new(
                StatusCode::CONFLICT,
                "invalid_state",
                format!("Action cannot be rejected from status {status:?}"),
                request_id,
            ));
        }
        RejectLlmTaskActionOutcome::ActionNotFound => {
            return Err(not_found("LLM task action", &request_id));
        }
        RejectLlmTaskActionOutcome::TaskNotFound => {
            return Err(not_found("LLM task", &request_id));
        }
    };
    transaction
        .commit()
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    Ok(Json(present_action(projection, &request_id)?))
}

fn present_action(
    action: LlmTaskActionProjection,
    request_id: &str,
) -> Result<LlmTaskActionResponse, ApiError> {
    let action_status = LlmTaskActionStatus::try_from(action.action_status.as_str())
        .map_err(|error| internal_error(error, request_id))?;
    let approval_policy = LlmTaskApprovalPolicy::try_from(action.approval_policy.as_str())
        .map_err(|error| internal_error(error, request_id))?;
    Ok(LlmTaskActionResponse {
        id: action.id,
        llm_task_id: action.llm_task_id,
        action_name: action.action_name,
        action_status,
        approval_policy,
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

fn valid_task_path(
    path: Result<Path<i64>, PathRejection>,
    request_id: &str,
) -> Result<Path<i64>, ApiError> {
    let Path(task_id) =
        path.map_err(|rejection| validation_error(rejection.body_text(), request_id))?;
    if task_id <= 0 {
        return Err(validation_error(
            "task_id must be greater than zero",
            request_id,
        ));
    }
    Ok(Path(task_id))
}

fn valid_action_path(
    path: Result<Path<(i64, i64)>, PathRejection>,
    request_id: &str,
) -> Result<Path<(i64, i64)>, ApiError> {
    let Path((task_id, action_id)) =
        path.map_err(|rejection| validation_error(rejection.body_text(), request_id))?;
    if task_id <= 0 || action_id <= 0 {
        return Err(validation_error(
            "task_id and action_id must be greater than zero",
            request_id,
        ));
    }
    Ok(Path((task_id, action_id)))
}

fn validate_reason(reason: Option<String>, request_id: &str) -> Result<Option<String>, ApiError> {
    if reason
        .as_ref()
        .is_some_and(|value| value.chars().count() > 1_000)
    {
        return Err(validation_error(
            "reason must contain at most 1000 characters",
            request_id,
        ));
    }
    Ok(reason)
}

fn validation_error(message: impl Into<String>, request_id: &str) -> ApiError {
    ApiError::new(
        StatusCode::UNPROCESSABLE_ENTITY,
        "validation_error",
        "Request validation failed",
        request_id.to_owned(),
    )
    .with_details(
        serde_json::json!({"errors": [{"message": message.into()}]})
            .as_object()
            .expect("validation details are an object")
            .clone(),
    )
}

fn not_found(resource: &str, request_id: &str) -> ApiError {
    ApiError::new(
        StatusCode::NOT_FOUND,
        "not_found",
        format!("{resource} not found"),
        request_id.to_owned(),
    )
}
