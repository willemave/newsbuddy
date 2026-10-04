use axum::extract::State;
use axum::extract::rejection::JsonRejection;
use axum::http::HeaderMap;
use axum::routing::post;
use axum::{Json, Router};
use newsly_contracts::{BulkMarkReadRequest, BulkMarkReadResponse, OperationStatus};
use newsly_db::mark_visible_news_items_read;

use crate::auth::AuthenticatedUser;
use crate::error::ApiError;
use crate::gateway::RouteOwnershipStamp;
use crate::write_support::{decode_json, internal_error};
use crate::{AppState, request_id_from_headers};

pub(super) fn router() -> Router<AppState> {
    Router::new().route("/api/news/items/mark-read", post(mark_news_items_read))
}

#[utoipa::path(
    post,
    path = "/api/news/items/mark-read",
    operation_id = "markNewsItemsRead",
    tag = "news",
    request_body = BulkMarkReadRequest,
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, description = "Visible news items marked read", body = BulkMarkReadResponse),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 409, description = "Stale runtime owner", body = newsly_contracts::ErrorEnvelope),
        (status = 422, description = "Validation Error", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn mark_news_items_read(
    State(state): State<AppState>,
    headers: HeaderMap,
    current_user: AuthenticatedUser,
    stamp: RouteOwnershipStamp,
    payload: Result<Json<BulkMarkReadRequest>, JsonRejection>,
) -> Result<Json<BulkMarkReadResponse>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    let Json(payload) = decode_json(payload, &request_id)?;
    if payload.content_ids.is_empty() {
        return Err(validation_error(
            "content_ids must contain at least one item",
            &request_id,
        ));
    }

    let total_requested = payload.content_ids.len();
    let mut transaction =
        crate::write_support::begin_fenced(state.database.pool(), &stamp, &request_id).await?;
    let result =
        mark_visible_news_items_read(&mut transaction, current_user.id, &payload.content_ids)
            .await
            .map_err(|error| internal_error(error, &request_id))?;
    crate::write_support::commit(transaction, &request_id).await?;

    Ok(Json(BulkMarkReadResponse {
        status: OperationStatus::Success,
        marked_count: result.marked_count,
        failed_ids: result.failed_ids,
        total_requested,
    }))
}

fn validation_error(message: impl Into<String>, request_id: &str) -> ApiError {
    ApiError::validation(message, request_id)
}
