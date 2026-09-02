use std::env;
use std::path::{Component, Path as FilePath};

use axum::extract::Path;
use axum::extract::rejection::PathRejection;
use axum::http::header::HOST;
use axum::http::{HeaderMap, StatusCode};
use newsly_db::{
    ContentSubmissionRepositoryError, LearningDeckRepositoryError, is_active_learning_deck_conflict,
};
use newsly_queue::{EnqueueRequest, QueueError, TaskType};
use reqwest::Url;
use serde_json::{Map, Value, json};

use crate::error::ApiError;
use crate::learning_deck_tokens::LearningDeckTokenSigner;
use crate::write_support::internal_error;

pub(super) fn content_task_request(task_type: TaskType, content_id: i64) -> EnqueueRequest {
    let mut request = EnqueueRequest::new(task_type);
    request.content_id = Some(content_id);
    request.payload = Some(Map::from_iter([(
        "content_id".to_owned(),
        Value::from(content_id),
    )]));
    request
}

pub(super) fn run_llm_task_request(task_id: i64, user_id: i64) -> EnqueueRequest {
    let mut request = EnqueueRequest::new(TaskType::RunLlmTask);
    request.payload = Some(Map::from_iter([
        ("llm_task_id".to_owned(), Value::from(task_id)),
        ("user_id".to_owned(), Value::from(user_id)),
    ]));
    request.owner_user_id = Some(user_id);
    request
}

pub(super) fn sandbox_root(request_id: &str) -> Result<String, ApiError> {
    let root = env::var("LLM_TASK_SANDBOX_ROOT").unwrap_or_else(|_| "/data/workspace".to_owned());
    let root = root.trim();
    let path = FilePath::new(root);
    if root.is_empty()
        || root == "/"
        || !path.is_absolute()
        || path.components().any(|component| {
            matches!(
                component,
                Component::ParentDir | Component::CurDir | Component::Prefix(_)
            )
        })
    {
        return Err(internal_error(
            "LLM_TASK_SANDBOX_ROOT must be an absolute normalized non-root path",
            request_id,
        ));
    }
    Ok(root.trim_end_matches('/').to_owned())
}

pub(super) fn external_url(
    headers: &HeaderMap,
    path: &str,
    request_id: &str,
) -> Result<String, ApiError> {
    let configured = env::var("PUBLIC_BASE_URL")
        .ok()
        .map(|value| value.trim().to_owned())
        .filter(|value| !value.is_empty());
    let mut base = if let Some(configured) = configured {
        let parsed = Url::parse(&configured).map_err(|error| internal_error(error, request_id))?;
        if parsed.host().is_none()
            || !matches!(parsed.scheme(), "http" | "https")
            || !parsed.username().is_empty()
            || parsed.password().is_some()
            || parsed.query().is_some()
            || parsed.fragment().is_some()
            || !matches!(parsed.path(), "" | "/")
            || (env::var("ENVIRONMENT").is_ok_and(|value| value.eq_ignore_ascii_case("production"))
                && parsed.scheme() != "https")
        {
            return Err(internal_error("PUBLIC_BASE_URL is invalid", request_id));
        }
        parsed
    } else {
        if env::var("ENVIRONMENT").is_ok_and(|value| value.eq_ignore_ascii_case("production")) {
            return Err(internal_error(
                "PUBLIC_BASE_URL is required in production",
                request_id,
            ));
        }
        let host = headers
            .get(HOST)
            .and_then(|value| value.to_str().ok())
            .ok_or_else(|| internal_error("request Host header is missing", request_id))?;
        let scheme = headers
            .get("x-forwarded-proto")
            .and_then(|value| value.to_str().ok())
            .and_then(|value| value.split(',').next())
            .map(str::trim)
            .filter(|value| matches!(*value, "http" | "https"))
            .unwrap_or("http");
        Url::parse(&format!("{scheme}://{host}"))
            .map_err(|error| internal_error(error, request_id))?
    };
    base.set_path(path);
    base.set_query(None);
    base.set_fragment(None);
    Ok(base.to_string())
}

pub(super) fn token_signer(request_id: &str) -> Result<LearningDeckTokenSigner, ApiError> {
    LearningDeckTokenSigner::from_environment().map_err(|error| internal_error(error, request_id))
}

pub(super) fn valid_deck_id(
    path: Result<Path<i64>, PathRejection>,
    request_id: &str,
) -> Result<i64, ApiError> {
    let Path(deck_id) =
        path.map_err(|rejection| validation_error(rejection.body_text(), request_id))?;
    if deck_id <= 0 {
        return Err(validation_error(
            "deck_id must be greater than zero",
            request_id,
        ));
    }
    Ok(deck_id)
}

pub(super) fn clean_optional(value: Option<String>) -> Option<String> {
    value
        .map(|value| value.trim().to_owned())
        .filter(|value| !value.is_empty())
}

pub(super) fn validation_error(message: impl Into<String>, request_id: &str) -> ApiError {
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

pub(super) fn deck_error(status: StatusCode, message: &str, request_id: &str) -> ApiError {
    let code = match status {
        StatusCode::NOT_FOUND => "not_found",
        StatusCode::CONFLICT => "invalid_state",
        StatusCode::BAD_REQUEST => "bad_request",
        _ => "learning_deck_error",
    };
    ApiError::new(status, code, message, request_id.to_owned())
}

pub(super) fn repository_error(error: LearningDeckRepositoryError, request_id: &str) -> ApiError {
    internal_error(error, request_id)
}

pub(super) fn create_repository_error(
    error: LearningDeckRepositoryError,
    request_id: &str,
) -> ApiError {
    if is_active_learning_deck_conflict(&error) {
        deck_error(
            StatusCode::CONFLICT,
            "A Learning Deck is already generating",
            request_id,
        )
    } else {
        match error {
            LearningDeckRepositoryError::DeckContentSourceMissing => deck_error(
                StatusCode::NOT_FOUND,
                "Learning Deck content source not found",
                request_id,
            ),
            LearningDeckRepositoryError::ContentSourceMissing => deck_error(
                StatusCode::NOT_FOUND,
                "Content source not found",
                request_id,
            ),
            LearningDeckRepositoryError::UnsupportedSource(source_kind) => deck_error(
                StatusCode::CONFLICT,
                &format!("Unsupported Learning Deck source kind: {source_kind}"),
                request_id,
            ),
            other => repository_error(other, request_id),
        }
    }
}

pub(super) fn submission_error(
    error: ContentSubmissionRepositoryError,
    request_id: &str,
) -> ApiError {
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

pub(super) fn queue_error(error: QueueError, request_id: &str) -> ApiError {
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
