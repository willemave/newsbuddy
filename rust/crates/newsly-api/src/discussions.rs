use std::collections::{BTreeMap, BTreeSet};

use axum::extract::rejection::PathRejection;
use axum::extract::{Path, State};
use axum::http::{HeaderMap, StatusCode};
use axum::routing::get;
use axum::{Json, Router};
use chrono::{DateTime, NaiveDateTime, Utc};
use newsly_contracts::{
    ContentDiscussionResponse, DiscussionCommentResponse, DiscussionGroupResponse,
    DiscussionItemResponse, DiscussionLinkResponse, DiscussionMode, DiscussionSummaryResponse,
};
use newsly_db::{
    ContentDiscussionProjection, NewsDiscussionProjection, find_visible_content_discussion,
    find_visible_news_discussion,
};
use serde_json::{Map, Value};

use crate::auth::AuthenticatedUser;
use crate::error::ApiError;
use crate::write_support::internal_error;
use crate::{AppState, request_id_from_headers};

pub(super) fn router() -> Router<AppState> {
    Router::new()
        .route(
            "/api/content/{content_id}/discussion",
            get(get_content_discussion),
        )
        .route(
            "/api/news/items/{news_item_id}/discussion",
            get(get_news_item_discussion),
        )
}

#[utoipa::path(
    get,
    path = "/api/content/{content_id}/discussion",
    operation_id = "getContentDiscussion",
    tag = "content",
    params(("content_id" = i64, Path, description = "Content ID", minimum = 1)),
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, description = "Successful Response", body = ContentDiscussionResponse),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 404, description = "Content not found", body = newsly_contracts::ErrorEnvelope),
        (status = 422, description = "Validation Error", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn get_content_discussion(
    State(state): State<AppState>,
    headers: HeaderMap,
    path: Result<Path<i64>, PathRejection>,
    current_user: AuthenticatedUser,
) -> Result<Json<ContentDiscussionResponse>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    let content_id = positive_path_id(path, "content_id", &request_id)?;
    let row = find_visible_content_discussion(state.database.pool(), current_user.id, content_id)
        .await
        .map_err(|error| internal_error(error, &request_id))?
        .ok_or_else(|| not_found("Content not found", &request_id))?;
    Ok(Json(present_content_discussion(row)))
}

#[utoipa::path(
    get,
    path = "/api/news/items/{news_item_id}/discussion",
    operation_id = "getNewsItemDiscussion",
    tag = "news",
    params(("news_item_id" = i64, Path, description = "News item ID", minimum = 1)),
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, description = "Successful Response", body = ContentDiscussionResponse),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 404, description = "News item not found", body = newsly_contracts::ErrorEnvelope),
        (status = 422, description = "Validation Error", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn get_news_item_discussion(
    State(state): State<AppState>,
    headers: HeaderMap,
    path: Result<Path<i64>, PathRejection>,
    current_user: AuthenticatedUser,
) -> Result<Json<ContentDiscussionResponse>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    let news_item_id = positive_path_id(path, "news_item_id", &request_id)?;
    let row = find_visible_news_discussion(state.database.pool(), current_user.id, news_item_id)
        .await
        .map_err(|error| internal_error(error, &request_id))?
        .ok_or_else(|| not_found("News item not found", &request_id))?;
    Ok(Json(present_news_discussion(row)))
}

fn present_content_discussion(row: ContentDiscussionProjection) -> ContentDiscussionResponse {
    let stored = row.stored_discussion.as_ref().and_then(Value::as_object);
    let data = stored
        .and_then(|value| value.get("discussion_data"))
        .and_then(Value::as_object);
    build_legacy_response(LegacyDiscussionInput {
        resource_id: row.content_id,
        fallback_platform: row.platform,
        discussion_url: row.discussion_url,
        stored,
        data,
        explicit_status: None,
        explicit_error: None,
        explicit_fetched_at: None,
    })
}

fn present_news_discussion(row: NewsDiscussionProjection) -> ContentDiscussionResponse {
    if let Some(stored) = row
        .stored_news_discussion
        .as_ref()
        .and_then(Value::as_object)
    {
        let summary = stored.get("summary").and_then(parse_summary);
        let status = news_discussion_status(stored, summary.is_some());
        let links = summary.as_ref().map(summary_links).unwrap_or_default();
        let discussion_url = string_field(stored, "discussion_url").or(row.discussion_url);
        let raw_comments_present = stored
            .get("raw_comments_ref")
            .is_some_and(|value| !value.is_null());
        let comments = stored
            .get("raw_comments_ref")
            .and_then(Value::as_array)
            .map(|values| values.iter().filter_map(parse_comment).collect::<Vec<_>>())
            .unwrap_or_default();
        let mode = if discussion_url.is_some()
            || raw_comments_present
            || summary.is_some()
            || !comments.is_empty()
        {
            DiscussionMode::Comments
        } else {
            DiscussionMode::None
        };
        let error_message = (status == "failed")
            .then(|| string_field(stored, "last_refresh_error"))
            .flatten();
        return ContentDiscussionResponse {
            content_id: row.news_item_id,
            status,
            mode,
            platform: string_field(stored, "platform").or(row.platform),
            source_url: discussion_url.clone(),
            discussion_url,
            fetched_at: datetime_field(stored, "last_comments_fetched_at"),
            error_message,
            comments,
            discussion_groups: Vec::new(),
            links,
            summary,
            comment_count: integer_field(stored, "comment_count"),
            stats: news_stats(stored),
        };
    }

    let stored = row
        .stored_legacy_discussion
        .as_ref()
        .and_then(Value::as_object);
    let embedded = row.embedded_discussion.as_ref().and_then(Value::as_object);
    let data = stored
        .and_then(|value| value.get("discussion_data"))
        .and_then(Value::as_object)
        .or(embedded);
    build_legacy_response(LegacyDiscussionInput {
        resource_id: row.news_item_id,
        fallback_platform: row.platform,
        discussion_url: row.discussion_url,
        stored,
        data,
        explicit_status: row.embedded_status,
        explicit_error: row.embedded_error,
        explicit_fetched_at: row.embedded_fetched_at,
    })
}

struct LegacyDiscussionInput<'a> {
    resource_id: i64,
    fallback_platform: Option<String>,
    discussion_url: Option<String>,
    stored: Option<&'a Map<String, Value>>,
    data: Option<&'a Map<String, Value>>,
    explicit_status: Option<String>,
    explicit_error: Option<String>,
    explicit_fetched_at: Option<String>,
}

#[expect(
    clippy::too_many_lines,
    reason = "legacy discussion normalization is one compatibility translation boundary"
)]
fn build_legacy_response(input: LegacyDiscussionInput<'_>) -> ContentDiscussionResponse {
    if input.stored.is_none() && input.data.is_none() {
        return ContentDiscussionResponse {
            content_id: input.resource_id,
            status: "not_ready".to_owned(),
            mode: DiscussionMode::None,
            platform: input.fallback_platform,
            source_url: input.discussion_url.clone(),
            discussion_url: input.discussion_url,
            fetched_at: None,
            error_message: None,
            comments: Vec::new(),
            discussion_groups: Vec::new(),
            links: Vec::new(),
            summary: None,
            comment_count: None,
            stats: BTreeMap::new(),
        };
    }

    let data = input.data;
    let mode = data
        .and_then(|value| value.get("mode"))
        .and_then(Value::as_str)
        .map(|value| match value {
            "comments" => DiscussionMode::Comments,
            "discussion_list" => DiscussionMode::DiscussionList,
            _ => DiscussionMode::None,
        })
        .unwrap_or_default();
    let comments = data
        .and_then(|value| value.get("comments"))
        .and_then(Value::as_array)
        .map(|values| values.iter().filter_map(parse_comment).collect::<Vec<_>>())
        .unwrap_or_default();
    let discussion_groups = data
        .and_then(|value| value.get("discussion_groups"))
        .and_then(Value::as_array)
        .map(|values| values.iter().filter_map(parse_group).collect::<Vec<_>>())
        .unwrap_or_default();
    let links = data
        .and_then(|value| value.get("links"))
        .and_then(Value::as_array)
        .map(|values| values.iter().filter_map(parse_link).collect::<Vec<_>>())
        .unwrap_or_default();
    let source_url = data
        .and_then(|value| string_field(value, "source_url"))
        .or_else(|| input.discussion_url.clone());
    let error_message = input
        .stored
        .and_then(|value| string_field(value, "error_message"))
        .or(input.explicit_error);
    let status = input
        .stored
        .and_then(|value| string_field(value, "status"))
        .or(input.explicit_status)
        .unwrap_or_else(|| {
            infer_status(
                mode,
                &comments,
                &discussion_groups,
                &links,
                error_message.as_deref(),
                source_url.as_deref(),
                input.discussion_url.as_deref(),
            )
            .to_owned()
        });
    let fetched_at = input
        .stored
        .and_then(|value| datetime_field(value, "fetched_at"))
        .or_else(|| {
            input
                .explicit_fetched_at
                .as_deref()
                .and_then(parse_datetime)
        });
    let platform = input
        .stored
        .and_then(|value| string_field(value, "platform"))
        .or(input.fallback_platform);
    let discussion_stats = data
        .and_then(|value| value.get("stats"))
        .and_then(Value::as_object)
        .map(|value| value.clone().into_iter().collect())
        .unwrap_or_default();
    let summary = data
        .and_then(|value| value.get("summary"))
        .and_then(parse_summary);
    ContentDiscussionResponse {
        content_id: input.resource_id,
        status,
        mode,
        platform,
        source_url,
        discussion_url: input.discussion_url,
        fetched_at,
        error_message,
        comments,
        discussion_groups,
        links,
        summary,
        comment_count: None,
        stats: discussion_stats,
    }
}

fn parse_comment(value: &Value) -> Option<DiscussionCommentResponse> {
    let object = value.as_object()?;
    let comment_id = coerced_string(object.get("comment_id")?)?;
    if comment_id.trim().is_empty() {
        return None;
    }
    Some(DiscussionCommentResponse {
        comment_id,
        parent_id: object.get("parent_id").and_then(coerced_string),
        author: object.get("author").and_then(coerced_string),
        text: object
            .get("text")
            .and_then(coerced_string)
            .unwrap_or_default(),
        compact_text: object.get("compact_text").and_then(coerced_string),
        depth: object.get("depth").and_then(Value::as_i64).unwrap_or(0),
        created_at: object.get("created_at").and_then(coerced_string),
        source_url: object.get("source_url").and_then(coerced_string),
    })
}

fn parse_group(value: &Value) -> Option<DiscussionGroupResponse> {
    let object = value.as_object()?;
    let label = object.get("label").and_then(coerced_string)?;
    if label.trim().is_empty() {
        return None;
    }
    let items = object
        .get("items")
        .and_then(Value::as_array)
        .map(|entries| {
            entries
                .iter()
                .filter_map(|entry| {
                    let item = entry.as_object()?;
                    let url = item.get("url").and_then(coerced_string)?;
                    if url.trim().is_empty() {
                        return None;
                    }
                    let title = item
                        .get("title")
                        .and_then(coerced_string)
                        .unwrap_or_else(|| url.clone());
                    Some(DiscussionItemResponse { title, url })
                })
                .collect()
        })
        .unwrap_or_default();
    Some(DiscussionGroupResponse { label, items })
}

fn parse_link(value: &Value) -> Option<DiscussionLinkResponse> {
    let object = value.as_object()?;
    let url = object.get("url").and_then(coerced_string)?;
    if url.trim().is_empty() {
        return None;
    }
    Some(DiscussionLinkResponse {
        url,
        source: object
            .get("source")
            .and_then(coerced_string)
            .unwrap_or_else(|| "unknown".to_owned()),
        comment_id: object.get("comment_id").and_then(coerced_string),
        group_label: object.get("group_label").and_then(coerced_string),
        title: object.get("title").and_then(coerced_string),
    })
}

fn parse_summary(value: &Value) -> Option<DiscussionSummaryResponse> {
    let mut normalized = value.clone();
    if let Some(object) = normalized.as_object_mut()
        && let Some(raw) = object.get("generated_at").and_then(Value::as_str)
    {
        object.insert(
            "generated_at".to_owned(),
            parse_datetime(raw).map_or(Value::Null, |value| Value::String(value.to_rfc3339())),
        );
    }
    serde_json::from_value(normalized).ok()
}

fn summary_links(summary: &DiscussionSummaryResponse) -> Vec<DiscussionLinkResponse> {
    let mut seen = BTreeSet::new();
    summary
        .notable_links
        .iter()
        .filter_map(|link| {
            if link.url.trim().is_empty() || !seen.insert(link.url.clone()) {
                return None;
            }
            Some(DiscussionLinkResponse {
                url: link.url.clone(),
                source: "summary".to_owned(),
                comment_id: link.source_comment_id.clone(),
                group_label: None,
                title: link.title.clone().or_else(|| link.reason.clone()),
            })
        })
        .collect()
}

fn news_discussion_status(stored: &Map<String, Value>, has_summary: bool) -> String {
    if string_field(stored, "summary_status").as_deref() == Some("completed") && has_summary {
        "completed"
    } else if string_field(stored, "last_refresh_status").as_deref() == Some("failed")
        && !has_summary
    {
        "failed"
    } else if stored
        .get("raw_comments_ref")
        .is_some_and(|value| !value.is_null())
    {
        "partial"
    } else {
        "not_ready"
    }
    .to_owned()
}

fn news_stats(stored: &Map<String, Value>) -> BTreeMap<String, Value> {
    [
        "comment_count",
        "fetched_comment_count",
        "raw_comments_sha256",
        "last_count_checked_at",
        "last_comments_fetched_at",
        "next_refresh_after",
        "summary_status",
        "summary_version",
        "summary_generated_at",
    ]
    .into_iter()
    .map(|key| {
        (
            key.to_owned(),
            stored.get(key).cloned().unwrap_or(Value::Null),
        )
    })
    .collect()
}

fn infer_status(
    mode: DiscussionMode,
    comments: &[DiscussionCommentResponse],
    groups: &[DiscussionGroupResponse],
    links: &[DiscussionLinkResponse],
    error_message: Option<&str>,
    source_url: Option<&str>,
    discussion_url: Option<&str>,
) -> &'static str {
    let renderable = match mode {
        DiscussionMode::Comments => !comments.is_empty() || !links.is_empty(),
        DiscussionMode::DiscussionList => !groups.is_empty() || !links.is_empty(),
        DiscussionMode::None => false,
    };
    if renderable {
        "completed"
    } else if error_message.is_some() {
        if source_url.is_some() || discussion_url.is_some() {
            "partial"
        } else {
            "failed"
        }
    } else if source_url.is_some() || discussion_url.is_some() {
        "partial"
    } else {
        "not_ready"
    }
}

fn string_field(object: &Map<String, Value>, field: &str) -> Option<String> {
    object.get(field).and_then(coerced_string)
}

fn integer_field(object: &Map<String, Value>, field: &str) -> Option<i64> {
    object.get(field).and_then(Value::as_i64)
}

fn datetime_field(object: &Map<String, Value>, field: &str) -> Option<DateTime<Utc>> {
    object
        .get(field)
        .and_then(Value::as_str)
        .and_then(parse_datetime)
}

fn coerced_string(value: &Value) -> Option<String> {
    match value {
        Value::Null => None,
        Value::String(value) => Some(value.clone()),
        other => Some(other.to_string()),
    }
}

fn parse_datetime(value: &str) -> Option<DateTime<Utc>> {
    DateTime::parse_from_rfc3339(value)
        .map(|value| value.with_timezone(&Utc))
        .ok()
        .or_else(|| {
            NaiveDateTime::parse_from_str(value, "%Y-%m-%dT%H:%M:%S%.f")
                .map(|value| value.and_utc())
                .ok()
        })
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

fn not_found(message: &str, request_id: &str) -> ApiError {
    ApiError::new(
        StatusCode::NOT_FOUND,
        "not_found",
        message,
        request_id.to_owned(),
    )
}
