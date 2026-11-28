use std::env;
use std::path::{Component, Path as FilePath};

use axum::extract::Path;
use axum::extract::rejection::PathRejection;
use axum::http::StatusCode;
use newsly_db::{
    ContentSubmissionRepositoryError, LearningDeckRepositoryError, is_active_learning_deck_conflict,
};
use newsly_queue::{EnqueueRequest, QueueError, TaskType};
use serde_json::{Map, Value};

use crate::error::ApiError;
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

pub(super) use crate::write_support::external_url;

pub(super) fn valid_deck_id(
    path: Result<Path<i64>, PathRejection>,
    request_id: &str,
) -> Result<i64, ApiError> {
    crate::write_support::positive_path_id(path, "deck_id", request_id)
}

pub(super) fn clean_optional(value: Option<String>) -> Option<String> {
    value
        .map(|value| value.trim().to_owned())
        .filter(|value| !value.is_empty())
}

pub(super) fn validation_error(message: impl Into<String>, request_id: &str) -> ApiError {
    ApiError::validation(message, request_id)
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
            "This Learning Deck already has an active generation",
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
