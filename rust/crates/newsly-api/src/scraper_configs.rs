use std::collections::{BTreeSet, HashMap};

use axum::extract::rejection::{JsonRejection, QueryRejection};
use axum::extract::{Extension, Path, Query, State};
use axum::http::{HeaderMap, StatusCode};
use axum::routing::{get, post, put};
use axum::{Json, Router};
use newsly_contracts::{
    CreateUserScraperConfig, FeedSubscriptionOutcome, ScraperConfigResponse,
    ScraperConfigStatsResponse, ScraperType, SubscribeToFeedRequest, UpdateUserScraperConfig,
};
use newsly_db::{
    NewScraperConfig, ScraperConfigPatch, ScraperConfigProjection, ScraperConfigRepositoryError,
    ScraperConfigStatsProjection, canonicalize_feed_url,
    create_scraper_config as persist_scraper_config,
    delete_scraper_config as persist_scraper_config_deletion, find_scraper_config,
    get_scraper_config_stats, list_scraper_configs as load_scraper_configs,
    scraper_config_identity_exists, update_scraper_config as persist_scraper_config_update,
};
use newsly_queue::{EnqueueRequest, QueueKernel, TaskType};
use serde::Deserialize;
use serde_json::{Value, json};

use crate::auth::AuthenticatedUser;
use crate::error::ApiError;
use crate::gateway::RouteOwnershipStamp;
use crate::scraper_config_normalization::{
    apply_validated_feed_url, feed_url, normalize_create_input, normalize_for_stored_type,
    normalize_update_input, requires_feed_probe, response_limit, validate_display_name,
};
use crate::write_support::{
    bad_request, decode_json, internal_error, require_operation, verify_stamp,
};
use crate::{AppState, request_id_from_headers};

const ALLOWED_SCRAPER_TYPES: [&str; 6] = [
    "aggregator",
    "atom",
    "podcast_rss",
    "reddit",
    "substack",
    "youtube",
];

const CREATE_OPERATION_ID: &str = "createScraperConfig";
const UPDATE_OPERATION_ID: &str = "updateScraperConfig";
const DELETE_OPERATION_ID: &str = "deleteScraperConfigEndpoint";
const CREATE_CONTENT_OPERATION_ID: &str = "createContentScraperConfig";
const UPDATE_CONTENT_OPERATION_ID: &str = "updateContentScraperConfig";
const DELETE_CONTENT_OPERATION_ID: &str = "deleteContentScraperConfigEndpoint";
const SUBSCRIBE_OPERATION_ID: &str = "subscribeScrapersToFeed";
const SUBSCRIBE_CONTENT_OPERATION_ID: &str = "subscribeContentScrapersToFeed";
const INITIAL_BACKFILL_COUNT: i64 = 2;
const BACKFILL_SUPPORTED_TYPES: [&str; 3] = ["substack", "atom", "podcast_rss"];

pub(super) fn router() -> Router<AppState> {
    Router::new()
        .route(
            "/api/scrapers",
            get(list_scraper_configs).post(create_scraper_config),
        )
        .route(
            "/api/scrapers/",
            get(list_scraper_configs).post(create_scraper_config),
        )
        .route(
            "/api/scrapers/{config_id}",
            put(update_scraper_config).delete(delete_scraper_config_endpoint),
        )
        .route("/api/scrapers/subscribe", post(subscribe_to_feed))
        .route(
            "/api/content/scrapers",
            get(list_content_scraper_configs).post(create_content_scraper_config),
        )
        .route(
            "/api/content/scrapers/",
            get(list_content_scraper_configs).post(create_content_scraper_config),
        )
        .route(
            "/api/content/scrapers/{config_id}",
            put(update_content_scraper_config).delete(delete_content_scraper_config_endpoint),
        )
        .route(
            "/api/content/scrapers/subscribe",
            post(subscribe_content_to_feed),
        )
}

#[derive(Debug, Deserialize)]
pub(super) struct ScraperConfigListQuery {
    #[serde(rename = "type")]
    scraper_type: Option<String>,
    types: Option<String>,
    #[serde(default = "default_include_stats")]
    include_stats: bool,
}

const fn default_include_stats() -> bool {
    true
}

#[utoipa::path(
    get,
    path = "/api/scrapers/",
    operation_id = "listScraperConfigs",
    tag = "scrapers",
    params(
        ("type" = Option<String>, Query, description = "Single scraper type"),
        ("types" = Option<String>, Query, description = "Comma-separated scraper types"),
        ("include_stats" = Option<bool>, Query, description = "Include derived source stats")
    ),
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, description = "Scraper configurations", body = [ScraperConfigResponse]),
        (status = 400, description = "Unsupported scraper type", body = newsly_contracts::ErrorEnvelope),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 422, description = "Validation Error", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn list_scraper_configs(
    State(state): State<AppState>,
    headers: HeaderMap,
    current_user: AuthenticatedUser,
    query: Result<Query<ScraperConfigListQuery>, QueryRejection>,
) -> Result<Json<Vec<ScraperConfigResponse>>, ApiError> {
    list_configs(state, headers, current_user, query).await
}

#[utoipa::path(
    get,
    path = "/api/content/scrapers/",
    operation_id = "listContentScraperConfigs",
    tag = "scrapers",
    params(
        ("type" = Option<String>, Query, description = "Single scraper type"),
        ("types" = Option<String>, Query, description = "Comma-separated scraper types"),
        ("include_stats" = Option<bool>, Query, description = "Include derived source stats")
    ),
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, description = "Scraper configurations", body = [ScraperConfigResponse]),
        (status = 400, description = "Unsupported scraper type", body = newsly_contracts::ErrorEnvelope),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 422, description = "Validation Error", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn list_content_scraper_configs(
    State(state): State<AppState>,
    headers: HeaderMap,
    current_user: AuthenticatedUser,
    query: Result<Query<ScraperConfigListQuery>, QueryRejection>,
) -> Result<Json<Vec<ScraperConfigResponse>>, ApiError> {
    list_configs(state, headers, current_user, query).await
}

async fn list_configs(
    state: AppState,
    headers: HeaderMap,
    current_user: AuthenticatedUser,
    query: Result<Query<ScraperConfigListQuery>, QueryRejection>,
) -> Result<Json<Vec<ScraperConfigResponse>>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    let Query(query) = decode_query(query, &request_id)?;
    let requested_types = requested_types(&query, &request_id)?;
    let configs = load_scraper_configs(
        state.database.pool(),
        current_user.id,
        requested_types.as_deref(),
    )
    .await
    .map_err(|error| internal_error(error, &request_id))?;
    let stats_by_config = if query.include_stats {
        get_scraper_config_stats(
            state.database.pool(),
            current_user.id,
            &configs,
            state.checkout_timeout,
        )
        .await
        .map_err(|error| internal_error(error, &request_id))?
    } else {
        HashMap::new()
    };
    Ok(Json(
        configs
            .into_iter()
            .map(|config| {
                let config_stats = query
                    .include_stats
                    .then(|| stats_by_config.get(&config.id))
                    .flatten();
                config_response(config, config_stats)
            })
            .collect(),
    ))
}

#[utoipa::path(
    post,
    path = "/api/scrapers/",
    operation_id = "createScraperConfig",
    tag = "scrapers",
    request_body = CreateUserScraperConfig,
    security(("HTTPBearer" = [])),
    responses(
        (status = 201, description = "Scraper config created", body = ScraperConfigResponse),
        (status = 400, description = "Invalid or duplicate scraper config", body = newsly_contracts::ErrorEnvelope),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 409, description = "Stale runtime owner", body = newsly_contracts::ErrorEnvelope),
        (status = 422, description = "Validation Error", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn create_scraper_config(
    State(state): State<AppState>,
    headers: HeaderMap,
    current_user: AuthenticatedUser,
    Extension(stamp): Extension<RouteOwnershipStamp>,
    payload: Result<Json<CreateUserScraperConfig>, JsonRejection>,
) -> Result<(StatusCode, Json<ScraperConfigResponse>), ApiError> {
    create_config(
        state,
        headers,
        current_user,
        stamp,
        payload,
        CREATE_OPERATION_ID,
    )
    .await
}

#[utoipa::path(
    post,
    path = "/api/content/scrapers/",
    operation_id = "createContentScraperConfig",
    tag = "scrapers",
    request_body = CreateUserScraperConfig,
    security(("HTTPBearer" = [])),
    responses(
        (status = 201, description = "Scraper config created", body = ScraperConfigResponse),
        (status = 400, description = "Invalid or duplicate scraper config", body = newsly_contracts::ErrorEnvelope),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 409, description = "Stale runtime owner", body = newsly_contracts::ErrorEnvelope),
        (status = 422, description = "Validation Error", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn create_content_scraper_config(
    State(state): State<AppState>,
    headers: HeaderMap,
    current_user: AuthenticatedUser,
    Extension(stamp): Extension<RouteOwnershipStamp>,
    payload: Result<Json<CreateUserScraperConfig>, JsonRejection>,
) -> Result<(StatusCode, Json<ScraperConfigResponse>), ApiError> {
    create_config(
        state,
        headers,
        current_user,
        stamp,
        payload,
        CREATE_CONTENT_OPERATION_ID,
    )
    .await
}

async fn create_config(
    state: AppState,
    headers: HeaderMap,
    current_user: AuthenticatedUser,
    stamp: RouteOwnershipStamp,
    payload: Result<Json<CreateUserScraperConfig>, JsonRejection>,
    operation_id: &str,
) -> Result<(StatusCode, Json<ScraperConfigResponse>), ApiError> {
    let request_id = request_id_from_headers(&headers);
    require_operation(&stamp, operation_id, &request_id)?;
    let Json(payload) = decode_json(payload, &request_id)?;
    validate_display_name(payload.display_name.as_deref())
        .map_err(|error| validation_error(error, &request_id))?;
    let scraper_type = payload.scraper_type.as_str();
    let mut config = normalize_create_input(payload.scraper_type, payload.config)
        .map_err(|error| validation_error(error, &request_id))?;
    let requested_feed_url = feed_url(&config).to_owned();
    let exists = scraper_config_identity_exists(
        state.database.pool(),
        current_user.id,
        scraper_type,
        &canonicalize_feed_url(&requested_feed_url),
    )
    .await
    .map_err(|error| internal_error(error, &request_id))?;
    if exists {
        return Err(bad_request(
            "Scraper config already exists for this feed",
            &request_id,
        ));
    }
    if requires_feed_probe(scraper_type) {
        let effective_url = state
            .feed_validator
            .validate_feed_url(&requested_feed_url)
            .await
            .map_err(|error| internal_error(error, &request_id))?
            .ok_or_else(|| {
                bad_request(
                    "config.feed_url must be a valid RSS/Atom feed URL",
                    &request_id,
                )
            })?;
        apply_validated_feed_url(&mut config, &effective_url);
    }
    let config_value = Value::Object(config);
    let normalized_feed_url = canonicalize_feed_url(
        config_value
            .get("feed_url")
            .and_then(Value::as_str)
            .unwrap_or_default(),
    );
    let mut transaction = state
        .database
        .pool()
        .begin()
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    verify_stamp(&mut transaction, &stamp, &request_id).await?;
    let record = persist_scraper_config(
        &mut transaction,
        &NewScraperConfig {
            user_id: current_user.id,
            scraper_type,
            display_name: payload.display_name.as_deref(),
            feed_url: &normalized_feed_url,
            config: &config_value,
            is_active: payload.is_active,
        },
    )
    .await
    .map_err(|error| match error {
        ScraperConfigRepositoryError::AlreadyExists => {
            bad_request("Scraper config already exists for this feed", &request_id)
        }
        other => internal_error(other, &request_id),
    })?;
    transaction
        .commit()
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    let record_stats = stats_for_record(&state, current_user.id, &record, &request_id).await?;
    Ok((
        StatusCode::CREATED,
        Json(config_response(record, Some(&record_stats))),
    ))
}

#[utoipa::path(
    post,
    path = "/api/scrapers/subscribe",
    operation_id = "subscribeScrapersToFeed",
    tag = "scrapers",
    request_body = SubscribeToFeedRequest,
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, description = "Existing subscription reactivated or already active", body = ScraperConfigResponse),
        (status = 201, description = "Feed subscription created", body = ScraperConfigResponse),
        (status = 400, description = "Invalid or unsupported feed", body = newsly_contracts::ErrorEnvelope),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 409, description = "Stale runtime owner", body = newsly_contracts::ErrorEnvelope),
        (status = 422, description = "Validation Error", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn subscribe_to_feed(
    State(state): State<AppState>,
    headers: HeaderMap,
    current_user: AuthenticatedUser,
    Extension(stamp): Extension<RouteOwnershipStamp>,
    payload: Result<Json<SubscribeToFeedRequest>, JsonRejection>,
) -> Result<(StatusCode, Json<ScraperConfigResponse>), ApiError> {
    subscribe(
        state,
        headers,
        current_user,
        stamp,
        payload,
        SUBSCRIBE_OPERATION_ID,
    )
    .await
}

#[utoipa::path(
    post,
    path = "/api/content/scrapers/subscribe",
    operation_id = "subscribeContentScrapersToFeed",
    tag = "scrapers",
    request_body = SubscribeToFeedRequest,
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, description = "Existing subscription reactivated or already active", body = ScraperConfigResponse),
        (status = 201, description = "Feed subscription created", body = ScraperConfigResponse),
        (status = 400, description = "Invalid or unsupported feed", body = newsly_contracts::ErrorEnvelope),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 409, description = "Stale runtime owner", body = newsly_contracts::ErrorEnvelope),
        (status = 422, description = "Validation Error", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn subscribe_content_to_feed(
    State(state): State<AppState>,
    headers: HeaderMap,
    current_user: AuthenticatedUser,
    Extension(stamp): Extension<RouteOwnershipStamp>,
    payload: Result<Json<SubscribeToFeedRequest>, JsonRejection>,
) -> Result<(StatusCode, Json<ScraperConfigResponse>), ApiError> {
    subscribe(
        state,
        headers,
        current_user,
        stamp,
        payload,
        SUBSCRIBE_CONTENT_OPERATION_ID,
    )
    .await
}

#[expect(
    clippy::too_many_lines,
    reason = "subscription keeps external feed validation outside the fenced finalize transaction"
)]
async fn subscribe(
    state: AppState,
    headers: HeaderMap,
    current_user: AuthenticatedUser,
    stamp: RouteOwnershipStamp,
    payload: Result<Json<SubscribeToFeedRequest>, JsonRejection>,
    operation_id: &str,
) -> Result<(StatusCode, Json<ScraperConfigResponse>), ApiError> {
    let request_id = request_id_from_headers(&headers);
    require_operation(&stamp, operation_id, &request_id)?;
    let Json(payload) = decode_json(payload, &request_id)?;
    let scraper_type = subscription_scraper_type(&payload.feed_type, &request_id)?;
    validate_display_name(payload.display_name.as_deref())
        .map_err(|error| bad_request(error.to_string(), &request_id))?;
    let mut config = subscription_config(scraper_type, &payload.feed_url, &request_id)?;
    let requested_feed_url = canonicalize_feed_url(feed_url(&config));

    if let Some(existing) = find_subscription_candidate(
        &state,
        current_user.id,
        scraper_type.as_str(),
        &requested_feed_url,
        &request_id,
    )
    .await?
    {
        return finalize_existing_subscription(
            &state,
            &stamp,
            existing,
            current_user.id,
            &requested_feed_url,
            &request_id,
        )
        .await;
    }

    if requires_feed_probe(scraper_type.as_str()) {
        let effective_url = state
            .feed_validator
            .validate_feed_url(&requested_feed_url)
            .await
            .map_err(|error| internal_error(error, &request_id))?
            .ok_or_else(|| {
                bad_request("feed_url must be a valid RSS/Atom feed URL", &request_id)
            })?;
        apply_validated_feed_url(&mut config, &effective_url);
    }
    let normalized_feed_url = canonicalize_feed_url(feed_url(&config));
    let config_value = Value::Object(config);
    let mut transaction = state
        .database
        .pool()
        .begin()
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    verify_stamp(&mut transaction, &stamp, &request_id).await?;
    let created = persist_scraper_config(
        &mut transaction,
        &NewScraperConfig {
            user_id: current_user.id,
            scraper_type: scraper_type.as_str(),
            display_name: payload.display_name.as_deref(),
            feed_url: &normalized_feed_url,
            config: &config_value,
            is_active: true,
        },
    )
    .await;
    let record = match created {
        Ok(record) => record,
        Err(ScraperConfigRepositoryError::AlreadyExists) => {
            drop(transaction);
            let existing = find_subscription_candidate(
                &state,
                current_user.id,
                scraper_type.as_str(),
                &normalized_feed_url,
                &request_id,
            )
            .await?
            .ok_or_else(|| {
                internal_error(
                    "concurrent scraper subscription was not visible after conflict",
                    &request_id,
                )
            })?;
            return finalize_existing_subscription(
                &state,
                &stamp,
                existing,
                current_user.id,
                &normalized_feed_url,
                &request_id,
            )
            .await;
        }
        Err(error) => return Err(internal_error(error, &request_id)),
    };
    let backfill_task_id = enqueue_initial_backfill(
        &state,
        &mut transaction,
        current_user.id,
        &record,
        &request_id,
    )
    .await?;
    transaction
        .commit()
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    present_subscription(
        &state,
        current_user.id,
        record,
        FeedSubscriptionOutcome::Created,
        backfill_task_id,
        &request_id,
    )
    .await
}

async fn find_subscription_candidate(
    state: &AppState,
    user_id: i64,
    scraper_type: &str,
    canonical_feed_url: &str,
    request_id: &str,
) -> Result<Option<ScraperConfigProjection>, ApiError> {
    let allowed_types = vec![scraper_type.to_owned()];
    let candidates = load_scraper_configs(state.database.pool(), user_id, Some(&allowed_types))
        .await
        .map_err(|error| internal_error(error, request_id))?;
    Ok(candidates.into_iter().find(|candidate| {
        candidate
            .feed_url
            .as_deref()
            .or_else(|| candidate.config.get("feed_url").and_then(Value::as_str))
            .is_some_and(|value| canonicalize_feed_url(value) == canonical_feed_url)
    }))
}

async fn finalize_existing_subscription(
    state: &AppState,
    stamp: &RouteOwnershipStamp,
    existing: ScraperConfigProjection,
    user_id: i64,
    canonical_feed_url: &str,
    request_id: &str,
) -> Result<(StatusCode, Json<ScraperConfigResponse>), ApiError> {
    let mut transaction = state
        .database
        .pool()
        .begin()
        .await
        .map_err(|error| internal_error(error, request_id))?;
    verify_stamp(&mut transaction, stamp, request_id).await?;
    let locked = persist_scraper_config_update(
        &mut transaction,
        user_id,
        existing.id,
        &existing.scraper_type,
        &ScraperConfigPatch::default(),
    )
    .await
    .map_err(|error| internal_error(error, request_id))?;
    let (record, outcome, backfill_task_id) = if locked.is_active {
        (locked, FeedSubscriptionOutcome::AlreadySubscribed, None)
    } else {
        let patch = ScraperConfigPatch {
            feed_url: locked
                .feed_url
                .as_deref()
                .filter(|value| !value.trim().is_empty())
                .is_none()
                .then_some(canonical_feed_url),
            is_active: Some(true),
            ..ScraperConfigPatch::default()
        };
        let record = persist_scraper_config_update(
            &mut transaction,
            user_id,
            locked.id,
            &locked.scraper_type,
            &patch,
        )
        .await
        .map_err(|error| internal_error(error, request_id))?;
        let task_id =
            enqueue_initial_backfill(state, &mut transaction, user_id, &record, request_id).await?;
        (record, FeedSubscriptionOutcome::Reactivated, task_id)
    };
    transaction
        .commit()
        .await
        .map_err(|error| internal_error(error, request_id))?;
    present_subscription(
        state,
        user_id,
        record,
        outcome,
        backfill_task_id,
        request_id,
    )
    .await
}

async fn enqueue_initial_backfill(
    state: &AppState,
    transaction: &mut sqlx::Transaction<'_, sqlx::Postgres>,
    user_id: i64,
    config: &ScraperConfigProjection,
    request_id: &str,
) -> Result<Option<i64>, ApiError> {
    if !BACKFILL_SUPPORTED_TYPES.contains(&config.scraper_type.as_str()) {
        return Ok(None);
    }
    let mut request = EnqueueRequest::new(TaskType::BackfillFeeds);
    request.payload = json!({
        "user_id": user_id,
        "config_ids": [config.id],
        "count": INITIAL_BACKFILL_COUNT,
    })
    .as_object()
    .cloned();
    request.dedupe = Some(true);
    request.owner_user_id = Some(user_id);
    let result = QueueKernel::new(state.database.pool().clone())
        .enqueue_many_in_transaction(transaction, vec![request])
        .await
        .map_err(|error| internal_error(error, request_id))?;
    Ok(result.task_ids.into_iter().next())
}

async fn present_subscription(
    state: &AppState,
    user_id: i64,
    record: ScraperConfigProjection,
    outcome: FeedSubscriptionOutcome,
    backfill_task_id: Option<i64>,
    request_id: &str,
) -> Result<(StatusCode, Json<ScraperConfigResponse>), ApiError> {
    let scraper_stats = stats_for_record(state, user_id, &record, request_id).await?;
    let status = if outcome == FeedSubscriptionOutcome::Created {
        StatusCode::CREATED
    } else {
        StatusCode::OK
    };
    let mut response = config_response(record, Some(&scraper_stats));
    response.subscription_outcome = Some(outcome);
    response.backfill_task_id = backfill_task_id;
    Ok((status, Json(response)))
}

fn subscription_scraper_type(feed_type: &str, request_id: &str) -> Result<ScraperType, ApiError> {
    match feed_type {
        "substack" => Ok(ScraperType::Substack),
        "atom" => Ok(ScraperType::Atom),
        "podcast_rss" => Ok(ScraperType::PodcastRss),
        "youtube" => Ok(ScraperType::Youtube),
        "reddit" => Ok(ScraperType::Reddit),
        "aggregator" => Ok(ScraperType::Aggregator),
        other => Err(bad_request(
            format!("Unsupported feed type: {other}"),
            request_id,
        )),
    }
}

fn subscription_config(
    scraper_type: ScraperType,
    feed_url_value: &str,
    request_id: &str,
) -> Result<serde_json::Map<String, Value>, ApiError> {
    let mut config = serde_json::Map::new();
    if scraper_type == ScraperType::Reddit {
        let subreddit = reddit_subreddit(feed_url_value).ok_or_else(|| {
            bad_request(
                "Reddit subscriptions require an /r/<subreddit> URL",
                request_id,
            )
        })?;
        config.insert("subreddit".to_owned(), Value::String(subreddit));
    } else {
        config.insert(
            "feed_url".to_owned(),
            Value::String(feed_url_value.to_owned()),
        );
    }
    normalize_create_input(scraper_type, config)
        .map_err(|error| bad_request(error.to_string(), request_id))
}

fn reddit_subreddit(value: &str) -> Option<String> {
    let url = reqwest::Url::parse(value.trim()).ok()?;
    let mut segments = url.path_segments()?;
    let prefix = segments.next()?;
    let subreddit = segments.next()?.trim();
    (prefix.eq_ignore_ascii_case("r") && !subreddit.is_empty()).then(|| subreddit.to_owned())
}

#[utoipa::path(
    put,
    path = "/api/scrapers/{config_id}",
    operation_id = "updateScraperConfig",
    tag = "scrapers",
    params(("config_id" = i64, Path, description = "Scraper config ID")),
    request_body = UpdateUserScraperConfig,
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, description = "Scraper config updated", body = ScraperConfigResponse),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 404, description = "Scraper config not found or invalid", body = newsly_contracts::ErrorEnvelope),
        (status = 409, description = "Stale runtime owner", body = newsly_contracts::ErrorEnvelope),
        (status = 422, description = "Validation Error", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn update_scraper_config(
    State(state): State<AppState>,
    Path(config_id): Path<i64>,
    headers: HeaderMap,
    current_user: AuthenticatedUser,
    Extension(stamp): Extension<RouteOwnershipStamp>,
    payload: Result<Json<UpdateUserScraperConfig>, JsonRejection>,
) -> Result<Json<ScraperConfigResponse>, ApiError> {
    update_config(
        state,
        config_id,
        headers,
        current_user,
        stamp,
        payload,
        UPDATE_OPERATION_ID,
    )
    .await
}

#[utoipa::path(
    put,
    path = "/api/content/scrapers/{config_id}",
    operation_id = "updateContentScraperConfig",
    tag = "scrapers",
    params(("config_id" = i64, Path, description = "Scraper config ID")),
    request_body = UpdateUserScraperConfig,
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, description = "Scraper config updated", body = ScraperConfigResponse),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 404, description = "Scraper config not found or invalid", body = newsly_contracts::ErrorEnvelope),
        (status = 409, description = "Stale runtime owner", body = newsly_contracts::ErrorEnvelope),
        (status = 422, description = "Validation Error", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn update_content_scraper_config(
    State(state): State<AppState>,
    Path(config_id): Path<i64>,
    headers: HeaderMap,
    current_user: AuthenticatedUser,
    Extension(stamp): Extension<RouteOwnershipStamp>,
    payload: Result<Json<UpdateUserScraperConfig>, JsonRejection>,
) -> Result<Json<ScraperConfigResponse>, ApiError> {
    update_config(
        state,
        config_id,
        headers,
        current_user,
        stamp,
        payload,
        UPDATE_CONTENT_OPERATION_ID,
    )
    .await
}

#[allow(clippy::too_many_arguments)]
async fn update_config(
    state: AppState,
    config_id: i64,
    headers: HeaderMap,
    current_user: AuthenticatedUser,
    stamp: RouteOwnershipStamp,
    payload: Result<Json<UpdateUserScraperConfig>, JsonRejection>,
    operation_id: &str,
) -> Result<Json<ScraperConfigResponse>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    require_operation(&stamp, operation_id, &request_id)?;
    let Json(payload) = decode_json(payload, &request_id)?;
    validate_display_name(payload.display_name.as_deref())
        .map_err(|error| validation_error(error, &request_id))?;
    let generic_config = payload
        .config
        .map(normalize_update_input)
        .transpose()
        .map_err(|error| validation_error(error, &request_id))?;
    let prepared = find_scraper_config(state.database.pool(), current_user.id, config_id)
        .await
        .map_err(|error| internal_error(error, &request_id))?
        .ok_or_else(|| not_found_error("Scraper config not found", &request_id))?;
    let mut config = generic_config
        .map(|config| normalize_for_stored_type(&prepared.scraper_type, config))
        .transpose()
        .map_err(|error| not_found_error(error.to_string(), &request_id))?;
    if let Some(config) = config.as_mut()
        && requires_feed_probe(&prepared.scraper_type)
    {
        let requested_feed_url = feed_url(config).to_owned();
        let effective_url = state
            .feed_validator
            .validate_feed_url(&requested_feed_url)
            .await
            .map_err(|error| internal_error(error, &request_id))?
            .ok_or_else(|| {
                not_found_error(
                    "config.feed_url must be a valid RSS/Atom feed URL",
                    &request_id,
                )
            })?;
        apply_validated_feed_url(config, &effective_url);
    }
    let config_value = config.map(Value::Object);
    let normalized_feed_url = config_value
        .as_ref()
        .and_then(|config| config.get("feed_url"))
        .and_then(Value::as_str)
        .map(canonicalize_feed_url);
    let mut transaction = state
        .database
        .pool()
        .begin()
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    verify_stamp(&mut transaction, &stamp, &request_id).await?;
    let record = persist_scraper_config_update(
        &mut transaction,
        current_user.id,
        config_id,
        &prepared.scraper_type,
        &ScraperConfigPatch {
            display_name: payload.display_name.as_deref(),
            feed_url: normalized_feed_url.as_deref(),
            config: config_value.as_ref(),
            is_active: payload.is_active,
        },
    )
    .await
    .map_err(|error| match error {
        ScraperConfigRepositoryError::NotFound => {
            not_found_error("Scraper config not found", &request_id)
        }
        ScraperConfigRepositoryError::AlreadyExists => {
            not_found_error("Scraper config already exists for this feed", &request_id)
        }
        ScraperConfigRepositoryError::ChangedDuringPrepare => ApiError::new(
            StatusCode::CONFLICT,
            "stale_resource",
            "Scraper config changed during validation",
            request_id.clone(),
        )
        .with_retryable(true),
        other => internal_error(other, &request_id),
    })?;
    transaction
        .commit()
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    let record_stats = stats_for_record(&state, current_user.id, &record, &request_id).await?;
    Ok(Json(config_response(record, Some(&record_stats))))
}

#[utoipa::path(
    delete,
    path = "/api/scrapers/{config_id}",
    operation_id = "deleteScraperConfigEndpoint",
    tag = "scrapers",
    params(("config_id" = i64, Path, description = "Scraper config ID")),
    security(("HTTPBearer" = [])),
    responses(
        (status = 204, description = "Scraper config deleted"),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 404, description = "Scraper config not found", body = newsly_contracts::ErrorEnvelope),
        (status = 409, description = "Stale runtime owner", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn delete_scraper_config_endpoint(
    State(state): State<AppState>,
    Path(config_id): Path<i64>,
    headers: HeaderMap,
    current_user: AuthenticatedUser,
    Extension(stamp): Extension<RouteOwnershipStamp>,
) -> Result<StatusCode, ApiError> {
    delete_config(
        state,
        config_id,
        headers,
        current_user,
        stamp,
        DELETE_OPERATION_ID,
    )
    .await
}

#[utoipa::path(
    delete,
    path = "/api/content/scrapers/{config_id}",
    operation_id = "deleteContentScraperConfigEndpoint",
    tag = "scrapers",
    params(("config_id" = i64, Path, description = "Scraper config ID")),
    security(("HTTPBearer" = [])),
    responses(
        (status = 204, description = "Scraper config deleted"),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 404, description = "Scraper config not found", body = newsly_contracts::ErrorEnvelope),
        (status = 409, description = "Stale runtime owner", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn delete_content_scraper_config_endpoint(
    State(state): State<AppState>,
    Path(config_id): Path<i64>,
    headers: HeaderMap,
    current_user: AuthenticatedUser,
    Extension(stamp): Extension<RouteOwnershipStamp>,
) -> Result<StatusCode, ApiError> {
    delete_config(
        state,
        config_id,
        headers,
        current_user,
        stamp,
        DELETE_CONTENT_OPERATION_ID,
    )
    .await
}

async fn delete_config(
    state: AppState,
    config_id: i64,
    headers: HeaderMap,
    current_user: AuthenticatedUser,
    stamp: RouteOwnershipStamp,
    operation_id: &str,
) -> Result<StatusCode, ApiError> {
    let request_id = request_id_from_headers(&headers);
    require_operation(&stamp, operation_id, &request_id)?;
    let mut transaction = state
        .database
        .pool()
        .begin()
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    verify_stamp(&mut transaction, &stamp, &request_id).await?;
    persist_scraper_config_deletion(&mut transaction, current_user.id, config_id)
        .await
        .map_err(|error| match error {
            ScraperConfigRepositoryError::NotFound => {
                not_found_error("Scraper config not found", &request_id)
            }
            other => internal_error(other, &request_id),
        })?;
    transaction
        .commit()
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    Ok(StatusCode::NO_CONTENT)
}

#[allow(clippy::result_large_err)]
fn requested_types(
    query: &ScraperConfigListQuery,
    request_id: &str,
) -> Result<Option<Vec<String>>, ApiError> {
    let mut requested = BTreeSet::new();
    if let Some(scraper_type) = query
        .scraper_type
        .as_deref()
        .filter(|scraper_type| !scraper_type.is_empty())
    {
        requested.insert(scraper_type.to_owned());
    }
    if let Some(types) = query.types.as_deref().filter(|types| !types.is_empty()) {
        requested.extend(
            types
                .split(',')
                .filter(|scraper_type| !scraper_type.is_empty())
                .map(str::to_owned),
        );
    }
    let invalid = requested
        .iter()
        .filter(|value| !ALLOWED_SCRAPER_TYPES.contains(&value.as_str()))
        .cloned()
        .collect::<Vec<_>>();
    if !invalid.is_empty() {
        return Err(bad_request(
            format!("Unsupported scraper types: {}", invalid.join(", ")),
            request_id,
        ));
    }
    Ok((!requested.is_empty()).then(|| requested.into_iter().collect()))
}

async fn stats_for_record(
    state: &AppState,
    user_id: i64,
    record: &ScraperConfigProjection,
    request_id: &str,
) -> Result<ScraperConfigStatsProjection, ApiError> {
    get_scraper_config_stats(
        state.database.pool(),
        user_id,
        std::slice::from_ref(record),
        state.checkout_timeout,
    )
    .await
    .map_err(|error| internal_error(error, request_id))?
    .remove(&record.id)
    .ok_or_else(|| internal_error("scraper stats omitted requested config", request_id))
}

fn config_response(
    config: ScraperConfigProjection,
    stats: Option<&ScraperConfigStatsProjection>,
) -> ScraperConfigResponse {
    let config_data = config.config.as_object().cloned().unwrap_or_default();
    ScraperConfigResponse {
        id: config.id,
        scraper_type: config.scraper_type,
        display_name: config.display_name,
        feed_url: config_data
            .get("feed_url")
            .and_then(Value::as_str)
            .map(str::to_owned),
        limit: response_limit(&config_data),
        config: config_data,
        is_active: config.is_active,
        created_at: config.created_at,
        stats: stats.map(stats_response),
        subscription_outcome: None,
        backfill_task_id: None,
    }
}

fn stats_response(stats: &ScraperConfigStatsProjection) -> ScraperConfigStatsResponse {
    ScraperConfigStatsResponse {
        total_count: stats.total_count,
        completed_count: stats.completed_count,
        unread_count: stats.unread_count,
        processing_count: stats.processing_count,
        latest_processed_at: stats.latest_processed_at,
        latest_publication_at: stats.latest_publication_at,
        next_expected_at: stats.next_expected_at,
        average_interval_hours: stats.average_interval_hours,
        interval_sample_size: stats.interval_sample_size,
    }
}

#[allow(clippy::result_large_err)]
fn decode_query<T>(
    query: Result<Query<T>, QueryRejection>,
    request_id: &str,
) -> Result<Query<T>, ApiError> {
    query.map_err(|rejection| validation_error(rejection.body_text(), request_id))
}

fn validation_error(error: impl std::fmt::Display, request_id: &str) -> ApiError {
    ApiError::new(
        StatusCode::UNPROCESSABLE_ENTITY,
        "validation_error",
        "Request validation failed",
        request_id.to_owned(),
    )
    .with_details(
        json!({"errors": [{"message": error.to_string()}]})
            .as_object()
            .expect("validation details are an object")
            .clone(),
    )
}

fn not_found_error(message: impl Into<String>, request_id: &str) -> ApiError {
    ApiError::new(
        StatusCode::NOT_FOUND,
        "not_found",
        message,
        request_id.to_owned(),
    )
}
