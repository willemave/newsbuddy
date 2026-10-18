use crate::{
    AppState,
    auth::AuthenticatedUser,
    error::ApiError,
    gateway::RouteOwnershipStamp,
    request_id_from_headers,
    write_support::{decode_json, internal_error},
};
use axum::{
    Json, Router,
    extract::{State, rejection::JsonRejection},
    http::{HeaderMap, StatusCode},
    routing::get,
};
use newsly_contracts::{NewsFilterPreference, UpdateNewsFilterRequest};
use newsly_db::news_filter_preferences::{
    self as repository, NewsFilterPreferenceError, NewsFilterPreferenceRecord,
};
use newsly_queue::{EnqueueRequest, QueueKernel, TaskType};
use serde_json::json;

pub(super) fn router() -> Router<AppState> {
    Router::new().route("/api/briefing/filter", get(load).put(save))
}

#[utoipa::path(get, path = "/api/briefing/filter", operation_id = "getNewsFilter", tag = "briefing", security(("HTTPBearer" = [])), responses((status = 200, description = "Current News filter", body = NewsFilterPreference), (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope), (status = 500, description = "Internal error", body = newsly_contracts::ErrorEnvelope)))]
pub(super) async fn load(
    State(state): State<AppState>,
    headers: HeaderMap,
    user: AuthenticatedUser,
) -> Result<Json<NewsFilterPreference>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    let mut connection = state
        .database
        .pool()
        .acquire()
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    let current = repository::load_current(&mut connection, user.id)
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    Ok(Json(present(current)))
}

#[utoipa::path(put, path = "/api/briefing/filter", operation_id = "updateNewsFilter", tag = "briefing", request_body = UpdateNewsFilterRequest, security(("HTTPBearer" = [])), responses((status = 200, description = "Saved News filter", body = NewsFilterPreference), (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope), (status = 409, description = "Revision conflict", body = newsly_contracts::ErrorEnvelope), (status = 422, description = "Validation error", body = newsly_contracts::ErrorEnvelope), (status = 500, description = "Internal error", body = newsly_contracts::ErrorEnvelope)))]
pub(super) async fn save(
    State(state): State<AppState>,
    headers: HeaderMap,
    user: AuthenticatedUser,
    stamp: RouteOwnershipStamp,
    payload: Result<Json<UpdateNewsFilterRequest>, JsonRejection>,
) -> Result<Json<NewsFilterPreference>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    let Json(payload) = decode_json(payload, &request_id)?;
    if payload.expected_revision < 0 {
        return Err(ApiError::validation(
            "expected_revision must be nonnegative",
            &request_id,
        ));
    }
    let mut transaction =
        crate::write_support::begin_fenced(state.database.pool(), &stamp, &request_id).await?;
    let saved = repository::save(
        &mut transaction,
        user.id,
        payload.expected_revision,
        &payload.text,
        payload.enabled,
    )
    .await
    .map_err(|error| match error {
        NewsFilterPreferenceError::Conflict => ApiError::new(
            StatusCode::CONFLICT,
            "news_filter_revision_conflict",
            "News filter changed; reload before saving",
            &request_id,
        ),
        NewsFilterPreferenceError::TextTooLong | NewsFilterPreferenceError::ActivationTooLong => {
            ApiError::validation(error.to_string(), &request_id)
        }
        other => internal_error(other, &request_id),
    })?;
    let available_at = chrono::Utc::now();
    let mut request = EnqueueRequest::new(TaskType::BriefingRefresh);
    request.payload = json!({"user_id":user.id,"mode":"append"})
        .as_object()
        .cloned();
    request.owner_user_id = Some(user.id);
    request.dedupe_key = Some(format!("briefing_refresh:{}:append", user.id));
    request.available_at = Some(available_at);
    let batch = QueueKernel::new(state.database.pool().clone())
        .enqueue_many_in_transaction(&mut transaction, vec![request])
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    if let Some(task_id) = batch
        .task_ids
        .first()
        .filter(|id| !batch.inserted_task_ids.contains(id))
    {
        newsly_db::expedite_pending_briefing_refresh(&mut transaction, *task_id, available_at)
            .await
            .map_err(|error| internal_error(error, &request_id))?;
    }
    crate::write_support::commit(transaction, &request_id).await?;
    Ok(Json(present(saved)))
}

fn present(record: NewsFilterPreferenceRecord) -> NewsFilterPreference {
    NewsFilterPreference {
        revision: record.revision,
        text: record.text,
        enabled: record.enabled,
    }
}
