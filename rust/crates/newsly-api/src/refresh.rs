use axum::extract::rejection::JsonRejection;
use axum::extract::{Extension, State};
use axum::http::{HeaderMap, StatusCode};
use axum::routing::post;
use axum::{Json, Router};
use chrono::{Duration, Utc};
use newsly_contracts::{AccessTokenResponse, RefreshTokenRequest};
use newsly_db::{RefreshRotationClaim, begin_refresh_rotation, store_refresh_replay};
use sha2::{Digest, Sha256};

use crate::error::ApiError;
use crate::gateway::RouteOwnershipStamp;
use crate::write_support::{decode_json, internal_error, require_operation, verify_stamp};
use crate::{AppState, request_id_from_headers};

const REFRESH_OPERATION_ID: &str = "refreshToken";
const REFRESH_REPLAY_TTL: Duration = Duration::minutes(10);

pub(super) fn router() -> Router<AppState> {
    Router::new().route("/auth/refresh", post(refresh_token))
}

#[utoipa::path(
    post,
    path = "/auth/refresh",
    operation_id = "refreshToken",
    tag = "auth",
    request_body = RefreshTokenRequest,
    responses(
        (status = 200, description = "Successful Response", body = AccessTokenResponse),
        (status = 401, description = "Invalid refresh token", body = newsly_contracts::ErrorEnvelope),
        (status = 409, description = "Stale runtime owner", body = newsly_contracts::ErrorEnvelope),
        (status = 422, description = "Validation Error", body = newsly_contracts::ErrorEnvelope),
        (status = 503, description = "Refresh temporarily unavailable", body = newsly_contracts::ErrorEnvelope)
    )
)]
#[expect(
    clippy::too_many_lines,
    reason = "refresh rotation is one security-sensitive transaction with explicit replay branches"
)]
pub(super) async fn refresh_token(
    State(state): State<AppState>,
    headers: HeaderMap,
    Extension(stamp): Extension<RouteOwnershipStamp>,
    payload: Result<Json<RefreshTokenRequest>, JsonRejection>,
) -> Result<Json<AccessTokenResponse>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    require_operation(&stamp, REFRESH_OPERATION_ID, &request_id)?;
    let Json(payload) = decode_json(payload, &request_id)?;
    if payload.refresh_token.is_empty() {
        return Err(ApiError::new(
            StatusCode::UNPROCESSABLE_ENTITY,
            "validation_error",
            "Request validation failed",
            request_id,
        )
        .with_details(
            serde_json::json!({"errors": [{"message": "refresh_token must contain at least one character"}]})
                .as_object()
                .expect("validation details are an object")
                .clone(),
        ));
    }
    let verified = state
        .auth
        .decode_refresh_token(&payload.refresh_token)
        .map_err(|_| invalid_refresh_token(&request_id))?;
    let digest = Sha256::digest(payload.refresh_token.as_bytes());
    let token_hash = sha256_hex(&digest);
    let advisory_lock_key = i64::from_be_bytes(
        digest[..8]
            .try_into()
            .expect("SHA-256 digest always contains eight bytes"),
    );
    let attempt_id = payload.attempt_id.map(|value| value.to_string());
    let now = Utc::now();

    let mut transaction = state
        .database
        .pool()
        .begin()
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    verify_stamp(&mut transaction, &stamp, &request_id).await?;
    let claim = begin_refresh_rotation(
        &mut transaction,
        &token_hash,
        advisory_lock_key,
        verified.user_id,
        verified.expires_at,
        attempt_id.as_deref(),
        now,
    )
    .await
    .map_err(|error| internal_error(error, &request_id))?;

    let (tokens, replayed) = match claim {
        RefreshRotationClaim::New => {
            let tokens = state
                .auth
                .issue_token_pair(verified.user_id, now)
                .map_err(|error| internal_error(error, &request_id))?;
            if let Some(attempt_id) = attempt_id.as_deref() {
                let encrypted = state
                    .auth
                    .encrypt_refresh_replay(&tokens)
                    .map_err(|error| internal_error(error, &request_id))?;
                let replay_expires_at =
                    std::cmp::min(verified.expires_at, now + REFRESH_REPLAY_TTL);
                store_refresh_replay(
                    &mut transaction,
                    &token_hash,
                    attempt_id,
                    &encrypted,
                    replay_expires_at,
                )
                .await
                .map_err(|error| internal_error(error, &request_id))?;
            }
            (tokens, false)
        }
        RefreshRotationClaim::Replay(encrypted) => {
            let tokens = state.auth.decrypt_refresh_replay(&encrypted).map_err(|error| {
                tracing::error!(error = %error, user_id = verified.user_id, "stored refresh replay is invalid");
                ApiError::new(
                    StatusCode::SERVICE_UNAVAILABLE,
                    "refresh_temporarily_unavailable",
                    "Token refresh is temporarily unavailable",
                    request_id.clone(),
                )
                .with_retryable(true)
            })?;
            (tokens, true)
        }
        RefreshRotationClaim::Rejected | RefreshRotationClaim::InactiveUser => {
            return Err(invalid_refresh_token(&request_id));
        }
    };
    transaction
        .commit()
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    tracing::info!(
        user_id = verified.user_id,
        replayed,
        operation_id = REFRESH_OPERATION_ID,
        "token refresh completed"
    );
    Ok(Json(tokens))
}

fn invalid_refresh_token(request_id: &str) -> ApiError {
    ApiError::new(
        StatusCode::UNAUTHORIZED,
        "authentication_required",
        "Invalid refresh token",
        request_id.to_owned(),
    )
    .bearer()
}

fn sha256_hex(digest: &[u8]) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut output = String::with_capacity(digest.len() * 2);
    for byte in digest {
        output.push(char::from(HEX[usize::from(byte >> 4)]));
        output.push(char::from(HEX[usize::from(byte & 0x0f)]));
    }
    output
}
