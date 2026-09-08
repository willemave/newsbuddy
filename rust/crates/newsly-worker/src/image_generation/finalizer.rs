use std::error::Error;

use newsly_queue::{QueueKernel, TaskResult};
use sqlx::{Postgres, Transaction};
use thiserror::Error;

use crate::summarization::fanout::{SummarizationFanoutError, enqueue_briefing_followups};
use crate::{HandlerAfterCommitFuture, HandlerFinalizerFuture, TaskFinalizer, TaskFinalizerResult};

use super::model::{ImageFinalizationPlan, ImageTargetOutcome};
use super::repository::{ImageRepositoryError, apply_generated_image};
use super::storage::ImageFileStoreError;

#[derive(Debug)]
pub(super) struct ImageFinalizer {
    plan: ImageFinalizationPlan,
    queue: QueueKernel,
    briefing_debounce_seconds: i64,
    briefing_batch_minimum: i64,
}

impl ImageFinalizer {
    pub(super) const fn new(
        plan: ImageFinalizationPlan,
        queue: QueueKernel,
        briefing_debounce_seconds: i64,
        briefing_batch_minimum: i64,
    ) -> Self {
        Self {
            plan,
            queue,
            briefing_debounce_seconds,
            briefing_batch_minimum,
        }
    }

    async fn apply_inner(
        &self,
        transaction: &mut Transaction<'static, Postgres>,
    ) -> Result<TaskFinalizerResult, ImageFinalizeError> {
        let outcome = apply_generated_image(transaction, &self.plan).await?;
        match outcome {
            ImageTargetOutcome::Ready => {
                self.plan.staged.publish().await?;
                enqueue_briefing_followups(
                    transaction,
                    &self.queue,
                    self.plan.attempt.content.id,
                    self.briefing_debounce_seconds,
                    self.briefing_batch_minimum,
                )
                .await?;
                Ok(TaskFinalizerResult::Keep)
            }
            ImageTargetOutcome::ContentMissing
            | ImageTargetOutcome::ContentBecameNews
            | ImageTargetOutcome::AlreadyGenerated => Ok(TaskFinalizerResult::Keep),
            ImageTargetOutcome::InputChanged => Ok(TaskFinalizerResult::Override(
                TaskResult::fail(
                    Some(
                        "Image source summary changed while generation was running; retrying from the current summary"
                            .to_owned(),
                    ),
                    true,
                ),
            )),
            ImageTargetOutcome::InvalidStatus => Ok(TaskFinalizerResult::Override(
                TaskResult::fail(
                    Some(
                        "Long-form image can only publish from awaiting_image or completed state"
                            .to_owned(),
                    ),
                    true,
                ),
            )),
        }
    }
}

impl TaskFinalizer for ImageFinalizer {
    fn apply<'a>(
        &'a self,
        transaction: &'a mut Transaction<'static, Postgres>,
    ) -> HandlerFinalizerFuture<'a> {
        Box::pin(async move {
            self.apply_inner(transaction)
                .await
                .map_err(|error| Box::new(error) as Box<dyn Error + Send + Sync>)
        })
    }

    fn after_commit(&self) -> HandlerAfterCommitFuture<'_> {
        Box::pin(async move {
            self.plan.staged.cleanup().await;
        })
    }
}

#[derive(Debug, Error)]
enum ImageFinalizeError {
    #[error("image-generation database finalization failed")]
    Repository(#[from] ImageRepositoryError),
    #[error("image-generation file publication failed")]
    Storage(#[from] ImageFileStoreError),
    #[error("image-generation Briefing fanout failed")]
    BriefingFanout(#[from] SummarizationFanoutError),
}

#[cfg(test)]
mod tests;
