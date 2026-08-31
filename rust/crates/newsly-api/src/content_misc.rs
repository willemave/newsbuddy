use std::collections::BTreeMap;
use std::str::FromStr;
use std::time::Duration;

use axum::extract::rejection::{JsonRejection, PathRejection, QueryRejection};
use axum::extract::{Extension, Path, Query, State};
use axum::http::header::{ACCEPT, CACHE_CONTROL, CONTENT_DISPOSITION, CONTENT_TYPE};
use axum::http::{HeaderMap, HeaderValue, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::{Json, Router};
use base64::Engine as _;
use base64::engine::general_purpose::{URL_SAFE, URL_SAFE_NO_PAD};
use chrono::{DateTime, NaiveDateTime, Utc};
use newsly_contracts::{
    ContentDiscussionResponse, ContentStatus, ContentType, ConvertNewsItemResponse,
    ConvertNewsResponse, DiscussionCommentResponse, DiscussionMode, DownloadMoreRequest,
    DownloadMoreResponse, MixedSearchFeedResultResponse, MixedSearchResponse, NarrationResponse,
    NarrationTargetType, PaginationMetadata, PodcastEpisodeSearchResponse,
    PodcastEpisodeSearchResultResponse, SubmissionContentResult,
    SubmissionFeedSubscriptionResponse, SubmissionFeedSubscriptionResult, SubmissionKind,
    SubmissionLearningDeckResult, SubmissionNoActionResult, SubmissionOutcome, SubmissionResult,
    SubmissionStatusListResponse, SubmissionStatusResponse, TweetSuggestion,
    TweetSuggestionsRequest, TweetSuggestionsResponse,
};
use newsly_db::{
    ContentMiscRepositoryError, DiscussionRefreshPlan, DiscussionTargetKind, FeedBackfillEntry,
    FeedBackfillPreparation, SubmissionProjection, finalize_article_conversion,
    list_active_feed_urls, list_submission_projections, persist_content_discussion,
    persist_feed_backfill, persist_news_discussion, prepare_agent_data_sync_dedupe_key,
    prepare_content_conversion, prepare_content_discussion_refresh, prepare_content_narration,
    prepare_feed_backfill, prepare_news_conversion, prepare_news_discussion_refresh,
    prepare_tweet_content, search_visible_content,
};
use newsly_providers::{
    ContentMiscGatewayError, DiscussionRefreshResult, FeedDiscoveryHit, PodcastEpisodeHit,
};
use newsly_queue::{EnqueueRequest, QueueError, QueueKernel, TaskType};
use serde::Deserialize;
use serde_json::{Map, Value, json};
use sha2::{Digest, Sha256};

use crate::auth::AuthenticatedUser;
use crate::content_read::presentation;
use crate::encoding::hex_encode;
use crate::error::ApiError;
use crate::gateway::RouteOwnershipStamp;
use crate::write_support::{
    bad_request, decode_json, internal_error, require_operation, verify_stamp,
};
use crate::{AppState, request_id_from_headers};

const CONVERT_CONTENT_OPERATION_ID: &str = "convertContentNewsToArticle";
const CONVERT_NEWS_OPERATION_ID: &str = "convertNewsItemToArticle";
const DOWNLOAD_MORE_OPERATION_ID: &str = "downloadContentMoreFromSeries";
const TWEET_OPERATION_ID: &str = "getContentTweetSuggestions";
const REFRESH_CONTENT_DISCUSSION_OPERATION_ID: &str = "refreshContentDiscussion";
const REFRESH_NEWS_DISCUSSION_OPERATION_ID: &str = "refreshNewsItemDiscussion";

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
            "/api/content/narration/{target_type}/{target_id}",
            get(get_narration),
        )
        .route(
            "/api/content/{content_id}/tweet-suggestions",
            post(get_tweet_suggestions),
        )
        .route(
            "/api/content/submissions/list",
            get(list_submission_statuses),
        )
        .route(
            "/api/content/{content_id}/discussion/refresh",
            post(refresh_content_discussion),
        )
        .route(
            "/api/news/items/{news_item_id}/discussion/refresh",
            post(refresh_news_item_discussion),
        )
        .route(
            "/api/content/search/podcasts",
            get(search_podcast_episode_matches),
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
    Extension(stamp): Extension<RouteOwnershipStamp>,
) -> Result<Json<ConvertNewsResponse>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    require_operation(&stamp, CONVERT_CONTENT_OPERATION_ID, &request_id)?;
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
    Extension(stamp): Extension<RouteOwnershipStamp>,
) -> Result<Json<ConvertNewsItemResponse>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    require_operation(&stamp, CONVERT_NEWS_OPERATION_ID, &request_id)?;
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
    let mut transaction = state
        .database
        .pool()
        .begin()
        .await
        .map_err(|error| internal_error(error, request_id))?;
    verify_stamp(&mut transaction, stamp, request_id).await?;
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
    let (payload, base_key) = agent_data_sync_payload(user_id, converted.content_id);
    let dedupe_key = prepare_agent_data_sync_dedupe_key(&mut transaction, user_id, &base_key)
        .await
        .map_err(|error| internal_error(error, request_id))?;
    let mut sync_request = EnqueueRequest::new(TaskType::SyncAgentData);
    sync_request.payload = Some(payload);
    sync_request.owner_user_id = Some(user_id);
    sync_request.dedupe = Some(true);
    sync_request.dedupe_key = Some(dedupe_key);
    requests.push(sync_request);
    QueueKernel::new(state.database.pool().clone())
        .enqueue_many_in_transaction(&mut transaction, requests)
        .await
        .map_err(|error| queue_error(error, request_id))?;
    transaction
        .commit()
        .await
        .map_err(|error| internal_error(error, request_id))?;
    Ok(converted)
}

#[utoipa::path(
    post,
    path = "/api/content/{content_id}/download-more",
    operation_id = "downloadContentMoreFromSeries",
    tag = "content",
    params(("content_id" = i64, Path, minimum = 1)),
    request_body = DownloadMoreRequest,
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, description = "Backfill completed", body = DownloadMoreResponse),
        (status = 400, description = "Feed could not be resolved", body = newsly_contracts::ErrorEnvelope),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 403, description = "Content not accessible", body = newsly_contracts::ErrorEnvelope),
        (status = 404, description = "Content not found", body = newsly_contracts::ErrorEnvelope),
        (status = 409, description = "Stale runtime owner", body = newsly_contracts::ErrorEnvelope),
        (status = 422, description = "Validation Error", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
#[expect(
    clippy::too_many_lines,
    reason = "the route keeps ownership fencing and its one feed-backfill transaction linear"
)]
pub(super) async fn download_more_from_series(
    State(state): State<AppState>,
    headers: HeaderMap,
    path: Result<Path<i64>, PathRejection>,
    current_user: AuthenticatedUser,
    Extension(stamp): Extension<RouteOwnershipStamp>,
    payload: Result<Json<DownloadMoreRequest>, JsonRejection>,
) -> Result<Json<DownloadMoreResponse>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    require_operation(&stamp, DOWNLOAD_MORE_OPERATION_ID, &request_id)?;
    let content_id = positive_path_id(path, "content_id", &request_id)?;
    let Json(payload) = decode_json(payload, &request_id)?;
    if !(1..=50).contains(&payload.count) {
        return Err(validation_error(
            "count must be between 1 and 50",
            &request_id,
        ));
    }
    verify_operation_now(&state, &stamp, &request_id).await?;
    let plan = match prepare_feed_backfill(
        state.database.pool(),
        current_user.id,
        content_id,
        payload.count,
    )
    .await
    .map_err(|error| internal_error(error, &request_id))?
    {
        FeedBackfillPreparation::Ready(plan) => plan,
        FeedBackfillPreparation::ContentNotFound => {
            return Err(not_found_message("Content not found", &request_id));
        }
        FeedBackfillPreparation::ContentNotAccessible => {
            return Err(ApiError::new(
                StatusCode::FORBIDDEN,
                "forbidden",
                "Content not accessible",
                request_id,
            ));
        }
        FeedBackfillPreparation::NotLongForm => {
            return Err(bad_request("Content is not long-form", &request_id));
        }
        FeedBackfillPreparation::FeedConfigNotFound => {
            return Err(bad_request(
                "Feed config not found for content",
                &request_id,
            ));
        }
    };
    let provider_entries = state
        .content_misc
        .fetch_feed_entries(&plan.feed_url, plan.target_limit)
        .await
        .map_err(|error| provider_bad_gateway(&error, &request_id))?;
    let scraped = provider_entries.len();
    let entries = provider_entries
        .into_iter()
        .map(|entry| FeedBackfillEntry {
            url: entry.url,
            title: entry.title,
            source: entry.source.or_else(|| plan.display_name.clone()),
            published_at: entry.published_at,
            content_type: if plan.scraper_type == "podcast_rss" {
                "podcast".to_owned()
            } else {
                "article".to_owned()
            },
        })
        .collect::<Vec<_>>();
    let mut transaction = state
        .database
        .pool()
        .begin()
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    verify_stamp(&mut transaction, &stamp, &request_id).await?;
    let persisted = persist_feed_backfill(&mut transaction, current_user.id, &plan, &entries)
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    if !persisted.content_ids.is_empty() {
        let requests = persisted
            .content_ids
            .iter()
            .map(|content_id| {
                let mut request = EnqueueRequest::new(TaskType::ProcessContent);
                request.content_id = Some(*content_id);
                request
            })
            .collect();
        QueueKernel::new(state.database.pool().clone())
            .enqueue_many_in_transaction(&mut transaction, requests)
            .await
            .map_err(|error| queue_error(error, &request_id))?;
    }
    transaction
        .commit()
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    Ok(Json(DownloadMoreResponse {
        status: "completed".to_owned(),
        requested_count: payload.count,
        base_limit: plan.base_limit,
        target_limit: plan.target_limit,
        scraped,
        saved: persisted.saved,
        duplicates: persisted.duplicates,
        errors: 0,
    }))
}

#[utoipa::path(
    get,
    path = "/api/content/narration/{target_type}/{target_id}",
    operation_id = "getContentNarration",
    tag = "content",
    params(
        ("target_type" = NarrationTargetType, Path, description = "Narration target type"),
        ("target_id" = i64, Path, minimum = 1)
    ),
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, description = "Successful Response", body = NarrationResponse, content_type = "application/json"),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 404, description = "Content not found", body = newsly_contracts::ErrorEnvelope),
        (status = 422, description = "Validation Error", body = newsly_contracts::ErrorEnvelope),
        (status = 503, description = "Narration audio unavailable", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn get_narration(
    State(state): State<AppState>,
    headers: HeaderMap,
    path: Result<Path<(String, i64)>, PathRejection>,
    current_user: AuthenticatedUser,
) -> Result<Response, ApiError> {
    let request_id = request_id_from_headers(&headers);
    let Path((target_type, target_id)) =
        path.map_err(|rejection| validation_error(rejection.body_text(), &request_id))?;
    if target_type != "content" || target_id <= 0 {
        return Err(not_found_message("Content not found", &request_id));
    }
    let plan = prepare_content_narration(state.database.pool(), current_user.id, target_id)
        .await
        .map_err(|error| internal_error(error, &request_id))?
        .ok_or_else(|| not_found_message("Content not found", &request_id))?;
    let title = presentation::present_content_detail(plan.content.clone()).map_or_else(
        |_| {
            plan.content
                .title
                .clone()
                .unwrap_or_else(|| format!("Content {target_id}"))
        },
        |value| value.display_title,
    );
    let narration_text = build_summary_narration(&title, &plan.content.content_metadata);
    if accepts_audio(&headers) {
        let audio = state
            .content_misc
            .synthesize_narration_mp3(&narration_text)
            .await
            .map_err(|error| narration_error(&error, &request_id))?;
        let mut response = audio.into_response();
        response
            .headers_mut()
            .insert(CONTENT_TYPE, HeaderValue::from_static("audio/mpeg"));
        response
            .headers_mut()
            .insert(CACHE_CONTROL, HeaderValue::from_static("no-store"));
        if let Ok(value) =
            HeaderValue::from_str(&format!("inline; filename=\"content-{target_id}.mp3\""))
        {
            response.headers_mut().insert(CONTENT_DISPOSITION, value);
        }
        return Ok(response);
    }
    Ok(Json(NarrationResponse {
        target_type: NarrationTargetType::Content,
        target_id,
        title,
        narration_text,
    })
    .into_response())
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
    Extension(stamp): Extension<RouteOwnershipStamp>,
    payload: Result<Json<TweetSuggestionsRequest>, JsonRejection>,
) -> Result<Json<TweetSuggestionsResponse>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    require_operation(&stamp, TWEET_OPERATION_ID, &request_id)?;
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
    let plan = prepare_tweet_content(state.database.pool(), current_user.id, content_id)
        .await
        .map_err(|error| internal_error(error, &request_id))?
        .ok_or_else(|| not_found_message("Content not found", &request_id))?;
    if !matches!(plan.status.as_str(), "completed" | "failed") {
        return Err(bad_request(
            format!("Content not ready for tweets (status: {})", plan.status),
            &request_id,
        ));
    }
    let context = tweet_context(&plan);
    let generated = state
        .content_misc
        .generate_tweet_suggestions(
            &context,
            plan_guidance(payload.message.as_deref()),
            payload.creativity,
            payload.length.as_str(),
            payload
                .llm_provider
                .map(newsly_contracts::UserLlmProvider::as_str),
        )
        .await
        .map_err(|error| provider_bad_gateway(&error, &request_id))?;
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
        .map_err(|message| bad_request(message, &request_id))?;
    let page =
        list_submission_projections(state.database.pool(), current_user.id, cursor, query.limit)
            .await
            .map_err(|error| internal_error(error, &request_id))?;
    let next_cursor = page
        .has_more
        .then(|| page.items.last())
        .flatten()
        .map(encode_submission_cursor);
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

#[utoipa::path(
    post,
    path = "/api/content/{content_id}/discussion/refresh",
    operation_id = "refreshContentDiscussion",
    tag = "content",
    params(("content_id" = i64, Path, minimum = 1)),
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, description = "Successful Response", body = ContentDiscussionResponse),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 404, description = "Content not found", body = newsly_contracts::ErrorEnvelope),
        (status = 409, description = "Stale runtime owner", body = newsly_contracts::ErrorEnvelope),
        (status = 422, description = "Validation Error", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn refresh_content_discussion(
    State(state): State<AppState>,
    headers: HeaderMap,
    path: Result<Path<i64>, PathRejection>,
    current_user: AuthenticatedUser,
    Extension(stamp): Extension<RouteOwnershipStamp>,
) -> Result<Json<ContentDiscussionResponse>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    require_operation(&stamp, REFRESH_CONTENT_DISCUSSION_OPERATION_ID, &request_id)?;
    let content_id = positive_path_id(path, "content_id", &request_id)?;
    verify_operation_now(&state, &stamp, &request_id).await?;
    let plan =
        prepare_content_discussion_refresh(state.database.pool(), current_user.id, content_id)
            .await
            .map_err(|error| internal_error(error, &request_id))?
            .ok_or_else(|| not_found_message("Content not found", &request_id))?;
    refresh_and_persist_discussion(&state, &stamp, plan, &request_id).await
}

#[utoipa::path(
    post,
    path = "/api/news/items/{news_item_id}/discussion/refresh",
    operation_id = "refreshNewsItemDiscussion",
    tag = "news",
    params(("news_item_id" = i64, Path, minimum = 1)),
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, description = "Successful Response", body = ContentDiscussionResponse),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 404, description = "News item not found", body = newsly_contracts::ErrorEnvelope),
        (status = 409, description = "Stale runtime owner", body = newsly_contracts::ErrorEnvelope),
        (status = 422, description = "Validation Error", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn refresh_news_item_discussion(
    State(state): State<AppState>,
    headers: HeaderMap,
    path: Result<Path<i64>, PathRejection>,
    current_user: AuthenticatedUser,
    Extension(stamp): Extension<RouteOwnershipStamp>,
) -> Result<Json<ContentDiscussionResponse>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    require_operation(&stamp, REFRESH_NEWS_DISCUSSION_OPERATION_ID, &request_id)?;
    let news_item_id = positive_path_id(path, "news_item_id", &request_id)?;
    verify_operation_now(&state, &stamp, &request_id).await?;
    let plan =
        prepare_news_discussion_refresh(state.database.pool(), current_user.id, news_item_id)
            .await
            .map_err(|error| internal_error(error, &request_id))?
            .ok_or_else(|| not_found_message("News item not found", &request_id))?;
    refresh_and_persist_discussion(&state, &stamp, plan, &request_id).await
}

async fn refresh_and_persist_discussion(
    state: &AppState,
    stamp: &RouteOwnershipStamp,
    plan: DiscussionRefreshPlan,
    request_id: &str,
) -> Result<Json<ContentDiscussionResponse>, ApiError> {
    let provider_result = state
        .content_misc
        .refresh_discussion(
            plan.platform.as_deref(),
            plan.discussion_url.as_deref(),
            plan.external_id.as_deref(),
        )
        .await;
    let (result, status, error_message) = match provider_result {
        Ok(result) => (Some(result), "completed", None),
        Err(error) => {
            tracing::warn!(error = %error, target_id = plan.id, "discussion refresh failed");
            (None, "failed", Some("Discussion refresh failed".to_owned()))
        }
    };
    let data = result.as_ref().map_or_else(
        || {
            json!({
                "mode": "none",
                "source_url": plan.discussion_url,
                "comments": [],
                "discussion_groups": [],
                "links": [],
                "stats": {},
            })
        },
        discussion_json,
    );
    let mut transaction = state
        .database
        .pool()
        .begin()
        .await
        .map_err(|error| internal_error(error, request_id))?;
    verify_stamp(&mut transaction, stamp, request_id).await?;
    match plan.kind {
        DiscussionTargetKind::Content => {
            persist_content_discussion(
                &mut transaction,
                &plan,
                status,
                &data,
                error_message.as_deref(),
            )
            .await
            .map_err(|error| internal_error(error, request_id))?;
        }
        DiscussionTargetKind::NewsItem => {
            persist_news_discussion(
                &mut transaction,
                &plan,
                status,
                &data,
                error_message.as_deref(),
            )
            .await
            .map_err(|error| internal_error(error, request_id))?;
        }
    }
    transaction
        .commit()
        .await
        .map_err(|error| internal_error(error, request_id))?;
    Ok(Json(present_refreshed_discussion(
        &plan,
        result,
        error_message,
    )))
}

#[derive(Debug, Deserialize)]
pub(super) struct ExternalSearchQuery {
    q: String,
    #[serde(default = "default_external_limit")]
    limit: usize,
}

const fn default_external_limit() -> usize {
    10
}

#[utoipa::path(
    get,
    path = "/api/content/search/podcasts",
    operation_id = "searchContentPodcastEpisodeMatches",
    tag = "content",
    params(
        ("q" = String, Query, min_length = 2, max_length = 200),
        ("limit" = Option<usize>, Query, minimum = 1, maximum = 25)
    ),
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, description = "Successful Response", body = PodcastEpisodeSearchResponse),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 422, description = "Validation Error", body = newsly_contracts::ErrorEnvelope),
        (status = 502, description = "Search provider failed", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn search_podcast_episode_matches(
    State(state): State<AppState>,
    headers: HeaderMap,
    query: Result<Query<ExternalSearchQuery>, QueryRejection>,
    _current_user: AuthenticatedUser,
) -> Result<Json<PodcastEpisodeSearchResponse>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    let Query(query) = parse_external_query(query, &request_id)?;
    let hits = state
        .content_misc
        .search_podcast_episodes(&query.q, query.limit)
        .await
        .map_err(|error| provider_bad_gateway(&error, &request_id))?;
    Ok(Json(PodcastEpisodeSearchResponse {
        results: hits.into_iter().map(present_podcast_hit).collect(),
    }))
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

async fn verify_operation_now(
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

fn build_summary_narration(title: &str, metadata: &Value) -> String {
    let summary = metadata.get("summary");
    let artifact_payload = summary
        .and_then(|value| value.get("artifact"))
        .and_then(|value| value.get("payload"));
    let narrative = artifact_payload
        .and_then(|value| value.get("overview"))
        .and_then(value_as_clean_string)
        .or_else(|| {
            summary.and_then(|value| match value {
                Value::String(value) => clean_text(value, 3_600),
                Value::Object(object) => ["editorial_narrative", "overview", "summary", "hook"]
                    .into_iter()
                    .find_map(|key| object.get(key).and_then(value_as_clean_string)),
                _ => None,
            })
        });
    let takeaway = artifact_payload
        .and_then(|value| value.get("takeaway"))
        .and_then(value_as_clean_string)
        .or_else(|| {
            summary
                .and_then(|value| value.get("takeaway"))
                .and_then(value_as_clean_string)
        });
    let mut points = Vec::new();
    for container in [summary, artifact_payload, Some(metadata)]
        .into_iter()
        .flatten()
    {
        for key in ["key_points", "bullet_points", "points", "insights"] {
            let Some(items) = container.get(key).and_then(Value::as_array) else {
                continue;
            };
            for item in items {
                let text = match item {
                    Value::String(value) => clean_text(value, 280),
                    Value::Object(object) => ["point", "text", "insight", "content"]
                        .into_iter()
                        .find_map(|field| object.get(field).and_then(value_as_clean_string)),
                    _ => None,
                };
                if let Some(text) = text
                    && !points
                        .iter()
                        .any(|existing: &String| existing.eq_ignore_ascii_case(&text))
                {
                    points.push(text);
                }
                if points.len() >= 10 {
                    break;
                }
            }
        }
    }
    let mut parts = vec![format!("Here is the full summary for {title}.")];
    if let Some(narrative) = narrative {
        parts.push(narrative);
    }
    if !points.is_empty() {
        parts.push(format!(
            "Key points. {}",
            points
                .iter()
                .enumerate()
                .map(|(index, point)| format!("Point {}: {point}", index + 1))
                .collect::<Vec<_>>()
                .join(" ")
        ));
    }
    if let Some(takeaway) = takeaway {
        parts.push(format!("Takeaway: {takeaway}"));
    }
    if parts.len() == 1 {
        parts.push("I don't have a complete processed summary yet.".to_owned());
    }
    truncate_chars(&parts.join(" "), 8_000)
}

fn tweet_context(plan: &newsly_db::TweetContentPlan) -> String {
    let summary = plan
        .metadata
        .get("summary")
        .map(Value::to_string)
        .unwrap_or_default();
    format!(
        "Title: {}\nType: {}\nSource: {}\nURL: {}\nSummary: {}",
        plan.title,
        plan.content_type,
        plan.source.as_deref().unwrap_or("unknown"),
        plan.url,
        truncate_chars(&summary, 12_000)
    )
}

fn plan_guidance(value: Option<&str>) -> Option<&str> {
    value.map(str::trim).filter(|value| !value.is_empty())
}

fn discussion_json(result: &DiscussionRefreshResult) -> Value {
    json!({
        "mode": "comments",
        "source_url": result.source_url,
        "comments": result.comments.iter().map(|comment| json!({
            "comment_id": comment.comment_id,
            "parent_id": comment.parent_id,
            "author": comment.author,
            "text": comment.text,
            "compact_text": Value::Null,
            "depth": comment.depth,
            "created_at": comment.created_at,
            "source_url": comment.source_url,
        })).collect::<Vec<_>>(),
        "discussion_groups": [],
        "links": [],
        "stats": result.stats,
    })
}

fn present_refreshed_discussion(
    plan: &DiscussionRefreshPlan,
    result: Option<DiscussionRefreshResult>,
    error_message: Option<String>,
) -> ContentDiscussionResponse {
    let (platform, source_url, comments, discussion_stats) = result.map_or_else(
        || {
            (
                plan.platform.clone(),
                plan.discussion_url.clone(),
                Vec::new(),
                BTreeMap::new(),
            )
        },
        |result| {
            (
                Some(result.platform),
                Some(result.source_url),
                result
                    .comments
                    .into_iter()
                    .map(|comment| DiscussionCommentResponse {
                        comment_id: comment.comment_id,
                        parent_id: comment.parent_id,
                        author: comment.author,
                        text: comment.text,
                        compact_text: None,
                        depth: comment.depth,
                        created_at: comment.created_at,
                        source_url: comment.source_url,
                    })
                    .collect(),
                result.stats,
            )
        },
    );
    let status = if error_message.is_some() {
        "failed"
    } else if comments.is_empty() {
        "partial"
    } else {
        "completed"
    };
    ContentDiscussionResponse {
        content_id: plan.id,
        status: status.to_owned(),
        mode: if comments.is_empty() {
            DiscussionMode::None
        } else {
            DiscussionMode::Comments
        },
        platform,
        source_url: source_url.clone(),
        discussion_url: source_url,
        fetched_at: Some(Utc::now()),
        error_message,
        comments,
        discussion_groups: Vec::new(),
        links: Vec::new(),
        summary: None,
        comment_count: discussion_stats
            .get("comment_count")
            .and_then(Value::as_i64),
        stats: discussion_stats,
    }
}

#[expect(
    clippy::too_many_lines,
    reason = "canonical result construction and agreeing compatibility mirrors must stay together"
)]
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
    let url = row
        .content_url
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
        .unwrap_or_else(|| format!("newsly://share-actions/{}", row.id));
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
        .and_then(processing_metadata)
        .and_then(|value| value.get("feed_subscription"))
        .cloned()
        .and_then(|value| serde_json::from_value::<SubmissionFeedSubscriptionResponse>(value).ok());
    let detected_feed = row
        .content_metadata
        .as_ref()
        .and_then(processing_metadata)
        .and_then(|value| value.get("detected_feed"))
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

fn processing_metadata(metadata: &Value) -> Option<&Map<String, Value>> {
    metadata
        .get("domain")
        .and_then(|value| value.get("processing"))
        .and_then(Value::as_object)
        .or_else(|| metadata.get("processing").and_then(Value::as_object))
        .or_else(|| metadata.as_object())
}

fn nested_string(value: &Value, path: &[&str]) -> Option<String> {
    path.iter()
        .try_fold(value, |current, key| current.get(*key))
        .and_then(value_as_clean_string)
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct SubmissionCursor {
    last_id: i64,
    last_created_at: String,
    #[serde(default)]
    filters_hash: Option<String>,
}

fn decode_submission_cursor(cursor: &str) -> Result<(DateTime<Utc>, i64), &'static str> {
    let decoded = URL_SAFE
        .decode(cursor)
        .or_else(|_| URL_SAFE_NO_PAD.decode(cursor))
        .map_err(|_| "Invalid pagination cursor")?;
    let cursor: SubmissionCursor =
        serde_json::from_slice(&decoded).map_err(|_| "Invalid pagination cursor")?;
    if cursor.last_id <= 0 || cursor.filters_hash.is_some_and(|value| !value.is_empty()) {
        return Err("Invalid pagination cursor");
    }
    let created_at = DateTime::parse_from_rfc3339(&cursor.last_created_at)
        .map(|value| value.with_timezone(&Utc))
        .ok()
        .or_else(|| {
            NaiveDateTime::parse_from_str(&cursor.last_created_at, "%Y-%m-%dT%H:%M:%S%.f")
                .map(|value| value.and_utc())
                .ok()
        })
        .ok_or("Invalid pagination cursor")?;
    Ok((created_at, cursor.last_id))
}

fn encode_submission_cursor(row: &SubmissionProjection) -> String {
    let payload = format!(
        "{{\"last_created_at\": \"{}\", \"last_id\": {}}}",
        row.created_at.naive_utc().format("%Y-%m-%dT%H:%M:%S%.f"),
        row.id
    );
    URL_SAFE.encode(payload)
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
        is_subscribed: subscribed_urls
            .iter()
            .any(|value| canonical_feed_url(value) == canonical_feed_url(&hit.feed_url)),
        feed_url: hit.feed_url,
        feed_type: hit.feed_type,
        feed_format: hit.feed_format,
        description: hit.description,
        rationale: Some("Podcast feed discovered through Apple Podcasts".to_owned()),
        evidence_url: hit.evidence_url,
    }
}

fn canonical_feed_url(value: &str) -> String {
    value.trim().trim_end_matches('/').to_ascii_lowercase()
}

fn agent_data_sync_payload(user_id: i64, content_id: i64) -> (Map<String, Value>, String) {
    let payload = json!({
        "user_id": user_id,
        "content_ids": [content_id],
        "news_item_ids": [],
        "chat_session_ids": [],
        "briefing_dates": [],
    });
    let serialized = serde_json::to_vec(&payload).expect("agent sync payload serializes");
    let digest = Sha256::digest(serialized);
    let encoded = hex_encode(&digest);
    (
        payload
            .as_object()
            .cloned()
            .expect("agent sync payload is an object"),
        format!("agent-sync|user:{user_id}|payload:{}", &encoded[..24]),
    )
}

fn parse_external_query<T>(
    query: Result<Query<T>, QueryRejection>,
    request_id: &str,
) -> Result<Query<T>, ApiError>
where
    T: ExternalQuery,
{
    let Query(query) =
        query.map_err(|rejection| validation_error(rejection.body_text(), request_id))?;
    query.validate(request_id)?;
    Ok(Query(query))
}

trait ExternalQuery {
    fn validate(&self, request_id: &str) -> Result<(), ApiError>;
}

impl ExternalQuery for ExternalSearchQuery {
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

fn accepts_audio(headers: &HeaderMap) -> bool {
    headers
        .get(ACCEPT)
        .and_then(|value| value.to_str().ok())
        .is_some_and(|value| value.to_ascii_lowercase().contains("audio/mpeg"))
}

fn clean_text(value: &str, limit: usize) -> Option<String> {
    let cleaned = value.split_whitespace().collect::<Vec<_>>().join(" ");
    (!cleaned.is_empty()).then(|| truncate_chars(&cleaned, limit))
}

fn value_as_clean_string(value: &Value) -> Option<String> {
    value.as_str().and_then(|value| clean_text(value, 3_600))
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

fn narration_error(error: &ContentMiscGatewayError, request_id: &str) -> ApiError {
    tracing::warn!(error = %error, "narration audio request failed");
    ApiError::new(
        StatusCode::SERVICE_UNAVAILABLE,
        "narration_unavailable",
        "Narration audio is unavailable",
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
    ApiError::new(
        StatusCode::UNPROCESSABLE_ENTITY,
        "validation_error",
        "Request validation failed",
        request_id,
    )
    .with_details(
        json!({"errors": [{"message": message.into()}]})
            .as_object()
            .expect("validation detail is an object")
            .clone(),
    )
}
