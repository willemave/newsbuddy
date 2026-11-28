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
                WHERE available_at <= timezone('UTC', clock_timestamp())
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

#[cfg(test)]
mod pull_forward_tests {
    use chrono::{DateTime, Duration, Utc};
    use newsly_queue::{EnqueueRequest, QueueKernel, TaskType};
    use serde_json::{Map, Value};
    use sqlx::PgPool;
    use sqlx::postgres::PgListener;

    use super::enqueue_and_pull_forward_briefing_refreshes;

    fn briefing_request(
        user_id: i64,
        dedupe_key: String,
        available_at: DateTime<Utc>,
    ) -> EnqueueRequest {
        let mut request = EnqueueRequest::new(TaskType::BriefingRefresh);
        request.owner_user_id = Some(user_id);
        request.payload = Some(Map::from_iter([(
            "user_id".to_owned(),
            Value::from(user_id),
        )]));
        request.dedupe = Some(true);
        request.dedupe_key = Some(dedupe_key);
        request.available_at = Some(available_at);
        request
    }

    #[sqlx::test(migrations = "../newsly-db/migrations")]
    async fn pull_forward_notifies_ready_queue_and_preserves_backing_off_retries(pool: PgPool) {
        let user_id = sqlx::query_scalar::<_, i64>(
            "INSERT INTO users (apple_id,email,is_admin,is_active) VALUES ('pull-fwd','pull-fwd@example.com',FALSE,TRUE) RETURNING id::bigint",
        )
        .fetch_one(&pool)
        .await
        .unwrap();
        let backing_off_user_id = sqlx::query_scalar::<_, i64>(
            "INSERT INTO users (apple_id,email,is_admin,is_active) VALUES ('pull-backoff','pull-backoff@example.com',FALSE,TRUE) RETURNING id::bigint",
        )
        .fetch_one(&pool)
        .await
        .unwrap();

        let queue = QueueKernel::new(pool.clone());
        let future_at = Utc::now() + Duration::minutes(10);
        let ready_key = format!("briefing_refresh:{user_id}");
        let backoff_key = format!("briefing_refresh:{backing_off_user_id}");

        let mut setup_tx = pool.begin().await.unwrap();
        queue
            .enqueue_many_in_transaction(
                &mut setup_tx,
                vec![
                    briefing_request(user_id, ready_key.clone(), future_at),
                    briefing_request(backing_off_user_id, backoff_key.clone(), future_at),
                ],
            )
            .await
            .unwrap();
        sqlx::query("UPDATE processing_tasks SET retry_count = 1 WHERE dedupe_key = $1")
            .bind(&backoff_key)
            .execute(&mut *setup_tx)
            .await
            .unwrap();
        setup_tx.commit().await.unwrap();

        let mut listener = PgListener::connect_with(&pool).await.unwrap();
        listener
            .listen(&TaskType::BriefingRefresh.spec().queue.notify_channel())
            .await
            .unwrap();

        // Begin the transaction BEFORE taking Utc::now() so transaction start `now()` precedes
        // the bound deadline — verifying `clock_timestamp()` still detects readiness and notifies.
        let mut pull_tx = pool.begin().await.unwrap();
        tokio::time::sleep(std::time::Duration::from_millis(15)).await;
        let now = Utc::now();
        enqueue_and_pull_forward_briefing_refreshes(
            &mut pull_tx,
            &queue,
            vec![
                briefing_request(user_id, ready_key.clone(), now),
                briefing_request(backing_off_user_id, backoff_key.clone(), now),
            ],
        )
        .await
        .unwrap();
        pull_tx.commit().await.unwrap();

        let notification = tokio::time::timeout(std::time::Duration::from_secs(2), listener.recv())
            .await
            .expect("pull-forward notification arrived")
            .unwrap();
        assert_eq!(
            notification.channel(),
            TaskType::BriefingRefresh.spec().queue.notify_channel()
        );
        assert_eq!(notification.payload(), "briefing_refresh_pulled_forward");

        let ready_available: chrono::NaiveDateTime =
            sqlx::query_scalar("SELECT available_at FROM processing_tasks WHERE dedupe_key = $1")
                .bind(&ready_key)
                .fetch_one(&pool)
                .await
                .unwrap();
        let backoff_available: chrono::NaiveDateTime =
            sqlx::query_scalar("SELECT available_at FROM processing_tasks WHERE dedupe_key = $1")
                .bind(&backoff_key)
                .fetch_one(&pool)
                .await
                .unwrap();
        assert!(ready_available <= now.naive_utc() + Duration::seconds(1));
        assert!(backoff_available > now.naive_utc() + Duration::minutes(5));
    }
}
