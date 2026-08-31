use chrono::{DateTime, NaiveDate, NaiveDateTime, Utc};
use serde_json::Value;
use sqlx::{FromRow, PgPool};
use thiserror::Error;

#[derive(Debug, Clone, PartialEq, Eq, FromRow)]
pub struct AdminCountRow {
    pub label: String,
    pub count: i64,
}

#[derive(Debug, Clone, PartialEq, Eq, FromRow)]
pub struct AdminQueueCountRow {
    pub queue_name: String,
    pub status: String,
    pub count: i64,
}

#[derive(Debug, Clone, PartialEq, Eq, FromRow)]
pub struct AdminUserStats {
    pub total_users: i64,
    pub active_users: i64,
    pub recent_users: i64,
    pub onboarding_completed: i64,
}

#[derive(Debug, Clone, PartialEq, FromRow)]
pub struct AdminProviderCostRow {
    pub provider: String,
    pub row_count: i64,
    pub request_count: i64,
    pub resource_count: i64,
    pub cost_usd: f64,
}

#[derive(Debug, Clone, PartialEq, Eq, FromRow)]
pub struct AdminRecentFailureRow {
    pub id: i64,
    pub queue_name: String,
    pub task_type: String,
    pub error_message: Option<String>,
    pub retry_count: i32,
    pub created_at: DateTime<Utc>,
    pub completed_at: Option<DateTime<Utc>>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct AdminDashboardSnapshot {
    pub content_counts: Vec<AdminCountRow>,
    pub task_counts: Vec<AdminCountRow>,
    pub queue_counts: Vec<AdminQueueCountRow>,
    pub user_stats: AdminUserStats,
    pub provider_costs: Vec<AdminProviderCostRow>,
    pub recent_failures: Vec<AdminRecentFailureRow>,
}

#[derive(Debug, Clone, PartialEq, Eq, FromRow)]
pub struct AdminFeedbackRow {
    pub user_id: i64,
    pub email: Option<String>,
    pub full_name: Option<String>,
    pub message: String,
    pub source: String,
    pub app_version: Option<String>,
    pub build_number: Option<String>,
    pub platform: Option<String>,
    pub os_version: Option<String>,
    pub device_model: Option<String>,
    pub created_at: DateTime<Utc>,
}

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct AdminVendorUsageFilter {
    pub provider: Option<String>,
    pub model: Option<String>,
    pub feature: Option<String>,
    pub user_id: Option<i64>,
    pub start_at: Option<DateTime<Utc>>,
    pub end_at: Option<DateTime<Utc>>,
    pub limit: i64,
}

#[derive(Debug, Clone, PartialEq, FromRow)]
pub struct AdminVendorUsageRow {
    pub id: i64,
    pub created_at: DateTime<Utc>,
    pub provider: String,
    pub model: String,
    pub feature: String,
    pub operation: String,
    pub source: Option<String>,
    pub request_id: Option<String>,
    pub task_id: Option<i64>,
    pub content_id: Option<i64>,
    pub session_id: Option<i64>,
    pub message_id: Option<i64>,
    pub user_id: Option<i64>,
    pub user_email: Option<String>,
    pub user_name: Option<String>,
    pub input_tokens: Option<i32>,
    pub output_tokens: Option<i32>,
    pub total_tokens: Option<i32>,
    pub request_count: Option<i32>,
    pub resource_count: Option<i32>,
    pub cost_usd: Option<f64>,
    pub pricing_version: Option<String>,
}

#[derive(Debug, Clone, PartialEq, FromRow)]
pub struct AdminVendorUsageTotals {
    pub row_count: i64,
    pub attributed_row_count: i64,
    pub input_tokens: i64,
    pub output_tokens: i64,
    pub total_tokens: i64,
    pub request_count: i64,
    pub resource_count: i64,
    pub cost_usd: f64,
}

#[derive(Debug, Clone, PartialEq, FromRow)]
pub struct AdminVendorUsageDailyRow {
    pub usage_day: NaiveDate,
    pub row_count: i64,
    pub cost_usd: f64,
    pub request_count: i64,
    pub resource_count: i64,
    pub total_tokens: i64,
}

#[derive(Debug, Clone, PartialEq)]
pub struct AdminVendorUsageSnapshot {
    pub rows: Vec<AdminVendorUsageRow>,
    pub totals: AdminVendorUsageTotals,
    pub daily: Vec<AdminVendorUsageDailyRow>,
}

/// Recent completed content available to the authenticated admin model-comparison surface.
///
/// The row owns only database state. Object-store reads and model calls happen after this query
/// returns, so the admin workflow cannot retain a PostgreSQL connection across external work.
#[derive(Debug, Clone, PartialEq, FromRow)]
pub struct AdminEvalCandidate {
    pub content_id: i64,
    pub content_type: String,
    pub created_at: NaiveDateTime,
    pub url: String,
    pub source_title: Option<String>,
    pub source_name: Option<String>,
    pub platform: Option<String>,
    pub publication_date: Option<NaiveDateTime>,
    pub content_metadata: Value,
    pub storage_provider: Option<String>,
    pub storage_bucket: Option<String>,
    pub storage_key: Option<String>,
}

/// Loads the newest bounded candidate pool per requested content type.
///
/// Sampling is performed in application memory after the query so a supplied seed is stable and
/// no database connection is held while object storage or model providers are contacted.
///
/// # Errors
///
/// Returns [`AdminRepositoryError::Sqlx`] when PostgreSQL rejects the query.
pub async fn list_admin_eval_candidates(
    pool: &PgPool,
    content_types: &[String],
    recent_pool_size: i64,
) -> Result<Vec<AdminEvalCandidate>, AdminRepositoryError> {
    Ok(sqlx::query_as::<_, AdminEvalCandidate>(
        r#"
        WITH ranked AS (
            SELECT
                content.id::bigint AS content_id,
                content.content_type,
                content.created_at,
                content.url,
                content.title AS source_title,
                content.source AS source_name,
                content.platform,
                content.publication_date,
                content.content_metadata,
                body.storage_provider,
                body.storage_bucket,
                body.storage_key,
                row_number() OVER (
                    PARTITION BY content.content_type
                    ORDER BY content.created_at DESC, content.id DESC
                ) AS recency_rank
            FROM contents AS content
            LEFT JOIN content_bodies AS body
              ON body.content_id = content.id
             AND body.variant = 'source'
            WHERE content.status = 'completed'
              AND content.content_type = ANY($1::text[])
        )
        SELECT
            content_id,
            content_type,
            created_at,
            url,
            source_title,
            source_name,
            platform,
            publication_date,
            content_metadata,
            storage_provider,
            storage_bucket,
            storage_key
        FROM ranked
        WHERE recency_rank <= $2::bigint
        ORDER BY content_type, created_at DESC, content_id DESC
        "#,
    )
    .bind(content_types)
    .bind(recent_pool_size)
    .fetch_all(pool)
    .await?)
}

/// Loads the bounded operational data rendered by the native Rust admin dashboard.
///
/// # Errors
///
/// Returns [`AdminRepositoryError::Sqlx`] when PostgreSQL rejects a query.
pub async fn load_admin_dashboard(
    pool: &PgPool,
    cutoff: Option<DateTime<Utc>>,
) -> Result<AdminDashboardSnapshot, AdminRepositoryError> {
    let content_counts = sqlx::query_as::<_, AdminCountRow>(
        r#"
        SELECT content_type AS label, COUNT(*)::bigint AS count
        FROM contents
        WHERE ($1::timestamptz IS NULL OR created_at >= $1)
        GROUP BY content_type
        ORDER BY content_type
        "#,
    )
    .bind(cutoff)
    .fetch_all(pool)
    .await?;
    let task_counts = sqlx::query_as::<_, AdminCountRow>(
        r#"
        SELECT status AS label, COUNT(*)::bigint AS count
        FROM processing_tasks
        WHERE ($1::timestamptz IS NULL OR created_at >= $1)
        GROUP BY status
        ORDER BY status
        "#,
    )
    .bind(cutoff)
    .fetch_all(pool)
    .await?;
    let queue_counts = sqlx::query_as::<_, AdminQueueCountRow>(
        r#"
        SELECT queue_name, status, COUNT(*)::bigint AS count
        FROM processing_tasks
        GROUP BY queue_name, status
        ORDER BY queue_name, status
        "#,
    )
    .fetch_all(pool)
    .await?;
    let user_stats = sqlx::query_as::<_, AdminUserStats>(
        r#"
        SELECT
            COUNT(*)::bigint AS total_users,
            COUNT(*) FILTER (WHERE is_active IS TRUE)::bigint AS active_users,
            COUNT(*) FILTER (WHERE created_at >= timezone('UTC', clock_timestamp()) - interval '24 hours')::bigint AS recent_users,
            COUNT(*) FILTER (WHERE has_completed_onboarding IS TRUE)::bigint AS onboarding_completed
        FROM users
        "#,
    )
    .fetch_one(pool)
    .await?;
    let provider_costs = sqlx::query_as::<_, AdminProviderCostRow>(
        r#"
        SELECT
            provider,
            COUNT(*)::bigint AS row_count,
            COALESCE(SUM(request_count), 0)::bigint AS request_count,
            COALESCE(SUM(resource_count), 0)::bigint AS resource_count,
            COALESCE(SUM(cost_usd), 0.0)::double precision AS cost_usd
        FROM vendor_usage_records
        WHERE created_at >= timezone('UTC', clock_timestamp()) - interval '30 days'
        GROUP BY provider
        ORDER BY COALESCE(SUM(cost_usd), 0.0) DESC, COUNT(*) DESC
        LIMIT 12
        "#,
    )
    .fetch_all(pool)
    .await?;
    let recent_failures = sqlx::query_as::<_, AdminRecentFailureRow>(
        r#"
        SELECT
            id::bigint,
            queue_name,
            task_type,
            error_message,
            retry_count,
            created_at,
            completed_at
        FROM processing_tasks
        WHERE status = 'failed'
          AND created_at >= timezone('UTC', clock_timestamp()) - interval '24 hours'
        ORDER BY COALESCE(completed_at, created_at) DESC
        LIMIT 30
        "#,
    )
    .fetch_all(pool)
    .await?;
    Ok(AdminDashboardSnapshot {
        content_counts,
        task_counts,
        queue_counts,
        user_stats,
        provider_costs,
        recent_failures,
    })
}

/// Lists the newest product feedback with its optional account label.
///
/// # Errors
///
/// Returns [`AdminRepositoryError::Sqlx`] when PostgreSQL rejects the query.
pub async fn list_admin_feedback(
    pool: &PgPool,
) -> Result<Vec<AdminFeedbackRow>, AdminRepositoryError> {
    Ok(sqlx::query_as::<_, AdminFeedbackRow>(
        r#"
        SELECT
            feedback.user_id::bigint,
            users.email,
            users.full_name,
            feedback.message,
            feedback.source,
            feedback.app_version,
            feedback.build_number,
            feedback.platform,
            feedback.os_version,
            feedback.device_model,
            feedback.created_at
        FROM user_feedback AS feedback
        LEFT JOIN users ON users.id = feedback.user_id
        ORDER BY feedback.created_at DESC
        LIMIT 200
        "#,
    )
    .fetch_all(pool)
    .await?)
}

/// Loads filtered vendor usage records and bounded rollups.
///
/// # Errors
///
/// Returns [`AdminRepositoryError::Sqlx`] when PostgreSQL rejects a query.
pub async fn load_admin_vendor_usage(
    pool: &PgPool,
    filter: &AdminVendorUsageFilter,
) -> Result<AdminVendorUsageSnapshot, AdminRepositoryError> {
    let limit = filter.limit.clamp(1, 500);
    let rows = sqlx::query_as::<_, AdminVendorUsageRow>(
        r#"
        SELECT
            usage.id::bigint,
            usage.created_at,
            usage.provider,
            usage.model,
            usage.feature,
            usage.operation,
            usage.source,
            usage.request_id,
            usage.task_id::bigint,
            usage.content_id::bigint,
            usage.session_id::bigint,
            usage.message_id::bigint,
            usage.user_id::bigint,
            users.email AS user_email,
            users.full_name AS user_name,
            usage.input_tokens,
            usage.output_tokens,
            usage.total_tokens,
            usage.request_count,
            usage.resource_count,
            usage.cost_usd,
            usage.pricing_version
        FROM vendor_usage_records AS usage
        LEFT JOIN users ON users.id = usage.user_id
        WHERE ($1::text IS NULL OR usage.provider = $1)
          AND ($2::text IS NULL OR usage.model = $2)
          AND ($3::text IS NULL OR usage.feature = $3)
          AND ($4::bigint IS NULL OR usage.user_id = $4)
          AND ($5::timestamptz IS NULL OR usage.created_at >= $5)
          AND ($6::timestamptz IS NULL OR usage.created_at <= $6)
        ORDER BY usage.created_at DESC
        LIMIT $7
        "#,
    )
    .bind(filter.provider.as_deref())
    .bind(filter.model.as_deref())
    .bind(filter.feature.as_deref())
    .bind(filter.user_id)
    .bind(filter.start_at)
    .bind(filter.end_at)
    .bind(limit)
    .fetch_all(pool)
    .await?;
    let totals = sqlx::query_as::<_, AdminVendorUsageTotals>(
        r#"
        SELECT
            COUNT(*)::bigint AS row_count,
            COUNT(user_id)::bigint AS attributed_row_count,
            COALESCE(SUM(input_tokens), 0)::bigint AS input_tokens,
            COALESCE(SUM(output_tokens), 0)::bigint AS output_tokens,
            COALESCE(SUM(total_tokens), 0)::bigint AS total_tokens,
            COALESCE(SUM(request_count), 0)::bigint AS request_count,
            COALESCE(SUM(resource_count), 0)::bigint AS resource_count,
            COALESCE(SUM(cost_usd), 0.0)::double precision AS cost_usd
        FROM vendor_usage_records
        WHERE ($1::text IS NULL OR provider = $1)
          AND ($2::text IS NULL OR model = $2)
          AND ($3::text IS NULL OR feature = $3)
          AND ($4::bigint IS NULL OR user_id = $4)
          AND ($5::timestamptz IS NULL OR created_at >= $5)
          AND ($6::timestamptz IS NULL OR created_at <= $6)
        "#,
    )
    .bind(filter.provider.as_deref())
    .bind(filter.model.as_deref())
    .bind(filter.feature.as_deref())
    .bind(filter.user_id)
    .bind(filter.start_at)
    .bind(filter.end_at)
    .fetch_one(pool)
    .await?;
    let daily = sqlx::query_as::<_, AdminVendorUsageDailyRow>(
        r#"
        SELECT
            created_at::date AS usage_day,
            COUNT(*)::bigint AS row_count,
            COALESCE(SUM(cost_usd), 0.0)::double precision AS cost_usd,
            COALESCE(SUM(request_count), 0)::bigint AS request_count,
            COALESCE(SUM(resource_count), 0)::bigint AS resource_count,
            COALESCE(SUM(total_tokens), 0)::bigint AS total_tokens
        FROM vendor_usage_records
        WHERE ($1::text IS NULL OR provider = $1)
          AND ($2::text IS NULL OR model = $2)
          AND ($3::text IS NULL OR feature = $3)
          AND ($4::bigint IS NULL OR user_id = $4)
          AND ($5::timestamptz IS NULL OR created_at >= $5)
          AND ($6::timestamptz IS NULL OR created_at <= $6)
        GROUP BY created_at::date
        ORDER BY created_at::date DESC
        LIMIT 60
        "#,
    )
    .bind(filter.provider.as_deref())
    .bind(filter.model.as_deref())
    .bind(filter.feature.as_deref())
    .bind(filter.user_id)
    .bind(filter.start_at)
    .bind(filter.end_at)
    .fetch_all(pool)
    .await?;
    Ok(AdminVendorUsageSnapshot {
        rows,
        totals,
        daily,
    })
}

#[derive(Debug, Error)]
pub enum AdminRepositoryError {
    #[error("PostgreSQL admin query failed")]
    Sqlx(#[from] sqlx::Error),
}
