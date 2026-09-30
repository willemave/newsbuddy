use chrono::{Duration, Utc};
use newsly_db::news_category_reclustering::NewsCategoryCandidate;
use newsly_db::{
    BeginNewsCategoryRunOutcome, NewsCategoryRunContext, NewsCategoryRunStatus,
    attach_news_category_run_task, begin_news_category_run, reserve_news_category_naming_attempt,
    settle_stale_news_category_runs,
};
use newsly_domain::{ResourceKey, RuntimeOwner};
use newsly_queue::{
    ClaimRequest, ClaimRuntimeScope, ClaimedTask, EnqueueRequest, QueueKernel, TaskQueue,
    TaskResult, TaskType,
};
use serde_json::{Map, Value, json};
use sqlx::PgPool;

use super::finalizer::ReclusteringFinalizer;
use crate::{TaskFinalizer, TaskFinalizerResult};

struct Fixture {
    queue: QueueKernel,
    claim: ClaimedTask,
    context: NewsCategoryRunContext,
}

async fn fixture(pool: &PgPool, suffix: &str) -> Fixture {
    newsly_db::run_migrations(pool).await.unwrap();
    let user_id = sqlx::query_scalar::<_, i64>(
        "INSERT INTO users(apple_id,email,is_active,is_admin) VALUES($1,$2,true,false) RETURNING id::bigint",
    )
    .bind(format!("finalizer-{suffix}"))
    .bind(format!("finalizer-{suffix}@example.com"))
    .fetch_one(pool)
    .await
    .unwrap();
    let now = Utc::now();
    let local_date = now.date_naive();
    sqlx::query(
        r"
        INSERT INTO user_news_category_schedule (
            user_id,timezone,timezone_revision,next_due_at,next_local_date,
            next_window_start_at,next_window_end_at
        ) VALUES ($1::bigint::integer,'UTC',1,$2,$3,$4,$5)
        ",
    )
    .bind(user_id)
    .bind((now + Duration::days(1)).naive_utc())
    .bind(local_date.succ_opt().unwrap())
    .bind((now + Duration::days(1) - Duration::minutes(5)).naive_utc())
    .bind((now + Duration::days(1) + Duration::hours(1)).naive_utc())
    .execute(pool)
    .await
    .unwrap();
    let run_id = sqlx::query_scalar::<_, i64>(
        r"
        INSERT INTO news_category_maintenance_runs (
            user_id,local_date,timezone,timezone_revision,scheduled_at,
            window_start_at,window_end_at,mode,status
        ) VALUES ($1::bigint::integer,$2,'UTC',1,$3,$4,$5,'shadow','scheduled')
        RETURNING id
        ",
    )
    .bind(user_id)
    .bind(local_date)
    .bind((now - Duration::minutes(1)).naive_utc())
    .bind((now - Duration::minutes(5)).naive_utc())
    .bind((now + Duration::hours(1)).naive_utc())
    .fetch_one(pool)
    .await
    .unwrap();
    let mut request = EnqueueRequest::new(TaskType::ReclusterNewsLenses);
    request.owner_user_id = Some(user_id);
    request.dedupe = Some(true);
    request.dedupe_key = Some(format!("finalizer-{suffix}"));
    request.payload = Some(Map::from_iter([
        ("user_id".to_owned(), Value::from(user_id)),
        ("run_id".to_owned(), Value::from(run_id)),
        ("timezone_revision".to_owned(), Value::from(1)),
        ("local_date".to_owned(), Value::from(local_date.to_string())),
        ("timezone".to_owned(), Value::from("UTC")),
        (
            "window_start_at".to_owned(),
            Value::from((now - Duration::minutes(5)).to_rfc3339()),
        ),
        (
            "window_end_at".to_owned(),
            Value::from((now + Duration::hours(1)).to_rfc3339()),
        ),
        ("mode".to_owned(), Value::from("shadow")),
    ]));
    let queue = QueueKernel::new(pool.clone());
    let task_id = queue.enqueue(request).await.unwrap();
    let mut transaction = pool.begin().await.unwrap();
    assert!(
        attach_news_category_run_task(&mut transaction, run_id, task_id)
            .await
            .unwrap()
    );
    transaction.commit().await.unwrap();
    let scope = ClaimRuntimeScope::namespaces(
        RuntimeOwner::Rust,
        [ResourceKey::new("recluster_news_lenses").unwrap()],
    )
    .unwrap();
    let claim = queue
        .claim(&ClaimRequest::for_queue(
            format!("finalizer-{suffix}"),
            TaskQueue::Llm,
            scope,
        ))
        .await
        .unwrap()
        .unwrap();
    let fence = newsly_db::NewsCategoryRunClaimFence {
        locked_by: claim.locked_by.clone(),
        lease_token: claim.lease_token.get(),
        retry_count: claim.retry_count,
        executor_runtime: claim.executor_runtime.to_string(),
        executor_version: claim.executor_version,
        executor_namespace: claim.executor_namespace.clone(),
    };
    let context =
        match begin_news_category_run(pool, task_id, user_id, run_id, 1, &fence, Utc::now())
            .await
            .unwrap()
        {
            BeginNewsCategoryRunOutcome::Ready(context) => context,
            outcome => panic!("expected ready run, got {outcome:?}"),
        };
    Fixture {
        queue,
        claim,
        context,
    }
}

fn candidate() -> NewsCategoryCandidate {
    NewsCategoryCandidate {
        input_hash: "a".repeat(64),
        candidate: json!({"schema_version":1}),
        naming_result: None,
    }
}

async fn apply_and_finish(
    fixture: &Fixture,
    finalizer: &ReclusteringFinalizer,
) -> TaskFinalizerResult {
    let mut finalization = fixture
        .queue
        .begin_fenced_finalization(&fixture.claim, &TaskResult::ok(), 3)
        .await
        .unwrap()
        .unwrap();
    let result = finalizer
        .apply(finalization.transaction_mut())
        .await
        .unwrap();
    finalization.finish().await.unwrap();
    result
}

#[sqlx::test(migrations = false)]
async fn timezone_revision_change_discards_candidate_and_fails_run(pool: PgPool) {
    let fixture = fixture(&pool, "revision").await;
    sqlx::query(
        "UPDATE user_news_category_schedule SET timezone_revision=2 WHERE user_id::bigint=$1",
    )
    .bind(fixture.context.run.user_id)
    .execute(&pool)
    .await
    .unwrap();
    let finalizer = ReclusteringFinalizer {
        context: fixture.context.clone(),
        snapshot: None,
        candidate: Some(candidate()),
        publication: None,
        status: NewsCategoryRunStatus::Shadowed,
        output: json!({"candidate":true}),
    };
    assert_eq!(
        apply_and_finish(&fixture, &finalizer).await,
        TaskFinalizerResult::Keep
    );
    let (status, candidate_exists): (String, bool) = sqlx::query_as(
        "SELECT run.status, EXISTS(SELECT 1 FROM news_category_candidates WHERE user_id=run.user_id) FROM news_category_maintenance_runs AS run WHERE run.id=$1",
    )
    .bind(fixture.context.run.run_id)
    .fetch_one(&pool)
    .await
    .unwrap();
    assert_eq!(status, "failed");
    assert!(!candidate_exists);
}

#[sqlx::test(migrations = false)]
async fn expiration_after_planning_rolls_back_candidate_and_skips_window(pool: PgPool) {
    let fixture = fixture(&pool, "window").await;
    sqlx::query(
        "UPDATE news_category_maintenance_runs SET window_end_at=timezone('UTC',now())-interval '1 second' WHERE id=$1",
    )
    .bind(fixture.context.run.run_id)
    .execute(&pool)
    .await
    .unwrap();
    let finalizer = ReclusteringFinalizer {
        context: fixture.context.clone(),
        snapshot: None,
        candidate: Some(candidate()),
        publication: None,
        status: NewsCategoryRunStatus::Shadowed,
        output: json!({"candidate":true}),
    };
    assert_eq!(
        apply_and_finish(&fixture, &finalizer).await,
        TaskFinalizerResult::Keep
    );
    let (status, candidate_exists): (String, bool) = sqlx::query_as(
        "SELECT run.status, EXISTS(SELECT 1 FROM news_category_candidates WHERE user_id=run.user_id) FROM news_category_maintenance_runs AS run WHERE run.id=$1",
    )
    .bind(fixture.context.run.run_id)
    .fetch_one(&pool)
    .await
    .unwrap();
    assert_eq!(status, "skipped_window");
    assert!(!candidate_exists);
}

#[sqlx::test(migrations = false)]
async fn lost_queue_lease_cannot_finalize_and_terminal_sweep_settles_run(pool: PgPool) {
    let fixture = fixture(&pool, "lease").await;
    sqlx::query(
        "UPDATE processing_tasks SET status='failed',completed_at=timezone('UTC',now()),locked_by=NULL,locked_at=NULL,lease_token=NULL,lease_expires_at=NULL WHERE id=$1",
    )
    .bind(fixture.claim.id)
    .execute(&pool)
    .await
    .unwrap();
    assert!(
        fixture
            .queue
            .begin_fenced_finalization(&fixture.claim, &TaskResult::ok(), 3)
            .await
            .unwrap()
            .is_none()
    );
    let mut transaction = pool.begin().await.unwrap();
    assert_eq!(
        settle_stale_news_category_runs(&mut transaction, Utc::now(), 8)
            .await
            .unwrap(),
        1
    );
    transaction.commit().await.unwrap();
    let status = sqlx::query_scalar::<_, String>(
        "SELECT status FROM news_category_maintenance_runs WHERE id=$1",
    )
    .bind(fixture.context.run.run_id)
    .fetch_one(&pool)
    .await
    .unwrap();
    assert_eq!(status, "failed");
}

#[sqlx::test(migrations = false)]
async fn completed_naming_audit_survives_reclaim_without_a_second_old_claim_reservation(
    pool: PgPool,
) {
    let fixture = fixture(&pool, "naming-reclaim").await;
    assert!(
        reserve_news_category_naming_attempt(&pool, &fixture.context, 100, 1_000)
            .await
            .unwrap()
    );
    let input_hash = newsly_db::news_lens_embeddings::input_hash("test input");
    newsly_db::news_category_reclustering::record_naming_attempt(
        &pool,
        &fixture.context,
        uuid::Uuid::new_v4(),
        &input_hash,
        None,
        None,
    )
    .await
    .unwrap();

    sqlx::query(
        "UPDATE processing_tasks SET locked_by='successor', lease_token=$2, retry_count=retry_count+1 WHERE id=$1",
    )
    .bind(fixture.claim.id)
    .bind(uuid::Uuid::new_v4())
    .execute(&pool)
    .await
    .unwrap();

    assert!(
        !reserve_news_category_naming_attempt(&pool, &fixture.context, 100, 1_000)
            .await
            .unwrap()
    );
    let (attempts, audits): (i32, i64) = sqlx::query_as(
        "SELECT run.naming_attempts, (SELECT count(*)::bigint FROM news_category_naming_attempts WHERE run_id=run.id) FROM news_category_maintenance_runs run WHERE run.id=$1",
    )
    .bind(fixture.context.run.run_id)
    .fetch_one(&pool)
    .await
    .unwrap();
    assert_eq!(attempts, 1);
    assert_eq!(audits, 1);
}
