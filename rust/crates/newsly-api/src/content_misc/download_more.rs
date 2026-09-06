use axum::Json;
use axum::extract::rejection::{JsonRejection, PathRejection};
use axum::extract::{Extension, Path, State};
use axum::http::{HeaderMap, StatusCode};
use newsly_contracts::{DownloadMoreRequest, DownloadMoreResponse};
use newsly_db::{
    FeedBackfillEntry, FeedBackfillOrigin, FeedBackfillPreparation, persist_feed_backfill,
    prepare_feed_backfill,
};
use newsly_providers::{FeedEntrySelection, FeedScrapeTarget, ScrapeGatewayError, ScrapedItem};
use newsly_queue::{EnqueueRequest, QueueKernel, TaskType};

use crate::auth::AuthenticatedUser;
use crate::error::ApiError;
use crate::gateway::RouteOwnershipStamp;
use crate::write_support::{
    bad_request, decode_json, internal_error, require_operation, verify_stamp,
};
use crate::{AppState, request_id_from_headers};

use super::{
    not_found_message, positive_path_id, queue_error, validation_error, verify_operation_now,
};

const DOWNLOAD_MORE_OPERATION_ID: &str = "downloadContentMoreFromSeries";

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
        (status = 502, description = "Feed provider failed", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
#[expect(
    clippy::too_many_lines,
    reason = "the route keeps ownership fencing and its one feed-backfill transaction linear"
)]
pub(crate) async fn download_more_from_series(
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
    let outcome = state
        .scrape
        .fetch_feed(&FeedScrapeTarget {
            known_urls: newsly_db::known_feed_urls(
                state.database.pool(),
                current_user.id,
                if plan.scraper_type == "podcast_rss" {
                    "podcast"
                } else {
                    "article"
                },
            )
            .await
            .map_err(|error| internal_error(error, &request_id))?,
            entry_selection: FeedEntrySelection::SkipKnown,
            config_id: plan.config_id,
            user_id: current_user.id,
            scraper_type: plan.scraper_type.clone(),
            display_name: plan.display_name.clone(),
            feed_url: plan.feed_url.clone(),
            limit: payload.count,
            fingerprint: "download-more".to_owned(),
        })
        .await
        .map_err(|error| scrape_provider_bad_gateway(&error, &request_id))?;
    if outcome.items.is_empty() && !outcome.item_errors.is_empty() {
        tracing::warn!(
            config_id = plan.config_id,
            diagnostic_count = outcome.item_errors.len(),
            "feed backfill produced no usable items"
        );
        return Err(unusable_feed_items(&request_id));
    }
    if !outcome.item_errors.is_empty() {
        tracing::warn!(
            config_id = plan.config_id,
            diagnostic_count = outcome.item_errors.len(),
            "feed backfill completed with item diagnostics"
        );
    }
    let scraped = outcome.items.len();
    let entries = outcome
        .items
        .into_iter()
        .filter_map(|item| match item {
            ScrapedItem::Content(entry) => Some(FeedBackfillEntry {
                url: entry.url,
                source_url: entry.source_url,
                title: entry.title,
                source: entry.source,
                platform: entry.platform,
                metadata: entry.metadata,
                published_at: entry.published_at,
                content_type: entry.content_type,
            }),
            ScrapedItem::News(_) => None,
        })
        .collect::<Vec<_>>();
    let mut transaction = state
        .database
        .pool()
        .begin()
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    verify_stamp(&mut transaction, &stamp, &request_id).await?;
    let persisted = persist_feed_backfill(
        &mut transaction,
        current_user.id,
        FeedBackfillOrigin::DownloadMore,
        &entries,
    )
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
        errors: outcome.item_errors.len() + persisted.rejected,
    }))
}

fn scrape_provider_bad_gateway(error: &ScrapeGatewayError, request_id: &str) -> ApiError {
    tracing::warn!(
        error = %error,
        diagnostic_code = error.diagnostic_code(),
        retryable = error.retryable(),
        "feed provider request failed"
    );
    ApiError::new(
        StatusCode::BAD_GATEWAY,
        error.diagnostic_code(),
        "Feed request failed",
        request_id,
    )
    .with_retryable(error.retryable())
}

fn unusable_feed_items(request_id: &str) -> ApiError {
    ApiError::new(
        StatusCode::BAD_GATEWAY,
        "feed_items_unusable",
        "Feed contained no usable items",
        request_id,
    )
    .with_retryable(true)
}
