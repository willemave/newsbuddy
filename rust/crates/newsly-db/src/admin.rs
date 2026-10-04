use chrono::{DateTime, NaiveDate, NaiveDateTime, Utc};
use serde_json::Value;
use sqlx::{AssertSqlSafe, FromRow, PgPool};
use thiserror::Error;

pub const VENDOR_COST_AGGREGATES_SQL: &str = r"
    CASE WHEN COUNT(*) FILTER (WHERE cost_usd IS NULL) = 0
        THEN ROUND(COALESCE(SUM(cost_usd), 0.0)::numeric, 8)::double precision
        ELSE NULL END AS cost_usd,
    ROUND(COALESCE(SUM(cost_usd), 0.0)::numeric, 8)::double precision AS known_cost_usd,
    ROUND(COALESCE(SUM(cost_usd) FILTER (WHERE cost_basis = 'public_list_estimate'), 0.0)::numeric, 8)::double precision AS public_list_estimate_usd,
    ROUND(COALESCE(SUM(cost_usd) FILTER (WHERE cost_basis = 'provider_estimate'), 0.0)::numeric, 8)::double precision AS provider_estimate_usd,
    COUNT(*) FILTER (WHERE cost_basis = 'non_billable')::bigint AS non_billable_record_count,
    COUNT(*) FILTER (WHERE cost_usd IS NULL)::bigint AS unpriced_call_count
";

pub fn render_vendor_usage_cost(
    cost: Option<f64>,
    known_cost: f64,
    public_list_estimate: f64,
    provider_estimate: f64,
    non_billable_records: i64,
    unpriced_records: i64,
) -> String {
    cost.map_or_else(
        || format!("Unknown (${known_cost:.6} priced subtotal: ${public_list_estimate:.6} public-list estimates, ${provider_estimate:.6} provider estimates; {non_billable_records} non-billable, {unpriced_records} unpriced records)"),
        |cost| format!("${cost:.6} (${public_list_estimate:.6} public-list estimates, ${provider_estimate:.6} provider estimates; {non_billable_records} non-billable records)"),
    )
}

#[cfg(test)]
mod cost_render_tests {
    use super::render_vendor_usage_cost;

    #[test]
    fn cost_rendering_uses_one_precision_and_preserves_unknown_totals() {
        assert_eq!(
            render_vendor_usage_cost(None, 0.3, 0.25, 0.05, 1, 2),
            "Unknown ($0.300000 priced subtotal: $0.250000 public-list estimates, $0.050000 provider estimates; 1 non-billable, 2 unpriced records)"
        );
        assert_eq!(
            render_vendor_usage_cost(Some(0.3), 0.3, 0.25, 0.05, 1, 0),
            "$0.300000 ($0.250000 public-list estimates, $0.050000 provider estimates; 1 non-billable records)"
        );
    }
}

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
    pub active_users: i64,
    pub onboarding_completed: i64,
}

#[derive(Debug, Clone, PartialEq, FromRow)]
pub struct AdminProviderCostRow {
    pub provider: String,
    pub row_count: i64,
    pub request_count: i64,
    pub cost_usd: Option<f64>,
    pub known_cost_usd: f64,
    pub public_list_estimate_usd: f64,
    pub provider_estimate_usd: f64,
    pub non_billable_record_count: i64,
    pub unpriced_call_count: i64,
}

#[derive(Debug, Clone, PartialEq, Eq, FromRow)]
pub struct AdminRecentFailureRow {
    pub id: i64,
    pub queue_name: String,
    pub task_type: String,
    pub error_message: Option<String>,
    pub created_at: DateTime<Utc>,
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
    pub platform: Option<String>,
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
    pub created_at: DateTime<Utc>,
    pub provider: String,
    pub model: String,
    pub feature: String,
    pub user_id: Option<i64>,
    pub user_email: Option<String>,
    pub user_name: Option<String>,
    pub total_tokens: Option<i32>,
    pub cost_usd: Option<f64>,
    pub cost_basis: Option<String>,
}

#[derive(Debug, Clone, PartialEq, FromRow)]
pub struct AdminVendorUsageTotals {
    pub row_count: i64,
    pub total_tokens: i64,
    pub request_count: i64,
    pub cost_usd: Option<f64>,
    pub known_cost_usd: f64,
    pub public_list_estimate_usd: f64,
    pub provider_estimate_usd: f64,
    pub non_billable_record_count: i64,
    pub unpriced_call_count: i64,
}

#[derive(Debug, Clone, PartialEq, FromRow)]
pub struct AdminVendorUsageDailyRow {
    pub usage_day: NaiveDate,
    pub row_count: i64,
    pub cost_usd: Option<f64>,
    pub known_cost_usd: f64,
    pub public_list_estimate_usd: f64,
    pub provider_estimate_usd: f64,
    pub non_billable_record_count: i64,
    pub unpriced_call_count: i64,
    pub request_count: i64,
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
            COUNT(*) FILTER (WHERE is_active IS TRUE)::bigint AS active_users,
            COUNT(*) FILTER (WHERE has_completed_onboarding IS TRUE)::bigint AS onboarding_completed
        FROM users
        "#,
    )
    .fetch_one(pool)
    .await?;
    let provider_costs = sqlx::query_as::<_, AdminProviderCostRow>(AssertSqlSafe(format!(
        r#"
        SELECT
            provider,
            COUNT(*)::bigint AS row_count,
            COALESCE(SUM(request_count), 0)::bigint AS request_count,
            {VENDOR_COST_AGGREGATES_SQL}
        FROM vendor_usage_records
        WHERE created_at >= timezone('UTC', clock_timestamp()) - interval '30 days'
        GROUP BY provider
        ORDER BY COALESCE(SUM(cost_usd), 0.0) DESC, COUNT(*) DESC
        LIMIT 12
        "#
    )))
    .fetch_all(pool)
    .await?;
    let recent_failures = sqlx::query_as::<_, AdminRecentFailureRow>(
        r#"
        SELECT
            id::bigint,
            queue_name,
            task_type,
            error_message,
            created_at
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
            feedback.platform,
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
            usage.created_at AT TIME ZONE 'UTC' AS created_at,
            usage.provider,
            usage.model,
            usage.feature,
            usage.user_id::bigint,
            users.email AS user_email,
            users.full_name AS user_name,
            usage.total_tokens,
            usage.cost_usd,
            usage.cost_basis
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
    let totals = sqlx::query_as::<_, AdminVendorUsageTotals>(AssertSqlSafe(format!(
        r#"
        SELECT
            COUNT(*)::bigint AS row_count,
            COALESCE(SUM(total_tokens), 0)::bigint AS total_tokens,
            COALESCE(SUM(request_count), 0)::bigint AS request_count,
            {VENDOR_COST_AGGREGATES_SQL}
        FROM vendor_usage_records
        WHERE ($1::text IS NULL OR provider = $1)
          AND ($2::text IS NULL OR model = $2)
          AND ($3::text IS NULL OR feature = $3)
          AND ($4::bigint IS NULL OR user_id = $4)
          AND ($5::timestamptz IS NULL OR created_at >= $5)
          AND ($6::timestamptz IS NULL OR created_at <= $6)
        "#
    )))
    .bind(filter.provider.as_deref())
    .bind(filter.model.as_deref())
    .bind(filter.feature.as_deref())
    .bind(filter.user_id)
    .bind(filter.start_at)
    .bind(filter.end_at)
    .fetch_one(pool)
    .await?;
    let daily = sqlx::query_as::<_, AdminVendorUsageDailyRow>(AssertSqlSafe(format!(
        r#"
        SELECT
            created_at::date AS usage_day,
            COUNT(*)::bigint AS row_count,
            {VENDOR_COST_AGGREGATES_SQL},
            COALESCE(SUM(request_count), 0)::bigint AS request_count,
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
        "#
    )))
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

#[cfg(test)]
mod usage_tests {
    use super::*;

    #[sqlx::test]
    async fn token_prices_apply_only_to_observed_standard_single_requests(pool: PgPool) {
        sqlx::query(
            "INSERT INTO vendor_usage_records
                (provider, model, feature, operation, request_count, input_tokens,
                 cache_read_tokens, cache_write_tokens, output_tokens, metadata, created_at)
             VALUES
                ('openai', 'openai:gpt-6-luna', 'news_processing',
                 'news_processing.summarize_short_form', 1, 1000, 200, 100, 100,
                 '{}'::json, timezone('UTC', now())),
                ('openrouter', 'Qwen/Qwen3-Embedding-8B', 'news_processing',
                 'news_processing.embed_relations', 1, 1000000, 0, 0, 0,
                 '{}'::json, timezone('UTC', now())),
                ('openrouter', 'openrouter:qwen/qwen3-embedding-8b', 'news_processing',
                 'news_processing.embed_relations', 1, 1000, 0, 0, 0,
                 '{}'::json, timezone('UTC', now())),
                ('openai', 'gpt-6-luna', 'onboarding', 'onboarding.generate',
                 1, 1000, 0, 0, 100, '{\"service_tier\":\"priority\"}'::json,
                 timezone('UTC', now())),
                ('openai', 'gpt-6-luna', 'news_processing',
                 'news_processing.summarize_short_form', 2, 1000, 0, 0, 100,
                 '{}'::json, timezone('UTC', now())),
                ('openai', 'gpt-6-luna', 'news_processing',
                 'news_processing.summarize_short_form', 1, 273000, 0, 0, 100,
                 '{}'::json, timezone('UTC', now()))",
        )
        .execute(&pool)
        .await
        .unwrap();
        let rows: Vec<(Option<f64>, Option<String>, Option<String>)> = sqlx::query_as(
            "SELECT cost_usd, cost_basis, pricing_version
             FROM vendor_usage_records ORDER BY id",
        )
        .fetch_all(&pool)
        .await
        .unwrap();
        assert_eq!(rows.len(), 6);
        assert!((rows[0].0.unwrap() - 0.000_134_5).abs() < 1e-10);
        assert_eq!(rows[0].1.as_deref(), Some("public_list_estimate"));
        assert_eq!(rows[0].2.as_deref(), Some("openai-standard-2026-09-29"));
        assert!((rows[1].0.unwrap() - 0.01).abs() < 1e-10);
        assert!((rows[2].0.unwrap() - 0.000_01).abs() < 1e-10);
        assert!(
            rows[3..5]
                .iter()
                .all(|row| row.0.is_none() && row.2.is_none())
        );
        assert!((rows[5].0.unwrap() - 0.054_675).abs() < 1e-10);
        assert_eq!(rows[5].1.as_deref(), Some("public_list_estimate"));
        assert_eq!(rows[5].2.as_deref(), Some("openai-standard-2026-09-29"));

        sqlx::query(
            "INSERT INTO vendor_usage_records
                (provider, model, feature, operation, request_count, cost_usd,
                 pricing_version, cost_basis, created_at)
             VALUES ('runware', 'image', 'image_generation', 'generate_image', 1,
                     0.42, 'provider-reported', 'provider_reported', timezone('UTC', now()))",
        )
        .execute(&pool)
        .await
        .unwrap();
        let preserved: (Option<f64>, Option<String>, Option<String>) = sqlx::query_as(
            "SELECT cost_usd, pricing_version, cost_basis FROM vendor_usage_records
             WHERE provider = 'runware'",
        )
        .fetch_one(&pool)
        .await
        .unwrap();
        assert_eq!(
            preserved,
            (
                Some(0.42),
                Some("provider-reported".to_owned()),
                Some("provider_reported".to_owned())
            )
        );

        let exa_basis: Option<String> = sqlx::query_scalar(
            "INSERT INTO vendor_usage_records
                (provider, model, feature, operation, request_count, cost_basis, created_at)
             VALUES ('exa', 'search', 'agent_search', 'agent_search.query', 1,
                     'unpriced_incomplete_provider_units', timezone('UTC', now()))
             RETURNING cost_basis",
        )
        .fetch_one(&pool)
        .await
        .unwrap();
        assert_eq!(
            exa_basis.as_deref(),
            Some("unpriced_incomplete_provider_units")
        );
    }

    #[sqlx::test]
    async fn admin_cost_aggregates_preserve_unknown_prices(pool: PgPool) {
        sqlx::query(
            "INSERT INTO vendor_usage_records
                (provider, model, feature, operation, cost_usd, cost_basis, created_at)
             VALUES ('test', 'priced', 'test', 'test', 0.25, 'public_list_estimate', timezone('UTC', now())),
                    ('test', 'estimated', 'test', 'test', 0.05, 'provider_estimate', timezone('UTC', now())),
                    ('test', 'unpriced', 'test', 'test', NULL, NULL, timezone('UTC', now())),
                    ('free', 'free', 'test', 'test', 0.0, 'non_billable', timezone('UTC', now()))",
        )
        .execute(&pool)
        .await
        .unwrap();
        let snapshot = load_admin_vendor_usage(&pool, &AdminVendorUsageFilter::default())
            .await
            .unwrap();
        assert_eq!(snapshot.totals.cost_usd, None);
        assert!((snapshot.totals.known_cost_usd - 0.30).abs() < f64::EPSILON);
        assert!((snapshot.totals.public_list_estimate_usd - 0.25).abs() < f64::EPSILON);
        assert!((snapshot.totals.provider_estimate_usd - 0.05).abs() < f64::EPSILON);
        assert_eq!(snapshot.totals.non_billable_record_count, 1);
        assert_eq!(snapshot.totals.unpriced_call_count, 1);
        assert_eq!(snapshot.daily[0].cost_usd, None);
        assert_eq!(snapshot.daily[0].unpriced_call_count, 1);
        let dashboard = load_admin_dashboard(&pool, None).await.unwrap();
        let unpriced = dashboard
            .provider_costs
            .iter()
            .find(|row| row.provider == "test")
            .unwrap();
        assert_eq!(unpriced.cost_usd, None);
        assert!((unpriced.provider_estimate_usd - 0.05).abs() < f64::EPSILON);
        assert_eq!(unpriced.unpriced_call_count, 1);
        let free = load_admin_vendor_usage(
            &pool,
            &AdminVendorUsageFilter {
                provider: Some("free".to_owned()),
                ..AdminVendorUsageFilter::default()
            },
        )
        .await
        .unwrap();
        assert_eq!(free.totals.cost_usd, Some(0.0));
        assert_eq!(free.totals.non_billable_record_count, 1);
        assert_eq!(free.totals.unpriced_call_count, 0);
    }
}
