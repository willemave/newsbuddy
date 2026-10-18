//! Snapshot conflicts retry without consuming the provider failure budget.

use crate::{HandlerExecution, HandlerFinalizerFuture, TaskFinalizer, TaskFinalizerResult};
use newsly_queue::TaskResult;
use serde_json::{Map, Value};
use sqlx::{Postgres, Transaction};

const MAX_CONFLICT_AGE_SECONDS: i64 = 300;

pub(super) fn defer_after_other_wait(task_id: i64, delay_seconds: i64) -> HandlerExecution {
    HandlerExecution::with_finalizer(
        TaskResult::defer(delay_seconds),
        ClearStaleAssignment { task_id },
    )
}

#[derive(Debug)]
struct ClearStaleAssignment {
    task_id: i64,
}

impl TaskFinalizer for ClearStaleAssignment {
    fn apply<'a>(
        &'a self,
        transaction: &'a mut Transaction<'static, Postgres>,
    ) -> HandlerFinalizerFuture<'a> {
        Box::pin(async move {
            // The exact queue lease fences this edit. Preserve mode and every unrelated field.
            sqlx::query("UPDATE processing_tasks SET payload=(COALESCE(payload,'{}'::json)::jsonb - ARRAY['assignment_stale_since','assignment_stale_attempts','assignment_stale_retry_count'])::json WHERE id::bigint=$1")
                .bind(self.task_id).execute(&mut **transaction).await?;
            Ok(TaskFinalizerResult::Keep)
        })
    }
}

pub(super) fn stale_assignment(
    task_id: i64,
    user_id: i64,
    retry_count: i32,
    payload: &Map<String, Value>,
    now: i64,
) -> HandlerExecution {
    // Provider retries and expired-lease reclaim advance the queue generation. A conflict
    // after that progress starts a fresh episode; neutral conflict deferrals keep this marker.
    let same_episode = payload
        .get("assignment_stale_retry_count")
        .and_then(Value::as_i64)
        == Some(i64::from(retry_count));
    let (since, attempts) = if same_episode {
        (
            payload
                .get("assignment_stale_since")
                .and_then(Value::as_i64)
                .unwrap_or(now)
                .min(now),
            payload
                .get("assignment_stale_attempts")
                .and_then(Value::as_u64)
                .unwrap_or(0),
        )
    } else {
        (now, 0)
    };
    let age_seconds = now.saturating_sub(since);
    if age_seconds >= MAX_CONFLICT_AGE_SECONDS {
        tracing::warn!(
            task_id,
            user_id,
            age_seconds,
            code = "briefing_assignment_conflict_exhausted",
            "Briefing assignment conflict recovery exhausted"
        );
        return HandlerExecution::from_result(TaskResult::fail(
            Some(
                "briefing_assignment_conflict_exhausted: snapshots changed for five minutes".into(),
            ),
            false,
        ));
    }
    let delay_seconds = if attempts < 3 { 5 } else { 60 };
    tracing::info!(
        task_id,
        user_id,
        age_seconds,
        delay_seconds,
        code = "briefing_assignment_stale",
        "Briefing assignment will retry with a fresh snapshot"
    );
    HandlerExecution::with_finalizer(
        TaskResult::defer(delay_seconds),
        StaleAssignment {
            task_id,
            retry_count,
            since,
            attempts: attempts.saturating_add(1).min(4),
        },
    )
}

#[derive(Debug)]
struct StaleAssignment {
    task_id: i64,
    retry_count: i32,
    since: i64,
    attempts: u64,
}

impl TaskFinalizer for StaleAssignment {
    fn apply<'a>(
        &'a self,
        transaction: &'a mut Transaction<'static, Postgres>,
    ) -> HandlerFinalizerFuture<'a> {
        Box::pin(async move {
            // The worker holds the exact queue lease; retain the original mode and intent.
            sqlx::query("UPDATE processing_tasks SET payload=(COALESCE(payload,'{}'::json)::jsonb || jsonb_build_object('assignment_stale_since',$2::bigint,'assignment_stale_attempts',$3::bigint,'assignment_stale_retry_count',$4::integer))::json WHERE id::bigint=$1")
                .bind(self.task_id).bind(self.since).bind(i64::try_from(self.attempts).unwrap_or(4)).bind(self.retry_count)
                .execute(&mut **transaction).await?;
            Ok(TaskFinalizerResult::Keep)
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn conflicts_back_off_then_exhaust_without_provider_retries() {
        for (attempts, age, delay) in [
            (0, 0, Some(5)),
            (2, 10, Some(5)),
            (3, 15, Some(60)),
            (4, 299, Some(60)),
            (4, 300, None),
        ] {
            let payload = json!({"mode":"full","assignment_stale_since":1000,"assignment_stale_attempts":attempts,"assignment_stale_retry_count":0});
            let execution = stale_assignment(1, 2, 0, payload.as_object().unwrap(), 1000 + age);
            assert_eq!(execution.task_result.retry_delay_seconds, delay);
            assert_eq!(execution.task_result.deferred, delay.is_some());
            assert!(!execution.task_result.retryable);
        }
    }

    #[test]
    fn provider_retry_or_expired_lease_generation_starts_a_fresh_conflict_episode() {
        let old = json!({"mode":"full","assignment_stale_since":1000,"assignment_stale_attempts":4,"assignment_stale_retry_count":2});
        assert!(
            !stale_assignment(1, 2, 2, old.as_object().unwrap(), 1400)
                .task_result
                .deferred
        );
        for retry_count in [3, 4] {
            let execution = stale_assignment(1, 2, retry_count, old.as_object().unwrap(), 1400);
            assert!(execution.task_result.deferred);
            assert_eq!(execution.task_result.retry_delay_seconds, Some(5));
        }
        let legacy = json!({"assignment_stale_since":1000,"assignment_stale_attempts":4});
        assert!(
            stale_assignment(1, 2, 0, legacy.as_object().unwrap(), 1400)
                .task_result
                .deferred
        );
    }

    #[sqlx::test(migrations = false)]
    async fn fenced_deferral_preserves_full_intent_and_retry_budget(pool: sqlx::PgPool) {
        use newsly_queue::{
            ClaimRequest, ClaimRuntimeScope, EnqueueRequest, QueueKernel, TaskType,
        };
        newsly_db::run_migrations(&pool).await.unwrap();
        let user:i64=sqlx::query_scalar("INSERT INTO users(apple_id,email,is_active,is_admin) VALUES('stale-assignment','stale-assignment@example.com',true,false) RETURNING id::bigint").fetch_one(&pool).await.unwrap();
        let queue = QueueKernel::new(pool.clone());
        let mut request = EnqueueRequest::new(TaskType::BriefingRefresh);
        request.owner_user_id = Some(user);
        request.payload = json!({"user_id":user,"mode":"full"}).as_object().cloned();
        queue.enqueue(request).await.unwrap();
        let scope = ClaimRuntimeScope::namespaces(
            newsly_domain::RuntimeOwner::Rust,
            [newsly_domain::ResourceKey::new("briefing_refresh").unwrap()],
        )
        .unwrap();
        let claim = queue
            .claim(&ClaimRequest::for_queue(
                "stale-assignment",
                newsly_queue::TaskQueue::Llm,
                scope.clone(),
            ))
            .await
            .unwrap()
            .unwrap();
        let mut finalization = queue
            .begin_fenced_finalization(&claim, &TaskResult::defer(5), 3)
            .await
            .unwrap()
            .unwrap();
        StaleAssignment {
            task_id: claim.id,
            retry_count: claim.retry_count,
            since: 1000,
            attempts: 1,
        }
        .apply(finalization.transaction_mut())
        .await
        .unwrap();
        finalization.finish().await.unwrap();
        let (status, retries, payload): (String, i32, Value) = sqlx::query_as(
            "SELECT status,retry_count,payload FROM processing_tasks WHERE id::bigint=$1",
        )
        .bind(claim.id)
        .fetch_one(&pool)
        .await
        .unwrap();
        assert_eq!(status, "pending");
        assert_eq!(retries, 0);
        assert_eq!(payload["mode"], "full");
        assert_eq!(payload["assignment_stale_since"], 1000);
        assert_eq!(payload["assignment_stale_attempts"], 1);
        assert_eq!(payload["assignment_stale_retry_count"], 0);
        assert!(
            queue
                .begin_fenced_finalization(&claim, &TaskResult::ok(), 3)
                .await
                .unwrap()
                .is_none()
        );
        let request =
            ClaimRequest::for_queue("stale-assignment", newsly_queue::TaskQueue::Llm, scope);
        sqlx::query("UPDATE processing_tasks SET available_at=now() WHERE id::bigint=$1")
            .bind(claim.id)
            .execute(&pool)
            .await
            .unwrap();
        let retry = queue.claim(&request).await.unwrap().unwrap();
        queue
            .finalize(
                &retry,
                &TaskResult {
                    retry_delay_seconds: Some(0),
                    ..TaskResult::fail(Some("composition transport error".into()), true)
                },
                3,
            )
            .await
            .unwrap()
            .unwrap();
        let next = queue.claim(&request).await.unwrap().unwrap();
        assert_eq!(next.retry_count, 1);
        let execution = stale_assignment(next.id, user, next.retry_count, &next.payload, 1400);
        assert!(
            execution.task_result.deferred,
            "provider retry starts a fresh conflict episode"
        );
        let mut finalization = queue
            .begin_fenced_finalization(&next, &execution.task_result, 3)
            .await
            .unwrap()
            .unwrap();
        execution
            .finalizer
            .unwrap()
            .apply(finalization.transaction_mut())
            .await
            .unwrap();
        finalization.finish().await.unwrap();
        let payload: Value =
            sqlx::query_scalar("SELECT payload FROM processing_tasks WHERE id::bigint=$1")
                .bind(next.id)
                .fetch_one(&pool)
                .await
                .unwrap();
        assert_eq!(payload["mode"], "full");
        assert_eq!(payload["assignment_stale_since"], 1400);
        assert_eq!(payload["assignment_stale_attempts"], 1);
        assert_eq!(payload["assignment_stale_retry_count"], 1);
    }
    #[sqlx::test(migrations = false)]
    async fn other_waits_clear_conflict_episode_behind_exact_lease_without_losing_full(
        pool: sqlx::PgPool,
    ) {
        use newsly_domain::{ResourceKey, RuntimeOwner};
        use newsly_queue::{
            ClaimRequest, ClaimRuntimeScope, EnqueueRequest, QueueKernel, TaskType,
        };
        newsly_db::run_migrations(&pool).await.unwrap();
        let queue = QueueKernel::new(pool.clone());
        let request = ClaimRequest::for_queue(
            "other-briefing-wait",
            newsly_queue::TaskQueue::Llm,
            ClaimRuntimeScope::namespaces(
                RuntimeOwner::Rust,
                [ResourceKey::new("briefing_refresh").unwrap()],
            )
            .unwrap(),
        );
        for delay in [1, 30] {
            let user: i64 = sqlx::query_scalar("INSERT INTO users(apple_id,email,is_active,is_admin) VALUES('other-wait-'||$1::text,'other-wait-'||$1::text||'@example.com',true,false) RETURNING id::bigint")
                .bind(delay).fetch_one(&pool).await.unwrap();
            let mut enqueue = EnqueueRequest::new(TaskType::BriefingRefresh);
            enqueue.owner_user_id = Some(user);
            enqueue.payload = json!({"user_id":user,"mode":"full","keep":"intent","assignment_stale_since":1000,"assignment_stale_attempts":4,"assignment_stale_retry_count":0}).as_object().cloned();
            queue.enqueue(enqueue).await.unwrap();
            let claim = queue.claim(&request).await.unwrap().unwrap();
            let execution = defer_after_other_wait(claim.id, delay);
            let mut finalization = queue
                .begin_fenced_finalization(&claim, &execution.task_result, 3)
                .await
                .unwrap()
                .unwrap();
            execution
                .finalizer
                .unwrap()
                .apply(finalization.transaction_mut())
                .await
                .unwrap();
            let transition = finalization.finish().await.unwrap().unwrap();
            assert_eq!(transition.retry_count, 0);
            assert_eq!(transition.retry_delay_seconds, Some(delay));
            assert!(transition.deferred);
            let payload: Value =
                sqlx::query_scalar("SELECT payload FROM processing_tasks WHERE id::bigint=$1")
                    .bind(claim.id)
                    .fetch_one(&pool)
                    .await
                    .unwrap();
            assert_eq!(
                payload,
                json!({"user_id":user,"mode":"full","keep":"intent"})
            );
            assert!(
                queue
                    .begin_fenced_finalization(&claim, &TaskResult::ok(), 3)
                    .await
                    .unwrap()
                    .is_none()
            );
            let fresh = stale_assignment(claim.id, user, 0, payload.as_object().unwrap(), 1400);
            assert_eq!(fresh.task_result.retry_delay_seconds, Some(5));
            assert!(fresh.task_result.deferred);
            sqlx::query("DELETE FROM processing_tasks WHERE id::bigint=$1")
                .bind(claim.id)
                .execute(&pool)
                .await
                .unwrap();
        }
    }
}
