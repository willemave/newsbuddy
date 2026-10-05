use axum::extract::rejection::QueryRejection;
use axum::extract::{Query, RawQuery, State};
use axum::http::{HeaderMap, StatusCode};
use axum::routing::get;
use axum::{Json, Router};
use chrono::NaiveDate;
use newsly_contracts::{ContentListResponse, ContentType, PaginationMetadata, SavedSource};
use newsly_db::{
    ContentCardProjection, ContentFeedCursor, ContentFeedPage, ContentFeedReadFilter,
    list_content_feed, list_knowledge_content, list_recently_read_content, search_visible_content,
};
use percent_encoding::percent_decode_str;
use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::auth::AuthenticatedUser;
use crate::content_read::presentation;
use crate::error::ApiError;
use crate::pagination_cursor::{self, InvalidCursor};
use crate::write_support::{bad_request, internal_error};
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
    let response = present_page(page, query.limit, &filters, SavedSourcePolicy::Metadata)
        .map_err(|error| internal_error(error, &request_id))?;
    Ok(Json(response))
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
    let response = present_page(page, query.limit, &filters, SavedSourcePolicy::Metadata)
        .map_err(|error| internal_error(error, &request_id))?;
    Ok(Json(response))
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
    let response = present_page(page, query.limit, &filters, SavedSourcePolicy::Knowledge)
        .map_err(|error| internal_error(error, &request_id))?;
    Ok(Json(response))
}

#[derive(Debug, Deserialize)]
pub(super) struct SearchQuery {
    q: String,
    #[serde(default = "default_all")]
    r#type: String,
    #[serde(default = "default_limit")]
    limit: usize,
    cursor: Option<String>,
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
        ("cursor" = Option<String>, Query, description = "Opaque pagination cursor")
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
    let normalized_query = query.q.trim();
    if !(2..=200).contains(&normalized_query.chars().count()) {
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
    let filters = search_filters(normalized_query, &query.r#type);
    let cursor = decode_optional_cursor(query.cursor.as_deref(), &filters, &request_id)?;
    let page = search_visible_content(
        state.database.pool(),
        current_user.id,
        normalized_query,
        content_type,
        cursor.as_ref(),
        0,
        query.limit,
    )
    .await
    .map_err(|error| internal_error(error, &request_id))?;
    let response = present_page(page, query.limit, &filters, SavedSourcePolicy::Metadata)
        .map_err(|error| internal_error(error, &request_id))?;
    Ok(Json(response))
}

fn present_page(
    mut page: ContentFeedPage,
    limit: usize,
    filters: &CursorBinding,
    saved_source_policy: SavedSourcePolicy,
) -> Result<ContentListResponse, InvalidCursor> {
    let has_more = page.items.len() > limit;
    if has_more {
        page.items.truncate(limit);
    }
    let next_cursor = has_more
        .then(|| page.items.last())
        .flatten()
        .map(|last| encode_cursor(last, filters))
        .transpose()?;
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
    Ok(ContentListResponse {
        contents,
        available_dates: page.available_dates,
        content_types: public_content_types(),
        meta: PaginationMetadata {
            next_cursor,
            has_more,
            page_size,
            total: None,
        },
    })
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
    filters: &CursorBinding,
    request_id: &str,
) -> Result<Option<ContentFeedCursor>, ApiError> {
    cursor
        .map(|cursor| decode_cursor(cursor, filters))
        .transpose()
        .map_err(|error| bad_request(error.to_string(), request_id))
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct CursorPayload {
    last_id: i64,
    last_created_at: String,
    filters_hash: String,
    #[serde(default)]
    last_rank: Option<f64>,
}

#[derive(Debug)]
struct CursorBinding {
    filters_hash: String,
    legacy_filters_hash: String,
    rank_required: bool,
}

fn decode_cursor(
    cursor: &str,
    filters: &CursorBinding,
) -> Result<ContentFeedCursor, InvalidCursor> {
    let payload: CursorPayload = pagination_cursor::decode(cursor)?;
    let filters_match = payload.filters_hash == filters.filters_hash
        || payload.filters_hash == filters.legacy_filters_hash;
    if payload.last_id <= 0
        || !filters_match
        || payload.last_rank.is_some_and(|rank| !rank.is_finite())
        || (filters.rank_required && payload.last_rank.is_none())
    {
        return Err(InvalidCursor);
    }
    let last_timestamp = pagination_cursor::decode_timestamp(&payload.last_created_at)?;
    Ok(ContentFeedCursor {
        last_id: payload.last_id,
        last_timestamp,
        last_rank: payload.last_rank,
    })
}

fn encode_cursor(
    item: &ContentCardProjection,
    filters: &CursorBinding,
) -> Result<String, InvalidCursor> {
    if item.content.id <= 0
        || item.search_rank.is_some_and(|rank| !rank.is_finite())
        || (filters.rank_required && item.search_rank.is_none())
    {
        return Err(InvalidCursor);
    }
    pagination_cursor::encode(&CursorPayload {
        last_id: item.content.id,
        last_created_at: pagination_cursor::encode_timestamp(item.sort_timestamp.naive_utc()),
        filters_hash: filters.filters_hash.clone(),
        last_rank: item.search_rank,
    })
}

fn filter_hash<T: Serialize>(filters: &T) -> String {
    let bytes = serde_json::to_vec(filters).expect("cursor filters are serializable");
    newsly_domain::sha256_hex(&bytes)
}

fn normalized_content_types(content_types: &[String]) -> Vec<String> {
    let mut values = content_types.to_vec();
    values.sort();
    values.dedup();
    values
}

#[derive(Serialize)]
struct ListFilters<'a> {
    route: &'static str,
    content_type: Vec<String>,
    date: Option<&'a str>,
    read_filter: &'a str,
}

fn list_filters(content_types: &[String], date: Option<&str>, read_filter: &str) -> CursorBinding {
    CursorBinding {
        filters_hash: filter_hash(&ListFilters {
            route: "content_list",
            content_type: normalized_content_types(content_types),
            date,
            read_filter,
        }),
        legacy_filters_hash: legacy_filter_hash(&legacy_filter_object([
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
        ])),
        rank_required: false,
    }
}

#[derive(Serialize)]
struct RecentFilters<'a> {
    route: &'static str,
    content_type: Vec<String>,
    date: Option<&'a str>,
}

fn recent_filters(content_types: &[String], date: Option<&str>) -> CursorBinding {
    CursorBinding {
        filters_hash: filter_hash(&RecentFilters {
            route: "recently_read",
            content_type: normalized_content_types(content_types),
            date,
        }),
        legacy_filters_hash: legacy_filter_hash(&legacy_filter_object([
            (
                "content_type",
                (!content_types.is_empty()).then(|| {
                    let mut values = content_types.to_vec();
                    values.sort();
                    Value::Array(values.into_iter().map(Value::String).collect())
                }),
            ),
            ("date", date.map(|value| Value::String(value.to_owned()))),
        ])),
        rank_required: false,
    }
}

#[derive(Serialize)]
struct KnowledgeFilters<'a> {
    route: &'static str,
    q: Option<&'a str>,
}

fn knowledge_filters(query: Option<&str>) -> CursorBinding {
    CursorBinding {
        filters_hash: filter_hash(&KnowledgeFilters {
            route: "knowledge",
            q: query,
        }),
        legacy_filters_hash: legacy_filter_hash(&legacy_filter_object([(
            "q",
            query.map(|value| Value::String(value.to_owned())),
        )])),
        rank_required: query.is_some(),
    }
}

#[derive(Serialize)]
struct SearchFilters<'a> {
    route: &'static str,
    q: &'a str,
    content_type: &'a str,
}

fn search_filters(query: &str, content_type: &str) -> CursorBinding {
    CursorBinding {
        filters_hash: filter_hash(&SearchFilters {
            route: "search",
            q: query,
            content_type,
        }),
        legacy_filters_hash: legacy_filter_hash(&legacy_filter_object([
            ("q", Some(Value::String(query.to_owned()))),
            ("type", Some(Value::String(content_type.to_owned()))),
        ])),
        rank_required: true,
    }
}

fn legacy_filter_hash(filters: &Value) -> String {
    newsly_domain::sha256_hex(legacy_cursor_json(filters).as_bytes())
}

fn legacy_filter_object<const N: usize>(entries: [(&str, Option<Value>); N]) -> Value {
    Value::Object(
        entries
            .into_iter()
            .filter_map(|(key, value)| value.map(|value| (key.to_owned(), value)))
            .collect(),
    )
}

fn legacy_cursor_json(value: &Value) -> String {
    match value {
        Value::Object(object) => format!(
            "{{{}}}",
            object
                .iter()
                .map(|(key, value)| format!(
                    "{}: {}",
                    serde_json::to_string(key).expect("JSON key serialization is infallible"),
                    legacy_cursor_json(value)
                ))
                .collect::<Vec<_>>()
                .join(", ")
        ),
        Value::Array(values) => format!(
            "[{}]",
            values
                .iter()
                .map(legacy_cursor_json)
                .collect::<Vec<_>>()
                .join(", ")
        ),
        other => serde_json::to_string(other).expect("JSON value serialization is infallible"),
    }
}

fn validation_error(message: impl Into<String>, request_id: &str) -> ApiError {
    ApiError::validation(message, request_id)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn filter_hash_is_order_independent_and_route_bound() {
        let first = list_filters(&["podcast".to_owned(), "article".to_owned()], None, "all");
        let reordered = list_filters(
            &[
                "article".to_owned(),
                "podcast".to_owned(),
                "article".to_owned(),
            ],
            None,
            "all",
        );
        assert_eq!(first.filters_hash, reordered.filters_hash);
        let hashes = [
            first.filters_hash,
            recent_filters(&[], None).filters_hash,
            knowledge_filters(None).filters_hash,
            search_filters("query", "all").filters_hash,
        ];
        assert_eq!(
            hashes
                .iter()
                .collect::<std::collections::BTreeSet<_>>()
                .len(),
            4
        );
    }

    #[test]
    fn bound_cursor_rejects_missing_hash_rank_and_nonpositive_id() {
        let filters = search_filters("query", "all");
        let missing_hash = pagination_cursor::encode(&serde_json::json!({
            "last_id": 1,
            "last_created_at": "2026-01-02T03:04:05.000000Z",
            "last_rank": 0.3
        }))
        .expect("fixture encodes");
        assert!(decode_cursor(&missing_hash, &filters).is_err());

        let invalid = CursorPayload {
            last_id: 0,
            last_created_at: "2026-01-02T03:04:05.000000Z".to_owned(),
            filters_hash: filters.filters_hash.clone(),
            last_rank: None,
        };
        let invalid = pagination_cursor::encode(&invalid).expect("fixture encodes");
        assert!(decode_cursor(&invalid, &filters).is_err());
    }

    #[test]
    fn decodes_server_issued_legacy_content_list_cursor() {
        let cursor = "eyJmaWx0ZXJzX2hhc2giOiAiNmQ4MDc1NmVmNWJkMzgyZGQxMTc5NzQ5Njc5ZjA2NTg5YTEwNjExNTRhOGVkNTgwODA0MWVhYjNhOWU2YjY2MSIsICJsYXN0X2NyZWF0ZWRfYXQiOiAiMjAyNi0wOC0zMFQxMjozNDo1Ni42NTQzMjEiLCAibGFzdF9pZCI6IDc3fQ==";
        let filters = list_filters(&["podcast".to_owned(), "article".to_owned()], None, "all");
        assert_legacy_cursor(cursor, &filters, None);
        assert!(decode_cursor(cursor, &recent_filters(&[], None)).is_err());
    }

    #[test]
    fn decodes_server_issued_legacy_recently_read_cursor() {
        let cursor = "eyJmaWx0ZXJzX2hhc2giOiAiYTk1NjQzMGMzOTNiYjVlYzliM2E5ZjE4ZGYzYTM0MWE1MGQ0N2U0MDJjMzUwNGMwN2M3MDEwNTA5MmQ3NzdkNCIsICJsYXN0X2NyZWF0ZWRfYXQiOiAiMjAyNi0wOC0zMFQxMjozNDo1Ni42NTQzMjEiLCAibGFzdF9pZCI6IDc3fQ==";
        let filters = recent_filters(&["article".to_owned()], Some("2026-08-30"));
        assert_legacy_cursor(cursor, &filters, None);
        assert!(decode_cursor(cursor, &recent_filters(&["article".to_owned()], None)).is_err());
    }

    #[test]
    fn decodes_server_issued_legacy_knowledge_cursor() {
        let cursor = "eyJmaWx0ZXJzX2hhc2giOiAiYzNhYTRlZjcyMDRmYTg3OWM4NmE2ZDU0ZTBkNDUzMmU4ZTc0MGE3NGMyYzI2ZDRhODUxM2QzNmU4MmQ1MWYxNiIsICJsYXN0X2NyZWF0ZWRfYXQiOiAiMjAyNi0wOC0zMFQxMjozNDo1Ni42NTQzMjEiLCAibGFzdF9pZCI6IDc3LCAibGFzdF9yYW5rIjogMC4zfQ==";
        let filters = knowledge_filters(Some("machine learning"));
        assert_legacy_cursor(cursor, &filters, Some(0.3));
        assert!(decode_cursor(cursor, &knowledge_filters(Some("other query"))).is_err());
    }

    #[test]
    fn decodes_server_issued_legacy_search_cursor() {
        let cursor = "eyJmaWx0ZXJzX2hhc2giOiAiMDg2ZGYyNzZjZGU3MTE5M2U5ZmQ1ZmQzODdlZGI5NTdmZGY4ODQzMzljZWQ4ZDU5MjEzOWQ0YTNkYzcwZmM1NiIsICJsYXN0X2NyZWF0ZWRfYXQiOiAiMjAyNi0wOC0zMFQxMjozNDo1Ni42NTQzMjEiLCAibGFzdF9pZCI6IDc3LCAibGFzdF9yYW5rIjogMC4zfQ==";
        let filters = search_filters("machine learning", "article");
        assert_legacy_cursor(cursor, &filters, Some(0.3));
        assert!(decode_cursor(cursor, &search_filters("machine learning", "all")).is_err());
    }

    fn assert_legacy_cursor(cursor: &str, filters: &CursorBinding, rank: Option<f64>) {
        let decoded = decode_cursor(cursor, filters).expect("legacy cursor decodes");
        assert_eq!(decoded.last_id, 77);
        assert_eq!(
            decoded.last_timestamp,
            chrono::NaiveDateTime::parse_from_str(
                "2026-08-30T12:34:56.654321",
                "%Y-%m-%dT%H:%M:%S%.6f"
            )
            .expect("valid timestamp")
        );
        assert_eq!(decoded.last_rank, rank);
    }
}
