use std::sync::Arc;

use newsly_db::{
    LearningDeckModelUsage, LearningDeckPreparationOutcome, LearningDeckSourceSettlement,
    LearningDeckTaskRepositoryError, MarkLearningDeckRunningOutcome,
    begin_learning_deck_preparation, mark_learning_deck_running,
    settle_learning_deck_source_missing,
};
use newsly_queue::{OwnedWorkPlan, TaskResult, TaskType};
use serde_json::{Map, Value, json};
use sqlx::PgPool;
use thiserror::Error;
use tokio_util::sync::CancellationToken;

use crate::{HandlerExecution, LeaseHealth};

use super::agent::{LearningDeckAgentError, LearningDeckAgentRuntime};
use super::artifacts::LearningDeckArtifactStore;
use super::finalizer::{LearningDeckFailureFinalizer, LearningDeckSuccessFinalizer};
use super::source::{LearningDeckSourceLoad, LearningDeckSourceLoader};

#[derive(Debug)]
pub(crate) enum LearningDeckDispatchOutcome {
    Handled(HandlerExecution),
    NotLearningDeck,
}

#[derive(Debug, Clone)]
pub struct LearningDeckTaskExecutor {
    pool: PgPool,
    agent: Arc<LearningDeckAgentRuntime>,
    artifacts: LearningDeckArtifactStore,
    source_loader: LearningDeckSourceLoader,
    max_retries: i32,
}

impl LearningDeckTaskExecutor {
    fn new(
        pool: PgPool,
        agent: Arc<LearningDeckAgentRuntime>,
        artifacts: LearningDeckArtifactStore,
        max_retries: i32,
    ) -> Self {
        Self {
            pool,
            agent,
            source_loader: LearningDeckSourceLoader::new(artifacts.clone()),
            artifacts,
            max_retries: max_retries.max(0),
        }
    }

    /// Builds the complete Learning Deck executor from fail-closed process configuration.
    pub fn from_env(pool: PgPool, max_retries: i32) -> Result<Self, LearningDeckTaskBuildError> {
        let artifacts = LearningDeckArtifactStore::from_env()
            .map_err(|error| LearningDeckTaskBuildError::Artifact(error.to_string()))?;
        let agent = Arc::new(
            LearningDeckAgentRuntime::from_env(pool.clone())
                .map_err(|error| LearningDeckTaskBuildError::Agent(error.to_string()))?,
        );
        Ok(Self::new(pool, agent, artifacts, max_retries))
    }

    /// Executes one durable Learning Deck attempt without carrying a transaction across I/O.
    pub(crate) async fn execute(
        &self,
        plan: &OwnedWorkPlan,
        mut lease: LeaseHealth,
    ) -> LearningDeckDispatchOutcome {
        if plan.task_type != TaskType::RunLlmTask {
            return handled_failure(None, "invalid_task_type", "Expected run_llm_task");
        }
        let Some(task_id) = positive_payload_id(&plan.payload, "llm_task_id") else {
            return handled_failure(
                None,
                "invalid_payload",
                "Missing or invalid llm_task_id in run_llm_task payload",
            );
        };
        let Some(user_id) = positive_payload_id(&plan.payload, "user_id") else {
            return handled_failure(
                None,
                "invalid_payload",
                "Missing or invalid user_id in run_llm_task payload",
            );
        };
        if plan.owner_user_id != Some(user_id) {
            return handled_failure(None, "ownership_mismatch", "LLM task ownership mismatch");
        }

        let snapshot = match self.prepare(task_id, user_id).await {
            Ok(PrepareResult::NotLearningDeck) => {
                return LearningDeckDispatchOutcome::NotLearningDeck;
            }
            Ok(PrepareResult::Finished(execution)) => {
                return LearningDeckDispatchOutcome::Handled(execution);
            }
            Ok(PrepareResult::Ready(snapshot)) => snapshot,
            Err(error) => return repository_failure(error, None),
        };
        if plan.retry_count > self.max_retries {
            return handled_failure(
                Some((task_id, user_id)),
                "lease_reclaim_budget_exhausted",
                "LLM task stopped after repeated worker interruptions",
            );
        }

        let source = match self.source_loader.load(&snapshot).await {
            Ok(source) => source,
            Err(error) => {
                return handled_failure(
                    Some((task_id, user_id)),
                    "source_read_failed",
                    error.to_string(),
                );
            }
        };
        let (source_snapshot, persistable_source) = match source {
            LearningDeckSourceLoad::Ready { full, persistable } => (full, persistable),
            LearningDeckSourceLoad::Missing { waiting_message } => {
                return match self
                    .settle_missing_source(&snapshot, &waiting_message)
                    .await
                {
                    Ok(execution) => LearningDeckDispatchOutcome::Handled(execution),
                    Err(error) => repository_failure(error, Some((task_id, user_id))),
                };
            }
        };
        match self.mark_running(&snapshot, &persistable_source).await {
            Ok(MarkLearningDeckRunningOutcome::Ready) => {}
            Ok(
                MarkLearningDeckRunningOutcome::Terminal
                | MarkLearningDeckRunningOutcome::Cancelled,
            ) => {
                return LearningDeckDispatchOutcome::Handled(HandlerExecution::from_result(
                    TaskResult::ok(),
                ));
            }
            Err(error) => return repository_failure(error, Some((task_id, user_id))),
        }

        if lease.ownership_lost() {
            return handled_defer(5);
        }
        let cancellation = CancellationToken::new();
        let run = self
            .agent
            .run(&snapshot, &source_snapshot, cancellation.clone());
        tokio::pin!(run);
        let agent = tokio::select! {
            result = &mut run => result,
            () = lease.wait_for_ownership_loss() => {
                cancellation.cancel();
                run.await
            }
        };
        let agent = match agent {
            Ok(agent) => agent,
            Err(error) => {
                if let Some(delay) = error.deferral_seconds() {
                    return handled_defer(delay);
                }
                let sandbox = sandbox_identity(&error);
                let agent_log_object_key = if error.agent_log_events().is_empty() {
                    None
                } else {
                    match self
                        .artifacts
                        .store_agent_log(
                            user_id,
                            snapshot.deck_id,
                            task_id,
                            error.agent_log_events(),
                        )
                        .await
                    {
                        Ok(key) => key,
                        Err(storage_error) => {
                            tracing::warn!(
                                task_id,
                                deck_id = snapshot.deck_id,
                                error = %storage_error,
                                "failed to store detached Learning Deck agent failure log"
                            );
                            None
                        }
                    }
                };
                return handled_agent_failure(
                    task_id,
                    user_id,
                    error.error_type(),
                    error.to_string(),
                    sandbox,
                    agent_log_object_key,
                );
            }
        };

        let agent_log_object_key = match self
            .artifacts
            .store_agent_log(user_id, snapshot.deck_id, task_id, &agent.events)
            .await
        {
            Ok(key) => key,
            Err(error) => {
                tracing::warn!(
                    task_id,
                    deck_id = snapshot.deck_id,
                    error = %error,
                    "failed to store detached Learning Deck agent log"
                );
                None
            }
        };
        if lease.ownership_lost() {
            return handled_defer(5);
        }
        let artifact = match self
            .artifacts
            .store_bundle(
                user_id,
                snapshot.deck_id,
                task_id,
                &agent.index_html,
                &agent.source_notes_md,
                &agent.assets,
            )
            .await
        {
            Ok(artifact) => artifact,
            Err(error) => {
                return handled_agent_failure(
                    task_id,
                    user_id,
                    "artifact_contract_failed",
                    error.to_string(),
                    Some((agent.sandbox_provider, agent.sandbox_id)),
                    agent_log_object_key,
                );
            }
        };
        if lease.ownership_lost() {
            if let Err(error) = self
                .artifacts
                .delete_many(&artifact.artifact_object_keys)
                .await
            {
                tracing::warn!(
                    task_id,
                    deck_id = snapshot.deck_id,
                    error = %error,
                    "failed to clean an unpublished Learning Deck bundle after lease loss"
                );
            }
            return handled_defer(5);
        }

        let model_name = agent.outcome.model_name.clone();
        let provider_response_id = agent.outcome.provider_response_id.clone();
        let provider_usage = agent.outcome.usage.clone();
        let request_count = u64::from(agent.outcome.request_count);
        let usage_json = json_object(json!({
            "provider_usage": provider_usage,
            "request_count": agent.outcome.request_count,
            "tool_call_count": agent.outcome.tool_call_count,
            "provider_response_id": provider_response_id,
            "events": agent.events,
        }));
        let vendor_usage = LearningDeckModelUsage {
            provider: agent.model_provider.clone(),
            model: model_name.clone(),
            provider_response_id,
            request_count,
            input_tokens: provider_usage.input_tokens,
            output_tokens: provider_usage.output_tokens,
            cache_read_tokens: provider_usage.cached_input_tokens,
            cache_write_tokens: provider_usage.cache_write_tokens,
            metadata: Map::from_iter([
                (
                    "reasoning_tokens".to_owned(),
                    Value::from(provider_usage.reasoning_tokens),
                ),
                (
                    "tool_call_count".to_owned(),
                    Value::from(agent.outcome.tool_call_count),
                ),
                (
                    "sandbox_provider".to_owned(),
                    Value::from(agent.sandbox_provider.clone()),
                ),
            ]),
        };
        LearningDeckDispatchOutcome::Handled(HandlerExecution::with_finalizer(
            TaskResult::ok(),
            LearningDeckSuccessFinalizer::new(
                self.artifacts.clone(),
                task_id,
                user_id,
                snapshot.deck_id,
                artifact,
                agent.browser_validation,
                agent.source_metadata_updates,
                agent.model_provider,
                model_name,
                agent.sandbox_provider,
                Some(agent.sandbox_id),
                agent_log_object_key,
                usage_json,
                vendor_usage,
            ),
        ))
    }

    async fn prepare(
        &self,
        task_id: i64,
        user_id: i64,
    ) -> Result<PrepareResult, LearningDeckTaskRepositoryError> {
        let mut transaction = self.pool.begin().await?;
        let outcome = begin_learning_deck_preparation(&mut transaction, task_id, user_id).await?;
        let prepared = match outcome {
            LearningDeckPreparationOutcome::NotLearningDeck => {
                transaction.rollback().await?;
                return Ok(PrepareResult::NotLearningDeck);
            }
            LearningDeckPreparationOutcome::Terminal
            | LearningDeckPreparationOutcome::Cancelled => {
                PrepareResult::Finished(HandlerExecution::from_result(TaskResult::ok()))
            }
            LearningDeckPreparationOutcome::Failed { message, .. } => PrepareResult::Finished(
                HandlerExecution::from_result(TaskResult::fail(Some(message), false)),
            ),
            LearningDeckPreparationOutcome::Ready(snapshot) => PrepareResult::Ready(snapshot),
        };
        transaction.commit().await?;
        Ok(prepared)
    }

    async fn settle_missing_source(
        &self,
        snapshot: &newsly_db::LearningDeckTaskSnapshot,
        waiting_message: &str,
    ) -> Result<HandlerExecution, LearningDeckTaskRepositoryError> {
        let mut transaction = self.pool.begin().await?;
        let outcome =
            settle_learning_deck_source_missing(&mut transaction, snapshot, waiting_message)
                .await?;
        let execution = match outcome {
            LearningDeckSourceSettlement::Deferred {
                retry_delay_seconds,
                ..
            } => HandlerExecution::from_result(TaskResult::defer(retry_delay_seconds)),
            LearningDeckSourceSettlement::Failed { message, .. } => {
                HandlerExecution::from_result(TaskResult::fail(Some(message), false))
            }
            LearningDeckSourceSettlement::Terminal | LearningDeckSourceSettlement::Cancelled => {
                HandlerExecution::from_result(TaskResult::ok())
            }
        };
        transaction.commit().await?;
        Ok(execution)
    }

    async fn mark_running(
        &self,
        snapshot: &newsly_db::LearningDeckTaskSnapshot,
        persistable_source: &Map<String, Value>,
    ) -> Result<MarkLearningDeckRunningOutcome, LearningDeckTaskRepositoryError> {
        let mut transaction = self.pool.begin().await?;
        let outcome =
            mark_learning_deck_running(&mut transaction, snapshot, persistable_source).await?;
        transaction.commit().await?;
        Ok(outcome)
    }
}

#[derive(Debug, Error)]
pub enum LearningDeckTaskBuildError {
    #[error("could not configure the Learning Deck agent: {0}")]
    Agent(String),
    #[error("could not configure Learning Deck artifact storage: {0}")]
    Artifact(String),
}

#[derive(Debug)]
enum PrepareResult {
    NotLearningDeck,
    Finished(HandlerExecution),
    Ready(newsly_db::LearningDeckTaskSnapshot),
}

fn handled_failure(
    verified_task: Option<(i64, i64)>,
    error_type: impl Into<String>,
    message: impl Into<String>,
) -> LearningDeckDispatchOutcome {
    let error_type = error_type.into();
    let message = message.into();
    let execution = if let Some((task_id, user_id)) = verified_task {
        HandlerExecution::with_finalizer(
            TaskResult::fail(Some(message.clone()), false),
            LearningDeckFailureFinalizer::new(task_id, user_id, error_type, message, None, None),
        )
    } else {
        HandlerExecution::from_result(TaskResult::fail(Some(message), false))
    };
    LearningDeckDispatchOutcome::Handled(execution)
}

fn handled_agent_failure(
    task_id: i64,
    user_id: i64,
    error_type: impl Into<String>,
    message: impl Into<String>,
    sandbox: Option<(String, String)>,
    agent_log_object_key: Option<String>,
) -> LearningDeckDispatchOutcome {
    let message = message.into();
    LearningDeckDispatchOutcome::Handled(HandlerExecution::with_finalizer(
        TaskResult::fail(Some(message.clone()), false),
        LearningDeckFailureFinalizer::new(
            task_id,
            user_id,
            error_type,
            message,
            sandbox,
            agent_log_object_key,
        ),
    ))
}

fn handled_defer(retry_delay_seconds: i64) -> LearningDeckDispatchOutcome {
    LearningDeckDispatchOutcome::Handled(HandlerExecution::from_result(TaskResult::defer(
        retry_delay_seconds,
    )))
}

fn repository_failure(
    error: LearningDeckTaskRepositoryError,
    verified_task: Option<(i64, i64)>,
) -> LearningDeckDispatchOutcome {
    match error {
        LearningDeckTaskRepositoryError::Sqlx(error) => LearningDeckDispatchOutcome::Handled(
            HandlerExecution::from_result(TaskResult::fail(Some(error.to_string()), true)),
        ),
        error => handled_failure(
            verified_task,
            repository_error_type(&error),
            error.to_string(),
        ),
    }
}

fn repository_error_type(error: &LearningDeckTaskRepositoryError) -> &'static str {
    match error {
        LearningDeckTaskRepositoryError::TaskNotFound => "LlmTaskError",
        LearningDeckTaskRepositoryError::OwnershipMismatch => "ownership_mismatch",
        LearningDeckTaskRepositoryError::LearningDeck(_) => "LearningDeckRepositoryError",
        LearningDeckTaskRepositoryError::Sqlx(_) => "LearningDeckTaskRepositoryError",
    }
}

fn sandbox_identity(error: &LearningDeckAgentError) -> Option<(String, String)> {
    error
        .sandbox_identity()
        .map(|(provider, id)| (provider.to_owned(), id.to_owned()))
}

fn positive_payload_id(payload: &Map<String, Value>, field: &str) -> Option<i64> {
    payload
        .get(field)
        .and_then(Value::as_i64)
        .filter(|value| *value > 0)
}

fn json_object(value: Value) -> Map<String, Value> {
    value.as_object().cloned().unwrap_or_default()
}
