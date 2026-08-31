use axum::extract::rejection::{PathRejection, QueryRejection};
use axum::extract::{Path, Query, State};
use axum::http::{HeaderMap, StatusCode};
use axum::routing::get;
use axum::{Json, Router};
use newsly_contracts::{ChatGptUrlResponse, ContentBodyResponse};
use newsly_db::{
    ContentBodyProjection, ContentBodyVariant, find_visible_content_body,
    find_visible_news_item_body,
};
use serde::Deserialize;

use crate::auth::AuthenticatedUser;
use crate::error::ApiError;
use crate::write_support::internal_error;
use crate::{AppState, request_id_from_headers};

const MAX_CONTENT_BODY_RESPONSE_CHARS: usize = 32_000;
const CHATGPT_BASE_URL: &str = "https://chat.openai.com/?q=";
const MAX_CHATGPT_URL_LENGTH: usize = 8_000;
const TRUNCATED_BODY_NOTICE: &str =
    "\n\n[Content truncated for app rendering. Open the original source for the full text.]";

pub(super) fn router() -> Router<AppState> {
    Router::new()
        .route("/api/content/{content_id}/body", get(get_content_body))
        .route(
            "/api/content/{content_id}/chat-url",
            get(get_content_chat_url),
        )
        .route(
            "/api/news/items/{news_item_id}/body",
            get(get_news_item_body),
        )
}

#[derive(Debug, Deserialize)]
pub(super) struct ChatUrlQuery {
    user_prompt: Option<String>,
}

#[utoipa::path(
    get,
    path = "/api/content/{content_id}/chat-url",
    operation_id = "getContentChatChatgptUrl",
    tag = "content",
    params(
        ("content_id" = i64, Path, description = "Content ID", minimum = 1),
        ("user_prompt" = Option<String>, Query, description = "Optional user prompt", max_length = 2000)
    ),
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, description = "Successful Response", body = ChatGptUrlResponse),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 404, description = "Content not found", body = newsly_contracts::ErrorEnvelope),
        (status = 422, description = "Validation Error", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
#[expect(
    clippy::too_many_lines,
    reason = "the compatibility route builds one bounded prompt and URL without reusable subflows"
)]
pub(super) async fn get_content_chat_url(
    State(state): State<AppState>,
    headers: HeaderMap,
    path: Result<Path<i64>, PathRejection>,
    query: Result<Query<ChatUrlQuery>, QueryRejection>,
    current_user: AuthenticatedUser,
) -> Result<Json<ChatGptUrlResponse>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    let content_id = positive_path_id(path, "content_id", &request_id)?;
    let Query(query) = query.map_err(|rejection| {
        validation_error(
            format!("Request validation failed: {}", rejection.body_text()),
            &request_id,
        )
    })?;
    if query
        .user_prompt
        .as_ref()
        .is_some_and(|value| value.chars().count() > 2_000)
    {
        return Err(validation_error(
            "user_prompt must contain at most 2000 characters",
            &request_id,
        ));
    }
    let projection = find_visible_content_body(
        state.database.pool(),
        current_user.id,
        content_id,
        ContentBodyVariant::Source,
    )
    .await
    .map_err(|error| internal_error(error, &request_id))?
    .ok_or_else(|| not_found("Content not found", &request_id))?;
    let stored_text = if let Some(pointer) = &projection.pointer {
        state
            .content_body_store
            .get_text(&pointer.storage_key)
            .await
            .map_err(|error| internal_error(error, &request_id))?
    } else {
        None
    };
    let body_text = stored_text.or_else(|| projection.fallback_text.clone());
    let display_title = resolve_display_title(&projection);
    let mut prompt_parts = Vec::new();
    if let Some(user_prompt) = query
        .user_prompt
        .as_deref()
        .map(str::trim)
        .filter(|value| !value.is_empty())
    {
        prompt_parts.extend([
            "USER PROMPT:".to_owned(),
            user_prompt.to_owned(),
            String::new(),
        ]);
    }
    prompt_parts.push(format!(
        "I'd like to discuss this {}:",
        projection.content_type
    ));
    prompt_parts.push(format!("Title: {display_title}"));
    if let Some(source) = projection
        .source
        .as_deref()
        .map(str::trim)
        .filter(|value| !value.is_empty())
    {
        prompt_parts.push(format!("Source: {source}"));
    }
    if let Some(publication_date) = projection.publication_date {
        prompt_parts.push(format!(
            "Published: {}",
            publication_date.format("%B %d, %Y")
        ));
    }
    if let Some(content) = body_text
        .as_deref()
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .or_else(|| fallback_discussion_text(&projection.metadata))
    {
        prompt_parts.push(String::new());
        let label = if projection.kind == "transcript" {
            "Transcript"
        } else {
            "Full Content"
        };
        prompt_parts.push(format!("{label}:"));
        prompt_parts.push(content.to_owned());
    }
    let full_prompt = prompt_parts.join("\n");
    let mut full_url = format!("{CHATGPT_BASE_URL}{}", quote_plus(&full_prompt));
    let mut truncated = false;
    if full_url.len() > MAX_CHATGPT_URL_LENGTH {
        truncated = true;
        let short_context = format!("Chat about: {display_title}");
        let available = MAX_CHATGPT_URL_LENGTH.saturating_sub(CHATGPT_BASE_URL.len() + 100);
        let truncated_prompt = if quote_plus(&short_context).len() < available {
            let content = body_text.unwrap_or_default();
            let excerpt = content.chars().take(available / 3).collect::<String>();
            format!("{short_context}\n{excerpt}\n\n[Content truncated for URL length...]")
        } else {
            short_context
        };
        full_url = format!("{CHATGPT_BASE_URL}{}", quote_plus(&truncated_prompt));
    }
    Ok(Json(ChatGptUrlResponse {
        chat_url: full_url,
        truncated,
    }))
}

fn resolve_display_title(projection: &ContentBodyProjection) -> String {
    projection
        .title
        .as_deref()
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(str::to_owned)
        .or_else(|| {
            projection
                .metadata
                .get("summary")
                .and_then(serde_json::Value::as_object)
                .and_then(|summary| summary.get("title"))
                .and_then(serde_json::Value::as_str)
                .map(str::trim)
                .filter(|value| !value.is_empty())
                .map(str::to_owned)
        })
        .unwrap_or_else(|| format!("Untitled {}", projection.content_type))
}

fn fallback_discussion_text(metadata: &serde_json::Value) -> Option<&str> {
    let summary = metadata.get("summary")?.as_object()?;
    ["full_markdown", "summary", "overview", "takeaway"]
        .into_iter()
        .find_map(|field| {
            summary
                .get(field)
                .and_then(serde_json::Value::as_str)
                .map(str::trim)
                .filter(|value| !value.is_empty())
        })
}

fn quote_plus(value: &str) -> String {
    const HEX: &[u8; 16] = b"0123456789ABCDEF";
    let mut encoded = String::with_capacity(value.len());
    for byte in value.as_bytes() {
        match *byte {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => {
                encoded.push(char::from(*byte));
            }
            b' ' => encoded.push('+'),
            other => {
                encoded.push('%');
                encoded.push(char::from(HEX[usize::from(other >> 4)]));
                encoded.push(char::from(HEX[usize::from(other & 0x0f)]));
            }
        }
    }
    encoded
}

#[derive(Debug, Deserialize)]
pub(super) struct BodyQuery {
    #[serde(default = "default_variant")]
    variant: String,
}

fn default_variant() -> String {
    "source".to_owned()
}

#[utoipa::path(
    get,
    path = "/api/content/{content_id}/body",
    operation_id = "getContentBody",
    tag = "content",
    params(
        ("content_id" = i64, Path, description = "Content ID", minimum = 1),
        ("variant" = Option<String>, Query, description = "Body variant")
    ),
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, description = "Successful Response", body = ContentBodyResponse),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 404, description = "Content body not found", body = newsly_contracts::ErrorEnvelope),
        (status = 422, description = "Validation Error", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn get_content_body(
    State(state): State<AppState>,
    headers: HeaderMap,
    path: Result<Path<i64>, PathRejection>,
    query: Result<Query<BodyQuery>, QueryRejection>,
    current_user: AuthenticatedUser,
) -> Result<Json<ContentBodyResponse>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    let content_id = positive_path_id(path, "content_id", &request_id)?;
    let variant = parse_variant(query, &request_id)?;
    let projection =
        find_visible_content_body(state.database.pool(), current_user.id, content_id, variant)
            .await
            .map_err(|error| internal_error(error, &request_id))?
            .ok_or_else(|| not_found("Content not found", &request_id))?;
    resolve_projection(&state, projection, "Content body not found", &request_id).await
}

#[utoipa::path(
    get,
    path = "/api/news/items/{news_item_id}/body",
    operation_id = "getNewsItemBody",
    tag = "news",
    params(
        ("news_item_id" = i64, Path, description = "News item ID", minimum = 1),
        ("variant" = Option<String>, Query, description = "Body variant")
    ),
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, description = "Successful Response", body = ContentBodyResponse),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 404, description = "News item body not found", body = newsly_contracts::ErrorEnvelope),
        (status = 422, description = "Validation Error", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn get_news_item_body(
    State(state): State<AppState>,
    headers: HeaderMap,
    path: Result<Path<i64>, PathRejection>,
    query: Result<Query<BodyQuery>, QueryRejection>,
    current_user: AuthenticatedUser,
) -> Result<Json<ContentBodyResponse>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    let news_item_id = positive_path_id(path, "news_item_id", &request_id)?;
    let variant = parse_variant(query, &request_id)?;
    let projection = find_visible_news_item_body(
        state.database.pool(),
        current_user.id,
        news_item_id,
        variant,
    )
    .await
    .map_err(|error| internal_error(error, &request_id))?
    .ok_or_else(|| not_found("News item not found", &request_id))?;
    resolve_projection(&state, projection, "News item body not found", &request_id).await
}

async fn resolve_projection(
    state: &AppState,
    projection: ContentBodyProjection,
    missing_message: &str,
    request_id: &str,
) -> Result<Json<ContentBodyResponse>, ApiError> {
    if let Some(pointer) = &projection.pointer {
        let stored = state
            .content_body_store
            .get_text(&pointer.storage_key)
            .await
            .map_err(|error| internal_error(error, request_id))?;
        if let Some(text) = stored {
            let format = valid_format(&pointer.content_format)
                .ok_or_else(|| internal_error("invalid stored content body format", request_id))?;
            return Ok(Json(ContentBodyResponse {
                content_id: projection.response_content_id,
                variant: projection.variant.as_str().to_owned(),
                kind: projection.kind,
                format: format.to_owned(),
                text: truncate_body_text(&text),
                updated_at: pointer.updated_at,
            }));
        }
    }
    let Some(text) = projection.fallback_text else {
        return Err(not_found(missing_message, request_id));
    };
    let format = projection
        .fallback_format
        .as_deref()
        .and_then(valid_format)
        .unwrap_or("text");
    Ok(Json(ContentBodyResponse {
        content_id: projection.response_content_id,
        variant: projection.variant.as_str().to_owned(),
        kind: projection.kind,
        format: format.to_owned(),
        text: truncate_body_text(&text),
        updated_at: projection.fallback_updated_at,
    }))
}

fn valid_format(value: &str) -> Option<&str> {
    matches!(value, "text" | "markdown").then_some(value)
}

fn truncate_body_text(text: &str) -> String {
    if text.chars().count() <= MAX_CONTENT_BODY_RESPONSE_CHARS {
        return text.to_owned();
    }
    let notice_chars = TRUNCATED_BODY_NOTICE.chars().count();
    let available = MAX_CONTENT_BODY_RESPONSE_CHARS.saturating_sub(notice_chars);
    if available == 0 {
        return TRUNCATED_BODY_NOTICE.trim().to_owned();
    }
    let mut trimmed = text.chars().take(available).collect::<String>();
    trimmed = trimmed.trim_end().to_owned();
    let split_at = [
        trimmed.rfind("\n\n"),
        trimmed.rfind('\n'),
        trimmed.rfind(' '),
    ]
    .into_iter()
    .flatten()
    .max();
    if let Some(split_at) = split_at
        && trimmed[..split_at].chars().count() >= available / 2
    {
        trimmed.truncate(split_at);
        trimmed = trimmed.trim_end().to_owned();
    }
    format!("{trimmed}{TRUNCATED_BODY_NOTICE}")
}

fn parse_variant(
    query: Result<Query<BodyQuery>, QueryRejection>,
    request_id: &str,
) -> Result<ContentBodyVariant, ApiError> {
    let Query(query) = query.map_err(|rejection| {
        validation_error(
            format!("Request validation failed: {}", rejection.body_text()),
            request_id,
        )
    })?;
    match query.variant.as_str() {
        "source" => Ok(ContentBodyVariant::Source),
        "rendered" => Ok(ContentBodyVariant::Rendered),
        _ => Err(validation_error(
            "variant must be either source or rendered",
            request_id,
        )),
    }
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
