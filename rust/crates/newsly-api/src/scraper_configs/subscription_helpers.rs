use super::{
    ApiError, AppState, BACKFILL_SUPPORTED_TYPES, EnqueueRequest, FeedSubscriptionOutcome,
    INITIAL_BACKFILL_COUNT, Json, QueueKernel, ScraperConfigProjection, ScraperConfigResponse,
    ScraperType, StatusCode, TaskType, Value, bad_request, config_response, internal_error, json,
    normalize_create_input, stats_for_record,
};

pub(super) async fn enqueue_initial_backfill(
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

pub(super) async fn enqueue_aggregator_activation(
    state: &AppState,
    transaction: &mut sqlx::Transaction<'static, sqlx::Postgres>,
    prior_demand: Option<&crate::aggregator_demand::Demand>,
    request_id: &str,
) -> Result<(), ApiError> {
    let Some(prior_demand) = prior_demand else {
        return Ok(());
    };
    crate::aggregator_demand::enqueue_new_demand(
        transaction,
        &QueueKernel::new(state.database.pool().clone()),
        prior_demand,
    )
    .await
    .map_err(|error| internal_error(error, request_id))
}

pub(super) async fn present_subscription(
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

pub(super) fn subscription_scraper_type(
    feed_type: &str,
    request_id: &str,
) -> Result<ScraperType, ApiError> {
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

pub(super) fn subscription_config(
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
