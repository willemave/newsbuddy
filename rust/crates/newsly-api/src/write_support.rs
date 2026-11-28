use axum::Json;
use axum::extract::Path;
use axum::extract::rejection::{JsonRejection, PathRejection};
use axum::http::header::HOST;
use axum::http::{HeaderMap, StatusCode};
use newsly_db::{RouteWriteFenceError, verify_route_write_fence};
use reqwest::Url;

use crate::AppState;
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

pub(super) async fn verify_route_now(
    pool: &sqlx::PgPool,
    stamp: &RouteOwnershipStamp,
    request_id: &str,
) -> Result<(), ApiError> {
    let mut transaction = pool
        .begin()
        .await
        .map_err(|error| internal_error(error, request_id))?;
    verify_stamp(&mut transaction, stamp, request_id).await?;
    transaction
        .commit()
        .await
        .map_err(|error| internal_error(error, request_id))
}

pub(super) async fn begin_fenced<'a>(
    pool: &'a sqlx::PgPool,
    stamp: &RouteOwnershipStamp,
    request_id: &str,
) -> Result<sqlx::Transaction<'a, sqlx::Postgres>, ApiError> {
    let mut transaction = pool
        .begin()
        .await
        .map_err(|error| internal_error(error, request_id))?;
    verify_stamp(&mut transaction, stamp, request_id).await?;
    Ok(transaction)
}

pub(super) async fn commit(
    transaction: sqlx::Transaction<'_, sqlx::Postgres>,
    request_id: &str,
) -> Result<(), ApiError> {
    transaction
        .commit()
        .await
        .map_err(|error| internal_error(error, request_id))
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

pub(super) fn positive_path_id(
    path: Result<Path<i64>, PathRejection>,
    field: &str,
    request_id: &str,
) -> Result<i64, ApiError> {
    let Path(id) =
        path.map_err(|rejection| ApiError::validation(rejection.body_text(), request_id))?;
    if id <= 0 {
        return Err(ApiError::validation(
            format!("{field} must be greater than zero"),
            request_id,
        ));
    }
    Ok(id)
}

pub(super) fn external_url(
    state: &AppState,
    headers: &HeaderMap,
    path: &str,
    request_id: &str,
) -> Result<String, ApiError> {
    let mut base = if let Some(configured) = &state.public_base_url {
        configured.clone()
    } else {
        if state.is_production {
            return Err(internal_error(
                "PUBLIC_BASE_URL is required in production",
                request_id,
            ));
        }
        let host = headers
            .get(HOST)
            .and_then(|value| value.to_str().ok())
            .ok_or_else(|| internal_error("request Host header is missing", request_id))?;
        let scheme = headers
            .get("x-forwarded-proto")
            .and_then(|value| value.to_str().ok())
            .and_then(|value| value.split(',').next())
            .map(str::trim)
            .filter(|value| matches!(*value, "http" | "https"))
            .unwrap_or("http");
        Url::parse(&format!("{scheme}://{host}"))
            .map_err(|error| internal_error(error, request_id))?
    };
    base.set_path(path);
    base.set_query(None);
    base.set_fragment(None);
    Ok(base.to_string())
}

pub(super) fn internal_error(error: impl std::fmt::Display, request_id: &str) -> ApiError {
    tracing::error!(request_id = %request_id, error = %error, "API operation failed");
    ApiError::new(
        StatusCode::INTERNAL_SERVER_ERROR,
        "internal_error",
        "Internal server error",
        request_id.to_owned(),
    )
    .with_retryable(true)
}
