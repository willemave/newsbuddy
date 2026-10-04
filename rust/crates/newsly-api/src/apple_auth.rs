use axum::extract::State;
use axum::extract::rejection::JsonRejection;
use axum::http::{HeaderMap, StatusCode};
use axum::routing::post;
use axum::{Json, Router};
use chrono::Utc;
use newsly_contracts::{AppleSignInRequest, TokenResponse};
use newsly_db::{UserProfileRepositoryError, find_apple_user, find_or_create_apple_user};

use crate::error::ApiError;
use crate::gateway::RouteOwnershipStamp;
use crate::users::user_response;
use crate::write_support::{decode_json, internal_error};
use crate::{AppState, request_id_from_headers};

const APPLE_SIGN_IN_OPERATION_ID: &str = "appleSignin";
const MAX_IDENTITY_FIELD_CHARACTERS: usize = 255;

pub(super) fn router() -> Router<AppState> {
    Router::new().route("/auth/apple", post(apple_sign_in))
}

#[utoipa::path(
    post,
    path = "/auth/apple",
    operation_id = "appleSignin",
    tag = "auth",
    request_body = AppleSignInRequest,
    responses(
        (status = 200, description = "Successful Response", body = TokenResponse),
        (status = 400, description = "Missing or invalid account data", body = newsly_contracts::ErrorEnvelope),
        (status = 401, description = "Invalid Apple token", body = newsly_contracts::ErrorEnvelope),
        (status = 409, description = "Email already registered or stale runtime owner", body = newsly_contracts::ErrorEnvelope),
        (status = 422, description = "Validation Error", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn apple_sign_in(
    State(state): State<AppState>,
    headers: HeaderMap,
    stamp: RouteOwnershipStamp,
    payload: Result<Json<AppleSignInRequest>, JsonRejection>,
) -> Result<Json<TokenResponse>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    let Json(payload) = decode_json(payload, &request_id)?;

    // Apple verification is remote work. Complete it before opening the short
    // persistence transaction so a JWKS fetch never holds PostgreSQL locks.
    let identity = state
        .auth
        .verify_apple_identity(&payload.id_token, payload.nonce.as_deref())
        .await
        .map_err(|error| {
            tracing::warn!(error = %error, operation_id = APPLE_SIGN_IN_OPERATION_ID, "Apple identity verification failed");
            invalid_apple_token(&request_id)
        })?;
    let full_name = first_nonempty(payload.full_name.as_deref(), None).map(str::to_owned);

    let mut transaction =
        crate::write_support::begin_fenced(state.database.pool(), &stamp, &request_id).await?;
    let upsert = if let Some(existing) = find_apple_user(&mut transaction, &identity.subject)
        .await
        .map_err(|error| internal_error(error, &request_id))?
    {
        existing
    } else {
        let email = first_nonempty(identity.email.as_deref(), None).ok_or_else(|| {
            ApiError::new(
                StatusCode::BAD_REQUEST,
                "missing_email",
                "Email is required but was not found in the Apple token",
                request_id.clone(),
            )
        })?;
        validate_identity_field("email", email, &request_id)?;
        if let Some(name) = full_name.as_deref() {
            validate_identity_field("full_name", name, &request_id)?;
        }
        find_or_create_apple_user(
            &mut transaction,
            &identity.subject,
            email,
            full_name.as_deref(),
        )
        .await
        .map_err(|error| match error {
            UserProfileRepositoryError::Sqlx(sqlx::Error::Database(database_error))
                if database_error.is_unique_violation()
                    && database_error.constraint() == Some("ix_users_email") =>
            {
                ApiError::new(
                    StatusCode::CONFLICT,
                    "email_already_registered",
                    "The verified Apple email is already registered to another account",
                    request_id.clone(),
                )
            }
            other => internal_error(other, &request_id),
        })?
    };
    crate::write_support::commit(transaction, &request_id).await?;

    let tokens = state
        .auth
        .issue_token_pair(upsert.profile.id, Utc::now())
        .map_err(|error| internal_error(error, &request_id))?;
    tracing::info!(
        user_id = upsert.profile.id,
        is_new_user = upsert.is_new_user,
        operation_id = APPLE_SIGN_IN_OPERATION_ID,
        "Apple sign-in completed"
    );
    Ok(Json(TokenResponse {
        access_token: tokens.access_token,
        refresh_token: tokens.refresh_token,
        token_type: tokens.token_type,
        user: user_response(upsert.profile),
        is_new_user: upsert.is_new_user,
    }))
}

fn first_nonempty<'a>(preferred: Option<&'a str>, fallback: Option<&'a str>) -> Option<&'a str> {
    preferred
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .or_else(|| fallback.map(str::trim).filter(|value| !value.is_empty()))
}

fn validate_identity_field(
    field: &'static str,
    value: &str,
    request_id: &str,
) -> Result<(), ApiError> {
    if value.chars().count() <= MAX_IDENTITY_FIELD_CHARACTERS {
        return Ok(());
    }
    Err(ApiError::new(
        StatusCode::BAD_REQUEST,
        "invalid_identity_data",
        format!("{field} must contain at most {MAX_IDENTITY_FIELD_CHARACTERS} characters"),
        request_id.to_owned(),
    ))
}

fn invalid_apple_token(request_id: &str) -> ApiError {
    ApiError::new(
        StatusCode::UNAUTHORIZED,
        "invalid_apple_token",
        "Invalid Apple token",
        request_id.to_owned(),
    )
    .bearer()
}
