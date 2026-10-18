use super::*;
use newsly_domain::{ResourceKey, RuntimeOwner};
use newsly_queue::{ClaimRequest, ClaimedTask, EnqueueRequest, QueueKernel, TaskStatus, TaskType};
use serde_json::json;

async fn fixture(pool: &PgPool) -> (i64, i64, QueueKernel, ClaimedTask) {
    let user: i64 = sqlx::query_scalar("INSERT INTO users(apple_id,email,is_active,is_admin) VALUES('grouping-deletion-'||gen_random_uuid(),gen_random_uuid()||'@example.com',false,false) RETURNING id::bigint")
        .fetch_one(pool).await.unwrap();
    let story: i64 = sqlx::query_scalar("INSERT INTO news_items(ingest_key,owner_user_id,visibility_scope,platform,status,raw_metadata,ingested_at,created_at) VALUES('grouping-deletion-'||gen_random_uuid(),$1::bigint::integer,'user','rss','ready','{}',now(),now()) RETURNING id::bigint")
        .bind(user).fetch_one(pool).await.unwrap();
    sqlx::query("INSERT INTO briefing_pending_sources(user_id,source_kind,source_id,enqueued_at) VALUES($1::bigint::integer,'news',$2,now())")
        .bind(user).bind(story).execute(pool).await.unwrap();
    sqlx::query("INSERT INTO news_filter_preferences(user_id,revision,text,enabled,origin) VALUES($1::bigint::integer,1,'Science',true,'settings')")
        .bind(user).execute(pool).await.unwrap();
    sqlx::query("INSERT INTO news_filter_decisions(user_id,news_item_id,revision,input_hash,evaluator_version,choice,no_match_probability,probabilities) VALUES($1::bigint::integer,$2::bigint::integer,1,'fixture-hash','fixture','match',0,'{}')")
        .bind(user).bind(story).execute(pool).await.unwrap();
    sqlx::query(
        "INSERT INTO news_filter_daily_usage(user_id,day) VALUES($1::bigint::integer,CURRENT_DATE)",
    )
    .bind(user)
    .execute(pool)
    .await
    .unwrap();
    sqlx::query("INSERT INTO contents(content_type,url,status,is_aggregate,content_metadata) VALUES('article','https://example.com/grouping-deletion-'||$1::text,'completed',false,$2)")
        .bind(user).bind(json!({"submitted_by_user_id":user,"keep":"shared"})).execute(pool).await.unwrap();
    let queue = QueueKernel::new(pool.clone());
    let mut request = EnqueueRequest::new(TaskType::DeleteUserAccount);
    request.payload = json!({"user_id":user}).as_object().cloned();
    request.dedupe = Some(true);
    queue.enqueue(request).await.unwrap();
    let claim = queue.claim(&claim_request()).await.unwrap().unwrap();
    (user, story, queue, claim)
}

fn claim_request() -> ClaimRequest {
    let mut request = ClaimRequest::for_queue(
        "account-deletion-contention",
        TaskType::DeleteUserAccount.spec().queue,
        newsly_queue::ClaimRuntimeScope::namespaces(
            RuntimeOwner::Rust,
            [ResourceKey::new("delete_user_account").unwrap()],
        )
        .unwrap(),
    );
    request.task_type = Some(TaskType::DeleteUserAccount);
    request
}

async fn assert_product_intact(pool: &PgPool, user: i64, story: i64, task: i64) {
    let exists: bool = sqlx::query_scalar(
        "SELECT EXISTS(SELECT 1 FROM users WHERE id::bigint=$1 AND NOT is_active)",
    )
    .bind(user)
    .fetch_one(pool)
    .await
    .unwrap();
    assert!(exists);
    let pending: i64 = sqlx::query_scalar(
        "SELECT count(*) FROM briefing_pending_sources WHERE user_id::bigint=$1 AND source_id=$2",
    )
    .bind(user)
    .bind(story)
    .fetch_one(pool)
    .await
    .unwrap();
    assert_eq!(
        pending, 1,
        "deletions preceding News trigger were rolled back"
    );
    let filter_rows: i64 = sqlx::query_scalar("SELECT (SELECT count(*) FROM news_filter_preferences WHERE user_id::bigint=$1)+(SELECT count(*) FROM news_filter_decisions WHERE user_id::bigint=$1)+(SELECT count(*) FROM news_filter_daily_usage WHERE user_id::bigint=$1)")
        .bind(user).fetch_one(pool).await.unwrap();
    assert_eq!(
        filter_rows, 3,
        "filter cleanup preceding News was rolled back"
    );
    let private: bool =
        sqlx::query_scalar("SELECT EXISTS(SELECT 1 FROM news_items WHERE id::bigint=$1)")
            .bind(story)
            .fetch_one(pool)
            .await
            .unwrap();
    assert!(private);
    let shared: bool = sqlx::query_scalar("SELECT EXISTS(SELECT 1 FROM contents WHERE content_metadata::jsonb->>'submitted_by_user_id'=$1::text)")
        .bind(user).fetch_one(pool).await.unwrap();
    assert!(shared, "shared metadata scrub was rolled back");
    let payload: Value =
        sqlx::query_scalar("SELECT payload FROM processing_tasks WHERE id::bigint=$1")
            .bind(task)
            .fetch_one(pool)
            .await
            .unwrap();
    assert_eq!(payload["user_id"], user);
}

async fn apply(
    queue: &QueueKernel,
    claim: &ClaimedTask,
    user: i64,
) -> newsly_queue::TaskTransition {
    let mut finalization = queue
        .begin_fenced_finalization(claim, &TaskResult::ok(), 3)
        .await
        .unwrap()
        .unwrap();
    let result = AccountDeletionFinalizer::new(user, claim.id)
        .apply(finalization.transaction_mut())
        .await
        .unwrap();
    if let TaskFinalizerResult::Override(result) = result {
        finalization.replace_result(&result, 3).unwrap();
    }
    finalization.finish().await.unwrap().unwrap()
}

#[sqlx::test(migrations = "../newsly-db/migrations")]
async fn grouping_busy_defers_whole_account_purge_then_completes(pool: PgPool) {
    let (user, story, queue, claim) = fixture(&pool).await;
    let mut guard = pool.begin().await.unwrap();
    assert!(
        newsly_db::news_grouping::try_lock_scope(&mut guard, "user", Some(user))
            .await
            .unwrap()
    );
    let transition = apply(&queue, &claim, user).await;
    assert!(transition.deferred);
    assert_eq!(transition.status, TaskStatus::Pending);
    assert_eq!(transition.retry_count, 0);
    assert_product_intact(&pool, user, story, claim.id).await;
    assert!(
        queue
            .begin_fenced_finalization(&claim, &TaskResult::ok(), 3)
            .await
            .unwrap()
            .is_none(),
        "deferral invalidates the old exact claim"
    );
    let count: i32 = sqlx::query_scalar(
        "SELECT deferral_count FROM news_grouping_deferrals WHERE task_id::bigint=$1",
    )
    .bind(claim.id)
    .fetch_one(&pool)
    .await
    .unwrap();
    assert_eq!(count, 1);
    assert_eq!(
        transition.retry_delay_seconds,
        Some(1 + claim.id.rem_euclid(3))
    );
    guard.rollback().await.unwrap();
    sqlx::query("UPDATE processing_tasks SET available_at=now() WHERE id::bigint=$1")
        .bind(claim.id)
        .execute(&pool)
        .await
        .unwrap();
    let retry = queue.claim(&claim_request()).await.unwrap().unwrap();
    assert_eq!(retry.retry_count, 0);
    assert_eq!(
        apply(&queue, &retry, user).await.status,
        TaskStatus::Completed
    );
    let remaining: i64 = sqlx::query_scalar("SELECT count(*) FROM users WHERE id::bigint=$1")
        .bind(user)
        .fetch_one(&pool)
        .await
        .unwrap();
    assert_eq!(remaining, 0);
    let remaining: i64 = sqlx::query_scalar("SELECT count(*) FROM news_items WHERE id::bigint=$1")
        .bind(story)
        .fetch_one(&pool)
        .await
        .unwrap();
    assert_eq!(remaining, 0);
    let (payload, dedupe): (Value, Option<String>) =
        sqlx::query_as("SELECT payload,dedupe_key FROM processing_tasks WHERE id::bigint=$1")
            .bind(claim.id)
            .fetch_one(&pool)
            .await
            .unwrap();
    assert_eq!(payload, json!({}));
    assert!(dedupe.is_none());
}

#[sqlx::test(migrations = "../newsly-db/migrations")]
async fn account_grouping_budget_exhaustion_is_visible_and_keeps_product_intact(pool: PgPool) {
    for by_age in [false, true] {
        let (user, story, queue, claim) = fixture(&pool).await;
        sqlx::query("INSERT INTO news_grouping_deferrals(task_id,deferral_count,first_deferred_at) VALUES($1::bigint::integer,$2,clock_timestamp()-$3*interval '1 second')")
            .bind(claim.id)
            .bind(if by_age { 1 } else { newsly_db::news_grouping::MAX_DEFERRALS - 1 })
            .bind(if by_age { newsly_db::news_grouping::MAX_CONFLICT_AGE_SECONDS + 1 } else { 0 })
            .execute(&pool).await.unwrap();
        let mut guard = pool.begin().await.unwrap();
        assert!(
            newsly_db::news_grouping::try_lock_scope(&mut guard, "user", Some(user))
                .await
                .unwrap()
        );
        let transition = apply(&queue, &claim, user).await;
        assert_eq!(transition.status, TaskStatus::Failed);
        assert_eq!(transition.retry_count, 0);
        let error: String =
            sqlx::query_scalar("SELECT error_message FROM processing_tasks WHERE id::bigint=$1")
                .bind(claim.id)
                .fetch_one(&pool)
                .await
                .unwrap();
        assert_eq!(error, "news_grouping_budget_exhausted");
        assert_product_intact(&pool, user, story, claim.id).await;
        guard.rollback().await.unwrap();
    }
}

#[derive(Debug, thiserror::Error)]
#[error("outer cleanup failure")]
struct WrappedError(#[source] AccountRepositoryError);

#[sqlx::test(migrations = "../newsly-db/migrations")]
async fn account_error_chain_only_recognizes_named_grouping_contention(pool: PgPool) {
    for (statement, expected) in [
        (
            "DO $$ BEGIN RAISE EXCEPTION 'busy' USING ERRCODE='40001',CONSTRAINT='news_grouping_scope_busy'; END $$",
            true,
        ),
        (
            "DO $$ BEGIN RAISE EXCEPTION 'busy' USING ERRCODE='40001',CONSTRAINT='unrelated_serialization'; END $$",
            false,
        ),
    ] {
        let error = sqlx::query(statement).execute(&pool).await.unwrap_err();
        let wrapped = WrappedError(AccountRepositoryError::Database(error));
        assert_eq!(
            newsly_db::news_grouping::contention_source(&wrapped),
            expected
        );
    }
}

#[sqlx::test(migrations = "../newsly-db/migrations")]
async fn active_work_wait_ends_a_previous_conflict_episode_without_consuming_provider_retry(
    pool: PgPool,
) {
    let (user, story, queue, claim) = fixture(&pool).await;
    let mut tx = pool.begin().await.unwrap();
    newsly_db::news_grouping::record_deferral(&mut tx, claim.id)
        .await
        .unwrap();
    sqlx::query("UPDATE news_grouping_deferrals SET first_deferred_at=clock_timestamp()-interval '2 hours',deferral_count=1200 WHERE task_id::bigint=$1").bind(claim.id).execute(&mut *tx).await.unwrap();
    tx.commit().await.unwrap();
    let mut fence = queue
        .begin_fenced_finalization(&claim, &TaskResult::defer(30), 3)
        .await
        .unwrap()
        .unwrap();
    assert!(matches!(
        AccountDeletionWaitFinalizer { task_id: claim.id }
            .apply(fence.transaction_mut())
            .await
            .unwrap(),
        TaskFinalizerResult::Keep
    ));
    let transition = fence.finish().await.unwrap().unwrap();
    assert_eq!(transition.status, TaskStatus::Pending);
    assert_product_intact(&pool, user, story, claim.id).await;
    let (retries,retained):(i32,i64) = sqlx::query_as("SELECT retry_count,(SELECT count(*) FROM news_grouping_deferrals WHERE task_id::bigint=$1) FROM processing_tasks WHERE id::bigint=$1").bind(claim.id).fetch_one(&pool).await.unwrap();
    assert_eq!((retries, retained), (0, 0));
    let mut tx = pool.begin().await.unwrap();
    let next = newsly_db::news_grouping::record_deferral(&mut tx, claim.id)
        .await
        .unwrap();
    assert_eq!(next.count, 1);
    assert!(!next.exhausted);
}
