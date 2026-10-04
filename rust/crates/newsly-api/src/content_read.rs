pub(super) mod presentation;

use std::collections::BTreeSet;

use axum::extract::rejection::{PathRejection, QueryRejection};
use axum::extract::{Path, Query, State};
use axum::http::{HeaderMap, StatusCode};
use axum::routing::get;
use axum::{Json, Router};
use chrono::NaiveDateTime;
use newsly_contracts::{
    ContentDetailResponse, ContentType, NewsItemDetailResponse, NewsItemListResponse,
    PaginationMetadata,
};
use newsly_db::{
    NewsListCursor, NewsReadFilter, find_visible_content_detail, find_visible_news_item_detail,
    list_active_feed_urls, list_visible_news_items,
};
use serde::{Deserialize, Serialize};

use crate::auth::AuthenticatedUser;
use crate::error::ApiError;
use crate::pagination_cursor::{self, InvalidCursor};
use crate::write_support::{bad_request, internal_error};
use crate::{AppState, request_id_from_headers};

pub(super) fn router() -> Router<AppState> {
    Router::new()
        .route("/api/content/{content_id}", get(get_content_detail))
        .route("/api/news/items/{news_item_id}", get(get_news_item))
        .route("/api/news/items", get(list_news_items))
}

#[utoipa::path(
    get,
    path = "/api/content/{content_id}",
    operation_id = "getContentDetail",
    tag = "content",
    params(("content_id" = i64, Path, description = "Content ID")),
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, description = "Successful Response", body = ContentDetailResponse),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 404, description = "Content not found", body = newsly_contracts::ErrorEnvelope),
        (status = 422, description = "Validation Error", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn get_content_detail(
    State(state): State<AppState>,
    headers: HeaderMap,
    path: Result<Path<i64>, PathRejection>,
    current_user: AuthenticatedUser,
) -> Result<Json<ContentDetailResponse>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    let content_id = positive_path_id(path, "content_id", &request_id)?;
    let row = find_visible_content_detail(state.database.pool(), current_user.id, content_id)
        .await
        .map_err(|error| internal_error(error, &request_id))?
        .ok_or_else(|| not_found("Content not found", &request_id))?;
    let mut response = presentation::present_content_detail(row)
        .map_err(|error| internal_error(error, &request_id))?;
    let candidate = presentation::subscription_candidate(&response)
        .map(|(feed_type, feed_url)| (feed_type.to_owned(), feed_url.to_owned()));
    if let Some((feed_type, feed_url)) = candidate {
        let active_urls = list_active_feed_urls(state.database.pool(), current_user.id, &feed_type)
            .await
            .map_err(|error| internal_error(error, &request_id))?;
        let requested = newsly_db::canonicalize_feed_url(&feed_url);
        response.can_subscribe = !active_urls
            .iter()
            .any(|url| newsly_db::canonicalize_feed_url(url) == requested);
    }
    Ok(Json(response))
}

#[utoipa::path(
    get,
    path = "/api/news/items/{news_item_id}",
    operation_id = "getNewsItem",
    tag = "news",
    params(("news_item_id" = i64, Path, description = "News item ID")),
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, description = "Successful Response", body = NewsItemDetailResponse),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 404, description = "Not found", body = newsly_contracts::ErrorEnvelope),
        (status = 422, description = "Validation Error", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn get_news_item(
    State(state): State<AppState>,
    headers: HeaderMap,
    path: Result<Path<i64>, PathRejection>,
    current_user: AuthenticatedUser,
) -> Result<Json<NewsItemDetailResponse>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    let news_item_id = positive_path_id(path, "news_item_id", &request_id)?;
    let item = find_visible_news_item_detail(state.database.pool(), current_user.id, news_item_id)
        .await
        .map_err(|error| internal_error(error, &request_id))?
        .ok_or_else(|| not_found("News item not found", &request_id))?;
    Ok(Json(presentation::present_news_detail(item)))
}

#[derive(Debug, Deserialize)]
pub(super) struct NewsListQuery {
    #[serde(default = "default_read_filter")]
    read_filter: String,
    cursor: Option<String>,
    #[serde(default = "default_limit")]
    limit: usize,
}

fn default_read_filter() -> String {
    "unread".to_owned()
}

const fn default_limit() -> usize {
    25
}

#[utoipa::path(
    get,
    path = "/api/news/items",
    operation_id = "listNewsItems",
    tag = "news",
    params(
        ("read_filter" = Option<String>, Query, description = "Filter by read status"),
        ("cursor" = Option<String>, Query, description = "Opaque cursor token"),
        ("limit" = Option<usize>, Query, description = "Maximum items to return", minimum = 1, maximum = 100)
    ),
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, description = "Successful Response", body = NewsItemListResponse),
        (status = 400, description = "Invalid cursor", body = newsly_contracts::ErrorEnvelope),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 404, description = "Not found", body = newsly_contracts::ErrorEnvelope),
        (status = 422, description = "Validation Error", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn list_news_items(
    State(state): State<AppState>,
    headers: HeaderMap,
    query: Result<Query<NewsListQuery>, QueryRejection>,
    current_user: AuthenticatedUser,
) -> Result<Json<NewsItemListResponse>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    let Query(query) = query.map_err(|rejection| {
        validation_error(
            format!("Request validation failed: {}", rejection.body_text()),
            &request_id,
        )
    })?;
    if !(1..=100).contains(&query.limit) {
        return Err(validation_error(
            "limit must be between 1 and 100",
            &request_id,
        ));
    }
    let read_filter = match query.read_filter.as_str() {
        "all" => NewsReadFilter::All,
        "read" => NewsReadFilter::Read,
        "unread" => NewsReadFilter::Unread,
        _ => {
            return Err(validation_error(
                "read_filter must be one of all, read, or unread",
                &request_id,
            ));
        }
    };
    let cursor = query
        .cursor
        .as_deref()
        .map(|cursor| decode_cursor(cursor, &query.read_filter))
        .transpose()
        .map_err(|error| bad_request(error.to_string(), &request_id))?;
    let mut page = list_visible_news_items(
        state.database.pool(),
        current_user.id,
        read_filter,
        cursor.as_ref(),
        query.limit,
    )
    .await
    .map_err(|error| internal_error(error, &request_id))?;
    let has_more = page.items.len() > query.limit;
    if has_more {
        page.items.truncate(query.limit);
    }
    let next_cursor = if has_more {
        page.items
            .last()
            .map(|item| encode_cursor(item.id, item.sort_timestamp.naive_utc(), &query.read_filter))
            .transpose()
            .map_err(|error| internal_error(error, &request_id))?
    } else {
        None
    };
    let available_dates = page
        .items
        .iter()
        .map(|item| item.sort_timestamp.date_naive().to_string())
        .collect::<BTreeSet<_>>()
        .into_iter()
        .rev()
        .collect();
    let contents = page
        .items
        .iter()
        .map(presentation::present_news_summary)
        .collect::<Vec<_>>();
    Ok(Json(NewsItemListResponse {
        meta: PaginationMetadata {
            next_cursor,
            has_more,
            page_size: contents.len(),
            total: Some(page.total),
        },
        contents,
        available_dates,
        content_types: vec![ContentType::News],
    }))
}

fn positive_path_id(
    path: Result<Path<i64>, PathRejection>,
    field: &str,
    request_id: &str,
) -> Result<i64, ApiError> {
    let Path(id) = path.map_err(|rejection| validation_error(rejection.body_text(), request_id))?;
    if id <= 0 {
        return Err(validation_error(
            format!("{field} must be greater than zero"),
            request_id,
        ));
    }
    Ok(id)
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct CursorPayload {
    last_id: i64,
    last_created_at: String,
    filters_hash: String,
}

fn decode_cursor(cursor: &str, read_filter: &str) -> Result<NewsListCursor, InvalidCursor> {
    let payload: CursorPayload = pagination_cursor::decode(cursor)?;
    if payload.last_id <= 0 || payload.filters_hash != filters_hash(read_filter) {
        return Err(InvalidCursor);
    }
    let last_sort_timestamp = pagination_cursor::decode_timestamp(&payload.last_created_at)?;
    Ok(NewsListCursor {
        last_id: payload.last_id,
        last_sort_timestamp,
    })
}

#[derive(Debug, Serialize)]
struct CursorFilter<'a> {
    route: &'static str,
    read_filter: &'a str,
}

fn filters_hash(read_filter: &str) -> String {
    let bytes = serde_json::to_vec(&CursorFilter {
        route: "news_items",
        read_filter,
    })
    .expect("cursor filter serialization is infallible");
    newsly_domain::sha256_hex(&bytes)
}

fn encode_cursor(
    last_id: i64,
    last_created_at: NaiveDateTime,
    read_filter: &str,
) -> Result<String, InvalidCursor> {
    if last_id <= 0 {
        return Err(InvalidCursor);
    }
    pagination_cursor::encode(&CursorPayload {
        last_id,
        last_created_at: pagination_cursor::encode_timestamp(last_created_at),
        filters_hash: filters_hash(read_filter),
    })
}

fn validation_error(message: impl Into<String>, request_id: &str) -> ApiError {
    ApiError::validation(message, request_id)
}

fn not_found(message: &str, request_id: &str) -> ApiError {
    ApiError::new(
        StatusCode::NOT_FOUND,
        "not_found",
        message,
        request_id.to_owned(),
    )
}

#[cfg(test)]
mod cursor_tests {
    use super::*;

    #[test]
    fn news_cursor_round_trips_and_is_bound_to_read_filter() {
        let timestamp =
            NaiveDateTime::parse_from_str("2026-08-30T12:34:56.999999", "%Y-%m-%dT%H:%M:%S%.6f")
                .expect("valid timestamp");
        let cursor = encode_cursor(42, timestamp, "unread").expect("cursor encodes");
        let decoded = decode_cursor(&cursor, "unread").expect("cursor decodes");
        assert_eq!(decoded.last_id, 42);
        assert_eq!(decoded.last_sort_timestamp, timestamp);
        assert!(decode_cursor(&cursor, "read").is_err());
    }
}
