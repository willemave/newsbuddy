use axum::extract::rejection::JsonRejection;
use axum::extract::{Extension, State};
use axum::http::{HeaderMap, StatusCode};
use axum::routing::post;
use axum::{Json, Router};
use chrono::Utc;
use newsly_contracts::{
    OperationStatus, RecordContentInteractionRequest, RecordContentInteractionResponse,
    SubmitFeedbackRequest, SubmitFeedbackResponse,
};
use newsly_db::{
    InteractionRepositoryError, NewContentInteraction, NewFeedback, insert_content_interaction,
    insert_feedback,
};

use crate::auth::AuthenticatedUser;
use crate::error::ApiError;
use crate::gateway::RouteOwnershipStamp;
use crate::write_support::{
    bad_request, decode_json, internal_error, require_operation, verify_stamp,
};
use crate::{AppState, request_id_from_headers};

const FEEDBACK_OPERATION_ID: &str = "createFeedback";
const ANALYTICS_OPERATION_ID: &str = "postAnalyticsContentInteraction";

pub(super) fn router() -> Router<AppState> {
    Router::new()
        .route("/api/feedback", post(create_feedback))
        .route("/api/analytics", post(record_content_interaction))
}

#[utoipa::path(
    post,
    path = "/api/feedback",
    operation_id = "createFeedback",
    tag = "feedback",
    request_body = SubmitFeedbackRequest,
    security(("HTTPBearer" = [])),
    responses(
        (status = 201, description = "Successful Response", body = SubmitFeedbackResponse),
        (status = 400, description = "Invalid feedback", body = newsly_contracts::ErrorEnvelope),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 403, description = "Authentication required", body = newsly_contracts::ErrorEnvelope),
        (status = 409, description = "Stale runtime owner", body = newsly_contracts::ErrorEnvelope),
        (status = 422, description = "Validation Error", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn create_feedback(
    State(state): State<AppState>,
    headers: HeaderMap,
    current_user: AuthenticatedUser,
    Extension(stamp): Extension<RouteOwnershipStamp>,
    payload: Result<Json<SubmitFeedbackRequest>, JsonRejection>,
) -> Result<(StatusCode, Json<SubmitFeedbackResponse>), ApiError> {
    let request_id = request_id_from_headers(&headers);
    require_operation(&stamp, FEEDBACK_OPERATION_ID, &request_id)?;
    let Json(mut payload) = decode_json(payload, &request_id)?;
    payload.message = clean_required(&payload.message, 4_000, "Feedback message", &request_id)?;
    payload.source = clean_required(&payload.source, 64, "Feedback source", &request_id)?;
    payload.app_version = clean_optional(payload.app_version, 64, "app_version", &request_id)?;
    payload.build_number = clean_optional(payload.build_number, 64, "build_number", &request_id)?;
    payload.platform = clean_optional(payload.platform, 64, "platform", &request_id)?;
    payload.os_version = clean_optional(payload.os_version, 128, "os_version", &request_id)?;
    payload.device_model = clean_optional(payload.device_model, 128, "device_model", &request_id)?;

    let mut transaction = state
        .database
        .pool()
        .begin()
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    verify_stamp(&mut transaction, &stamp, &request_id).await?;
    let feedback_id = insert_feedback(
        &mut transaction,
        &NewFeedback {
            user_id: current_user.id,
            message: &payload.message,
            source: &payload.source,
            app_version: payload.app_version.as_deref(),
            build_number: payload.build_number.as_deref(),
            platform: payload.platform.as_deref(),
            os_version: payload.os_version.as_deref(),
            device_model: payload.device_model.as_deref(),
        },
    )
    .await
    .map_err(|error| internal_error(error, &request_id))?;
    transaction
        .commit()
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    Ok((
        StatusCode::CREATED,
        Json(SubmitFeedbackResponse {
            status: OperationStatus::Success,
            feedback_id,
        }),
    ))
}

#[utoipa::path(
    post,
    path = "/api/analytics",
    operation_id = "postAnalyticsContentInteraction",
    tag = "analytics",
    request_body = RecordContentInteractionRequest,
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, description = "Successful Response", body = RecordContentInteractionResponse),
        (status = 400, description = "Invalid interaction", body = newsly_contracts::ErrorEnvelope),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 403, description = "Authentication required", body = newsly_contracts::ErrorEnvelope),
        (status = 404, description = "Content not found", body = newsly_contracts::ErrorEnvelope),
        (status = 409, description = "Stale runtime owner", body = newsly_contracts::ErrorEnvelope),
        (status = 422, description = "Validation Error", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn record_content_interaction(
    State(state): State<AppState>,
    headers: HeaderMap,
    current_user: AuthenticatedUser,
    Extension(stamp): Extension<RouteOwnershipStamp>,
    payload: Result<Json<RecordContentInteractionRequest>, JsonRejection>,
) -> Result<Json<RecordContentInteractionResponse>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    require_operation(&stamp, ANALYTICS_OPERATION_ID, &request_id)?;
    let Json(mut payload) = decode_json(payload, &request_id)?;
    payload.interaction_id =
        clean_required(&payload.interaction_id, 64, "interaction_id", &request_id)?;
    if payload.content_id <= 0 {
        return Err(bad_request(
            "content_id must be greater than zero",
            &request_id,
        ));
    }
    payload.surface = clean_optional(payload.surface, 64, "surface", &request_id)?;
    let occurred_at = payload.occurred_at.unwrap_or_else(Utc::now).naive_utc();

    let mut transaction = state
        .database
        .pool()
        .begin()
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    verify_stamp(&mut transaction, &stamp, &request_id).await?;
    let result = insert_content_interaction(
        &mut transaction,
        &NewContentInteraction {
            user_id: current_user.id,
            content_id: payload.content_id,
            interaction_id: &payload.interaction_id,
            interaction_type: payload.interaction_type.as_str(),
            occurred_at,
            surface: payload.surface.as_deref(),
            context_data: payload.context_data,
        },
    )
    .await
    .map_err(|error| match error {
        InteractionRepositoryError::ContentNotFound(_) => ApiError::new(
            StatusCode::NOT_FOUND,
            "not_found",
            "Content not found",
            request_id.clone(),
        ),
        InteractionRepositoryError::Sqlx(source) => internal_error(source, &request_id),
    })?;
    transaction
        .commit()
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    Ok(Json(RecordContentInteractionResponse {
        status: OperationStatus::Success,
        recorded: result.recorded,
        interaction_id: payload.interaction_id,
        analytics_interaction_id: Some(result.id),
    }))
}

fn clean_required(
    value: &str,
    max_chars: usize,
    label: &str,
    request_id: &str,
) -> Result<String, ApiError> {
    let value = value.trim().to_owned();
    if value.is_empty() || value.chars().count() > max_chars {
        return Err(bad_request(
            format!("{label} must contain 1-{max_chars} characters"),
            request_id,
        ));
    }
    Ok(value)
}

fn clean_optional(
    value: Option<String>,
    max_chars: usize,
    label: &str,
    request_id: &str,
) -> Result<Option<String>, ApiError> {
    let Some(value) = value else {
        return Ok(None);
    };
    let value = value.trim().to_owned();
    if value.chars().count() > max_chars {
        return Err(bad_request(
            format!("{label} must contain at most {max_chars} characters"),
            request_id,
        ));
    }
    Ok((!value.is_empty()).then_some(value))
}
