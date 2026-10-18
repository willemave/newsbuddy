use std::collections::{BTreeSet, HashSet};

use axum::extract::rejection::JsonRejection;
use axum::extract::{Path, State};
use axum::http::{HeaderMap, StatusCode};
use axum::routing::{get, post};
use axum::{Json, Router};
use chrono::Utc;
use newsly_contracts::{
    AgentOnboardingCompleteRequest, AgentOnboardingStartRequest, AgentOnboardingStartResponse,
    OnboardingAudioDiscoverRequest, OnboardingAudioDiscoverResponse, OnboardingCompleteRequest,
    OnboardingCompleteResponse, OnboardingDiscoveryLaneStatus, OnboardingDiscoveryStatusResponse,
    OnboardingSelectedAggregator, ScraperType,
};
use newsly_db::{
    AgentOnboardingSuggestionProjection, ExistingOnboardingFeedConfig, OnboardingAudioLaneInput,
    OnboardingAudioRunInput, OnboardingCompletionAggregator, OnboardingCompletionInput,
    OnboardingCompletionProjection, OnboardingCompletionSource, OnboardingFlowRepositoryError,
    canonicalize_feed_url, complete_onboarding_selection, create_onboarding_audio_run,
    expedite_pending_briefing_refresh, find_onboarding_discovery_status,
    list_existing_onboarding_feed_configs, load_agent_onboarding_suggestions,
    load_onboarding_completion_suggestions,
};
use newsly_domain::AggregatorKey;
use newsly_providers::OnboardingAudioPlan;
use newsly_queue::{EnqueueRequest, QueueError, QueueKernel, TaskType};
use serde_json::{Map, Value, json};

use crate::auth::AuthenticatedUser;
use crate::error::ApiError;
use crate::gateway::RouteOwnershipStamp;
use crate::model_usage::onboarding_events_with_usage;
use crate::onboarding::present_discovery_status;
use crate::scraper_config_normalization::{
    apply_validated_feed_url, feed_url, normalize_create_input,
};
use crate::write_support::{bad_request, decode_json, internal_error, not_found, verify_stamp};
use crate::{AppState, request_id_from_headers};
use newsly_providers::FeedValidationError;

const INITIAL_BACKFILL_COUNT: i64 = 2;

pub(super) fn router() -> Router<AppState> {
    Router::new()
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
    stamp: RouteOwnershipStamp,
    payload: Result<Json<OnboardingAudioDiscoverRequest>, JsonRejection>,
) -> Result<Json<OnboardingAudioDiscoverResponse>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    let Json(payload) = decode_json(payload, &request_id)?;
    validate_length(&payload.transcript, 3, 8_000, "transcript", &request_id)?;
    validate_optional_length(payload.locale.as_deref(), 20, "locale", &request_id)?;
    if payload
        .input_kind
        .as_deref()
        .is_some_and(|kind| !matches!(kind, "typed" | "speech_transcript"))
    {
        return Err(ApiError::validation(
            "input_kind must be typed or speech_transcript",
            &request_id,
        ));
    }
    let transcript = payload.transcript.trim();
    if transcript.is_empty() {
        return Err(bad_request("Transcript is required", &request_id));
    }
    let response = start_audio_run(
        &state,
        current_user.id,
        &payload.transcript,
        payload.input_kind.as_deref().unwrap_or("speech_transcript"),
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
    stamp: RouteOwnershipStamp,
    payload: Result<Json<OnboardingCompleteRequest>, JsonRejection>,
) -> Result<Json<OnboardingCompleteResponse>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    let Json(payload) = decode_json(payload, &request_id)?;
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
    stamp: RouteOwnershipStamp,
    payload: Result<Json<AgentOnboardingStartRequest>, JsonRejection>,
) -> Result<Json<AgentOnboardingStartResponse>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    let Json(payload) = decode_json(payload, &request_id)?;
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
        &payload.brief,
        "agent_brief",
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
    stamp: RouteOwnershipStamp,
    Path(run_id): Path<i64>,
    payload: Result<Json<AgentOnboardingCompleteRequest>, JsonRejection>,
) -> Result<Json<OnboardingCompleteResponse>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    let Json(payload) = decode_json(payload, &request_id)?;
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
        news_filter_seed: None,
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
    input_kind: &str,
    locale: Option<&str>,
    stamp: &RouteOwnershipStamp,
    request_id: &str,
) -> Result<OnboardingAudioDiscoverResponse, ApiError> {
    let (events, usage) =
        onboarding_events_with_usage(state, Some(user_id), "onboarding.audio_plan");
    let plan = state
        .onboarding
        .build_audio_plan(transcript, locale, events)
        .await;
    usage.finish().await;
    let mut transaction =
        crate::write_support::begin_fenced(state.database.pool(), stamp, request_id).await?;
    let input = audio_run_input(user_id, transcript, input_kind, plan);
    let run = create_onboarding_audio_run(&mut transaction, &input)
        .await
        .map_err(|error| internal_error(error, request_id))?;
    let mut request = EnqueueRequest::new(TaskType::OnboardingDiscover);
    request.payload = object(json!({
        "user_id": user_id,
        "run_id": run.run_id,
        "narration": transcript.chars().take(8_000).collect::<String>(),
    }));
    request.owner_user_id = Some(user_id);
    QueueKernel::new(state.database.pool().clone())
        .enqueue_many_in_transaction(&mut transaction, vec![request])
        .await
        .map_err(|error| queue_internal_error(error, request_id))?;
    crate::write_support::commit(transaction, request_id).await?;
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

fn audio_run_input(
    user_id: i64,
    transcript: &str,
    input_kind: &str,
    plan: OnboardingAudioPlan,
) -> OnboardingAudioRunInput {
    OnboardingAudioRunInput {
        user_id,
        original_input_text: transcript.to_owned(),
        original_input_kind: input_kind.to_owned(),
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
    let input = prepare_completion(state, user_id, payload, request_id).await?;
    let mut transaction = state
        .database
        .pool()
        .begin()
        .await
        .map_err(|error| completion_unavailable(error, request_id))?;
    verify_stamp(&mut transaction, stamp, request_id).await?;
    let prior_demand = crate::aggregator_demand::snapshot(&mut transaction)
        .await
        .map_err(|error| completion_unavailable(error, request_id))?;
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
    crate::aggregator_demand::enqueue_new_demand(
        &mut transaction,
        &QueueKernel::new(state.database.pool().clone()),
        &prior_demand,
    )
    .await
    .map_err(|error| completion_unavailable(error, request_id))?;
    newsly_db::first_edition_progress::reconcile_sources(&mut transaction, user_id)
        .await
        .map_err(|error| completion_unavailable(error, request_id))?;
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
    let twitter_username = payload
        .twitter_username
        .map(|value| normalize_twitter_username(&value, request_id))
        .transpose()?;
    Ok(PreparedCompletion {
        selection: OnboardingCompletionInput {
            user_id,
            discovery_run_id: payload.discovery_run_id,
            selected_suggestion_ids,
            news_filter_seed: payload
                .news_filter_seed
                .map(|seed| (seed.text, seed.enabled)),
            sources,
            subreddits,
            aggregators,
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
    value
        .parse::<ScraperType>()
        .ok()
        .filter(|kind| kind.supports_backfill())
}

fn completion_tasks(
    user_id: i64,
    persisted: &OnboardingCompletionProjection,
) -> (Vec<EnqueueRequest>, Option<usize>, usize) {
    let mut requests = Vec::new();
    let briefing_index = requests.len();
    let mut briefing = EnqueueRequest::new(TaskType::BriefingRefresh);
    briefing.priority = 10;
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
    for source in &persisted.sources_to_scrape {
        primary_index.get_or_insert(requests.len());
        let mut request = EnqueueRequest::new(TaskType::Scrape);
        request.payload = object(
            json!({"sources": [source], "first_edition_run_id": persisted.first_edition_run_id}),
        );
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

fn normalize_aggregators(
    aggregators: Vec<OnboardingSelectedAggregator>,
) -> Vec<OnboardingCompletionAggregator> {
    aggregators
        .into_iter()
        .filter_map(|aggregator| {
            let key = aggregator.key.trim().to_lowercase();
            let catalog_key = AggregatorKey::from_key(&key)?;
            let topics = aggregator
                .topics
                .iter()
                .filter_map(|topic| catalog_key.canonical_topic(topic))
                .collect::<BTreeSet<_>>()
                .into_iter()
                .map(str::to_owned)
                .collect();
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
    if let Some(seed) = &payload.news_filter_seed {
        if payload.discovery_run_id.is_none() {
            return Err(bad_request(
                "news_filter_seed requires a discovery run",
                request_id,
            ));
        }
        if seed.text.chars().count() > 8_000 {
            return Err(ApiError::validation(
                "news_filter_seed.text exceeds 8000 characters",
                request_id,
            ));
        }
    }
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
    ApiError::validation(message, request_id)
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
mod tests;
