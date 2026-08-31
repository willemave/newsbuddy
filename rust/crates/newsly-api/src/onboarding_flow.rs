use std::collections::HashSet;

use axum::extract::rejection::JsonRejection;
use axum::extract::{Extension, Path, State};
use axum::http::{HeaderMap, StatusCode};
use axum::routing::{get, post};
use axum::{Json, Router};
use chrono::Utc;
use newsly_contracts::{
    AgentOnboardingCompleteRequest, AgentOnboardingStartRequest, AgentOnboardingStartResponse,
    OnboardingAudioDiscoverRequest, OnboardingAudioDiscoverResponse, OnboardingCompleteRequest,
    OnboardingCompleteResponse, OnboardingDiscoveryLaneStatus, OnboardingDiscoveryStatusResponse,
    OnboardingFastDiscoverRequest, OnboardingFastDiscoverResponse, OnboardingProfileRequest,
    OnboardingProfileResponse, OnboardingSelectedAggregator, OnboardingSuggestion,
    OnboardingSuggestionType, OnboardingVoiceParseRequest, OnboardingVoiceParseResponse,
    ScraperType,
};
use newsly_db::{
    AgentOnboardingSuggestionProjection, ExistingOnboardingFeedConfig, OnboardingAudioLaneInput,
    OnboardingAudioRunInput, OnboardingCompletionAggregator, OnboardingCompletionInput,
    OnboardingCompletionProjection, OnboardingCompletionSource,
    OnboardingDiscoveryStatusProjection, OnboardingFlowRepositoryError,
    OnboardingSuggestionProjection, canonicalize_feed_url, complete_onboarding_selection,
    create_onboarding_audio_run, expedite_pending_briefing_refresh,
    find_onboarding_discovery_status, list_existing_onboarding_feed_configs,
    load_agent_onboarding_suggestions, load_onboarding_completion_suggestions,
};
use newsly_providers::{OnboardingAudioPlan, OnboardingDiscoverySeeds, OnboardingSuggestionSeed};
use newsly_queue::{EnqueueRequest, QueueError, QueueKernel, TaskType};
use serde_json::{Map, Value, json};

use crate::auth::AuthenticatedUser;
use crate::error::ApiError;
use crate::feed_validation::FeedValidationError;
use crate::gateway::RouteOwnershipStamp;
use crate::scraper_config_normalization::{
    apply_validated_feed_url, feed_url, normalize_create_input,
};
use crate::write_support::{
    bad_request, decode_json, internal_error, not_found, require_operation, verify_stamp,
};
use crate::{AppState, request_id_from_headers};

const PROFILE_OPERATION_ID: &str = "buildOnboardingProfile";
const VOICE_OPERATION_ID: &str = "parseOnboardingVoice";
const FAST_DISCOVER_OPERATION_ID: &str = "runOnboardingFastDiscover";
const AUDIO_DISCOVER_OPERATION_ID: &str = "startOnboardingDiscoverAudioDiscoveryFlow";
const COMPLETE_FLOW_OPERATION_ID: &str = "completeOnboardingFlow";
const AGENT_START_OPERATION_ID: &str = "startOnboarding";
const AGENT_COMPLETE_OPERATION_ID: &str = "completeOnboarding";
const INITIAL_BACKFILL_COUNT: i64 = 2;
const SUPPORTED_AGGREGATOR_KEYS: [&str; 7] = [
    "brutalist",
    "finurls",
    "hackernews",
    "mediagazer",
    "memeorandum",
    "sciurls",
    "techmeme",
];

pub(super) fn router() -> Router<AppState> {
    Router::new()
        .route("/api/onboarding/profile", post(build_profile))
        .route("/api/onboarding/parse-voice", post(parse_voice))
        .route("/api/onboarding/fast-discover", post(fast_discover))
        .route("/api/onboarding/audio-discover", post(audio_discover))
        .route("/api/onboarding/complete", post(complete_flow))
        .route("/api/agent/onboarding", post(start_agent_onboarding))
        .route("/api/agent/onboarding/{run_id}", get(get_agent_onboarding))
        .route(
            "/api/agent/onboarding/{run_id}/complete",
            post(complete_agent_onboarding),
        )
}

#[utoipa::path(
    post,
    path = "/api/onboarding/profile",
    operation_id = "buildOnboardingProfile",
    tag = "onboarding",
    request_body = OnboardingProfileRequest,
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, description = "Successful Response", body = OnboardingProfileResponse),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 409, description = "Stale runtime owner", body = newsly_contracts::ErrorEnvelope),
        (status = 422, description = "Validation Error", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn build_profile(
    State(state): State<AppState>,
    headers: HeaderMap,
    current_user: AuthenticatedUser,
    Extension(stamp): Extension<RouteOwnershipStamp>,
    payload: Result<Json<OnboardingProfileRequest>, JsonRejection>,
) -> Result<Json<OnboardingProfileResponse>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    let Json(payload) = decode_json(payload, &request_id)?;
    require_operation(&stamp, PROFILE_OPERATION_ID, &request_id)?;
    validate_length(&payload.first_name, 1, 120, "first_name", &request_id)?;
    if payload.interest_topics.len() > 12 {
        return Err(validation_error(
            "interest_topics must contain at most 12 items",
            &request_id,
        ));
    }
    let interest_topics = clean_topics(&payload.interest_topics, usize::MAX);
    if interest_topics.is_empty() {
        return Err(validation_error("interest_topics is required", &request_id));
    }
    verify_before_external(&state, &stamp, &request_id).await?;
    let profile = state
        .onboarding
        .build_profile(&payload.first_name, &interest_topics)
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    let _ = current_user;
    Ok(Json(OnboardingProfileResponse {
        profile_summary: profile.profile_summary,
        inferred_topics: profile.inferred_topics,
        candidate_sources: profile.candidate_sources,
    }))
}

#[utoipa::path(
    post,
    path = "/api/onboarding/parse-voice",
    operation_id = "parseOnboardingVoice",
    tag = "onboarding",
    request_body = OnboardingVoiceParseRequest,
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, description = "Successful Response", body = OnboardingVoiceParseResponse),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 409, description = "Stale runtime owner", body = newsly_contracts::ErrorEnvelope),
        (status = 422, description = "Validation Error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn parse_voice(
    State(state): State<AppState>,
    headers: HeaderMap,
    current_user: AuthenticatedUser,
    Extension(stamp): Extension<RouteOwnershipStamp>,
    payload: Result<Json<OnboardingVoiceParseRequest>, JsonRejection>,
) -> Result<Json<OnboardingVoiceParseResponse>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    let Json(payload) = decode_json(payload, &request_id)?;
    require_operation(&stamp, VOICE_OPERATION_ID, &request_id)?;
    validate_length(&payload.transcript, 3, 6_000, "transcript", &request_id)?;
    validate_optional_length(payload.locale.as_deref(), 20, "locale", &request_id)?;
    verify_before_external(&state, &stamp, &request_id).await?;
    let transcript = payload.transcript.trim();
    let response = if transcript.is_empty() {
        empty_voice_response()
    } else {
        match state
            .onboarding
            .parse_voice(transcript, payload.locale.as_deref())
            .await
        {
            Ok(fields) => {
                let topics = clean_topics(&fields.interest_topics, 8);
                let mut missing_fields = Vec::new();
                if fields.first_name.is_none() {
                    missing_fields.push("first_name".to_owned());
                }
                if topics.is_empty() {
                    missing_fields.push("interest_topics".to_owned());
                }
                OnboardingVoiceParseResponse {
                    first_name: fields.first_name,
                    interest_topics: topics,
                    confidence: fields.confidence,
                    missing_fields,
                }
            }
            Err(error) => {
                tracing::error!(
                    error = %error,
                    user_id = current_user.id,
                    "onboarding voice parse failed; returning the Python-compatible empty result"
                );
                empty_voice_response()
            }
        }
    };
    Ok(Json(response))
}

#[utoipa::path(
    post,
    path = "/api/onboarding/fast-discover",
    operation_id = "runOnboardingFastDiscover",
    tag = "onboarding",
    request_body = OnboardingFastDiscoverRequest,
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, description = "Successful Response", body = OnboardingFastDiscoverResponse),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 409, description = "Stale runtime owner", body = newsly_contracts::ErrorEnvelope),
        (status = 422, description = "Validation Error", body = newsly_contracts::ErrorEnvelope),
        (status = 503, description = "Feed discovery unavailable", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn fast_discover(
    State(state): State<AppState>,
    headers: HeaderMap,
    current_user: AuthenticatedUser,
    Extension(stamp): Extension<RouteOwnershipStamp>,
    payload: Result<Json<OnboardingFastDiscoverRequest>, JsonRejection>,
) -> Result<Json<OnboardingFastDiscoverResponse>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    let Json(payload) = decode_json(payload, &request_id)?;
    require_operation(&stamp, FAST_DISCOVER_OPERATION_ID, &request_id)?;
    validate_min_length(&payload.profile_summary, 3, "profile_summary", &request_id)?;
    if payload.inferred_topics.len() > 12 {
        return Err(validation_error(
            "inferred_topics must contain at most 12 items",
            &request_id,
        ));
    }
    verify_before_external(&state, &stamp, &request_id).await?;
    let seeds = match state
        .onboarding
        .fast_discover(&payload.profile_summary, &payload.inferred_topics)
        .await
    {
        Ok(seeds) => seeds,
        Err(error) => {
            tracing::error!(
                error = %error,
                user_id = current_user.id,
                "fast onboarding discovery failed; returning no static defaults"
            );
            return Ok(Json(empty_discovery_response()));
        }
    };
    let response = normalize_discovery_seeds(
        &state,
        seeds,
        &payload.profile_summary,
        &payload.inferred_topics,
    )
    .await
    .map_err(|error| {
        feed_unavailable(
            &error,
            "Feed discovery is temporarily unavailable",
            &request_id,
        )
    })?;
    Ok(Json(response))
}

#[utoipa::path(
    post,
    path = "/api/onboarding/audio-discover",
    operation_id = "startOnboardingDiscoverAudioDiscoveryFlow",
    tag = "onboarding",
    request_body = OnboardingAudioDiscoverRequest,
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, description = "Successful Response", body = OnboardingAudioDiscoverResponse),
        (status = 400, description = "Transcript is empty", body = newsly_contracts::ErrorEnvelope),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 409, description = "Stale runtime owner", body = newsly_contracts::ErrorEnvelope),
        (status = 422, description = "Validation Error", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn audio_discover(
    State(state): State<AppState>,
    headers: HeaderMap,
    current_user: AuthenticatedUser,
    Extension(stamp): Extension<RouteOwnershipStamp>,
    payload: Result<Json<OnboardingAudioDiscoverRequest>, JsonRejection>,
) -> Result<Json<OnboardingAudioDiscoverResponse>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    let Json(payload) = decode_json(payload, &request_id)?;
    require_operation(&stamp, AUDIO_DISCOVER_OPERATION_ID, &request_id)?;
    validate_length(&payload.transcript, 3, 8_000, "transcript", &request_id)?;
    validate_optional_length(payload.locale.as_deref(), 20, "locale", &request_id)?;
    let transcript = payload.transcript.trim();
    if transcript.is_empty() {
        return Err(bad_request("Transcript is required", &request_id));
    }
    let response = start_audio_run(
        &state,
        current_user.id,
        transcript,
        payload.locale.as_deref(),
        &stamp,
        &request_id,
    )
    .await?;
    Ok(Json(response))
}

#[utoipa::path(
    post,
    path = "/api/onboarding/complete",
    operation_id = "completeOnboardingFlow",
    tag = "onboarding",
    request_body = OnboardingCompleteRequest,
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, description = "Successful Response", body = OnboardingCompleteResponse),
        (status = 400, description = "Invalid onboarding selection", body = newsly_contracts::ErrorEnvelope),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 409, description = "Stale runtime owner", body = newsly_contracts::ErrorEnvelope),
        (status = 422, description = "Validation Error", body = newsly_contracts::ErrorEnvelope),
        (status = 503, description = "Onboarding dependency unavailable", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn complete_flow(
    State(state): State<AppState>,
    headers: HeaderMap,
    current_user: AuthenticatedUser,
    Extension(stamp): Extension<RouteOwnershipStamp>,
    payload: Result<Json<OnboardingCompleteRequest>, JsonRejection>,
) -> Result<Json<OnboardingCompleteResponse>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    let Json(payload) = decode_json(payload, &request_id)?;
    require_operation(&stamp, COMPLETE_FLOW_OPERATION_ID, &request_id)?;
    validate_completion_request(&payload, &request_id)?;
    let response =
        complete_onboarding(&state, current_user.id, payload, &stamp, &request_id).await?;
    Ok(Json(response))
}

#[utoipa::path(
    post,
    path = "/api/agent/onboarding",
    operation_id = "startOnboarding",
    tag = "onboarding",
    request_body = AgentOnboardingStartRequest,
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, description = "Successful Response", body = AgentOnboardingStartResponse),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 409, description = "Stale runtime owner", body = newsly_contracts::ErrorEnvelope),
        (status = 422, description = "Validation Error", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn start_agent_onboarding(
    State(state): State<AppState>,
    headers: HeaderMap,
    current_user: AuthenticatedUser,
    Extension(stamp): Extension<RouteOwnershipStamp>,
    payload: Result<Json<AgentOnboardingStartRequest>, JsonRejection>,
) -> Result<Json<AgentOnboardingStartResponse>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    let Json(payload) = decode_json(payload, &request_id)?;
    require_operation(&stamp, AGENT_START_OPERATION_ID, &request_id)?;
    validate_length(&payload.brief, 1, 4_000, "brief", &request_id)?;
    if payload.brief.chars().count() < 3 || payload.brief.trim().is_empty() {
        return Err(internal_error(
            "agent onboarding brief does not satisfy the reused audio request contract",
            &request_id,
        ));
    }
    let response = start_audio_run(
        &state,
        current_user.id,
        payload.brief.trim(),
        None,
        &stamp,
        &request_id,
    )
    .await?;
    Ok(Json(AgentOnboardingStartResponse {
        run_id: response.run_id,
        status: response.run_status,
        job_id: None,
    }))
}

#[utoipa::path(
    get,
    path = "/api/agent/onboarding/{run_id}",
    operation_id = "getOnboarding",
    tag = "onboarding",
    params(("run_id" = i64, Path, description = "Onboarding run ID")),
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, description = "Successful Response", body = OnboardingDiscoveryStatusResponse),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 404, description = "Onboarding run not found", body = newsly_contracts::ErrorEnvelope),
        (status = 422, description = "Validation Error", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn get_agent_onboarding(
    State(state): State<AppState>,
    headers: HeaderMap,
    current_user: AuthenticatedUser,
    Path(run_id): Path<i64>,
) -> Result<Json<OnboardingDiscoveryStatusResponse>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    let status = find_onboarding_discovery_status(state.database.pool(), current_user.id, run_id)
        .await
        .map_err(|error| internal_error(error, &request_id))?
        .ok_or_else(|| not_found("Onboarding run", &request_id))?;
    Ok(Json(present_discovery_status(status)))
}

#[utoipa::path(
    post,
    path = "/api/agent/onboarding/{run_id}/complete",
    operation_id = "completeOnboarding",
    tag = "onboarding",
    params(("run_id" = i64, Path, description = "Onboarding run ID")),
    request_body = AgentOnboardingCompleteRequest,
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, description = "Successful Response", body = OnboardingCompleteResponse),
        (status = 400, description = "Invalid onboarding selection", body = newsly_contracts::ErrorEnvelope),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 409, description = "Stale runtime owner", body = newsly_contracts::ErrorEnvelope),
        (status = 422, description = "Validation Error", body = newsly_contracts::ErrorEnvelope),
        (status = 503, description = "Onboarding dependency unavailable", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn complete_agent_onboarding(
    State(state): State<AppState>,
    headers: HeaderMap,
    current_user: AuthenticatedUser,
    Extension(stamp): Extension<RouteOwnershipStamp>,
    Path(run_id): Path<i64>,
    payload: Result<Json<AgentOnboardingCompleteRequest>, JsonRejection>,
) -> Result<Json<OnboardingCompleteResponse>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    let Json(payload) = decode_json(payload, &request_id)?;
    require_operation(&stamp, AGENT_COMPLETE_OPERATION_ID, &request_id)?;
    let selected_suggestion_ids = if payload.accept_all {
        load_agent_onboarding_suggestions(state.database.pool(), current_user.id, run_id)
            .await
            .map_err(|error| completion_selection_error(error, &request_id))?
            .into_iter()
            .map(|suggestion| suggestion.id)
            .collect()
    } else {
        payload.selected_suggestion_ids
    };
    let completion = OnboardingCompleteRequest {
        discovery_run_id: Some(run_id),
        selected_suggestion_ids,
        selected_aggregators: payload.selected_aggregators,
        twitter_username: None,
    };
    validate_completion_request(&completion, &request_id)?;
    let response =
        complete_onboarding(&state, current_user.id, completion, &stamp, &request_id).await?;
    Ok(Json(response))
}

async fn start_audio_run(
    state: &AppState,
    user_id: i64,
    transcript: &str,
    locale: Option<&str>,
    stamp: &RouteOwnershipStamp,
    request_id: &str,
) -> Result<OnboardingAudioDiscoverResponse, ApiError> {
    verify_before_external(state, stamp, request_id).await?;
    let plan = state.onboarding.build_audio_plan(transcript, locale).await;
    let mut transaction = state
        .database
        .pool()
        .begin()
        .await
        .map_err(|error| internal_error(error, request_id))?;
    verify_stamp(&mut transaction, stamp, request_id).await?;
    let input = audio_run_input(user_id, plan);
    let run = create_onboarding_audio_run(&mut transaction, &input)
        .await
        .map_err(|error| internal_error(error, request_id))?;
    let mut request = EnqueueRequest::new(TaskType::OnboardingDiscover);
    request.payload = object(json!({"user_id": user_id, "run_id": run.run_id}));
    request.owner_user_id = Some(user_id);
    QueueKernel::new(state.database.pool().clone())
        .enqueue_many_in_transaction(&mut transaction, vec![request])
        .await
        .map_err(|error| queue_internal_error(error, request_id))?;
    transaction
        .commit()
        .await
        .map_err(|error| internal_error(error, request_id))?;
    Ok(OnboardingAudioDiscoverResponse {
        run_id: run.run_id,
        run_status: run.run_status,
        topic_summary: run.topic_summary,
        inferred_topics: run.inferred_topics,
        lanes: run
            .lanes
            .into_iter()
            .map(|lane| OnboardingDiscoveryLaneStatus {
                name: lane.name,
                status: lane.status,
                completed_queries: lane.completed_queries,
                query_count: lane.query_count,
            })
            .collect(),
    })
}

fn audio_run_input(user_id: i64, plan: OnboardingAudioPlan) -> OnboardingAudioRunInput {
    OnboardingAudioRunInput {
        user_id,
        topic_summary: plan.topic_summary,
        inferred_topics: plan.inferred_topics,
        lanes: plan
            .lanes
            .into_iter()
            .map(|lane| OnboardingAudioLaneInput {
                name: lane.name,
                goal: lane.goal,
                target: lane.target.as_str().to_owned(),
                queries: lane.queries,
            })
            .collect(),
    }
}

async fn complete_onboarding(
    state: &AppState,
    user_id: i64,
    payload: OnboardingCompleteRequest,
    stamp: &RouteOwnershipStamp,
    request_id: &str,
) -> Result<OnboardingCompleteResponse, ApiError> {
    verify_before_external(state, stamp, request_id).await?;
    let input = prepare_completion(state, user_id, payload, request_id).await?;
    let mut transaction = state
        .database
        .pool()
        .begin()
        .await
        .map_err(|error| completion_unavailable(error, request_id))?;
    verify_stamp(&mut transaction, stamp, request_id).await?;
    let persisted = complete_onboarding_selection(&mut transaction, &input.selection)
        .await
        .map_err(|error| match error {
            OnboardingFlowRepositoryError::UserMissingOrInactive => {
                bad_request("User not found", request_id)
            }
            other @ (OnboardingFlowRepositoryError::DiscoveryRunNotFound
            | OnboardingFlowRepositoryError::DiscoveryRunNotCompleted
            | OnboardingFlowRepositoryError::InvalidSuggestionSelection) => {
                completion_selection_error(other, request_id)
            }
            other => completion_unavailable(other, request_id),
        })?;
    let (requests, primary_index, briefing_index) = completion_tasks(user_id, &persisted);
    let batch = QueueKernel::new(state.database.pool().clone())
        .enqueue_many_in_transaction(&mut transaction, requests)
        .await
        .map_err(|error| completion_unavailable(error, request_id))?;
    let briefing_task_id = batch.task_ids.get(briefing_index).copied().ok_or_else(|| {
        completion_unavailable("briefing refresh queue result missing", request_id)
    })?;
    if !batch.inserted_task_ids.contains(&briefing_task_id) {
        expedite_pending_briefing_refresh(&mut transaction, briefing_task_id, Utc::now())
            .await
            .map_err(|error| completion_unavailable(error, request_id))?;
    }
    let task_id = primary_index.and_then(|index| batch.task_ids.get(index).copied());
    transaction
        .commit()
        .await
        .map_err(|error| completion_unavailable(error, request_id))?;
    Ok(OnboardingCompleteResponse {
        status: "queued".to_owned(),
        task_id,
        inbox_count_estimate: persisted.inbox_count.max(100),
        configured_source_count: persisted.configured_source_count,
        longform_status: "loading".to_owned(),
        has_completed_onboarding: true,
        has_completed_new_user_tutorial: persisted.tutorial_complete,
    })
}

struct PreparedCompletion {
    selection: OnboardingCompletionInput,
}

async fn prepare_completion(
    state: &AppState,
    user_id: i64,
    payload: OnboardingCompleteRequest,
    request_id: &str,
) -> Result<PreparedCompletion, ApiError> {
    let selected_suggestion_ids = payload.selected_suggestion_ids;
    let suggestions = match payload.discovery_run_id {
        Some(run_id) => load_onboarding_completion_suggestions(
            state.database.pool(),
            user_id,
            run_id,
            &selected_suggestion_ids,
        )
        .await
        .map_err(|error| completion_selection_error(error, request_id))?,
        None => Vec::new(),
    };
    let existing = list_existing_onboarding_feed_configs(state.database.pool(), user_id)
        .await
        .map_err(|error| completion_unavailable(error, request_id))?;
    let mut sources = Vec::with_capacity(suggestions.len());
    let mut subreddits = Vec::new();
    for suggestion in suggestions {
        if suggestion.suggestion_type == "reddit" {
            let subreddit = suggestion
                .subreddit
                .as_deref()
                .and_then(normalize_subreddit)
                .ok_or_else(|| bad_request("Selected Reddit suggestion is invalid", request_id))?;
            subreddits.push(subreddit);
        } else {
            sources.push(prepare_source(state, suggestion, &existing, request_id).await?);
        }
    }
    let aggregators = normalize_aggregators(payload.selected_aggregators);
    let (update_twitter_username, twitter_username) = match payload.twitter_username {
        None => (false, None),
        Some(value) => (true, normalize_twitter_username(&value, request_id)?),
    };
    Ok(PreparedCompletion {
        selection: OnboardingCompletionInput {
            user_id,
            discovery_run_id: payload.discovery_run_id,
            selected_suggestion_ids,
            sources,
            subreddits,
            aggregators,
            update_twitter_username,
            twitter_username,
        },
    })
}

async fn prepare_source(
    state: &AppState,
    source: AgentOnboardingSuggestionProjection,
    existing: &[ExistingOnboardingFeedConfig],
    request_id: &str,
) -> Result<OnboardingCompletionSource, ApiError> {
    if source
        .title
        .as_ref()
        .is_some_and(|value| value.chars().count() > 255)
    {
        return Err(bad_request(
            "display_name must contain at most 255 characters",
            request_id,
        ));
    }
    let requested_source_url = source
        .feed_url
        .ok_or_else(|| bad_request("Selected feed suggestion is invalid", request_id))?;
    validate_length(&requested_source_url, 5, 2_048, "feed_url", request_id)?;
    let scraper_type = selected_scraper_type(&source.suggestion_type)
        .ok_or_else(|| bad_request("Selected feed suggestion type is invalid", request_id))?;
    let mut config = Map::new();
    config.insert(
        "feed_url".to_owned(),
        Value::String(requested_source_url.clone()),
    );
    config
        .entry("limit".to_owned())
        .or_insert_with(|| Value::from(1));
    let mut config = normalize_create_input(scraper_type, config)
        .map_err(|error| bad_request(error.to_string(), request_id))?;
    let requested_feed_url = feed_url(&config).to_owned();
    let requested_canonical = canonicalize_feed_url(&requested_feed_url);
    let stored = existing.iter().find(|candidate| {
        candidate.scraper_type == scraper_type.as_str()
            && candidate
                .feed_url
                .as_deref()
                .or_else(|| candidate.config.get("feed_url").and_then(Value::as_str))
                .is_some_and(|value| canonicalize_feed_url(value) == requested_canonical)
    });
    if let Some(stored) = stored {
        let effective = stored
            .feed_url
            .as_deref()
            .or_else(|| stored.config.get("feed_url").and_then(Value::as_str))
            .unwrap_or(&requested_feed_url);
        apply_validated_feed_url(&mut config, effective);
    } else {
        let effective = state
            .feed_validator
            .validate_feed_url(&requested_feed_url)
            .await
            .map_err(|error| {
                feed_unavailable(
                    &error,
                    "Feed validation is temporarily unavailable",
                    request_id,
                )
            })?
            .ok_or_else(|| {
                bad_request(
                    "config.feed_url must be a valid RSS/Atom feed URL",
                    request_id,
                )
            })?;
        apply_validated_feed_url(&mut config, &effective);
    }
    let feed_url = feed_url(&config).to_owned();
    Ok(OnboardingCompletionSource {
        scraper_type: scraper_type.as_str().to_owned(),
        title: source.title,
        feed_url,
        seed_feed_url: requested_source_url,
        config: Value::Object(config),
    })
}

fn selected_scraper_type(value: &str) -> Option<ScraperType> {
    match value {
        "substack" => Some(ScraperType::Substack),
        "atom" => Some(ScraperType::Atom),
        "podcast_rss" => Some(ScraperType::PodcastRss),
        _ => None,
    }
}

fn completion_tasks(
    user_id: i64,
    persisted: &OnboardingCompletionProjection,
) -> (Vec<EnqueueRequest>, Option<usize>, usize) {
    let mut requests = Vec::new();
    let briefing_index = requests.len();
    let mut briefing = EnqueueRequest::new(TaskType::BriefingRefresh);
    briefing.payload = object(json!({"user_id": user_id, "mode": "append"}));
    briefing.dedupe_key = Some(format!("briefing_refresh:{user_id}:append"));
    briefing.owner_user_id = Some(user_id);
    briefing.available_at = Some(Utc::now());
    requests.push(briefing);

    let mut primary_index = None;
    if !persisted.feed_config_ids.is_empty() {
        primary_index = Some(requests.len());
        let mut request = EnqueueRequest::new(TaskType::BackfillFeeds);
        request.payload = object(json!({
            "user_id": user_id,
            "config_ids": persisted.feed_config_ids,
            "count": INITIAL_BACKFILL_COUNT,
            "first_edition_run_id": persisted.first_edition_run_id,
        }));
        request.dedupe = Some(true);
        request.owner_user_id = Some(user_id);
        requests.push(request);
    }
    if !persisted.sources_to_scrape.is_empty() {
        primary_index.get_or_insert(requests.len());
        let mut request = EnqueueRequest::new(TaskType::Scrape);
        request.payload = object(json!({
            "sources": persisted.sources_to_scrape,
            "first_edition_run_id": persisted.first_edition_run_id,
        }));
        request.access_user_id = Some(user_id);
        requests.push(request);
    }
    if !persisted.has_feed_discovery_task {
        primary_index.get_or_insert(requests.len());
        let mut request = EnqueueRequest::new(TaskType::DiscoverFeeds);
        request.payload = object(json!({"user_id": user_id, "trigger": "onboarding"}));
        request.dedupe = Some(true);
        request.owner_user_id = Some(user_id);
        requests.push(request);
    }
    for content_id in &persisted.generate_image_content_ids {
        let mut request = EnqueueRequest::new(TaskType::GenerateImage);
        request.content_id = Some(*content_id);
        requests.push(request);
    }
    (requests, primary_index, briefing_index)
}

async fn verify_before_external(
    state: &AppState,
    stamp: &RouteOwnershipStamp,
    request_id: &str,
) -> Result<(), ApiError> {
    let mut transaction = state
        .database
        .pool()
        .begin()
        .await
        .map_err(|error| internal_error(error, request_id))?;
    verify_stamp(&mut transaction, stamp, request_id).await?;
    transaction
        .commit()
        .await
        .map_err(|error| internal_error(error, request_id))
}

async fn normalize_discovery_seeds(
    state: &AppState,
    seeds: OnboardingDiscoverySeeds,
    profile_summary: &str,
    inferred_topics: &[String],
) -> Result<OnboardingFastDiscoverResponse, FeedValidationError> {
    let substacks = normalize_feed_suggestions(
        state,
        seeds.substacks,
        OnboardingSuggestionType::Substack,
        profile_summary,
        inferred_topics,
    )
    .await?;
    let podcasts = normalize_feed_suggestions(
        state,
        seeds.podcasts,
        OnboardingSuggestionType::PodcastRss,
        profile_summary,
        inferred_topics,
    )
    .await?;
    let subreddits =
        normalize_subreddit_suggestions(seeds.subreddits, profile_summary, inferred_topics);
    Ok(OnboardingFastDiscoverResponse {
        recommended_pods: podcasts,
        recommended_substacks: substacks,
        recommended_subreddits: subreddits,
    })
}

async fn normalize_feed_suggestions(
    state: &AppState,
    seeds: Vec<OnboardingSuggestionSeed>,
    suggestion_type: OnboardingSuggestionType,
    profile_summary: &str,
    inferred_topics: &[String],
) -> Result<Vec<OnboardingSuggestion>, FeedValidationError> {
    let mut suggestions = Vec::new();
    let mut seen = HashSet::new();
    for seed in seeds {
        let site_url = clean_optional(seed.site_url);
        let mut candidate =
            clean_optional(seed.feed_url).or_else(|| clean_optional(seed.candidate_feed_url));
        if candidate.is_none() && suggestion_type == OnboardingSuggestionType::Substack {
            candidate = site_url
                .as_deref()
                .map(|site_url| format!("{}/feed", site_url.trim_end_matches('/')));
        }
        if candidate.is_none() && seed.is_likely_feed == Some(true) {
            candidate = site_url.as_deref().and_then(infer_feed_url);
        }
        let Some(candidate) = candidate else {
            continue;
        };
        let Some(feed_url) = state.feed_validator.validate_feed_url(&candidate).await? else {
            continue;
        };
        let canonical = canonicalize_feed_url(&feed_url);
        if !seen.insert(canonical.clone()) {
            continue;
        }
        let title = clean_optional(seed.title);
        let rationale = clean_optional(seed.rationale).or_else(|| {
            Some(default_rationale(
                suggestion_type,
                title.as_deref().unwrap_or(&canonical),
                profile_summary,
                inferred_topics,
            ))
        });
        suggestions.push(OnboardingSuggestion {
            id: None,
            suggestion_type,
            title,
            site_url,
            feed_url: Some(canonical),
            subreddit: None,
            rationale,
            score: seed.score,
            is_default: false,
        });
        if suggestions.len() >= 5 {
            break;
        }
    }
    Ok(suggestions)
}

fn normalize_subreddit_suggestions(
    seeds: Vec<OnboardingSuggestionSeed>,
    profile_summary: &str,
    inferred_topics: &[String],
) -> Vec<OnboardingSuggestion> {
    let mut suggestions = Vec::new();
    let mut seen = HashSet::new();
    for seed in seeds {
        let site_url = clean_optional(seed.site_url);
        let subreddit = clean_optional(seed.subreddit)
            .and_then(|value| normalize_subreddit(&value))
            .or_else(|| site_url.as_deref().and_then(extract_subreddit));
        let Some(subreddit) = subreddit else {
            continue;
        };
        let key = subreddit.to_lowercase();
        if !seen.insert(key) {
            continue;
        }
        let title = clean_optional(seed.title).or_else(|| Some(subreddit.clone()));
        let rationale = clean_optional(seed.rationale).or_else(|| {
            Some(default_rationale(
                OnboardingSuggestionType::Reddit,
                title.as_deref().unwrap_or(&subreddit),
                profile_summary,
                inferred_topics,
            ))
        });
        suggestions.push(OnboardingSuggestion {
            id: None,
            suggestion_type: OnboardingSuggestionType::Reddit,
            title,
            site_url,
            feed_url: None,
            subreddit: Some(subreddit),
            rationale,
            score: seed.score,
            is_default: false,
        });
        if suggestions.len() >= 5 {
            break;
        }
    }
    suggestions
}

fn default_rationale(
    suggestion_type: OnboardingSuggestionType,
    label: &str,
    profile_summary: &str,
    inferred_topics: &[String],
) -> String {
    let context = clean_topics(inferred_topics, 2);
    let context = if context.is_empty() {
        profile_summary.trim().to_owned()
    } else {
        context.join(", ")
    };
    let context = if context.is_empty() {
        "your interests"
    } else {
        &context
    };
    match suggestion_type {
        OnboardingSuggestionType::PodcastRss => {
            format!("Podcast covering {label} with discussions relevant to {context}.")
        }
        OnboardingSuggestionType::Reddit => {
            format!("Active subreddit for {label} with ongoing threads related to {context}.")
        }
        OnboardingSuggestionType::Substack | OnboardingSuggestionType::Atom => {
            format!("Feed focused on {label} with updates tied to {context}.")
        }
    }
}

fn present_discovery_status(
    status: OnboardingDiscoveryStatusProjection,
) -> OnboardingDiscoveryStatusResponse {
    OnboardingDiscoveryStatusResponse {
        run_id: status.run_id,
        run_status: status.run_status,
        topic_summary: status.topic_summary,
        inferred_topics: status.inferred_topics,
        lanes: status
            .lanes
            .into_iter()
            .map(|lane| OnboardingDiscoveryLaneStatus {
                name: lane.name,
                status: lane.status,
                completed_queries: lane.completed_queries,
                query_count: lane.query_count,
            })
            .collect(),
        suggestions: status.suggestions.map(present_suggestions),
        error_message: status.error_message,
    }
}

fn present_suggestions(
    suggestions: Vec<OnboardingSuggestionProjection>,
) -> OnboardingFastDiscoverResponse {
    let mut response = empty_discovery_response();
    for suggestion in suggestions {
        let Ok(suggestion_type) =
            OnboardingSuggestionType::try_from(suggestion.suggestion_type.as_str())
        else {
            continue;
        };
        let item = OnboardingSuggestion {
            id: Some(suggestion.id),
            suggestion_type,
            title: suggestion.title,
            site_url: suggestion.site_url,
            feed_url: suggestion.feed_url,
            subreddit: suggestion.subreddit,
            rationale: suggestion.rationale,
            score: suggestion.score,
            is_default: false,
        };
        match suggestion_type {
            OnboardingSuggestionType::PodcastRss => response.recommended_pods.push(item),
            OnboardingSuggestionType::Reddit => response.recommended_subreddits.push(item),
            OnboardingSuggestionType::Substack | OnboardingSuggestionType::Atom => {
                response.recommended_substacks.push(item);
            }
        }
    }
    response
}

fn normalize_aggregators(
    aggregators: Vec<OnboardingSelectedAggregator>,
) -> Vec<OnboardingCompletionAggregator> {
    aggregators
        .into_iter()
        .filter_map(|aggregator| {
            let key = aggregator.key.trim().to_lowercase();
            if !SUPPORTED_AGGREGATOR_KEYS.contains(&key.as_str()) {
                return None;
            }
            let mut topics = aggregator
                .topics
                .into_iter()
                .map(|topic| topic.trim().to_lowercase())
                .filter(|topic| !topic.is_empty())
                .collect::<Vec<_>>();
            topics.sort();
            topics.dedup();
            Some(OnboardingCompletionAggregator {
                key,
                title: aggregator.title,
                topics,
            })
        })
        .collect()
}

fn normalize_twitter_username(
    username: &str,
    request_id: &str,
) -> Result<Option<String>, ApiError> {
    let mut cleaned = username.trim();
    if cleaned.is_empty() {
        return Ok(None);
    }
    if let Some(value) = cleaned.strip_prefix('@') {
        cleaned = value.trim();
    }
    if cleaned.is_empty() {
        return Ok(None);
    }
    if cleaned.len() > 15
        || !cleaned
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || byte == b'_')
    {
        return Err(bad_request(
            "Twitter username must be 1-15 chars (letters, numbers, underscore)",
            request_id,
        ));
    }
    Ok(Some(cleaned.to_ascii_lowercase()))
}

fn normalize_subreddit(value: &str) -> Option<String> {
    let value = value.trim();
    let value = value.strip_prefix("r/").unwrap_or(value).trim_matches('/');
    (!value.is_empty()).then(|| value.to_owned())
}

fn extract_subreddit(site_url: &str) -> Option<String> {
    let url = reqwest::Url::parse(site_url).ok()?;
    let host = url.host_str()?.to_ascii_lowercase();
    if host != "reddit.com" && !host.ends_with(".reddit.com") {
        return None;
    }
    let parts = url
        .path_segments()?
        .filter(|part| !part.is_empty())
        .collect::<Vec<_>>();
    (parts.len() >= 2 && parts[0].eq_ignore_ascii_case("r"))
        .then(|| parts[1].trim().to_lowercase())
        .filter(|value| !value.is_empty())
}

fn infer_feed_url(site_url: &str) -> Option<String> {
    let lowered = site_url.to_lowercase();
    ["/feed", ".xml", "rss", "atom", "podcast"]
        .iter()
        .any(|marker| lowered.contains(marker))
        .then(|| site_url.trim().to_owned())
}

fn empty_voice_response() -> OnboardingVoiceParseResponse {
    OnboardingVoiceParseResponse {
        first_name: None,
        interest_topics: Vec::new(),
        confidence: Some(0.0),
        missing_fields: vec!["first_name".to_owned(), "interest_topics".to_owned()],
    }
}

fn empty_discovery_response() -> OnboardingFastDiscoverResponse {
    OnboardingFastDiscoverResponse {
        recommended_pods: Vec::new(),
        recommended_substacks: Vec::new(),
        recommended_subreddits: Vec::new(),
    }
}

fn clean_topics(values: &[String], limit: usize) -> Vec<String> {
    let mut topics = Vec::new();
    let mut seen = HashSet::new();
    for value in values {
        let value = value.trim().trim_matches(['.', ',', ';', ':']);
        if value.is_empty() || !seen.insert(value.to_lowercase()) {
            continue;
        }
        topics.push(value.to_owned());
        if topics.len() >= limit {
            break;
        }
    }
    topics
}

fn clean_optional(value: Option<String>) -> Option<String> {
    value
        .map(|value| value.trim().to_owned())
        .filter(|value| !value.is_empty())
}

fn object(value: Value) -> Option<Map<String, Value>> {
    match value {
        Value::Object(object) => Some(object),
        _ => None,
    }
}

fn validate_length(
    value: &str,
    minimum: usize,
    maximum: usize,
    field: &str,
    request_id: &str,
) -> Result<(), ApiError> {
    let length = value.chars().count();
    if !(minimum..=maximum).contains(&length) {
        return Err(validation_error(
            format!("{field} must contain between {minimum} and {maximum} characters"),
            request_id,
        ));
    }
    Ok(())
}

fn validate_min_length(
    value: &str,
    minimum: usize,
    field: &str,
    request_id: &str,
) -> Result<(), ApiError> {
    if value.chars().count() < minimum {
        return Err(validation_error(
            format!("{field} must contain at least {minimum} characters"),
            request_id,
        ));
    }
    Ok(())
}

fn validate_optional_length(
    value: Option<&str>,
    maximum: usize,
    field: &str,
    request_id: &str,
) -> Result<(), ApiError> {
    if value.is_some_and(|value| value.chars().count() > maximum) {
        return Err(validation_error(
            format!("{field} must contain at most {maximum} characters"),
            request_id,
        ));
    }
    Ok(())
}

fn validate_completion_request(
    payload: &OnboardingCompleteRequest,
    request_id: &str,
) -> Result<(), ApiError> {
    if payload.discovery_run_id.is_some_and(|run_id| run_id <= 0) {
        return Err(bad_request(
            "discovery_run_id must be greater than zero",
            request_id,
        ));
    }
    if payload.selected_suggestion_ids.len() > 100 {
        return Err(bad_request(
            "selected_suggestion_ids must contain at most 100 items",
            request_id,
        ));
    }
    if payload
        .selected_suggestion_ids
        .iter()
        .any(|suggestion_id| *suggestion_id <= 0)
    {
        return Err(bad_request(
            "selected_suggestion_ids must contain positive IDs",
            request_id,
        ));
    }
    let unique_ids = payload
        .selected_suggestion_ids
        .iter()
        .copied()
        .collect::<HashSet<_>>();
    if unique_ids.len() != payload.selected_suggestion_ids.len() {
        return Err(bad_request(
            "selected_suggestion_ids must not contain duplicates",
            request_id,
        ));
    }
    if payload.discovery_run_id.is_none() && !payload.selected_suggestion_ids.is_empty() {
        return Err(bad_request(
            "selected_suggestion_ids require a discovery_run_id",
            request_id,
        ));
    }
    Ok(())
}

fn validation_error(message: impl Into<String>, request_id: &str) -> ApiError {
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

fn feed_unavailable(error: &FeedValidationError, message: &str, request_id: &str) -> ApiError {
    tracing::error!(error = %error, "onboarding feed validation unavailable");
    ApiError::new(
        StatusCode::SERVICE_UNAVAILABLE,
        "service_unavailable",
        message,
        request_id.to_owned(),
    )
    .with_retryable(true)
}

fn completion_unavailable(error: impl std::fmt::Display, request_id: &str) -> ApiError {
    tracing::error!(error = %error, "onboarding completion dependency failed");
    ApiError::new(
        StatusCode::SERVICE_UNAVAILABLE,
        "service_unavailable",
        "Onboarding completion is temporarily unavailable",
        request_id.to_owned(),
    )
    .with_retryable(true)
}

fn completion_selection_error(error: OnboardingFlowRepositoryError, request_id: &str) -> ApiError {
    let message = match error {
        OnboardingFlowRepositoryError::DiscoveryRunNotFound => {
            "Discovery run is unavailable for this account"
        }
        OnboardingFlowRepositoryError::DiscoveryRunNotCompleted => {
            "Discovery must finish before onboarding can be completed"
        }
        OnboardingFlowRepositoryError::InvalidSuggestionSelection => {
            "Selected suggestions are not part of this discovery run"
        }
        other => return completion_unavailable(other, request_id),
    };
    bad_request(message, request_id)
}

fn queue_internal_error(error: QueueError, request_id: &str) -> ApiError {
    internal_error(error, request_id)
}

#[cfg(test)]
mod tests {
    use super::{completion_tasks, validate_completion_request};
    use newsly_contracts::OnboardingCompleteRequest;
    use newsly_db::OnboardingCompletionProjection;
    use newsly_queue::TaskType;

    #[test]
    fn completion_never_requeues_onboarding_discovery() {
        let persisted = OnboardingCompletionProjection {
            configured_source_count: 1,
            feed_config_ids: vec![10],
            first_edition_run_id: 20,
            sources_to_scrape: vec!["Reddit".to_owned()],
            generate_image_content_ids: vec![30],
            inbox_count: 100,
            tutorial_complete: false,
            has_feed_discovery_task: false,
        };
        let (requests, _, _) = completion_tasks(7, &persisted);
        assert!(
            requests
                .iter()
                .all(|request| request.task_type != TaskType::OnboardingDiscover)
        );
    }

    #[test]
    fn runless_completion_cannot_name_discovered_suggestions() {
        let request = OnboardingCompleteRequest {
            discovery_run_id: None,
            selected_suggestion_ids: vec![9],
            selected_aggregators: Vec::new(),
            twitter_username: None,
        };
        assert!(validate_completion_request(&request, "request-1").is_err());
    }
}
