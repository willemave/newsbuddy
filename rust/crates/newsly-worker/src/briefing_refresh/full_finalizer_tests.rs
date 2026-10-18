use super::*;
use newsly_db::{BriefingRefreshMode, PreparedBriefingRefresh};
use newsly_queue::{ClaimRequest, ClaimRuntimeScope, TaskQueue, TaskResult};
use serde_json::json;

#[sqlx::test(migrations = false)]
async fn stale_full_finalization_retains_intent_and_exhausts_neutral_conflict_budget(
    pool: sqlx::PgPool,
) {
    newsly_db::run_migrations(&pool).await.unwrap();
    let user_id: i64 = sqlx::query_scalar("INSERT INTO users(apple_id,email,is_active,is_admin) VALUES('stale-full','stale-full@example.com',true,false) RETURNING id::bigint")
        .fetch_one(&pool).await.unwrap();
    let queue = QueueKernel::new(pool.clone());
    let mut enqueue = EnqueueRequest::new(TaskType::BriefingRefresh);
    enqueue.owner_user_id = Some(user_id);
    enqueue.payload = json!({"user_id":user_id,"mode":"full","keep":"intent"})
        .as_object()
        .cloned();
    let task_id = queue.enqueue(enqueue).await.unwrap();
    let request = ClaimRequest::for_queue(
        "stale-full",
        TaskQueue::Llm,
        ClaimRuntimeScope::namespaces(
            newsly_domain::RuntimeOwner::Rust,
            [newsly_domain::ResourceKey::new("briefing_refresh").unwrap()],
        )
        .unwrap(),
    );
    for exhausted in [false, true] {
        let claim = queue.claim(&request).await.unwrap().unwrap();
        let publication = BriefingRefreshPublication {
            prepared: PreparedBriefingRefresh {
                task_id,
                user_id,
                mode: BriefingRefreshMode::Full,
                starting_version: -1,
                prepared_state_changed: false,
                append_batches: vec![],
                compaction_batches: vec![],
            },
            append_segments: vec![],
            compactions: vec![],
            embedding_usage: vec![],
            finalized_at: Utc::now(),
        };
        let finalizer = BriefingRefreshFinalizer::new(
            queue.clone(),
            publication,
            crate::briefing_refresh::BriefingRefreshWorkerConfig::from_env()
                .unwrap()
                .repository,
            0,
            false,
            false,
        );
        let mut finalization = queue
            .begin_fenced_finalization(&claim, &TaskResult::ok(), 3)
            .await
            .unwrap()
            .unwrap();
        let TaskFinalizerResult::Override(result) = finalizer
            .apply(finalization.transaction_mut())
            .await
            .unwrap()
        else {
            panic!("stale Full must override successful completion");
        };
        assert_eq!(result.deferred, !exhausted);
        assert!(!result.retryable);
        finalization.replace_result(&result, 3).unwrap();
        let transition = finalization.finish().await.unwrap().unwrap();
        assert_eq!(transition.retry_count, 0);
        let (status, payload): (String, Value) =
            sqlx::query_as("SELECT status,payload FROM processing_tasks WHERE id::bigint=$1")
                .bind(task_id)
                .fetch_one(&pool)
                .await
                .unwrap();
        assert_eq!(status, if exhausted { "failed" } else { "pending" });
        assert_eq!(payload["mode"], "full");
        assert_eq!(payload["keep"], "intent");
        let count: i64 = sqlx::query_scalar(
            "SELECT count(*) FROM processing_tasks WHERE owner_user_id::bigint=$1",
        )
        .bind(user_id)
        .fetch_one(&pool)
        .await
        .unwrap();
        assert_eq!(count, 1, "stale Full cannot silently become a sweep");
        assert!(
            queue
                .begin_fenced_finalization(&claim, &TaskResult::ok(), 3)
                .await
                .unwrap()
                .is_none()
        );
        if !exhausted {
            assert_eq!(result.retry_delay_seconds, Some(5));
            sqlx::query("UPDATE processing_tasks SET available_at=now(),payload=(payload::jsonb || jsonb_build_object('assignment_stale_since',$2::bigint))::json WHERE id::bigint=$1")
                .bind(task_id).bind(Utc::now().timestamp()-301).execute(&pool).await.unwrap();
        }
    }
}
