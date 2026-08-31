use std::error::Error;
use std::sync::atomic::{AtomicBool, Ordering};

use newsly_db::{
    MediaApplyOutcome, MediaNextTask, MediaTaskRepositoryError, apply_media_mutation,
    record_media_transcription_usage,
};
use newsly_queue::{EnqueueRequest, QueueError, QueueKernel, TaskType};
use serde_json::{Map, Value};
use sqlx::{Postgres, Transaction};
use thiserror::Error;

use crate::{HandlerAfterCommitFuture, HandlerFinalizerFuture, TaskFinalizer, TaskFinalizerResult};

use super::model::MediaFinalizationPlan;
use super::storage::MediaFileStore;

#[derive(Debug)]
pub(super) struct MediaFinalizer {
    queue: QueueKernel,
    file_store: MediaFileStore,
    plan: MediaFinalizationPlan,
    cleanup_applied: AtomicBool,
}

impl MediaFinalizer {
    pub(super) const fn new(
        queue: QueueKernel,
        file_store: MediaFileStore,
        plan: MediaFinalizationPlan,
    ) -> Self {
        Self {
            queue,
            file_store,
            plan,
            cleanup_applied: AtomicBool::new(false),
        }
    }

    async fn apply_inner(
        &self,
        transaction: &mut Transaction<'static, Postgres>,
    ) -> Result<(), MediaFinalizeError> {
        let outcome =
            apply_media_mutation(transaction, self.plan.content_id, &self.plan.mutation).await?;
        let mutation_applied = matches!(outcome, MediaApplyOutcome::Applied { .. });
        if let Some(usage) = &self.plan.usage {
            record_media_transcription_usage(transaction, usage).await?;
        }
        let MediaApplyOutcome::Applied {
            next_task: Some(next_task),
        } = outcome
        else {
            if mutation_applied && self.plan.cleanup_tweet_attempt.is_some() {
                self.cleanup_applied.store(true, Ordering::Release);
            }
            return Ok(());
        };

        let task_type = match next_task {
            MediaNextTask::Summarize => TaskType::Summarize,
            MediaNextTask::TranscribeTweetVideo => TaskType::TranscribeTweetVideo,
        };
        let mut payload = Map::new();
        payload.insert("content_id".to_owned(), Value::from(self.plan.content_id));
        let mut request = EnqueueRequest::new(task_type);
        request.content_id = Some(self.plan.content_id);
        request.payload = Some(payload);
        self.queue
            .enqueue_many_in_transaction(transaction, vec![request])
            .await?;
        if self.plan.cleanup_tweet_attempt.is_some() {
            self.cleanup_applied.store(true, Ordering::Release);
        }
        Ok(())
    }
}

impl TaskFinalizer for MediaFinalizer {
    fn apply<'a>(
        &'a self,
        transaction: &'a mut Transaction<'static, Postgres>,
    ) -> HandlerFinalizerFuture<'a> {
        Box::pin(async move {
            self.apply_inner(transaction)
                .await
                .map_err(|error| Box::new(error) as Box<dyn Error + Send + Sync>)?;
            Ok(TaskFinalizerResult::Keep)
        })
    }

    fn after_commit(&self) -> HandlerAfterCommitFuture<'_> {
        Box::pin(async move {
            if self.cleanup_applied.load(Ordering::Acquire)
                && let Some(path) = self.plan.cleanup_tweet_attempt.as_deref()
            {
                self.file_store.cleanup_tweet_attempt(path).await;
            }
        })
    }
}

#[derive(Debug, Error)]
enum MediaFinalizeError {
    #[error("media persistence failed")]
    Repository(#[from] MediaTaskRepositoryError),
    #[error("media downstream enqueue failed")]
    Queue(#[from] QueueError),
}
