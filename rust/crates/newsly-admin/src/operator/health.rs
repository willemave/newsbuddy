use super::*;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, FromRow)]
pub struct SourceHealth {
    pub source_key: String,
    pub last_attempt_at: DateTime<Utc>,
    pub last_success_at: Option<DateTime<Utc>>,
    pub consecutive_failures: i32,
    pub persisted_count: i64,
    pub new_count: i64,
    pub error_code: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct HealthSnapshot {
    pub generated_at: DateTime<Utc>,
    pub content: CountByStatus,
    pub tasks: CountByStatus,
    pub events: EventHealth,
    pub usage: UsageFreshness,
    pub sources: Vec<SourceHealth>,
    pub pipeline: newsly_db::PipelineHealthCounts,
}

impl HealthSnapshot {
    pub fn render_text(&self) -> String {
        use std::fmt::Write;
        let mut output = format!(
            "Health snapshot:\n- content: {} total\n- tasks: {} total\n- events: {} total\n- latest usage record: {}",
            self.content.total,
            self.tasks.total,
            self.events.total,
            self.usage
                .latest_record_at
                .map_or_else(|| "none".to_owned(), |value| value.to_rfc3339())
        );
        let _ = write!(
            output,
            "\n- pipeline: {} failing sources, {} overdue tasks, {} terminal product mismatches",
            self.pipeline.failing_sources,
            self.pipeline.overdue_tasks,
            self.pipeline.terminal_product_mismatches
        );
        for source in &self.sources {
            let _ = write!(
                output,
                "\n- source {}: {}, {} new, {} consecutive failures",
                source.source_key,
                source.error_code.as_deref().unwrap_or("fetch succeeded"),
                source.new_count,
                source.consecutive_failures
            );
        }
        output
    }
}

#[derive(Debug, Clone, PartialEq, Eq, FromRow)]
pub(super) struct StatusCountRow {
    pub(super) label: String,
    pub(super) count: i64,
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

    let sources = sqlx::query_as::<_, SourceHealth>("SELECT source_key, last_attempt_at, last_success_at, consecutive_failures, persisted_count, new_count, error_code FROM source_ingestion_health ORDER BY consecutive_failures DESC, source_key LIMIT 500").fetch_all(pool).await?;
    let pipeline = newsly_db::pipeline_health_counts(&mut *pool.acquire().await?).await?;
    Ok(HealthSnapshot {
        pipeline,
        sources,
        generated_at: Utc::now(),
        content: count_by_status(content_rows),
        tasks: count_by_status(task_rows),
        events: EventHealth { total: event_count },
        usage: UsageFreshness {
            latest_record_at: usage.latest_record_at,
        },
    })
}
