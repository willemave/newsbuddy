mod category_routing;
mod config;
mod filtering;
mod finalizer;
mod handler;
mod normalize;
mod planning;
mod semantic_lenses;
mod stale_assignment;

pub use config::{BriefingRefreshWorkerConfig, BriefingRefreshWorkerConfigError};
pub use handler::{BriefingRefreshHandler, BriefingRefreshWorkerServices};

mod warm_news;
pub use warm_news::PrepareNewsLensHandler;

mod reclustering;
pub use reclustering::ReclusterNewsLensesHandler;

const EMBEDDING_PROVIDER: &str = "openrouter";

pub(crate) async fn enqueue_and_pull_forward_briefing_refreshes(
    transaction: &mut sqlx::Transaction<'static, sqlx::Postgres>,
    queue: &newsly_queue::QueueKernel,
    requests: Vec<newsly_queue::EnqueueRequest>,
) -> Result<(), newsly_queue::QueueError> {
    if requests.is_empty() {
        return Ok(());
    }
    let mut dedupe_keys = Vec::with_capacity(requests.len());
    let mut deadlines = Vec::with_capacity(requests.len());
    for request in &requests {
        if let (Some(dedupe_key), Some(available_at)) = (&request.dedupe_key, request.available_at)
        {
            dedupe_keys.push(dedupe_key.clone());
            deadlines.push(available_at.naive_utc());
        }
    }
    queue
        .enqueue_many_in_transaction(transaction, requests)
        .await?;
    if !dedupe_keys.is_empty() {
        let pulled_ready: bool = sqlx::query_scalar(
            r"
            WITH locked AS MATERIALIZED (
                SELECT task.id, wake.available_at
                FROM processing_tasks AS task
                JOIN unnest($1::text[], $2::timestamp[]) AS wake(dedupe_key, available_at)
                  ON task.dedupe_key = wake.dedupe_key
                WHERE task.status = 'pending' AND task.retry_count = 0
                ORDER BY task.id
                FOR UPDATE OF task
            ),
            updated AS (
                UPDATE processing_tasks AS task
                SET available_at = LEAST(task.available_at, locked.available_at)
                FROM locked
                WHERE task.id = locked.id
                  AND locked.available_at < task.available_at
                RETURNING task.available_at
            )
            SELECT EXISTS(
                SELECT 1
                FROM updated
                WHERE available_at <= timezone('UTC', now())
            )
            ",
        )
        .bind(&dedupe_keys)
        .bind(&deadlines)
        .fetch_one(&mut **transaction)
        .await?;
        if pulled_ready {
            newsly_queue::notify_queue_in_transaction(
                transaction,
                newsly_queue::TaskType::BriefingRefresh.spec().queue,
                "briefing_refresh_pulled_forward",
            )
            .await?;
        }
    }
    Ok(())
}

fn split_model_spec(value: &str) -> (&str, &str) {
    value.split_once(':').unwrap_or(("unknown", value))
}

fn nonempty(value: &str) -> Option<&str> {
    (!value.trim().is_empty()).then_some(value)
}

fn centroid_model(model: &str) -> String {
    format!("{EMBEDDING_PROVIDER}:{model}")
}
