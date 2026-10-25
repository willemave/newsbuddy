use super::*;
use newsly_domain::ResourceKey;

async fn claimed(queue: &QueueKernel) -> ClaimedTask {
    let user: i64 = sqlx::query_scalar(
        "INSERT INTO users(apple_id,email,is_active,is_admin) VALUES($1,$2,true,false) RETURNING id::bigint",
    )
    .bind(Uuid::new_v4().to_string())
    .bind(format!("{}@example.com", Uuid::new_v4()))
    .fetch_one(&queue.pool)
    .await
    .unwrap();
    let mut enqueue = EnqueueRequest::new(TaskType::BriefingRefresh);
    enqueue.owner_user_id = Some(user);
    enqueue.payload = json!({"user_id":user,"mode":"append"}).as_object().cloned();
    queue.enqueue(enqueue).await.unwrap();
    let mut request = ClaimRequest::for_queue(
        "live-claim-test",
        TaskType::BriefingRefresh.spec().queue,
        ClaimRuntimeScope::namespaces(
            RuntimeOwner::Rust,
            [ResourceKey::new("briefing_refresh").unwrap()],
        )
        .unwrap(),
    );
    request.task_type = Some(TaskType::BriefingRefresh);
    queue.claim(&request).await.unwrap().unwrap()
}

#[sqlx::test(migrations = "../newsly-db/migrations")]
async fn exact_live_claim_locks_the_task_until_transaction_ends(pool: PgPool) {
    let queue = QueueKernel::new(pool.clone());
    let claim = claimed(&queue).await;
    let mut transaction = pool.begin().await.unwrap();
    assert!(
        queue
            .lock_live_claim(&mut transaction, &claim)
            .await
            .unwrap()
    );
    let lock_error =
        sqlx::query("SELECT id FROM processing_tasks WHERE id::bigint = $1 FOR UPDATE NOWAIT")
            .bind(claim.id)
            .fetch_one(&pool)
            .await
            .unwrap_err();
    assert_eq!(
        lock_error.as_database_error().unwrap().code().as_deref(),
        Some("55P03")
    );
    transaction.rollback().await.unwrap();
    sqlx::query("SELECT id FROM processing_tasks WHERE id::bigint = $1 FOR UPDATE NOWAIT")
        .bind(claim.id)
        .fetch_one(&pool)
        .await
        .unwrap();
}

#[sqlx::test(migrations = "../newsly-db/migrations")]
async fn stale_claim_identity_never_authorizes_mutation(pool: PgPool) {
    let queue = QueueKernel::new(pool.clone());
    let claim = claimed(&queue).await;
    let mut wrong_worker = claim.clone();
    wrong_worker.locked_by.push_str("-other");
    let mut wrong_token = claim.clone();
    wrong_token.lease_token = LeaseToken::new();
    let mut wrong_retry = claim.clone();
    wrong_retry.retry_count += 1;
    let mut wrong_version = claim.clone();
    wrong_version.executor_version += 1;
    let mut wrong_runtime = claim.clone();
    wrong_runtime.executor_runtime = RuntimeOwner::Python;
    let mut wrong_namespace = claim.clone();
    wrong_namespace.executor_namespace.push_str("-other");
    for stale in [
        wrong_worker,
        wrong_token,
        wrong_retry,
        wrong_version,
        wrong_runtime,
        wrong_namespace,
    ] {
        let mut transaction = pool.begin().await.unwrap();
        assert!(
            !queue
                .lock_live_claim(&mut transaction, &stale)
                .await
                .unwrap()
        );
        transaction.rollback().await.unwrap();
    }
}

#[sqlx::test(migrations = "../newsly-db/migrations")]
async fn expired_or_terminal_claim_never_authorizes_mutation(pool: PgPool) {
    let queue = QueueKernel::new(pool.clone());
    let claim = claimed(&queue).await;
    sqlx::query("UPDATE processing_tasks SET lease_expires_at = timezone('UTC', clock_timestamp()) - interval '1 second' WHERE id::bigint = $1")
        .bind(claim.id).execute(&pool).await.unwrap();
    let mut transaction = pool.begin().await.unwrap();
    assert!(
        !queue
            .lock_live_claim(&mut transaction, &claim)
            .await
            .unwrap()
    );
    transaction.rollback().await.unwrap();
    sqlx::query("UPDATE processing_tasks SET status = 'failed', lease_token = NULL, lease_expires_at = timezone('UTC', clock_timestamp()) + interval '5 minutes' WHERE id::bigint = $1")
        .bind(claim.id).execute(&pool).await.unwrap();
    let mut transaction = pool.begin().await.unwrap();
    assert!(
        !queue
            .lock_live_claim(&mut transaction, &claim)
            .await
            .unwrap()
    );
}

#[sqlx::test(migrations = "../newsly-db/migrations")]
async fn claim_expiry_uses_operation_time_in_an_older_transaction(pool: PgPool) {
    let queue = QueueKernel::new(pool.clone());
    let claim = claimed(&queue).await;
    let mut transaction = pool.begin().await.unwrap();
    // Fix the transaction timestamp before advancing the row's lease deadline.
    let transaction_time: chrono::NaiveDateTime =
        sqlx::query_scalar("SELECT timezone('UTC', now())")
            .fetch_one(&mut *transaction)
            .await
            .unwrap();
    sqlx::query("UPDATE processing_tasks SET lease_expires_at = timezone('UTC', clock_timestamp()) WHERE id::bigint = $1")
        .bind(claim.id).execute(&pool).await.unwrap();
    let deadline: chrono::NaiveDateTime =
        sqlx::query_scalar("SELECT lease_expires_at FROM processing_tasks WHERE id::bigint = $1")
            .bind(claim.id)
            .fetch_one(&pool)
            .await
            .unwrap();
    assert!(deadline > transaction_time);
    assert!(
        !queue
            .lock_live_claim(&mut transaction, &claim)
            .await
            .unwrap()
    );
}
