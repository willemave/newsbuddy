use axum::Json;
use axum::extract::rejection::JsonRejection;
use axum::http::StatusCode;
use newsly_db::{RouteWriteFenceError, verify_route_write_fence};

use crate::error::ApiError;
use crate::gateway::RouteOwnershipStamp;

pub(super) fn decode_json<T>(
    payload: Result<Json<T>, JsonRejection>,
    request_id: &str,
) -> Result<Json<T>, ApiError> {
    payload.map_err(|rejection| {
        ApiError::new(
            StatusCode::UNPROCESSABLE_ENTITY,
            "validation_error",
            "Request validation failed",
            request_id.to_owned(),
        )
        .with_details(
            serde_json::json!({"errors": [{"message": rejection.body_text()}]})
                .as_object()
                .expect("validation details are an object")
                .clone(),
        )
    })
}

pub(super) fn require_operation(
    stamp: &RouteOwnershipStamp,
    operation_id: &str,
    request_id: &str,
) -> Result<(), ApiError> {
    if stamp.operation_id == operation_id {
        Ok(())
    } else {
        Err(stale_owner(request_id))
    }
}

pub(super) async fn verify_stamp(
    transaction: &mut sqlx::Transaction<'_, sqlx::Postgres>,
    stamp: &RouteOwnershipStamp,
    request_id: &str,
) -> Result<(), ApiError> {
    verify_route_write_fence(transaction, &stamp.operation_id, stamp.owner, stamp.version)
        .await
        .map_err(|error| match error {
            RouteWriteFenceError::Missing(_) | RouteWriteFenceError::Stale { .. } => {
                stale_owner(request_id)
            }
            RouteWriteFenceError::Sqlx(source) => internal_error(source, request_id),
        })
}

pub(super) fn bad_request(message: impl Into<String>, request_id: &str) -> ApiError {
    ApiError::new(
        StatusCode::BAD_REQUEST,
        "bad_request",
        message,
        request_id.to_owned(),
    )
}

pub(super) fn not_found(resource: &str, request_id: &str) -> ApiError {
    ApiError::new(
        StatusCode::NOT_FOUND,
        "not_found",
        format!("{resource} not found"),
        request_id.to_owned(),
    )
}

pub(super) fn stale_owner(request_id: &str) -> ApiError {
    ApiError::new(
        StatusCode::CONFLICT,
        "stale_ownership",
        "This Rust runtime no longer owns the operation version",
        request_id.to_owned(),
    )
    .with_retryable(true)
}

pub(super) fn internal_error(error: impl std::fmt::Display, request_id: &str) -> ApiError {
    tracing::error!(error = %error, "mutation operation failed");
    ApiError::new(
        StatusCode::INTERNAL_SERVER_ERROR,
        "internal_error",
        "Internal server error",
        request_id.to_owned(),
    )
    .with_retryable(true)
}
