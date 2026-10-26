use super::*;
use crate::LlmTaskDeliveryKind;
use newsly_domain::ResourceKey;

async fn user(pool: &PgPool) -> i64 {
    sqlx::query_scalar("INSERT INTO users(apple_id,email,is_active,is_admin) VALUES($1,$2,true,false) RETURNING id::bigint")
        .bind(Uuid::new_v4().to_string())
        .bind(format!("{}@example.com", Uuid::new_v4()))
        .fetch_one(pool).await.unwrap()
}

async fn enqueue(queue: &QueueKernel, user_id: i64, task_id: i64) -> i64 {
    let request = EnqueueRequest::run_llm_task(task_id, user_id, LlmTaskDeliveryKind::LearningDeck);
    queue.enqueue(request).await.unwrap()
}

fn request(worker: &str) -> ClaimRequest {
    let mut request = ClaimRequest::for_queue(
        worker,
        TaskType::RunLlmTask.spec().queue,
        ClaimRuntimeScope::namespaces(
            RuntimeOwner::Rust,
            [ResourceKey::new("run_llm_task").unwrap()],
        )
        .unwrap(),
    );
    request.task_type = Some(TaskType::RunLlmTask);
    request
}

#[sqlx::test(migrations = "../newsly-db/migrations")]
async fn concurrent_claims_exclude_same_owner(pool: PgPool) {
    let queue = QueueKernel::new(pool.clone());
    let owner = user(&pool).await;
    enqueue(&queue, owner, 1).await;
    enqueue(&queue, owner, 2).await;
    let first_request = request("first");
    let second_request = request("second");
    let (first, second) = tokio::join!(queue.claim(&first_request), queue.claim(&second_request));
    assert_eq!(
        [first.unwrap(), second.unwrap()].iter().flatten().count(),
        1
    );
    assert!(queue.claim(&request("third")).await.unwrap().is_none());
}

#[sqlx::test(migrations = "../newsly-db/migrations")]
async fn concurrent_claims_allow_different_owners(pool: PgPool) {
    let queue = QueueKernel::new(pool.clone());
    enqueue(&queue, user(&pool).await, 1).await;
    enqueue(&queue, user(&pool).await, 2).await;
    let first_request = request("first");
    let second_request = request("second");
    let (first, second) = tokio::join!(queue.claim(&first_request), queue.claim(&second_request));
    assert_ne!(
        first.unwrap().unwrap().owner_user_id,
        second.unwrap().unwrap().owner_user_id
    );
}

async fn index_race(pool: PgPool, commit: bool) {
    let queue = QueueKernel::new(pool.clone());
    let user = user(&pool).await;
    let first = enqueue(&queue, user, 2).await;
    let waiting_delivery = enqueue(&queue, user, 1).await;
    let mut writer = pool.begin().await.unwrap();
    let writer_pid: i32 = sqlx::query_scalar("SELECT pg_backend_pid()")
        .fetch_one(&mut *writer)
        .await
        .unwrap();
    sqlx::query("UPDATE processing_tasks SET status='processing',locked_by='uncommitted-writer',lease_token=$2,locked_at=now(),lease_expires_at=now()+interval '5 minutes' WHERE id::bigint=$1")
        .bind(first).bind(Uuid::new_v4()).execute(&mut *writer).await.unwrap();
    let worker = tokio::spawn(async move { queue.claim(&request("index-racer")).await });
    // Prove the claimer reached the uncommitted index entry before allowing the writer to
    // finish. A scheduling sleep cannot establish this interleaving.
    tokio::time::timeout(Duration::from_secs(5), async {
        loop {
            let waiting: bool = sqlx::query_scalar("SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE $1=ANY(pg_blocking_pids(pid)) AND wait_event='transactionid')")
                .bind(writer_pid).fetch_one(&pool).await.unwrap();
            if waiting { break; }
            tokio::task::yield_now().await;
        }
    }).await.expect("claimer must wait on writer's uncommitted unique-index entry");
    if commit {
        writer.commit().await.unwrap();
    } else {
        writer.rollback().await.unwrap();
    }
    let claim = tokio::time::timeout(Duration::from_secs(5), worker)
        .await
        .unwrap()
        .unwrap()
        .unwrap();
    if commit {
        assert!(
            claim.is_none(),
            "named unique conflict is retried with a fresh snapshot"
        );
    } else {
        assert_eq!(claim.unwrap().id, waiting_delivery);
    }
}

#[sqlx::test(migrations = "../newsly-db/migrations")]
async fn uncommitted_owner_claim_commit_retries_the_named_index_conflict(pool: PgPool) {
    index_race(pool, true).await;
}

#[sqlx::test(migrations = "../newsly-db/migrations")]
async fn uncommitted_owner_claim_rollback_allows_the_waiter(pool: PgPool) {
    index_race(pool, false).await;
}

#[sqlx::test(migrations = "../newsly-db/migrations")]
async fn expired_owner_reclaims_itself_and_fences_old_worker(pool: PgPool) {
    let queue = QueueKernel::new(pool.clone());
    let user = user(&pool).await;
    let first_delivery = enqueue(&queue, user, 1).await;
    let original = queue.claim(&request("original")).await.unwrap().unwrap();
    let next_delivery = enqueue(&queue, user, 3).await;
    assert!(queue.claim(&request("blocked")).await.unwrap().is_none());
    sqlx::query("UPDATE processing_tasks SET lease_expires_at=now()-interval '1 second' WHERE id::bigint=$1")
        .bind(first_delivery).execute(&pool).await.unwrap();
    let replacement = queue.claim(&request("replacement")).await.unwrap().unwrap();
    assert_eq!(replacement.id, first_delivery);
    assert_eq!(replacement.retry_count, original.retry_count + 1);
    assert_ne!(replacement.lease_token, original.lease_token);
    assert!(
        queue
            .finalize(&original, &TaskResult::ok(), 3)
            .await
            .unwrap()
            .is_none()
    );
    assert!(
        queue
            .claim(&request("still-blocked"))
            .await
            .unwrap()
            .is_none()
    );
    queue
        .finalize(&replacement, &TaskResult::ok(), 3)
        .await
        .unwrap()
        .unwrap();
    let next_claim = queue.claim(&request("next-deck")).await.unwrap().unwrap();
    assert_eq!(next_claim.id, next_delivery);
    assert_eq!(next_claim.payload["llm_task_id"], 3);
}

#[sqlx::test(migrations = "../newsly-db/migrations")]
async fn terminal_outcomes_release_next_deck(pool: PgPool) {
    let queue = QueueKernel::new(pool.clone());
    for result in [
        TaskResult::ok(),
        TaskResult::fail(Some("permanent".to_owned()), false),
    ] {
        let owner = user(&pool).await;
        enqueue(&queue, owner, 1).await;
        let active = queue.claim(&request("active")).await.unwrap().unwrap();
        let next = enqueue(&queue, owner, 2).await;
        queue.finalize(&active, &result, 3).await.unwrap().unwrap();
        let next_claim = queue.claim(&request("next")).await.unwrap().unwrap();
        assert_eq!(next_claim.id, next);
        queue
            .finalize(&next_claim, &TaskResult::ok(), 3)
            .await
            .unwrap()
            .unwrap();
    }
}

#[sqlx::test(migrations = "../newsly-db/migrations")]
async fn retry_and_source_wait_allow_independent_deck_then_reclaim_safely(pool: PgPool) {
    let queue = QueueKernel::new(pool.clone());
    for result in [
        TaskResult::defer(60),
        TaskResult {
            retry_delay_seconds: Some(60),
            ..TaskResult::fail(Some("retry".to_owned()), true)
        },
    ] {
        let owner = user(&pool).await;
        let first = enqueue(&queue, owner, 1).await;
        let original = queue.claim(&request("original")).await.unwrap().unwrap();
        let next = enqueue(&queue, owner, 2).await;
        queue
            .finalize(&original, &result, 3)
            .await
            .unwrap()
            .unwrap();
        let next_claim = queue.claim(&request("independent")).await.unwrap().unwrap();
        assert_eq!(next_claim.id, next);
        // Even once the first delivery is ready again, the independent deck owns the slot.
        sqlx::query("UPDATE processing_tasks SET available_at=now()-interval '1 second' WHERE id::bigint=$1")
            .bind(first).execute(&pool).await.unwrap();
        assert!(queue.claim(&request("blocked")).await.unwrap().is_none());
        queue
            .finalize(&next_claim, &TaskResult::ok(), 3)
            .await
            .unwrap()
            .unwrap();
        let resumed = queue.claim(&request("resumed")).await.unwrap().unwrap();
        assert_eq!(resumed.id, first);
        assert_ne!(resumed.lease_token, original.lease_token);
        assert!(
            queue
                .finalize(&original, &TaskResult::ok(), 3)
                .await
                .unwrap()
                .is_none()
        );
        queue
            .finalize(&resumed, &TaskResult::ok(), 3)
            .await
            .unwrap()
            .unwrap();
    }
}

#[sqlx::test(migrations = "../newsly-db/migrations")]
async fn share_actions_do_not_acquire_deck_exclusion(pool: PgPool) {
    let queue = QueueKernel::new(pool.clone());
    let owner = user(&pool).await;
    enqueue(&queue, owner, 1).await;
    queue.claim(&request("deck")).await.unwrap().unwrap();
    let action = EnqueueRequest::run_llm_task(2, owner, LlmTaskDeliveryKind::ShareAction);
    let id = queue.enqueue(action).await.unwrap();
    assert_eq!(
        queue
            .claim(&request("share-action"))
            .await
            .unwrap()
            .unwrap()
            .id,
        id
    );
}

#[sqlx::test(migrations = "../newsly-db/migrations")]
async fn started_retry_reserves_owner_slot_without_consuming_budget_on_a_later_deck(pool: PgPool) {
    let queue = QueueKernel::new(pool.clone());
    let owner = user(&pool).await;
    let task: i64 = sqlx::query_scalar("INSERT INTO llm_tasks(user_id,task_kind,mode,workflow_key,workflow_version,workflow_state,status,approval_policy,allowed_actions,tool_policy,input_json,output_json,artifact_manifest,usage_json,status_history) VALUES($1,'learning_deck','learning_deck_presentation','learning_deck.presentation.v1',1,'running','running','{}','[]','{}','{}','{}','{}','{}','[]') RETURNING id::bigint")
        .bind(owner).fetch_one(&pool).await.unwrap();
    let first = enqueue(&queue, owner, task).await;
    let original = queue.claim(&request("original")).await.unwrap().unwrap();
    sqlx::query("INSERT INTO learning_deck_generation_state(llm_task_id,input_fingerprint,deadline_at) VALUES($1,$2,now()+interval '15 minutes')")
        .bind(task).bind("a".repeat(64)).execute(&pool).await.unwrap();
    let next = enqueue(&queue, owner, task + 1).await;
    queue
        .finalize(
            &original,
            &TaskResult {
                retry_delay_seconds: Some(60),
                ..TaskResult::fail(Some("transient provider failure".to_owned()), true)
            },
            3,
        )
        .await
        .unwrap()
        .unwrap();
    assert!(
        queue
            .claim(&request("waiting-for-retry"))
            .await
            .unwrap()
            .is_none(),
        "a later deck cannot consume the running retry's remaining wall-clock budget"
    );
    let independent = enqueue(&queue, user(&pool).await, task + 2).await;
    let other = queue.claim(&request("other-user")).await.unwrap().unwrap();
    assert_eq!(other.id, independent);
    queue
        .finalize(&other, &TaskResult::ok(), 3)
        .await
        .unwrap()
        .unwrap();
    sqlx::query(
        "UPDATE processing_tasks SET available_at=now()-interval '1 second' WHERE id::bigint=$1",
    )
    .bind(first)
    .execute(&pool)
    .await
    .unwrap();
    let resumed = queue.claim(&request("retry")).await.unwrap().unwrap();
    assert_eq!(resumed.id, first);
    queue
        .finalize(
            &resumed,
            &TaskResult::fail(Some("terminal".to_owned()), false),
            3,
        )
        .await
        .unwrap()
        .unwrap();
    assert_eq!(
        queue.claim(&request("next")).await.unwrap().unwrap().id,
        next
    );
}
