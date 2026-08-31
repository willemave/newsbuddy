use std::collections::{BTreeMap, BTreeSet};
use std::error::Error;
use std::sync::Arc;

use futures_util::stream::{self, StreamExt};
use newsly_db::{FeedBackfillEntry, FeedBackfillPlan, persist_feed_backfill};
use newsly_providers::{ContentMiscGateway, FeedEntryHit};
use newsly_queue::{EnqueueRequest, OwnedWorkPlan, QueueKernel, TaskResult, TaskType};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use sqlx::{FromRow, PgPool, Postgres, Transaction};

use crate::{
    HandlerExecution, HandlerFinalizerFuture, HandlerFuture, LeaseHealth, TaskFinalizer,
    TaskFinalizerResult, TaskHandler,
};

const MAX_CONCURRENT_FEEDS: usize = 4;

#[derive(Debug, Clone)]
pub struct FeedBackfillWorkerServices {
    pool: PgPool,
    queue: QueueKernel,
    provider: ContentMiscGateway,
}

impl FeedBackfillWorkerServices {
    pub const fn new(pool: PgPool, queue: QueueKernel, provider: ContentMiscGateway) -> Self {
        Self {
            pool,
            queue,
            provider,
        }
    }
}

#[derive(Debug, Clone)]
pub struct BackfillFeedsHandler {
    services: Arc<FeedBackfillWorkerServices>,
}

impl BackfillFeedsHandler {
    pub fn new(services: Arc<FeedBackfillWorkerServices>) -> Self {
        Self { services }
    }
}

impl TaskHandler for BackfillFeedsHandler {
    fn task_type(&self) -> TaskType {
        TaskType::BackfillFeeds
    }

    fn execute(&self, plan: Arc<OwnedWorkPlan>, _lease: LeaseHealth) -> HandlerFuture<'_> {
        let services = Arc::clone(&self.services);
        Box::pin(async move { execute_backfill(&services, &plan).await })
    }
}

async fn execute_backfill(
    services: &FeedBackfillWorkerServices,
    task: &OwnedWorkPlan,
) -> HandlerExecution {
    let request = match parse_request(task) {
        Ok(request) => request,
        Err(message) => {
            return HandlerExecution::from_result(TaskResult::fail(Some(message), false));
        }
    };
    let prepared = match prepare_configs(&services.pool, &request).await {
        Ok(Some(prepared)) => prepared,
        Ok(None) => {
            return HandlerExecution::from_result(TaskResult::fail(
                Some(format!("active user {} does not exist", request.user_id)),
                false,
            ));
        }
        Err(error) => {
            return HandlerExecution::from_result(TaskResult::fail(Some(error.to_string()), true));
        }
    };
    let provider = services.provider.clone();
    let results = stream::iter(prepared.plans.iter().cloned())
        .map(move |plan| {
            let provider = provider.clone();
            async move {
                let result = provider
                    .fetch_feed_entries(&plan.feed_url, plan.target_limit)
                    .await
                    .map_err(|error| error.to_string());
                FeedFetchOutcome { plan, result }
            }
        })
        .buffer_unordered(MAX_CONCURRENT_FEEDS)
        .collect::<Vec<_>>()
        .await;
    let successes = results
        .iter()
        .filter(|outcome| outcome.result.is_ok())
        .count();
    let task_result = if successes > 0 {
        TaskResult::ok()
    } else {
        TaskResult::fail(
            Some("All onboarding feed backfills failed".to_owned()),
            true,
        )
    };
    HandlerExecution::with_finalizer(
        task_result,
        FeedBackfillFinalizer {
            queue: services.queue.clone(),
            request,
            prepared,
            results,
        },
    )
}

#[derive(Debug, Clone)]
struct FeedBackfillRequest {
    user_id: i64,
    config_ids: Vec<i64>,
    count: usize,
    first_edition_run_id: Option<i64>,
}

fn parse_request(task: &OwnedWorkPlan) -> Result<FeedBackfillRequest, String> {
    let user_id = task
        .owner_user_id
        .filter(|value| *value > 0)
        .ok_or_else(|| "backfill_feeds requires a positive owner user_id".to_owned())?;
    if task.payload.get("user_id").and_then(Value::as_i64) != Some(user_id) {
        return Err("backfill_feeds owner and payload user_id must match".to_owned());
    }
    let config_ids = task
        .payload
        .get("config_ids")
        .and_then(Value::as_array)
        .ok_or_else(|| "backfill_feeds config_ids must be an array".to_owned())?
        .iter()
        .map(|value| {
            value
                .as_i64()
                .filter(|value| *value > 0)
                .ok_or_else(|| "backfill_feeds config_ids contains an invalid id".to_owned())
        })
        .collect::<Result<BTreeSet<_>, _>>()?
        .into_iter()
        .collect::<Vec<_>>();
    if config_ids.is_empty() {
        return Err("backfill_feeds requires at least one config_id".to_owned());
    }
    let count = task
        .payload
        .get("count")
        .and_then(Value::as_u64)
        .and_then(|value| usize::try_from(value).ok())
        .filter(|value| (1..=50).contains(value))
        .ok_or_else(|| "backfill_feeds count must be between 1 and 50".to_owned())?;
    let first_edition_run_id = task
        .payload
        .get("first_edition_run_id")
        .map(|value| {
            value
                .as_i64()
                .filter(|value| *value > 0)
                .ok_or_else(|| "backfill_feeds first_edition_run_id is invalid".to_owned())
        })
        .transpose()?;
    Ok(FeedBackfillRequest {
        user_id,
        config_ids,
        count,
        first_edition_run_id,
    })
}

#[derive(Debug, Clone, FromRow)]
struct FeedConfigRow {
    id: i64,
    scraper_type: String,
    display_name: Option<String>,
    feed_url: Option<String>,
    config: Value,
}

#[derive(Debug, Clone)]
struct FeedConfigPlan {
    id: i64,
    scraper_type: String,
    display_name: Option<String>,
    feed_url: String,
    target_limit: usize,
    fingerprint: String,
}

#[derive(Debug)]
struct PreparedFeedConfigs {
    plans: Vec<FeedConfigPlan>,
}

async fn prepare_configs(
    pool: &PgPool,
    request: &FeedBackfillRequest,
) -> Result<Option<PreparedFeedConfigs>, sqlx::Error> {
    let mut transaction = pool.begin().await?;
    let active = sqlx::query_scalar::<_, bool>(
        "SELECT EXISTS(SELECT 1 FROM users WHERE id::bigint = $1 AND is_active IS TRUE)",
    )
    .bind(request.user_id)
    .fetch_one(&mut *transaction)
    .await?;
    if !active {
        transaction.rollback().await?;
        return Ok(None);
    }
    let rows = sqlx::query_as::<_, FeedConfigRow>(
        r"
        SELECT id::bigint AS id, scraper_type, display_name, feed_url, config::jsonb AS config
        FROM user_scraper_configs
        WHERE user_id::bigint = $1 AND is_active IS TRUE AND id::bigint = ANY($2::bigint[])
        ORDER BY id
        FOR SHARE
        ",
    )
    .bind(request.user_id)
    .bind(&request.config_ids)
    .fetch_all(&mut *transaction)
    .await?;
    transaction.commit().await?;
    let mut plans = Vec::new();
    for row in rows {
        let feed_url = row
            .feed_url
            .clone()
            .or_else(|| clean_string(row.config.get("feed_url")));
        if !matches!(
            row.scraper_type.as_str(),
            "substack" | "atom" | "podcast_rss"
        ) || feed_url.is_none()
        {
            continue;
        }
        let base_limit = row
            .config
            .get("limit")
            .and_then(Value::as_u64)
            .and_then(|value| usize::try_from(value).ok())
            .filter(|value| (1..=100).contains(value))
            .unwrap_or(10);
        plans.push(FeedConfigPlan {
            id: row.id,
            scraper_type: row.scraper_type.clone(),
            display_name: row.display_name.clone(),
            feed_url: feed_url.expect("checked above"),
            target_limit: base_limit.saturating_add(request.count).min(100),
            fingerprint: config_fingerprint(&row),
        });
    }
    Ok(Some(PreparedFeedConfigs { plans }))
}

fn clean_string(value: Option<&Value>) -> Option<String> {
    value
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(ToOwned::to_owned)
}

fn config_fingerprint(row: &FeedConfigRow) -> String {
    let value = json!({
        "id": row.id,
        "scraper_type": row.scraper_type,
        "display_name": row.display_name,
        "feed_url": row.feed_url,
        "config": row.config,
    });
    hex_sha256(&serde_json::to_vec(&value).expect("JSON values always serialize"))
}

#[derive(Debug)]
struct FeedFetchOutcome {
    plan: FeedConfigPlan,
    result: Result<Vec<FeedEntryHit>, String>,
}

#[derive(Debug)]
struct FeedBackfillFinalizer {
    queue: QueueKernel,
    request: FeedBackfillRequest,
    prepared: PreparedFeedConfigs,
    results: Vec<FeedFetchOutcome>,
}

impl FeedBackfillFinalizer {
    async fn apply_inner(
        &self,
        transaction: &mut Transaction<'static, Postgres>,
    ) -> Result<TaskFinalizerResult, Box<dyn Error + Send + Sync>> {
        let active = sqlx::query_scalar::<_, i64>(
            r"
            SELECT id::bigint FROM users
            WHERE id::bigint = $1 AND is_active IS TRUE
            FOR UPDATE
            ",
        )
        .bind(self.request.user_id)
        .fetch_optional(&mut **transaction)
        .await?;
        if active.is_none() {
            return Ok(TaskFinalizerResult::Override(TaskResult::fail(
                Some("backfill_feeds user is missing or inactive".to_owned()),
                false,
            )));
        }
        if !configs_still_match(transaction, self.request.user_id, &self.prepared.plans).await? {
            return Ok(TaskFinalizerResult::Override(TaskResult::fail(
                Some("feed configuration changed before backfill finalization".to_owned()),
                true,
            )));
        }

        let mut processed_counts = BTreeMap::<i64, i64>::new();
        let mut successful_ids = BTreeSet::<i64>::new();
        let mut process_requests = Vec::new();
        for outcome in &self.results {
            let Ok(provider_entries) = &outcome.result else {
                continue;
            };
            successful_ids.insert(outcome.plan.id);
            let plan = FeedBackfillPlan {
                content_id: 0,
                config_id: outcome.plan.id,
                scraper_type: outcome.plan.scraper_type.clone(),
                display_name: outcome.plan.display_name.clone(),
                feed_url: outcome.plan.feed_url.clone(),
                base_limit: outcome.plan.target_limit.saturating_sub(self.request.count),
                target_limit: outcome.plan.target_limit,
            };
            let entries = provider_entries
                .iter()
                .cloned()
                .map(|entry| FeedBackfillEntry {
                    url: entry.url,
                    title: entry.title,
                    source: entry.source.or_else(|| outcome.plan.display_name.clone()),
                    published_at: entry.published_at,
                    content_type: if outcome.plan.scraper_type == "podcast_rss" {
                        "podcast".to_owned()
                    } else {
                        "article".to_owned()
                    },
                })
                .collect::<Vec<_>>();
            let persisted =
                persist_feed_backfill(transaction, self.request.user_id, &plan, &entries).await?;
            processed_counts.insert(
                outcome.plan.id,
                i64::try_from(persisted.saved.saturating_add(persisted.duplicates))
                    .unwrap_or(i64::MAX),
            );
            process_requests.extend(persisted.content_ids.into_iter().map(|content_id| {
                let mut request = EnqueueRequest::new(TaskType::ProcessContent);
                request.content_id = Some(content_id);
                request
            }));
        }
        if !process_requests.is_empty() {
            self.queue
                .enqueue_many_in_transaction(transaction, process_requests)
                .await?;
        }
        if let Some(run_id) = self.request.first_edition_run_id
            && !record_first_edition_progress(
                transaction,
                self.request.user_id,
                run_id,
                &self.request.config_ids,
                &successful_ids,
                &processed_counts,
            )
            .await?
        {
            return Ok(TaskFinalizerResult::Override(TaskResult::fail(
                Some("Could not record onboarding feed progress".to_owned()),
                true,
            )));
        }
        Ok(TaskFinalizerResult::Keep)
    }
}

impl TaskFinalizer for FeedBackfillFinalizer {
    fn apply<'a>(
        &'a self,
        transaction: &'a mut Transaction<'static, Postgres>,
    ) -> HandlerFinalizerFuture<'a> {
        Box::pin(async move { self.apply_inner(transaction).await })
    }
}

async fn configs_still_match(
    transaction: &mut Transaction<'static, Postgres>,
    user_id: i64,
    plans: &[FeedConfigPlan],
) -> Result<bool, sqlx::Error> {
    if plans.is_empty() {
        return Ok(true);
    }
    let ids = plans.iter().map(|plan| plan.id).collect::<Vec<_>>();
    let rows = sqlx::query_as::<_, FeedConfigRow>(
        r"
        SELECT id::bigint AS id, scraper_type, display_name, feed_url, config::jsonb AS config
        FROM user_scraper_configs
        WHERE user_id::bigint = $1 AND is_active IS TRUE AND id::bigint = ANY($2::bigint[])
        ORDER BY id
        FOR UPDATE
        ",
    )
    .bind(user_id)
    .bind(ids)
    .fetch_all(&mut **transaction)
    .await?;
    let current = rows
        .iter()
        .map(|row| (row.id, config_fingerprint(row)))
        .collect::<BTreeMap<_, _>>();
    Ok(plans
        .iter()
        .all(|plan| current.get(&plan.id) == Some(&plan.fingerprint)))
}

async fn record_first_edition_progress(
    transaction: &mut Transaction<'static, Postgres>,
    user_id: i64,
    run_id: i64,
    config_ids: &[i64],
    successful_ids: &BTreeSet<i64>,
    processed_counts: &BTreeMap<i64, i64>,
) -> Result<bool, sqlx::Error> {
    let active_run = sqlx::query_scalar::<_, i64>(
        r"
        SELECT id::bigint FROM onboarding_first_edition_runs
        WHERE id::bigint = $1 AND user_id::bigint = $2 AND status = 'active'
        FOR UPDATE
        ",
    )
    .bind(run_id)
    .bind(user_id)
    .fetch_optional(&mut **transaction)
    .await?;
    if active_run.is_none() {
        return Ok(false);
    }
    let source_keys = config_ids
        .iter()
        .map(|config_id| format!("feed:{config_id}"))
        .collect::<Vec<_>>();
    let rows = sqlx::query_as::<_, (i64, String, String, i32)>(
        r"
        SELECT id::bigint, source_key, status, processed_item_count
        FROM onboarding_first_edition_sources
        WHERE run_id::bigint = $1 AND source_key = ANY($2::text[])
        ORDER BY id
        FOR UPDATE
        ",
    )
    .bind(run_id)
    .bind(&source_keys)
    .fetch_all(&mut **transaction)
    .await?;
    if rows.len() != source_keys.len() {
        return Ok(false);
    }
    let mut changed = false;
    for (row_id, source_key, status, old_count) in rows {
        let Some(config_id) = source_key
            .strip_prefix("feed:")
            .and_then(|value| value.parse::<i64>().ok())
        else {
            return Ok(false);
        };
        let outcome = if successful_ids.contains(&config_id) {
            "processed"
        } else {
            "unavailable"
        };
        let count = processed_counts.get(&config_id).copied().unwrap_or(0);
        if status == "processed" || (status == outcome && i64::from(old_count) == count) {
            continue;
        }
        sqlx::query(
            r"
            UPDATE onboarding_first_edition_sources
            SET status = $1, processed_item_count = $2,
                completed_at = coalesce(completed_at, timezone('UTC', now()))
            WHERE id::bigint = $3
            ",
        )
        .bind(outcome)
        .bind(i32::try_from(count).unwrap_or(i32::MAX))
        .bind(row_id)
        .execute(&mut **transaction)
        .await?;
        changed = true;
    }
    if changed {
        sqlx::query(
            "UPDATE onboarding_first_edition_runs SET revision = revision + 1 WHERE id::bigint = $1",
        )
        .bind(run_id)
        .execute(&mut **transaction)
        .await?;
    }
    Ok(true)
}

fn hex_sha256(value: &[u8]) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let digest = Sha256::digest(value);
    let mut output = String::with_capacity(digest.len() * 2);
    for byte in digest {
        output.push(char::from(HEX[usize::from(byte >> 4)]));
        output.push(char::from(HEX[usize::from(byte & 0x0f)]));
    }
    output
}
