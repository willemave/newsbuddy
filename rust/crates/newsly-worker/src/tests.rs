use std::time::Duration;

use newsly_domain::{ResourceKey, RuntimeOwner};
use newsly_queue::{
    ClaimRequest, ClaimRuntimeScope, EnqueueRequest, QueueKernel, TaskQueue, TaskResult, TaskType,
};
use serde_json::json;
use sqlx::{PgPool, Postgres, Transaction};

use super::{
    FinalizerErrorDisposition, HandlerExecution, HandlerFinalizerFuture, HandlerFuture,
    HandlerRegistry, LeaseHealth, TaskFinalizer, TaskHandler, WorkerAttempt, WorkerConfig,
    WorkerKernel, heartbeat_interval, heartbeat_retry_interval,
};

#[derive(Debug)]
struct FailingHandler {
    disposition: FinalizerErrorDisposition,
}

impl TaskHandler for FailingHandler {
    fn task_type(&self) -> TaskType {
        TaskType::Scrape
    }

    fn execute(
        &self,
        _plan: std::sync::Arc<newsly_queue::OwnedWorkPlan>,
        _lease: LeaseHealth,
    ) -> HandlerFuture<'_> {
        let disposition = self.disposition;
        Box::pin(async move {
            HandlerExecution::with_finalizer(TaskResult::ok(), FailingFinalizer { disposition })
        })
    }
}

#[derive(Debug)]
struct FailingFinalizer {
    disposition: FinalizerErrorDisposition,
}

impl TaskFinalizer for FailingFinalizer {
    fn apply<'a>(
        &'a self,
        _transaction: &'a mut Transaction<'static, Postgres>,
    ) -> HandlerFinalizerFuture<'a> {
        Box::pin(async { Err(std::io::Error::other("synthetic finalizer failure").into()) })
    }

    fn error_disposition(
        &self,
        _source: &(dyn std::error::Error + Send + Sync),
    ) -> FinalizerErrorDisposition {
        self.disposition
    }
}

#[test]
fn heartbeat_cadence_preserves_existing_bounds() {
    assert_eq!(
        heartbeat_interval(Duration::from_secs(300)),
        Duration::from_secs(30)
    );
    assert_eq!(
        heartbeat_interval(Duration::from_secs(15)),
        Duration::from_secs(5)
    );
    assert_eq!(
        heartbeat_retry_interval(Duration::from_secs(2)),
        Duration::from_secs(2)
    );
}

async fn worker_with_failing_finalizer(
    pool: &PgPool,
    max_retries: i32,
    disposition: FinalizerErrorDisposition,
) -> (WorkerKernel, i64) {
    newsly_db::run_migrations(pool)
        .await
        .expect("schema should migrate");
    let queue = QueueKernel::new(pool.clone());
    let mut request = EnqueueRequest::new(TaskType::Scrape);
    request.payload = json!({"sources": ["sciurls"]}).as_object().cloned();
    let task_id = queue.enqueue(request).await.expect("task should enqueue");

    let scope = ClaimRuntimeScope::namespaces(
        RuntimeOwner::Rust,
        [ResourceKey::new(TaskType::Scrape.as_str()).expect("namespace should be valid")],
    )
    .expect("scope should be valid");
    let mut claim = ClaimRequest::for_queue("test-worker", TaskQueue::Content, scope);
    claim.task_type = Some(TaskType::Scrape);
    let mut config = WorkerConfig::new(claim);
    config.max_retries = max_retries;
    let mut handlers = HandlerRegistry::new();
    handlers
        .register(FailingHandler { disposition })
        .expect("handler should register");
    let worker = WorkerKernel::new(queue, handlers, config, None).expect("worker should be valid");
    (worker, task_id)
}

#[sqlx::test]
async fn retryable_finalizer_error_consumes_its_budget_and_terminalizes(pool: PgPool) {
    let (worker, task_id) =
        worker_with_failing_finalizer(&pool, 1, FinalizerErrorDisposition::Retryable).await;

    let first_attempt = worker
        .run_once()
        .await
        .expect("first attempt should finalize");
    let WorkerAttempt::Retried(first_transition) = first_attempt else {
        panic!("first finalizer failure should consume one retry")
    };
    assert_eq!(first_transition.retry_count, 1);
    sqlx::query("UPDATE processing_tasks SET available_at = timezone('UTC', now()) WHERE id = $1")
        .bind(task_id)
        .execute(&pool)
        .await
        .expect("retry should become immediately claimable");

    let second_attempt = worker.run_once().await.expect("retry should finalize");
    assert!(matches!(second_attempt, WorkerAttempt::Failed(_)));

    let row = sqlx::query_as::<_, (String, i32, Option<String>)>(
        "SELECT status, retry_count, error_message FROM processing_tasks WHERE id = $1",
    )
    .bind(task_id)
    .fetch_one(&pool)
    .await
    .expect("task should remain queryable");
    assert_eq!(row.0, "failed");
    assert_eq!(row.1, 1);
    assert_eq!(row.2.as_deref(), Some("scrape product finalization failed"));
}

#[sqlx::test]
async fn terminal_finalizer_error_does_not_consume_retry_budget(pool: PgPool) {
    let (worker, task_id) =
        worker_with_failing_finalizer(&pool, 3, FinalizerErrorDisposition::Terminal).await;

    let attempt = worker.run_once().await.expect("attempt should finalize");

    assert!(matches!(attempt, WorkerAttempt::Failed(_)));
    let retry_count =
        sqlx::query_scalar::<_, i32>("SELECT retry_count FROM processing_tasks WHERE id = $1")
            .bind(task_id)
            .fetch_one(&pool)
            .await
            .expect("task should remain queryable");
    assert_eq!(retry_count, 0);
}
