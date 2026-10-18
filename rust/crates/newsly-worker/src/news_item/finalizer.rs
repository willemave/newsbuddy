use std::error::Error;

use newsly_queue::{QueueKernel, TaskResult};
use sqlx::{Postgres, Transaction};

use crate::{HandlerFinalizerFuture, TaskFinalizer, TaskFinalizerResult};

use super::model::{EnrichmentFinalizationPlan, NewsApplyOutcome, ProcessFinalizationPlan};
use super::repository::{apply_enrichment, apply_processing};

#[derive(Debug, Clone)]
pub(super) struct EnrichmentFinalizer {
    queue: QueueKernel,
    plan: EnrichmentFinalizationPlan,
}

impl EnrichmentFinalizer {
    pub(super) const fn new(queue: QueueKernel, plan: EnrichmentFinalizationPlan) -> Self {
        Self { queue, plan }
    }
}

impl TaskFinalizer for EnrichmentFinalizer {
    fn apply<'a>(
        &'a self,
        transaction: &'a mut Transaction<'static, Postgres>,
    ) -> HandlerFinalizerFuture<'a> {
        Box::pin(async move {
            let outcome = apply_enrichment(transaction, &self.queue, &self.plan).await?;
            let result = bounded_grouping_result(
                transaction,
                self.plan.task_id,
                self.plan.snapshot.id,
                outcome,
            )
            .await?;
            Ok::<_, Box<dyn Error + Send + Sync>>(result)
        })
    }
}

#[derive(Debug, Clone)]
pub(super) struct ProcessNewsFinalizer {
    queue: QueueKernel,
    plan: ProcessFinalizationPlan,
}

impl ProcessNewsFinalizer {
    pub(super) const fn new(queue: QueueKernel, plan: ProcessFinalizationPlan) -> Self {
        Self { queue, plan }
    }
}

impl TaskFinalizer for ProcessNewsFinalizer {
    fn apply<'a>(
        &'a self,
        transaction: &'a mut Transaction<'static, Postgres>,
    ) -> HandlerFinalizerFuture<'a> {
        Box::pin(async move {
            let started = std::time::Instant::now();
            let outcome = apply_processing(transaction, &self.queue, &self.plan).await?;
            tracing::info!(
                task_id = self.plan.task_id,
                news_item_id = self.plan.snapshot.id,
                elapsed_ms = started.elapsed().as_millis(),
                ?outcome,
                stage = "news_grouping_finalize",
                "News grouping transaction completed"
            );
            let result = bounded_grouping_result(
                transaction,
                self.plan.task_id,
                self.plan.snapshot.id,
                outcome,
            )
            .await?;
            Ok::<_, Box<dyn Error + Send + Sync>>(result)
        })
    }
}

async fn bounded_grouping_result(
    transaction: &mut Transaction<'static, Postgres>,
    task_id: i64,
    news_item_id: i64,
    outcome: NewsApplyOutcome,
) -> Result<TaskFinalizerResult, sqlx::Error> {
    if outcome != NewsApplyOutcome::CandidateChanged {
        newsly_db::news_grouping::clear_deferral(transaction, task_id).await?;
        return Ok(finalizer_result(outcome));
    }
    let deferral = newsly_db::news_grouping::record_deferral(transaction, task_id).await?;
    let count = deferral.count;
    let age = deferral.age_seconds;
    if deferral.exhausted {
        tracing::error!(
            task_id,
            news_item_id,
            count,
            age_seconds = age,
            code = "news_grouping_budget_exhausted",
            "News grouping recovery budget exhausted"
        );
        Ok(TaskFinalizerResult::Override(TaskResult::fail(
            Some("news_grouping_budget_exhausted".into()),
            false,
        )))
    } else {
        tracing::info!(
            task_id,
            news_item_id,
            count,
            age_seconds = age,
            code = "news_grouping_prepare_or_guard_wait",
            "deferring News grouping without consuming provider retries"
        );
        Ok(TaskFinalizerResult::Override(TaskResult::defer(
            deferral.delay_seconds,
        )))
    }
}

fn finalizer_result(outcome: NewsApplyOutcome) -> TaskFinalizerResult {
    match outcome {
        NewsApplyOutcome::Applied => TaskFinalizerResult::Keep,
        NewsApplyOutcome::NewsItemMissing => TaskFinalizerResult::Override(TaskResult::fail(
            Some("news item disappeared before finalization".to_owned()),
            false,
        )),
        NewsApplyOutcome::SourceChanged
        | NewsApplyOutcome::CandidateChanged
        | NewsApplyOutcome::DemandChanged => TaskFinalizerResult::Override(TaskResult::defer(1)),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[sqlx::test(migrations = false)]
    async fn grouping_deferrals_preserve_provider_retries_and_settle_exhaustion(
        pool: sqlx::PgPool,
    ) {
        newsly_db::run_migrations(&pool).await.unwrap();
        let queue = QueueKernel::new(pool.clone());
        let mut request =
            newsly_queue::EnqueueRequest::new(newsly_queue::TaskType::ProcessNewsItem);
        request.payload = serde_json::json!({"news_item_id":1}).as_object().cloned();
        let task = queue.enqueue(request).await.unwrap();
        let mut tx = pool.begin().await.unwrap();
        let TaskFinalizerResult::Override(result) =
            bounded_grouping_result(&mut tx, task, 1, NewsApplyOutcome::CandidateChanged)
                .await
                .unwrap()
        else {
            panic!("expected defer")
        };
        assert!(result.deferred);
        assert!(!result.retryable);
        tx.commit().await.unwrap();
        let retries: i32 =
            sqlx::query_scalar("SELECT retry_count FROM processing_tasks WHERE id::bigint=$1")
                .bind(task)
                .fetch_one(&pool)
                .await
                .unwrap();
        assert_eq!(retries, 0);
        sqlx::query(
            "UPDATE news_grouping_deferrals SET deferral_count=$2 WHERE task_id::bigint=$1",
        )
        .bind(task)
        .bind(newsly_db::news_grouping::MAX_DEFERRALS - 1)
        .execute(&pool)
        .await
        .unwrap();
        let mut tx = pool.begin().await.unwrap();
        let TaskFinalizerResult::Override(result) =
            bounded_grouping_result(&mut tx, task, 1, NewsApplyOutcome::CandidateChanged)
                .await
                .unwrap()
        else {
            panic!("expected failure")
        };
        assert!(!result.deferred);
        assert!(!result.retryable);
        assert_eq!(
            result.error_message.as_deref(),
            Some("news_grouping_budget_exhausted")
        );
    }
}
