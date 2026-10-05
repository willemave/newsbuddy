use std::str::FromStr;
use std::sync::Arc;
use std::time::Duration;

use axum::extract::rejection::{JsonRejection, PathRejection, QueryRejection};
use axum::extract::{Path, Query, State};
use axum::http::{HeaderMap, StatusCode};
use axum::routing::{get, post};
use axum::{Json, Router};
use chrono::{DateTime, Utc};
use newsly_contracts::{
    ContentStatus, ContentType, ConvertNewsItemResponse, ConvertNewsResponse,
    MixedSearchFeedResultResponse, MixedSearchResponse, PaginationMetadata,
    PodcastEpisodeSearchResultResponse, SubmissionContentResult,
    SubmissionFeedSubscriptionResponse, SubmissionFeedSubscriptionResult, SubmissionKind,
    SubmissionLearningDeckResult, SubmissionNoActionResult, SubmissionOutcome, SubmissionResult,
    SubmissionStatusListResponse, SubmissionStatusResponse, TweetSuggestion,
    TweetSuggestionsRequest, TweetSuggestionsResponse,
};
use newsly_db::{
    ContentMiscRepositoryError, SubmissionProjection, finalize_article_conversion,
    find_visible_content_detail, list_active_feed_urls, list_submission_projections,
    prepare_content_conversion, prepare_news_conversion, search_visible_content,
};
use newsly_providers::{ContentMiscGatewayError, FeedDiscoveryHit, PodcastEpisodeHit};
use newsly_queue::{EnqueueRequest, QueueError, QueueKernel, TaskType};
use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::auth::AuthenticatedUser;
use crate::content_read::presentation;
use crate::error::ApiError;
use crate::gateway::RouteOwnershipStamp;
use crate::model_usage::ApiAgentUsageSink;
use crate::pagination_cursor::{self, InvalidCursor};
use crate::write_support::{bad_request, decode_json, internal_error};
use crate::{AppState, request_id_from_headers};

pub(crate) mod download_more;

pub(crate) use download_more::download_more_from_series;

pub(super) fn router() -> Router<AppState> {
    Router::new()
        .route(
            "/api/content/{content_id}/convert-to-article",
            post(convert_content_news_to_article),
        )
        .route(
            "/api/news/items/{news_item_id}/convert-to-article",
            post(convert_news_item_to_article),
        )
        .route(
            "/api/content/{content_id}/download-more",
            post(download_more_from_series),
        )
        .route(
            "/api/content/{content_id}/tweet-suggestions",
            post(get_tweet_suggestions),
        )
        .route(
            "/api/content/submissions/list",
            get(list_submission_statuses),
        )
        .route("/api/content/search/mixed", get(search_mixed_contents))
}

#[utoipa::path(
    post,
    path = "/api/content/{content_id}/convert-to-article",
    operation_id = "convertContentNewsToArticle",
    tag = "content",
    params(("content_id" = i64, Path, minimum = 1)),
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, description = "News link converted successfully", body = ConvertNewsResponse),
        (status = 400, description = "Content cannot be converted", body = newsly_contracts::ErrorEnvelope),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 404, description = "Content not found", body = newsly_contracts::ErrorEnvelope),
        (status = 409, description = "Stale runtime owner", body = newsly_contracts::ErrorEnvelope),
        (status = 422, description = "Validation Error", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn convert_content_news_to_article(
    State(state): State<AppState>,
    headers: HeaderMap,
    path: Result<Path<i64>, PathRejection>,
    current_user: AuthenticatedUser,
    stamp: RouteOwnershipStamp,
) -> Result<Json<ConvertNewsResponse>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    let content_id = positive_path_id(path, "content_id", &request_id)?;
    let plan = prepare_content_conversion(state.database.pool(), current_user.id, content_id)
        .await
        .map_err(|error| conversion_error(error, &request_id))?
        .ok_or_else(|| not_found_message("Content not found", &request_id))?;
    let article_url = normalize_http_url(&plan.article_url, &request_id)?;
    let converted = finalize_conversion(
        &state,
        &stamp,
        current_user.id,
        &article_url,
        plan.title.as_deref(),
        plan.source.as_deref(),
        None,
        None,
        &request_id,
    )
    .await?;
    Ok(Json(ConvertNewsResponse {
        status: "success".to_owned(),
        new_content_id: converted.content_id,
        original_content_id: plan.original_content_id,
        already_exists: converted.already_exists,
        message: conversion_message(converted.already_exists),
    }))
}

#[utoipa::path(
    post,
    path = "/api/news/items/{news_item_id}/convert-to-article",
    operation_id = "convertNewsItemToArticle",
    tag = "news",
    params(("news_item_id" = i64, Path, minimum = 1)),
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, description = "Successful Response", body = ConvertNewsItemResponse),
        (status = 400, description = "News item cannot be converted", body = newsly_contracts::ErrorEnvelope),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 404, description = "News item not found", body = newsly_contracts::ErrorEnvelope),
        (status = 409, description = "Stale runtime owner", body = newsly_contracts::ErrorEnvelope),
        (status = 422, description = "Validation Error", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn convert_news_item_to_article(
    State(state): State<AppState>,
    headers: HeaderMap,
    path: Result<Path<i64>, PathRejection>,
    current_user: AuthenticatedUser,
    stamp: RouteOwnershipStamp,
) -> Result<Json<ConvertNewsItemResponse>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    let news_item_id = positive_path_id(path, "news_item_id", &request_id)?;
    let plan = prepare_news_conversion(state.database.pool(), current_user.id, news_item_id)
        .await
        .map_err(|error| conversion_error(error, &request_id))?
        .ok_or_else(|| not_found_message("News item not found", &request_id))?;
    let article_url = normalize_http_url(&plan.article_url, &request_id)?;
    let converted = finalize_conversion(
        &state,
        &stamp,
        current_user.id,
        &article_url,
        plan.title.as_deref(),
        plan.source.as_deref(),
        plan.published_at,
        Some(&plan.raw_metadata),
        &request_id,
    )
    .await?;
    Ok(Json(ConvertNewsItemResponse {
        status: "success".to_owned(),
        news_item_id: plan.news_item_id,
        new_content_id: converted.content_id,
        already_exists: converted.already_exists,
        message: conversion_message(converted.already_exists),
    }))
}

#[allow(clippy::too_many_arguments)]
async fn finalize_conversion(
    state: &AppState,
    stamp: &RouteOwnershipStamp,
    user_id: i64,
    article_url: &str,
    title: Option<&str>,
    source: Option<&str>,
    published_at: Option<DateTime<Utc>>,
    news_metadata: Option<&Value>,
    request_id: &str,
) -> Result<newsly_db::ConvertedArticle, ApiError> {
    let mut transaction =
        crate::write_support::begin_fenced(state.database.pool(), stamp, request_id).await?;
    let converted = finalize_article_conversion(
        &mut transaction,
        user_id,
        article_url,
        title,
        source,
        published_at,
        news_metadata,
    )
    .await
    .map_err(|error| internal_error(error, request_id))?;
    let mut requests = Vec::new();
    if !converted.already_exists {
        let mut request = EnqueueRequest::new(if converted.reused_body {
            TaskType::Summarize
        } else {
            TaskType::ProcessContent
        });
        request.content_id = Some(converted.content_id);
        requests.push(request);
    }
    QueueKernel::new(state.database.pool().clone())
        .enqueue_many_in_transaction(&mut transaction, requests)
        .await
        .map_err(|error| queue_error(error, request_id))?;
    crate::write_support::commit(transaction, request_id).await?;
    Ok(converted)
}

#[utoipa::path(
    post,
    path = "/api/content/{content_id}/tweet-suggestions",
    operation_id = "getContentTweetSuggestions",
    tag = "content",
    params(("content_id" = i64, Path, minimum = 1)),
    request_body = TweetSuggestionsRequest,
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, description = "Tweet suggestions generated", body = TweetSuggestionsResponse),
        (status = 400, description = "Content not ready", body = newsly_contracts::ErrorEnvelope),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 404, description = "Content not found", body = newsly_contracts::ErrorEnvelope),
        (status = 409, description = "Stale runtime owner", body = newsly_contracts::ErrorEnvelope),
        (status = 422, description = "Validation Error", body = newsly_contracts::ErrorEnvelope),
        (status = 502, description = "LLM generation failed", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn get_tweet_suggestions(
    State(state): State<AppState>,
    headers: HeaderMap,
    path: Result<Path<i64>, PathRejection>,
    current_user: AuthenticatedUser,
    _stamp: RouteOwnershipStamp,
    payload: Result<Json<TweetSuggestionsRequest>, JsonRejection>,
) -> Result<Json<TweetSuggestionsResponse>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    let content_id = positive_path_id(path, "content_id", &request_id)?;
    let Json(payload) = decode_json(payload, &request_id)?;
    if !(1..=10).contains(&payload.creativity) {
        return Err(validation_error(
            "creativity must be between 1 and 10",
            &request_id,
        ));
    }
    if payload
        .message
        .as_ref()
        .is_some_and(|value| value.chars().count() > 500)
    {
        return Err(validation_error(
            "message must contain at most 500 characters",
            &request_id,
        ));
    }
    let content_detail =
        find_visible_content_detail(state.database.pool(), current_user.id, content_id)
            .await
            .map_err(|error| internal_error(error, &request_id))?
            .ok_or_else(|| not_found_message("Content not found", &request_id))?;
    if !matches!(content_detail.status.as_str(), "completed" | "failed") {
        return Err(bad_request(
            format!(
                "Content not ready for tweets (status: {})",
                content_detail.status
            ),
            &request_id,
        ));
    }
    let tweet_source = tweet_context(&content_detail);
    let usage = Arc::new(ApiAgentUsageSink::new(
        state.database.pool().clone(),
        Some(current_user.id),
        "content.tweet_suggestions",
        Some(content_id),
    ));
    let generated = state
        .content_misc
        .generate_tweet_suggestions_with_events(
            &tweet_source,
            plan_guidance(payload.message.as_deref()),
            payload.creativity,
            payload.length.as_str(),
            payload
                .llm_provider
                .map(newsly_contracts::UserLlmProvider::as_str),
            usage.clone(),
        )
        .await;
    usage.finish().await;
    let generated = generated.map_err(|error| provider_bad_gateway(&error, &request_id))?;
    Ok(Json(TweetSuggestionsResponse {
        content_id,
        creativity: payload.creativity,
        length: payload.length,
        model: generated.model,
        suggestions: generated
            .suggestions
            .into_iter()
            .map(|suggestion| TweetSuggestion {
                id: suggestion.id,
                text: suggestion.text,
                style_label: suggestion.style_label,
            })
            .collect(),
    }))
}

#[derive(Debug, Default, Deserialize)]
pub(super) struct SubmissionListQuery {
    cursor: Option<String>,
    #[serde(default = "default_submission_limit")]
    limit: usize,
}

const fn default_submission_limit() -> usize {
    25
}

#[utoipa::path(
    get,
    path = "/api/content/submissions/list",
    operation_id = "listContentSubmissionStatuses",
    tag = "content",
    params(
        ("cursor" = Option<String>, Query),
        ("limit" = Option<usize>, Query, minimum = 1, maximum = 100)
    ),
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, description = "Successful Response", body = SubmissionStatusListResponse),
        (status = 400, description = "Invalid cursor", body = newsly_contracts::ErrorEnvelope),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 422, description = "Validation Error", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn list_submission_statuses(
    State(state): State<AppState>,
    headers: HeaderMap,
    query: Result<Query<SubmissionListQuery>, QueryRejection>,
    current_user: AuthenticatedUser,
) -> Result<Json<SubmissionStatusListResponse>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    let Query(query) =
        query.map_err(|rejection| validation_error(rejection.body_text(), &request_id))?;
    if !(1..=100).contains(&query.limit) {
        return Err(validation_error(
            "limit must be between 1 and 100",
            &request_id,
        ));
    }
    let cursor = query
        .cursor
        .as_deref()
        .map(decode_submission_cursor)
        .transpose()
        .map_err(|error| bad_request(error.to_string(), &request_id))?;
    let page =
        list_submission_projections(state.database.pool(), current_user.id, cursor, query.limit)
            .await
            .map_err(|error| internal_error(error, &request_id))?;
    let next_cursor = page
        .has_more
        .then(|| page.items.last())
        .flatten()
        .map(encode_submission_cursor)
        .transpose()
        .map_err(|error| internal_error(error, &request_id))?;
    let submissions = page
        .items
        .into_iter()
        .map(present_submission)
        .collect::<Vec<_>>();
    let page_size = submissions.len();
    Ok(Json(SubmissionStatusListResponse {
        submissions,
        meta: PaginationMetadata {
            next_cursor,
            has_more: page.has_more,
            page_size,
            total: None,
        },
    }))
}

#[derive(Debug, Deserialize)]
pub(super) struct ExternalSearchQuery {
    q: String,
    #[serde(default = "default_external_limit")]
    limit: usize,
}

impl ExternalSearchQuery {
    fn validate(&self, request_id: &str) -> Result<(), ApiError> {
        if !(2..=200).contains(&self.q.chars().count()) {
            return Err(validation_error(
                "q must contain between 2 and 200 characters",
                request_id,
            ));
        }
        if !(1..=25).contains(&self.limit) {
            return Err(validation_error(
                "limit must be between 1 and 25",
                request_id,
            ));
        }
        Ok(())
    }
}

const fn default_external_limit() -> usize {
    10
}

#[utoipa::path(
    get,
    path = "/api/content/search/mixed",
    operation_id = "searchMixedContents",
    tag = "content",
    params(
        ("q" = String, Query, min_length = 2, max_length = 200),
        ("limit" = Option<usize>, Query, minimum = 1, maximum = 25)
    ),
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, description = "Successful Response", body = MixedSearchResponse),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 422, description = "Validation Error", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn search_mixed_contents(
    State(state): State<AppState>,
    headers: HeaderMap,
    query: Result<Query<ExternalSearchQuery>, QueryRejection>,
    current_user: AuthenticatedUser,
) -> Result<Json<MixedSearchResponse>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    let Query(query) = parse_external_query(query, &request_id)?;
    let local = search_visible_content(
        state.database.pool(),
        current_user.id,
        &query.q,
        None,
        None,
        0,
        query.limit,
    );
    let external = tokio::time::timeout(Duration::from_secs(8), async {
        tokio::join!(
            state
                .content_misc
                .discover_feeds(&query.q, query.limit.min(5)),
            state
                .content_misc
                .search_podcast_episodes(&query.q, query.limit)
        )
    });
    let (local, external) = tokio::join!(local, external);
    let local = local.map_err(|error| internal_error(error, &request_id))?;
    let (feed_hits, podcast_hits) = if let Ok((feeds, podcasts)) = external {
        (
            feeds.unwrap_or_else(|error| {
                tracing::warn!(error = %error, "mixed feed discovery failed");
                Vec::new()
            }),
            podcasts.unwrap_or_else(|error| {
                tracing::warn!(error = %error, "mixed podcast discovery failed");
                Vec::new()
            }),
        )
    } else {
        tracing::warn!(query = %query.q, "mixed external search timed out");
        (Vec::new(), Vec::new())
    };
    let subscribed = active_feed_urls(state.database.pool(), current_user.id, &request_id).await?;
    let content = local
        .items
        .into_iter()
        .take(query.limit)
        .filter_map(|item| {
            presentation::present_content_summary(item.content, item.knowledge_saved_at, None)
                .map_err(|error| {
                    tracing::warn!(error = %error, "skipping invalid mixed-search content");
                })
                .ok()
        })
        .collect();
    Ok(Json(MixedSearchResponse {
        query: query.q,
        content,
        feeds: feed_hits
            .into_iter()
            .map(|feed| present_feed_hit(feed, &subscribed))
            .collect(),
        podcasts: podcast_hits.into_iter().map(present_podcast_hit).collect(),
    }))
}

async fn active_feed_urls(
    pool: &sqlx::PgPool,
    user_id: i64,
    request_id: &str,
) -> Result<Vec<String>, ApiError> {
    let mut values = Vec::new();
    for feed_type in ["substack", "atom", "podcast_rss"] {
        values.extend(
            list_active_feed_urls(pool, user_id, feed_type)
                .await
                .map_err(|error| internal_error(error, request_id))?,
        );
    }
    Ok(values)
}

fn tweet_context(content: &newsly_db::ContentDetailProjection) -> String {
    let summary = content
        .content_metadata
        .get("summary")
        .map(Value::to_string)
        .unwrap_or_default();
    format!(
        "Title: {}\nType: {}\nSource: {}\nURL: {}\nSummary: {}",
        content
            .title
            .as_deref()
            .filter(|title| !title.trim().is_empty())
            .map_or_else(|| format!("Content {}", content.id), ToOwned::to_owned),
        content.content_type,
        content.source.as_deref().unwrap_or("unknown"),
        content.url,
        truncate_chars(&summary, 12_000)
    )
}

fn plan_guidance(value: Option<&str>) -> Option<&str> {
    value.map(str::trim).filter(|value| !value.is_empty())
}

fn present_submission(row: SubmissionProjection) -> SubmissionStatusResponse {
    let content_type = row
        .content_type
        .as_deref()
        .and_then(|value| ContentType::from_str(value).ok())
        .unwrap_or(ContentType::Unknown);
    let task_kind = submission_kind(&row);
    let status = row
        .content_status
        .as_deref()
        .and_then(|value| ContentStatus::from_str(value).ok())
        .unwrap_or_else(|| task_content_status(&row.task_status));
    let url = submission_url(&row);
    let no_action = row.task_status == "completed"
        && row.output.get("action").and_then(Value::as_str) == Some("no_action");
    let rationale = if no_action {
        nested_string(&row.output, &["rationale"])
            .or(Some("Newsly could not find an action to take.".to_owned()))
    } else {
        row.action_rationale.clone()
    };
    let feed_subscription = row
        .content_metadata
        .as_ref()
        .and_then(Value::as_object)
        .and_then(|value| newsly_db::content_metadata::runtime_value(value, "feed_subscription"))
        .cloned()
        .and_then(|value| serde_json::from_value::<SubmissionFeedSubscriptionResponse>(value).ok());
    let detected_feed = row
        .content_metadata
        .as_ref()
        .and_then(Value::as_object)
        .and_then(|value| newsly_db::content_metadata::runtime_value(value, "detected_feed"))
        .cloned()
        .and_then(|value| serde_json::from_value(value).ok());
    let outcome = if no_action {
        SubmissionOutcome::NoAction
    } else if task_kind == SubmissionKind::FeedSubscription {
        feed_outcome(status, feed_subscription.as_ref())
    } else {
        status_outcome(status)
    };
    let result = if no_action {
        SubmissionResult::NoAction(SubmissionNoActionResult::new(
            rationale
                .clone()
                .expect("no-action submission always has a rationale"),
        ))
    } else {
        match task_kind {
            SubmissionKind::Content => {
                SubmissionResult::Content(SubmissionContentResult::new(outcome))
            }
            SubmissionKind::FeedSubscription => {
                SubmissionResult::FeedSubscription(Box::new(SubmissionFeedSubscriptionResult::new(
                    outcome,
                    detected_feed.clone(),
                    feed_subscription.clone(),
                )))
            }
            SubmissionKind::LearningDeck => {
                SubmissionResult::LearningDeck(SubmissionLearningDeckResult::new(outcome))
            }
        }
    };
    SubmissionStatusResponse {
        id: row.id,
        content_type,
        url: url.clone(),
        source_url: row
            .content_source_url
            .or_else(|| (!url.starts_with("newsly://")).then_some(url)),
        title: row.content_title.or_else(|| {
            row.action_input
                .as_ref()
                .and_then(|value| nested_string(value, &["title"]))
                .or_else(|| nested_string(&row.output, &["title"]))
        }),
        status,
        error_message: row.content_error.or(row.action_error).or(row.task_error),
        created_at: row.created_at,
        processed_at: row
            .content_processed_at
            .or(row.action_completed_at)
            .or(row.completed_at),
        submitted_via: Some("share_action".to_owned()),
        is_self_submission: true,
        result,
        submission_kind: task_kind,
        outcome,
        rationale,
        detected_feed,
        feed_subscription,
    }
}

fn submission_url(row: &SubmissionProjection) -> String {
    row.content_url
        .clone()
        .or_else(|| nested_string(&row.input, &["url"]))
        .or_else(|| {
            row.action_input
                .as_ref()
                .and_then(|value| nested_string(value, &["source_url"]))
        })
        .or_else(|| {
            row.action_input
                .as_ref()
                .and_then(|value| nested_string(value, &["url"]))
        })
        .unwrap_or_else(|| format!("newsly://share-actions/{}", row.id))
}

fn submission_kind(row: &SubmissionProjection) -> SubmissionKind {
    match (row.mode.as_str(), row.action_name.as_deref()) {
        ("presentation", _) | (_, Some("create_learning_deck")) => SubmissionKind::LearningDeck,
        ("add_feed", _) | (_, Some("subscribe_to_feed")) => SubmissionKind::FeedSubscription,
        _ => SubmissionKind::Content,
    }
}

fn task_content_status(status: &str) -> ContentStatus {
    match status {
        "queued" | "preparing" => ContentStatus::Pending,
        "running" | "awaiting_approval" | "applying" => ContentStatus::Processing,
        "completed" => ContentStatus::Completed,
        "cancelled" => ContentStatus::Skipped,
        _ => ContentStatus::Failed,
    }
}

fn status_outcome(status: ContentStatus) -> SubmissionOutcome {
    match status {
        ContentStatus::New | ContentStatus::Pending => SubmissionOutcome::Queued,
        ContentStatus::Processing | ContentStatus::AwaitingImage => SubmissionOutcome::Processing,
        ContentStatus::Completed => SubmissionOutcome::Completed,
        ContentStatus::Skipped => SubmissionOutcome::Skipped,
        ContentStatus::Failed => SubmissionOutcome::Failed,
    }
}

fn feed_outcome(
    status: ContentStatus,
    subscription: Option<&SubmissionFeedSubscriptionResponse>,
) -> SubmissionOutcome {
    if matches!(status, ContentStatus::New | ContentStatus::Pending) {
        return SubmissionOutcome::Queued;
    }
    if status == ContentStatus::Processing {
        return SubmissionOutcome::Processing;
    }
    if status == ContentStatus::Failed {
        return SubmissionOutcome::Failed;
    }
    match subscription.map(|value| value.status.as_str()) {
        Some("created" | "reactivated") => SubmissionOutcome::Subscribed,
        Some("already_exists") => SubmissionOutcome::AlreadySubscribed,
        Some("no_feed_found") => SubmissionOutcome::FeedNotFound,
        Some("fetch_failed") => SubmissionOutcome::FeedFetchFailed,
        Some(_) => SubmissionOutcome::FeedSubscriptionFailed,
        None => status_outcome(status),
    }
}

fn nested_string(value: &Value, path: &[&str]) -> Option<String> {
    path.iter()
        .try_fold(value, |current, key| current.get(*key))
        .and_then(value_as_clean_string)
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct SubmissionCursor {
    last_id: i64,
    last_created_at: String,
}

fn decode_submission_cursor(cursor: &str) -> Result<(DateTime<Utc>, i64), InvalidCursor> {
    let cursor: SubmissionCursor = pagination_cursor::decode(cursor)?;
    if cursor.last_id <= 0 {
        return Err(InvalidCursor);
    }
    let created_at = pagination_cursor::decode_timestamp(&cursor.last_created_at)?.and_utc();
    Ok((created_at, cursor.last_id))
}

fn encode_submission_cursor(row: &SubmissionProjection) -> Result<String, InvalidCursor> {
    if row.id <= 0 {
        return Err(InvalidCursor);
    }
    pagination_cursor::encode(&SubmissionCursor {
        last_id: row.id,
        last_created_at: pagination_cursor::encode_timestamp(row.created_at.naive_utc()),
    })
}

fn present_podcast_hit(hit: PodcastEpisodeHit) -> PodcastEpisodeSearchResultResponse {
    PodcastEpisodeSearchResultResponse {
        title: hit.title,
        episode_url: hit.episode_url,
        podcast_title: hit.podcast_title,
        source: hit.source,
        snippet: hit.snippet,
        feed_url: hit.feed_url,
        published_at: hit.published_at,
        provider: Some(hit.provider),
        score: hit.score,
    }
}

fn present_feed_hit(
    hit: FeedDiscoveryHit,
    subscribed_urls: &[String],
) -> MixedSearchFeedResultResponse {
    MixedSearchFeedResultResponse {
        id: hit.id,
        title: hit.title,
        site_url: hit.site_url,
        is_subscribed: subscribed_urls.iter().any(|value| {
            newsly_db::canonicalize_feed_url(value)
                == newsly_db::canonicalize_feed_url(&hit.feed_url)
        }),
        feed_url: hit.feed_url,
        feed_type: hit.feed_type,
        feed_format: hit.feed_format,
        description: hit.description,
        rationale: Some("Podcast feed discovered through Apple Podcasts".to_owned()),
        evidence_url: hit.evidence_url,
    }
}

fn parse_external_query(
    query: Result<Query<ExternalSearchQuery>, QueryRejection>,
    request_id: &str,
) -> Result<Query<ExternalSearchQuery>, ApiError> {
    let Query(query) =
        query.map_err(|rejection| validation_error(rejection.body_text(), request_id))?;
    query.validate(request_id)?;
    Ok(Query(query))
}

fn positive_path_id(
    path: Result<Path<i64>, PathRejection>,
    field: &str,
    request_id: &str,
) -> Result<i64, ApiError> {
    let Path(value) =
        path.map_err(|rejection| validation_error(rejection.body_text(), request_id))?;
    if value <= 0 {
        return Err(validation_error(
            format!("{field} must be greater than zero"),
            request_id,
        ));
    }
    Ok(value)
}

fn normalize_http_url(value: &str, request_id: &str) -> Result<String, ApiError> {
    let parsed = reqwest::Url::parse(value.trim())
        .map_err(|_| bad_request("No article URL found", request_id))?;
    if !matches!(parsed.scheme(), "http" | "https") || parsed.host().is_none() {
        return Err(bad_request("No article URL found", request_id));
    }
    Ok(parsed.to_string())
}

fn conversion_message(already_exists: bool) -> String {
    if already_exists {
        "Article already exists in system"
    } else {
        "Article created and queued for processing"
    }
    .to_owned()
}

fn value_as_clean_string(value: &Value) -> Option<String> {
    value
        .as_str()
        .map(|value| value.split_whitespace().collect::<Vec<_>>().join(" "))
        .filter(|value| !value.is_empty())
        .map(|value| truncate_chars(&value, 3_600))
}

fn truncate_chars(value: &str, limit: usize) -> String {
    if value.chars().count() <= limit {
        return value.to_owned();
    }
    let keep = limit.saturating_sub(3);
    format!("{}...", value.chars().take(keep).collect::<String>().trim())
}

fn conversion_error(error: ContentMiscRepositoryError, request_id: &str) -> ApiError {
    match error {
        ContentMiscRepositoryError::NotNewsContent
        | ContentMiscRepositoryError::ArticleUrlMissing => {
            bad_request(error.to_string(), request_id)
        }
        other => internal_error(other, request_id),
    }
}

fn provider_bad_gateway(error: &ContentMiscGatewayError, request_id: &str) -> ApiError {
    tracing::warn!(error = %error, "content provider request failed");
    ApiError::new(
        StatusCode::BAD_GATEWAY,
        "provider_error",
        "External provider request failed",
        request_id,
    )
    .with_retryable(true)
}

fn queue_error(error: QueueError, request_id: &str) -> ApiError {
    internal_error(error, request_id)
}

fn not_found_message(message: &str, request_id: &str) -> ApiError {
    ApiError::new(StatusCode::NOT_FOUND, "not_found", message, request_id)
}

fn validation_error(message: impl Into<String>, request_id: &str) -> ApiError {
    ApiError::validation(message, request_id)
}

#[cfg(test)]
mod submission_cursor_tests {
    use super::*;

    #[test]
    fn submission_cursor_round_trips_without_filter_fields() {
        let cursor = pagination_cursor::encode(&SubmissionCursor {
            last_id: 17,
            last_created_at: "2026-08-30T12:34:56.000000Z".to_owned(),
        })
        .expect("cursor encodes");
        let (created_at, id) = decode_submission_cursor(&cursor).expect("cursor decodes");
        assert_eq!(id, 17);
        assert_eq!(created_at.to_rfc3339(), "2026-08-30T12:34:56+00:00");

        let with_hash = pagination_cursor::encode(&serde_json::json!({
            "last_id": 17,
            "last_created_at": "2026-08-30T12:34:56.000000Z",
            "filters_hash": "unexpected"
        }))
        .expect("fixture encodes");
        assert!(decode_submission_cursor(&with_hash).is_err());
    }

    #[test]
    fn decodes_server_issued_legacy_submission_cursor() {
        let cursor = "eyJsYXN0X2NyZWF0ZWRfYXQiOiAiMjAyNi0wOC0zMFQxMjozNDo1Ni40NTY3ODkiLCAibGFzdF9pZCI6IDE3fQ==";
        let (created_at, id) = decode_submission_cursor(cursor).expect("legacy cursor decodes");
        assert_eq!(id, 17);
        assert_eq!(created_at.to_rfc3339(), "2026-08-30T12:34:56.456789+00:00");
    }
}
