use std::str::FromStr;

use axum::extract::rejection::JsonRejection;
use axum::extract::{Extension, State};
use axum::http::{HeaderMap, StatusCode};
use axum::routing::post;
use axum::{Json, Router};
use newsly_contracts::{
    ContentStatus, ContentSubmissionResponse, ContentType, SubmitContentRequest,
};
use newsly_db::{
    ContentSubmissionInput, ContentSubmissionRepositoryError, SubmissionTaskResolution,
    apply_content_submission,
};
use newsly_queue::{EnqueueRequest, QueueError, QueueKernel, TaskType};
use serde_json::{Map, Value};
use sha2::{Digest, Sha256};

use crate::auth::AuthenticatedUser;
use crate::encoding::hex_encode;
use crate::error::ApiError;
use crate::gateway::RouteOwnershipStamp;
use crate::write_support::{decode_json, internal_error, require_operation, verify_stamp};
use crate::{AppState, request_id_from_headers};

const SUBMIT_CONTENT_OPERATION_ID: &str = "submitContent";
const MAX_HTTP_URL_CHARS: usize = 2_083;

pub(super) fn router() -> Router<AppState> {
    Router::new().route("/api/content/submit", post(submit_content))
}

#[utoipa::path(
    post,
    path = "/api/content/submit",
    operation_id = "submitContent",
    tag = "content",
    summary = "Submit a one-off URL for processing",
    description = "Submit article or podcast URLs for processing. Only http/https URLs are accepted.",
    request_body = SubmitContentRequest,
    security(("HTTPBearer" = [])),
    responses(
        (status = 201, description = "Content created and queued", body = ContentSubmissionResponse),
        (status = 200, description = "Existing content matched and reused", body = ContentSubmissionResponse),
        (status = 400, description = "Content state is invalid", body = newsly_contracts::ErrorEnvelope),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 403, description = "Authentication required", body = newsly_contracts::ErrorEnvelope),
        (status = 409, description = "Stale runtime owner", body = newsly_contracts::ErrorEnvelope),
        (status = 422, description = "Validation Error", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
#[allow(clippy::too_many_lines)]
pub(super) async fn submit_content(
    State(state): State<AppState>,
    headers: HeaderMap,
    current_user: AuthenticatedUser,
    Extension(stamp): Extension<RouteOwnershipStamp>,
    payload: Result<Json<SubmitContentRequest>, JsonRejection>,
) -> Result<(StatusCode, Json<ContentSubmissionResponse>), ApiError> {
    let request_id = request_id_from_headers(&headers);
    require_operation(&stamp, SUBMIT_CONTENT_OPERATION_ID, &request_id)?;
    let Json(payload) = decode_json(payload, &request_id)?;
    let normalized = NormalizedSubmission::try_from_request(payload, &request_id)?;

    let mut transaction = state
        .database
        .pool()
        .begin()
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    verify_stamp(&mut transaction, &stamp, &request_id).await?;
    let applied = apply_content_submission(
        &mut transaction,
        &ContentSubmissionInput {
            url: &normalized.url,
            title: normalized.title.as_deref(),
            platform: normalized.platform.as_deref(),
            instruction: normalized.instruction.as_deref(),
            crawl_links: normalized.crawl_links,
            subscribe_to_feed: false,
            share_and_chat: normalized.share_and_chat,
            chat_initial_message: normalized.chat_initial_message.as_deref(),
            save_to_knowledge_and_mark_read: normalized.save_to_knowledge_and_mark_read,
            user_id: current_user.id,
            submitted_via: "share_sheet",
        },
    )
    .await
    .map_err(|error| repository_error(error, &request_id))?;

    let queue = QueueKernel::new(state.database.pool().clone());
    let mut task_id = None;
    if let SubmissionTaskResolution::Reuse(existing_task_id) = applied.task_resolution {
        queue
            .grant_access_in_transaction(&mut transaction, existing_task_id, current_user.id)
            .await
            .map_err(|error| queue_error(error, &request_id))?;
        task_id = Some(existing_task_id);
    }

    let mut requests = Vec::new();
    // Preserve the established observable enqueue ordering. A completed share-and-chat request is
    // handed to the chat queue before any optional reanalysis for the same content.
    if applied.enqueue_dig_deeper {
        requests.push(dig_deeper_request(
            applied.content_id,
            current_user.id,
            normalized.chat_initial_message.as_deref(),
        ));
    }
    let analyze_request_index =
        if applied.task_resolution == SubmissionTaskResolution::EnqueueAnalyze {
            let index = requests.len();
            requests.push(analyze_request(&applied, &normalized, current_user.id));
            Some(index)
        } else {
            None
        };
    if applied.enqueue_generated_image {
        let mut request = EnqueueRequest::new(TaskType::GenerateImage);
        request.content_id = Some(applied.content_id);
        requests.push(request);
    }
    if !requests.is_empty() {
        let enqueued = queue
            .enqueue_many_in_transaction(&mut transaction, requests)
            .await
            .map_err(|error| queue_error(error, &request_id))?;
        if let Some(index) = analyze_request_index {
            task_id = enqueued.task_ids.get(index).copied();
            if task_id.is_none() {
                return Err(internal_error(
                    "content submission queue omitted the analysis task id",
                    &request_id,
                ));
            }
        }
    }
    transaction
        .commit()
        .await
        .map_err(|error| internal_error(error, &request_id))?;

    let content_type = ContentType::from_str(&applied.content_type)
        .map_err(|error| invalid_durable_content(error, &request_id))?;
    let status = ContentStatus::from_str(&applied.status)
        .map_err(|error| invalid_durable_content(error, &request_id))?;
    let response_status = if applied.already_exists {
        StatusCode::OK
    } else {
        StatusCode::CREATED
    };
    Ok((
        response_status,
        Json(ContentSubmissionResponse {
            content_id: applied.content_id,
            content_type,
            status,
            platform: applied.platform,
            already_exists: applied.already_exists,
            message: if applied.already_exists {
                "Content already submitted; using existing record".to_owned()
            } else {
                "Content queued for analysis".to_owned()
            },
            task_id,
            source: Some(applied.source),
        }),
    ))
}

#[derive(Debug)]
#[allow(clippy::struct_excessive_bools)]
struct NormalizedSubmission {
    url: String,
    title: Option<String>,
    platform: Option<String>,
    instruction: Option<String>,
    crawl_links: bool,
    share_and_chat: bool,
    chat_initial_message: Option<String>,
    save_to_knowledge_and_mark_read: bool,
}

impl NormalizedSubmission {
    fn try_from_request(payload: SubmitContentRequest, request_id: &str) -> Result<Self, ApiError> {
        if payload.subscribe_to_feed {
            return Err(ApiError::new(
                StatusCode::BAD_REQUEST,
                "feed_subscription_requires_dedicated_endpoint",
                "Use POST /api/scrapers/subscribe or a Share Add Feed action to subscribe",
                request_id.to_owned(),
            ));
        }
        validate_max_chars(payload.title.as_deref(), 500, "title", request_id)?;
        validate_max_chars(payload.platform.as_deref(), 50, "platform", request_id)?;
        validate_max_chars(
            payload.instruction.as_deref(),
            4_000,
            "instruction",
            request_id,
        )?;
        validate_max_chars(
            payload.chat_initial_message.as_deref(),
            2_000,
            "chat_initial_message",
            request_id,
        )?;
        let url = normalize_http_url(&payload.url, request_id)?;
        let platform = clean_optional(payload.platform).map(|value| value.to_lowercase());
        let instruction = clean_optional(payload.instruction);
        let chat_initial_message = clean_optional(payload.chat_initial_message);
        Ok(Self {
            url,
            title: payload.title,
            platform,
            instruction,
            crawl_links: payload.crawl_links,
            share_and_chat: payload.share_and_chat,
            chat_initial_message,
            save_to_knowledge_and_mark_read: payload.save_to_knowledge_and_mark_read,
        })
    }
}

fn normalize_http_url(raw_url: &str, request_id: &str) -> Result<String, ApiError> {
    let raw_url = raw_url.trim();
    if raw_url.chars().count() > MAX_HTTP_URL_CHARS {
        return Err(validation_error(
            "url must contain at most 2083 characters",
            request_id,
        ));
    }
    let parsed = reqwest::Url::parse(raw_url)
        .map_err(|_| validation_error("url must be a valid HTTP URL", request_id))?;
    if !matches!(parsed.scheme(), "http" | "https") || parsed.host().is_none() {
        return Err(validation_error(
            "url must use http or https and include a host",
            request_id,
        ));
    }
    Ok(parsed.to_string())
}

fn validate_max_chars(
    value: Option<&str>,
    max_chars: usize,
    field: &str,
    request_id: &str,
) -> Result<(), ApiError> {
    if value.is_some_and(|value| value.chars().count() > max_chars) {
        return Err(validation_error(
            format!("{field} must contain at most {max_chars} characters"),
            request_id,
        ));
    }
    Ok(())
}

fn clean_optional(value: Option<String>) -> Option<String> {
    value
        .map(|value| value.trim().to_owned())
        .filter(|value| !value.is_empty())
}

fn analyze_request(
    applied: &newsly_db::AppliedContentSubmission,
    normalized: &NormalizedSubmission,
    user_id: i64,
) -> EnqueueRequest {
    let mut payload = Map::from_iter([("content_id".to_owned(), Value::from(applied.content_id))]);
    if let Some(instruction) = &normalized.instruction {
        payload.insert("instruction".to_owned(), Value::from(instruction.clone()));
    }
    if normalized.crawl_links {
        payload.insert("crawl_links".to_owned(), Value::Bool(true));
    }
    let mut request = EnqueueRequest::new(TaskType::AnalyzeUrl);
    request.content_id = Some(applied.content_id);
    request.payload = Some(payload);
    request.dedupe = Some(true);
    request.access_user_id = Some(user_id);
    request
}

fn dig_deeper_request(
    content_id: i64,
    user_id: i64,
    initial_message: Option<&str>,
) -> EnqueueRequest {
    let mut payload = Map::from_iter([("user_id".to_owned(), Value::from(user_id))]);
    if let Some(message) = initial_message {
        payload.insert("initial_message".to_owned(), Value::from(message));
    }
    let digest = Sha256::digest(initial_message.unwrap_or_default().as_bytes());
    let message_hash = hex_encode(&digest)[..16].to_owned();
    let mut request = EnqueueRequest::new(TaskType::DigDeeper);
    request.content_id = Some(content_id);
    request.payload = Some(payload);
    request.dedupe_key = Some(format!(
        "dig_deeper|user:{user_id}|content:{content_id}|message:{message_hash}"
    ));
    request.owner_user_id = Some(user_id);
    request
}

fn validation_error(message: impl Into<String>, request_id: &str) -> ApiError {
    ApiError::new(
        StatusCode::UNPROCESSABLE_ENTITY,
        "validation_error",
        message,
        request_id.to_owned(),
    )
}

fn repository_error(error: ContentSubmissionRepositoryError, request_id: &str) -> ApiError {
    match error {
        ContentSubmissionRepositoryError::UserMissingOrInactive => ApiError::new(
            StatusCode::BAD_REQUEST,
            "inactive_user",
            "Task user is missing or inactive",
            request_id.to_owned(),
        ),
        other => internal_error(other, request_id),
    }
}

fn queue_error(error: QueueError, request_id: &str) -> ApiError {
    match error {
        QueueError::UserMissingOrInactive => ApiError::new(
            StatusCode::BAD_REQUEST,
            "inactive_user",
            "Task user is missing or inactive",
            request_id.to_owned(),
        ),
        other => internal_error(other, request_id),
    }
}

fn invalid_durable_content(error: impl std::fmt::Display, request_id: &str) -> ApiError {
    tracing::error!(error = %error, "content submission loaded an invalid durable enum");
    ApiError::new(
        StatusCode::BAD_REQUEST,
        "invalid_content_state",
        "Content has an invalid type or status",
        request_id.to_owned(),
    )
}

#[cfg(test)]
mod tests {
    use axum::{http::StatusCode, response::IntoResponse};
    use newsly_contracts::SubmitContentRequest;

    use super::{NormalizedSubmission, normalize_http_url};

    #[test]
    fn request_normalization_preserves_submission_defaults() {
        let normalized = NormalizedSubmission::try_from_request(
            SubmitContentRequest {
                url: " https://EXAMPLE.com ".to_owned(),
                content_type: None,
                title: Some("  supplied title  ".to_owned()),
                platform: Some("  YouTube  ".to_owned()),
                instruction: Some("  inspect links  ".to_owned()),
                crawl_links: false,
                subscribe_to_feed: false,
                share_and_chat: false,
                chat_initial_message: Some("  start here  ".to_owned()),
                save_to_knowledge_and_mark_read: false,
            },
            "test",
        )
        .unwrap();

        assert_eq!(normalized.url, "https://example.com/");
        assert_eq!(normalized.title.as_deref(), Some("  supplied title  "));
        assert_eq!(normalized.platform.as_deref(), Some("youtube"));
        assert_eq!(normalized.instruction.as_deref(), Some("inspect links"));
        assert_eq!(
            normalized.chat_initial_message.as_deref(),
            Some("start here")
        );
    }

    #[test]
    fn invalid_scheme_is_rejected() {
        assert!(normalize_http_url("ftp://example.com/file", "test").is_err());
    }

    #[test]
    fn legacy_content_feed_subscription_is_rejected_before_persistence() {
        let error = NormalizedSubmission::try_from_request(
            SubmitContentRequest {
                url: "https://example.com/feed.xml".to_owned(),
                content_type: None,
                title: None,
                platform: None,
                instruction: None,
                crawl_links: false,
                subscribe_to_feed: true,
                share_and_chat: false,
                chat_initial_message: None,
                save_to_knowledge_and_mark_read: false,
            },
            "test",
        )
        .expect_err("legacy content subscription must use the canonical feed endpoint");

        assert_eq!(error.into_response().status(), StatusCode::BAD_REQUEST);
    }
}
