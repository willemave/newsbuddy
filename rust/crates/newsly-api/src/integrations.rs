use axum::extract::rejection::{JsonRejection, PathRejection};
use axum::extract::{Extension, Path, State};
use axum::http::{HeaderMap, StatusCode};
use axum::routing::{get, post, put};
use axum::{Json, Router};
use base64::Engine as _;
use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use chrono::{Duration, Utc};
use getrandom::fill as random_fill;
use newsly_contracts::{
    DeleteStatus, DeleteUserLlmIntegrationResponse, IntegrationDisconnectResponse,
    IntegrationDisconnectStatus, UpsertUserLlmIntegrationRequest, UserLlmIntegrationResponse,
    UserLlmIntegrationTestResponse, UserLlmProvider, XConnectionResponse, XOAuthExchangeRequest,
    XOAuthStartRequest, XOAuthStartResponse,
};
use newsly_db::{
    IntegrationRepositoryError, NewXUserLookupUsage, PrepareXOAuthExchangeOutcome,
    UserLlmIntegrationProjection, XConnectionProjection, delete_user_llm_integration,
    finalize_x_disconnect, finalize_x_oauth_exchange, find_x_connection,
    list_user_llm_integrations, prepare_x_disconnect, prepare_x_oauth_exchange,
    record_x_user_lookup_usage, store_x_oauth_pending, upsert_user_llm_integration,
    user_llm_integration_configured,
};
use newsly_providers::X_DEFAULT_SCOPES;
use sha2::{Digest, Sha256};

use crate::auth::AuthenticatedUser;
use crate::error::ApiError;
use crate::gateway::RouteOwnershipStamp;
use crate::write_support::{
    bad_request, decode_json, internal_error, not_found, require_operation, verify_stamp,
};
use crate::{AppState, request_id_from_headers};

const PUT_OPERATION_ID: &str = "putLlmIntegration";
const DELETE_OPERATION_ID: &str = "deleteLlmIntegration";
const TEST_OPERATION_ID: &str = "testLlmIntegration";
const X_START_OPERATION_ID: &str = "startIntegrationsXOauthFlow";
const X_EXCHANGE_OPERATION_ID: &str = "exchangeIntegrationsXOauthCode";
const X_DISCONNECT_OPERATION_ID: &str = "disconnectIntegrationsConnectionX";

pub(super) fn router() -> Router<AppState> {
    Router::new()
        .route("/api/integrations/llm", get(get_llm_integrations))
        .route(
            "/api/integrations/llm/{provider}",
            put(put_llm_integration).delete(delete_llm_integration_endpoint),
        )
        .route(
            "/api/integrations/llm/{provider}/test",
            post(test_llm_integration),
        )
        .route(
            "/api/integrations/x/connection",
            get(get_x_connection).delete(disconnect_x_connection),
        )
        .route("/api/integrations/x/oauth/start", post(start_x_oauth_flow))
        .route(
            "/api/integrations/x/oauth/exchange",
            post(exchange_x_oauth_code),
        )
}

#[utoipa::path(
    get,
    path = "/api/integrations/x/connection",
    operation_id = "getIntegrationsXConnection",
    tag = "integrations",
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, description = "Current X connection state", body = XConnectionResponse),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn get_x_connection(
    State(state): State<AppState>,
    headers: HeaderMap,
    current_user: AuthenticatedUser,
) -> Result<Json<XConnectionResponse>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    let connection = find_x_connection(state.database.pool(), current_user.id)
        .await
        .map_err(|error| internal_error(error, &request_id))?
        .ok_or_else(|| not_found("User", &request_id))?;
    Ok(Json(present_x_connection(connection)))
}

#[utoipa::path(
    post,
    path = "/api/integrations/x/oauth/start",
    operation_id = "startIntegrationsXOauthFlow",
    tag = "integrations",
    request_body = XOAuthStartRequest,
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, description = "X OAuth flow started", body = XOAuthStartResponse),
        (status = 400, description = "Invalid request or missing OAuth configuration", body = newsly_contracts::ErrorEnvelope),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 409, description = "Stale runtime owner", body = newsly_contracts::ErrorEnvelope),
        (status = 422, description = "Validation Error", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn start_x_oauth_flow(
    State(state): State<AppState>,
    headers: HeaderMap,
    current_user: AuthenticatedUser,
    Extension(stamp): Extension<RouteOwnershipStamp>,
    payload: Result<Json<XOAuthStartRequest>, JsonRejection>,
) -> Result<Json<XOAuthStartResponse>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    require_operation(&stamp, X_START_OPERATION_ID, &request_id)?;
    let Json(payload) = decode_json(payload, &request_id)?;
    if payload
        .twitter_username
        .as_ref()
        .is_some_and(|username| username.chars().count() > 50)
    {
        return Err(validation_error(
            "twitter_username must contain at most 50 characters",
            &request_id,
        ));
    }
    let twitter_username = normalize_twitter_username(payload.twitter_username.as_deref())
        .map_err(|message| bad_request(message, &request_id))?;
    let gateway = state.x_oauth.as_ref().ok_or_else(|| {
        bad_request(
            "X OAuth is not configured. Set X_CLIENT_ID, X_OAUTH_REDIRECT_URI, and X_TOKEN_ENCRYPTION_KEY.",
            &request_id,
        )
    })?;
    if state.integration_token_cipher.is_none() {
        return Err(bad_request(
            "X OAuth is not configured. Set X_CLIENT_ID, X_OAUTH_REDIRECT_URI, and X_TOKEN_ENCRYPTION_KEY.",
            &request_id,
        ));
    }
    let state_token = random_urlsafe::<32>().map_err(|error| internal_error(error, &request_id))?;
    let code_verifier =
        random_urlsafe::<64>().map_err(|error| internal_error(error, &request_id))?;
    let code_challenge = URL_SAFE_NO_PAD.encode(Sha256::digest(code_verifier.as_bytes()));
    let scopes = X_DEFAULT_SCOPES.map(str::to_owned).to_vec();
    let authorize_url = gateway.authorize_url(&state_token, &code_challenge, &scopes);

    let mut transaction = state
        .database
        .pool()
        .begin()
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    verify_stamp(&mut transaction, &stamp, &request_id).await?;
    let stored = store_x_oauth_pending(
        &mut transaction,
        current_user.id,
        twitter_username.as_deref(),
        &state_token,
        &code_verifier,
        Utc::now(),
        &scopes,
    )
    .await
    .map_err(|error| internal_error(error, &request_id))?;
    if !stored {
        return Err(not_found("User", &request_id));
    }
    transaction
        .commit()
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    Ok(Json(XOAuthStartResponse {
        authorize_url,
        state: state_token,
        scopes,
    }))
}

#[utoipa::path(
    post,
    path = "/api/integrations/x/oauth/exchange",
    operation_id = "exchangeIntegrationsXOauthCode",
    tag = "integrations",
    request_body = XOAuthExchangeRequest,
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, description = "X OAuth connection persisted", body = XConnectionResponse),
        (status = 400, description = "Invalid or expired OAuth flow", body = newsly_contracts::ErrorEnvelope),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 409, description = "Stale or superseded OAuth flow", body = newsly_contracts::ErrorEnvelope),
        (status = 422, description = "Validation Error", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope),
        (status = 502, description = "X provider failure", body = newsly_contracts::ErrorEnvelope)
    )
)]
#[expect(
    clippy::too_many_lines,
    reason = "the OAuth exchange keeps prepare, external call, and fenced finalize steps explicit"
)]
pub(super) async fn exchange_x_oauth_code(
    State(state): State<AppState>,
    headers: HeaderMap,
    current_user: AuthenticatedUser,
    Extension(stamp): Extension<RouteOwnershipStamp>,
    payload: Result<Json<XOAuthExchangeRequest>, JsonRejection>,
) -> Result<Json<XConnectionResponse>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    require_operation(&stamp, X_EXCHANGE_OPERATION_ID, &request_id)?;
    let Json(payload) = decode_json(payload, &request_id)?;
    validate_oauth_exchange(&payload, &request_id)?;
    let gateway = state
        .x_oauth
        .as_ref()
        .ok_or_else(|| bad_request("X OAuth is not configured", &request_id))?;

    let mut prepare = state
        .database
        .pool()
        .begin()
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    verify_stamp(&mut prepare, &stamp, &request_id).await?;
    let prepared =
        prepare_x_oauth_exchange(&mut prepare, current_user.id, &payload.state, Utc::now())
            .await
            .map_err(|error| internal_error(error, &request_id))?;
    let prepared = match prepared {
        PrepareXOAuthExchangeOutcome::Prepared(prepared) => prepared,
        PrepareXOAuthExchangeOutcome::NotInitialized => {
            return Err(bad_request(
                "OAuth flow not initialized. Start OAuth first.",
                &request_id,
            ));
        }
        PrepareXOAuthExchangeOutcome::MissingPendingState => {
            return Err(bad_request(
                "OAuth flow expired or missing. Start OAuth again.",
                &request_id,
            ));
        }
        PrepareXOAuthExchangeOutcome::InvalidPendingState => {
            return Err(bad_request(
                "Invalid OAuth pending state. Start OAuth again.",
                &request_id,
            ));
        }
        PrepareXOAuthExchangeOutcome::StateMismatch => {
            return Err(bad_request("Invalid OAuth state", &request_id));
        }
        PrepareXOAuthExchangeOutcome::Expired => {
            return Err(bad_request(
                "OAuth flow expired. Start OAuth again.",
                &request_id,
            ));
        }
    };
    prepare
        .commit()
        .await
        .map_err(|error| internal_error(error, &request_id))?;

    let token = gateway
        .exchange_code(&payload.code, &prepared.code_verifier)
        .await
        .map_err(|error| provider_error(error, &request_id))?;
    let provider_user = gateway
        .authenticated_user(&token.access_token)
        .await
        .map_err(|error| provider_error(error, &request_id))?;
    persist_x_lookup_usage_best_effort(
        &state,
        &stamp,
        &request_id,
        current_user.id,
        &provider_user.id,
    )
    .await;
    let provider_username = normalize_twitter_username(provider_user.username.as_deref())
        .map_err(|message| bad_request(message, &request_id))?;
    let token_cipher = state
        .integration_token_cipher
        .as_ref()
        .ok_or_else(|| internal_error("integration token cipher is not configured", &request_id))?;
    let encrypted_access = token_cipher
        .encrypt(&token.access_token)
        .map_err(|error| internal_error(error, &request_id))?;
    let encrypted_refresh = token
        .refresh_token
        .as_deref()
        .map(|token| token_cipher.encrypt(token))
        .transpose()
        .map_err(|error| internal_error(error, &request_id))?;
    let token_expires_at = token
        .expires_in
        .filter(|seconds| *seconds > 0)
        .map(|seconds| Utc::now() + Duration::seconds(seconds.saturating_sub(60).max(0)));
    let scopes = if token.scopes.is_empty() {
        X_DEFAULT_SCOPES.map(str::to_owned).to_vec()
    } else {
        token.scopes
    };

    let mut finalize = state
        .database
        .pool()
        .begin()
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    verify_stamp(&mut finalize, &stamp, &request_id).await?;
    let finalized = finalize_x_oauth_exchange(
        &mut finalize,
        current_user.id,
        prepared.connection_id,
        &payload.state,
        &provider_user.id,
        provider_username.as_deref(),
        &encrypted_access,
        encrypted_refresh.as_deref(),
        token_expires_at,
        &scopes,
        Utc::now(),
    )
    .await
    .map_err(|error| integration_finalize_error(error, &request_id))?;
    if !finalized {
        return Err(stale_flow(&request_id));
    }
    finalize
        .commit()
        .await
        .map_err(|error| internal_error(error, &request_id))?;

    let connection = find_x_connection(state.database.pool(), current_user.id)
        .await
        .map_err(|error| internal_error(error, &request_id))?
        .ok_or_else(|| not_found("User", &request_id))?;
    Ok(Json(present_x_connection(connection)))
}

#[utoipa::path(
    delete,
    path = "/api/integrations/x/connection",
    operation_id = "disconnectIntegrationsConnectionX",
    tag = "integrations",
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, description = "X integration disconnected", body = IntegrationDisconnectResponse),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 409, description = "Stale runtime owner", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope),
        (status = 502, description = "X provider failure", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn disconnect_x_connection(
    State(state): State<AppState>,
    headers: HeaderMap,
    current_user: AuthenticatedUser,
    Extension(stamp): Extension<RouteOwnershipStamp>,
) -> Result<Json<IntegrationDisconnectResponse>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    require_operation(&stamp, X_DISCONNECT_OPERATION_ID, &request_id)?;
    let mut prepare = state
        .database
        .pool()
        .begin()
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    verify_stamp(&mut prepare, &stamp, &request_id).await?;
    let plan = prepare_x_disconnect(&mut prepare, current_user.id)
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    prepare
        .commit()
        .await
        .map_err(|error| internal_error(error, &request_id))?;

    let Some(plan) = plan else {
        return Ok(Json(disconnected_response()));
    };
    if let Some(encrypted_token) = plan.encrypted_token.as_deref() {
        let raw_token = state
            .integration_token_cipher
            .as_ref()
            .ok_or_else(|| {
                internal_error("integration token cipher is not configured", &request_id)
            })?
            .decrypt(encrypted_token)
            .map_err(|error| internal_error(error, &request_id))?;
        let gateway = state
            .x_oauth
            .as_ref()
            .ok_or_else(|| provider_error("X OAuth is not configured", &request_id))?;
        gateway
            .revoke(&raw_token, plan.token_type_hint)
            .await
            .map_err(|error| provider_error(error, &request_id))?;
    }

    let mut finalize = state
        .database
        .pool()
        .begin()
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    verify_stamp(&mut finalize, &stamp, &request_id).await?;
    let finalized = finalize_x_disconnect(
        &mut finalize,
        current_user.id,
        plan.connection_id,
        Utc::now(),
    )
    .await
    .map_err(|error| internal_error(error, &request_id))?;
    if !finalized {
        return Err(stale_flow(&request_id));
    }
    finalize
        .commit()
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    Ok(Json(disconnected_response()))
}

#[utoipa::path(
    get,
    path = "/api/integrations/llm",
    operation_id = "getLlmIntegrations",
    tag = "integrations",
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, description = "Configured user LLM providers", body = [UserLlmIntegrationResponse]),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn get_llm_integrations(
    State(state): State<AppState>,
    headers: HeaderMap,
    current_user: AuthenticatedUser,
) -> Result<Json<Vec<UserLlmIntegrationResponse>>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    let integrations = list_user_llm_integrations(state.database.pool(), current_user.id)
        .await
        .map_err(|error| internal_error(error, &request_id))?
        .into_iter()
        .map(|integration| present_integration(&integration, &request_id))
        .collect::<Result<Vec<_>, _>>()?;
    Ok(Json(integrations))
}

#[utoipa::path(
    put,
    path = "/api/integrations/llm/{provider}",
    operation_id = "putLlmIntegration",
    tag = "integrations",
    params(("provider" = String, Path, description = "LLM provider")),
    request_body = UpsertUserLlmIntegrationRequest,
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, description = "Provider key stored", body = UserLlmIntegrationResponse),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 404, description = "Unsupported provider", body = newsly_contracts::ErrorEnvelope),
        (status = 409, description = "Stale runtime owner", body = newsly_contracts::ErrorEnvelope),
        (status = 422, description = "Validation Error", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn put_llm_integration(
    State(state): State<AppState>,
    headers: HeaderMap,
    path: Result<Path<String>, PathRejection>,
    current_user: AuthenticatedUser,
    Extension(stamp): Extension<RouteOwnershipStamp>,
    payload: Result<Json<UpsertUserLlmIntegrationRequest>, JsonRejection>,
) -> Result<Json<UserLlmIntegrationResponse>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    require_operation(&stamp, PUT_OPERATION_ID, &request_id)?;
    let provider = parse_provider(path, &request_id)?;
    let Json(payload) = decode_json(payload, &request_id)?;
    validate_api_key(&payload.api_key, &request_id)?;
    let encrypted_api_key = state
        .integration_token_cipher
        .as_ref()
        .ok_or_else(|| internal_error("integration token cipher is not configured", &request_id))?
        .encrypt(&payload.api_key)
        .map_err(|error| internal_error(error, &request_id))?;

    let mut transaction = state
        .database
        .pool()
        .begin()
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    verify_stamp(&mut transaction, &stamp, &request_id).await?;
    let integration = upsert_user_llm_integration(
        &mut transaction,
        current_user.id,
        provider.as_str(),
        &encrypted_api_key,
    )
    .await
    .map_err(|error| internal_error(error, &request_id))?;
    transaction
        .commit()
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    Ok(Json(present_integration(&integration, &request_id)?))
}

#[utoipa::path(
    delete,
    path = "/api/integrations/llm/{provider}",
    operation_id = "deleteLlmIntegration",
    tag = "integrations",
    params(("provider" = String, Path, description = "LLM provider")),
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, description = "Provider key deleted", body = DeleteUserLlmIntegrationResponse),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 404, description = "Provider or integration not found", body = newsly_contracts::ErrorEnvelope),
        (status = 409, description = "Stale runtime owner", body = newsly_contracts::ErrorEnvelope),
        (status = 422, description = "Validation Error", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn delete_llm_integration_endpoint(
    State(state): State<AppState>,
    headers: HeaderMap,
    path: Result<Path<String>, PathRejection>,
    current_user: AuthenticatedUser,
    Extension(stamp): Extension<RouteOwnershipStamp>,
) -> Result<Json<DeleteUserLlmIntegrationResponse>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    require_operation(&stamp, DELETE_OPERATION_ID, &request_id)?;
    let provider = parse_provider(path, &request_id)?;
    let mut transaction = state
        .database
        .pool()
        .begin()
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    verify_stamp(&mut transaction, &stamp, &request_id).await?;
    let deleted = delete_user_llm_integration(&mut transaction, current_user.id, provider.as_str())
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    if !deleted {
        return Err(not_found("Integration", &request_id));
    }
    transaction
        .commit()
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    Ok(Json(DeleteUserLlmIntegrationResponse {
        status: DeleteStatus::Deleted,
        provider: provider.as_str().to_owned(),
    }))
}

#[utoipa::path(
    post,
    path = "/api/integrations/llm/{provider}/test",
    operation_id = "testLlmIntegration",
    tag = "integrations",
    params(("provider" = String, Path, description = "LLM provider")),
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, description = "Provider key presence", body = UserLlmIntegrationTestResponse),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 404, description = "Unsupported provider", body = newsly_contracts::ErrorEnvelope),
        (status = 409, description = "Stale runtime owner", body = newsly_contracts::ErrorEnvelope),
        (status = 422, description = "Validation Error", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn test_llm_integration(
    State(state): State<AppState>,
    headers: HeaderMap,
    path: Result<Path<String>, PathRejection>,
    current_user: AuthenticatedUser,
    Extension(stamp): Extension<RouteOwnershipStamp>,
) -> Result<Json<UserLlmIntegrationTestResponse>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    require_operation(&stamp, TEST_OPERATION_ID, &request_id)?;
    let provider = parse_provider(path, &request_id)?;
    let ok =
        user_llm_integration_configured(state.database.pool(), current_user.id, provider.as_str())
            .await
            .map_err(|error| internal_error(error, &request_id))?;
    Ok(Json(UserLlmIntegrationTestResponse { provider, ok }))
}

fn parse_provider(
    path: Result<Path<String>, PathRejection>,
    request_id: &str,
) -> Result<UserLlmProvider, ApiError> {
    let Path(provider) =
        path.map_err(|rejection| validation_error(rejection.body_text(), request_id))?;
    UserLlmProvider::try_from(provider.as_str()).map_err(|_| not_found("Provider", request_id))
}

fn validate_api_key(api_key: &str, request_id: &str) -> Result<(), ApiError> {
    if api_key.is_empty() || api_key.chars().count() > 4_096 {
        return Err(validation_error(
            "api_key must contain between 1 and 4096 characters",
            request_id,
        ));
    }
    Ok(())
}

fn present_integration(
    integration: &UserLlmIntegrationProjection,
    request_id: &str,
) -> Result<UserLlmIntegrationResponse, ApiError> {
    let provider = UserLlmProvider::try_from(integration.provider.as_str())
        .map_err(|error| internal_error(error, request_id))?;
    Ok(UserLlmIntegrationResponse {
        provider,
        configured: integration.configured,
        updated_at: integration.updated_at,
    })
}

async fn persist_x_lookup_usage_best_effort(
    state: &AppState,
    stamp: &RouteOwnershipStamp,
    request_id: &str,
    user_id: i64,
    provider_user_id: &str,
) {
    let mut transaction = match state.database.pool().begin().await {
        Ok(transaction) => transaction,
        Err(error) => {
            tracing::warn!(error = %error, request_id, "X lookup usage transaction unavailable");
            return;
        }
    };
    if let Err(error) = verify_stamp(&mut transaction, stamp, request_id).await {
        tracing::warn!(
            ?error,
            request_id,
            "skipping X lookup usage after ownership change"
        );
        return;
    }
    let usage = NewXUserLookupUsage {
        request_id,
        user_id,
        provider_user_id,
    };
    if let Err(error) = record_x_user_lookup_usage(&mut transaction, &usage).await {
        tracing::warn!(error = %error, request_id, user_id, "X lookup usage insert failed");
        return;
    }
    if let Err(error) = transaction.commit().await {
        tracing::warn!(error = %error, request_id, user_id, "X lookup usage commit failed");
    }
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

fn validate_oauth_exchange(
    payload: &XOAuthExchangeRequest,
    request_id: &str,
) -> Result<(), ApiError> {
    if payload.code.is_empty() || payload.code.chars().count() > 4_096 {
        return Err(validation_error(
            "code must contain between 1 and 4096 characters",
            request_id,
        ));
    }
    if payload.state.is_empty() || payload.state.chars().count() > 255 {
        return Err(validation_error(
            "state must contain between 1 and 255 characters",
            request_id,
        ));
    }
    Ok(())
}

fn normalize_twitter_username(username: Option<&str>) -> Result<Option<String>, &'static str> {
    let Some(username) = username else {
        return Ok(None);
    };
    let username = username
        .trim()
        .strip_prefix('@')
        .unwrap_or(username.trim())
        .trim();
    if username.is_empty() {
        return Ok(None);
    }
    if username.len() > 15
        || !username
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || byte == b'_')
    {
        return Err("Twitter username must be 1-15 chars (letters, numbers, underscore)");
    }
    Ok(Some(username.to_ascii_lowercase()))
}

fn present_x_connection(connection: XConnectionProjection) -> XConnectionResponse {
    XConnectionResponse {
        provider: "x".to_owned(),
        connected: connection.connected,
        is_active: connection.is_active,
        provider_user_id: connection.provider_user_id,
        provider_username: connection.provider_username,
        scopes: connection.scopes,
        last_synced_at: connection.last_synced_at,
        last_status: connection.last_status,
        last_error: connection.last_error,
        twitter_username: connection.twitter_username,
    }
}

fn disconnected_response() -> IntegrationDisconnectResponse {
    IntegrationDisconnectResponse {
        status: IntegrationDisconnectStatus::Disconnected,
        provider: "x".to_owned(),
    }
}

fn random_urlsafe<const N: usize>() -> Result<String, getrandom::Error> {
    let mut bytes = [0_u8; N];
    random_fill(&mut bytes)?;
    Ok(URL_SAFE_NO_PAD.encode(bytes))
}

fn provider_error(error: impl std::fmt::Display, request_id: &str) -> ApiError {
    tracing::error!(error = %error, "X OAuth provider operation failed");
    ApiError::new(
        StatusCode::BAD_GATEWAY,
        "provider_error",
        "X provider request failed",
        request_id.to_owned(),
    )
    .with_retryable(true)
}

fn integration_finalize_error(error: IntegrationRepositoryError, request_id: &str) -> ApiError {
    if let IntegrationRepositoryError::Sqlx(sqlx::Error::Database(database_error)) = &error
        && database_error.constraint() == Some("uq_provider_provider_user")
    {
        return bad_request(
            "This X account is already linked to another user.",
            request_id,
        );
    }
    internal_error(error, request_id)
}

fn stale_flow(request_id: &str) -> ApiError {
    ApiError::new(
        StatusCode::CONFLICT,
        "stale_oauth_flow",
        "The X OAuth flow was superseded; start OAuth again",
        request_id.to_owned(),
    )
}
