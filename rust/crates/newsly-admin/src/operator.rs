use std::collections::BTreeMap;
use std::fmt::Write as _;

use chrono::{DateTime, Duration, Utc};
use clap::ValueEnum;
use serde::Serialize;
use sqlx::{FromRow, PgPool};
use thiserror::Error;

const MAX_WINDOW_HOURS: i64 = 24 * 90;
const MAX_TOP_FAILURES: i64 = 100;
const MAX_RECENT_FAILURES: i64 = 200;
const ERROR_MESSAGE_LIMIT: i32 = 1_000;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct QueryWindow {
    pub since: DateTime<Utc>,
    pub until: DateTime<Utc>,
    pub window_hours: i64,
}

impl QueryWindow {
    /// Builds a bounded window ending at the current UTC time.
    ///
    /// # Errors
    ///
    /// Returns [`OperatorQueryError::InvalidWindowHours`] or
    /// [`OperatorQueryError::WindowTooLarge`] when the requested lookback is outside the
    /// operator limits.
    pub fn ending_now(window_hours: i64) -> Result<Self, OperatorQueryError> {
        Self::ending_at(Utc::now(), window_hours)
    }

    /// Builds a bounded window ending at an explicit UTC time.
    ///
    /// # Errors
    ///
    /// Returns [`OperatorQueryError::InvalidWindowHours`] or
    /// [`OperatorQueryError::WindowTooLarge`] when the requested lookback is outside the
    /// operator limits.
    pub fn ending_at(until: DateTime<Utc>, window_hours: i64) -> Result<Self, OperatorQueryError> {
        validate_window_hours(window_hours)?;
        Ok(Self {
            since: until - Duration::hours(window_hours),
            until,
            window_hours,
        })
    }

    /// Builds a bounded window from explicit UTC bounds.
    ///
    /// # Errors
    ///
    /// Returns [`OperatorQueryError::InvalidTimeWindow`] when the bounds are reversed, or
    /// [`OperatorQueryError::WindowTooLarge`] when they span more than the operator limit.
    pub fn from_bounds(
        since: DateTime<Utc>,
        until: DateTime<Utc>,
    ) -> Result<Self, OperatorQueryError> {
        if since > until {
            return Err(OperatorQueryError::InvalidTimeWindow);
        }
        let duration = until - since;
        if duration > Duration::hours(MAX_WINDOW_HOURS) {
            return Err(OperatorQueryError::WindowTooLarge {
                maximum_hours: MAX_WINDOW_HOURS,
            });
        }
        let window_hours = duration.num_hours().max(1);
        Ok(Self {
            since,
            until,
            window_hours,
        })
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, ValueEnum)]
pub enum UsageGroupBy {
    User,
    Feature,
    Operation,
    Provider,
    Vendor,
    Model,
    Source,
}

impl UsageGroupBy {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::User => "user",
            Self::Feature => "feature",
            Self::Operation => "operation",
            Self::Provider => "provider",
            Self::Vendor => "vendor",
            Self::Model => "model",
            Self::Source => "source",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct CountByStatus {
    pub total: i64,
    pub by_status: BTreeMap<String, i64>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct EventHealth {
    pub total: i64,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct UsageFreshness {
    pub latest_record_at: Option<DateTime<Utc>>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct HealthSnapshot {
    pub generated_at: DateTime<Utc>,
    pub content: CountByStatus,
    pub tasks: CountByStatus,
    pub events: EventHealth,
    pub usage: UsageFreshness,
}

impl HealthSnapshot {
    pub fn render_text(&self) -> String {
        format!(
            "Health snapshot:\n- content: {} total\n- tasks: {} total\n- events: {} total\n- latest usage record: {}",
            self.content.total,
            self.tasks.total,
            self.events.total,
            self.usage
                .latest_record_at
                .map_or_else(|| "none".to_owned(), |value| value.to_rfc3339())
        )
    }
}

#[derive(Debug, Clone, PartialEq, Eq, FromRow)]
struct StatusCountRow {
    label: String,
    count: i64,
}

#[derive(Debug, Clone, PartialEq, Eq, FromRow)]
struct UsageFreshnessRow {
    latest_record_at: Option<DateTime<Utc>>,
}

/// Loads the stable coarse health envelope using fixed read-only queries.
///
/// # Errors
///
/// Returns [`OperatorQueryError::Sqlx`] when `PostgreSQL` rejects a query or a result cannot be
/// decoded.
pub async fn load_health_snapshot(pool: &PgPool) -> Result<HealthSnapshot, OperatorQueryError> {
    let content_rows = sqlx::query_as::<_, StatusCountRow>(
        r"
        SELECT COALESCE(status, 'unknown') AS label, COUNT(*)::bigint AS count
        FROM contents
        GROUP BY COALESCE(status, 'unknown')
        ORDER BY label
        ",
    )
    .fetch_all(pool)
    .await?;
    let task_rows = sqlx::query_as::<_, StatusCountRow>(
        r"
        SELECT COALESCE(status, 'unknown') AS label, COUNT(*)::bigint AS count
        FROM processing_tasks
        GROUP BY COALESCE(status, 'unknown')
        ORDER BY label
        ",
    )
    .fetch_all(pool)
    .await?;
    let usage = sqlx::query_as::<_, UsageFreshnessRow>(
        r"
        SELECT MAX(created_at) AT TIME ZONE 'UTC' AS latest_record_at
        FROM vendor_usage_records
        ",
    )
    .fetch_one(pool)
    .await?;
    let event_count: i64 = sqlx::query_scalar("SELECT COUNT(*)::bigint FROM event_logs")
        .fetch_one(pool)
        .await?;

    Ok(HealthSnapshot {
        generated_at: Utc::now(),
        content: count_by_status(content_rows),
        tasks: count_by_status(task_rows),
        events: EventHealth { total: event_count },
        usage: UsageFreshness {
            latest_record_at: usage.latest_record_at,
        },
    })
}

fn count_by_status(rows: Vec<StatusCountRow>) -> CountByStatus {
    let by_status = rows
        .into_iter()
        .map(|row| (row.label, row.count))
        .collect::<BTreeMap<_, _>>();
    CountByStatus {
        total: by_status.values().sum(),
        by_status,
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, FromRow)]
pub struct QueueTaskBacklog {
    pub queue_name: String,
    pub task_type: String,
    pub pending_count: i64,
    pub oldest_pending_age_seconds: Option<f64>,
}

#[derive(Debug, Clone, PartialEq, Serialize, FromRow)]
pub struct QueueProcessingBacklog {
    pub queue_name: String,
    pub task_type: String,
    pub processing_count: i64,
    pub oldest_processing_age_seconds: Option<f64>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, FromRow)]
pub struct QueueTaskActivity {
    pub queue_name: String,
    pub task_type: String,
    pub enqueued_count: i64,
    pub completed_count: i64,
    pub failed_count: i64,
}

#[derive(Debug, Clone, PartialEq, Serialize, FromRow)]
pub struct QueueTaskLatency {
    pub queue_name: String,
    pub task_type: String,
    pub sample_count: i64,
    pub ready_wait_p50_seconds: f64,
    pub ready_wait_p95_seconds: f64,
    pub total_wait_p50_seconds: f64,
    pub total_wait_p95_seconds: f64,
    pub run_time_p50_seconds: f64,
    pub run_time_p95_seconds: f64,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, FromRow)]
pub struct QueueRetryBucket {
    pub retry_count: i32,
    pub pending_count: i64,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, FromRow)]
pub struct QueueFailureSummary {
    pub task_type: String,
    pub error_message: String,
    pub count: i64,
}

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct QueueHealthSnapshot {
    pub generated_at: DateTime<Utc>,
    pub window_hours: i64,
    pub pending: Vec<QueueTaskBacklog>,
    pub processing: Vec<QueueProcessingBacklog>,
    pub activity: Vec<QueueTaskActivity>,
    pub latency: Vec<QueueTaskLatency>,
    pub processing_count: i64,
    pub expired_lease_count: i64,
    pub retry_buckets: Vec<QueueRetryBucket>,
    pub recent_failed_count: i64,
    pub top_failures: Vec<QueueFailureSummary>,
}

impl QueueHealthSnapshot {
    pub fn render_text(&self) -> String {
        let mut text = format!(
            "Queue health:\n- processing: {}\n- expired leases: {}\n- recent failures ({}h): {}",
            self.processing_count,
            self.expired_lease_count,
            self.window_hours,
            self.recent_failed_count
        );
        if !self.pending.is_empty() {
            text.push_str("\nPending:");
            for row in self.pending.iter().take(10) {
                let age = row
                    .oldest_pending_age_seconds
                    .map_or_else(|| "unknown".to_owned(), |value| format!("{value:.0}s"));
                let _ = write!(
                    text,
                    "\n- {}/{}: {} pending, oldest {}",
                    row.queue_name, row.task_type, row.pending_count, age
                );
            }
        }
        if !self.processing.is_empty() {
            text.push_str("\nProcessing:");
            for row in self.processing.iter().take(10) {
                let age = row
                    .oldest_processing_age_seconds
                    .map_or_else(|| "unknown".to_owned(), |value| format!("{value:.0}s"));
                let _ = write!(
                    text,
                    "\n- {}/{}: {} processing, oldest {}",
                    row.queue_name, row.task_type, row.processing_count, age
                );
            }
        }
        if !self.retry_buckets.is_empty() {
            let buckets = self
                .retry_buckets
                .iter()
                .map(|row| format!("{}={}", row.retry_count, row.pending_count))
                .collect::<Vec<_>>()
                .join(", ");
            let _ = write!(text, "\nRetry buckets: {buckets}");
        }
        if !self.top_failures.is_empty() {
            text.push_str("\nTop failures:");
            for row in self.top_failures.iter().take(5) {
                let _ = write!(
                    text,
                    "\n- {}: {}x {}",
                    row.task_type, row.count, row.error_message
                );
            }
        }
        text
    }
}

#[derive(Debug, Clone, PartialEq, Eq, FromRow)]
struct QueueCounts {
    processing: i64,
    expired_leases: i64,
    recent_failed: i64,
}

/// Loads bounded queue backlog, lease, activity, latency, and grouped-failure aggregates.
///
/// # Errors
///
/// Returns a validation error when the window or result limit exceeds the operator bounds, and
/// [`OperatorQueryError::Sqlx`] when `PostgreSQL` rejects a query or a result cannot be decoded.
pub async fn load_queue_health(
    pool: &PgPool,
    window_hours: i64,
    top_errors_limit: i64,
) -> Result<QueueHealthSnapshot, OperatorQueryError> {
    validate_window_hours(window_hours)?;
    validate_limit(top_errors_limit, MAX_TOP_FAILURES)?;
    let window = QueryWindow::ending_now(window_hours)?;

    let (pending, processing) = load_queue_backlogs(pool).await?;
    let activity = load_queue_activity(pool, window).await?;
    let latency = load_queue_latency(pool, window).await?;
    let retry_buckets = load_retry_buckets(pool).await?;
    let counts = load_queue_counts(pool, window).await?;
    let top_failures = load_top_failures(pool, window, top_errors_limit).await?;

    Ok(QueueHealthSnapshot {
        generated_at: window.until,
        window_hours,
        pending,
        processing,
        activity,
        latency,
        processing_count: counts.processing,
        expired_lease_count: counts.expired_leases,
        retry_buckets,
        recent_failed_count: counts.recent_failed,
        top_failures,
    })
}

async fn load_queue_backlogs(
    pool: &PgPool,
) -> Result<(Vec<QueueTaskBacklog>, Vec<QueueProcessingBacklog>), OperatorQueryError> {
    let pending = sqlx::query_as::<_, QueueTaskBacklog>(
        r"
        SELECT
            COALESCE(queue_name, 'unknown') AS queue_name,
            COALESCE(task_type, 'unknown') AS task_type,
            COUNT(*)::bigint AS pending_count,
            EXTRACT(EPOCH FROM (
                timezone('UTC', clock_timestamp()) - MIN(COALESCE(available_at, created_at))
            ))::double precision AS oldest_pending_age_seconds
        FROM processing_tasks
        WHERE status = 'pending'
        GROUP BY queue_name, task_type
        ORDER BY MIN(COALESCE(available_at, created_at)), COUNT(*) DESC, queue_name, task_type
        ",
    )
    .fetch_all(pool)
    .await?;
    let processing = sqlx::query_as::<_, QueueProcessingBacklog>(
        r"
        SELECT
            COALESCE(queue_name, 'unknown') AS queue_name,
            COALESCE(task_type, 'unknown') AS task_type,
            COUNT(*)::bigint AS processing_count,
            EXTRACT(EPOCH FROM (
                timezone('UTC', clock_timestamp())
                - MIN(COALESCE(started_at, locked_at, created_at))
            ))::double precision AS oldest_processing_age_seconds
        FROM processing_tasks
        WHERE status = 'processing'
        GROUP BY queue_name, task_type
        ORDER BY MIN(COALESCE(started_at, locked_at, created_at)), COUNT(*) DESC,
                 queue_name, task_type
        ",
    )
    .fetch_all(pool)
    .await?;
    Ok((pending, processing))
}

async fn load_queue_activity(
    pool: &PgPool,
    window: QueryWindow,
) -> Result<Vec<QueueTaskActivity>, OperatorQueryError> {
    let activity = sqlx::query_as::<_, QueueTaskActivity>(
        r"
        SELECT
            COALESCE(queue_name, 'unknown') AS queue_name,
            COALESCE(task_type, 'unknown') AS task_type,
            COUNT(*) FILTER (
                WHERE created_at >= ($1::timestamptz AT TIME ZONE 'UTC')
                  AND created_at <= ($2::timestamptz AT TIME ZONE 'UTC')
            )::bigint AS enqueued_count,
            COUNT(*) FILTER (
                WHERE status = 'completed'
                  AND completed_at >= ($1::timestamptz AT TIME ZONE 'UTC')
                  AND completed_at <= ($2::timestamptz AT TIME ZONE 'UTC')
            )::bigint AS completed_count,
            COUNT(*) FILTER (
                WHERE status = 'failed'
                  AND completed_at >= ($1::timestamptz AT TIME ZONE 'UTC')
                  AND completed_at <= ($2::timestamptz AT TIME ZONE 'UTC')
            )::bigint AS failed_count
        FROM processing_tasks
        WHERE (created_at >= ($1::timestamptz AT TIME ZONE 'UTC')
               AND created_at <= ($2::timestamptz AT TIME ZONE 'UTC'))
           OR (completed_at >= ($1::timestamptz AT TIME ZONE 'UTC')
               AND completed_at <= ($2::timestamptz AT TIME ZONE 'UTC'))
        GROUP BY queue_name, task_type
        ORDER BY enqueued_count DESC, failed_count DESC, queue_name, task_type
        ",
    )
    .bind(window.since)
    .bind(window.until)
    .fetch_all(pool)
    .await?;
    Ok(activity)
}

async fn load_queue_latency(
    pool: &PgPool,
    window: QueryWindow,
) -> Result<Vec<QueueTaskLatency>, OperatorQueryError> {
    let latency = sqlx::query_as::<_, QueueTaskLatency>(
        r"
        SELECT
            COALESCE(queue_name, 'unknown') AS queue_name,
            COALESCE(task_type, 'unknown') AS task_type,
            COUNT(*)::bigint AS sample_count,
            COALESCE(percentile_cont(0.5) WITHIN GROUP (
                ORDER BY GREATEST(EXTRACT(EPOCH FROM started_at - available_at), 0)::double precision
            ), 0)::double precision AS ready_wait_p50_seconds,
            COALESCE(percentile_cont(0.95) WITHIN GROUP (
                ORDER BY GREATEST(EXTRACT(EPOCH FROM started_at - available_at), 0)::double precision
            ), 0)::double precision AS ready_wait_p95_seconds,
            COALESCE(percentile_cont(0.5) WITHIN GROUP (
                ORDER BY GREATEST(EXTRACT(EPOCH FROM started_at - created_at), 0)::double precision
            ), 0)::double precision AS total_wait_p50_seconds,
            COALESCE(percentile_cont(0.95) WITHIN GROUP (
                ORDER BY GREATEST(EXTRACT(EPOCH FROM started_at - created_at), 0)::double precision
            ), 0)::double precision AS total_wait_p95_seconds,
            COALESCE(percentile_cont(0.5) WITHIN GROUP (
                ORDER BY GREATEST(EXTRACT(EPOCH FROM completed_at - started_at), 0)::double precision
            ), 0)::double precision AS run_time_p50_seconds,
            COALESCE(percentile_cont(0.95) WITHIN GROUP (
                ORDER BY GREATEST(EXTRACT(EPOCH FROM completed_at - started_at), 0)::double precision
            ), 0)::double precision AS run_time_p95_seconds
        FROM processing_tasks
        WHERE status IN ('completed', 'failed')
          AND completed_at >= ($1::timestamptz AT TIME ZONE 'UTC')
          AND completed_at <= ($2::timestamptz AT TIME ZONE 'UTC')
          AND created_at IS NOT NULL
          AND available_at IS NOT NULL
          AND started_at IS NOT NULL
        GROUP BY queue_name, task_type
        ORDER BY sample_count DESC, queue_name, task_type
        ",
    )
    .bind(window.since)
    .bind(window.until)
    .fetch_all(pool)
    .await?;
    Ok(latency)
}

async fn load_retry_buckets(pool: &PgPool) -> Result<Vec<QueueRetryBucket>, OperatorQueryError> {
    let retry_buckets = sqlx::query_as::<_, QueueRetryBucket>(
        r"
        SELECT COALESCE(retry_count, 0)::integer AS retry_count,
               COUNT(*)::bigint AS pending_count
        FROM processing_tasks
        WHERE status = 'pending'
        GROUP BY COALESCE(retry_count, 0)
        ORDER BY retry_count
        ",
    )
    .fetch_all(pool)
    .await?;
    Ok(retry_buckets)
}

async fn load_queue_counts(
    pool: &PgPool,
    window: QueryWindow,
) -> Result<QueueCounts, OperatorQueryError> {
    let counts = sqlx::query_as::<_, QueueCounts>(
        r"
        SELECT
            COUNT(*) FILTER (WHERE status = 'processing')::bigint AS processing,
            COUNT(*) FILTER (
                WHERE status = 'processing'
                  AND lease_expires_at IS NOT NULL
                  AND lease_expires_at <= timezone('UTC', clock_timestamp())
            )::bigint AS expired_leases,
            COUNT(*) FILTER (
                WHERE status = 'failed'
                  AND COALESCE(completed_at, created_at)
                      >= ($1::timestamptz AT TIME ZONE 'UTC')
                  AND COALESCE(completed_at, created_at)
                      <= ($2::timestamptz AT TIME ZONE 'UTC')
            )::bigint AS recent_failed
        FROM processing_tasks
        ",
    )
    .bind(window.since)
    .bind(window.until)
    .fetch_one(pool)
    .await?;
    Ok(counts)
}

async fn load_top_failures(
    pool: &PgPool,
    window: QueryWindow,
    limit: i64,
) -> Result<Vec<QueueFailureSummary>, OperatorQueryError> {
    let top_failures = sqlx::query_as::<_, QueueFailureSummary>(
        r"
        SELECT
            COALESCE(task_type, 'unknown') AS task_type,
            LEFT(COALESCE(error_message, 'unknown'), $4) AS error_message,
            COUNT(*)::bigint AS count
        FROM processing_tasks
        WHERE status = 'failed'
          AND COALESCE(completed_at, created_at) >= ($1::timestamptz AT TIME ZONE 'UTC')
          AND COALESCE(completed_at, created_at) <= ($2::timestamptz AT TIME ZONE 'UTC')
        GROUP BY task_type, LEFT(COALESCE(error_message, 'unknown'), $4)
        ORDER BY count DESC, task_type
        LIMIT $3
        ",
    )
    .bind(window.since)
    .bind(window.until)
    .bind(limit)
    .bind(ERROR_MESSAGE_LIMIT)
    .fetch_all(pool)
    .await?;
    Ok(top_failures)
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, FromRow)]
pub struct RecentTaskFailure {
    pub id: i64,
    pub queue_name: String,
    pub task_type: String,
    pub content_id: Option<i64>,
    pub retry_count: i32,
    pub executor_runtime: String,
    pub error_message: Option<String>,
    pub created_at: DateTime<Utc>,
    pub completed_at: Option<DateTime<Utc>>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct RecentTaskFailures {
    pub generated_at: DateTime<Utc>,
    pub window_hours: i64,
    pub limit: i64,
    pub count: usize,
    pub truncated: bool,
    pub failures: Vec<RecentTaskFailure>,
}

impl RecentTaskFailures {
    pub fn render_text(&self) -> String {
        if self.failures.is_empty() {
            return format!("No failed tasks found in the last {}h.", self.window_hours);
        }
        let mut text = format!(
            "Recent task failures ({}h, showing {}):",
            self.window_hours, self.count
        );
        for row in &self.failures {
            let message = row.error_message.as_deref().unwrap_or("unknown");
            let _ = write!(
                text,
                "\n- [{}] task {} {}/{} retry {}: {}",
                row.completed_at.unwrap_or(row.created_at).to_rfc3339(),
                row.id,
                row.queue_name,
                row.task_type,
                row.retry_count,
                message
            );
        }
        if self.truncated {
            let _ = write!(
                text,
                "\n- additional failures omitted after limit {}",
                self.limit
            );
        }
        text
    }
}

/// Loads bounded recent task failures without reading or returning task payloads.
///
/// # Errors
///
/// Returns a validation error when the window or result limit exceeds the operator bounds, and
/// [`OperatorQueryError::Sqlx`] when `PostgreSQL` rejects the query or a result cannot be decoded.
pub async fn load_recent_task_failures(
    pool: &PgPool,
    window_hours: i64,
    limit: i64,
) -> Result<RecentTaskFailures, OperatorQueryError> {
    validate_window_hours(window_hours)?;
    validate_limit(limit, MAX_RECENT_FAILURES)?;
    let window = QueryWindow::ending_now(window_hours)?;
    let mut failures = sqlx::query_as::<_, RecentTaskFailure>(
        r"
        SELECT
            id::bigint,
            COALESCE(queue_name, 'unknown') AS queue_name,
            COALESCE(task_type, 'unknown') AS task_type,
            content_id::bigint,
            COALESCE(retry_count, 0)::integer AS retry_count,
            COALESCE(executor_runtime, 'unknown') AS executor_runtime,
            LEFT(error_message, $4) AS error_message,
            created_at AT TIME ZONE 'UTC' AS created_at,
            completed_at AT TIME ZONE 'UTC' AS completed_at
        FROM processing_tasks
        WHERE status = 'failed'
          AND COALESCE(completed_at, created_at) >= ($1::timestamptz AT TIME ZONE 'UTC')
          AND COALESCE(completed_at, created_at) <= ($2::timestamptz AT TIME ZONE 'UTC')
        ORDER BY COALESCE(completed_at, created_at) DESC, id DESC
        LIMIT $3
        ",
    )
    .bind(window.since)
    .bind(window.until)
    .bind(limit + 1)
    .bind(ERROR_MESSAGE_LIMIT)
    .fetch_all(pool)
    .await?;
    let truncated = failures.len() > usize::try_from(limit).unwrap_or(usize::MAX);
    if truncated {
        failures.truncate(usize::try_from(limit).unwrap_or(usize::MAX));
    }
    Ok(RecentTaskFailures {
        generated_at: window.until,
        window_hours,
        limit,
        count: failures.len(),
        truncated,
        failures,
    })
}

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct UsageTotals {
    pub call_count: i64,
    pub input_tokens: i64,
    pub cache_read_tokens: i64,
    pub cache_write_tokens: i64,
    pub output_tokens: i64,
    pub total_tokens: i64,
    pub request_count: i64,
    pub resource_count: i64,
    pub cost_usd: f64,
    pub providers: BTreeMap<String, i64>,
    pub models: BTreeMap<String, i64>,
}

impl UsageTotals {
    fn render_units(&self) -> String {
        let mut units = Vec::new();
        if self.total_tokens != 0 {
            units.push(format!("{} tokens", self.total_tokens));
        }
        if self.cache_read_tokens != 0 {
            units.push(format!("{} cache-read tokens", self.cache_read_tokens));
        }
        if self.cache_write_tokens != 0 {
            units.push(format!("{} cache-write tokens", self.cache_write_tokens));
        }
        if self.request_count != 0 {
            units.push(format!("{} requests", self.request_count));
        }
        if self.resource_count != 0 {
            units.push(format!("{} resources", self.resource_count));
        }
        if units.is_empty() {
            "0 usage units".to_owned()
        } else {
            units.join(", ")
        }
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, FromRow)]
pub struct UsageGroup {
    pub key: String,
    pub call_count: i64,
    pub input_tokens: i64,
    pub cache_read_tokens: i64,
    pub cache_write_tokens: i64,
    pub output_tokens: i64,
    pub total_tokens: i64,
    pub request_count: i64,
    pub resource_count: i64,
    pub cost_usd: f64,
}

impl UsageGroup {
    fn totals(&self) -> UsageTotals {
        UsageTotals {
            call_count: self.call_count,
            input_tokens: self.input_tokens,
            cache_read_tokens: self.cache_read_tokens,
            cache_write_tokens: self.cache_write_tokens,
            output_tokens: self.output_tokens,
            total_tokens: self.total_tokens,
            request_count: self.request_count,
            resource_count: self.resource_count,
            cost_usd: self.cost_usd,
            providers: BTreeMap::new(),
            models: BTreeMap::new(),
        }
    }
}

#[derive(Debug, Clone, PartialEq, FromRow)]
struct UsageTotalsRow {
    call_count: i64,
    input_tokens: i64,
    cache_read_tokens: i64,
    cache_write_tokens: i64,
    output_tokens: i64,
    total_tokens: i64,
    request_count: i64,
    resource_count: i64,
    cost_usd: f64,
}

#[derive(Debug, Clone, PartialEq, Eq, FromRow)]
struct UsageDimensionCount {
    dimension: String,
    key: String,
    count: i64,
}

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct UsageSummary {
    pub generated_at: DateTime<Utc>,
    pub since: DateTime<Utc>,
    pub until: DateTime<Utc>,
    pub group_by: String,
    pub totals: UsageTotals,
    pub groups: Vec<UsageGroup>,
}

impl UsageSummary {
    pub fn render_text(&self) -> String {
        let mut text = format!(
            "Usage summary grouped by {}:\nTotals: {} calls, {}, ${:.4}",
            self.group_by,
            self.totals.call_count,
            self.totals.render_units(),
            self.totals.cost_usd
        );
        if !self.groups.is_empty() {
            text.push_str("\nGroups:");
            for row in &self.groups {
                let totals = row.totals();
                let _ = write!(
                    text,
                    "\n- {}: {} calls, {}, ${:.4}",
                    row.key,
                    row.call_count,
                    totals.render_units(),
                    row.cost_usd
                );
            }
        }
        text
    }
}

/// Loads bounded usage and estimated-cost aggregates for one stable grouping.
///
/// # Errors
///
/// Returns [`OperatorQueryError::Sqlx`] when `PostgreSQL` rejects a query or a result cannot be
/// decoded.
pub async fn load_usage_summary(
    pool: &PgPool,
    window: QueryWindow,
    group_by: UsageGroupBy,
) -> Result<UsageSummary, OperatorQueryError> {
    let totals = sqlx::query_as::<_, UsageTotalsRow>(
        r"
        SELECT
            COUNT(*)::bigint AS call_count,
            COALESCE(SUM(input_tokens), 0)::bigint AS input_tokens,
            COALESCE(SUM(cache_read_tokens), 0)::bigint AS cache_read_tokens,
            COALESCE(SUM(cache_write_tokens), 0)::bigint AS cache_write_tokens,
            COALESCE(SUM(output_tokens), 0)::bigint AS output_tokens,
            COALESCE(SUM(total_tokens), 0)::bigint AS total_tokens,
            COALESCE(SUM(request_count), 0)::bigint AS request_count,
            COALESCE(SUM(resource_count), 0)::bigint AS resource_count,
            ROUND(COALESCE(SUM(cost_usd), 0.0)::numeric, 8)::double precision AS cost_usd
        FROM vendor_usage_records
        WHERE created_at >= ($1::timestamptz AT TIME ZONE 'UTC')
          AND created_at <= ($2::timestamptz AT TIME ZONE 'UTC')
        ",
    )
    .bind(window.since)
    .bind(window.until)
    .fetch_one(pool)
    .await?;
    let dimension_counts = load_usage_dimension_counts(pool, window).await?;
    let groups = sqlx::query_as::<_, UsageGroup>(
        r"
        WITH grouped AS (
            SELECT
                CASE $3::text
                    WHEN 'user' THEN COALESCE(user_id::text, 'unknown')
                    WHEN 'feature' THEN COALESCE(feature, 'unknown')
                    WHEN 'operation' THEN COALESCE(operation, 'unknown')
                    WHEN 'provider' THEN COALESCE(provider, 'unknown')
                    WHEN 'vendor' THEN COALESCE(provider, 'unknown')
                    WHEN 'model' THEN COALESCE(model, 'unknown')
                    WHEN 'source' THEN COALESCE(source, 'unknown')
                    ELSE 'unknown'
                END AS key,
                COUNT(*)::bigint AS call_count,
                COALESCE(SUM(input_tokens), 0)::bigint AS input_tokens,
                COALESCE(SUM(cache_read_tokens), 0)::bigint AS cache_read_tokens,
                COALESCE(SUM(cache_write_tokens), 0)::bigint AS cache_write_tokens,
                COALESCE(SUM(output_tokens), 0)::bigint AS output_tokens,
                COALESCE(SUM(total_tokens), 0)::bigint AS total_tokens,
                COALESCE(SUM(request_count), 0)::bigint AS request_count,
                COALESCE(SUM(resource_count), 0)::bigint AS resource_count,
                ROUND(COALESCE(SUM(cost_usd), 0.0)::numeric, 8)::double precision AS cost_usd
            FROM vendor_usage_records
            WHERE created_at >= ($1::timestamptz AT TIME ZONE 'UTC')
              AND created_at <= ($2::timestamptz AT TIME ZONE 'UTC')
            GROUP BY 1
        )
        SELECT key, call_count, input_tokens, cache_read_tokens, cache_write_tokens,
               output_tokens, total_tokens, request_count, resource_count, cost_usd
        FROM grouped
        ORDER BY (key = 'unknown'), LOWER(key)
        ",
    )
    .bind(window.since)
    .bind(window.until)
    .bind(group_by.as_str())
    .fetch_all(pool)
    .await?;

    let providers = dimension_counts
        .iter()
        .filter(|row| row.dimension == "provider")
        .map(|row| (row.key.clone(), row.count))
        .collect();
    let models = dimension_counts
        .into_iter()
        .filter(|row| row.dimension == "model")
        .map(|row| (row.key, row.count))
        .collect();
    let totals = UsageTotals {
        call_count: totals.call_count,
        input_tokens: totals.input_tokens,
        cache_read_tokens: totals.cache_read_tokens,
        cache_write_tokens: totals.cache_write_tokens,
        output_tokens: totals.output_tokens,
        total_tokens: totals.total_tokens,
        request_count: totals.request_count,
        resource_count: totals.resource_count,
        cost_usd: totals.cost_usd,
        providers,
        models,
    };

    Ok(UsageSummary {
        generated_at: Utc::now(),
        since: window.since,
        until: window.until,
        group_by: group_by.as_str().to_owned(),
        totals,
        groups,
    })
}

async fn load_usage_dimension_counts(
    pool: &PgPool,
    window: QueryWindow,
) -> Result<Vec<UsageDimensionCount>, OperatorQueryError> {
    Ok(sqlx::query_as::<_, UsageDimensionCount>(
        r"
        SELECT 'provider'::text AS dimension,
               COALESCE(provider, 'unknown') AS key,
               COUNT(*)::bigint AS count
        FROM vendor_usage_records
        WHERE created_at >= ($1::timestamptz AT TIME ZONE 'UTC')
          AND created_at <= ($2::timestamptz AT TIME ZONE 'UTC')
        GROUP BY COALESCE(provider, 'unknown')
        UNION ALL
        SELECT 'model'::text AS dimension,
               COALESCE(model, 'unknown') AS key,
               COUNT(*)::bigint AS count
        FROM vendor_usage_records
        WHERE created_at >= ($1::timestamptz AT TIME ZONE 'UTC')
          AND created_at <= ($2::timestamptz AT TIME ZONE 'UTC')
        GROUP BY COALESCE(model, 'unknown')
        ORDER BY dimension, key
        ",
    )
    .bind(window.since)
    .bind(window.until)
    .fetch_all(pool)
    .await?)
}

fn validate_window_hours(window_hours: i64) -> Result<(), OperatorQueryError> {
    if window_hours < 1 {
        return Err(OperatorQueryError::InvalidWindowHours);
    }
    if window_hours > MAX_WINDOW_HOURS {
        return Err(OperatorQueryError::WindowTooLarge {
            maximum_hours: MAX_WINDOW_HOURS,
        });
    }
    Ok(())
}

fn validate_limit(limit: i64, maximum: i64) -> Result<(), OperatorQueryError> {
    if !(1..=maximum).contains(&limit) {
        return Err(OperatorQueryError::InvalidLimit { maximum });
    }
    Ok(())
}

#[derive(Debug, Error)]
pub enum OperatorQueryError {
    #[error("window hours must be at least 1")]
    InvalidWindowHours,
    #[error("query window may not exceed {maximum_hours} hours")]
    WindowTooLarge { maximum_hours: i64 },
    #[error("query window start must not be later than its end")]
    InvalidTimeWindow,
    #[error("limit must be between 1 and {maximum}")]
    InvalidLimit { maximum: i64 },
    #[error("PostgreSQL operator query failed")]
    Sqlx(#[from] sqlx::Error),
}

#[cfg(test)]
mod tests {
    use chrono::TimeZone;

    use super::*;

    #[test]
    fn query_window_rejects_unbounded_ranges() {
        let until = Utc.with_ymd_and_hms(2026, 8, 31, 12, 0, 0).unwrap();
        let since = until - Duration::hours(MAX_WINDOW_HOURS + 1);

        assert!(matches!(
            QueryWindow::from_bounds(since, until),
            Err(OperatorQueryError::WindowTooLarge { .. })
        ));
    }

    #[test]
    fn health_text_keeps_legacy_operator_summary() {
        let generated_at = Utc.with_ymd_and_hms(2026, 8, 31, 12, 0, 0).unwrap();
        let snapshot = HealthSnapshot {
            generated_at,
            content: CountByStatus {
                total: 12,
                by_status: BTreeMap::new(),
            },
            tasks: CountByStatus {
                total: 20,
                by_status: BTreeMap::new(),
            },
            events: EventHealth { total: 0 },
            usage: UsageFreshness {
                latest_record_at: Some(generated_at),
            },
        };

        let rendered = snapshot.render_text();
        assert!(rendered.contains("Health snapshot:"));
        assert!(rendered.contains("- content: 12 total"));
        assert!(rendered.contains("- latest usage record: 2026-08-31T12:00:00+00:00"));
    }

    #[test]
    fn usage_text_renders_tokens_vendor_units_and_cost() {
        let generated_at = Utc.with_ymd_and_hms(2026, 8, 31, 12, 0, 0).unwrap();
        let summary = UsageSummary {
            generated_at,
            since: generated_at - Duration::hours(24),
            until: generated_at,
            group_by: "vendor".to_owned(),
            totals: UsageTotals {
                call_count: 2,
                input_tokens: 60,
                cache_read_tokens: 40,
                cache_write_tokens: 10,
                output_tokens: 40,
                total_tokens: 100,
                request_count: 2,
                resource_count: 9,
                cost_usd: 0.42,
                providers: BTreeMap::new(),
                models: BTreeMap::new(),
            },
            groups: vec![UsageGroup {
                key: "exa".to_owned(),
                call_count: 1,
                input_tokens: 0,
                cache_read_tokens: 0,
                cache_write_tokens: 0,
                output_tokens: 0,
                total_tokens: 0,
                request_count: 1,
                resource_count: 8,
                cost_usd: 0.28,
            }],
        };

        let rendered = summary.render_text();
        assert!(rendered.contains(
            "Totals: 2 calls, 100 tokens, 40 cache-read tokens, 10 cache-write tokens, \
             2 requests, 9 resources, $0.4200"
        ));
        assert!(rendered.contains("- exa: 1 calls, 1 requests, 8 resources, $0.2800"));
    }

    #[test]
    fn recent_failure_text_does_not_expose_payloads() {
        let generated_at = Utc.with_ymd_and_hms(2026, 8, 31, 12, 0, 0).unwrap();
        let failures = RecentTaskFailures {
            generated_at,
            window_hours: 24,
            limit: 20,
            count: 1,
            truncated: false,
            failures: vec![RecentTaskFailure {
                id: 7,
                queue_name: "llm".to_owned(),
                task_type: "run_llm_task".to_owned(),
                content_id: None,
                retry_count: 2,
                executor_runtime: "rust".to_owned(),
                error_message: Some("provider timeout".to_owned()),
                created_at: generated_at - Duration::minutes(5),
                completed_at: Some(generated_at),
            }],
        };

        let rendered = failures.render_text();
        assert!(rendered.contains("task 7 llm/run_llm_task retry 2: provider timeout"));
        assert!(!rendered.contains("payload"));
    }
}
