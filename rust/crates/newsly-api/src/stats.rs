use axum::extract::State;
use axum::http::HeaderMap;
use axum::routing::get;
use axum::{Json, Router};
use newsly_contracts::{
    BadgeStatsResponse, LongFormStatsResponse, ProcessingCountResponse, UnreadCountsResponse,
};
use newsly_db::{
    ProcessingCountsProjection, UnreadCountsProjection, get_long_form_unread_count,
    get_processing_counts, get_unread_counts,
};

use crate::auth::AuthenticatedUser;
use crate::error::ApiError;
use crate::write_support::internal_error;
use crate::{AppState, request_id_from_headers};

pub(super) fn router() -> Router<AppState> {
    Router::new()
        .route("/api/content/stats/unread-counts", get(unread_counts))
        .route("/api/content/stats/processing-count", get(processing_count))
        .route("/api/content/stats/badge", get(badge_stats))
        .route("/api/content/stats/long-form", get(long_form_stats))
}

#[utoipa::path(
    get,
    path = "/api/content/stats/unread-counts",
    operation_id = "getContentStatsUnreadCounts",
    tag = "content",
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, description = "Unread counts", body = UnreadCountsResponse),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn unread_counts(
    State(state): State<AppState>,
    headers: HeaderMap,
    current_user: AuthenticatedUser,
) -> Result<Json<UnreadCountsResponse>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    let projection = get_unread_counts(state.database.pool(), current_user.id)
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    Ok(Json(unread_response(&projection)))
}

#[utoipa::path(
    get,
    path = "/api/content/stats/processing-count",
    operation_id = "getContentStatsProcessingCount",
    tag = "content",
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, description = "Processing counts", body = ProcessingCountResponse),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn processing_count(
    State(state): State<AppState>,
    headers: HeaderMap,
    current_user: AuthenticatedUser,
) -> Result<Json<ProcessingCountResponse>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    let projection = get_processing_counts(
        state.database.pool(),
        current_user.id,
        state.checkout_timeout,
    )
    .await
    .map_err(|error| internal_error(error, &request_id))?;
    Ok(Json(processing_response(&projection)))
}

#[utoipa::path(
    get,
    path = "/api/content/stats/badge",
    operation_id = "getContentBadgeStats",
    tag = "content",
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, description = "Combined app badge counts", body = BadgeStatsResponse),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn badge_stats(
    State(state): State<AppState>,
    headers: HeaderMap,
    current_user: AuthenticatedUser,
) -> Result<Json<BadgeStatsResponse>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    let (unread, processing) = tokio::try_join!(
        get_unread_counts(state.database.pool(), current_user.id),
        get_processing_counts(
            state.database.pool(),
            current_user.id,
            state.checkout_timeout
        )
    )
    .map_err(|error| internal_error(error, &request_id))?;
    Ok(Json(BadgeStatsResponse {
        unread: unread_response(&unread),
        processing: processing_response(&processing),
    }))
}

#[utoipa::path(
    get,
    path = "/api/content/stats/long-form",
    operation_id = "getContentLongFormStats",
    tag = "content",
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, description = "Unread long-form count", body = LongFormStatsResponse),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn long_form_stats(
    State(state): State<AppState>,
    headers: HeaderMap,
    current_user: AuthenticatedUser,
) -> Result<Json<LongFormStatsResponse>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    let unread_count = get_long_form_unread_count(state.database.pool(), current_user.id)
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    Ok(Json(LongFormStatsResponse { unread_count }))
}

fn unread_response(projection: &UnreadCountsProjection) -> UnreadCountsResponse {
    UnreadCountsResponse {
        article: projection.article,
        podcast: projection.podcast,
        news: projection.news,
    }
}

fn processing_response(projection: &ProcessingCountsProjection) -> ProcessingCountResponse {
    ProcessingCountResponse {
        processing_count: projection.processing_count,
        long_form_count: projection.long_form_count,
        news_count: projection.news_count,
        news_crawl_count: projection.news_crawl_count,
    }
}
