use super::*;
use newsly_domain::ResourceKey;

async fn user(pool: &PgPool) -> i64 {
    sqlx::query_scalar("INSERT INTO users(apple_id,email,is_active,is_admin) VALUES($1,$2,true,false) RETURNING id::bigint")
        .bind(Uuid::new_v4().to_string())
        .bind(format!("{}@example.com", Uuid::new_v4()))
        .fetch_one(pool).await.unwrap()
}

async fn enqueue(queue: &QueueKernel, user_id: i64, mode: &str) -> i64 {
    let mut request = EnqueueRequest::new(TaskType::BriefingRefresh);
    request.owner_user_id = Some(user_id);
    request.payload = json!({"user_id":user_id,"mode":mode}).as_object().cloned();
    request.dedupe = Some(true);
    request.dedupe_key = Some(format!("briefing_refresh:{user_id}:{mode}"));
    queue.enqueue(request).await.unwrap()
}

fn request(worker: &str) -> ClaimRequest {
    let mut request = ClaimRequest::for_queue(
        worker,
        TaskType::BriefingRefresh.spec().queue,
        ClaimRuntimeScope::namespaces(
            RuntimeOwner::Rust,
            [ResourceKey::new("briefing_refresh").unwrap()],
        )
        .unwrap(),
    );
    request.task_type = Some(TaskType::BriefingRefresh);
    request
}

#[sqlx::test(migrations = "../newsly-db/migrations")]
async fn concurrent_claims_exclude_one_owner(pool: PgPool) {
    let queue = QueueKernel::new(pool.clone());
    let user = user(&pool).await;
    enqueue(&queue, user, "append").await;
    enqueue(&queue, user, "sweep").await;
    let first_request = request("first");
    let second_request = request("second");
    let (first, second) = tokio::join!(queue.claim(&first_request), queue.claim(&second_request));
    let claims = [first.unwrap(), second.unwrap()];
    assert_eq!(claims.iter().flatten().count(), 1);
    let claim = claims.into_iter().flatten().next().unwrap();
    assert!(queue.claim(&request("third")).await.unwrap().is_none());
    assert_eq!(
        enqueue(&queue, user, claim.payload["mode"].as_str().unwrap()).await,
        claim.id
    );
}

#[sqlx::test(migrations = "../newsly-db/migrations")]
async fn concurrent_claims_allow_different_owners(pool: PgPool) {
    let queue = QueueKernel::new(pool.clone());
    enqueue(&queue, user(&pool).await, "append").await;
    enqueue(&queue, user(&pool).await, "append").await;
    let first_request = request("first");
    let second_request = request("second");
    let (first, second) = tokio::join!(queue.claim(&first_request), queue.claim(&second_request));
    let first = first.unwrap().unwrap();
    let second = second.unwrap().unwrap();
    assert_ne!(first.owner_user_id, second.owner_user_id);
}

async fn index_race(pool: PgPool, commit: bool) {
    let queue = QueueKernel::new(pool.clone());
    let user = user(&pool).await;
    let sweep = enqueue(&queue, user, "sweep").await;
    let append = enqueue(&queue, user, "append").await;
    let mut writer = pool.begin().await.unwrap();
    let writer_pid: i32 = sqlx::query_scalar("SELECT pg_backend_pid()")
        .fetch_one(&mut *writer)
        .await
        .unwrap();
    sqlx::query("UPDATE processing_tasks SET status='processing',locked_by='uncommitted-writer',lease_token=$2,locked_at=now(),lease_expires_at=now()+interval '5 minutes' WHERE id::bigint=$1")
        .bind(sweep).bind(Uuid::new_v4()).execute(&mut *writer).await.unwrap();
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
        assert_eq!(claim.unwrap().id, append);
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
async fn expired_owner_reclaims_itself_fences_old_worker_and_preserves_full(pool: PgPool) {
    let queue = QueueKernel::new(pool.clone());
    let user = user(&pool).await;
    let append = enqueue(&queue, user, "append").await;
    let original = queue.claim(&request("original")).await.unwrap().unwrap();
    let full = enqueue(&queue, user, "full").await;
    assert!(queue.claim(&request("blocked")).await.unwrap().is_none());
    sqlx::query("UPDATE processing_tasks SET lease_expires_at=now()-interval '1 second' WHERE id::bigint=$1")
        .bind(append).execute(&pool).await.unwrap();
    let replacement = queue.claim(&request("replacement")).await.unwrap().unwrap();
    assert_eq!(replacement.id, append);
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
    let full_claim = queue.claim(&request("full")).await.unwrap().unwrap();
    assert_eq!(full_claim.id, full);
    assert_eq!(full_claim.payload["mode"], "full");
}

#[sqlx::test(migrations = "../newsly-db/migrations")]
async fn completion_retry_and_deferral_release_owner_exclusion(pool: PgPool) {
    let queue = QueueKernel::new(pool.clone());
    for result in [
        TaskResult::ok(),
        TaskResult::defer(60),
        TaskResult {
            retry_delay_seconds: Some(60),
            ..TaskResult::fail(Some("retry me".to_owned()), true)
        },
    ] {
        let user = user(&pool).await;
        enqueue(&queue, user, "append").await;
        let active = queue.claim(&request("active")).await.unwrap().unwrap();
        let next = enqueue(&queue, user, "sweep").await;
        let transition = queue.finalize(&active, &result, 3).await.unwrap().unwrap();
        let next_claim = queue.claim(&request("next")).await.unwrap().unwrap();
        assert_eq!(next_claim.id, next);
        if !result.success {
            let available: NaiveDateTime =
                sqlx::query_scalar("SELECT available_at FROM processing_tasks WHERE id::bigint=$1")
                    .bind(active.id)
                    .fetch_one(&pool)
                    .await
                    .unwrap();
            assert_eq!(available.and_utc(), transition.available_at);
            assert!(available > Utc::now().naive_utc() + chrono::Duration::seconds(55));
        }
        queue
            .finalize(&next_claim, &TaskResult::ok(), 3)
            .await
            .unwrap()
            .unwrap();
    }
}

#[sqlx::test(migrations = "../newsly-db/migrations")]
async fn other_owned_tasks_are_not_excluded_by_briefing(pool: PgPool) {
    let queue = QueueKernel::new(pool.clone());
    let user = user(&pool).await;
    enqueue(&queue, user, "append").await;
    queue.claim(&request("briefing")).await.unwrap().unwrap();
    let mut chat = EnqueueRequest::new(TaskType::DigDeeper);
    chat.owner_user_id = Some(user);
    chat.payload = json!({"user_id":user}).as_object().cloned();
    let id = queue.enqueue(chat).await.unwrap();
    let mut chat_request = request("chat");
    chat_request.task_type = Some(TaskType::DigDeeper);
    chat_request.queue_name = Some(TaskType::DigDeeper.spec().queue);
    chat_request.runtime_scope = ClaimRuntimeScope::namespaces(
        RuntimeOwner::Rust,
        [ResourceKey::new("dig_deeper").unwrap()],
    )
    .unwrap();
    assert_eq!(queue.claim(&chat_request).await.unwrap().unwrap().id, id);
}

#[sqlx::test(migrations = "../newsly-db/migrations")]
async fn unrelated_unique_violation_is_not_retried_or_swallowed(pool: PgPool) {
    let queue = QueueKernel::new(pool.clone());
    enqueue(&queue, user(&pool).await, "append").await;
    enqueue(&queue, user(&pool).await, "append").await;
    sqlx::query("CREATE UNIQUE INDEX unrelated_worker_unique ON processing_tasks(locked_by) WHERE status='processing'")
        .execute(&pool).await.unwrap();
    queue
        .claim(&request("same-worker-name"))
        .await
        .unwrap()
        .unwrap();
    let error = queue.claim(&request("same-worker-name")).await.unwrap_err();
    let QueueError::Sqlx(sqlx::Error::Database(error)) = error else {
        panic!("expected unique violation")
    };
    assert_eq!(error.constraint(), Some("unrelated_worker_unique"));
}

#[sqlx::test(migrations = "../newsly-db/migrations")]
async fn migration_rejects_duplicate_processing_owners_without_mutating_them(pool: PgPool) {
    let queue = QueueKernel::new(pool.clone());
    let user = user(&pool).await;
    let first = enqueue(&queue, user, "append").await;
    let second = enqueue(&queue, user, "sweep").await;
    let mut transaction = pool.begin().await.unwrap();
    sqlx::query("DROP INDEX uq_processing_tasks_briefing_owner_processing")
        .execute(&mut *transaction)
        .await
        .unwrap();
    sqlx::query("UPDATE processing_tasks SET status='processing' WHERE id::bigint=ANY($1)")
        .bind([first, second])
        .execute(&mut *transaction)
        .await
        .unwrap();
    sqlx::query("SET LOCAL newsly.maintenance_barrier_confirmed = 'on'")
        .execute(&mut *transaction)
        .await
        .unwrap();
    let error = sqlx::raw_sql(include_str!(
        "../../../newsly-db/migrations/20261018000002_briefing_owner_claim_index.sql"
    ))
    .execute(&mut *transaction)
    .await
    .unwrap_err();
    assert!(
        error
            .to_string()
            .contains("multiple processing Briefing tasks")
    );
    transaction.rollback().await.unwrap();
    let count: i64 = sqlx::query_scalar(
        "SELECT count(*) FROM processing_tasks WHERE id::bigint=ANY($1) AND status='pending'",
    )
    .bind([first, second])
    .fetch_one(&pool)
    .await
    .unwrap();
    assert_eq!(count, 2);
}

#[derive(Default)]
struct RecoveryStats {
    active_owners: BTreeSet<i64>,
    claimed_owners: Vec<i64>,
    completed_ids: BTreeSet<i64>,
    max_parallel_users: usize,
    first_completion_ms: Option<u128>,
    last_completion_ms: u128,
}

#[sqlx::test(migrations = "../newsly-db/migrations")]
async fn hundred_user_recovery_with_two_consumers_and_fake_provider(pool: PgPool) {
    let queue = QueueKernel::new(pool.clone());
    let users: Vec<i64> = sqlx::query_scalar("INSERT INTO users(apple_id,email,is_active,is_admin) SELECT 'recovery-'||n,'recovery-'||n||'@example.com',true,false FROM generate_series(1,100) n RETURNING id::bigint")
        .fetch_all(&pool).await.unwrap();
    for &user in &users {
        for mode in ["append", "sweep"] {
            enqueue(&queue, user, mode).await;
        }
    }
    // Model ten first-run owners using the real queue priority field; every other row is fresh
    // retry-generation-zero background work, so this does not test retry-bucket fairness.
    let priority_owners = users[..10].to_vec();
    sqlx::query("UPDATE processing_tasks SET priority=100 WHERE owner_user_id::bigint=ANY($1)")
        .bind(&priority_owners)
        .execute(&pool)
        .await
        .unwrap();
    let stats = Arc::new(Mutex::new(RecoveryStats::default()));
    let first_claims = Arc::new(tokio::sync::Barrier::new(2));
    let started = std::time::Instant::now();
    let consumer = |name: &'static str| {
        let queue = queue.clone();
        let stats = Arc::clone(&stats);
        let first_claims = Arc::clone(&first_claims);
        async move {
            let request = request(name);
            let mut first = true;
            loop {
                if stats.lock().unwrap().completed_ids.len() == 200 {
                    break;
                }
                let Some(claim) = queue.claim(&request).await.unwrap() else {
                    tokio::task::yield_now().await;
                    continue;
                };
                let owner = claim.owner_user_id.unwrap();
                {
                    let mut stats = stats.lock().unwrap();
                    assert!(stats.active_owners.insert(owner), "same owner overlapped");
                    stats.claimed_owners.push(owner);
                    stats.max_parallel_users =
                        stats.max_parallel_users.max(stats.active_owners.len());
                }
                if first {
                    first = false;
                    first_claims.wait().await;
                }
                // Deliberately fake external work: this proves queue coordination and bounded
                // recovery, never real provider throughput or an onboarding latency SLO.
                tokio::time::sleep(Duration::from_millis(1)).await;
                {
                    let mut stats = stats.lock().unwrap();
                    assert!(stats.active_owners.remove(&owner));
                }
                queue
                    .finalize(&claim, &TaskResult::ok(), 3)
                    .await
                    .unwrap()
                    .unwrap();
                let mut stats = stats.lock().unwrap();
                let elapsed = started.elapsed().as_millis();
                stats.first_completion_ms.get_or_insert(elapsed);
                stats.last_completion_ms = elapsed;
                assert!(stats.completed_ids.insert(claim.id));
            }
        }
    };
    tokio::time::timeout(Duration::from_secs(15), async {
        tokio::join!(consumer("recovery-first"), consumer("recovery-second"));
    })
    .await
    .expect("all owners must drain without starvation");
    let remaining: i64 = sqlx::query_scalar(
        "SELECT count(*) FROM processing_tasks WHERE status IN ('pending','processing')",
    )
    .fetch_one(&pool)
    .await
    .unwrap();
    assert_eq!(remaining, 0);
    let stats = stats.lock().unwrap();
    assert_eq!(stats.completed_ids.len(), 200);
    assert_eq!(stats.max_parallel_users, 2);
    assert!(stats.active_owners.is_empty());
    let first_background = stats
        .claimed_owners
        .iter()
        .position(|owner| !priority_owners.contains(owner))
        .unwrap();
    for owner in priority_owners {
        assert!(
            stats
                .claimed_owners
                .iter()
                .position(|id| *id == owner)
                .unwrap()
                < first_background
        );
    }
    eprintln!(
        "fake-provider recovery: users=100 tasks=200 consumers=2 max_parallel_users={} elapsed_ms={} first_completion_ms={} last_completion_ms={}",
        stats.max_parallel_users,
        started.elapsed().as_millis(),
        stats.first_completion_ms.unwrap(),
        stats.last_completion_ms
    );
}
