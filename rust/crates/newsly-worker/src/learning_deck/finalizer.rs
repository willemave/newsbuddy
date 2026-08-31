use std::error::Error;
use std::sync::Mutex;

use newsly_db::{
    LearningDeckModelUsage, PublishLearningDeck, PublishLearningDeckOutcome,
    StoredLearningDeckArtifact, fail_learning_deck_task, publish_learning_deck,
};
use newsly_queue::TaskResult;
use serde_json::{Map, Value};
use sqlx::{Postgres, Transaction};

use crate::{HandlerAfterCommitFuture, HandlerFinalizerFuture, TaskFinalizer, TaskFinalizerResult};

use super::artifacts::LearningDeckArtifactStore;

#[derive(Debug)]
pub(super) struct LearningDeckSuccessFinalizer {
    artifact_store: LearningDeckArtifactStore,
    task_id: i64,
    user_id: i64,
    deck_id: i64,
    artifact: StoredLearningDeckArtifact,
    browser_validation: Map<String, Value>,
    source_metadata_updates: Map<String, Value>,
    model_provider: String,
    model_name: String,
    sandbox_provider: String,
    sandbox_id: Option<String>,
    agent_log_object_key: Option<String>,
    usage_json: Map<String, Value>,
    vendor_usage: LearningDeckModelUsage,
    cleanup_after_commit: Mutex<Vec<String>>,
}

impl LearningDeckSuccessFinalizer {
    #[allow(clippy::too_many_arguments)]
    pub(super) fn new(
        artifact_store: LearningDeckArtifactStore,
        task_id: i64,
        user_id: i64,
        deck_id: i64,
        artifact: StoredLearningDeckArtifact,
        browser_validation: Map<String, Value>,
        source_metadata_updates: Map<String, Value>,
        model_provider: String,
        model_name: String,
        sandbox_provider: String,
        sandbox_id: Option<String>,
        agent_log_object_key: Option<String>,
        usage_json: Map<String, Value>,
        vendor_usage: LearningDeckModelUsage,
    ) -> Self {
        Self {
            artifact_store,
            task_id,
            user_id,
            deck_id,
            artifact,
            browser_validation,
            source_metadata_updates,
            model_provider,
            model_name,
            sandbox_provider,
            sandbox_id,
            agent_log_object_key,
            usage_json,
            vendor_usage,
            cleanup_after_commit: Mutex::new(Vec::new()),
        }
    }

    async fn apply_inner(
        &self,
        transaction: &mut Transaction<'_, Postgres>,
    ) -> Result<TaskFinalizerResult, Box<dyn Error + Send + Sync>> {
        let publication = PublishLearningDeck {
            task_id: self.task_id,
            user_id: self.user_id,
            deck_id: self.deck_id,
            artifact: &self.artifact,
            browser_validation: &self.browser_validation,
            source_metadata_updates: &self.source_metadata_updates,
            model_provider: &self.model_provider,
            model_name: &self.model_name,
            sandbox_provider: &self.sandbox_provider,
            sandbox_id: self.sandbox_id.as_deref(),
            agent_log_object_key: self.agent_log_object_key.as_deref(),
            usage_json: &self.usage_json,
            vendor_usage: Some(&self.vendor_usage),
        };
        let outcome = publish_learning_deck(transaction, &publication).await?;
        let (cleanup, result) = match outcome {
            PublishLearningDeckOutcome::Published { stale_object_keys } => {
                (stale_object_keys, TaskFinalizerResult::Keep)
            }
            PublishLearningDeckOutcome::Terminal | PublishLearningDeckOutcome::Cancelled => (
                self.artifact.artifact_object_keys.clone(),
                TaskFinalizerResult::Keep,
            ),
            PublishLearningDeckOutcome::Failed {
                error_type,
                message,
            } => {
                tracing::warn!(
                    task_id = self.task_id,
                    deck_id = self.deck_id,
                    error_type,
                    error = %message,
                    "Learning Deck publication was rejected by durable product state"
                );
                (
                    self.artifact.artifact_object_keys.clone(),
                    TaskFinalizerResult::Override(TaskResult::fail(Some(message), false)),
                )
            }
        };
        self.cleanup_after_commit
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .extend(cleanup);
        Ok(result)
    }
}

impl TaskFinalizer for LearningDeckSuccessFinalizer {
    fn apply<'a>(
        &'a self,
        transaction: &'a mut Transaction<'static, Postgres>,
    ) -> HandlerFinalizerFuture<'a> {
        Box::pin(async move { self.apply_inner(transaction).await })
    }

    fn after_commit(&self) -> HandlerAfterCommitFuture<'_> {
        Box::pin(async move {
            let keys = {
                let mut cleanup = self
                    .cleanup_after_commit
                    .lock()
                    .unwrap_or_else(std::sync::PoisonError::into_inner);
                std::mem::take(&mut *cleanup)
            };
            if keys.is_empty() {
                return;
            }
            if let Err(error) = self.artifact_store.delete_many(&keys).await {
                tracing::warn!(
                    task_id = self.task_id,
                    deck_id = self.deck_id,
                    object_count = keys.len(),
                    error = %error,
                    "failed to retire Learning Deck artifact objects after publication"
                );
            }
        })
    }
}

#[derive(Debug)]
pub(super) struct LearningDeckFailureFinalizer {
    task_id: i64,
    user_id: i64,
    error_type: String,
    message: String,
    sandbox_provider: Option<String>,
    sandbox_id: Option<String>,
    agent_log_object_key: Option<String>,
}

impl LearningDeckFailureFinalizer {
    pub(super) fn new(
        task_id: i64,
        user_id: i64,
        error_type: impl Into<String>,
        message: impl Into<String>,
        sandbox: Option<(String, String)>,
        agent_log_object_key: Option<String>,
    ) -> Self {
        let (sandbox_provider, sandbox_id) = sandbox
            .map(|(provider, id)| (Some(provider), Some(id)))
            .unwrap_or_default();
        Self {
            task_id,
            user_id,
            error_type: error_type.into(),
            message: message.into(),
            sandbox_provider,
            sandbox_id,
            agent_log_object_key,
        }
    }
}

impl TaskFinalizer for LearningDeckFailureFinalizer {
    fn apply<'a>(
        &'a self,
        transaction: &'a mut Transaction<'static, Postgres>,
    ) -> HandlerFinalizerFuture<'a> {
        Box::pin(async move {
            fail_learning_deck_task(
                transaction,
                self.task_id,
                self.user_id,
                &self.error_type,
                &self.message,
                self.sandbox_provider.as_deref(),
                self.sandbox_id.as_deref(),
                self.agent_log_object_key.as_deref(),
            )
            .await
            .map(|()| TaskFinalizerResult::Keep)
            .map_err(|error| Box::new(error) as Box<dyn Error + Send + Sync>)
        })
    }
}
