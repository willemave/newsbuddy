use std::collections::BTreeMap;

use axum::extract::rejection::QueryRejection;
use axum::extract::{Query, RawQuery, State};
use axum::http::{HeaderMap, StatusCode};
use axum::routing::get;
use axum::{Json, Router};
use base64::Engine as _;
use base64::engine::general_purpose::{URL_SAFE, URL_SAFE_NO_PAD};
use chrono::{NaiveDate, NaiveDateTime};
use newsly_contracts::{ContentListResponse, ContentType, PaginationMetadata, SavedSource};
use newsly_db::{
    ContentCardProjection, ContentFeedCursor, ContentFeedPage, ContentFeedReadFilter,
    list_content_feed, list_knowledge_content, list_recently_read_content, search_visible_content,
};
use percent_encoding::percent_decode_str;
use serde::Deserialize;
use serde_json::Value;
use sha2::{Digest, Sha256};

use crate::auth::AuthenticatedUser;
use crate::content_read::presentation;
use crate::encoding::hex_encode;
use crate::error::ApiError;
use crate::write_support::internal_error;
use crate::{AppState, request_id_from_headers};

pub(super) fn router() -> Router<AppState> {
    Router::new()
        .route("/api/content/", get(list_contents))
        .route("/api/content/search", get(search_contents))
        .route("/api/content/knowledge/list", get(get_knowledge_library))
        .route("/api/content/recently-read/list", get(get_recently_read))
}

#[derive(Debug, Deserialize)]
pub(super) struct ListQuery {
    date: Option<String>,
    #[serde(default = "default_all")]
    read_filter: String,
    cursor: Option<String>,
    #[serde(default = "default_limit")]
    limit: usize,
    #[serde(default = "default_true")]
    include_available_dates: bool,
}

fn default_all() -> String {
    "all".to_owned()
}

const fn default_limit() -> usize {
    25
}

const fn default_true() -> bool {
    true
}

#[utoipa::path(
    get,
    path = "/api/content/",
    operation_id = "listContents",
    tag = "content",
    params(
        ("content_type" = Option<Vec<String>>, Query, description = "Content type filters"),
        ("date" = Option<String>, Query, description = "Filter date in YYYY-MM-DD format"),
        ("read_filter" = Option<String>, Query, description = "all, read, or unread"),
        ("cursor" = Option<String>, Query, description = "Opaque pagination cursor"),
        ("limit" = Option<usize>, Query, minimum = 1, maximum = 100),
        ("include_available_dates" = Option<bool>, Query)
    ),
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, description = "Successful Response", body = ContentListResponse),
        (status = 400, description = "Invalid date or cursor", body = newsly_contracts::ErrorEnvelope),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 422, description = "Validation Error", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn list_contents(
    State(state): State<AppState>,
    headers: HeaderMap,
    RawQuery(raw_query): RawQuery,
    query: Result<Query<ListQuery>, QueryRejection>,
    current_user: AuthenticatedUser,
) -> Result<Json<ContentListResponse>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    let Query(query) = parse_query(query, &request_id)?;
    validate_limit(query.limit, &request_id)?;
    let read_filter = parse_read_filter(&query.read_filter, &request_id)?;
    let date = query
        .date
        .as_deref()
        .map(|value| NaiveDate::parse_from_str(value, "%Y-%m-%d"))
        .transpose()
        .map_err(|_| bad_request("Invalid date format", &request_id))?;
    let content_types = repeated_query_values(raw_query.as_deref(), "content_type", &request_id)?;
    let filters = list_filters(&content_types, query.date.as_deref(), &query.read_filter);
    let cursor = decode_optional_cursor(query.cursor.as_deref(), &filters, &request_id)?;
    let page = list_content_feed(
        state.database.pool(),
        current_user.id,
        &content_types,
        date,
        read_filter,
        cursor.as_ref(),
        query.limit,
        query.include_available_dates,
    )
    .await
    .map_err(|error| internal_error(error, &request_id))?;
    Ok(Json(present_page(
        page,
        query.limit,
        &filters,
        SavedSourcePolicy::Metadata,
    )))
}

#[derive(Debug, Deserialize)]
pub(super) struct RecentQuery {
    date: Option<String>,
    cursor: Option<String>,
    #[serde(default = "default_limit")]
    limit: usize,
}

#[utoipa::path(
    get,
    path = "/api/content/recently-read/list",
    operation_id = "getContentListRecentlyRead",
    tag = "content",
    params(
        ("content_type" = Option<Vec<String>>, Query, description = "Content type filters"),
        ("date" = Option<String>, Query, description = "Read date in YYYY-MM-DD format"),
        ("cursor" = Option<String>, Query, description = "Opaque pagination cursor"),
        ("limit" = Option<usize>, Query, minimum = 1, maximum = 100)
    ),
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, description = "Successful Response", body = ContentListResponse),
        (status = 400, description = "Invalid cursor", body = newsly_contracts::ErrorEnvelope),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 422, description = "Validation Error", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn get_recently_read(
    State(state): State<AppState>,
    headers: HeaderMap,
    RawQuery(raw_query): RawQuery,
    query: Result<Query<RecentQuery>, QueryRejection>,
    current_user: AuthenticatedUser,
) -> Result<Json<ContentListResponse>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    let Query(query) = parse_query(query, &request_id)?;
    validate_limit(query.limit, &request_id)?;
    let date = query
        .date
        .as_deref()
        .map(|value| NaiveDate::parse_from_str(value, "%Y-%m-%d"))
        .transpose()
        .map_err(|_| validation_error("date must be a valid YYYY-MM-DD date", &request_id))?;
    let content_types = repeated_query_values(raw_query.as_deref(), "content_type", &request_id)?;
    let filters = recent_filters(&content_types, query.date.as_deref());
    let cursor = decode_optional_cursor(query.cursor.as_deref(), &filters, &request_id)?;
    let page = list_recently_read_content(
        state.database.pool(),
        current_user.id,
        &content_types,
        date,
        cursor.as_ref(),
        query.limit,
    )
    .await
    .map_err(|error| internal_error(error, &request_id))?;
    Ok(Json(present_page(
        page,
        query.limit,
        &filters,
        SavedSourcePolicy::Metadata,
    )))
}

#[derive(Debug, Deserialize)]
pub(super) struct KnowledgeQuery {
    cursor: Option<String>,
    q: Option<String>,
    #[serde(default = "default_limit")]
    limit: usize,
}

#[utoipa::path(
    get,
    path = "/api/content/knowledge/list",
    operation_id = "getContentListKnowledgeLibrary",
    tag = "content",
    params(
        ("cursor" = Option<String>, Query, description = "Opaque pagination cursor"),
        ("q" = Option<String>, Query, description = "Optional Knowledge search query", min_length = 2, max_length = 200),
        ("limit" = Option<usize>, Query, minimum = 1, maximum = 100)
    ),
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, description = "Successful Response", body = ContentListResponse),
        (status = 400, description = "Invalid cursor", body = newsly_contracts::ErrorEnvelope),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 422, description = "Validation Error", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn get_knowledge_library(
    State(state): State<AppState>,
    headers: HeaderMap,
    query: Result<Query<KnowledgeQuery>, QueryRejection>,
    current_user: AuthenticatedUser,
) -> Result<Json<ContentListResponse>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    let Query(query) = parse_query(query, &request_id)?;
    validate_limit(query.limit, &request_id)?;
    if let Some(raw) = &query.q
        && !(2..=200).contains(&raw.chars().count())
    {
        return Err(validation_error(
            "q must contain between 2 and 200 characters",
            &request_id,
        ));
    }
    let normalized_query = query
        .q
        .as_deref()
        .map(str::trim)
        .filter(|value| !value.is_empty());
    let filters = knowledge_filters(normalized_query);
    let cursor = decode_optional_cursor(query.cursor.as_deref(), &filters, &request_id)?;
    let page = list_knowledge_content(
        state.database.pool(),
        current_user.id,
        normalized_query,
        cursor.as_ref(),
        query.limit,
    )
    .await
    .map_err(|error| internal_error(error, &request_id))?;
    Ok(Json(present_page(
        page,
        query.limit,
        &filters,
        SavedSourcePolicy::Knowledge,
    )))
}

#[derive(Debug, Deserialize)]
pub(super) struct SearchQuery {
    q: String,
    #[serde(default = "default_all")]
    r#type: String,
    #[serde(default = "default_limit")]
    limit: usize,
    cursor: Option<String>,
    #[serde(default)]
    offset: usize,
}

#[utoipa::path(
    get,
    path = "/api/content/search",
    operation_id = "searchContents",
    tag = "content",
    params(
        ("q" = String, Query, min_length = 2, max_length = 200),
        ("type" = Option<String>, Query, description = "all, article, podcast, or news"),
        ("limit" = Option<usize>, Query, minimum = 1, maximum = 100),
        ("cursor" = Option<String>, Query, description = "Opaque pagination cursor"),
        ("offset" = Option<usize>, Query, deprecated, description = "Deprecated offset")
    ),
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, description = "Successful Response", body = ContentListResponse),
        (status = 400, description = "Invalid cursor", body = newsly_contracts::ErrorEnvelope),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 422, description = "Validation Error", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn search_contents(
    State(state): State<AppState>,
    headers: HeaderMap,
    query: Result<Query<SearchQuery>, QueryRejection>,
    current_user: AuthenticatedUser,
) -> Result<Json<ContentListResponse>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    let Query(query) = parse_query(query, &request_id)?;
    validate_limit(query.limit, &request_id)?;
    if !(2..=200).contains(&query.q.chars().count()) {
        return Err(validation_error(
            "q must contain between 2 and 200 characters",
            &request_id,
        ));
    }
    let content_type = match query.r#type.as_str() {
        "all" => None,
        value @ ("article" | "podcast" | "news") => Some(value),
        _ => {
            return Err(validation_error(
                "type must be one of all, article, podcast, or news",
                &request_id,
            ));
        }
    };
    let filters = search_filters(&query.q, &query.r#type);
    let cursor = decode_optional_cursor(query.cursor.as_deref(), &filters, &request_id)?;
    let page = search_visible_content(
        state.database.pool(),
        current_user.id,
        &query.q,
        content_type,
        cursor.as_ref(),
        query.offset,
        query.limit,
    )
    .await
    .map_err(|error| internal_error(error, &request_id))?;
    Ok(Json(present_page(
        page,
        query.limit,
        &filters,
        SavedSourcePolicy::Metadata,
    )))
}

fn present_page(
    mut page: ContentFeedPage,
    limit: usize,
    filters: &Value,
    saved_source_policy: SavedSourcePolicy,
) -> ContentListResponse {
    let has_more = page.items.len() > limit;
    if has_more {
        page.items.truncate(limit);
    }
    let next_cursor = has_more
        .then(|| page.items.last())
        .flatten()
        .map(|last| encode_cursor(last, filters));
    let contents = page
        .items
        .into_iter()
        .filter_map(|item| {
            let override_source = match saved_source_policy {
                SavedSourcePolicy::Knowledge | SavedSourcePolicy::Metadata
                    if item.saved_from_x_bookmark =>
                {
                    Some(SavedSource::XBookmark)
                }
                SavedSourcePolicy::Knowledge => Some(SavedSource::Knowledge),
                SavedSourcePolicy::Metadata => None,
            };
            match presentation::present_content_summary(
                item.content,
                item.knowledge_saved_at,
                override_source,
            ) {
                Ok(content) => Some(content),
                Err(error) => {
                    tracing::warn!(error = %error, "skipping invalid content card projection");
                    None
                }
            }
        })
        .collect::<Vec<_>>();
    let page_size = contents.len();
    ContentListResponse {
        contents,
        available_dates: page.available_dates,
        content_types: public_content_types(),
        meta: PaginationMetadata {
            next_cursor,
            has_more,
            page_size,
            total: None,
        },
    }
}

#[derive(Debug, Clone, Copy)]
enum SavedSourcePolicy {
    Metadata,
    Knowledge,
}

fn public_content_types() -> Vec<ContentType> {
    vec![
        ContentType::Article,
        ContentType::Podcast,
        ContentType::News,
        ContentType::InsightReport,
        ContentType::Unknown,
    ]
}

fn parse_query<T>(
    query: Result<Query<T>, QueryRejection>,
    request_id: &str,
) -> Result<Query<T>, ApiError> {
    query.map_err(|rejection| {
        validation_error(
            format!("Request validation failed: {}", rejection.body_text()),
            request_id,
        )
    })
}

fn validate_limit(limit: usize, request_id: &str) -> Result<(), ApiError> {
    if (1..=100).contains(&limit) {
        Ok(())
    } else {
        Err(validation_error(
            "limit must be between 1 and 100",
            request_id,
        ))
    }
}

fn parse_read_filter(value: &str, request_id: &str) -> Result<ContentFeedReadFilter, ApiError> {
    match value {
        "all" => Ok(ContentFeedReadFilter::All),
        "read" => Ok(ContentFeedReadFilter::Read),
        "unread" => Ok(ContentFeedReadFilter::Unread),
        _ => Err(validation_error(
            "read_filter must be one of all, read, or unread",
            request_id,
        )),
    }
}

fn repeated_query_values(
    raw_query: Option<&str>,
    name: &str,
    request_id: &str,
) -> Result<Vec<String>, ApiError> {
    let mut values = Vec::new();
    for pair in raw_query.unwrap_or_default().split('&') {
        let (raw_key, raw_value) = pair.split_once('=').unwrap_or((pair, ""));
        let key = decode_query_component(raw_key, request_id)?;
        if key == name {
            let value = decode_query_component(raw_value, request_id)?;
            if !value.is_empty() && value != "all" {
                values.push(value);
            }
        }
    }
    Ok(values)
}

fn decode_query_component(value: &str, request_id: &str) -> Result<String, ApiError> {
    let replaced = value.replace('+', " ");
    percent_decode_str(&replaced)
        .decode_utf8()
        .map(std::borrow::Cow::into_owned)
        .map_err(|_| {
            ApiError::new(
                StatusCode::UNPROCESSABLE_ENTITY,
                "validation_error",
                "Request validation failed",
                request_id.to_owned(),
            )
        })
}

fn decode_optional_cursor(
    cursor: Option<&str>,
    filters: &Value,
    request_id: &str,
) -> Result<Option<ContentFeedCursor>, ApiError> {
    cursor
        .map(|cursor| decode_cursor(cursor, filters))
        .transpose()
        .map_err(|message| bad_request(message, request_id))
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct CursorPayload {
    last_id: i64,
    last_created_at: String,
    #[serde(default)]
    filters_hash: Option<String>,
    #[serde(default)]
    last_rank: Option<f64>,
}

fn decode_cursor(cursor: &str, filters: &Value) -> Result<ContentFeedCursor, &'static str> {
    let decoded = URL_SAFE
        .decode(cursor)
        .or_else(|_| URL_SAFE_NO_PAD.decode(cursor))
        .map_err(|_| "Invalid pagination cursor")?;
    let payload: CursorPayload =
        serde_json::from_slice(&decoded).map_err(|_| "Invalid pagination cursor")?;
    if payload.last_id <= 0 {
        return Err("Invalid pagination cursor");
    }
    if payload
        .filters_hash
        .as_deref()
        .is_some_and(|value| !value.is_empty() && value != filters_hash(filters))
    {
        return Err("Invalid pagination cursor for filters");
    }
    let last_timestamp =
        parse_cursor_datetime(&payload.last_created_at).ok_or("Invalid pagination cursor")?;
    Ok(ContentFeedCursor {
        last_id: payload.last_id,
        last_timestamp,
        last_rank: payload.last_rank,
    })
}

fn encode_cursor(item: &ContentCardProjection, filters: &Value) -> String {
    let mut payload = BTreeMap::new();
    payload.insert("filters_hash", Value::String(filters_hash(filters)));
    payload.insert(
        "last_created_at",
        Value::String(
            item.sort_timestamp
                .naive_utc()
                .format("%Y-%m-%dT%H:%M:%S%.f")
                .to_string(),
        ),
    );
    payload.insert("last_id", Value::from(item.content.id));
    if let Some(rank) = item.search_rank.and_then(serde_json::Number::from_f64) {
        payload.insert("last_rank", Value::Number(rank));
    }
    let payload = payload
        .into_iter()
        .map(|(key, value)| (key.to_owned(), value))
        .collect();
    URL_SAFE.encode(cursor_compatibility_json(&Value::Object(payload)).as_bytes())
}

fn parse_cursor_datetime(value: &str) -> Option<NaiveDateTime> {
    chrono::DateTime::parse_from_rfc3339(value)
        .map(|value| value.naive_utc())
        .ok()
        .or_else(|| {
            [
                "%Y-%m-%dT%H:%M:%S%.f",
                "%Y-%m-%d %H:%M:%S%.f",
                "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%d %H:%M:%S",
            ]
            .into_iter()
            .find_map(|format| NaiveDateTime::parse_from_str(value, format).ok())
        })
}

fn filters_hash(filters: &Value) -> String {
    hex_encode(&Sha256::digest(
        cursor_compatibility_json(filters).as_bytes(),
    ))
}

fn list_filters(content_types: &[String], date: Option<&str>, read_filter: &str) -> Value {
    filter_object([
        (
            "content_type",
            (!content_types.is_empty()).then(|| {
                let mut values = content_types.to_vec();
                values.sort();
                Value::Array(values.into_iter().map(Value::String).collect())
            }),
        ),
        ("date", date.map(|value| Value::String(value.to_owned()))),
        ("read_filter", Some(Value::String(read_filter.to_owned()))),
    ])
}

fn recent_filters(content_types: &[String], date: Option<&str>) -> Value {
    filter_object([
        (
            "content_type",
            (!content_types.is_empty()).then(|| {
                let mut values = content_types.to_vec();
                values.sort();
                Value::Array(values.into_iter().map(Value::String).collect())
            }),
        ),
        ("date", date.map(|value| Value::String(value.to_owned()))),
    ])
}

fn knowledge_filters(query: Option<&str>) -> Value {
    filter_object([("q", query.map(|value| Value::String(value.to_owned())))])
}

fn search_filters(query: &str, content_type: &str) -> Value {
    filter_object([
        ("q", Some(Value::String(query.to_owned()))),
        ("type", Some(Value::String(content_type.to_owned()))),
    ])
}

fn filter_object<const N: usize>(entries: [(&str, Option<Value>); N]) -> Value {
    Value::Object(
        entries
            .into_iter()
            .filter_map(|(key, value)| value.map(|value| (key.to_owned(), value)))
            .collect(),
    )
}

fn cursor_compatibility_json(value: &Value) -> String {
    match value {
        Value::Object(object) => format!(
            "{{{}}}",
            object
                .iter()
                .map(|(key, value)| format!(
                    "{}: {}",
                    serde_json::to_string(key).expect("JSON key serialization is infallible"),
                    cursor_compatibility_json(value)
                ))
                .collect::<Vec<_>>()
                .join(", ")
        ),
        Value::Array(values) => format!(
            "[{}]",
            values
                .iter()
                .map(cursor_compatibility_json)
                .collect::<Vec<_>>()
                .join(", ")
        ),
        other => serde_json::to_string(other).expect("JSON value serialization is infallible"),
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

fn bad_request(message: impl Into<String>, request_id: &str) -> ApiError {
    ApiError::new(
        StatusCode::BAD_REQUEST,
        "bad_request",
        message,
        request_id.to_owned(),
    )
}

#[cfg(test)]
mod tests {
    use super::{cursor_compatibility_json, filters_hash, list_filters};

    #[test]
    fn filter_hash_preserves_the_installed_cursor_format() {
        let filters = list_filters(&["podcast".to_owned(), "article".to_owned()], None, "all");
        assert_eq!(
            cursor_compatibility_json(&filters),
            r#"{"content_type": ["article", "podcast"], "read_filter": "all"}"#
        );
        assert_eq!(filters_hash(&filters).len(), 64);
    }
}
