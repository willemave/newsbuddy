use axum::extract::rejection::JsonRejection;
use axum::extract::{Extension, Path, State};
use axum::http::{HeaderMap, StatusCode};
use axum::routing::{delete, post};
use axum::{Json, Router};
use newsly_contracts::{
    BulkMarkReadRequest, BulkMarkReadResponse, KnowledgeMutationResponse, KnowledgeMutationStatus,
    MarkReadResponse, MarkUnreadResponse, OperationStatus,
};
use newsly_db::{
    KnowledgeReprocessOutcome, content_exists, mark_content_read as persist_content_read,
    mark_content_unread as persist_content_unread, mark_contents_read,
    remove_content_from_knowledge as persist_knowledge_removal,
    reset_saved_content_for_reprocessing, save_content_to_knowledge as persist_knowledge_save,
};
use newsly_queue::{EnqueueRequest, QueueKernel, TaskType};
use serde_json::{Map, Value};

use crate::auth::AuthenticatedUser;
use crate::error::ApiError;
use crate::gateway::RouteOwnershipStamp;
use crate::write_support::{
    bad_request, decode_json, internal_error, not_found, require_operation, verify_stamp,
};
use crate::{AppState, request_id_from_headers};

const MARK_READ_OPERATION_ID: &str = "markContentRead";
const MARK_UNREAD_OPERATION_ID: &str = "markContentUnread";
const BULK_MARK_READ_OPERATION_ID: &str = "bulkContentMarkRead";
const SAVE_KNOWLEDGE_OPERATION_ID: &str = "saveContentToKnowledge";
const REMOVE_KNOWLEDGE_OPERATION_ID: &str = "removeContentFromKnowledge";
const REPROCESS_KNOWLEDGE_OPERATION_ID: &str = "reprocessKnowledgeContent";

pub(super) fn router() -> Router<AppState> {
    Router::new()
        .route(
            "/api/content/{content_id}/mark-read",
            post(mark_content_read),
        )
        .route(
            "/api/content/{content_id}/mark-unread",
            delete(mark_content_unread),
        )
        .route("/api/content/bulk-mark-read", post(bulk_mark_read))
        .route(
            "/api/content/{content_id}/knowledge",
            post(save_to_knowledge).delete(remove_from_knowledge),
        )
        .route(
            "/api/content/{content_id}/knowledge/reprocess",
            post(reprocess_knowledge_content),
        )
}

#[utoipa::path(
    post,
    path = "/api/content/{content_id}/mark-read",
    operation_id = "markContentRead",
    tag = "content",
    params(("content_id" = i64, Path, description = "Content ID")),
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, description = "Content marked as read", body = MarkReadResponse),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 404, description = "Content not found", body = newsly_contracts::ErrorEnvelope),
        (status = 409, description = "Stale runtime owner", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn mark_content_read(
    State(state): State<AppState>,
    Path(content_id): Path<i64>,
    headers: HeaderMap,
    current_user: AuthenticatedUser,
    Extension(stamp): Extension<RouteOwnershipStamp>,
) -> Result<Json<MarkReadResponse>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    require_operation(&stamp, MARK_READ_OPERATION_ID, &request_id)?;
    validate_content_id(content_id, &request_id)?;
    let mut transaction = begin_write(&state, &request_id).await?;
    verify_stamp(&mut transaction, &stamp, &request_id).await?;
    require_content(&mut transaction, content_id, &request_id).await?;
    persist_content_read(&mut transaction, current_user.id, content_id)
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    commit_write(transaction, &request_id).await?;
    Ok(Json(MarkReadResponse {
        status: OperationStatus::Success,
        content_id,
    }))
}

#[utoipa::path(
    delete,
    path = "/api/content/{content_id}/mark-unread",
    operation_id = "markContentUnread",
    tag = "content",
    params(("content_id" = i64, Path, description = "Content ID")),
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, description = "Content marked as unread", body = MarkUnreadResponse),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 404, description = "Content not found", body = newsly_contracts::ErrorEnvelope),
        (status = 409, description = "Stale runtime owner", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn mark_content_unread(
    State(state): State<AppState>,
    Path(content_id): Path<i64>,
    headers: HeaderMap,
    current_user: AuthenticatedUser,
    Extension(stamp): Extension<RouteOwnershipStamp>,
) -> Result<Json<MarkUnreadResponse>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    require_operation(&stamp, MARK_UNREAD_OPERATION_ID, &request_id)?;
    validate_content_id(content_id, &request_id)?;
    let mut transaction = begin_write(&state, &request_id).await?;
    verify_stamp(&mut transaction, &stamp, &request_id).await?;
    require_content(&mut transaction, content_id, &request_id).await?;
    let removed_records = persist_content_unread(&mut transaction, current_user.id, content_id)
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    commit_write(transaction, &request_id).await?;
    Ok(Json(MarkUnreadResponse {
        status: OperationStatus::Success,
        content_id,
        removed_records,
    }))
}

#[utoipa::path(
    post,
    path = "/api/content/bulk-mark-read",
    operation_id = "bulkContentMarkRead",
    tag = "content",
    request_body = BulkMarkReadRequest,
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, description = "Content items marked as read", body = BulkMarkReadResponse),
        (status = 400, description = "Invalid content IDs", body = newsly_contracts::ErrorEnvelope),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 409, description = "Stale runtime owner", body = newsly_contracts::ErrorEnvelope),
        (status = 422, description = "Validation Error", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn bulk_mark_read(
    State(state): State<AppState>,
    headers: HeaderMap,
    current_user: AuthenticatedUser,
    Extension(stamp): Extension<RouteOwnershipStamp>,
    payload: Result<Json<BulkMarkReadRequest>, JsonRejection>,
) -> Result<Json<BulkMarkReadResponse>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    require_operation(&stamp, BULK_MARK_READ_OPERATION_ID, &request_id)?;
    let Json(payload) = decode_json(payload, &request_id)?;
    if payload.content_ids.is_empty() {
        return Err(ApiError::new(
            StatusCode::UNPROCESSABLE_ENTITY,
            "validation_error",
            "content_ids must contain at least one item",
            request_id,
        ));
    }
    if payload
        .content_ids
        .iter()
        .any(|content_id| *content_id <= 0)
    {
        return Err(bad_request(
            "content_ids must contain positive integers",
            &request_id,
        ));
    }
    let total_requested = payload.content_ids.len();
    let mut transaction = begin_write(&state, &request_id).await?;
    verify_stamp(&mut transaction, &stamp, &request_id).await?;
    let result = mark_contents_read(&mut transaction, current_user.id, &payload.content_ids)
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    if !result.failed_ids.is_empty() {
        return Err(bad_request(
            format!("Invalid content IDs: {:?}", result.failed_ids),
            &request_id,
        ));
    }
    commit_write(transaction, &request_id).await?;
    Ok(Json(BulkMarkReadResponse {
        status: OperationStatus::Success,
        marked_count: result.marked_count,
        failed_ids: result.failed_ids,
        total_requested,
    }))
}

#[utoipa::path(
    post,
    path = "/api/content/{content_id}/knowledge",
    operation_id = "saveContentToKnowledge",
    tag = "content",
    params(("content_id" = i64, Path, description = "Content ID")),
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, description = "Content saved to Knowledge", body = KnowledgeMutationResponse),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 404, description = "Content not found", body = newsly_contracts::ErrorEnvelope),
        (status = 409, description = "Stale runtime owner", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn save_to_knowledge(
    State(state): State<AppState>,
    Path(content_id): Path<i64>,
    headers: HeaderMap,
    current_user: AuthenticatedUser,
    Extension(stamp): Extension<RouteOwnershipStamp>,
) -> Result<Json<KnowledgeMutationResponse>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    require_operation(&stamp, SAVE_KNOWLEDGE_OPERATION_ID, &request_id)?;
    validate_content_id(content_id, &request_id)?;
    let mut transaction = begin_write(&state, &request_id).await?;
    verify_stamp(&mut transaction, &stamp, &request_id).await?;
    require_content(&mut transaction, content_id, &request_id).await?;
    persist_knowledge_save(&mut transaction, current_user.id, content_id)
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    commit_write(transaction, &request_id).await?;
    Ok(Json(KnowledgeMutationResponse {
        status: KnowledgeMutationStatus::Success,
        content_id,
        is_saved_to_knowledge: true,
        message: "Saved to knowledge".to_owned(),
    }))
}

#[utoipa::path(
    delete,
    path = "/api/content/{content_id}/knowledge",
    operation_id = "removeContentFromKnowledge",
    tag = "content",
    params(("content_id" = i64, Path, description = "Content ID")),
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, description = "Content removed from Knowledge", body = KnowledgeMutationResponse),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 404, description = "Content not found", body = newsly_contracts::ErrorEnvelope),
        (status = 409, description = "Stale runtime owner", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn remove_from_knowledge(
    State(state): State<AppState>,
    Path(content_id): Path<i64>,
    headers: HeaderMap,
    current_user: AuthenticatedUser,
    Extension(stamp): Extension<RouteOwnershipStamp>,
) -> Result<Json<KnowledgeMutationResponse>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    require_operation(&stamp, REMOVE_KNOWLEDGE_OPERATION_ID, &request_id)?;
    validate_content_id(content_id, &request_id)?;
    let mut transaction = begin_write(&state, &request_id).await?;
    verify_stamp(&mut transaction, &stamp, &request_id).await?;
    require_content(&mut transaction, content_id, &request_id).await?;
    let removed = persist_knowledge_removal(&mut transaction, current_user.id, content_id)
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    commit_write(transaction, &request_id).await?;
    Ok(Json(KnowledgeMutationResponse {
        status: if removed {
            KnowledgeMutationStatus::Success
        } else {
            KnowledgeMutationStatus::NotFound
        },
        content_id,
        is_saved_to_knowledge: false,
        message: if removed {
            "Removed from knowledge"
        } else {
            "Content was not saved to knowledge"
        }
        .to_owned(),
    }))
}

#[utoipa::path(
    post,
    path = "/api/content/{content_id}/knowledge/reprocess",
    operation_id = "reprocessKnowledgeContent",
    tag = "content",
    params(("content_id" = i64, Path, description = "Content ID")),
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, description = "Saved item queued for full reprocessing", body = KnowledgeMutationResponse),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 404, description = "Content not saved to Knowledge", body = newsly_contracts::ErrorEnvelope),
        (status = 409, description = "Content already ready or stale runtime owner", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn reprocess_knowledge_content(
    State(state): State<AppState>,
    Path(content_id): Path<i64>,
    headers: HeaderMap,
    current_user: AuthenticatedUser,
    Extension(stamp): Extension<RouteOwnershipStamp>,
) -> Result<Json<KnowledgeMutationResponse>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    require_operation(&stamp, REPROCESS_KNOWLEDGE_OPERATION_ID, &request_id)?;
    validate_content_id(content_id, &request_id)?;
    let mut transaction = begin_write(&state, &request_id).await?;
    verify_stamp(&mut transaction, &stamp, &request_id).await?;
    let outcome =
        reset_saved_content_for_reprocessing(&mut transaction, current_user.id, content_id)
            .await
            .map_err(|error| internal_error(error, &request_id))?;
    let message = match outcome {
        KnowledgeReprocessOutcome::NotSaved => {
            return Err(not_found("Saved content", &request_id));
        }
        KnowledgeReprocessOutcome::AlreadyReady => {
            return Err(ApiError::new(
                StatusCode::CONFLICT,
                "content_ready",
                "Content is already ready",
                request_id,
            ));
        }
        KnowledgeReprocessOutcome::AlreadyActive => "Already preparing",
        KnowledgeReprocessOutcome::Reset => {
            QueueKernel::new(state.database.pool().clone())
                .enqueue_many_in_transaction(
                    &mut transaction,
                    vec![analyze_request(content_id, current_user.id)],
                )
                .await
                .map_err(|error| internal_error(error, &request_id))?;
            tracing::info!(
                content_id,
                user_id = current_user.id,
                "saved content reprocess queued"
            );
            "Reprocessing started"
        }
    };
    commit_write(transaction, &request_id).await?;
    Ok(Json(KnowledgeMutationResponse {
        status: KnowledgeMutationStatus::Success,
        content_id,
        is_saved_to_knowledge: true,
        message: message.to_owned(),
    }))
}

/// `analyze_url` is the head of every content pipeline, so it re-derives type and platform and
/// fans out to extraction, media, summarization, and image tasks as it would for a new save.
fn analyze_request(content_id: i64, user_id: i64) -> EnqueueRequest {
    let mut request = EnqueueRequest::new(TaskType::AnalyzeUrl);
    request.content_id = Some(content_id);
    request.payload = Some(Map::from_iter([(
        "content_id".to_owned(),
        Value::from(content_id),
    )]));
    request.dedupe = Some(true);
    request.access_user_id = Some(user_id);
    request
}

fn validate_content_id(content_id: i64, request_id: &str) -> Result<(), ApiError> {
    if content_id <= 0 || i32::try_from(content_id).is_err() {
        Err(bad_request(
            "content_id must be a positive 32-bit integer",
            request_id,
        ))
    } else {
        Ok(())
    }
}

async fn require_content(
    transaction: &mut sqlx::Transaction<'_, sqlx::Postgres>,
    content_id: i64,
    request_id: &str,
) -> Result<(), ApiError> {
    if content_exists(transaction, content_id)
        .await
        .map_err(|error| internal_error(error, request_id))?
    {
        Ok(())
    } else {
        Err(not_found("Content", request_id))
    }
}

async fn begin_write<'a>(
    state: &'a AppState,
    request_id: &str,
) -> Result<sqlx::Transaction<'a, sqlx::Postgres>, ApiError> {
    state
        .database
        .pool()
        .begin()
        .await
        .map_err(|error| internal_error(error, request_id))
}

async fn commit_write(
    transaction: sqlx::Transaction<'_, sqlx::Postgres>,
    request_id: &str,
) -> Result<(), ApiError> {
    transaction
        .commit()
        .await
        .map_err(|error| internal_error(error, request_id))
}
