use std::collections::{HashMap, HashSet};
use std::env;
use std::fmt::Write as _;
use std::str::FromStr;
use std::sync::OnceLock;
use std::time::Instant;

use axum::extract::rejection::{JsonRejection, PathRejection, QueryRejection};
use axum::extract::{Extension, Path, Query, State};
use axum::http::header::{CACHE_CONTROL, ETAG, IF_NONE_MATCH, VARY};
use axum::http::{HeaderMap, HeaderValue, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::{Json, Router};
use base64::Engine as _;
use base64::engine::general_purpose::{URL_SAFE, URL_SAFE_NO_PAD};
use chrono::NaiveDateTime;
use newsly_contracts::{
    AudioEpisodeKind, AudioEpisodeResponse, AudioEpisodeStatus, BRIEFING_DIG_FRAGMENT_MAX_LENGTH,
    BriefingBlockDto, BriefingDigSearchRequest, BriefingDigSearchResponse, BriefingDigSearchResult,
    BriefingDigSummarizeRequest, BriefingDigSummarizeResponse, BriefingDiscussionDto,
    BriefingFirstRunPhase, BriefingFirstRunProgress, BriefingFirstRunSourceOutcome,
    BriefingFirstRunSourceProgress, BriefingIndexResponse, BriefingLensResponse,
    BriefingLensSummary, BriefingNarrationRequest, BriefingNarrationResponse,
    BriefingReadMarkRequest, BriefingReadMarkResponse, BriefingRefreshResponse, BriefingSegmentDto,
    BriefingSourceDto, BriefingTier, ContentType,
};
use newsly_db::{
    AudioEpisodeProjection, BriefingDiscussionProjection, BriefingFirstRunProjection,
    BriefingIndexProjection, BriefingLensCursorProjection, BriefingLensPageProjection,
    BriefingLensProjection, BriefingReadMarkProjection, BriefingRepositoryError,
    BriefingSourceProjection, ContentBriefingSourceProjection, NewsBriefingSourceProjection,
    PrepareNarrationOutcome, ensure_briefing_state_version, expedite_pending_briefing_refresh,
    load_briefing_index, load_briefing_index_validator, load_briefing_lens_page,
    load_briefing_narration, mark_briefing_lens_read, mark_briefing_sources_read,
    prepare_briefing_narration, recent_briefing_dig_count, record_briefing_dig_usage,
};
use newsly_providers::{
    BriefingDigGateway, BriefingDigGatewayError, BriefingDigSummary, BriefingWebSearchResult,
};
use newsly_queue::{EnqueueRequest, QueueError, QueueKernel, TaskType};
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value, json};
use sha2::{Digest, Sha256};

use crate::auth::AuthenticatedUser;
use crate::error::ApiError;
use crate::gateway::RouteOwnershipStamp;
use crate::write_support::{
    bad_request, decode_json, internal_error, not_found, require_operation, verify_stamp,
};
use crate::{AppState, request_id_from_headers};

const READ_OPERATION_ID: &str = "markBriefingRead";
const LENS_READ_OPERATION_ID: &str = "markBriefingLensesLensRead";
const REFRESH_OPERATION_ID: &str = "refreshBriefing";
const DIG_SEARCH_OPERATION_ID: &str = "digBriefingSearch";
const DIG_SUMMARIZE_OPERATION_ID: &str = "digBriefingSummarize";
const LEGACY_NARRATION_OPERATION_ID: &str = "narrationBriefing";
const NARRATION_OPERATION_ID: &str = "chapteredBriefingNarration";
const BRIEFING_LENS_PAGE_MAX: usize = 12;
const DIG_SYSTEM_PROMPT: &str = concat!(
    "You expand a selected fragment from a personal news briefing into a grounded mini-explainer. ",
    "Use only the passage context and the numbered search results; never invent facts. Structure: ",
    "first a 2-3 sentence paragraph explaining the fragment in the passage's context; then 3 to 5 ",
    "bullet lines, each starting with '- ', each carrying the most concrete facts available ",
    "(numbers, dates, names, mechanisms) and ending with its source number like [2]; include one ",
    "or two short verbatim quotes from the search results in \"double quotes\" with their citation, ",
    "where a striking phrase exists; then one closing sentence on why it matters to the reader. ",
    "Format with light markdown: **bold** the two to four most load-bearing terms or figures and use ",
    "'-' bullets — no headings, no preamble, no 'based on the search results'. Aim for 160-240 words."
);

static DIG_GATEWAY: OnceLock<Result<BriefingDigGateway, String>> = OnceLock::new();

pub(super) fn router() -> Router<AppState> {
    Router::new()
        .route("/api/briefing", get(get_index))
        .route("/api/briefing/lenses/{lens_key}", get(get_lens))
        .route("/api/briefing/read-marks", post(mark_read))
        .route(
            "/api/briefing/lenses/{lens_key}/read-marks",
            post(mark_lens_read),
        )
        .route("/api/briefing/refresh", post(refresh))
        .route("/api/briefing/dig/search", post(dig_search))
        .route("/api/briefing/dig/summarize", post(dig_summarize))
        .route("/api/briefing/narration", post(legacy_narration))
        .route("/api/briefing/narrations", post(chaptered_narration))
        .route(
            "/api/briefing/narrations/{episode_group_id}",
            get(narration_status),
        )
}

#[utoipa::path(
    get,
    path = "/api/briefing",
    operation_id = "getBriefingIndex",
    tag = "briefing",
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, description = "Successful Response", body = BriefingIndexResponse),
        (status = 304, description = "Not Modified"),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn get_index(
    State(state): State<AppState>,
    headers: HeaderMap,
    current_user: AuthenticatedUser,
) -> Result<Response, ApiError> {
    let request_id = request_id_from_headers(&headers);
    if let Some(if_none_match) = headers
        .get(IF_NONE_MATCH)
        .and_then(|value| value.to_str().ok())
    {
        let validator = load_briefing_index_validator(state.database.pool(), current_user.id)
            .await
            .map_err(|error| internal_error(error, &request_id))?;
        let etag = briefing_etag(
            current_user.id,
            validator.version,
            validator.first_run_id,
            validator.first_run_revision,
        );
        if if_none_match == etag {
            let mut response = StatusCode::NOT_MODIFIED.into_response();
            apply_cache_headers(response.headers_mut(), &etag);
            return Ok(response);
        }
    }
    let projection = load_briefing_index(state.database.pool(), current_user.id)
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    let response_body = present_index(projection, &request_id)?;
    let etag = briefing_etag(
        current_user.id,
        response_body.version,
        response_body.first_run.as_ref().map_or(0, |run| run.run_id),
        response_body
            .first_run
            .as_ref()
            .map_or(0, |run| run.revision),
    );
    let mut response = Json(response_body).into_response();
    apply_cache_headers(response.headers_mut(), &etag);
    Ok(response)
}

#[derive(Debug, Default, Deserialize)]
pub(super) struct LensQuery {
    limit: Option<usize>,
    cursor: Option<String>,
}

#[utoipa::path(
    get,
    path = "/api/briefing/lenses/{lens_key}",
    operation_id = "getBriefingLensesLens",
    tag = "briefing",
    params(
        ("lens_key" = String, Path, description = "Briefing Lens key"),
        ("limit" = Option<usize>, Query, minimum = 1, maximum = 12),
        ("cursor" = Option<String>, Query, max_length = 512)
    ),
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, description = "Successful Response", body = BriefingLensResponse),
        (status = 400, description = "Invalid cursor", body = newsly_contracts::ErrorEnvelope),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 404, description = "Briefing Lens not found", body = newsly_contracts::ErrorEnvelope),
        (status = 409, description = "Stale cursor", body = newsly_contracts::ErrorEnvelope),
        (status = 422, description = "Validation Error", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn get_lens(
    State(state): State<AppState>,
    headers: HeaderMap,
    path: Result<Path<String>, PathRejection>,
    query: Result<Query<LensQuery>, QueryRejection>,
    current_user: AuthenticatedUser,
) -> Result<Json<BriefingLensResponse>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    let Path(lens_key) = path.map_err(|error| validation_error(error.body_text(), &request_id))?;
    let Query(query) = query.map_err(|error| validation_error(error.body_text(), &request_id))?;
    if query
        .limit
        .is_some_and(|limit| !(1..=BRIEFING_LENS_PAGE_MAX).contains(&limit))
    {
        return Err(validation_error(
            "limit must be between 1 and 12",
            &request_id,
        ));
    }
    if query
        .cursor
        .as_ref()
        .is_some_and(|cursor| cursor.is_empty() || cursor.chars().count() > 512)
    {
        return Err(validation_error(
            "cursor must contain between 1 and 512 characters",
            &request_id,
        ));
    }
    let cursor = query
        .cursor
        .as_deref()
        .map(decode_lens_cursor)
        .transpose()
        .map_err(|message| bad_request(message, &request_id))?;
    let projection = load_briefing_lens_page(
        state.database.pool(),
        current_user.id,
        &lens_key,
        query.limit,
        cursor.as_ref(),
    )
    .await
    .map_err(|error| lens_repository_error(error, &request_id))?
    .ok_or_else(|| not_found("Briefing lens", &request_id))?;
    Ok(Json(present_lens(&projection, &request_id)?))
}

#[utoipa::path(
    post,
    path = "/api/briefing/read-marks",
    operation_id = "markBriefingRead",
    tag = "briefing",
    request_body = BriefingReadMarkRequest,
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, description = "Successful Response", body = BriefingReadMarkResponse),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 409, description = "Stale runtime owner", body = newsly_contracts::ErrorEnvelope),
        (status = 422, description = "Validation Error", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn mark_read(
    State(state): State<AppState>,
    headers: HeaderMap,
    current_user: AuthenticatedUser,
    Extension(stamp): Extension<RouteOwnershipStamp>,
    payload: Result<Json<BriefingReadMarkRequest>, JsonRejection>,
) -> Result<Json<BriefingReadMarkResponse>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    require_operation(&stamp, READ_OPERATION_ID, &request_id)?;
    let Json(payload) = decode_json(payload, &request_id)?;
    validate_source_keys(&payload.source_keys, &request_id)?;
    let mut transaction = state
        .database
        .pool()
        .begin()
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    verify_stamp(&mut transaction, &stamp, &request_id).await?;
    let result =
        mark_briefing_sources_read(&mut transaction, current_user.id, &payload.source_keys)
            .await
            .map_err(|error| internal_error(error, &request_id))?;
    transaction
        .commit()
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    Ok(Json(present_read_mark(&result)))
}

#[utoipa::path(
    post,
    path = "/api/briefing/lenses/{lens_key}/read-marks",
    operation_id = "markBriefingLensesLensRead",
    tag = "briefing",
    params(("lens_key" = String, Path, description = "Briefing Lens key")),
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, description = "Successful Response", body = BriefingReadMarkResponse),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 404, description = "Briefing Lens not found", body = newsly_contracts::ErrorEnvelope),
        (status = 409, description = "Stale runtime owner", body = newsly_contracts::ErrorEnvelope),
        (status = 422, description = "Validation Error", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn mark_lens_read(
    State(state): State<AppState>,
    headers: HeaderMap,
    path: Result<Path<String>, PathRejection>,
    current_user: AuthenticatedUser,
    Extension(stamp): Extension<RouteOwnershipStamp>,
) -> Result<Json<BriefingReadMarkResponse>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    require_operation(&stamp, LENS_READ_OPERATION_ID, &request_id)?;
    let Path(lens_key) = path.map_err(|error| validation_error(error.body_text(), &request_id))?;
    let mut transaction = state
        .database
        .pool()
        .begin()
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    verify_stamp(&mut transaction, &stamp, &request_id).await?;
    let result = mark_briefing_lens_read(&mut transaction, current_user.id, &lens_key)
        .await
        .map_err(|error| internal_error(error, &request_id))?
        .ok_or_else(|| not_found("Briefing lens", &request_id))?;
    transaction
        .commit()
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    Ok(Json(present_read_mark(&result)))
}

#[utoipa::path(
    post,
    path = "/api/briefing/refresh",
    operation_id = "refreshBriefing",
    tag = "briefing",
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, description = "Successful Response", body = BriefingRefreshResponse),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 409, description = "Stale runtime owner", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn refresh(
    State(state): State<AppState>,
    headers: HeaderMap,
    current_user: AuthenticatedUser,
    Extension(stamp): Extension<RouteOwnershipStamp>,
) -> Result<Json<BriefingRefreshResponse>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    require_operation(&stamp, REFRESH_OPERATION_ID, &request_id)?;
    let mut transaction = state
        .database
        .pool()
        .begin()
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    verify_stamp(&mut transaction, &stamp, &request_id).await?;
    let version = ensure_briefing_state_version(&mut transaction, current_user.id)
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    let available_at = chrono::Utc::now();
    let mut request = EnqueueRequest::new(TaskType::BriefingRefresh);
    request.payload = Some(
        json!({"user_id": current_user.id, "mode": "append"})
            .as_object()
            .expect("Briefing refresh payload is an object")
            .clone(),
    );
    request.dedupe_key = Some(format!("briefing_refresh:{}:append", current_user.id));
    request.owner_user_id = Some(current_user.id);
    request.available_at = Some(available_at);
    let batch = QueueKernel::new(state.database.pool().clone())
        .enqueue_many_in_transaction(&mut transaction, vec![request])
        .await
        .map_err(|error| queue_error(error, &request_id))?;
    let task_id = batch
        .task_ids
        .first()
        .copied()
        .ok_or_else(|| internal_error("refresh queue returned no task", &request_id))?;
    let inserted = batch.inserted_task_ids.contains(&task_id);
    let expedited = if inserted {
        false
    } else {
        expedite_pending_briefing_refresh(&mut transaction, task_id, available_at)
            .await
            .map_err(|error| internal_error(error, &request_id))?
    };
    transaction
        .commit()
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    Ok(Json(BriefingRefreshResponse {
        enqueued: inserted || expedited,
        version,
    }))
}

#[utoipa::path(
    post,
    path = "/api/briefing/dig/search",
    operation_id = "digBriefingSearch",
    tag = "briefing",
    request_body = BriefingDigSearchRequest,
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, description = "Successful Response", body = BriefingDigSearchResponse),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 409, description = "Stale runtime owner", body = newsly_contracts::ErrorEnvelope),
        (status = 422, description = "Validation Error", body = newsly_contracts::ErrorEnvelope),
        (status = 503, description = "Search provider unavailable", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn dig_search(
    State(state): State<AppState>,
    headers: HeaderMap,
    current_user: AuthenticatedUser,
    Extension(stamp): Extension<RouteOwnershipStamp>,
    payload: Result<Json<BriefingDigSearchRequest>, JsonRejection>,
) -> Result<Json<BriefingDigSearchResponse>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    require_operation(&stamp, DIG_SEARCH_OPERATION_ID, &request_id)?;
    let Json(payload) = decode_json(payload, &request_id)?;
    validate_fragment(&payload.fragment, &request_id)?;
    verify_external_operation(&state, &stamp, &request_id).await?;
    let started_at = Instant::now();
    let results = dig_gateway(&request_id)?
        .search(&truncate_chars(&payload.fragment, 200))
        .await
        .map_err(|error| provider_error(&error, &request_id))?;
    let elapsed_ms = elapsed_millis(started_at);
    persist_dig_usage(
        &state,
        &stamp,
        current_user.id,
        "briefing_dig.search",
        "exa",
        "search",
        &request_id,
        None,
        None,
        json!({"result_count": results.len()}),
    )
    .await?;
    Ok(Json(BriefingDigSearchResponse {
        results: results.into_iter().map(present_search_result).collect(),
        elapsed_ms,
    }))
}

#[utoipa::path(
    post,
    path = "/api/briefing/dig/summarize",
    operation_id = "digBriefingSummarize",
    tag = "briefing",
    request_body = BriefingDigSummarizeRequest,
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, description = "Successful Response", body = BriefingDigSummarizeResponse),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 409, description = "Stale runtime owner", body = newsly_contracts::ErrorEnvelope),
        (status = 422, description = "Validation Error", body = newsly_contracts::ErrorEnvelope),
        (status = 429, description = "Briefing Dig limit reached", body = newsly_contracts::ErrorEnvelope),
        (status = 503, description = "Model provider unavailable", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn dig_summarize(
    State(state): State<AppState>,
    headers: HeaderMap,
    current_user: AuthenticatedUser,
    Extension(stamp): Extension<RouteOwnershipStamp>,
    payload: Result<Json<BriefingDigSummarizeRequest>, JsonRejection>,
) -> Result<Json<BriefingDigSummarizeResponse>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    require_operation(&stamp, DIG_SUMMARIZE_OPERATION_ID, &request_id)?;
    let Json(payload) = decode_json(payload, &request_id)?;
    validate_fragment(&payload.fragment, &request_id)?;
    if payload.passage_context.chars().count() > 2_000 {
        return Err(validation_error(
            "passage_context must contain at most 2000 characters",
            &request_id,
        ));
    }
    let hourly_limit = dig_hourly_limit();
    if hourly_limit > 0
        && recent_briefing_dig_count(state.database.pool(), current_user.id)
            .await
            .map_err(|error| internal_error(error, &request_id))?
            >= hourly_limit
    {
        return Err(ApiError::new(
            StatusCode::TOO_MANY_REQUESTS,
            "rate_limited",
            "Briefing dig limit reached",
            request_id,
        )
        .with_retryable(true));
    }
    verify_external_operation(&state, &stamp, &request_id).await?;
    let prompt = summary_prompt(&payload);
    let started_at = Instant::now();
    let summary = dig_gateway(&request_id)?
        .summarize(DIG_SYSTEM_PROMPT.to_owned(), prompt)
        .await
        .map_err(|error| provider_error(&error, &request_id))?;
    let elapsed_ms = elapsed_millis(started_at);
    persist_summary_usage(&state, &stamp, current_user.id, &request_id, &summary).await?;
    Ok(Json(BriefingDigSummarizeResponse {
        summary: summary.text,
        model: summary.model,
        elapsed_ms,
    }))
}

#[derive(Debug, Deserialize)]
pub(super) struct NarrationDeliveryQuery {
    #[serde(default = "default_delivery")]
    delivery: String,
}

fn default_delivery() -> String {
    "background".to_owned()
}

#[utoipa::path(
    post,
    path = "/api/briefing/narration",
    operation_id = "narrationBriefing",
    tag = "briefing",
    request_body = BriefingNarrationRequest,
    params(("delivery" = Option<String>, Query, description = "background or stream")),
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, description = "Successful Response", body = AudioEpisodeResponse),
        (status = 400, description = "No narration available", body = newsly_contracts::ErrorEnvelope),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 404, description = "Briefing Lens not found", body = newsly_contracts::ErrorEnvelope),
        (status = 409, description = "Stale runtime owner", body = newsly_contracts::ErrorEnvelope),
        (status = 422, description = "Validation Error", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn legacy_narration(
    State(state): State<AppState>,
    headers: HeaderMap,
    current_user: AuthenticatedUser,
    Extension(stamp): Extension<RouteOwnershipStamp>,
    query: Result<Query<NarrationDeliveryQuery>, QueryRejection>,
    payload: Result<Json<BriefingNarrationRequest>, JsonRejection>,
) -> Result<Json<AudioEpisodeResponse>, ApiError> {
    let episodes = create_narration(
        &state,
        &headers,
        current_user.id,
        &stamp,
        LEGACY_NARRATION_OPERATION_ID,
        false,
        query,
        payload,
    )
    .await?;
    let episode = episodes.into_iter().next().ok_or_else(|| {
        internal_error(
            "legacy narration has no episode",
            &request_id_from_headers(&headers),
        )
    })?;
    present_audio_episode(episode, &request_id_from_headers(&headers)).map(Json)
}

#[utoipa::path(
    post,
    path = "/api/briefing/narrations",
    operation_id = "chapteredBriefingNarration",
    tag = "briefing",
    request_body = BriefingNarrationRequest,
    params(("delivery" = Option<String>, Query, description = "background or stream")),
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, description = "Successful Response", body = BriefingNarrationResponse),
        (status = 400, description = "No narration available", body = newsly_contracts::ErrorEnvelope),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 404, description = "Briefing Lens not found", body = newsly_contracts::ErrorEnvelope),
        (status = 409, description = "Stale runtime owner", body = newsly_contracts::ErrorEnvelope),
        (status = 422, description = "Validation Error", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn chaptered_narration(
    State(state): State<AppState>,
    headers: HeaderMap,
    current_user: AuthenticatedUser,
    Extension(stamp): Extension<RouteOwnershipStamp>,
    query: Result<Query<NarrationDeliveryQuery>, QueryRejection>,
    payload: Result<Json<BriefingNarrationRequest>, JsonRejection>,
) -> Result<Json<BriefingNarrationResponse>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    let episodes = create_narration(
        &state,
        &headers,
        current_user.id,
        &stamp,
        NARRATION_OPERATION_ID,
        true,
        query,
        payload,
    )
    .await?;
    present_narration(episodes, &request_id).map(Json)
}

#[utoipa::path(
    get,
    path = "/api/briefing/narrations/{episode_group_id}",
    operation_id = "narrationBriefingStatus",
    tag = "briefing",
    params(("episode_group_id" = String, Path, description = "Narration group ID")),
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, description = "Successful Response", body = BriefingNarrationResponse),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 404, description = "Briefing narration not found", body = newsly_contracts::ErrorEnvelope),
        (status = 422, description = "Validation Error", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn narration_status(
    State(state): State<AppState>,
    headers: HeaderMap,
    path: Result<Path<String>, PathRejection>,
    current_user: AuthenticatedUser,
) -> Result<Json<BriefingNarrationResponse>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    let Path(group_id) = path.map_err(|error| validation_error(error.body_text(), &request_id))?;
    let episodes = load_briefing_narration(state.database.pool(), current_user.id, &group_id)
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    if episodes.is_empty() {
        return Err(not_found("Briefing narration", &request_id));
    }
    present_narration(episodes, &request_id).map(Json)
}

#[allow(clippy::too_many_arguments)]
async fn create_narration(
    state: &AppState,
    headers: &HeaderMap,
    user_id: i64,
    stamp: &RouteOwnershipStamp,
    operation_id: &str,
    chaptered: bool,
    query: Result<Query<NarrationDeliveryQuery>, QueryRejection>,
    payload: Result<Json<BriefingNarrationRequest>, JsonRejection>,
) -> Result<Vec<AudioEpisodeProjection>, ApiError> {
    let request_id = request_id_from_headers(headers);
    require_operation(stamp, operation_id, &request_id)?;
    let Query(query) = query.map_err(|error| validation_error(error.body_text(), &request_id))?;
    if !matches!(query.delivery.as_str(), "background" | "stream") {
        return Err(validation_error(
            "delivery must be background or stream",
            &request_id,
        ));
    }
    let Json(payload) = decode_json(payload, &request_id)?;
    let lens_key = payload.lens_key.as_str();
    if lens_key.is_empty() || lens_key.chars().count() > 64 {
        return Err(validation_error(
            "lens_key must contain between 1 and 64 characters",
            &request_id,
        ));
    }
    let mut transaction = state
        .database
        .pool()
        .begin()
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    verify_stamp(&mut transaction, stamp, &request_id).await?;
    let episodes = match prepare_briefing_narration(&mut transaction, user_id, lens_key, chaptered)
        .await
        .map_err(|error| internal_error(error, &request_id))?
    {
        PrepareNarrationOutcome::Ready(episodes) => episodes,
        PrepareNarrationOutcome::LensNotFound => {
            return Err(not_found("Briefing lens", &request_id));
        }
        PrepareNarrationOutcome::Empty => {
            return Err(bad_request(
                "No briefing narration is available",
                &request_id,
            ));
        }
    };
    let requests = episodes
        .iter()
        .filter(|episode| episode.status != "completed")
        .map(|episode| {
            let mut request = EnqueueRequest::new(TaskType::GenerateAudioEpisode);
            request.payload = Some(
                json!({"audio_episode_id": episode.id, "user_id": user_id})
                    .as_object()
                    .expect("audio episode payload is an object")
                    .clone(),
            );
            request.dedupe_key = Some(format!("audio_episode:{}", episode.id));
            request.owner_user_id = Some(user_id);
            request
        })
        .collect::<Vec<_>>();
    if !requests.is_empty() {
        QueueKernel::new(state.database.pool().clone())
            .enqueue_many_in_transaction(&mut transaction, requests)
            .await
            .map_err(|error| queue_error(error, &request_id))?;
    }
    transaction
        .commit()
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    Ok(episodes)
}

fn present_index(
    projection: BriefingIndexProjection,
    request_id: &str,
) -> Result<BriefingIndexResponse, ApiError> {
    let mut segments_by_lens = HashMap::<i64, Vec<_>>::new();
    for segment in &projection.segments {
        segments_by_lens
            .entry(segment.lens_id)
            .or_default()
            .push(segment);
    }
    let mut summaries = projection
        .lenses
        .iter()
        .map(|lens| {
            let segments = segments_by_lens.get(&lens.id).cloned().unwrap_or_default();
            let source_keys = segments
                .iter()
                .flat_map(|segment| segment.source_keys.iter().cloned())
                .collect::<HashSet<_>>();
            present_lens_summary(
                lens,
                segments.len(),
                &source_keys,
                &projection.read_source_keys,
                request_id,
            )
        })
        .collect::<Result<Vec<_>, _>>()?;
    let ready_keys = summaries
        .iter()
        .filter(|summary| summary.segment_count > 0)
        .map(|summary| summary.key.clone())
        .collect::<Vec<_>>();
    let first_run = projection
        .first_run
        .map(|run| present_first_run(&run, ready_keys));
    if first_run.is_some() {
        summaries.retain(|summary| {
            summary.segment_count > 0 || projection.pending_lens_keys.contains(&summary.key)
        });
    }
    Ok(BriefingIndexResponse {
        version: projection.state.version,
        masthead_title: projection.state.masthead_title,
        masthead_deck: projection.state.masthead_deck,
        generated_at: projection
            .segments
            .iter()
            .map(|segment| segment.created_at)
            .max(),
        lenses: summaries,
        first_run,
    })
}

fn present_first_run(
    run: &BriefingFirstRunProjection,
    ready_category_keys: Vec<String>,
) -> BriefingFirstRunProgress {
    let connected_source_count = run.sources.len();
    let mut completed_sources = Vec::new();
    let mut pending = run
        .sources
        .iter()
        .filter(|source| !matches!(source.status.as_str(), "processed" | "unavailable"))
        .collect::<Vec<_>>();
    pending.sort_by_key(|source| source.position);
    for source in &run.sources {
        let outcome = match source.status.as_str() {
            "processed" => Some(BriefingFirstRunSourceOutcome::Processed),
            "unavailable" => Some(BriefingFirstRunSourceOutcome::Unavailable),
            _ => None,
        };
        if let Some(outcome) = outcome {
            completed_sources.push(BriefingFirstRunSourceProgress {
                display_name: source.display_name.clone(),
                processed_item_count: source.processed_item_count.max(0),
                outcome,
            });
        }
    }
    let all_done = completed_sources.len() == connected_source_count;
    let phase = if all_done && !ready_category_keys.is_empty() {
        BriefingFirstRunPhase::Ready
    } else if all_done {
        BriefingFirstRunPhase::WaitingForContent
    } else {
        BriefingFirstRunPhase::Active
    };
    BriefingFirstRunProgress {
        run_id: run.run_id,
        revision: run.revision,
        phase,
        connected_source_count,
        completed_sources,
        active_sources: pending
            .iter()
            .take(2)
            .map(|source| source.display_name.clone())
            .collect(),
        queued_sources: pending
            .iter()
            .skip(2)
            .map(|source| source.display_name.clone())
            .collect(),
        ready_category_keys,
    }
}

fn present_lens(
    projection: &BriefingLensPageProjection,
    request_id: &str,
) -> Result<BriefingLensResponse, ApiError> {
    let all_source_keys = projection
        .all_source_keys
        .iter()
        .cloned()
        .collect::<HashSet<_>>();
    let lens = present_lens_summary(
        &projection.lens,
        projection.segment_count,
        &all_source_keys,
        &projection.read_source_keys,
        request_id,
    )?;
    let mut ordered_source_keys = Vec::new();
    let mut seen = HashSet::new();
    for key in projection
        .segments
        .iter()
        .flat_map(|segment| &segment.source_keys)
    {
        if seen.insert(key.clone()) {
            ordered_source_keys.push(key.clone());
        }
    }
    let sources = ordered_source_keys
        .iter()
        .filter_map(|key| {
            projection.sources.get(key).map(|source| {
                present_source(
                    key,
                    source,
                    projection.read_source_keys.contains(key),
                    request_id,
                )
            })
        })
        .collect::<Result<Vec<_>, _>>()?;
    let segments = projection
        .segments
        .iter()
        .map(|segment| {
            let blocks = serde_json::from_value::<Vec<BriefingBlockDto>>(segment.blocks.clone())
                .map_err(|error| internal_error(error, request_id))?;
            Ok(BriefingSegmentDto {
                id: segment.id,
                created_at: segment.created_at,
                status: segment.status.clone(),
                narration_text: segment.narration_text.clone(),
                blocks,
                source_keys: segment.source_keys.clone(),
            })
        })
        .collect::<Result<Vec<_>, ApiError>>()?;
    let next_cursor = if projection.has_more {
        projection.segments.last().map(|segment| {
            encode_lens_cursor(
                projection.lens.id,
                segment.id,
                segment.created_at.naive_utc(),
            )
        })
    } else {
        None
    };
    Ok(BriefingLensResponse {
        version: projection.state.version,
        lens,
        segments,
        sources,
        next_cursor,
        has_more: projection.has_more,
    })
}

fn present_lens_summary(
    lens: &BriefingLensProjection,
    segment_count: usize,
    source_keys: &HashSet<String>,
    read_keys: &HashSet<String>,
    request_id: &str,
) -> Result<BriefingLensSummary, ApiError> {
    Ok(BriefingLensSummary {
        key: lens.key.clone(),
        tier: BriefingTier::try_from(lens.tier.as_str())
            .map_err(|error| internal_error(error, request_id))?,
        title: lens.title.clone(),
        deck: lens.deck.clone(),
        position: lens.position,
        segment_count,
        unread_source_count: source_keys.difference(read_keys).count(),
    })
}

fn present_source(
    source_key: &str,
    source: &BriefingSourceProjection,
    read: bool,
    request_id: &str,
) -> Result<BriefingSourceDto, ApiError> {
    match source {
        BriefingSourceProjection::Content(source) => {
            present_content_source(source_key, source, read, request_id)
        }
        BriefingSourceProjection::News(source) => Ok(present_news_source(source_key, source, read)),
    }
}

fn present_content_source(
    source_key: &str,
    source: &ContentBriefingSourceProjection,
    read: bool,
    request_id: &str,
) -> Result<BriefingSourceDto, ApiError> {
    let metadata = source.metadata.as_object().cloned().unwrap_or_default();
    let content_type = ContentType::from_str(&source.content_type)
        .map_err(|error| internal_error(error, request_id))?;
    let summary = metadata
        .get("summary")
        .and_then(extract_short_summary)
        .or_else(|| clean_string(metadata.get("excerpt")));
    let key_points = metadata_key_points(&metadata);
    let version = image_version(&metadata);
    Ok(BriefingSourceDto {
        source_key: source_key.to_owned(),
        kind: "content".to_owned(),
        id: source.id,
        title: source
            .title
            .clone()
            .unwrap_or_else(|| format!("Content {}", source.id)),
        summary,
        key_points: Some(key_points),
        url: source
            .source_url
            .clone()
            .or_else(|| Some(source.url.clone())),
        image_url: Some(versioned_image_url(
            format!("/static/images/content/{}.png", source.id),
            version.as_deref(),
        )),
        thumbnail_url: Some(versioned_image_url(
            format!("/static/images/thumbnails/{}.png", source.id),
            version.as_deref(),
        )),
        published_at: source.publication_date.or(Some(source.created_at)),
        content_type: Some(content_type),
        read,
        discussion: None,
    })
}

fn present_news_source(
    source_key: &str,
    source: &NewsBriefingSourceProjection,
    read: bool,
) -> BriefingSourceDto {
    let metadata = source.raw_metadata.as_object().cloned().unwrap_or_default();
    let version = image_version(&metadata);
    let has_image = metadata.get("image_generated_at").is_some_and(value_truthy);
    BriefingSourceDto {
        source_key: source_key.to_owned(),
        kind: "news".to_owned(),
        id: source.id,
        title: news_title(&metadata, source.summary_text.as_deref(), source.id),
        summary: source.summary_text.as_deref().and_then(clean_text),
        key_points: Some(json_string_values(&source.summary_key_points)),
        url: source
            .article_url
            .clone()
            .or_else(|| source.canonical_story_url.clone())
            .or_else(|| source.canonical_item_url.clone()),
        image_url: None,
        thumbnail_url: has_image.then(|| {
            versioned_image_url(
                format!("/static/images/news_thumbnails/{}.png", source.id),
                version.as_deref(),
            )
        }),
        published_at: source
            .published_at
            .or(source.processed_at)
            .or(Some(source.ingested_at))
            .or(Some(source.created_at)),
        content_type: Some(ContentType::News),
        read,
        discussion: discussions_enabled()
            .then(|| source.discussion.as_ref().and_then(present_discussion))
            .flatten(),
    }
}

fn present_discussion(source: &BriefingDiscussionProjection) -> Option<BriefingDiscussionDto> {
    if matches!(source.last_refresh_status.as_str(), "gone" | "unsupported") {
        return None;
    }
    if source.summary.is_none() && source.comment_count.unwrap_or(0) <= 0 {
        return None;
    }
    let summary = source.summary.as_ref().and_then(Value::as_object);
    let completed = source.summary_status == "completed"
        && summary
            .and_then(|value| value.get("overview"))
            .and_then(Value::as_str)
            .is_some();
    let summary_status = if completed {
        "completed"
    } else if source.summary_status == "failed" || source.last_refresh_status == "failed" {
        "failed"
    } else {
        "not_ready"
    };
    let overview = completed
        .then(|| {
            summary
                .and_then(|value| value.get("overview"))
                .and_then(Value::as_str)
                .map(|value| truncate_overview(value, discussion_overview_max_chars()))
        })
        .flatten();
    let top_comment = completed
        .then(|| {
            summary
                .and_then(|value| value.get("representative_comments"))
                .and_then(Value::as_array)
                .and_then(|comments| comments.first())
                .and_then(Value::as_object)
        })
        .flatten();
    let external_url = completed
        .then(|| {
            summary
                .and_then(|value| value.get("external_discussion_url"))
                .and_then(Value::as_str)
                .and_then(clean_text)
        })
        .flatten()
        .or_else(|| source.discussion_url.clone());
    Some(BriefingDiscussionDto {
        platform: source.platform.clone(),
        comment_count: source.comment_count,
        summary_status: summary_status.to_owned(),
        overview,
        top_comment_author: top_comment
            .and_then(|comment| comment.get("author"))
            .and_then(Value::as_str)
            .and_then(clean_text),
        top_comment_text: top_comment
            .and_then(|comment| comment.get("text"))
            .and_then(Value::as_str)
            .and_then(clean_text),
        external_url,
        updated_at: source
            .summary_generated_at
            .or(source.last_comments_fetched_at)
            .or(source.last_count_checked_at),
    })
}

fn present_read_mark(result: &BriefingReadMarkProjection) -> BriefingReadMarkResponse {
    BriefingReadMarkResponse {
        marked: result.marked,
        retired: result.retired,
        version: result.version,
    }
}

fn present_audio_episode(
    episode: AudioEpisodeProjection,
    request_id: &str,
) -> Result<AudioEpisodeResponse, ApiError> {
    let kind = AudioEpisodeKind::try_from(episode.kind.as_str())
        .map_err(|error| internal_error(error, request_id))?;
    let status = AudioEpisodeStatus::try_from(episode.status.as_str())
        .map_err(|error| internal_error(error, request_id))?;
    let snapshot = episode.source_snapshot.as_object();
    let source_item_ids = json_i64_values(&episode.source_item_ids);
    let source_content_ids = if let Some(id) = episode.source_content_id {
        vec![id]
    } else {
        snapshot
            .and_then(|snapshot| snapshot.get("content_ids"))
            .map(json_i64_values)
            .unwrap_or_default()
    };
    let source_count = snapshot
        .and_then(|snapshot| snapshot.get("source_count"))
        .and_then(Value::as_u64)
        .and_then(|value| usize::try_from(value).ok())
        .unwrap_or({
            if source_content_ids.is_empty() {
                source_item_ids.len()
            } else {
                source_content_ids.len()
            }
        });
    let read_policy = snapshot
        .and_then(|snapshot| snapshot.get("read_on_play"))
        .and_then(Value::as_object);
    Ok(AudioEpisodeResponse {
        id: episode.id,
        kind,
        status,
        title: episode.title,
        source_content_id: episode.source_content_id,
        source_item_ids,
        source_content_ids,
        source_count,
        source_titles: snapshot
            .and_then(|snapshot| snapshot.get("items"))
            .and_then(Value::as_array)
            .map(|items| {
                items
                    .iter()
                    .filter_map(|item| item.get("title").and_then(Value::as_str))
                    .filter_map(clean_text)
                    .collect()
            })
            .unwrap_or_default(),
        read_on_play_content_ids: read_policy
            .and_then(|policy| policy.get("content_ids"))
            .map(json_i64_values)
            .unwrap_or_default(),
        read_on_play_news_item_ids: read_policy
            .and_then(|policy| policy.get("news_item_ids"))
            .map(json_i64_values)
            .unwrap_or_default(),
        duration_seconds: episode.duration_seconds,
        audio_url: (status == AudioEpisodeStatus::Completed
            && episode.audio_storage_path.is_some())
        .then(|| format!("/api/content/audio-episodes/{}/audio", episode.id)),
        stream_url: Some(format!("/api/content/audio-episodes/{}/stream", episode.id)),
        script_text: episode.script_text,
        error_message: (status == AudioEpisodeStatus::Failed)
            .then(|| newsly_db::public_audio_episode_error_message().to_owned()),
        created_at: episode.created_at,
        updated_at: episode.updated_at,
    })
}

fn present_narration(
    mut episodes: Vec<AudioEpisodeProjection>,
    request_id: &str,
) -> Result<BriefingNarrationResponse, ApiError> {
    episodes.sort_by_key(|episode| (episode.chapter_index.unwrap_or(0), episode.id));
    let first = episodes
        .first()
        .ok_or_else(|| internal_error("Briefing narration has no chapters", request_id))?;
    let snapshot = first
        .source_snapshot
        .as_object()
        .ok_or_else(|| internal_error("Briefing narration metadata is incomplete", request_id))?;
    let group_id = first
        .episode_group_id
        .as_deref()
        .and_then(clean_text)
        .ok_or_else(|| internal_error("Briefing narration group is missing", request_id))?;
    let lens_key = snapshot
        .get("lens_key")
        .and_then(Value::as_str)
        .and_then(clean_text)
        .ok_or_else(|| internal_error("Briefing narration lens is missing", request_id))?;
    let lens_title = snapshot
        .get("lens_title")
        .and_then(Value::as_str)
        .and_then(clean_text)
        .unwrap_or_else(|| "Briefing".to_owned());
    let first_status = AudioEpisodeStatus::try_from(first.status.as_str())
        .map_err(|error| internal_error(error, request_id))?;
    let statuses = episodes
        .iter()
        .map(|episode| {
            AudioEpisodeStatus::try_from(episode.status.as_str())
                .map_err(|error| internal_error(error, request_id))
        })
        .collect::<Result<Vec<_>, _>>()?;
    let playable = first_status == AudioEpisodeStatus::Completed;
    let status = if statuses
        .iter()
        .all(|status| *status == AudioEpisodeStatus::Completed)
    {
        AudioEpisodeStatus::Completed
    } else if first_status == AudioEpisodeStatus::Failed {
        AudioEpisodeStatus::Failed
    } else if playable || statuses.contains(&AudioEpisodeStatus::Processing) {
        AudioEpisodeStatus::Processing
    } else {
        AudioEpisodeStatus::Pending
    };
    let duration_seconds = episodes
        .iter()
        .map(|episode| episode.duration_seconds.unwrap_or(0).max(0))
        .fold(0_i32, i32::saturating_add);
    let chapters = episodes
        .into_iter()
        .map(|episode| present_audio_episode(episode, request_id))
        .collect::<Result<Vec<_>, _>>()?;
    Ok(BriefingNarrationResponse {
        episode_group_id: group_id,
        lens_key,
        title: format!("{lens_title} briefing"),
        status,
        playable,
        duration_seconds,
        chapters,
    })
}

async fn verify_external_operation(
    state: &AppState,
    stamp: &RouteOwnershipStamp,
    request_id: &str,
) -> Result<(), ApiError> {
    let mut transaction = state
        .database
        .pool()
        .begin()
        .await
        .map_err(|error| internal_error(error, request_id))?;
    verify_stamp(&mut transaction, stamp, request_id).await?;
    transaction
        .commit()
        .await
        .map_err(|error| internal_error(error, request_id))
}

#[allow(clippy::too_many_arguments)]
async fn persist_dig_usage(
    state: &AppState,
    stamp: &RouteOwnershipStamp,
    user_id: i64,
    operation: &str,
    provider: &str,
    model: &str,
    request_id: &str,
    input_tokens: Option<i64>,
    output_tokens: Option<i64>,
    metadata: Value,
) -> Result<(), ApiError> {
    let mut transaction = state
        .database
        .pool()
        .begin()
        .await
        .map_err(|error| internal_error(error, request_id))?;
    verify_stamp(&mut transaction, stamp, request_id).await?;
    record_briefing_dig_usage(
        &mut transaction,
        user_id,
        operation,
        provider,
        model,
        request_id,
        input_tokens,
        output_tokens,
        metadata,
    )
    .await
    .map_err(|error| internal_error(error, request_id))?;
    transaction
        .commit()
        .await
        .map_err(|error| internal_error(error, request_id))
}

async fn persist_summary_usage(
    state: &AppState,
    stamp: &RouteOwnershipStamp,
    user_id: i64,
    request_id: &str,
    summary: &BriefingDigSummary,
) -> Result<(), ApiError> {
    let (provider, model) = summary
        .model
        .split_once(':')
        .unwrap_or(("unknown", summary.model.as_str()));
    persist_dig_usage(
        state,
        stamp,
        user_id,
        "briefing_dig.summarize",
        provider,
        model,
        request_id,
        i64::try_from(summary.usage.input_tokens).ok(),
        i64::try_from(summary.usage.output_tokens).ok(),
        json!({
            "request_count": summary.usage.request_count,
            "cached_input_tokens": summary.usage.cached_input_tokens,
            "reasoning_tokens": summary.usage.reasoning_tokens,
        }),
    )
    .await
}

fn dig_gateway(request_id: &str) -> Result<&'static BriefingDigGateway, ApiError> {
    DIG_GATEWAY
        .get_or_init(|| BriefingDigGateway::from_env().map_err(|error| error.to_string()))
        .as_ref()
        .map_err(|error| {
            tracing::error!(error, "Briefing Dig provider configuration failed");
            ApiError::new(
                StatusCode::SERVICE_UNAVAILABLE,
                "provider_unavailable",
                "Briefing Dig is unavailable",
                request_id.to_owned(),
            )
            .with_retryable(true)
        })
}

fn provider_error(error: &BriefingDigGatewayError, request_id: &str) -> ApiError {
    tracing::warn!(error = %error, "Briefing Dig provider request failed");
    ApiError::new(
        StatusCode::SERVICE_UNAVAILABLE,
        "provider_unavailable",
        "Briefing Dig is temporarily unavailable",
        request_id.to_owned(),
    )
    .with_retryable(true)
}

fn present_search_result(result: BriefingWebSearchResult) -> BriefingDigSearchResult {
    BriefingDigSearchResult {
        title: result.title,
        url: result.url,
        snippet: result.snippet,
        published_date: result.published_date,
    }
}

fn summary_prompt(request: &BriefingDigSummarizeRequest) -> String {
    let results = request
        .results
        .iter()
        .enumerate()
        .map(|(index, result)| {
            let published = result
                .published_date
                .as_ref()
                .map_or_else(String::new, |date| format!(" (published {date})"));
            format!(
                "{}. {}{}\nURL: {}\nSnippet: {}",
                index + 1,
                result.title,
                published,
                result.url,
                result.snippet.as_deref().unwrap_or_default()
            )
        })
        .collect::<Vec<_>>()
        .join("\n\n");
    format!(
        "Selected fragment: {}\n\nBriefing context: {}\n\nSearch results:\n{}",
        truncate_chars(&request.fragment, 300),
        truncate_chars(&request.passage_context, 2_000),
        results
    )
}

fn validate_fragment(fragment: &str, request_id: &str) -> Result<(), ApiError> {
    let length = fragment.chars().count();
    if !(3..=BRIEFING_DIG_FRAGMENT_MAX_LENGTH).contains(&length) {
        return Err(validation_error(
            format!(
                "fragment must contain between 3 and {BRIEFING_DIG_FRAGMENT_MAX_LENGTH} characters"
            ),
            request_id,
        ));
    }
    Ok(())
}

fn validate_source_keys(source_keys: &[String], request_id: &str) -> Result<(), ApiError> {
    if source_keys.is_empty() {
        return Err(validation_error(
            "source_keys must contain at least one item",
            request_id,
        ));
    }
    Ok(())
}

fn dig_hourly_limit() -> i64 {
    env::var("BRIEFING_DIG_HOURLY_LIMIT")
        .ok()
        .and_then(|value| value.parse::<i64>().ok())
        .unwrap_or(60)
        .clamp(0, 1_000)
}

fn discussions_enabled() -> bool {
    env::var("BRIEFING_DISCUSSION_STRIP_ENABLED")
        .ok()
        .and_then(|value| parse_bool(&value))
        .unwrap_or(true)
}

fn discussion_overview_max_chars() -> usize {
    env::var("BRIEFING_DISCUSSION_OVERVIEW_MAX_CHARS")
        .ok()
        .and_then(|value| value.parse::<usize>().ok())
        .unwrap_or(280)
        .clamp(80, 900)
}

fn parse_bool(value: &str) -> Option<bool> {
    match value.trim().to_ascii_lowercase().as_str() {
        "1" | "true" | "yes" | "on" => Some(true),
        "0" | "false" | "no" | "off" => Some(false),
        _ => None,
    }
}

fn briefing_etag(user_id: i64, version: i32, run_id: i64, revision: i32) -> String {
    let digest =
        Sha256::digest(format!("briefing:{user_id}:v{version}:o{run_id}.{revision}").as_bytes());
    let mut hex = String::with_capacity(digest.len() * 2);
    for byte in digest {
        write!(&mut hex, "{byte:02x}").expect("writing to a String cannot fail");
    }
    format!("W/\"{}\"", &hex[..24])
}

fn apply_cache_headers(headers: &mut HeaderMap, etag: &str) {
    if let Ok(value) = HeaderValue::from_str(etag) {
        headers.insert(ETAG, value);
    }
    headers.insert(CACHE_CONTROL, HeaderValue::from_static("private, no-cache"));
    headers.insert(VARY, HeaderValue::from_static("Authorization"));
}

#[derive(Debug, Serialize, Deserialize)]
struct LensCursorWire {
    lens_id: i64,
    segment_id: i64,
    created_at: String,
}

fn encode_lens_cursor(lens_id: i64, segment_id: i64, created_at: NaiveDateTime) -> String {
    let payload = LensCursorWire {
        lens_id,
        segment_id,
        created_at: created_at.format("%Y-%m-%dT%H:%M:%S%.6f").to_string(),
    };
    URL_SAFE_NO_PAD.encode(serde_json::to_vec(&payload).expect("cursor serializes"))
}

fn decode_lens_cursor(value: &str) -> Result<BriefingLensCursorProjection, &'static str> {
    if value.is_empty() || value.len() > 512 {
        return Err("Malformed Briefing cursor");
    }
    let bytes = URL_SAFE_NO_PAD
        .decode(value)
        .or_else(|_| URL_SAFE.decode(value))
        .map_err(|_| "Malformed Briefing cursor")?;
    let payload = serde_json::from_slice::<LensCursorWire>(&bytes)
        .map_err(|_| "Malformed Briefing cursor")?;
    let created_at = NaiveDateTime::parse_from_str(&payload.created_at, "%Y-%m-%dT%H:%M:%S%.f")
        .map_err(|_| "Malformed Briefing cursor")?;
    if payload.lens_id <= 0 || payload.segment_id <= 0 {
        return Err("Malformed Briefing cursor");
    }
    Ok(BriefingLensCursorProjection {
        lens_id: payload.lens_id,
        segment_id: payload.segment_id,
        created_at,
    })
}

fn lens_repository_error(error: BriefingRepositoryError, request_id: &str) -> ApiError {
    match error {
        BriefingRepositoryError::CursorWrongLens => {
            bad_request("Briefing cursor belongs to another Lens", request_id)
        }
        BriefingRepositoryError::CursorAnchorMismatch => {
            bad_request("Briefing cursor anchor does not match", request_id)
        }
        BriefingRepositoryError::StaleCursor => ApiError::new(
            StatusCode::CONFLICT,
            "stale_cursor",
            "Briefing cursor anchor is no longer active",
            request_id.to_owned(),
        ),
        BriefingRepositoryError::Sqlx(error) => internal_error(error, request_id),
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
        json!({"errors": [{"message": message.into()}]})
            .as_object()
            .expect("validation details are an object")
            .clone(),
    )
}

fn queue_error(error: QueueError, request_id: &str) -> ApiError {
    tracing::error!(error = %error, "Briefing queue operation failed");
    internal_error(error, request_id)
}

fn elapsed_millis(started_at: Instant) -> u64 {
    u64::try_from(started_at.elapsed().as_millis()).unwrap_or(u64::MAX)
}

fn truncate_chars(value: &str, max: usize) -> String {
    value.chars().take(max).collect()
}

fn clean_string(value: Option<&Value>) -> Option<String> {
    value.and_then(Value::as_str).and_then(clean_text)
}

fn clean_text(value: &str) -> Option<String> {
    let cleaned = value.split_whitespace().collect::<Vec<_>>().join(" ");
    (!cleaned.is_empty()).then_some(cleaned)
}

fn extract_short_summary(value: &Value) -> Option<String> {
    if let Some(text) = value.as_str() {
        return clean_text(text);
    }
    let summary = value.as_object()?;
    for path in [
        &["one_line"][..],
        &["artifact", "payload", "overview"][..],
        &["overview"][..],
    ] {
        if let Some(text) = value_at_path(summary, path)
            .and_then(Value::as_str)
            .and_then(clean_text)
        {
            return Some(text);
        }
    }
    if summary.get("summary_type").and_then(Value::as_str) == Some("interleaved") {
        for key in ["hook", "takeaway"] {
            if let Some(text) = summary
                .get(key)
                .and_then(Value::as_str)
                .and_then(clean_text)
            {
                return Some(text);
            }
        }
    }
    if let Some(narrative) = summary
        .get("editorial_narrative")
        .and_then(Value::as_str)
        .and_then(clean_text)
    {
        return narrative.split("\n\n").next().and_then(clean_text);
    }
    if let Some(text) = summary
        .get("points")
        .and_then(Value::as_array)
        .and_then(|points| points.first())
        .and_then(|point| point.get("text"))
        .and_then(Value::as_str)
        .and_then(clean_text)
    {
        return Some(text);
    }
    for key in ["summary", "hook", "takeaway"] {
        if let Some(text) = summary
            .get(key)
            .and_then(Value::as_str)
            .and_then(clean_text)
        {
            return Some(text);
        }
    }
    None
}

fn value_at_path<'a>(root: &'a Map<String, Value>, path: &[&str]) -> Option<&'a Value> {
    let mut value = root.get(*path.first()?)?;
    for key in &path[1..] {
        value = value.get(*key)?;
    }
    Some(value)
}

fn metadata_key_points(metadata: &Map<String, Value>) -> Vec<String> {
    let candidates = metadata.get("key_points").or_else(|| {
        metadata
            .get("summary")
            .and_then(Value::as_object)
            .and_then(|summary| summary.get("key_points").or_else(|| summary.get("points")))
    });
    candidates
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(|item| {
            item.as_str()
                .or_else(|| item.get("text").and_then(Value::as_str))
                .and_then(clean_text)
        })
        .take(6)
        .collect()
}

fn image_version(metadata: &Map<String, Value>) -> Option<String> {
    metadata
        .get("image_version")
        .filter(|value| value_truthy(value))
        .or_else(|| {
            metadata
                .get("thumbnail_version")
                .filter(|value| value_truthy(value))
        })
        .and_then(|value| match value {
            Value::String(value) => clean_text(value),
            Value::Number(value) => Some(value.to_string()),
            Value::Bool(true) => Some("True".to_owned()),
            _ => None,
        })
}

fn versioned_image_url(base: String, version: Option<&str>) -> String {
    let Some(version) = version else {
        return base;
    };
    let Ok(origin) = reqwest::Url::parse("https://newsly.invalid") else {
        return base;
    };
    let Ok(mut parsed) = origin.join(&base) else {
        return base;
    };
    parsed.query_pairs_mut().append_pair("v", version);
    parsed.query().map_or_else(
        || parsed.path().to_owned(),
        |query| format!("{}?{query}", parsed.path()),
    )
}

fn news_title(metadata: &Map<String, Value>, summary: Option<&str>, id: i64) -> String {
    let summary_title = metadata
        .get("summary")
        .and_then(|value| value.get("title"))
        .and_then(Value::as_str)
        .and_then(clean_text);
    let related_title = metadata
        .get("cluster")
        .and_then(|value| value.get("related_titles"))
        .and_then(Value::as_array)
        .and_then(|titles| titles.first())
        .and_then(Value::as_str)
        .and_then(clean_text);
    let article_title = metadata
        .get("article")
        .and_then(|value| value.get("title"))
        .and_then(Value::as_str)
        .and_then(clean_text);
    summary_title
        .or(related_title)
        .or(article_title)
        .or_else(|| {
            summary
                .and_then(clean_text)
                .map(|value| truncate_title(&value))
        })
        .unwrap_or_else(|| format!("News item {id}"))
}

fn truncate_title(value: &str) -> String {
    if value.chars().count() <= 120 {
        return value.to_owned();
    }
    let mut excerpt = value.chars().take(120).collect::<String>();
    while excerpt.ends_with(char::is_whitespace) {
        excerpt.pop();
    }
    format!("{excerpt}…")
}

fn json_string_values(value: &Value) -> Vec<String> {
    value
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .map(str::to_owned)
        .collect()
}

fn json_i64_values(value: &Value) -> Vec<i64> {
    value
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(|value| value.as_i64().or_else(|| value.as_str()?.parse().ok()))
        .collect()
}

fn value_truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(value) => *value,
        Value::String(value) => !value.trim().is_empty(),
        Value::Array(value) => !value.is_empty(),
        Value::Object(value) => !value.is_empty(),
        Value::Number(value) => value.as_i64() != Some(0),
    }
}

fn truncate_overview(value: &str, max_chars: usize) -> String {
    let cleaned = value.split_whitespace().collect::<Vec<_>>().join(" ");
    if cleaned.chars().count() <= max_chars {
        return cleaned;
    }
    if max_chars <= 3 {
        return cleaned.chars().take(max_chars).collect();
    }
    let hard_limit = max_chars.saturating_sub(3);
    let candidate = cleaned.chars().take(hard_limit + 1).collect::<String>();
    if let Some((index, punctuation)) = candidate
        .char_indices()
        .rfind(|(_, character)| matches!(character, '.' | '!' | '?'))
        && candidate[..index].chars().count() >= 24.max(max_chars * 45 / 100)
    {
        let mut sentence = candidate[..index].to_owned();
        sentence.push(punctuation);
        return sentence;
    }
    let hard = cleaned.chars().take(hard_limit).collect::<String>();
    let truncated = hard
        .rsplit_once(' ')
        .map_or(hard.as_str(), |(prefix, _)| prefix)
        .trim()
        .trim_end_matches(['.', ',', ';', ':']);
    format!("{truncated}...").chars().take(max_chars).collect()
}
