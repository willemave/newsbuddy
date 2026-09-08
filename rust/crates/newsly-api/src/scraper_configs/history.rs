use axum::Json;
use axum::extract::rejection::QueryRejection;
use axum::extract::{Path, Query, State};
use axum::http::HeaderMap;
use newsly_contracts::{FeedHistoryItem, FeedHistoryResponse};
use newsly_db::list_scraper_configs as load_scraper_configs;
use serde::Deserialize;
use serde_json::Value;

use super::{decode_query, not_found_error};
use crate::auth::AuthenticatedUser;
use crate::error::ApiError;
use crate::write_support::{bad_request, internal_error};
use crate::{AppState, request_id_from_headers};

#[derive(Debug, Deserialize)]
pub(super) struct FeedHistoryQuery {
    #[serde(default)]
    offset: i64,
    status: Option<String>,
}

#[utoipa::path(
    get,
    path = "/api/scrapers/{config_id}/history",
    operation_id = "getFeedHistory",
    tag = "scrapers",
    params(
        ("config_id" = i64, Path, description = "Owned feed configuration"),
        ("offset" = Option<i64>, Query, description = "History offset, pages contain 30 items"),
        ("status" = Option<String>, Query, description = "completed (default), active, failed, or all")
    ),
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, description = "Feed processing history", body = FeedHistoryResponse),
        (status = 400, description = "Invalid history query", body = newsly_contracts::ErrorEnvelope),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 404, description = "Feed not found", body = newsly_contracts::ErrorEnvelope),
        (status = 422, description = "Validation error", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn feed_history(
    State(state): State<AppState>,
    headers: HeaderMap,
    user: AuthenticatedUser,
    Path(config_id): Path<i64>,
    query: Result<Query<FeedHistoryQuery>, QueryRejection>,
) -> Result<Json<FeedHistoryResponse>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    let Query(query) = decode_query(query, &request_id)?;
    let filter = query.status.as_deref().unwrap_or("completed");
    if query.offset < 0
        || query.offset > i64::MAX - 31
        || !matches!(filter, "completed" | "active" | "failed" | "all")
    {
        return Err(bad_request("Invalid feed history query", &request_id));
    }
    let types = ["substack", "atom", "youtube", "podcast_rss"].map(str::to_owned);
    let configs = load_scraper_configs(state.database.pool(), user.id, Some(&types))
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    if !configs.iter().any(|config| config.id == config_id) {
        return Err(not_found_error("Feed not found", &request_id));
    }
    let mut rows = newsly_db::load_feed_history(
        state.database.pool(),
        user.id,
        &configs,
        config_id,
        filter,
        query.offset,
        31,
    )
    .await
    .map_err(|error| internal_error(error, &request_id))?;
    let next_offset = (rows.len() > 30).then_some(query.offset + 30);
    rows.truncate(30);
    Ok(Json(FeedHistoryResponse {
        next_offset,
        items: rows
            .into_iter()
            .map(|row| FeedHistoryItem {
                id: row.id,
                title: row.title,
                content_type: row.content_type,
                status: row.status,
                stage: row.stage,
                processed_at: row.processed_at,
                publication_at: row.publication_at,
                duration_seconds: positive_metadata_number(&row.metadata, "duration_seconds"),
                reading_minutes: row
                    .source_char_count
                    .filter(|count| *count > 0)
                    .map(|count| (i64::from(count) + 1199) / 1200),
            })
            .collect(),
    }))
}

fn positive_metadata_number(metadata: &Value, key: &str) -> Option<i64> {
    metadata
        .get(key)
        .and_then(|value| value.as_i64().or_else(|| value.as_str()?.parse().ok()))
        .filter(|value| *value > 0)
}
