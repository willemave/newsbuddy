use crate::{HandlerFinalizerFuture, TaskFinalizer, TaskFinalizerResult};
use chrono::Utc;
use newsly_db::news_category_reclustering::{
    self as repository, NewsCategoryCandidate, NewsCategoryPublicationLens, NewsCategorySnapshot,
};
use newsly_db::{NewsCategoryRunContext, NewsCategoryRunStatus, finish_news_category_run};
use newsly_queue::TaskResult;
use serde_json::Value;
use sqlx::{Postgres, Transaction};

#[derive(Debug)]
pub(super) struct ReclusteringFinalizer {
    pub context: NewsCategoryRunContext,
    pub snapshot: Option<NewsCategorySnapshot>,
    pub candidate: Option<NewsCategoryCandidate>,
    pub publication: Option<Vec<NewsCategoryPublicationLens>>,
    pub status: NewsCategoryRunStatus,
    pub output: Value,
}
impl TaskFinalizer for ReclusteringFinalizer {
    fn apply<'a>(
        &'a self,
        tx: &'a mut Transaction<'static, Postgres>,
    ) -> HandlerFinalizerFuture<'a> {
        Box::pin(async move {
            // QueueKernel already locked the exact claimed task/lease. Owner first
            // keeps timezone/profile mutations ordered with routing publication.
            sqlx::query_scalar::<_, i64>(
                "SELECT id::bigint FROM users WHERE id::bigint=$1 FOR UPDATE",
            )
            .bind(self.context.run.user_id)
            .fetch_optional(&mut **tx)
            .await?;
            if Utc::now() >= self.context.run.window_end_at {
                finish_news_category_run(
                    tx,
                    self.context.run.run_id,
                    self.context.run.timezone_revision,
                    NewsCategoryRunStatus::SkippedWindow,
                    &self.output,
                    Utc::now(),
                )
                .await?;
                return Ok(TaskFinalizerResult::Keep);
            }
            if let Some(snapshot) = &self.snapshot
                && !repository::snapshot_is_current(tx, snapshot).await?
            {
                return Ok(TaskFinalizerResult::Override(TaskResult::defer(30)));
            }
            sqlx::query("SAVEPOINT nightly_category_product_write")
                .execute(&mut **tx)
                .await?;
            if let (Some(snapshot), Some(lenses)) = (&self.snapshot, &self.publication) {
                repository::apply_partition(tx, snapshot, lenses).await?;
            }
            if let Some(candidate) = &self.candidate {
                repository::save_candidate(tx, self.context.run.user_id, candidate).await?;
            }
            let finalized_at = Utc::now();
            let finished = finish_news_category_run(
                tx,
                self.context.run.run_id,
                self.context.run.timezone_revision,
                self.status,
                &self.output,
                finalized_at,
            )
            .await?;
            if !finished {
                sqlx::query("ROLLBACK TO SAVEPOINT nightly_category_product_write")
                    .execute(&mut **tx)
                    .await?;
                sqlx::query("RELEASE SAVEPOINT nightly_category_product_write")
                    .execute(&mut **tx)
                    .await?;
                // Persist the reason/window outcome after discarding every candidate or routing
                // mutation made under the stale fence.
                finish_news_category_run(
                    tx,
                    self.context.run.run_id,
                    self.context.run.timezone_revision,
                    self.status,
                    &self.output,
                    finalized_at,
                )
                .await?;
                return Ok(TaskFinalizerResult::Keep);
            }
            sqlx::query("RELEASE SAVEPOINT nightly_category_product_write")
                .execute(&mut **tx)
                .await?;
            tracing::info!(run_id=self.context.run.run_id,user_id=self.context.run.user_id,
                status=?self.status,"nightly category maintenance finalized");
            Ok(TaskFinalizerResult::Keep)
        })
    }
}
