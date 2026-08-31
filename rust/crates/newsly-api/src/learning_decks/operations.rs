use axum::Json;
use axum::extract::rejection::{JsonRejection, PathRejection};
use axum::extract::{Extension, Path, State};
use axum::http::{HeaderMap, StatusCode};
use newsly_contracts::{
    LearningDeckCreateRequest, LearningDeckListResponse, LearningDeckResponse,
    LearningDeckShareResponse, LearningDeckUrlResponse,
};
use newsly_db::{
    ContentSourceOutcome, ContentSubmissionInput, CreateLearningDeckOutcome,
    DisableLearningDeckShareOutcome, EnableLearningDeckShareOutcome, RetryLearningDeckOutcome,
    SubmissionTaskResolution, apply_content_submission, convert_news_item_to_learning_deck_source,
    create_or_rerun_learning_deck, delete_learning_deck as persist_deck_deletion,
    disable_learning_deck_share, find_visible_news_item_for_learning_deck,
    list_learning_decks as load_learning_decks, load_submitted_content_learning_deck_source,
    persist_learning_deck_share, prepare_enable_learning_deck_share,
    resolve_content_learning_deck_source, retry_learning_deck as persist_deck_retry,
};
use newsly_queue::{QueueKernel, TaskType};

use crate::auth::AuthenticatedUser;
use crate::error::ApiError;
use crate::gateway::RouteOwnershipStamp;
use crate::learning_deck_artifacts::LearningDeckArtifactStore;
use crate::learning_deck_tokens::{generate_share_nonce, hash_learning_deck_token};
use crate::write_support::{decode_json, internal_error, require_operation, verify_stamp};
use crate::{AppState, request_id_from_headers};

use super::presentation::{
    PrivateDeckTarget, load_and_present_deck, present_deck, private_deck_url,
};
use super::source::{
    CreateSource, ValidatedCreateRequest, github_learning_deck_source, normalize_news_article_url,
    normalize_submitted_url,
};
use super::support::{
    agent_data_sync_request, content_task_request, create_repository_error, deck_error,
    external_url, queue_error, repository_error, run_llm_task_request, sandbox_root,
    submission_error, token_signer, valid_deck_id,
};

const CREATE_OPERATION_ID: &str = "createLearningDeck";
const RETRY_OPERATION_ID: &str = "retryLearningDeck";
const DELETE_OPERATION_ID: &str = "deleteLearningDeck";
const ENABLE_SHARE_OPERATION_ID: &str = "enableLearningDecksShare";
const DISABLE_SHARE_OPERATION_ID: &str = "disableLearningDecksShare";
const VIEWER_URL_OPERATION_ID: &str = "createLearningDecksViewerUrl";
const SOURCE_NOTES_URL_OPERATION_ID: &str = "createLearningDecksSourceNotesUrl";

#[utoipa::path(
    get,
    path = "/api/learning/decks",
    operation_id = "listLearningDecks",
    tag = "learning",
    summary = "List current user's Learning Decks",
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, description = "Successful Response", body = LearningDeckListResponse),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(crate) async fn list_decks(
    State(state): State<AppState>,
    headers: HeaderMap,
    current_user: AuthenticatedUser,
) -> Result<Json<LearningDeckListResponse>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    let signer = token_signer(&request_id)?;
    let decks = load_learning_decks(state.database.pool(), current_user.id)
        .await
        .map_err(|error| repository_error(error, &request_id))?
        .into_iter()
        .map(|deck| present_deck(deck, &signer, &request_id))
        .collect::<Result<Vec<_>, _>>()?;
    Ok(Json(LearningDeckListResponse { decks }))
}

#[utoipa::path(
    post,
    path = "/api/learning/decks",
    operation_id = "createLearningDeck",
    tag = "learning",
    summary = "Create or rerun a Learning Deck",
    request_body = LearningDeckCreateRequest,
    security(("HTTPBearer" = [])),
    responses(
        (status = 202, description = "Successful Response", body = LearningDeckResponse),
        (status = 400, description = "Invalid source", body = newsly_contracts::ErrorEnvelope),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 404, description = "Source not found", body = newsly_contracts::ErrorEnvelope),
        (status = 409, description = "Source or generation state conflict", body = newsly_contracts::ErrorEnvelope),
        (status = 422, description = "Validation Error", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
#[expect(
    clippy::too_many_lines,
    reason = "deck creation keeps source resolution, immutable staging, and enqueue ownership explicit"
)]
pub(crate) async fn create_deck(
    State(state): State<AppState>,
    headers: HeaderMap,
    current_user: AuthenticatedUser,
    Extension(stamp): Extension<RouteOwnershipStamp>,
    payload: Result<Json<LearningDeckCreateRequest>, JsonRejection>,
) -> Result<(StatusCode, Json<LearningDeckResponse>), ApiError> {
    let request_id = request_id_from_headers(&headers);
    require_operation(&stamp, CREATE_OPERATION_ID, &request_id)?;
    let Json(payload) = decode_json(payload, &request_id)?;
    let request = ValidatedCreateRequest::try_from(payload, &request_id)?;
    let sandbox_root = sandbox_root(&request_id)?;
    let queue = QueueKernel::new(state.database.pool().clone());
    let mut transaction = state
        .database
        .pool()
        .begin()
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    verify_stamp(&mut transaction, &stamp, &request_id).await?;

    let (source, mut queue_requests) = match request.source {
        CreateSource::Content(content_id) => {
            let source = match resolve_content_learning_deck_source(
                &mut transaction,
                current_user.id,
                content_id,
            )
            .await
            .map_err(|error| repository_error(error, &request_id))?
            {
                ContentSourceOutcome::Ready(source) => source,
                ContentSourceOutcome::NotFoundOrNotReady => {
                    return Err(deck_error(
                        StatusCode::NOT_FOUND,
                        "Content not found or not ready",
                        &request_id,
                    ));
                }
                ContentSourceOutcome::TextUnavailable => {
                    return Err(deck_error(
                        StatusCode::CONFLICT,
                        "Content source text is not available",
                        &request_id,
                    ));
                }
            };
            (source, Vec::new())
        }
        CreateSource::NewsItem(news_item_id) => {
            let item = find_visible_news_item_for_learning_deck(
                &mut transaction,
                current_user.id,
                news_item_id,
            )
            .await
            .map_err(|error| repository_error(error, &request_id))?
            .ok_or_else(|| deck_error(StatusCode::NOT_FOUND, "Fast Read not found", &request_id))?;
            let article_url = normalize_news_article_url(&item, &request_id)?;
            let converted = convert_news_item_to_learning_deck_source(
                &mut transaction,
                current_user.id,
                &item,
                &article_url,
            )
            .await
            .map_err(|error| repository_error(error, &request_id))?;
            let mut requests = Vec::new();
            if converted.enqueue_process_content {
                requests.push(content_task_request(
                    TaskType::ProcessContent,
                    converted
                        .source
                        .source_content_id
                        .expect("converted source has content id"),
                ));
            }
            if converted.enqueue_agent_data_sync {
                requests.push(
                    agent_data_sync_request(
                        &mut transaction,
                        current_user.id,
                        converted
                            .source
                            .source_content_id
                            .expect("converted source has content id"),
                        &request_id,
                    )
                    .await?,
                );
            }
            (converted.source, requests)
        }
        CreateSource::Url(url) => {
            if let Some(source) = github_learning_deck_source(&url, &request_id)? {
                (source, Vec::new())
            } else {
                let normalized_url = normalize_submitted_url(&url, &request_id)?;
                let applied = apply_content_submission(
                    &mut transaction,
                    &ContentSubmissionInput {
                        url: &normalized_url,
                        title: None,
                        platform: None,
                        instruction: None,
                        crawl_links: false,
                        subscribe_to_feed: false,
                        share_and_chat: false,
                        chat_initial_message: None,
                        save_to_knowledge_and_mark_read: true,
                        user_id: current_user.id,
                        submitted_via: "learning_deck",
                    },
                )
                .await
                .map_err(|error| submission_error(error, &request_id))?;
                let mut requests = Vec::new();
                match applied.task_resolution {
                    SubmissionTaskResolution::None => {}
                    SubmissionTaskResolution::Reuse(task_id) => {
                        queue
                            .grant_access_in_transaction(&mut transaction, task_id, current_user.id)
                            .await
                            .map_err(|error| queue_error(error, &request_id))?;
                    }
                    SubmissionTaskResolution::EnqueueAnalyze => {
                        let mut analyze =
                            content_task_request(TaskType::AnalyzeUrl, applied.content_id);
                        analyze.dedupe = Some(true);
                        analyze.access_user_id = Some(current_user.id);
                        requests.push(analyze);
                    }
                }
                if applied.enqueue_generated_image {
                    requests.push(content_task_request(
                        TaskType::GenerateImage,
                        applied.content_id,
                    ));
                }
                if applied.enqueue_agent_data_sync {
                    requests.push(
                        agent_data_sync_request(
                            &mut transaction,
                            current_user.id,
                            applied.content_id,
                            &request_id,
                        )
                        .await?,
                    );
                }
                let source = load_submitted_content_learning_deck_source(
                    &mut transaction,
                    applied.content_id,
                )
                .await
                .map_err(|error| repository_error(error, &request_id))?;
                (source, requests)
            }
        }
    };

    let outcome = create_or_rerun_learning_deck(
        &mut transaction,
        current_user.id,
        &source,
        request.interests_prompt.as_deref(),
        &sandbox_root,
    )
    .await
    .map_err(|error| create_repository_error(error, &request_id))?;
    let deck_id = match outcome {
        CreateLearningDeckOutcome::AttemptCreated { deck_id, task_id } => {
            queue_requests.push(run_llm_task_request(task_id, current_user.id));
            deck_id
        }
        CreateLearningDeckOutcome::ExistingActiveAttempt { deck_id } => deck_id,
        CreateLearningDeckOutcome::AnotherDeckActive => {
            return Err(deck_error(
                StatusCode::CONFLICT,
                "A Learning Deck is already generating",
                &request_id,
            ));
        }
    };
    if !queue_requests.is_empty() {
        queue
            .enqueue_many_in_transaction(&mut transaction, queue_requests)
            .await
            .map_err(|error| queue_error(error, &request_id))?;
    }
    transaction
        .commit()
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    let response = load_and_present_deck(&state, current_user.id, deck_id, &request_id).await?;
    Ok((StatusCode::ACCEPTED, Json(response)))
}

#[utoipa::path(
    get,
    path = "/api/learning/decks/{deck_id}",
    operation_id = "getLearningDeck",
    tag = "learning",
    summary = "Get one Learning Deck",
    params(("deck_id" = i64, Path, description = "Learning Deck ID")),
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, description = "Successful Response", body = LearningDeckResponse),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 404, description = "Learning Deck not found", body = newsly_contracts::ErrorEnvelope),
        (status = 422, description = "Validation Error", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(crate) async fn get_deck(
    State(state): State<AppState>,
    headers: HeaderMap,
    current_user: AuthenticatedUser,
    path: Result<Path<i64>, PathRejection>,
) -> Result<Json<LearningDeckResponse>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    let deck_id = valid_deck_id(path, &request_id)?;
    Ok(Json(
        load_and_present_deck(&state, current_user.id, deck_id, &request_id).await?,
    ))
}

#[utoipa::path(
    post,
    path = "/api/learning/decks/{deck_id}/retry",
    operation_id = "retryLearningDeck",
    tag = "learning",
    summary = "Retry a failed Learning Deck",
    params(("deck_id" = i64, Path, description = "Learning Deck ID")),
    security(("HTTPBearer" = [])),
    responses(
        (status = 202, description = "Successful Response", body = LearningDeckResponse),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 404, description = "Learning Deck not found", body = newsly_contracts::ErrorEnvelope),
        (status = 409, description = "Learning Deck cannot be retried", body = newsly_contracts::ErrorEnvelope),
        (status = 422, description = "Validation Error", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(crate) async fn retry_deck(
    State(state): State<AppState>,
    headers: HeaderMap,
    current_user: AuthenticatedUser,
    Extension(stamp): Extension<RouteOwnershipStamp>,
    path: Result<Path<i64>, PathRejection>,
) -> Result<(StatusCode, Json<LearningDeckResponse>), ApiError> {
    let request_id = request_id_from_headers(&headers);
    require_operation(&stamp, RETRY_OPERATION_ID, &request_id)?;
    let deck_id = valid_deck_id(path, &request_id)?;
    let sandbox_root = sandbox_root(&request_id)?;
    let mut transaction = state
        .database
        .pool()
        .begin()
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    verify_stamp(&mut transaction, &stamp, &request_id).await?;
    let outcome = persist_deck_retry(&mut transaction, current_user.id, deck_id, &sandbox_root)
        .await
        .map_err(|error| create_repository_error(error, &request_id))?;
    match outcome {
        RetryLearningDeckOutcome::AttemptCreated { task_id, .. } => {
            QueueKernel::new(state.database.pool().clone())
                .enqueue_many_in_transaction(
                    &mut transaction,
                    vec![run_llm_task_request(task_id, current_user.id)],
                )
                .await
                .map_err(|error| queue_error(error, &request_id))?;
        }
        RetryLearningDeckOutcome::ExistingActiveRetry { .. } => {}
        RetryLearningDeckOutcome::DeckNotFound => {
            return Err(deck_error(
                StatusCode::NOT_FOUND,
                "Learning Deck not found",
                &request_id,
            ));
        }
        RetryLearningDeckOutcome::AnotherDeckActive => {
            return Err(deck_error(
                StatusCode::CONFLICT,
                "A Learning Deck is already generating",
                &request_id,
            ));
        }
        RetryLearningDeckOutcome::NoFailedAttempt => {
            return Err(deck_error(
                StatusCode::CONFLICT,
                "Learning Deck does not have a failed attempt",
                &request_id,
            ));
        }
    }
    transaction
        .commit()
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    let response = load_and_present_deck(&state, current_user.id, deck_id, &request_id).await?;
    Ok((StatusCode::ACCEPTED, Json(response)))
}

#[utoipa::path(
    delete,
    path = "/api/learning/decks/{deck_id}",
    operation_id = "deleteLearningDeck",
    tag = "learning",
    summary = "Delete a Learning Deck",
    params(("deck_id" = i64, Path, description = "Learning Deck ID")),
    security(("HTTPBearer" = [])),
    responses(
        (status = 204, description = "Successful Response"),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 404, description = "Learning Deck not found", body = newsly_contracts::ErrorEnvelope),
        (status = 409, description = "Stale runtime owner", body = newsly_contracts::ErrorEnvelope),
        (status = 422, description = "Validation Error", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(crate) async fn delete_deck(
    State(state): State<AppState>,
    headers: HeaderMap,
    current_user: AuthenticatedUser,
    Extension(stamp): Extension<RouteOwnershipStamp>,
    path: Result<Path<i64>, PathRejection>,
) -> Result<StatusCode, ApiError> {
    let request_id = request_id_from_headers(&headers);
    require_operation(&stamp, DELETE_OPERATION_ID, &request_id)?;
    let deck_id = valid_deck_id(path, &request_id)?;
    let mut transaction = state
        .database
        .pool()
        .begin()
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    verify_stamp(&mut transaction, &stamp, &request_id).await?;
    let deletion = persist_deck_deletion(&mut transaction, current_user.id, deck_id)
        .await
        .map_err(|error| repository_error(error, &request_id))?
        .ok_or_else(|| {
            deck_error(
                StatusCode::NOT_FOUND,
                "Learning Deck not found",
                &request_id,
            )
        })?;
    transaction
        .commit()
        .await
        .map_err(|error| internal_error(error, &request_id))?;

    // External cleanup deliberately happens after the short finalization transaction. The stable
    // deck is already private/deleted if object storage is temporarily unavailable, and a retry is
    // safe because missing immutable objects are treated as success.
    let artifacts = LearningDeckArtifactStore::from_environment()
        .map_err(|error| internal_error(error, &request_id))?;
    artifacts
        .delete_many(&deletion.object_keys)
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    Ok(StatusCode::NO_CONTENT)
}

#[utoipa::path(
    post,
    path = "/api/learning/decks/{deck_id}/viewer-url",
    operation_id = "createLearningDecksViewerUrl",
    tag = "learning",
    summary = "Create a short-lived private viewer URL",
    params(("deck_id" = i64, Path, description = "Learning Deck ID")),
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, description = "Successful Response", body = LearningDeckUrlResponse),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 404, description = "Learning Deck not found", body = newsly_contracts::ErrorEnvelope),
        (status = 409, description = "Learning Deck is not ready", body = newsly_contracts::ErrorEnvelope),
        (status = 422, description = "Validation Error", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(crate) async fn create_viewer_url(
    State(state): State<AppState>,
    headers: HeaderMap,
    current_user: AuthenticatedUser,
    Extension(stamp): Extension<RouteOwnershipStamp>,
    path: Result<Path<i64>, PathRejection>,
) -> Result<Json<LearningDeckUrlResponse>, ApiError> {
    private_deck_url(
        &state,
        &headers,
        current_user.id,
        &stamp,
        path,
        VIEWER_URL_OPERATION_ID,
        PrivateDeckTarget::Viewer,
    )
    .await
}

#[utoipa::path(
    post,
    path = "/api/learning/decks/{deck_id}/source-notes-url",
    operation_id = "createLearningDecksSourceNotesUrl",
    tag = "learning",
    summary = "Create a short-lived private source-notes URL",
    params(("deck_id" = i64, Path, description = "Learning Deck ID")),
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, description = "Successful Response", body = LearningDeckUrlResponse),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 404, description = "Learning Deck not found", body = newsly_contracts::ErrorEnvelope),
        (status = 409, description = "Learning Deck is not ready", body = newsly_contracts::ErrorEnvelope),
        (status = 422, description = "Validation Error", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(crate) async fn create_source_notes_url(
    State(state): State<AppState>,
    headers: HeaderMap,
    current_user: AuthenticatedUser,
    Extension(stamp): Extension<RouteOwnershipStamp>,
    path: Result<Path<i64>, PathRejection>,
) -> Result<Json<LearningDeckUrlResponse>, ApiError> {
    private_deck_url(
        &state,
        &headers,
        current_user.id,
        &stamp,
        path,
        SOURCE_NOTES_URL_OPERATION_ID,
        PrivateDeckTarget::SourceNotes,
    )
    .await
}

#[utoipa::path(
    post,
    path = "/api/learning/decks/{deck_id}/share",
    operation_id = "enableLearningDecksShare",
    tag = "learning",
    summary = "Enable public sharing for a Learning Deck",
    params(("deck_id" = i64, Path, description = "Learning Deck ID")),
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, description = "Successful Response", body = LearningDeckShareResponse),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 404, description = "Learning Deck not found", body = newsly_contracts::ErrorEnvelope),
        (status = 409, description = "Learning Deck is not ready to share", body = newsly_contracts::ErrorEnvelope),
        (status = 422, description = "Validation Error", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(crate) async fn enable_share(
    State(state): State<AppState>,
    headers: HeaderMap,
    current_user: AuthenticatedUser,
    Extension(stamp): Extension<RouteOwnershipStamp>,
    path: Result<Path<i64>, PathRejection>,
) -> Result<Json<LearningDeckShareResponse>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    require_operation(&stamp, ENABLE_SHARE_OPERATION_ID, &request_id)?;
    let deck_id = valid_deck_id(path, &request_id)?;
    let generated_nonce =
        generate_share_nonce().map_err(|error| internal_error(error, &request_id))?;
    let signer = token_signer(&request_id)?;
    let mut transaction = state
        .database
        .pool()
        .begin()
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    verify_stamp(&mut transaction, &stamp, &request_id).await?;
    let outcome = prepare_enable_learning_deck_share(
        &mut transaction,
        current_user.id,
        deck_id,
        &generated_nonce,
    )
    .await
    .map_err(|error| repository_error(error, &request_id))?;
    let nonce = match outcome {
        EnableLearningDeckShareOutcome::Ready { nonce, .. } => nonce,
        EnableLearningDeckShareOutcome::DeckNotFound => {
            return Err(deck_error(
                StatusCode::NOT_FOUND,
                "Learning Deck not found",
                &request_id,
            ));
        }
        EnableLearningDeckShareOutcome::DeckNotReady => {
            return Err(deck_error(
                StatusCode::CONFLICT,
                "Learning Deck is not ready to share",
                &request_id,
            ));
        }
    };
    let token = signer
        .share_token(deck_id, &nonce)
        .map_err(|error| internal_error(error, &request_id))?;
    persist_learning_deck_share(
        &mut transaction,
        deck_id,
        &nonce,
        &hash_learning_deck_token(&token),
    )
    .await
    .map_err(|error| repository_error(error, &request_id))?;
    transaction
        .commit()
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    Ok(Json(LearningDeckShareResponse {
        share_enabled: true,
        share_url: Some(external_url(
            &headers,
            &format!("/learning/share/{token}/"),
            &request_id,
        )?),
    }))
}

#[utoipa::path(
    delete,
    path = "/api/learning/decks/{deck_id}/share",
    operation_id = "disableLearningDecksShare",
    tag = "learning",
    summary = "Disable public sharing for a Learning Deck",
    params(("deck_id" = i64, Path, description = "Learning Deck ID")),
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, description = "Successful Response", body = LearningDeckShareResponse),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 404, description = "Learning Deck not found", body = newsly_contracts::ErrorEnvelope),
        (status = 409, description = "Stale runtime owner", body = newsly_contracts::ErrorEnvelope),
        (status = 422, description = "Validation Error", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(crate) async fn disable_share(
    State(state): State<AppState>,
    headers: HeaderMap,
    current_user: AuthenticatedUser,
    Extension(stamp): Extension<RouteOwnershipStamp>,
    path: Result<Path<i64>, PathRejection>,
) -> Result<Json<LearningDeckShareResponse>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    require_operation(&stamp, DISABLE_SHARE_OPERATION_ID, &request_id)?;
    let deck_id = valid_deck_id(path, &request_id)?;
    let mut transaction = state
        .database
        .pool()
        .begin()
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    verify_stamp(&mut transaction, &stamp, &request_id).await?;
    match disable_learning_deck_share(&mut transaction, current_user.id, deck_id)
        .await
        .map_err(|error| repository_error(error, &request_id))?
    {
        DisableLearningDeckShareOutcome::Disabled => {}
        DisableLearningDeckShareOutcome::DeckNotFound => {
            return Err(deck_error(
                StatusCode::NOT_FOUND,
                "Learning Deck not found",
                &request_id,
            ));
        }
    }
    transaction
        .commit()
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    Ok(Json(LearningDeckShareResponse {
        share_enabled: false,
        share_url: None,
    }))
}
