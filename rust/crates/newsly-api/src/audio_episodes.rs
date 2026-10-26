use crate::audio_episode_presentation::present_audio_episode;
use newsly_domain::truncate_chars;
use std::collections::HashSet;
use std::env;
use std::time::Duration;

use axum::body::{Body, Bytes};
use axum::extract::rejection::{JsonRejection, PathRejection, QueryRejection};
use axum::extract::{Path, Query, State};
use axum::http::header::{HOST, RANGE};
use axum::http::{HeaderMap, StatusCode};
use axum::response::{Html, Response};
use axum::routing::{get, post};
use axum::{Json, Router};
use base64::Engine as _;
use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use chrono::Utc;
use futures_util::Stream;
use newsly_contracts::{
    AudioEpisodeListQuery, AudioEpisodeResponse, AudioEpisodeShareResponse,
    CUSTOM_NARRATION_MAX_SOURCES, CustomNarrationCreateRequest,
};
use newsly_db::{
    AudioEpisodeReadTrigger, AudioEpisodeRecord, AudioEpisodeShareOutcome, NewAudioEpisode,
    NewsReadFilter, find_shared_audio_episode, find_user_audio_episode,
    find_user_audio_episode_for_update, find_visible_content_body, find_visible_content_detail,
    find_visible_news_item_detail, list_user_custom_narrations, list_visible_news_items,
    mark_audio_episode_sources_read, reset_audio_episode_for_generation, upsert_audio_episode,
};
use newsly_queue::{EnqueueRequest, QueueKernel, TaskType};
use reqwest::Url;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use tokio::io::AsyncReadExt as _;

use crate::auth::AuthenticatedUser;
use crate::error::ApiError;
use crate::gateway::RouteOwnershipStamp;
use crate::write_support::{bad_request, decode_json, internal_error, not_found};
use crate::{AppState, request_id_from_headers};
use newsly_domain::hex_encode;

mod streaming;

use streaming::{AUDIO_CHUNK_BYTES, audio_stream_response, stream_stored_audio};

const FAST_NEWS_LIMIT: usize = 200;
const LONGFORM_BODY_MAX_CHARS: usize = 16_000;
const CUSTOM_SOURCE_TOTAL_CHAR_LIMIT: usize = 24_000;
const CUSTOM_SOURCE_MIN_CHARS: usize = 2_000;
const STREAM_POLL_INTERVAL: Duration = Duration::from_millis(250);
const STREAM_FOLLOW_TIMEOUT: Duration = Duration::from_secs(180);

pub(super) fn router() -> Router<AppState> {
    Router::new()
        .route(
            "/api/content/audio-episodes/fast-news",
            post(create_fast_news_audio_episode),
        )
        .route(
            "/api/content/audio-episodes/custom-narrations",
            get(list_custom_narration_audio_episodes).post(create_custom_narration_audio_episode),
        )
        .route(
            "/api/content/audio-episodes/{audio_episode_id}",
            get(get_audio_episode),
        )
        .route(
            "/api/content/audio-episodes/{audio_episode_id}/audio",
            get(get_audio_episode_audio),
        )
        .route(
            "/api/content/audio-episodes/{audio_episode_id}/playback-finished",
            post(finish_audio_episode_playback),
        )
        .route(
            "/api/content/audio-episodes/{audio_episode_id}/share",
            post(enable_audio_episode_public_share).delete(disable_audio_episode_public_share),
        )
        .route(
            "/api/content/audio-episodes/{audio_episode_id}/stream",
            get(stream_audio_episode),
        )
        .route(
            "/api/content/{content_id}/audio-episodes/council",
            post(create_content_council_audio_episode),
        )
        .route(
            "/api/news/items/{news_item_id}/audio-episodes/discussion",
            post(create_news_item_discussion_audio_episode),
        )
        .route("/audio/share/{token}/", get(serve_shared_audio_episode))
        .route(
            "/audio/share/{token}/audio",
            get(serve_shared_audio_episode_audio),
        )
}

#[utoipa::path(
    post,
    path = "/api/content/audio-episodes/fast-news",
    operation_id = "createContentFastNewsAudioEpisode",
    tag = "content",
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, body = AudioEpisodeResponse),
        (status = 400, body = newsly_contracts::ErrorEnvelope),
        (status = 401, body = newsly_contracts::ErrorEnvelope),
        (status = 409, body = newsly_contracts::ErrorEnvelope),
        (status = 422, body = newsly_contracts::ErrorEnvelope),
        (status = 500, body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn create_fast_news_audio_episode(
    State(state): State<AppState>,
    headers: HeaderMap,
    current_user: AuthenticatedUser,
    stamp: RouteOwnershipStamp,
) -> Result<Json<AudioEpisodeResponse>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    let page = list_visible_news_items(
        state.database.pool(),
        current_user.id,
        NewsReadFilter::Unread,
        None,
        FAST_NEWS_LIMIT,
    )
    .await
    .map_err(|error| internal_error(error, &request_id))?;
    if page.items.is_empty() {
        return Err(bad_request(
            "No unread Fast Reads are available",
            &request_id,
        ));
    }
    let source_item_ids = page.items.iter().map(|item| item.id).collect::<Vec<_>>();
    let items = page
        .items
        .iter()
        .map(news_source_payload)
        .collect::<Vec<_>>();
    let snapshot = json!({
        "kind": "fast_news_digest",
        "total_unread": page.total,
        "included_count": items.len(),
        "items": items,
    });
    create_and_enqueue(
        &state,
        &stamp,
        &request_id,
        NewAudioEpisode {
            user_id: current_user.id,
            kind: "fast_news_digest",
            title: "Fast Reads Brief",
            source_content_id: None,
            source_item_ids: &source_item_ids,
            source_snapshot: &snapshot,
        },
    )
    .await
    .and_then(|episode| present_audio_episode(episode.into(), &request_id).map(Json))
}

#[utoipa::path(
    post,
    path = "/api/content/{content_id}/audio-episodes/council",
    operation_id = "createContentCouncilAudioEpisode",
    tag = "content",
    params(
        ("content_id" = i64, Path, minimum = 1)
    ),
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, body = AudioEpisodeResponse),
        (status = 400, body = newsly_contracts::ErrorEnvelope),
        (status = 401, body = newsly_contracts::ErrorEnvelope),
        (status = 404, body = newsly_contracts::ErrorEnvelope),
        (status = 409, body = newsly_contracts::ErrorEnvelope),
        (status = 422, body = newsly_contracts::ErrorEnvelope),
        (status = 500, body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn create_content_council_audio_episode(
    State(state): State<AppState>,
    headers: HeaderMap,
    current_user: AuthenticatedUser,
    stamp: RouteOwnershipStamp,
    path: Result<Path<i64>, PathRejection>,
) -> Result<Json<AudioEpisodeResponse>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    let content_id = positive_path_id(path, "content_id", &request_id)?;
    let projection =
        find_visible_content_detail(state.database.pool(), current_user.id, content_id)
            .await
            .map_err(|error| internal_error(error, &request_id))?
            .ok_or_else(|| not_found("Content", &request_id))?;
    if !matches!(projection.content_type.as_str(), "article" | "podcast") {
        return Err(bad_request(
            "Audio discussions are only for long form",
            &request_id,
        ));
    }
    let body = load_content_body(&state, current_user.id, content_id, &request_id).await?;
    let snapshot = content_source_payload(&projection, &body, LONGFORM_BODY_MAX_CHARS);
    let title = truncate_chars(
        &format!("Expert discussion: {}", content_display_title(&projection)),
        255,
    );
    create_and_enqueue(
        &state,
        &stamp,
        &request_id,
        NewAudioEpisode {
            user_id: current_user.id,
            kind: "content_council_discussion",
            title: &title,
            source_content_id: Some(content_id),
            source_item_ids: &[],
            source_snapshot: &snapshot,
        },
    )
    .await
    .and_then(|episode| present_audio_episode(episode.into(), &request_id).map(Json))
}

#[utoipa::path(
    post,
    path = "/api/news/items/{news_item_id}/audio-episodes/discussion",
    operation_id = "createDiscussionNewsItemAudioEpisode",
    tag = "discussion",
    params(
        ("news_item_id" = i64, Path, minimum = 1)
    ),
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, body = AudioEpisodeResponse),
        (status = 400, body = newsly_contracts::ErrorEnvelope),
        (status = 401, body = newsly_contracts::ErrorEnvelope),
        (status = 404, body = newsly_contracts::ErrorEnvelope),
        (status = 409, body = newsly_contracts::ErrorEnvelope),
        (status = 422, body = newsly_contracts::ErrorEnvelope),
        (status = 500, body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn create_news_item_discussion_audio_episode(
    State(state): State<AppState>,
    headers: HeaderMap,
    current_user: AuthenticatedUser,
    stamp: RouteOwnershipStamp,
    path: Result<Path<i64>, PathRejection>,
) -> Result<Json<AudioEpisodeResponse>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    let news_item_id = positive_path_id(path, "news_item_id", &request_id)?;
    let item = find_visible_news_item_detail(state.database.pool(), current_user.id, news_item_id)
        .await
        .map_err(|error| internal_error(error, &request_id))?
        .ok_or_else(|| not_found("News item", &request_id))?;
    if item
        .summary_text
        .as_deref()
        .is_none_or(|summary| summary.trim().is_empty())
        && item.summary_key_points.as_array().is_none_or(Vec::is_empty)
    {
        return Err(bad_request(
            "No Fast Read summary is available",
            &request_id,
        ));
    }
    let item_payload = news_source_payload(&item);
    let title = truncate_chars(
        &format!("News discussion: {}", news_display_title(&item)),
        255,
    );
    let snapshot = json!({"kind": "news_item_discussion", "item": item_payload});
    create_and_enqueue(
        &state,
        &stamp,
        &request_id,
        NewAudioEpisode {
            user_id: current_user.id,
            kind: "news_item_discussion",
            title: &title,
            source_content_id: None,
            source_item_ids: &[news_item_id],
            source_snapshot: &snapshot,
        },
    )
    .await
    .and_then(|episode| present_audio_episode(episode.into(), &request_id).map(Json))
}

#[utoipa::path(
    post,
    path = "/api/content/audio-episodes/custom-narrations",
    operation_id = "createContentCustomNarrationAudioEpisode",
    tag = "content",
    request_body = CustomNarrationCreateRequest,
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, body = AudioEpisodeResponse),
        (status = 400, body = newsly_contracts::ErrorEnvelope),
        (status = 401, body = newsly_contracts::ErrorEnvelope),
        (status = 404, body = newsly_contracts::ErrorEnvelope),
        (status = 409, body = newsly_contracts::ErrorEnvelope),
        (status = 422, body = newsly_contracts::ErrorEnvelope),
        (status = 500, body = newsly_contracts::ErrorEnvelope)
    )
)]
#[expect(
    clippy::too_many_lines,
    reason = "the route keeps source validation and its immutable queue snapshot visibly atomic"
)]
pub(super) async fn create_custom_narration_audio_episode(
    State(state): State<AppState>,
    headers: HeaderMap,
    current_user: AuthenticatedUser,
    stamp: RouteOwnershipStamp,
    payload: Result<Json<CustomNarrationCreateRequest>, JsonRejection>,
) -> Result<Json<AudioEpisodeResponse>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    let Json(payload) = decode_json(payload, &request_id)?;
    let content_ids = normalize_ids(payload.content_ids, "Content", &request_id)?;
    let news_item_ids = normalize_ids(payload.news_item_ids, "Fast Read", &request_id)?;
    let source_count = content_ids.len() + news_item_ids.len();
    if source_count == 0 {
        return Err(bad_request(
            "Select at least one article, podcast, or Fast Read",
            &request_id,
        ));
    }
    if source_count > CUSTOM_NARRATION_MAX_SOURCES {
        return Err(bad_request(
            format!("Select at most {CUSTOM_NARRATION_MAX_SOURCES} sources"),
            &request_id,
        ));
    }
    let normalized_title = payload
        .title
        .as_deref()
        .map(str::trim)
        .filter(|title| !title.is_empty())
        .map(ToOwned::to_owned);
    if normalized_title
        .as_ref()
        .is_some_and(|title| title.chars().count() > 120)
    {
        return Err(validation_error(
            "title must contain at most 120 characters",
            &request_id,
        ));
    }
    let source_budget = (CUSTOM_SOURCE_TOTAL_CHAR_LIMIT / source_count)
        .clamp(CUSTOM_SOURCE_MIN_CHARS, LONGFORM_BODY_MAX_CHARS);
    let mut items = Vec::with_capacity(source_count);
    let mut first_reader_title = None;
    for content_id in &content_ids {
        let projection =
            find_visible_content_detail(state.database.pool(), current_user.id, *content_id)
                .await
                .map_err(|error| internal_error(error, &request_id))?
                .ok_or_else(|| not_found(&format!("Content {content_id}"), &request_id))?;
        if !matches!(projection.content_type.as_str(), "article" | "podcast") {
            return Err(bad_request(
                "Custom narrations only support articles and podcasts",
                &request_id,
            ));
        }
        let body = load_content_body(&state, current_user.id, *content_id, &request_id).await?;
        if first_reader_title.is_none() {
            first_reader_title = Some(content_display_title(&projection));
        }
        let mut item = content_source_payload(&projection, &body, source_budget);
        item.as_object_mut()
            .expect("content source payload is an object")
            .insert("source_kind".to_owned(), json!("long_form"));
        items.push(item);
    }
    for news_item_id in &news_item_ids {
        let item =
            find_visible_news_item_detail(state.database.pool(), current_user.id, *news_item_id)
                .await
                .map_err(|error| internal_error(error, &request_id))?
                .ok_or_else(|| not_found(&format!("Fast Read {news_item_id}"), &request_id))?;
        let mut source = news_source_payload(&item);
        let summary = item.summary_text.as_deref().unwrap_or("").trim();
        if summary.is_empty() && item.summary_key_points.as_array().is_none_or(Vec::is_empty) {
            return Err(bad_request(
                format!("No Fast Read summary is available for item {news_item_id}"),
                &request_id,
            ));
        }
        let object = source
            .as_object_mut()
            .expect("news source payload is an object");
        object.insert("source_kind".to_owned(), json!("fast_read"));
        object.insert("news_item_id".to_owned(), json!(news_item_id));
        object.insert("source_text".to_owned(), json!(summary));
        object.insert("source_text_excerpt_strategy".to_owned(), json!("summary"));
        object.insert("source_text_truncated".to_owned(), json!(false));
        object.insert(
            "source_text_chars".to_owned(),
            json!(summary.chars().count()),
        );
        object.insert(
            "source_text_included_chars".to_owned(),
            json!(summary.chars().count()),
        );
        items.push(source);
    }
    let source_text_total_chars = sum_item_usize(&items, "source_text_chars");
    let source_text_included_chars = sum_item_usize(&items, "source_text_included_chars");
    let snapshot = json!({
        "kind": "custom_narration",
        "source_count": items.len(),
        "content_ids": content_ids,
        "news_item_ids": news_item_ids,
        "read_on_play": {
            "content_ids": if payload.mark_source_content_read_on_play { content_ids.clone() } else { Vec::new() },
            "news_item_ids": news_item_ids,
        },
        "source_text_budget_chars": source_budget,
        "source_text_total_chars": source_text_total_chars,
        "source_text_included_chars": source_text_included_chars,
        "items": items,
    });
    let title = normalized_title
        .unwrap_or_else(|| custom_narration_title(&snapshot, first_reader_title.as_deref()));
    create_and_enqueue(
        &state,
        &stamp,
        &request_id,
        NewAudioEpisode {
            user_id: current_user.id,
            kind: "custom_narration",
            title: &title,
            source_content_id: None,
            source_item_ids: &news_item_ids,
            source_snapshot: &snapshot,
        },
    )
    .await
    .and_then(|episode| present_audio_episode(episode.into(), &request_id).map(Json))
}

#[utoipa::path(
    get,
    path = "/api/content/audio-episodes/custom-narrations",
    operation_id = "listContentCustomNarrationAudioEpisodes",
    tag = "content",
    params(AudioEpisodeListQuery),
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, body = [AudioEpisodeResponse]),
        (status = 401, body = newsly_contracts::ErrorEnvelope),
        (status = 422, body = newsly_contracts::ErrorEnvelope),
        (status = 500, body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn list_custom_narration_audio_episodes(
    State(state): State<AppState>,
    headers: HeaderMap,
    current_user: AuthenticatedUser,
    query: Result<Query<AudioEpisodeListQuery>, QueryRejection>,
) -> Result<Json<Vec<AudioEpisodeResponse>>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    let Query(query) = decode_query(query, &request_id)?;
    if !(1..=50).contains(&query.limit) {
        return Err(validation_error(
            "limit must be between 1 and 50",
            &request_id,
        ));
    }
    let episodes = list_user_custom_narrations(state.database.pool(), current_user.id, query.limit)
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    Ok(Json(episodes.into_iter().filter_map(|episode| {
        let episode_id = episode.id;
        match present_audio_episode(episode.into(), &request_id) {
            Ok(episode) => Some(episode),
            Err(error) => { tracing::warn!(episode_id, error = ?error, "skipping invalid audio episode in list"); None }
        }
    }).collect()))
}

#[utoipa::path(
    get,
    path = "/api/content/audio-episodes/{audio_episode_id}",
    operation_id = "getContentAudioEpisode",
    tag = "content",
    params(("audio_episode_id" = i64, Path, minimum = 1)),
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, body = AudioEpisodeResponse),
        (status = 401, body = newsly_contracts::ErrorEnvelope),
        (status = 404, body = newsly_contracts::ErrorEnvelope),
        (status = 422, body = newsly_contracts::ErrorEnvelope),
        (status = 500, body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn get_audio_episode(
    State(state): State<AppState>,
    headers: HeaderMap,
    current_user: AuthenticatedUser,
    path: Result<Path<i64>, PathRejection>,
) -> Result<Json<AudioEpisodeResponse>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    let episode_id = positive_path_id(path, "audio_episode_id", &request_id)?;
    let episode = find_user_audio_episode(state.database.pool(), current_user.id, episode_id)
        .await
        .map_err(|error| internal_error(error, &request_id))?
        .ok_or_else(|| not_found("Audio episode", &request_id))?;
    present_audio_episode(episode.into(), &request_id).map(Json)
}

#[utoipa::path(
    get,
    path = "/api/content/audio-episodes/{audio_episode_id}/audio",
    operation_id = "getContentAudioEpisodeAudio",
    tag = "content",
    params(("audio_episode_id" = i64, Path, minimum = 1)),
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, description = "Generated MP3", content_type = "audio/mpeg"),
        (status = 206, description = "Generated audio byte range"),
        (status = 401, body = newsly_contracts::ErrorEnvelope),
        (status = 404, body = newsly_contracts::ErrorEnvelope),
        (status = 409, body = newsly_contracts::ErrorEnvelope),
        (status = 416, description = "Requested audio range is not satisfiable"),
        (status = 422, body = newsly_contracts::ErrorEnvelope),
        (status = 500, body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn get_audio_episode_audio(
    State(state): State<AppState>,
    headers: HeaderMap,
    current_user: AuthenticatedUser,
    stamp: RouteOwnershipStamp,
    path: Result<Path<i64>, PathRejection>,
) -> Result<Response, ApiError> {
    let request_id = request_id_from_headers(&headers);
    let episode_id = positive_path_id(path, "audio_episode_id", &request_id)?;
    let episode =
        mark_play_started(&state, &stamp, current_user.id, episode_id, &request_id).await?;
    if episode.status != "completed" {
        return Err(conflict("Audio episode is not ready", &request_id));
    }
    let path = episode
        .audio_storage_path
        .as_deref()
        .ok_or_else(|| not_found("Audio file", &request_id))?;
    stream_stored_audio(
        &state,
        path,
        episode_id,
        &episode.audio_content_type,
        headers.get(RANGE).and_then(|value| value.to_str().ok()),
        &request_id,
    )
    .await
}

#[utoipa::path(
    post,
    path = "/api/content/audio-episodes/{audio_episode_id}/playback-finished",
    operation_id = "finishContentFinishedAudioEpisodePlayback",
    tag = "content",
    params(("audio_episode_id" = i64, Path, minimum = 1)),
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, body = AudioEpisodeResponse),
        (status = 401, body = newsly_contracts::ErrorEnvelope),
        (status = 404, body = newsly_contracts::ErrorEnvelope),
        (status = 409, body = newsly_contracts::ErrorEnvelope),
        (status = 422, body = newsly_contracts::ErrorEnvelope),
        (status = 500, body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn finish_audio_episode_playback(
    State(state): State<AppState>,
    headers: HeaderMap,
    current_user: AuthenticatedUser,
    stamp: RouteOwnershipStamp,
    path: Result<Path<i64>, PathRejection>,
) -> Result<Json<AudioEpisodeResponse>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    let episode_id = positive_path_id(path, "audio_episode_id", &request_id)?;
    let mut transaction =
        crate::write_support::begin_fenced(state.database.pool(), &stamp, &request_id).await?;
    let episode = find_user_audio_episode_for_update(&mut transaction, current_user.id, episode_id)
        .await
        .map_err(|error| internal_error(error, &request_id))?
        .ok_or_else(|| not_found("Audio episode", &request_id))?;
    if episode.status != "completed" {
        return Err(conflict("Audio episode is not ready", &request_id));
    }
    mark_audio_episode_sources_read(&mut transaction, &episode, AudioEpisodeReadTrigger::Finish)
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    crate::write_support::commit(transaction, &request_id).await?;
    present_audio_episode(episode.into(), &request_id).map(Json)
}

#[utoipa::path(
    post,
    path = "/api/content/audio-episodes/{audio_episode_id}/share",
    operation_id = "enableContentAudioEpisodePublicShare",
    tag = "content",
    params(("audio_episode_id" = i64, Path, minimum = 1)),
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, body = AudioEpisodeShareResponse),
        (status = 400, body = newsly_contracts::ErrorEnvelope),
        (status = 401, body = newsly_contracts::ErrorEnvelope),
        (status = 404, body = newsly_contracts::ErrorEnvelope),
        (status = 409, body = newsly_contracts::ErrorEnvelope),
        (status = 422, body = newsly_contracts::ErrorEnvelope),
        (status = 500, body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn enable_audio_episode_public_share(
    State(state): State<AppState>,
    headers: HeaderMap,
    current_user: AuthenticatedUser,
    stamp: RouteOwnershipStamp,
    path: Result<Path<i64>, PathRejection>,
) -> Result<Json<AudioEpisodeShareResponse>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    let episode_id = positive_path_id(path, "audio_episode_id", &request_id)?;
    let nonce = random_share_nonce().map_err(|error| internal_error(error, &request_id))?;
    let token = state
        .auth
        .issue_audio_episode_share_token(episode_id, &nonce)
        .map_err(|error| internal_error(error, &request_id))?;
    let token_hash = hash_token(&token);
    let mut transaction =
        crate::write_support::begin_fenced(state.database.pool(), &stamp, &request_id).await?;
    let outcome = newsly_db::enable_audio_episode_share(
        &mut transaction,
        current_user.id,
        episode_id,
        &nonce,
        &token_hash,
    )
    .await
    .map_err(|error| internal_error(error, &request_id))?;
    match outcome {
        AudioEpisodeShareOutcome::Enabled => {}
        AudioEpisodeShareOutcome::NotFound => {
            return Err(not_found("Audio episode", &request_id));
        }
        AudioEpisodeShareOutcome::WrongKind => {
            return Err(bad_request(
                "Only custom narrations can be shared",
                &request_id,
            ));
        }
        AudioEpisodeShareOutcome::NotReady => {
            return Err(conflict("Narration is not ready to share", &request_id));
        }
    }
    crate::write_support::commit(transaction, &request_id).await?;
    let page_path = format!("/audio/share/{token}/");
    let audio_path = format!("/audio/share/{token}/audio");
    Ok(Json(AudioEpisodeShareResponse {
        share_enabled: true,
        share_page_url: Some(external_url(&headers, &page_path, &request_id)?),
        share_audio_url: Some(external_url(&headers, &audio_path, &request_id)?),
    }))
}

#[utoipa::path(
    delete,
    path = "/api/content/audio-episodes/{audio_episode_id}/share",
    operation_id = "disableContentAudioEpisodePublicShare",
    tag = "content",
    params(("audio_episode_id" = i64, Path, minimum = 1)),
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, body = AudioEpisodeShareResponse),
        (status = 401, body = newsly_contracts::ErrorEnvelope),
        (status = 404, body = newsly_contracts::ErrorEnvelope),
        (status = 409, body = newsly_contracts::ErrorEnvelope),
        (status = 422, body = newsly_contracts::ErrorEnvelope),
        (status = 500, body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn disable_audio_episode_public_share(
    State(state): State<AppState>,
    headers: HeaderMap,
    current_user: AuthenticatedUser,
    stamp: RouteOwnershipStamp,
    path: Result<Path<i64>, PathRejection>,
) -> Result<Json<AudioEpisodeShareResponse>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    let episode_id = positive_path_id(path, "audio_episode_id", &request_id)?;
    let mut transaction =
        crate::write_support::begin_fenced(state.database.pool(), &stamp, &request_id).await?;
    let disabled =
        newsly_db::disable_audio_episode_share(&mut transaction, current_user.id, episode_id)
            .await
            .map_err(|error| internal_error(error, &request_id))?;
    if !disabled {
        return Err(not_found("Audio episode", &request_id));
    }
    crate::write_support::commit(transaction, &request_id).await?;
    Ok(Json(AudioEpisodeShareResponse {
        share_enabled: false,
        share_page_url: None,
        share_audio_url: None,
    }))
}

#[utoipa::path(
    get,
    path = "/api/content/audio-episodes/{audio_episode_id}/stream",
    operation_id = "streamContentAudioEpisode",
    tag = "content",
    params(("audio_episode_id" = i64, Path, minimum = 1)),
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, description = "Generated or followed MP3 stream", content_type = "audio/mpeg"),
        (status = 206, description = "Generated audio byte range"),
        (status = 401, body = newsly_contracts::ErrorEnvelope),
        (status = 404, body = newsly_contracts::ErrorEnvelope),
        (status = 409, body = newsly_contracts::ErrorEnvelope),
        (status = 416, description = "Requested audio range is not satisfiable"),
        (status = 422, body = newsly_contracts::ErrorEnvelope),
        (status = 500, body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn stream_audio_episode(
    State(state): State<AppState>,
    headers: HeaderMap,
    current_user: AuthenticatedUser,
    stamp: RouteOwnershipStamp,
    path: Result<Path<i64>, PathRejection>,
) -> Result<Response, ApiError> {
    let request_id = request_id_from_headers(&headers);
    let episode_id = positive_path_id(path, "audio_episode_id", &request_id)?;
    let mut transaction =
        crate::write_support::begin_fenced(state.database.pool(), &stamp, &request_id).await?;
    let mut episode =
        find_user_audio_episode_for_update(&mut transaction, current_user.id, episode_id)
            .await
            .map_err(|error| internal_error(error, &request_id))?
            .ok_or_else(|| not_found("Audio episode", &request_id))?;
    mark_audio_episode_sources_read(&mut transaction, &episode, AudioEpisodeReadTrigger::Play)
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    if episode.status == "completed" {
        crate::write_support::commit(transaction, &request_id).await?;
        let stored_path = episode
            .audio_storage_path
            .as_deref()
            .ok_or_else(|| not_found("Audio file", &request_id))?;
        return stream_stored_audio(
            &state,
            stored_path,
            episode_id,
            &episode.audio_content_type,
            headers.get(RANGE).and_then(|value| value.to_str().ok()),
            &request_id,
        )
        .await;
    }
    let processing_is_fresh = episode.status == "processing"
        && episode
            .started_at
            .is_some_and(|started| Utc::now().signed_duration_since(started).num_minutes() < 15);
    if !processing_is_fresh {
        if let Some(reset) =
            reset_audio_episode_for_generation(&mut transaction, current_user.id, episode_id)
                .await
                .map_err(|error| internal_error(error, &request_id))?
        {
            episode = reset;
        }
        enqueue_generation(&state, &mut transaction, &episode)
            .await
            .map_err(|error| internal_error(error, &request_id))?;
    }
    crate::write_support::commit(transaction, &request_id).await?;

    let follow_state = state.clone();
    let stream = io_byte_stream(async_stream::try_stream! {
        let deadline = tokio::time::Instant::now() + STREAM_FOLLOW_TIMEOUT;
        loop {
            if tokio::time::Instant::now() >= deadline {
                Err::<(), std::io::Error>(std::io::Error::new(
                    std::io::ErrorKind::TimedOut,
                    "audio generation timed out",
                ))?;
            }
            let current = find_user_audio_episode(
                follow_state.database.pool(),
                current_user.id,
                episode_id,
            )
            .await
            .map_err(std::io::Error::other)?
            .ok_or_else(|| std::io::Error::new(std::io::ErrorKind::NotFound, "audio episode disappeared"))?;
            match current.status.as_str() {
                "completed" => {
                    let path = current.audio_storage_path.as_deref().ok_or_else(|| {
                        std::io::Error::new(std::io::ErrorKind::NotFound, "audio file is missing")
                    })?;
                    let mut file = follow_state.audio_storage.open(path).await.map_err(std::io::Error::other)?;
                    let mut buffer = vec![0_u8; AUDIO_CHUNK_BYTES];
                    loop {
                        let read = file.read(&mut buffer).await?;
                        if read == 0 {
                            break;
                        }
                        yield Bytes::copy_from_slice(&buffer[..read]);
                    }
                    break;
                }
                "failed" => {
                    Err::<(), std::io::Error>(std::io::Error::other(
                        "audio generation failed",
                    ))?;
                }
                _ => tokio::time::sleep(STREAM_POLL_INTERVAL).await,
            }
        }
    });
    Ok(audio_stream_response(
        Body::from_stream(stream),
        episode_id,
        "audio/mpeg",
        None,
    ))
}

fn io_byte_stream<S>(stream: S) -> S
where
    S: Stream<Item = Result<Bytes, std::io::Error>> + Send + 'static,
{
    stream
}

pub(super) async fn serve_shared_audio_episode(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(token): Path<String>,
) -> Result<Html<String>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    let episode = shared_episode(&state, &token, &request_id).await?;
    let title = escape_html(&episode.title);
    let audio_url = external_url(
        &headers,
        &format!("/audio/share/{token}/audio"),
        &request_id,
    )?;
    let audio_url = escape_html_attribute(&audio_url);
    Ok(Html(format!(
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><title>{title}</title><style>:root{{color-scheme:light dark}}body{{margin:0;min-height:100vh;display:grid;place-items:center;font-family:-apple-system,BlinkMacSystemFont,\"Segoe UI\",sans-serif;background:#f7f4ef;color:#201d1a}}main{{width:min(92vw,680px);padding:32px 24px}}h1{{margin:0 0 18px;font-size:clamp(1.75rem,4vw,3rem);line-height:1.05}}audio{{width:100%;margin:8px 0 18px}}a{{color:#8b3a2f;font-weight:650}}</style></head><body><main><h1>{title}</h1><audio controls preload=\"metadata\" src=\"{audio_url}\"></audio><p><a href=\"{audio_url}\">Open direct audio link</a></p></main></body></html>"
    )))
}

pub(super) async fn serve_shared_audio_episode_audio(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(token): Path<String>,
) -> Result<Response, ApiError> {
    let request_id = request_id_from_headers(&headers);
    let episode = shared_episode(&state, &token, &request_id).await?;
    let stored_path = episode
        .audio_storage_path
        .as_deref()
        .ok_or_else(|| not_found("Audio file", &request_id))?;
    stream_stored_audio(
        &state,
        stored_path,
        episode.id,
        &episode.audio_content_type,
        headers.get(RANGE).and_then(|value| value.to_str().ok()),
        &request_id,
    )
    .await
}

async fn create_and_enqueue(
    state: &AppState,
    stamp: &RouteOwnershipStamp,
    request_id: &str,
    input: NewAudioEpisode<'_>,
) -> Result<AudioEpisodeRecord, ApiError> {
    let mut transaction =
        crate::write_support::begin_fenced(state.database.pool(), stamp, request_id).await?;
    let episode = upsert_audio_episode(&mut transaction, &input)
        .await
        .map_err(|error| internal_error(error, request_id))?;
    if episode.status != "completed" {
        enqueue_generation(state, &mut transaction, &episode)
            .await
            .map_err(|error| internal_error(error, request_id))?;
    }
    crate::write_support::commit(transaction, request_id).await?;
    Ok(episode)
}

async fn enqueue_generation(
    state: &AppState,
    transaction: &mut sqlx::Transaction<'_, sqlx::Postgres>,
    episode: &AudioEpisodeRecord,
) -> Result<(), newsly_queue::QueueError> {
    let mut request = EnqueueRequest::new(TaskType::GenerateAudioEpisode);
    request.payload = Some(
        json!({"audio_episode_id": episode.id, "user_id": episode.user_id})
            .as_object()
            .expect("audio episode payload is an object")
            .clone(),
    );
    request.dedupe_key = Some(format!("audio_episode:{}", episode.id));
    request.owner_user_id = Some(episode.user_id);
    QueueKernel::new(state.database.pool().clone())
        .enqueue_many_in_transaction(transaction, vec![request])
        .await?;
    Ok(())
}

async fn mark_play_started(
    state: &AppState,
    stamp: &RouteOwnershipStamp,
    user_id: i64,
    episode_id: i64,
    request_id: &str,
) -> Result<AudioEpisodeRecord, ApiError> {
    let mut transaction =
        crate::write_support::begin_fenced(state.database.pool(), stamp, request_id).await?;
    let episode = find_user_audio_episode_for_update(&mut transaction, user_id, episode_id)
        .await
        .map_err(|error| internal_error(error, request_id))?
        .ok_or_else(|| not_found("Audio episode", request_id))?;
    mark_audio_episode_sources_read(&mut transaction, &episode, AudioEpisodeReadTrigger::Play)
        .await
        .map_err(|error| internal_error(error, request_id))?;
    crate::write_support::commit(transaction, request_id).await?;
    Ok(episode)
}

async fn load_content_body(
    state: &AppState,
    user_id: i64,
    content_id: i64,
    request_id: &str,
) -> Result<String, ApiError> {
    let projection = find_visible_content_body(
        state.database.pool(),
        user_id,
        content_id,
        newsly_db::ContentBodyVariant::Source,
    )
    .await
    .map_err(|error| internal_error(error, request_id))?
    .ok_or_else(|| not_found("Content", request_id))?;
    let stored = if let Some(pointer) = &projection.pointer {
        state
            .content_body_store
            .get_text(&pointer.storage_key)
            .await
            .map_err(|error| internal_error(error, request_id))?
    } else {
        None
    };
    stored
        .or(projection.fallback_text)
        .filter(|text| !text.trim().is_empty())
        .ok_or_else(|| bad_request("No article or transcript text is available", request_id))
}

fn content_source_payload(
    content: &newsly_db::ContentDetailProjection,
    body: &str,
    max_chars: usize,
) -> Value {
    let normalized = body.trim();
    let (source_text, strategy) = excerpt_longform(normalized, max_chars);
    json!({
        "content_id": content.id,
        "content_type": content.content_type,
        "title": content_source_title(content),
        "source": content.source,
        "platform": content.platform,
        "url": content.url,
        "publication_date": content.publication_date,
        "summary": extract_content_summary(&content.content_metadata),
        "source_text": source_text,
        "source_text_excerpt_strategy": strategy,
        "source_text_truncated": normalized.chars().count() > max_chars,
        "source_text_chars": normalized.chars().count(),
        "source_text_included_chars": source_text.chars().count(),
    })
}

fn news_source_payload(item: &newsly_db::NewsItemProjection) -> Value {
    json!({
        "id": item.id,
        "title": news_display_title(item),
        "source": item.source_label,
        "platform": item.platform,
        "published_at": item.published_at,
        "summary": item.summary_text,
        "key_points": item.summary_key_points,
        "article_url": item.article_url.as_ref().or(item.canonical_story_url.as_ref()),
        "discussion_url": item.discussion_url.as_ref().or(item.canonical_item_url.as_ref()),
    })
}

fn content_display_title(content: &newsly_db::ContentDetailProjection) -> String {
    let metadata = content
        .content_metadata
        .as_object()
        .map(newsly_db::content_metadata::runtime_metadata)
        .unwrap_or_default();
    newsly_db::content_metadata::source_presentation::content_display_title(
        content.title.as_deref(),
        &metadata,
    )
    .or_else(|| {
        first_metadata_text(&content.content_metadata, &["title", "episode_title"])
            .map(str::to_owned)
    })
    .unwrap_or_else(|| format!("Content {}", content.id))
}

// Keep source provenance byte-equivalent to existing snapshots: title participates in the hash.
fn content_source_title(content: &newsly_db::ContentDetailProjection) -> String {
    content
        .title
        .as_deref()
        .map(str::trim)
        .filter(|title| !title.is_empty())
        .or_else(|| first_metadata_text(&content.content_metadata, &["title", "episode_title"]))
        .map_or_else(|| format!("Content {}", content.id), ToOwned::to_owned)
}

fn news_display_title(item: &newsly_db::NewsItemProjection) -> String {
    first_metadata_text(
        &item.raw_metadata,
        &["summary_title", "article_title", "title", "name"],
    )
    .or_else(|| {
        item.summary_text
            .as_deref()
            .map(str::trim)
            .filter(|value| !value.is_empty())
    })
    .map_or_else(|| format!("News item {}", item.id), ToOwned::to_owned)
}

fn first_metadata_text<'a>(metadata: &'a Value, keys: &[&str]) -> Option<&'a str> {
    keys.iter().find_map(|key| {
        metadata
            .get(*key)
            .and_then(Value::as_str)
            .map(str::trim)
            .filter(|value| !value.is_empty())
    })
}

fn extract_content_summary(metadata: &Value) -> Value {
    match metadata.get("summary") {
        Some(Value::Object(summary)) => Value::Object(summary.clone()),
        Some(Value::String(summary)) => json!({"overview": summary, "key_points": []}),
        _ => json!({"overview": Value::Null, "key_points": []}),
    }
}

fn excerpt_longform(body: &str, max_chars: usize) -> (String, &'static str) {
    let chars = body.chars().collect::<Vec<_>>();
    if chars.len() <= max_chars {
        return (body.to_owned(), "full");
    }
    let bounded = max_chars.max(1_000);
    let head = usize::max((bounded * 44) / 100, 400);
    let middle = usize::max((bounded * 25) / 100, 250);
    let tail = usize::max(bounded.saturating_sub(head + middle), 350);
    let middle_start = chars.len().saturating_sub(middle) / 2;
    let mut result = String::new();
    result.push_str("\n\n[Source opening excerpt]\n");
    result.extend(chars[..head.min(chars.len())].iter());
    result.push_str("\n\n[Source middle excerpt]\n");
    result.extend(chars[middle_start..(middle_start + middle).min(chars.len())].iter());
    result.push_str("\n\n[Source closing excerpt]\n");
    result.extend(chars[chars.len().saturating_sub(tail)..].iter());
    (result, "head_middle_tail")
}

fn custom_narration_title(snapshot: &Value, first_reader_title: Option<&str>) -> String {
    let Some(items) = snapshot.get("items").and_then(Value::as_array) else {
        return "Custom narration".to_owned();
    };
    let first_title = first_reader_title
        .or_else(|| {
            items
                .first()
                .and_then(|item| item.get("title"))
                .and_then(Value::as_str)
        })
        .map(str::trim)
        .filter(|title| !title.is_empty())
        .unwrap_or("Selected sources");
    if items.len() == 1 {
        format!("Narration: {first_title}")
    } else {
        format!("Narration: {first_title} + {} more", items.len() - 1)
    }
}

async fn shared_episode(
    state: &AppState,
    token: &str,
    request_id: &str,
) -> Result<AudioEpisodeRecord, ApiError> {
    if token.len() > 4_096 {
        return Err(not_found("Share link", request_id));
    }
    let (episode_id, nonce) = state
        .auth
        .decode_audio_episode_share_token(token)
        .map_err(|_| not_found("Share link", request_id))?;
    find_shared_audio_episode(
        state.database.pool(),
        episode_id,
        &nonce,
        &hash_token(token),
    )
    .await
    .map_err(|error| internal_error(error, request_id))?
    .ok_or_else(|| not_found("Share link", request_id))
}

fn normalize_ids(ids: Vec<i64>, label: &str, request_id: &str) -> Result<Vec<i64>, ApiError> {
    if ids.iter().any(|id| *id <= 0) {
        return Err(bad_request(
            format!("{label} ids must be positive"),
            request_id,
        ));
    }
    let mut seen = HashSet::new();
    Ok(ids.into_iter().filter(|id| seen.insert(*id)).collect())
}

fn sum_item_usize(items: &[Value], field: &str) -> usize {
    items
        .iter()
        .filter_map(|item| item.get(field).and_then(Value::as_u64))
        .filter_map(|value| usize::try_from(value).ok())
        .sum()
}

fn random_share_nonce() -> Result<String, getrandom::Error> {
    let mut bytes = [0_u8; 24];
    getrandom::fill(&mut bytes)?;
    Ok(URL_SAFE_NO_PAD.encode(bytes))
}

fn hash_token(token: &str) -> String {
    hex_encode(&Sha256::digest(token.as_bytes()))
}

fn external_url(headers: &HeaderMap, path: &str, request_id: &str) -> Result<String, ApiError> {
    let configured = env::var("PUBLIC_BASE_URL")
        .ok()
        .map(|value| value.trim().to_owned())
        .filter(|value| !value.is_empty());
    let mut base = if let Some(configured) = configured {
        let parsed = Url::parse(&configured).map_err(|error| internal_error(error, request_id))?;
        if parsed.host().is_none()
            || !matches!(parsed.scheme(), "http" | "https")
            || !parsed.username().is_empty()
            || parsed.password().is_some()
            || parsed.query().is_some()
            || parsed.fragment().is_some()
            || !matches!(parsed.path(), "" | "/")
            || (env::var("ENVIRONMENT").is_ok_and(|value| value.eq_ignore_ascii_case("production"))
                && parsed.scheme() != "https")
        {
            return Err(internal_error("PUBLIC_BASE_URL is invalid", request_id));
        }
        parsed
    } else {
        if env::var("ENVIRONMENT").is_ok_and(|value| value.eq_ignore_ascii_case("production")) {
            return Err(internal_error(
                "PUBLIC_BASE_URL is required in production",
                request_id,
            ));
        }
        let host = headers
            .get(HOST)
            .and_then(|value| value.to_str().ok())
            .ok_or_else(|| internal_error("request Host header is missing", request_id))?;
        let scheme = headers
            .get("x-forwarded-proto")
            .and_then(|value| value.to_str().ok())
            .and_then(|value| value.split(',').next())
            .map(str::trim)
            .filter(|value| matches!(*value, "http" | "https"))
            .unwrap_or("http");
        Url::parse(&format!("{scheme}://{host}"))
            .map_err(|error| internal_error(error, request_id))?
    };
    base.set_path(path);
    base.set_query(None);
    base.set_fragment(None);
    Ok(base.to_string())
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

fn decode_query<T>(
    query: Result<Query<T>, QueryRejection>,
    request_id: &str,
) -> Result<Query<T>, ApiError> {
    query.map_err(|rejection| validation_error(rejection.body_text(), request_id))
}

fn validation_error(message: impl Into<String>, request_id: &str) -> ApiError {
    ApiError::validation(message, request_id)
}

fn conflict(message: &str, request_id: &str) -> ApiError {
    ApiError::new(
        StatusCode::CONFLICT,
        "conflict",
        message,
        request_id.to_owned(),
    )
}

fn escape_html(value: &str) -> String {
    let mut escaped = String::with_capacity(value.len());
    for character in value.chars() {
        match character {
            '&' => escaped.push_str("&amp;"),
            '<' => escaped.push_str("&lt;"),
            '>' => escaped.push_str("&gt;"),
            '"' => escaped.push_str("&quot;"),
            '\'' => escaped.push_str("&#x27;"),
            _ => escaped.push(character),
        }
    }
    escaped
}

fn escape_html_attribute(value: &str) -> String {
    escape_html(value)
}

#[cfg(test)]
mod title_tests;
