use std::collections::HashSet;

use axum::extract::rejection::JsonRejection;
use axum::extract::{Extension, State};
use axum::http::{HeaderMap, StatusCode};
use axum::routing::get;
use axum::{Json, Router};
use newsly_contracts::{
    CouncilPersonaConfig, CouncilPersonaInput, ReadingExperience, UpdateUserProfileRequest,
    UserResponse,
};
use newsly_db::{
    RouteWriteFenceError, UserProfilePatch, UserProfileProjection, find_user_profile,
    update_user_profile, verify_route_write_fence,
};

use crate::auth::AuthenticatedUser;
use crate::error::ApiError;
use crate::gateway::RouteOwnershipStamp;
use crate::{AppState, request_id_from_headers};

const UPDATE_USER_OPERATION_ID: &str = "updateMeCurrentUserInfo";

pub(super) fn router() -> Router<AppState> {
    Router::new().route("/auth/me", get(get_current_user).patch(update_current_user))
}

#[utoipa::path(
    get,
    path = "/auth/me",
    operation_id = "getMeCurrentUserInfo",
    tag = "auth",
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, description = "Successful Response", body = UserResponse),
        (status = 400, description = "Inactive user", body = newsly_contracts::ErrorEnvelope),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 403, description = "Authentication required", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn get_current_user(
    State(state): State<AppState>,
    headers: HeaderMap,
    current_user: AuthenticatedUser,
) -> Result<Json<UserResponse>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    let profile = find_user_profile(state.database.pool(), current_user.id)
        .await
        .map_err(|error| internal_error(error, &request_id))?
        .ok_or_else(|| invalid_credentials(&request_id))?;
    Ok(Json(user_response(profile)))
}

#[utoipa::path(
    patch,
    path = "/auth/me",
    operation_id = "updateMeCurrentUserInfo",
    tag = "auth",
    request_body = UpdateUserProfileRequest,
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, description = "Successful Response", body = UserResponse),
        (status = 400, description = "Invalid profile value", body = newsly_contracts::ErrorEnvelope),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 403, description = "Authentication required", body = newsly_contracts::ErrorEnvelope),
        (status = 409, description = "Stale runtime owner", body = newsly_contracts::ErrorEnvelope),
        (status = 422, description = "Validation Error", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn update_current_user(
    State(state): State<AppState>,
    headers: HeaderMap,
    current_user: AuthenticatedUser,
    Extension(stamp): Extension<RouteOwnershipStamp>,
    payload: Result<Json<UpdateUserProfileRequest>, JsonRejection>,
) -> Result<Json<UserResponse>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    if stamp.operation_id != UPDATE_USER_OPERATION_ID {
        return Err(stale_owner(&request_id));
    }
    let Json(payload) = payload.map_err(|rejection| {
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
    let patch = validate_patch(payload).map_err(|message| {
        ApiError::new(
            StatusCode::BAD_REQUEST,
            "bad_request",
            message,
            request_id.clone(),
        )
    })?;

    let mut transaction = state.database.pool().begin().await.map_err(|error| {
        tracing::error!(error = %error, "user profile transaction could not begin");
        internal_error(error, &request_id)
    })?;
    verify_route_write_fence(
        &mut transaction,
        &stamp.operation_id,
        stamp.owner,
        stamp.version,
    )
    .await
    .map_err(|error| route_fence_error(error, &request_id))?;
    let profile = update_user_profile(&mut transaction, current_user.id, &patch)
        .await
        .map_err(|error| internal_error(error, &request_id))?
        .ok_or_else(|| invalid_credentials(&request_id))?;
    transaction
        .commit()
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    Ok(Json(user_response(profile)))
}

fn validate_patch(payload: UpdateUserProfileRequest) -> Result<UserProfilePatch, String> {
    let full_name = payload
        .full_name
        .map(|value| {
            if value.chars().count() > 255 {
                return Err("full_name must contain at most 255 characters".to_owned());
            }
            let cleaned = value.trim().to_owned();
            Ok(if cleaned.is_empty() {
                None
            } else {
                Some(cleaned)
            })
        })
        .transpose()?;
    let twitter_username = payload
        .twitter_username
        .map(|value| normalize_twitter_username(&value))
        .transpose()?;
    let council_personas = payload
        .council_personas
        .map(validate_council_personas)
        .transpose()?
        .map(|personas| serde_json::to_value(personas).expect("personas serialize to JSON"));
    Ok(UserProfilePatch {
        full_name,
        twitter_username,
        council_personas,
        reading_experience: payload
            .reading_experience
            .map(|experience| experience.as_str().to_owned()),
    })
}

fn normalize_twitter_username(value: &str) -> Result<Option<String>, String> {
    let cleaned = value
        .trim()
        .strip_prefix('@')
        .unwrap_or(value.trim())
        .trim();
    if cleaned.is_empty() {
        return Ok(None);
    }
    if cleaned.chars().count() > 15
        || !cleaned
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || byte == b'_')
    {
        return Err(
            "Twitter username must be 1-15 chars (letters, numbers, underscore)".to_owned(),
        );
    }
    Ok(Some(cleaned.to_ascii_lowercase()))
}

fn validate_council_personas(
    personas: Vec<CouncilPersonaInput>,
) -> Result<Vec<CouncilPersonaConfig>, String> {
    if !(2..=3).contains(&personas.len()) {
        return Err("council_personas must contain 2-3 entries".to_owned());
    }
    let mut normalized = Vec::with_capacity(personas.len());
    let mut ids = HashSet::new();
    for persona in personas {
        let mut persona = CouncilPersonaConfig::from(persona);
        persona.id = String::from(persona.id.trim());
        persona.display_name = String::from(persona.display_name.trim());
        persona.instruction_prompt = String::from(persona.instruction_prompt.trim());
        if persona.id.is_empty()
            || persona.id.chars().count() > 50
            || persona.display_name.is_empty()
            || persona.display_name.chars().count() > 80
            || persona.instruction_prompt.chars().count() > 1_500
            || !(0..=2).contains(&persona.sort_order)
        {
            return Err("council_personas contains an invalid expert configuration".to_owned());
        }
        if !ids.insert(persona.id.clone()) {
            return Err("council_personas must use unique ids".to_owned());
        }
        normalized.push(persona);
    }
    normalized.sort_by_key(|persona| persona.sort_order);
    if normalized
        .iter()
        .enumerate()
        .any(|(index, persona)| usize::try_from(persona.sort_order) != Ok(index))
    {
        return Err("council_personas sort_order values must be contiguous".to_owned());
    }
    Ok(normalized)
}

pub(super) fn user_response(profile: UserProfileProjection) -> UserResponse {
    let council_personas = profile
        .council_personas
        .and_then(|value| serde_json::from_value(value).ok())
        .and_then(|personas| validate_council_personas(personas).ok())
        .unwrap_or_default();
    let reading_experience = match profile.reading_experience.as_str() {
        "classic" => ReadingExperience::Classic,
        _ => ReadingExperience::Briefing,
    };
    UserResponse {
        email: profile.email,
        full_name: profile.full_name,
        id: profile.id,
        apple_id: profile.apple_id,
        is_admin: profile.is_admin,
        is_active: profile.is_active,
        twitter_username: profile.twitter_username,
        council_personas,
        has_x_bookmark_sync: profile.has_x_bookmark_sync,
        has_completed_onboarding: profile.has_completed_onboarding,
        has_completed_new_user_tutorial: profile.has_completed_new_user_tutorial,
        reading_experience,
        created_at: profile.created_at,
        updated_at: profile.updated_at,
    }
}

fn invalid_credentials(request_id: &str) -> ApiError {
    ApiError::new(
        StatusCode::UNAUTHORIZED,
        "authentication_required",
        "Could not validate credentials",
        request_id.to_owned(),
    )
    .bearer()
}

fn stale_owner(request_id: &str) -> ApiError {
    ApiError::new(
        StatusCode::CONFLICT,
        "stale_ownership",
        "This Rust runtime no longer owns the operation version",
        request_id.to_owned(),
    )
    .with_retryable(true)
}

fn route_fence_error(error: RouteWriteFenceError, request_id: &str) -> ApiError {
    tracing::warn!(error = %error, "user profile write rejected by route fence");
    match error {
        RouteWriteFenceError::Missing(_) | RouteWriteFenceError::Stale { .. } => {
            stale_owner(request_id)
        }
        RouteWriteFenceError::Sqlx(source) => internal_error(source, request_id),
    }
}

fn internal_error(error: impl std::fmt::Display, request_id: &str) -> ApiError {
    tracing::error!(error = %error, "user profile operation failed");
    ApiError::new(
        StatusCode::INTERNAL_SERVER_ERROR,
        "internal_error",
        "Internal server error",
        request_id.to_owned(),
    )
    .with_retryable(true)
}
