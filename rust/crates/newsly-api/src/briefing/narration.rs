//! Narration commands and observation keep immutable edition identity separate from lens selection.

use axum::Json;
use axum::extract::rejection::{JsonRejection, PathRejection, QueryRejection};
use axum::extract::{Extension, Path, Query, State};
use axum::http::{HeaderMap, StatusCode};
use newsly_contracts::{
    AudioEpisodeResponse, BriefingNarrationRequest, BriefingNarrationResponse,
    BriefingNarrationScope, LegacyBriefingNarrationRequest,
};
use newsly_db::{
    AudioEpisodeProjection, BriefingNarrationSelection, PrepareNarrationOutcome,
    load_briefing_narration, prepare_briefing_narration, retry_briefing_narration,
};
use newsly_queue::{EnqueueRequest, QueueKernel, TaskType};
use serde_json::json;

use super::presentation::{present_audio_episode, present_narration};
use super::{NarrationDeliveryQuery, queue_error, validation_error};
use crate::auth::AuthenticatedUser;
use crate::error::ApiError;
use crate::gateway::RouteOwnershipStamp;
use crate::write_support::{
    bad_request, decode_json, internal_error, not_found, require_operation, verify_stamp,
};
use crate::{AppState, request_id_from_headers};

#[cfg(test)]
#[path = "narration_tests.rs"]
mod tests;

const LEGACY_NARRATION_OPERATION_ID: &str = "narrationBriefing";
const NARRATION_OPERATION_ID: &str = "chapteredBriefingNarration";
const RETRY_OPERATION_ID: &str = "retryBriefingNarration";

#[utoipa::path(
    post,
    path = "/api/briefing/narration",
    operation_id = "narrationBriefing",
    tag = "briefing",
    request_body = LegacyBriefingNarrationRequest,
    params(("delivery" = Option<String>, Query, description = "background or stream")),
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, description = "Successful Response", body = AudioEpisodeResponse),
        (status = 400, description = "No narration available", body = newsly_contracts::ErrorEnvelope),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 404, description = "Briefing Lens not found", body = newsly_contracts::ErrorEnvelope),
        (status = 409, description = "Stale runtime owner", body = newsly_contracts::ErrorEnvelope),
        (status = 422, description = "Validation Error", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(crate) async fn legacy_narration(
    State(state): State<AppState>,
    headers: HeaderMap,
    current_user: AuthenticatedUser,
    Extension(stamp): Extension<RouteOwnershipStamp>,
    query: Result<Query<NarrationDeliveryQuery>, QueryRejection>,
    payload: Result<Json<LegacyBriefingNarrationRequest>, JsonRejection>,
) -> Result<Json<AudioEpisodeResponse>, ApiError> {
    let payload = payload.map(|Json(payload)| {
        Json(BriefingNarrationRequest {
            scope: None,
            lens_key: Some(payload.lens_key),
        })
    });
    let episodes = create_narration(
        &state,
        &headers,
        current_user.id,
        &stamp,
        LEGACY_NARRATION_OPERATION_ID,
        false,
        query,
        payload,
    )
    .await?;
    let episode = episodes.into_iter().next().ok_or_else(|| {
        internal_error(
            "legacy narration has no episode",
            &request_id_from_headers(&headers),
        )
    })?;
    present_audio_episode(episode, &request_id_from_headers(&headers)).map(Json)
}

#[utoipa::path(
    post,
    path = "/api/briefing/narrations",
    operation_id = "chapteredBriefingNarration",
    tag = "briefing",
    request_body = BriefingNarrationRequest,
    params(("delivery" = Option<String>, Query, description = "background or stream")),
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, description = "Successful Response", body = BriefingNarrationResponse),
        (status = 400, description = "No narration available", body = newsly_contracts::ErrorEnvelope),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 404, description = "Briefing Lens not found", body = newsly_contracts::ErrorEnvelope),
        (status = 409, description = "Stale runtime owner", body = newsly_contracts::ErrorEnvelope),
        (status = 422, description = "Validation Error", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(crate) async fn chaptered_narration(
    State(state): State<AppState>,
    headers: HeaderMap,
    current_user: AuthenticatedUser,
    Extension(stamp): Extension<RouteOwnershipStamp>,
    query: Result<Query<NarrationDeliveryQuery>, QueryRejection>,
    payload: Result<Json<BriefingNarrationRequest>, JsonRejection>,
) -> Result<Json<BriefingNarrationResponse>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    let episodes = create_narration(
        &state,
        &headers,
        current_user.id,
        &stamp,
        NARRATION_OPERATION_ID,
        true,
        query,
        payload,
    )
    .await?;
    present_narration(episodes, &request_id).map(Json)
}

#[utoipa::path(
    get,
    path = "/api/briefing/narrations/{episode_group_id}",
    operation_id = "narrationBriefingStatus",
    tag = "briefing",
    params(("episode_group_id" = String, Path, description = "Narration group ID")),
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, description = "Successful Response", body = BriefingNarrationResponse),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 404, description = "Briefing narration not found", body = newsly_contracts::ErrorEnvelope),
        (status = 422, description = "Validation Error", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(crate) async fn narration_status(
    State(state): State<AppState>,
    headers: HeaderMap,
    path: Result<Path<String>, PathRejection>,
    current_user: AuthenticatedUser,
) -> Result<Json<BriefingNarrationResponse>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    let Path(group_id) = path.map_err(|error| validation_error(error.body_text(), &request_id))?;
    let episodes = load_briefing_narration(state.database.pool(), current_user.id, &group_id)
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    if episodes.is_empty() {
        return Err(not_found("Briefing narration", &request_id));
    }
    present_narration(episodes, &request_id).map(Json)
}

fn narration_selection(
    payload: BriefingNarrationRequest,
    chaptered: bool,
) -> Option<BriefingNarrationSelection> {
    match (payload.scope, payload.lens_key) {
        (scope, Some(key)) if !key.trim().is_empty() && key.chars().count() <= 64 => match scope {
            Some(BriefingNarrationScope::Lens) if chaptered => {
                Some(BriefingNarrationSelection::AdaptedLens(key))
            }
            None => Some(BriefingNarrationSelection::Lens(key)),
            _ => None,
        },
        (Some(scope), None) if chaptered => match scope {
            BriefingNarrationScope::ArticleTier => Some(BriefingNarrationSelection::ArticleTier),
            BriefingNarrationScope::PodcastTier => Some(BriefingNarrationSelection::PodcastTier),
            BriefingNarrationScope::NewsProgram => Some(BriefingNarrationSelection::NewsProgram),
            BriefingNarrationScope::Lens => None,
        },
        _ => None,
    }
}

#[allow(clippy::too_many_arguments)]
async fn create_narration(
    state: &AppState,
    headers: &HeaderMap,
    user_id: i64,
    stamp: &RouteOwnershipStamp,
    operation_id: &str,
    chaptered: bool,
    query: Result<Query<NarrationDeliveryQuery>, QueryRejection>,
    payload: Result<Json<BriefingNarrationRequest>, JsonRejection>,
) -> Result<Vec<AudioEpisodeProjection>, ApiError> {
    let request_id = request_id_from_headers(headers);
    require_operation(stamp, operation_id, &request_id)?;
    let Query(query) = query.map_err(|error| validation_error(error.body_text(), &request_id))?;
    if !matches!(query.delivery.as_str(), "background" | "stream") {
        return Err(validation_error(
            "delivery must be background or stream",
            &request_id,
        ));
    }
    let Json(payload) = decode_json(payload, &request_id)?;
    let selection = narration_selection(payload, chaptered).ok_or_else(|| {
        validation_error(
            "provide scope lens with lens_key, a tier scope without lens_key, or a legacy lens_key",
            &request_id,
        )
    })?;
    let mut transaction = state
        .database
        .pool()
        .begin()
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    verify_stamp(&mut transaction, stamp, &request_id).await?;
    let episodes =
        match prepare_briefing_narration(&mut transaction, user_id, &selection, chaptered)
            .await
            .map_err(|error| internal_error(error, &request_id))?
        {
            PrepareNarrationOutcome::Ready(episodes) => episodes,
            PrepareNarrationOutcome::LensNotFound => {
                return Err(not_found("Briefing lens", &request_id));
            }
            PrepareNarrationOutcome::Empty => {
                if matches!(selection, BriefingNarrationSelection::AdaptedLens(_)) {
                    return Err(ApiError::new(
                        StatusCode::BAD_REQUEST,
                        "briefing_narration_empty",
                        "No unread sources are available for audio in this lens",
                        &request_id,
                    ));
                }
                return Err(bad_request(
                    "No briefing narration is available",
                    &request_id,
                ));
            }
        };
    enqueue_narration(
        state.database.pool(),
        &mut transaction,
        &episodes,
        user_id,
        &request_id,
    )
    .await?;
    transaction
        .commit()
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    Ok(episodes)
}

async fn enqueue_narration(
    pool: &sqlx::PgPool,
    transaction: &mut sqlx::Transaction<'_, sqlx::Postgres>,
    episodes: &[AudioEpisodeProjection],
    user_id: i64,
    request_id: &str,
) -> Result<(), ApiError> {
    let requests = episodes
        .iter()
        .filter(|episode| episode.status != "completed")
        .map(|episode| {
            let mut request = EnqueueRequest::new(TaskType::GenerateAudioEpisode);
            request.payload = Some(
                json!({"audio_episode_id": episode.id, "user_id": user_id})
                    .as_object()
                    .expect("audio episode payload is an object")
                    .clone(),
            );
            request.dedupe_key = Some(format!("audio_episode:{}", episode.id));
            request.owner_user_id = Some(user_id);
            request
        })
        .collect::<Vec<_>>();
    if !requests.is_empty() {
        QueueKernel::new(pool.clone())
            .enqueue_many_in_transaction(transaction, requests)
            .await
            .map_err(|error| queue_error(error, request_id))?;
    }
    Ok(())
}

#[utoipa::path(
    post,
    path = "/api/briefing/narrations/{episode_group_id}/retry",
    operation_id = "retryBriefingNarration",
    tag = "briefing",
    params(("episode_group_id" = String, Path, description = "Existing narration group ID")),
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, description = "Original narration with failed chapters queued", body = BriefingNarrationResponse),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 404, description = "Briefing narration not found", body = newsly_contracts::ErrorEnvelope),
        (status = 409, description = "Stale runtime owner", body = newsly_contracts::ErrorEnvelope),
        (status = 422, description = "Validation Error", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(crate) async fn retry_narration(
    State(state): State<AppState>,
    headers: HeaderMap,
    path: Result<Path<String>, PathRejection>,
    current_user: AuthenticatedUser,
    Extension(stamp): Extension<RouteOwnershipStamp>,
) -> Result<Json<BriefingNarrationResponse>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    require_operation(&stamp, RETRY_OPERATION_ID, &request_id)?;
    let Path(group_id) = path.map_err(|error| validation_error(error.body_text(), &request_id))?;
    let mut transaction = state
        .database
        .pool()
        .begin()
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    verify_stamp(&mut transaction, &stamp, &request_id).await?;
    let episodes = retry_briefing_narration(&mut transaction, current_user.id, &group_id)
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    if episodes.is_empty() {
        return Err(not_found("Briefing narration", &request_id));
    }
    enqueue_narration(
        state.database.pool(),
        &mut transaction,
        &episodes,
        current_user.id,
        &request_id,
    )
    .await?;
    transaction
        .commit()
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    present_narration(episodes, &request_id).map(Json)
}
