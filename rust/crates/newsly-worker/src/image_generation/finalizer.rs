use std::error::Error;

use newsly_queue::TaskResult;
use sqlx::{Postgres, Transaction};
use thiserror::Error;

use crate::{HandlerAfterCommitFuture, HandlerFinalizerFuture, TaskFinalizer, TaskFinalizerResult};

use super::model::{ImageFinalizationPlan, ImageTargetOutcome};
use super::repository::{ImageRepositoryError, apply_generated_image};
use super::storage::ImageFileStoreError;

#[derive(Debug)]
pub(super) struct ImageFinalizer {
    plan: ImageFinalizationPlan,
}

impl ImageFinalizer {
    pub(super) const fn new(plan: ImageFinalizationPlan) -> Self {
        Self { plan }
    }

    async fn apply_inner(
        &self,
        transaction: &mut Transaction<'static, Postgres>,
    ) -> Result<TaskFinalizerResult, ImageFinalizeError> {
        let outcome = apply_generated_image(transaction, &self.plan).await?;
        match outcome {
            ImageTargetOutcome::Ready => {
                self.plan.staged.publish().await?;
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
}
