use axum::Json;
use axum::extract::Path;
use axum::extract::rejection::PathRejection;
use axum::http::{HeaderMap, StatusCode};
use chrono::Utc;
use newsly_contracts::{
    LearningDeckResponse, LearningDeckRunResponse, LearningDeckRunStatus, LearningDeckSourceKind,
    LearningDeckStatus, LearningDeckTimelineEntry, LearningDeckUrlResponse,
};
use newsly_db::{
    LearningDeckAttemptProjection, LearningDeckProjection, get_learning_deck as load_learning_deck,
};

use crate::error::ApiError;
use crate::gateway::RouteOwnershipStamp;
use crate::learning_deck_tokens::LearningDeckTokenSigner;
use crate::write_support::{internal_error, require_operation};
use crate::{AppState, request_id_from_headers};

use super::support::{deck_error, external_url, repository_error, token_signer, valid_deck_id};

pub(super) enum PrivateDeckTarget {
    Viewer,
    SourceNotes,
}

#[allow(clippy::too_many_arguments)]
pub(super) async fn private_deck_url(
    state: &AppState,
    headers: &HeaderMap,
    user_id: i64,
    stamp: &RouteOwnershipStamp,
    path: Result<Path<i64>, PathRejection>,
    operation_id: &str,
    target: PrivateDeckTarget,
) -> Result<Json<LearningDeckUrlResponse>, ApiError> {
    let request_id = request_id_from_headers(headers);
    require_operation(stamp, operation_id, &request_id)?;
    let deck_id = valid_deck_id(path, &request_id)?;
    let deck = load_learning_deck(state.database.pool(), user_id, deck_id)
        .await
        .map_err(|error| repository_error(error, &request_id))?
        .ok_or_else(|| {
            deck_error(
                StatusCode::NOT_FOUND,
                "Learning Deck not found",
                &request_id,
            )
        })?;
    if deck.latest_successful_attempt_id.is_none() {
        return Err(deck_error(
            StatusCode::CONFLICT,
            "Learning Deck is not ready",
            &request_id,
        ));
    }
    let signed = token_signer(&request_id)?
        .private_token(deck.id, user_id, Utc::now())
        .map_err(|error| internal_error(error, &request_id))?;
    let suffix = match target {
        PrivateDeckTarget::Viewer => "/",
        PrivateDeckTarget::SourceNotes => "/source-notes",
    };
    Ok(Json(LearningDeckUrlResponse {
        url: external_url(
            headers,
            &format!("/learning/signed/{}{suffix}", signed.token),
            &request_id,
        )?,
        expires_at: Some(signed.expires_at),
    }))
}

pub(super) async fn load_and_present_deck(
    state: &AppState,
    user_id: i64,
    deck_id: i64,
    request_id: &str,
) -> Result<LearningDeckResponse, ApiError> {
    let projection = load_learning_deck(state.database.pool(), user_id, deck_id)
        .await
        .map_err(|error| repository_error(error, request_id))?
        .ok_or_else(|| deck_error(StatusCode::NOT_FOUND, "Learning Deck not found", request_id))?;
    present_deck(projection, &token_signer(request_id)?, request_id)
}

pub(super) fn present_deck(
    deck: LearningDeckProjection,
    signer: &LearningDeckTokenSigner,
    request_id: &str,
) -> Result<LearningDeckResponse, ApiError> {
    let source_kind = LearningDeckSourceKind::try_from(deck.source_kind.as_str())
        .map_err(|error| internal_error(error, request_id))?;
    let status = deck_status(&deck, request_id)?;
    let latest_run = deck
        .latest_attempt
        .as_ref()
        .map(|attempt| present_attempt(attempt, request_id))
        .transpose()?;
    let thumbnail_url = if deck.thumbnail_available {
        let access_token = signer
            .private_token(deck.id, deck.user_id, Utc::now())
            .map_err(|error| internal_error(error, request_id))?;
        Some(format!(
            "/learning/signed/{}/assets/thumbnail.png",
            access_token.token
        ))
    } else {
        None
    };
    Ok(LearningDeckResponse {
        id: deck.id,
        title: deck.title.clone(),
        source_kind,
        source_url: deck.source_url,
        source_content_id: deck.source_content_id,
        source_title: Some(deck.title),
        source_metadata: deck.source_metadata,
        status,
        share_enabled: deck.share_enabled,
        viewer_available: deck.viewer_available,
        source_notes_available: deck.source_notes_available,
        thumbnail_url,
        latest_successful_run_id: deck.latest_successful_attempt_id,
        latest_run,
        created_at: deck.created_at,
        updated_at: deck.updated_at,
    })
}

fn deck_status(
    deck: &LearningDeckProjection,
    request_id: &str,
) -> Result<Option<LearningDeckStatus>, ApiError> {
    let mut status = deck
        .latest_successful_attempt_id
        .map(|_| LearningDeckStatus::Ready);
    if let Some(attempt) = &deck.latest_attempt
        && attempt.status != "completed"
    {
        status = Some(
            LearningDeckRunStatus::from_attempt_status(&attempt.status)
                .map(LearningDeckStatus::from)
                .map_err(|error| internal_error(error, request_id))?,
        );
    }
    Ok(status)
}

fn present_attempt(
    attempt: &LearningDeckAttemptProjection,
    request_id: &str,
) -> Result<LearningDeckRunResponse, ApiError> {
    let status = LearningDeckRunStatus::from_attempt_status(&attempt.status)
        .map_err(|error| internal_error(error, request_id))?;
    let timeline = attempt
        .timeline
        .iter()
        .map(|entry| {
            Ok(LearningDeckTimelineEntry {
                status: LearningDeckRunStatus::from_attempt_status(&entry.status)
                    .map_err(|error| internal_error(error, request_id))?,
                note: entry.note.clone(),
                created_at: entry.created_at,
            })
        })
        .collect::<Result<Vec<_>, ApiError>>()?;
    Ok(LearningDeckRunResponse {
        id: attempt.id,
        status,
        interests_prompt: attempt.interests_prompt.clone(),
        timeline,
        error_message: attempt.public_error_message(),
        started_at: attempt.started_at,
        completed_at: attempt.completed_at,
        created_at: attempt.created_at,
        updated_at: attempt.updated_at,
    })
}
