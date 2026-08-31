use axum::extract::rejection::{JsonRejection, PathRejection, QueryRejection};
use axum::extract::{Extension, FromRequest, Path, Query, Request, State};
use axum::http::{HeaderMap, StatusCode};
use axum::routing::{get, post};
use axum::{Json, Router};
use base64::Engine as _;
use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use chrono::{Duration, Utc};
use newsly_contracts::{
    CliLinkApproveRequest, CliLinkApproveResponse, CliLinkPollResponse, CliLinkStartRequest,
    CliLinkStartResponse, CliLinkStatus,
};
use newsly_db::{
    CliLinkPollStatus, CliLinkRepositoryError, approve_cli_link, hash_api_key, poll_cli_link,
    start_cli_link,
};
use serde::Deserialize;

use crate::auth::AuthenticatedUser;
use crate::error::ApiError;
use crate::gateway::RouteOwnershipStamp;
use crate::write_support::{internal_error, require_operation, verify_stamp};
use crate::{AppState, request_id_from_headers};

const START_OPERATION_ID: &str = "startCliLink";
const POLL_OPERATION_ID: &str = "pollCliLink";
const APPROVE_OPERATION_ID: &str = "approveCliLink";
const SESSION_TTL: Duration = Duration::minutes(10);

#[derive(Debug, Deserialize)]
pub(super) struct PollQuery {
    poll_token: String,
}

pub(super) fn router() -> Router<AppState> {
    Router::new()
        .route("/api/agent/cli/link/start", post(start_link))
        .route("/api/agent/cli/link/{session_id}", get(poll_link))
        .route(
            "/api/agent/cli/link/{session_id}/approve",
            post(approve_link),
        )
}

#[utoipa::path(
    post,
    path = "/api/agent/cli/link/start",
    operation_id = "startCliLink",
    tag = "agent",
    request_body = Option<CliLinkStartRequest>,
    responses(
        (status = 200, description = "CLI link session created", body = CliLinkStartResponse),
        (status = 409, description = "Stale runtime owner", body = newsly_contracts::ErrorEnvelope),
        (status = 422, description = "Validation Error", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn start_link(
    State(state): State<AppState>,
    headers: HeaderMap,
    Extension(stamp): Extension<RouteOwnershipStamp>,
    request: Request,
) -> Result<Json<CliLinkStartResponse>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    require_operation(&stamp, START_OPERATION_ID, &request_id)?;
    let payload = Option::<Json<CliLinkStartRequest>>::from_request(request, &state)
        .await
        .map_err(|rejection| validation_error(rejection.body_text(), &request_id))?
        .map_or_else(CliLinkStartRequest::default, |Json(value)| value);
    let device_name = normalize_device_name(payload.device_name, &request_id)?;
    let session_id = random_urlsafe::<12>().map_err(|error| internal_error(error, &request_id))?;
    let approve_token =
        random_urlsafe::<24>().map_err(|error| internal_error(error, &request_id))?;
    let poll_token = random_urlsafe::<24>().map_err(|error| internal_error(error, &request_id))?;
    let expires_at = Utc::now() + SESSION_TTL;

    let mut transaction = state
        .database
        .pool()
        .begin()
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    verify_stamp(&mut transaction, &stamp, &request_id).await?;
    let started = start_cli_link(
        &mut transaction,
        &session_id,
        &hash_api_key(&approve_token),
        &hash_api_key(&poll_token),
        device_name.as_deref(),
        expires_at,
    )
    .await
    .map_err(|error| internal_error(error, &request_id))?;
    transaction
        .commit()
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    Ok(Json(CliLinkStartResponse {
        approve_url: format!(
            "newsly://cli-link?session_id={}&approve_token={approve_token}",
            started.session_id
        ),
        session_id: started.session_id,
        status: CliLinkStatus::Pending,
        poll_token,
        expires_at: started.expires_at,
        poll_interval_seconds: 2,
    }))
}

#[utoipa::path(
    post,
    path = "/api/agent/cli/link/{session_id}/approve",
    operation_id = "approveCliLink",
    tag = "agent",
    params(("session_id" = String, Path, description = "CLI link session")),
    request_body = CliLinkApproveRequest,
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, description = "CLI link approved", body = CliLinkApproveResponse),
        (status = 400, description = "Invalid or expired CLI link", body = newsly_contracts::ErrorEnvelope),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 403, description = "Authentication required", body = newsly_contracts::ErrorEnvelope),
        (status = 409, description = "Stale runtime owner", body = newsly_contracts::ErrorEnvelope),
        (status = 422, description = "Validation Error", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn approve_link(
    State(state): State<AppState>,
    headers: HeaderMap,
    current_user: AuthenticatedUser,
    Extension(stamp): Extension<RouteOwnershipStamp>,
    session_id: Result<Path<String>, PathRejection>,
    payload: Result<Json<CliLinkApproveRequest>, JsonRejection>,
) -> Result<Json<CliLinkApproveResponse>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    require_operation(&stamp, APPROVE_OPERATION_ID, &request_id)?;
    let Path(session_id) =
        session_id.map_err(|rejection| validation_error(rejection.body_text(), &request_id))?;
    validate_length("session_id", &session_id, 8, 64, &request_id)?;
    let Json(payload) =
        payload.map_err(|rejection| validation_error(rejection.body_text(), &request_id))?;
    validate_length("approve_token", &payload.approve_token, 8, 255, &request_id)?;
    let device_name = normalize_device_name(payload.device_name, &request_id)?;

    let mut transaction = state
        .database
        .pool()
        .begin()
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    verify_stamp(&mut transaction, &stamp, &request_id).await?;
    let approved = match approve_cli_link(
        &mut transaction,
        &session_id,
        &payload.approve_token,
        current_user.id,
        device_name.as_deref(),
        Utc::now(),
    )
    .await
    {
        Ok(approved) => approved,
        Err(CliLinkRepositoryError::Expired) => {
            transaction
                .commit()
                .await
                .map_err(|error| internal_error(error, &request_id))?;
            return Err(cli_link_error(CliLinkRepositoryError::Expired, &request_id));
        }
        Err(error) => return Err(cli_link_error(error, &request_id)),
    };
    transaction
        .commit()
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    Ok(Json(CliLinkApproveResponse {
        session_id: approved.session_id,
        status: CliLinkStatus::Approved,
        key_prefix: approved.key_prefix,
        expires_at: approved.expires_at,
    }))
}

#[utoipa::path(
    get,
    path = "/api/agent/cli/link/{session_id}",
    operation_id = "pollCliLink",
    tag = "agent",
    params(
        ("session_id" = String, Path, description = "CLI link session"),
        ("poll_token" = String, Query, description = "Secret polling token")
    ),
    responses(
        (status = 200, description = "CLI link status", body = CliLinkPollResponse),
        (status = 400, description = "Invalid CLI link", body = newsly_contracts::ErrorEnvelope),
        (status = 409, description = "Stale runtime owner", body = newsly_contracts::ErrorEnvelope),
        (status = 422, description = "Validation Error", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn poll_link(
    State(state): State<AppState>,
    headers: HeaderMap,
    Extension(stamp): Extension<RouteOwnershipStamp>,
    session_id: Result<Path<String>, PathRejection>,
    query: Result<Query<PollQuery>, QueryRejection>,
) -> Result<Json<CliLinkPollResponse>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    require_operation(&stamp, POLL_OPERATION_ID, &request_id)?;
    let Path(session_id) =
        session_id.map_err(|rejection| validation_error(rejection.body_text(), &request_id))?;
    validate_length("session_id", &session_id, 8, 64, &request_id)?;
    let Query(query) =
        query.map_err(|rejection| validation_error(rejection.body_text(), &request_id))?;
    validate_length("poll_token", &query.poll_token, 8, 255, &request_id)?;

    // Poll is a GET for client ergonomics but consumes the raw key exactly
    // once, so it still takes the same durable ownership fence as a mutation.
    let mut transaction = state
        .database
        .pool()
        .begin()
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    verify_stamp(&mut transaction, &stamp, &request_id).await?;
    let polled = poll_cli_link(&mut transaction, &session_id, &query.poll_token, Utc::now())
        .await
        .map_err(|error| cli_link_error(error, &request_id))?;
    transaction
        .commit()
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    Ok(Json(CliLinkPollResponse {
        session_id: polled.session_id,
        status: match polled.status {
            CliLinkPollStatus::Pending => CliLinkStatus::Pending,
            CliLinkPollStatus::Approved => CliLinkStatus::Approved,
            CliLinkPollStatus::Claimed => CliLinkStatus::Claimed,
            CliLinkPollStatus::Expired => CliLinkStatus::Expired,
        },
        expires_at: polled.expires_at,
        api_key: polled.api_key,
        key_prefix: polled.key_prefix,
    }))
}

fn random_urlsafe<const N: usize>() -> Result<String, getrandom::Error> {
    let mut bytes = [0_u8; N];
    getrandom::fill(&mut bytes)?;
    Ok(URL_SAFE_NO_PAD.encode(bytes))
}

fn normalize_device_name(
    value: Option<String>,
    request_id: &str,
) -> Result<Option<String>, ApiError> {
    let value = value.map(|name| name.trim().to_owned());
    if value
        .as_ref()
        .is_some_and(|name| name.chars().count() > 255)
    {
        return Err(validation_error(
            "device_name must contain at most 255 characters",
            request_id,
        ));
    }
    Ok(value.filter(|name| !name.is_empty()))
}

fn validate_length(
    field: &'static str,
    value: &str,
    minimum: usize,
    maximum: usize,
    request_id: &str,
) -> Result<(), ApiError> {
    if (minimum..=maximum).contains(&value.chars().count()) {
        return Ok(());
    }
    Err(validation_error(
        format!("{field} must contain between {minimum} and {maximum} characters"),
        request_id,
    ))
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

fn cli_link_error(error: CliLinkRepositoryError, request_id: &str) -> ApiError {
    match error {
        CliLinkRepositoryError::NotFound
        | CliLinkRepositoryError::Expired
        | CliLinkRepositoryError::InvalidApprovalToken
        | CliLinkRepositoryError::InvalidPollingToken
        | CliLinkRepositoryError::AlreadyClaimed
        | CliLinkRepositoryError::MissingApiKey => ApiError::new(
            StatusCode::BAD_REQUEST,
            "invalid_cli_link",
            error.to_string(),
            request_id.to_owned(),
        ),
        CliLinkRepositoryError::Sqlx(_) | CliLinkRepositoryError::ApiKey(_) => {
            internal_error(error, request_id)
        }
    }
}
