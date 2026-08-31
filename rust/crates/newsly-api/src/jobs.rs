use axum::extract::rejection::PathRejection;
use axum::extract::{Path, State};
use axum::http::{HeaderMap, StatusCode};
use axum::routing::get;
use axum::{Json, Router};
use newsly_contracts::JobStatusResponse;
use newsly_db::find_job_for_user;

use crate::auth::AuthenticatedUser;
use crate::error::ApiError;
use crate::{AppState, request_id_from_headers};

pub(super) fn router() -> Router<AppState> {
    Router::new().route("/api/jobs/{job_id}", get(get_job))
}

#[utoipa::path(
    get,
    path = "/api/jobs/{job_id}",
    operation_id = "getJob",
    tag = "agent",
    params(("job_id" = i64, Path, description = "Durable processing task identifier")),
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, description = "Successful Response", body = JobStatusResponse),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 404, description = "Job not found", body = newsly_contracts::ErrorEnvelope),
        (status = 422, description = "Validation Error", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn get_job(
    State(state): State<AppState>,
    headers: HeaderMap,
    path: Result<Path<i64>, PathRejection>,
    current_user: AuthenticatedUser,
) -> Result<Json<JobStatusResponse>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    let Path(job_id) = path.map_err(|rejection| {
        ApiError::new(
            StatusCode::UNPROCESSABLE_ENTITY,
            "validation_error",
            "Request validation failed",
            request_id.clone(),
        )
        .with_details(
            serde_json::json!({"errors": [{"message": rejection.body_text()}]})
                .as_object()
                .expect("validation details are an object")
                .clone(),
        )
    })?;

    let job = find_job_for_user(state.database.pool(), job_id, current_user.id)
        .await
        .map_err(|error| {
            tracing::error!(error = %error, job_id, user_id = current_user.id, "job lookup failed");
            ApiError::new(
                StatusCode::INTERNAL_SERVER_ERROR,
                "internal_error",
                "Internal server error",
                request_id.clone(),
            )
            .with_retryable(true)
        })?
        .ok_or_else(|| {
            ApiError::new(
                StatusCode::NOT_FOUND,
                "not_found",
                "Job not found",
                request_id,
            )
        })?;

    Ok(Json(JobStatusResponse {
        id: job.id,
        task_type: job.task_type,
        status: job.status,
        queue_name: job.queue_name,
        content_id: job.content_id,
        payload: job.payload,
        retry_count: job.retry_count,
        created_at: job.created_at,
        started_at: job.started_at,
        completed_at: job.completed_at,
        error_message: job.error_message,
    }))
}
