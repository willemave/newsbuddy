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

#[sqlx::test]
async fn expired_scrape_attempts_consume_budget_before_dispatch(pool: PgPool) {
    let (worker, task_id) =
        worker_with_failing_finalizer(&pool, 1, FinalizerErrorDisposition::Retryable).await;
    let first = worker
        .queue
        .claim(&worker.config.claim)
        .await
        .unwrap()
        .unwrap();
    for expected in [1, 2] {
        sqlx::query("UPDATE processing_tasks SET lease_expires_at = now() - interval '1 second' WHERE id::bigint = $1").bind(task_id).execute(&pool).await.unwrap();
        if expected == 1 {
            let replacement = worker
                .queue
                .claim(&worker.config.claim)
                .await
                .unwrap()
                .unwrap();
            assert_eq!(replacement.retry_count, expected);
            assert_ne!(replacement.lease_token, first.lease_token);
            assert!(
                worker
                    .queue
                    .begin_fenced_finalization(&first, &TaskResult::ok(), 1)
                    .await
                    .unwrap()
                    .is_none()
            );
        } else {
            assert!(matches!(
                worker.run_once().await.unwrap(),
                WorkerAttempt::Failed(_)
            ));
        }
    }
    let message: String =
        sqlx::query_scalar("SELECT error_message FROM processing_tasks WHERE id::bigint = $1")
            .bind(task_id)
            .fetch_one(&pool)
            .await
            .unwrap();
    assert_eq!(message, "Expired attempts exhausted the retry budget");
}

#[derive(Debug)]
struct NeverFinishes;
impl TaskHandler for NeverFinishes {
    fn task_type(&self) -> TaskType {
        TaskType::Scrape
    }
    fn execute(
        &self,
        _plan: std::sync::Arc<newsly_queue::OwnedWorkPlan>,
        _lease: LeaseHealth,
    ) -> HandlerFuture<'_> {
        Box::pin(std::future::pending())
    }
}

#[sqlx::test]
async fn a_hung_handler_cannot_renew_forever(pool: PgPool) {
    let (mut worker, _) =
        worker_with_failing_finalizer(&pool, 0, FinalizerErrorDisposition::Retryable).await;
    worker.handlers = HandlerRegistry::new();
    worker.handlers.register(NeverFinishes).unwrap();
    worker.config.attempt_timeout = Duration::from_millis(20);
    let attempt = tokio::time::timeout(Duration::from_secs(2), worker.run_once())
        .await
        .unwrap()
        .unwrap();
    assert!(matches!(attempt, WorkerAttempt::Failed(_)));
}

#[sqlx::test]
async fn terminal_queue_failure_settles_running_llm_ledger(pool: PgPool) {
    let (worker, _) =
        worker_with_failing_finalizer(&pool, 0, FinalizerErrorDisposition::Retryable).await;
    let user_id: i64 = sqlx::query_scalar("INSERT INTO users (apple_id, email, is_admin, is_active) VALUES ('terminal-ledger', 'ledger@example.test', FALSE, TRUE) RETURNING id::bigint").fetch_one(&pool).await.unwrap();
    let llm_id: i64 = sqlx::query_scalar("INSERT INTO llm_tasks (user_id, task_kind, mode, workflow_key, workflow_version, workflow_state, status, approval_policy, allowed_actions, tool_policy, input_json, output_json, artifact_manifest, usage_json, status_history) VALUES ($1::bigint::integer, 'share_action', 'add_content', 'share_action', 1, 'running', 'running', '{}', '[]', '{}', '{}', '{}', '{}', '{}', '[]') RETURNING id::bigint").bind(user_id).fetch_one(&pool).await.unwrap();
    let mut request = EnqueueRequest::new(TaskType::RunLlmTask);
    request.owner_user_id = Some(user_id);
    request.payload = json!({"llm_task_id": llm_id, "user_id": user_id})
        .as_object()
        .cloned();
    worker.queue.enqueue(request).await.unwrap();
    let scope = ClaimRuntimeScope::namespaces(
        RuntimeOwner::Rust,
        [ResourceKey::new("run_llm_task").unwrap()],
    )
    .unwrap();
    let claim = worker
        .queue
        .claim(&ClaimRequest::for_queue(
            "ledger-test",
            TaskQueue::Llm,
            scope,
        ))
        .await
        .unwrap()
        .unwrap();
    let attempt = worker
        .finalize_attempt(
            &claim,
            HandlerExecution::with_finalizer(
                TaskResult::ok(),
                FailingFinalizer {
                    disposition: FinalizerErrorDisposition::Retryable,
                },
            ),
        )
        .await
        .unwrap();
    assert!(matches!(attempt, WorkerAttempt::Failed(_)));
    let state: (String, String) =
        sqlx::query_as("SELECT status, workflow_state FROM llm_tasks WHERE id::bigint = $1")
            .bind(llm_id)
            .fetch_one(&pool)
            .await
            .unwrap();
    assert_eq!(state, ("failed".to_owned(), "failed".to_owned()));
    let mut conn = pool.acquire().await.unwrap();
    assert_eq!(
        newsly_db::pipeline_health_counts(&mut conn)
            .await
            .unwrap()
            .terminal_product_mismatches,
        0
    );
    // A legacy mismatch is observable, and a real pending retry removes that mismatch.
    sqlx::query("UPDATE llm_tasks SET status = 'running' WHERE id::bigint = $1")
        .bind(llm_id)
        .execute(&mut *conn)
        .await
        .unwrap();
    assert_eq!(
        newsly_db::pipeline_health_counts(&mut conn)
            .await
            .unwrap()
            .terminal_product_mismatches,
        1
    );
    drop(conn);
    let mut retry = EnqueueRequest::new(TaskType::RunLlmTask);
    retry.owner_user_id = Some(user_id);
    retry.payload = json!({"llm_task_id": llm_id, "user_id": user_id})
        .as_object()
        .cloned();
    worker.queue.enqueue(retry).await.unwrap();
    let mut conn = pool.acquire().await.unwrap();
    assert_eq!(
        newsly_db::pipeline_health_counts(&mut conn)
            .await
            .unwrap()
            .terminal_product_mismatches,
        0
    );
}

#[derive(Debug)]
struct DelayedAllocation {
    cleaned: std::sync::Arc<std::sync::atomic::AtomicBool>,
}
impl TaskHandler for DelayedAllocation {
    fn task_type(&self) -> TaskType {
        TaskType::RunLlmTask
    }
    fn execute(
        &self,
        _plan: std::sync::Arc<newsly_queue::OwnedWorkPlan>,
        lease: LeaseHealth,
    ) -> HandlerFuture<'_> {
        Box::pin(async move {
            // Allocation is deliberately not cancellable until its remote ID arrives.
            tokio::time::sleep(Duration::from_millis(80)).await;
            assert!(lease.ownership_lost());
            self.cleaned
                .store(true, std::sync::atomic::Ordering::SeqCst);
            HandlerExecution::from_result(TaskResult::ok())
        })
    }
}

#[sqlx::test]
async fn deadline_drains_sandbox_allocation_before_finalizing(pool: PgPool) {
    let (mut worker, _) =
        worker_with_failing_finalizer(&pool, 0, FinalizerErrorDisposition::Retryable).await;
    let user_id: i64 = sqlx::query_scalar("INSERT INTO users (apple_id, email, is_admin, is_active) VALUES ('allocation', 'allocation@example.test', FALSE, TRUE) RETURNING id::bigint").fetch_one(&pool).await.unwrap();
    let mut request = EnqueueRequest::new(TaskType::RunLlmTask);
    request.owner_user_id = Some(user_id);
    request.payload = json!({"llm_task_id": 1, "user_id": user_id})
        .as_object()
        .cloned();
    worker.queue.enqueue(request).await.unwrap();
    let cleaned = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));
    worker.handlers = HandlerRegistry::new();
    worker
        .handlers
        .register(DelayedAllocation {
            cleaned: cleaned.clone(),
        })
        .unwrap();
    let scope = ClaimRuntimeScope::namespaces(
        RuntimeOwner::Rust,
        [ResourceKey::new("run_llm_task").unwrap()],
    )
    .unwrap();
    worker.config.claim = ClaimRequest::for_queue("allocation-test", TaskQueue::Llm, scope);
    worker.config.attempt_timeout = Duration::from_millis(20);
    let attempt = tokio::time::timeout(Duration::from_secs(2), worker.run_once())
        .await
        .unwrap()
        .unwrap();
    assert!(matches!(attempt, WorkerAttempt::Failed(_)));
    assert!(cleaned.load(std::sync::atomic::Ordering::SeqCst));
}

#[sqlx::test]
async fn terminal_failure_settles_related_workflows_and_preserves_newer_generations(pool: PgPool) {
    let (_, task_id) =
        worker_with_failing_finalizer(&pool, 0, FinalizerErrorDisposition::Retryable).await;
    let user_id: i64 = sqlx::query_scalar("INSERT INTO users (apple_id, email, is_admin, is_active) VALUES ('terminal-workflows', 'workflows@example.test', FALSE, TRUE) RETURNING id::bigint").fetch_one(&pool).await.unwrap();
    let session_id: i64 = sqlx::query_scalar("INSERT INTO chat_sessions (user_id, council_mode, is_hidden_from_history) VALUES ($1::integer, FALSE, FALSE) RETURNING id::bigint").bind(user_id).fetch_one(&pool).await.unwrap();
    let message_id: i64 = sqlx::query_scalar("INSERT INTO chat_messages (session_id, message_list, status, stream_generation) VALUES ($1::integer, '[]', 'processing', 0) RETURNING id::bigint").bind(session_id).fetch_one(&pool).await.unwrap();
    let episode_id: i64 = sqlx::query_scalar("INSERT INTO audio_episodes (user_id, kind, status, title, input_hash, source_item_ids, source_snapshot, prompt_version, audio_content_type, share_enabled, started_at) VALUES ($1::integer, 'briefing', 'processing', 'Test', 'hash', '[]', '{}', 1, 'audio/mpeg', FALSE, timezone('UTC', now())) RETURNING id::bigint").bind(user_id).fetch_one(&pool).await.unwrap();
    let run_id: i64 = sqlx::query_scalar("INSERT INTO onboarding_discovery_runs (user_id, status, created_at, discovery_task_id, discovery_retry_count) VALUES ($1::integer, 'processing', now(), $2, 0) RETURNING id::bigint").bind(user_id).bind(task_id).fetch_one(&pool).await.unwrap();
    let weekly_id: i64 = sqlx::query_scalar("INSERT INTO feed_discovery_runs (user_id, status, discovery_task_id, discovery_retry_count) VALUES ($1::integer, 'processing', $2, 1) RETURNING id::bigint").bind(user_id).bind(task_id).fetch_one(&pool).await.unwrap();
    let content_id: i64 = sqlx::query_scalar("INSERT INTO contents (content_type, url, is_aggregate, status, content_metadata) VALUES ('article', 'https://example.test/artwork', FALSE, 'completed', '{\"domain\":{\"artwork_status\":\"pending\"}}') RETURNING id::bigint").fetch_one(&pool).await.unwrap();
    sqlx::query("UPDATE processing_tasks SET payload = $2 WHERE id::bigint = $1")
        .bind(task_id)
        .bind(json!({"message_id":message_id,"audio_episode_id":episode_id}))
        .execute(&pool)
        .await
        .unwrap();
    let mut tx = pool.begin().await.unwrap();
    for kind in [
        "chat_turn",
        "generate_audio_episode",
        "onboarding_discover",
        "discover_feeds",
        "generate_image",
    ] {
        newsly_db::settle_failed_task(
            &mut tx,
            task_id,
            kind,
            Some(content_id),
            Some(user_id),
            "deadline",
        )
        .await
        .unwrap();
    }
    tx.commit().await.unwrap();
    let chat: String = sqlx::query_scalar("SELECT status FROM chat_messages WHERE id::bigint = $1")
        .bind(message_id)
        .fetch_one(&pool)
        .await
        .unwrap();
    let audio: String =
        sqlx::query_scalar("SELECT status FROM audio_episodes WHERE id::bigint = $1")
            .bind(episode_id)
            .fetch_one(&pool)
            .await
            .unwrap();
    let onboarding: String =
        sqlx::query_scalar("SELECT status FROM onboarding_discovery_runs WHERE id::bigint = $1")
            .bind(run_id)
            .fetch_one(&pool)
            .await
            .unwrap();
    let weekly: String =
        sqlx::query_scalar("SELECT status FROM feed_discovery_runs WHERE id::bigint = $1")
            .bind(weekly_id)
            .fetch_one(&pool)
            .await
            .unwrap();
    let artwork: String = sqlx::query_scalar("SELECT content_metadata::jsonb #>> '{domain,artwork_status}' FROM contents WHERE id::bigint = $1").bind(content_id).fetch_one(&pool).await.unwrap();
    assert_eq!(
        (
            chat.as_str(),
            audio.as_str(),
            onboarding.as_str(),
            weekly.as_str(),
            artwork.as_str()
        ),
        ("failed", "failed", "failed", "processing", "failed")
    );
    sqlx::query("UPDATE processing_tasks SET retry_count = 1 WHERE id::bigint = $1")
        .bind(task_id)
        .execute(&pool)
        .await
        .unwrap();
    let mut tx = pool.begin().await.unwrap();
    newsly_db::settle_failed_task(
        &mut tx,
        task_id,
        "discover_feeds",
        None,
        Some(user_id),
        "deadline",
    )
    .await
    .unwrap();
    tx.commit().await.unwrap();
    let weekly: String =
        sqlx::query_scalar("SELECT status FROM feed_discovery_runs WHERE id::bigint = $1")
            .bind(weekly_id)
            .fetch_one(&pool)
            .await
            .unwrap();
    assert_eq!(weekly, "failed");
}

#[sqlx::test]
async fn x_continuation_survives_a_fresh_preparation_without_promoting_checkpoint(pool: PgPool) {
    newsly_db::run_migrations(&pool).await.unwrap();
    let user_id: i64 = sqlx::query_scalar("INSERT INTO users (apple_id, email, is_admin, is_active) VALUES ('x-resume', 'x-resume@example.test', FALSE, TRUE) RETURNING id::bigint").fetch_one(&pool).await.unwrap();
    let connection_id: i64 = sqlx::query_scalar("INSERT INTO user_integration_connections (user_id, provider, access_token_encrypted, created_at) VALUES ($1::integer, 'x', 'encrypted-fixture', now()) RETURNING id::bigint").bind(user_id).fetch_one(&pool).await.unwrap();
    let now = chrono::Utc::now();
    let mut tx = pool.begin().await.unwrap();
    newsly_db::complete_x_sync(
        &mut tx,
        connection_id,
        "completed",
        Some("100"),
        &json!({"bookmarks":{"last_synced_item_id":"100"}}),
        now,
    )
    .await
    .unwrap();
    tx.commit().await.unwrap();
    let mut tx = pool.begin().await.unwrap();
    newsly_db::complete_x_sync(&mut tx, connection_id, "partial", None, &json!({"bookmarks":{"in_progress":true,"continuation":"page-11","pending_newest_item_id":"160","last_synced_item_id":"100"}}), now).await.unwrap();
    tx.commit().await.unwrap();
    let mut tx = pool.begin().await.unwrap();
    let newsly_db::PrepareXSyncOutcome::Prepared(plan) =
        newsly_db::prepare_x_sync(&mut tx, user_id, false, now, 60, 60)
            .await
            .unwrap()
    else {
        panic!("continuation must bypass cooldown")
    };
    assert_eq!(plan.bookmark_cursor.as_deref(), Some("page-11"));
    assert_eq!(plan.bookmark_pending_newest.as_deref(), Some("160"));
    assert_eq!(plan.last_synced_item_id.as_deref(), Some("100"));
    assert!(!plan.skip_bookmarks);
    newsly_db::complete_x_sync(
        &mut tx,
        connection_id,
        "completed",
        Some("160"),
        &json!({"bookmarks":{"in_progress":false,"last_synced_item_id":"160"}}),
        now,
    )
    .await
    .unwrap();
    tx.commit().await.unwrap();
    let checkpoint: String = sqlx::query_scalar("SELECT last_synced_item_id FROM user_integration_sync_state WHERE connection_id::bigint = $1").bind(connection_id).fetch_one(&pool).await.unwrap();
    assert_eq!(checkpoint, "160");
    let mut tx = pool.begin().await.unwrap();
    assert!(matches!(
        newsly_db::prepare_x_sync(&mut tx, user_id, false, now, 60, 60)
            .await
            .unwrap(),
        newsly_db::PrepareXSyncOutcome::SkippedRecently
    ));
}

#[sqlx::test]
async fn watchdog_distinguishes_repeated_source_failure_from_a_healthy_empty_check(pool: PgPool) {
    let (_, task_id) =
        worker_with_failing_finalizer(&pool, 0, FinalizerErrorDisposition::Retryable).await;
    let mut tx = pool.begin().await.unwrap();
    for _ in 0..3 {
        newsly_db::record_source_health(
            &mut tx,
            "aggregator:test",
            None,
            0,
            0,
            Some("invalid_html"),
        )
        .await
        .unwrap();
    }
    assert_eq!(
        newsly_db::pipeline_health_counts(&mut tx)
            .await
            .unwrap()
            .failing_sources,
        1
    );
    newsly_db::record_source_health(&mut tx, "aggregator:test", None, 0, 0, None)
        .await
        .unwrap();
    assert_eq!(
        newsly_db::pipeline_health_counts(&mut tx)
            .await
            .unwrap()
            .failing_sources,
        0
    );
    sqlx::query("UPDATE processing_tasks SET available_at = timezone('UTC', now()) - interval '3 hours' WHERE id::bigint = $1").bind(task_id).execute(&mut *tx).await.unwrap();
    assert_eq!(
        newsly_db::pipeline_health_counts(&mut tx)
            .await
            .unwrap()
            .overdue_tasks,
        1
    );
}
