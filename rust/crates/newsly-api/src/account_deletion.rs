use axum::extract::rejection::JsonRejection;
use axum::extract::{Extension, State};
use axum::http::{HeaderMap, StatusCode};
use axum::routing::delete;
use axum::{Json, Router};
use newsly_contracts::{DeleteAccountRequest, DeleteAccountResponse};
use newsly_db::deactivate_active_user;
use newsly_queue::{EnqueueRequest, QueueKernel, TaskType};
use serde_json::{Map, Value};

use crate::auth::AuthenticatedUser;
use crate::error::ApiError;
use crate::gateway::RouteOwnershipStamp;
use crate::write_support::{decode_json, internal_error, require_operation, verify_stamp};
use crate::{AppState, request_id_from_headers};

const DELETE_ACCOUNT_OPERATION_ID: &str = "deleteMeCurrentAccount";

pub(super) fn router() -> Router<AppState> {
    Router::new().route("/auth/me", delete(delete_current_account))
}

#[utoipa::path(
    delete,
    path = "/auth/me",
    operation_id = "deleteMeCurrentAccount",
    tag = "auth",
    request_body = DeleteAccountRequest,
    security(("HTTPBearer" = [])),
    responses(
        (status = 202, description = "Deletion scheduled", body = DeleteAccountResponse),
        (status = 400, description = "Fresh Apple authorization is invalid", body = newsly_contracts::ErrorEnvelope),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 403, description = "Authentication required", body = newsly_contracts::ErrorEnvelope),
        (status = 409, description = "Stale runtime owner", body = newsly_contracts::ErrorEnvelope),
        (status = 422, description = "Validation Error", body = newsly_contracts::ErrorEnvelope),
        (status = 503, description = "Deletion could not be scheduled", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn delete_current_account(
    State(state): State<AppState>,
    headers: HeaderMap,
    current_user: AuthenticatedUser,
    Extension(stamp): Extension<RouteOwnershipStamp>,
    payload: Result<Json<DeleteAccountRequest>, JsonRejection>,
) -> Result<(StatusCode, Json<DeleteAccountResponse>), ApiError> {
    let request_id = request_id_from_headers(&headers);
    require_operation(&stamp, DELETE_ACCOUNT_OPERATION_ID, &request_id)?;
    let Json(payload) = decode_json(payload, &request_id)?;
    if payload.id_token.is_empty() || payload.authorization_code.is_empty() {
        return Err(ApiError::new(
            StatusCode::UNPROCESSABLE_ENTITY,
            "validation_error",
            "id_token and authorization_code must be nonempty",
            request_id,
        ));
    }

    // Complete remote identity verification and grant revocation before the
    // short transaction that changes account state and hands work to the queue.
    let identity = state
        .auth
        .verify_apple_identity(&payload.id_token)
        .await
        .map_err(|error| apple_reauthentication_error(error, &request_id))?;
    if identity.subject != current_user.apple_id {
        return Err(ApiError::new(
            StatusCode::BAD_REQUEST,
            "apple_account_mismatch",
            "Apple account does not match the signed-in user",
            request_id,
        ));
    }
    state
        .auth
        .exchange_and_revoke_apple_authorization(&payload.authorization_code)
        .await
        .map_err(|error| {
            tracing::warn!(error = %error, user_id = current_user.id, "Apple authorization revocation failed");
            ApiError::new(
                StatusCode::BAD_REQUEST,
                "apple_revocation_failed",
                "Apple authorization could not be revoked",
                request_id.clone(),
            )
        })?;

    let mut transaction = state
        .database
        .pool()
        .begin()
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    verify_stamp(&mut transaction, &stamp, &request_id).await?;
    let deactivated = deactivate_active_user(&mut transaction, current_user.id)
        .await
        .map_err(|error| deletion_unavailable(error, &request_id))?;
    if !deactivated {
        return Err(deletion_unavailable(
            "account is no longer active",
            &request_id,
        ));
    }
    let mut task_payload = Map::new();
    task_payload.insert("user_id".to_owned(), Value::from(current_user.id));
    let mut enqueue = EnqueueRequest::new(TaskType::DeleteUserAccount);
    enqueue.payload = Some(task_payload);
    enqueue.dedupe_key = Some(format!("delete-user-account:{}", current_user.id));
    QueueKernel::new(state.database.pool().clone())
        .enqueue_many_in_transaction(&mut transaction, vec![enqueue])
        .await
        .map_err(|error| deletion_unavailable(error, &request_id))?;
    transaction
        .commit()
        .await
        .map_err(|error| deletion_unavailable(error, &request_id))?;
    tracing::info!(
        user_id = current_user.id,
        operation_id = DELETE_ACCOUNT_OPERATION_ID,
        "account deletion scheduled"
    );
    Ok((
        StatusCode::ACCEPTED,
        Json(DeleteAccountResponse {
            status: "deletion_scheduled".to_owned(),
        }),
    ))
}

fn apple_reauthentication_error(error: impl std::fmt::Display, request_id: &str) -> ApiError {
    tracing::warn!(error = %error, "Apple account reauthentication failed");
    ApiError::new(
        StatusCode::BAD_REQUEST,
        "invalid_apple_token",
        "Invalid Apple token",
        request_id.to_owned(),
    )
}

fn deletion_unavailable(error: impl std::fmt::Display, request_id: &str) -> ApiError {
    tracing::error!(error = %error, "account deletion could not be scheduled");
    ApiError::new(
        StatusCode::SERVICE_UNAVAILABLE,
        "deletion_unavailable",
        "Account deletion could not be scheduled; try again",
        request_id.to_owned(),
    )
    .with_retryable(true)
}
