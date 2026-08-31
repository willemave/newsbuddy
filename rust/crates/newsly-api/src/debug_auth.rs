use axum::extract::{Extension, FromRequest, Request, State};
use axum::http::{HeaderMap, StatusCode};
use axum::routing::post;
use axum::{Json, Router};
use chrono::Utc;
use newsly_contracts::{DebugUserSessionRequest, TokenResponse};
use newsly_db::{DebugUserPatch, UserProfileRepositoryError, create_or_update_debug_user};
use uuid::Uuid;

use crate::error::ApiError;
use crate::gateway::RouteOwnershipStamp;
use crate::users::user_response;
use crate::write_support::{internal_error, require_operation, verify_stamp};
use crate::{AppState, request_id_from_headers};

const DEBUG_USER_OPERATION_ID: &str = "debugNewCreateUser";

pub(super) fn router() -> Router<AppState> {
    Router::new().route("/auth/debug/new-user", post(debug_create_user))
}

#[utoipa::path(
    post,
    path = "/auth/debug/new-user",
    operation_id = "debugNewCreateUser",
    tag = "auth",
    request_body = Option<DebugUserSessionRequest>,
    responses(
        (status = 200, description = "Successful Response", body = TokenResponse),
        (status = 404, description = "Debug auth is unavailable or the user was not found", body = newsly_contracts::ErrorEnvelope),
        (status = 409, description = "Stale runtime owner", body = newsly_contracts::ErrorEnvelope),
        (status = 422, description = "Validation Error", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn debug_create_user(
    State(state): State<AppState>,
    headers: HeaderMap,
    Extension(stamp): Extension<RouteOwnershipStamp>,
    request: Request,
) -> Result<Json<TokenResponse>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    require_operation(&stamp, DEBUG_USER_OPERATION_ID, &request_id)?;
    if !state.debug_auth_enabled {
        return Err(not_found(&request_id));
    }
    let payload = Option::<Json<DebugUserSessionRequest>>::from_request(request, &state)
        .await
        .map_err(|rejection| {
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
        })?
        .map_or_else(DebugUserSessionRequest::default, |Json(value)| value);
    if payload.user_id.is_some_and(|user_id| user_id <= 0) {
        return Err(ApiError::new(
            StatusCode::UNPROCESSABLE_ENTITY,
            "validation_error",
            "user_id must be greater than or equal to 1",
            request_id,
        ));
    }

    let unique = Uuid::new_v4().simple().to_string();
    let apple_id = format!("debug_{unique}");
    let email = format!("debug+{}@example.com", &unique[..16]);
    let patch = DebugUserPatch {
        has_completed_onboarding: payload.has_completed_onboarding,
        has_completed_new_user_tutorial: payload.has_completed_new_user_tutorial,
        reading_experience: payload
            .reading_experience
            .map(|value| value.as_str().to_owned()),
    };
    let mut transaction = state
        .database
        .pool()
        .begin()
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    verify_stamp(&mut transaction, &stamp, &request_id).await?;
    let upsert =
        create_or_update_debug_user(&mut transaction, payload.user_id, &apple_id, &email, &patch)
            .await
            .map_err(|error| match error {
                UserProfileRepositoryError::UserNotFound(_) => not_found(&request_id),
                other => internal_error(other, &request_id),
            })?;
    transaction
        .commit()
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    let tokens = state
        .auth
        .issue_token_pair(upsert.profile.id, Utc::now())
        .map_err(|error| internal_error(error, &request_id))?;
    Ok(Json(TokenResponse {
        access_token: tokens.access_token,
        refresh_token: tokens.refresh_token,
        token_type: tokens.token_type,
        user: user_response(upsert.profile),
        is_new_user: upsert.is_new_user,
    }))
}

fn not_found(request_id: &str) -> ApiError {
    ApiError::new(
        StatusCode::NOT_FOUND,
        "not_found",
        "Not found",
        request_id.to_owned(),
    )
}
