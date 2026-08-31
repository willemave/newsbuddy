use std::error::Error;

use newsly_queue::{QueueKernel, TaskResult};
use sqlx::{Postgres, Transaction};

use crate::{HandlerFinalizerFuture, TaskFinalizer, TaskFinalizerResult};

use super::fanout::enqueue_summary_followups;
use super::model::{
    SummarizationApplyOutcome, SummarizationFinalizationPlan, SummarizationMutation,
};
use super::repository::apply_summarization_state;

#[derive(Debug, Clone)]
pub(super) struct SummarizationFinalizer {
    queue: QueueKernel,
    plan: SummarizationFinalizationPlan,
    briefing_debounce_seconds: i64,
    briefing_batch_minimum: i64,
}

impl SummarizationFinalizer {
    pub(super) const fn new(
        queue: QueueKernel,
        plan: SummarizationFinalizationPlan,
        briefing_debounce_seconds: i64,
        briefing_batch_minimum: i64,
    ) -> Self {
        Self {
            queue,
            plan,
            briefing_debounce_seconds,
            briefing_batch_minimum,
        }
    }

    async fn apply_inner(
        &self,
        transaction: &mut Transaction<'static, Postgres>,
    ) -> Result<TaskFinalizerResult, Box<dyn Error + Send + Sync>> {
        match apply_summarization_state(transaction, &self.plan).await? {
            SummarizationApplyOutcome::Applied(applied) => {
                if matches!(
                    self.plan.mutation,
                    SummarizationMutation::Complete { .. } | SummarizationMutation::Unchanged
                ) {
                    enqueue_summary_followups(
                        transaction,
                        &self.queue,
                        &applied,
                        self.briefing_debounce_seconds,
                        self.briefing_batch_minimum,
                    )
                    .await?;
                }
                Ok(TaskFinalizerResult::Keep)
            }
            SummarizationApplyOutcome::ContentMissing => Ok(TaskFinalizerResult::Keep),
            SummarizationApplyOutcome::SourceChanged => {
                Ok(TaskFinalizerResult::Override(TaskResult::fail(
                    Some("summarization input changed before finalization".to_owned()),
                    true,
                )))
            }
        }
    }
}

impl TaskFinalizer for SummarizationFinalizer {
    fn apply<'a>(
        &'a self,
        transaction: &'a mut Transaction<'static, Postgres>,
    ) -> HandlerFinalizerFuture<'a> {
        Box::pin(async move { self.apply_inner(transaction).await })
    }
}
