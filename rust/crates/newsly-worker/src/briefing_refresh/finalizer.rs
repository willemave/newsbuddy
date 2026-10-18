use std::error::Error;

use chrono::{Duration, Utc};
use newsly_db::{
    BriefingRefreshConfig, BriefingRefreshPublication, BriefingRefreshRepositoryError,
    apply_briefing_refresh,
};
use newsly_queue::{EnqueueRequest, QueueKernel, TaskType};
use serde_json::{Map, Value};
use sqlx::{Postgres, Transaction};

use crate::{
    FinalizerErrorDisposition, HandlerFinalizerFuture, TaskFinalizer, TaskFinalizerResult,
};

#[cfg(test)]
#[path = "full_finalizer_tests.rs"]
mod full_finalizer_tests;

#[derive(Debug, Clone)]
pub(super) struct BriefingRefreshFinalizer {
    queue: QueueKernel,
    publication: BriefingRefreshPublication,
    config: BriefingRefreshConfig,
    stale_recoveries: u64,
    waiting_for_news: bool,
    waiting_for_embeddings: bool,
}

impl BriefingRefreshFinalizer {
    pub(super) const fn new(
        queue: QueueKernel,
        publication: BriefingRefreshPublication,
        config: BriefingRefreshConfig,
        stale_recoveries: u64,
        waiting_for_news: bool,
        waiting_for_embeddings: bool,
    ) -> Self {
        Self {
            queue,
            publication,
            config,
            stale_recoveries,
            waiting_for_news,
            waiting_for_embeddings,
        }
    }

    async fn apply_inner(
        &self,
        transaction: &mut Transaction<'static, Postgres>,
    ) -> Result<TaskFinalizerResult, Box<dyn Error + Send + Sync>> {
        let outcome = apply_briefing_refresh(transaction, &self.publication, &self.config).await?;
        let user_id = self.publication.prepared.user_id;
        if outcome.stale && self.publication.prepared.mode == newsly_db::BriefingRefreshMode::Full {
            // Retry the original Full intent under this exact lease, with the same
            // bounded neutral-conflict budget used by assignment preparation.
            let (retry_count, payload): (i32, Value) = sqlx::query_as(
                "SELECT retry_count,COALESCE(payload,'{}'::json) FROM processing_tasks WHERE id::bigint=$1",
            ).bind(self.publication.prepared.task_id).fetch_one(&mut **transaction).await?;
            let execution = super::stale_assignment::stale_assignment(
                self.publication.prepared.task_id,
                user_id,
                retry_count,
                payload.as_object().unwrap_or(&Map::new()),
                Utc::now().timestamp(),
            );
            if let Some(finalizer) = execution.finalizer {
                finalizer.apply(transaction).await?;
            }
            return Ok(TaskFinalizerResult::Override(execution.task_result));
        }
        let initial: bool = sqlx::query_scalar("SELECT EXISTS(SELECT 1 FROM onboarding_first_edition_runs WHERE user_id::bigint=$1 AND status='active' AND news_seeded AND NOT news_seed_settled)")
            .bind(user_id).fetch_one(&mut **transaction).await?;
        let delay = next_sweep_delay(
            initial,
            outcome.stale,
            self.stale_recoveries,
            outcome.appended_segments,
            self.waiting_for_news,
            outcome.next_sweep_delay_seconds,
            self.config.sweep_seconds,
        );
        let mut request = sweep_request(user_id, delay);
        if self.waiting_for_embeddings {
            request
                .payload
                .as_mut()
                .expect("sweep payload")
                .insert("waiting_for_news_embeddings".to_owned(), Value::Bool(true));
        }
        if initial {
            request.priority = 10;
        }
        if outcome.stale
            && let Some(payload) = &mut request.payload
        {
            payload.insert(
                "stale_recoveries".into(),
                Value::from(self.stale_recoveries.saturating_add(1).min(3)),
            );
        }
        enqueue_next_sweep(
            &self.queue,
            transaction,
            self.publication.prepared.task_id,
            request,
            self.stale_recoveries,
            outcome.stale,
        )
        .await?;
        tracing::info!(
            task_id = self.publication.prepared.task_id,
            user_id,
            mode = self.publication.prepared.mode.as_str(),
            version = outcome.version,
            appended_segments = outcome.appended_segments,
            compacted_segments = outcome.compacted_segments,
            retired_segments = outcome.retired_segments,
            stale = outcome.stale,
            next_sweep_delay_seconds = outcome.next_sweep_delay_seconds,
            scheduled_sweep_delay_seconds = delay,
            "Briefing refresh finalized behind the exact queue lease"
        );
        Ok(TaskFinalizerResult::Keep)
    }
}

impl TaskFinalizer for BriefingRefreshFinalizer {
    fn apply<'a>(
        &'a self,
        transaction: &'a mut Transaction<'static, Postgres>,
    ) -> HandlerFinalizerFuture<'a> {
        Box::pin(async move { self.apply_inner(transaction).await })
    }

    fn error_disposition(
        &self,
        source: &(dyn Error + Send + Sync + 'static),
    ) -> FinalizerErrorDisposition {
        let Some(BriefingRefreshRepositoryError::Sqlx(sqlx::Error::Database(database))) =
            source.downcast_ref::<BriefingRefreshRepositoryError>()
        else {
            return FinalizerErrorDisposition::Retryable;
        };
        if terminal_sqlstate(database.code().as_deref()) {
            FinalizerErrorDisposition::Terminal
        } else {
            FinalizerErrorDisposition::Retryable
        }
    }
}

fn next_sweep_delay(
    initial: bool,
    stale: bool,
    stale_recoveries: u64,
    appended_segments: usize,
    waiting_for_news: bool,
    repository_delay: i64,
    sweep_seconds: i64,
) -> i64 {
    let recover = stale && stale_recoveries < 3;
    // A productive first-edition sweep can advance other prepared categories
    // while embeddings catch up. An unproductive pass still backs off.
    if initial && !stale && appended_segments > 0 {
        repository_delay.min(5)
    } else if waiting_for_news && !stale {
        // Vector completion normally wakes this task. A short recovery poll also
        // covers completion just before the successor becomes visible.
        5
    } else if stale && !recover && initial {
        60
    } else if recover {
        repository_delay.min(5)
    } else if initial && !stale {
        repository_delay.clamp(30, 60)
    } else {
        repository_delay.max(if stale { sweep_seconds } else { 0 })
    }
}

fn terminal_sqlstate(code: Option<&str>) -> bool {
    code.is_some_and(|code| code.starts_with("22") || code.starts_with("23"))
}

async fn enqueue_next_sweep(
    queue: &QueueKernel,
    transaction: &mut Transaction<'static, Postgres>,
    current_task_id: i64,
    request: EnqueueRequest,
    previous_recoveries: u64,
    stale: bool,
) -> Result<(), newsly_queue::QueueError> {
    // The exact claim is already locked. Release its key so a successful sweep can
    // atomically create its successor instead of deduplicating against itself.
    sqlx::query(
        "UPDATE processing_tasks SET dedupe_key=NULL WHERE id::bigint=$1 AND dedupe_key=$2",
    )
    .bind(current_task_id)
    .bind(&request.dedupe_key)
    .execute(&mut **transaction)
    .await?;
    advance_pending_sweep(transaction, &request, previous_recoveries, stale).await?;
    queue
        .enqueue_many_in_transaction(transaction, vec![request])
        .await?;
    Ok(())
}

async fn advance_pending_sweep(
    transaction: &mut Transaction<'static, Postgres>,
    request: &EnqueueRequest,
    previous_recoveries: u64,
    stale: bool,
) -> Result<(), sqlx::Error> {
    let recoveries = if stale {
        previous_recoveries.saturating_add(1).min(3)
    } else {
        0
    };
    // A coalesced future sweep must not swallow this earlier recovery obligation.
    sqlx::query("UPDATE processing_tasks SET available_at=CASE WHEN retry_count=0 THEN LEAST(available_at,$2) ELSE available_at END, payload=(COALESCE(payload,'{}'::json)::jsonb || jsonb_build_object('stale_recoveries',$4::bigint,'waiting_for_news_embeddings',COALESCE((payload->>'waiting_for_news_embeddings')::boolean,false) OR $5))::json WHERE owner_user_id::bigint=$1 AND task_type='briefing_refresh' AND status='pending' AND dedupe_key=$3")
        .bind(request.owner_user_id)
        .bind(request.available_at.map(|value| value.naive_utc()))
        .bind(&request.dedupe_key)
        .bind(i64::try_from(recoveries).unwrap_or(3))
        .bind(request.payload.as_ref().and_then(|p| p.get("waiting_for_news_embeddings")).and_then(Value::as_bool).unwrap_or(false))
        .execute(&mut **transaction).await?;
    Ok(())
}

fn sweep_request(user_id: i64, delay_seconds: i64) -> EnqueueRequest {
    let mut request = EnqueueRequest::new(TaskType::BriefingRefresh);
    request.payload = Some(Map::from_iter([
        ("user_id".to_owned(), Value::from(user_id)),
        ("mode".to_owned(), Value::from("sweep")),
    ]));
    request.owner_user_id = Some(user_id);
    request.dedupe = Some(true);
    request.dedupe_key = Some(format!("briefing_refresh:{user_id}:sweep"));
    request.available_at = Some(Utc::now() + Duration::seconds(delay_seconds.clamp(0, 86_400)));
    request
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn productive_first_edition_advances_without_losing_retry_backoff() {
        let cases = [
            (true, false, 0, true, true, 0, 0),
            (true, false, 0, true, true, 20, 5),
            (true, false, 0, false, true, 0, 5),
            (false, false, 0, true, true, 0, 5),
            (true, true, 2, true, true, 20, 5),
            (true, true, 3, true, true, 20, 60),
            (false, true, 3, true, false, 10, 120),
            (true, false, 0, false, false, 10, 30),
            (true, false, 0, false, false, 90, 60),
            (false, true, 2, false, true, 20, 5),
            (false, false, 0, true, false, 7, 7),
        ];
        for (initial, stale, recoveries, appended, waiting, delay, expected) in cases {
            assert_eq!(
                next_sweep_delay(
                    initial,
                    stale,
                    recoveries,
                    usize::from(appended),
                    waiting,
                    delay,
                    120
                ),
                expected,
                "initial={initial} stale={stale} recoveries={recoveries} appended={appended} waiting={waiting} delay={delay}"
            );
        }
    }

    #[test]
    fn deterministic_sql_data_and_constraint_errors_are_terminal() {
        assert!(terminal_sqlstate(Some("22001")));
        assert!(terminal_sqlstate(Some("23505")));
        assert!(!terminal_sqlstate(Some("40001")));
        assert!(!terminal_sqlstate(None));
    }

    #[sqlx::test(migrations = false)]
    async fn stale_recovery_advances_a_coalesced_sweep_and_caps_the_budget(pool: sqlx::PgPool) {
        newsly_db::run_migrations(&pool).await.unwrap();
        let user_id: i64 = sqlx::query_scalar("INSERT INTO users(apple_id,email,is_active,is_admin) VALUES ('sweep-test','sweep@example.com',true,false) RETURNING id::bigint").fetch_one(&pool).await.unwrap();
        let queue = QueueKernel::new(pool.clone());
        let task_id = queue.enqueue(sweep_request(user_id, 600)).await.unwrap();
        let request = sweep_request(user_id, 5);
        let mut tx = pool.begin().await.unwrap();
        advance_pending_sweep(&mut tx, &request, u64::MAX, true)
            .await
            .unwrap();
        tx.commit().await.unwrap();
        let (available, recoveries): (chrono::NaiveDateTime, i64) = sqlx::query_as("SELECT available_at, (payload->>'stale_recoveries')::bigint FROM processing_tasks WHERE id=$1").bind(task_id).fetch_one(&pool).await.unwrap();
        assert!(
            (available - request.available_at.unwrap().naive_utc())
                .num_microseconds()
                .unwrap()
                .abs()
                <= 1
        );
        assert_eq!(recoveries, 3);
        assert_eq!(queue.enqueue(request).await.unwrap(), task_id);
    }

    #[sqlx::test(migrations = false)]
    async fn a_running_sweep_creates_a_durable_successor(pool: sqlx::PgPool) {
        newsly_db::run_migrations(&pool).await.unwrap();
        let user_id:i64=sqlx::query_scalar("INSERT INTO users(apple_id,email,is_active,is_admin) VALUES ('successor','successor@example.com',true,false) RETURNING id::bigint").fetch_one(&pool).await.unwrap();
        let queue = QueueKernel::new(pool.clone());
        let current = queue.enqueue(sweep_request(user_id, 0)).await.unwrap();
        let scope = newsly_queue::ClaimRuntimeScope::namespaces(
            newsly_domain::RuntimeOwner::Rust,
            [newsly_domain::ResourceKey::new("briefing_refresh").unwrap()],
        )
        .unwrap();
        let claim = queue
            .claim(&newsly_queue::ClaimRequest::for_queue(
                "successor-test",
                newsly_queue::TaskQueue::Llm,
                scope,
            ))
            .await
            .unwrap()
            .unwrap();
        let mut finalization = queue
            .begin_fenced_finalization(&claim, &newsly_queue::TaskResult::ok(), 3)
            .await
            .unwrap()
            .unwrap();
        enqueue_next_sweep(
            &queue,
            finalization.transaction_mut(),
            current,
            sweep_request(user_id, 30),
            0,
            false,
        )
        .await
        .unwrap();
        finalization.finish().await.unwrap();
        let next: i64 = sqlx::query_scalar(
            "SELECT id::bigint FROM processing_tasks WHERE dedupe_key=$1 AND status='pending'",
        )
        .bind(format!("briefing_refresh:{user_id}:sweep"))
        .fetch_one(&pool)
        .await
        .unwrap();
        assert_ne!(current, next);
    }

    #[sqlx::test(migrations = false)]
    async fn readiness_marker_survives_coalescing_without_shortening_provider_backoff(
        pool: sqlx::PgPool,
    ) {
        newsly_db::run_migrations(&pool).await.unwrap();
        let user_id:i64=sqlx::query_scalar("INSERT INTO users(apple_id,email,is_active,is_admin) VALUES ('readiness-sweep','readiness-sweep@example.com',true,false) RETURNING id::bigint").fetch_one(&pool).await.unwrap();
        let queue = QueueKernel::new(pool.clone());
        let task_id = queue.enqueue(sweep_request(user_id, 600)).await.unwrap();
        sqlx::query("UPDATE processing_tasks SET retry_count=1 WHERE id::bigint=$1")
            .bind(task_id)
            .execute(&pool)
            .await
            .unwrap();
        let before: chrono::NaiveDateTime =
            sqlx::query_scalar("SELECT available_at FROM processing_tasks WHERE id::bigint=$1")
                .bind(task_id)
                .fetch_one(&pool)
                .await
                .unwrap();
        let mut request = sweep_request(user_id, 5);
        request
            .payload
            .as_mut()
            .unwrap()
            .insert("waiting_for_news_embeddings".into(), Value::Bool(true));
        let mut tx = pool.begin().await.unwrap();
        advance_pending_sweep(&mut tx, &request, 0, false)
            .await
            .unwrap();
        advance_pending_sweep(&mut tx, &sweep_request(user_id, 0), 0, false)
            .await
            .unwrap();
        tx.commit().await.unwrap();
        let (after,marker):(chrono::NaiveDateTime,bool)=sqlx::query_as("SELECT available_at,(payload->>'waiting_for_news_embeddings')::boolean FROM processing_tasks WHERE id::bigint=$1").bind(task_id).fetch_one(&pool).await.unwrap();
        assert_eq!(after, before);
        assert!(marker);
    }

    #[test]
    fn sweep_request_is_owned_and_deduped() {
        let request = sweep_request(42, 90);
        assert_eq!(request.task_type, TaskType::BriefingRefresh);
        assert_eq!(request.owner_user_id, Some(42));
        assert_eq!(
            request.dedupe_key.as_deref(),
            Some("briefing_refresh:42:sweep")
        );
        assert_eq!(
            request
                .payload
                .as_ref()
                .and_then(|payload| payload.get("mode"))
                .and_then(Value::as_str),
            Some("sweep")
        );
    }
}
