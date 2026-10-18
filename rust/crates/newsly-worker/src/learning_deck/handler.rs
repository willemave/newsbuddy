use std::sync::Arc;
use std::time::Duration;

use newsly_db::{
    LearningDeckGenerationClaimFence, LearningDeckPreparationOutcome, LearningDeckSourceSettlement,
    LearningDeckTaskRepositoryError, MarkLearningDeckRunningOutcome,
    begin_learning_deck_preparation, learning_deck_generation_is_owned, mark_learning_deck_running,
    publish_learning_deck_progress, read_learning_deck_generation,
    settle_learning_deck_source_missing,
};
use newsly_queue::{OwnedWorkPlan, TaskResult};
use serde_json::{Map, Value, json};
use sqlx::PgPool;
use thiserror::Error;
use tokio::time::{Instant, timeout_at};
use tokio_util::sync::CancellationToken;

use crate::{HandlerExecution, LeaseHealth};

use super::agent::{LearningDeckAgentError, LearningDeckAgentRuntime, minimum_generation_budget};
use super::artifacts::{LearningDeckArtifactError, LearningDeckArtifactStore};
use super::finalizer::{LearningDeckFailureFinalizer, LearningDeckSuccessFinalizer};
use super::source::{LearningDeckSourceLoad, load_source};

#[derive(Debug, Clone)]
pub struct LearningDeckTaskExecutor {
    pool: PgPool,
    agent: Arc<LearningDeckAgentRuntime>,
    artifacts: LearningDeckArtifactStore,
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
            artifacts,
            max_retries: max_retries.max(0),
        }
    }

    /// Builds the complete Learning Deck executor from fail-closed process configuration.
    pub fn from_env(pool: PgPool, max_retries: i32) -> Result<Self, LearningDeckTaskBuildError> {
        let artifacts = LearningDeckArtifactStore::from_env(pool.clone())
            .map_err(|error| LearningDeckTaskBuildError::Artifact(error.to_string()))?;
        artifacts.start_cleanup();
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
        task_id: i64,
        user_id: i64,
    ) -> HandlerExecution {
        let snapshot = match self.prepare(task_id, user_id).await {
            Ok(PrepareResult::NotLearningDeck) => {
                return crate::run_llm_task::unsupported_execution(
                    task_id,
                    user_id,
                    "learning_deck",
                );
            }
            Ok(PrepareResult::Finished(execution)) => return execution,
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

        let source = match load_source(&self.artifacts, &snapshot).await {
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
                    Ok(execution) => execution,
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
                return HandlerExecution::from_result(TaskResult::ok());
            }
            Err(error) => return repository_failure(error, Some((task_id, user_id))),
        }

        if lease.ownership_lost() {
            return handled_defer(5);
        }
        let claim = lease.claim();
        let Ok(executor_version) = i32::try_from(claim.executor_version) else {
            return handled_failure(
                Some((task_id, user_id)),
                "invalid_executor_version",
                "Learning Deck executor version is out of range",
            );
        };
        let fence = LearningDeckGenerationClaimFence {
            processing_task_id: claim.id,
            user_id,
            llm_task_id: task_id,
            deck_id: snapshot.deck_id,
            locked_by: claim.locked_by.clone(),
            lease_token: claim.lease_token.get(),
            retry_count: claim.retry_count,
            executor_runtime: claim.executor_runtime.as_str().to_owned(),
            executor_version,
            executor_namespace: claim.executor_namespace.clone(),
        };
        let cancellation = CancellationToken::new();
        let run = self.agent.run(
            &snapshot,
            &source_snapshot,
            &fence,
            &self.artifacts,
            cancellation.clone(),
        );
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
                let state = read_learning_deck_generation(&self.pool, &fence)
                    .await
                    .ok()
                    .flatten();
                let remaining = state.as_ref().map(|state| state.remaining_seconds);
                let retry_note = retry_progress_note(
                    state
                        .as_ref()
                        .and_then(|state| state.draft_object_key.as_deref()),
                );
                let retry_deadline = remaining.map(|seconds| {
                    Instant::now() + Duration::from_secs(u64::try_from(seconds.max(0)).unwrap_or(0))
                });
                let log_deadline = Instant::now()
                    + Duration::from_secs(
                        u64::try_from(remaining.unwrap_or(5).clamp(0, 5)).unwrap_or(0),
                    );
                let agent_log_object_key = if error.agent_log_events().is_empty() {
                    None
                } else {
                    match timeout_at(
                        log_deadline,
                        self.artifacts.store_agent_log(
                            user_id,
                            snapshot.deck_id,
                            task_id,
                            error.agent_log_events(),
                        ),
                    )
                    .await
                    {
                        Ok(Ok(key)) => key,
                        Ok(Err(storage_error)) => {
                            tracing::warn!(task_id, error = %storage_error,"failed to store detached Learning Deck agent failure log");
                            None
                        }
                        Err(_) => None,
                    }
                };
                if retryable_agent_error(&error) && plan.retry_count < self.max_retries {
                    let retry_budget = minimum_generation_budget(
                        state
                            .as_ref()
                            .is_some_and(|state| state.draft_object_key.is_some()),
                    ) + Duration::from_secs(5);
                    if retry_deadline
                        .is_none_or(|deadline| deadline > Instant::now() + retry_budget)
                    {
                        let _ =
                            publish_learning_deck_progress(&self.pool, &fence, retry_note).await;
                        return retry_execution(error.to_string());
                    }
                    return handled_agent_failure(
                        task_id,
                        user_id,
                        "generation_deadline_exceeded",
                        "Learning Deck generation deadline expired",
                        sandbox,
                        agent_log_object_key,
                    );
                }
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

        let agent_log_object_key = match timeout_at(
            agent.deadline.min(Instant::now() + Duration::from_secs(5)),
            self.artifacts
                .store_agent_log(user_id, snapshot.deck_id, task_id, &agent.events),
        )
        .await
        {
            Ok(Ok(key)) => key,
            Ok(Err(error)) => {
                tracing::warn!(task_id,error = %error,"failed to store detached Learning Deck agent log");
                None
            }
            Err(_) => None,
        };
        if lease.ownership_lost() {
            return handled_defer(5);
        }
        match timeout_at(
            agent.deadline,
            publish_learning_deck_progress(&self.pool, &fence, "Saving Learning Deck"),
        )
        .await
        {
            Ok(Ok(true)) => {}
            Ok(Ok(false)) => {
                return match read_learning_deck_generation(&self.pool, &fence).await {
                    Ok(Some(state)) if state.remaining_seconds == 0 => handled_agent_failure(
                        task_id,
                        user_id,
                        "generation_deadline_exceeded",
                        "Learning Deck generation deadline expired",
                        Some((agent.sandbox_provider, agent.sandbox_id)),
                        agent_log_object_key,
                    ),
                    _ => handled_defer(5),
                };
            }
            Ok(Err(error)) => {
                if Instant::now() + minimum_generation_budget(true) + Duration::from_secs(5)
                    >= agent.deadline
                {
                    return handled_agent_failure(
                        task_id,
                        user_id,
                        "generation_deadline_exceeded",
                        "Learning Deck generation deadline expired",
                        Some((agent.sandbox_provider, agent.sandbox_id)),
                        agent_log_object_key,
                    );
                }
                if plan.retry_count >= self.max_retries {
                    return handled_agent_failure(
                        task_id,
                        user_id,
                        "artifact_storage_failed",
                        error.to_string(),
                        Some((agent.sandbox_provider, agent.sandbox_id)),
                        agent_log_object_key,
                    );
                }
                return retry_execution(error.to_string());
            }
            Err(_) => {
                return handled_agent_failure(
                    task_id,
                    user_id,
                    "generation_deadline_exceeded",
                    "Learning Deck generation deadline expired",
                    Some((agent.sandbox_provider, agent.sandbox_id)),
                    agent_log_object_key,
                );
            }
        }
        let stored = {
            let store = timeout_at(
                agent.deadline,
                self.artifacts.store_bundle(
                    user_id,
                    snapshot.deck_id,
                    task_id,
                    &agent.index_html,
                    &agent.source_notes_md,
                    &agent.assets,
                ),
            );
            tokio::pin!(store);
            tokio::select! {
                result = &mut store => result,
                () = lease.wait_for_ownership_loss() => return handled_defer(5),
                () = wait_for_product_cancellation(&self.pool,&fence) => return handled_defer(5),
            }
        };
        let artifact = match stored {
            Err(_) => {
                return handled_agent_failure(
                    task_id,
                    user_id,
                    "generation_deadline_exceeded",
                    "Learning Deck generation deadline expired while saving",
                    Some((agent.sandbox_provider, agent.sandbox_id)),
                    agent_log_object_key,
                );
            }
            Ok(Ok(artifact)) => artifact,
            Ok(Err(error)) => {
                if matches!(
                    &error,
                    LearningDeckArtifactError::Database(_)
                        | LearningDeckArtifactError::ObjectStore(_)
                        | LearningDeckArtifactError::File { .. }
                ) {
                    if Instant::now() + minimum_generation_budget(true) + Duration::from_secs(5)
                        >= agent.deadline
                    {
                        return handled_agent_failure(
                            task_id,
                            user_id,
                            "generation_deadline_exceeded",
                            "Learning Deck generation deadline expired",
                            Some((agent.sandbox_provider, agent.sandbox_id)),
                            agent_log_object_key,
                        );
                    }
                    if plan.retry_count >= self.max_retries {
                        return handled_agent_failure(
                            task_id,
                            user_id,
                            "artifact_storage_failed",
                            error.to_string(),
                            Some((agent.sandbox_provider, agent.sandbox_id)),
                            agent_log_object_key,
                        );
                    }
                    let state = read_learning_deck_generation(&self.pool, &fence)
                        .await
                        .ok()
                        .flatten();
                    let retry_note = retry_progress_note(
                        state
                            .as_ref()
                            .and_then(|state| state.draft_object_key.as_deref()),
                    );
                    let _ = publish_learning_deck_progress(&self.pool, &fence, retry_note).await;
                    return retry_execution(error.to_string());
                }
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
            // Every immutable object is already registered for retry-safe background cleanup.
            return handled_defer(5);
        }

        let model_name = agent.outcome.model_name.clone();
        let provider_usage = agent.outcome.usage.clone();
        let usage_json = json_object(json!({
            "provider_usage": provider_usage,
            "request_count": agent.outcome.request_count,
            "tool_call_count": agent.outcome.tool_call_count,
            "provider_response_id": agent.outcome.provider_response_id,
            "event_count": agent.events.len(),
        }));
        HandlerExecution::with_finalizer(
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
            ),
        )
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
) -> HandlerExecution {
    let error_type = error_type.into();
    let message = message.into();
    if let Some((task_id, user_id)) = verified_task {
        HandlerExecution::with_finalizer(
            TaskResult::fail(Some(message.clone()), false),
            LearningDeckFailureFinalizer::new(task_id, user_id, error_type, message, None, None),
        )
    } else {
        HandlerExecution::from_result(TaskResult::fail(Some(message), false))
    }
}

fn handled_agent_failure(
    task_id: i64,
    user_id: i64,
    error_type: impl Into<String>,
    message: impl Into<String>,
    sandbox: Option<(String, String)>,
    agent_log_object_key: Option<String>,
) -> HandlerExecution {
    let message = message.into();
    HandlerExecution::with_finalizer(
        TaskResult::fail(Some(message.clone()), false),
        LearningDeckFailureFinalizer::new(
            task_id,
            user_id,
            error_type,
            message,
            sandbox,
            agent_log_object_key,
        ),
    )
}

async fn wait_for_product_cancellation(pool: &PgPool, fence: &LearningDeckGenerationClaimFence) {
    let mut interval = tokio::time::interval(Duration::from_secs(5));
    interval.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
    loop {
        interval.tick().await;
        match tokio::time::timeout(
            Duration::from_secs(5),
            learning_deck_generation_is_owned(pool, fence),
        )
        .await
        {
            Ok(Ok(true)) => {}
            Ok(Ok(false)) => return,
            Ok(Err(error)) => {
                tracing::warn!(llm_task_id=fence.llm_task_id,error=%error,"could not check Learning Deck ownership while publishing");
            }
            Err(_) => tracing::warn!(
                llm_task_id = fence.llm_task_id,
                "Learning Deck ownership check timed out while publishing"
            ),
        }
    }
}

fn retry_progress_note(draft_object_key: Option<&str>) -> &'static str {
    if draft_object_key.is_some() {
        "Retrying from saved progress"
    } else {
        "Retrying deck generation"
    }
}

fn retry_execution(message: String) -> HandlerExecution {
    let mut result = TaskResult::fail(Some(message), true);
    result.retry_delay_seconds = Some(5);
    HandlerExecution::from_result(result)
}

fn handled_defer(retry_delay_seconds: i64) -> HandlerExecution {
    HandlerExecution::from_result(TaskResult::defer(retry_delay_seconds))
}

fn repository_failure(
    error: LearningDeckTaskRepositoryError,
    verified_task: Option<(i64, i64)>,
) -> HandlerExecution {
    match error {
        LearningDeckTaskRepositoryError::Sqlx(error) => {
            HandlerExecution::from_result(TaskResult::fail(Some(error.to_string()), true))
        }
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

fn json_object(value: Value) -> Map<String, Value> {
    value.as_object().cloned().unwrap_or_default()
}

fn retryable_agent_error(error: &LearningDeckAgentError) -> bool {
    match error {
        LearningDeckAgentError::SandboxExecution { source, .. } => retryable_agent_error(source),
        LearningDeckAgentError::E2b(error)
        | LearningDeckAgentError::Lifecycle(
            crate::task_sandbox::TaskSandboxError::Provider(error)
            | crate::task_sandbox::TaskSandboxError::ProviderOperation { source: error, .. },
        ) => {
            matches!(error, newsly_e2b::E2bError::NotFound { .. })
                || matches!(
                    error.disposition(),
                    newsly_e2b::ErrorDisposition::Retryable
                        | newsly_e2b::ErrorDisposition::Ambiguous
                )
        }
        LearningDeckAgentError::Database(_)
        | LearningDeckAgentError::Validator { .. }
        | LearningDeckAgentError::Storage(
            LearningDeckArtifactError::Database(_)
            | LearningDeckArtifactError::ObjectStore(_)
            | LearningDeckArtifactError::File { .. },
        )
        | LearningDeckAgentError::Lifecycle(crate::task_sandbox::TaskSandboxError::Deadline)
        | LearningDeckAgentError::Agent(
            newsly_agent_runtime::AgentRuntimeError::DeadlineExceeded
            | newsly_agent_runtime::AgentRuntimeError::Provider(_),
        ) => true,
        _ => false,
    }
}

#[cfg(test)]
mod retry_tests {
    use super::{LearningDeckAgentError, retryable_agent_error};
    #[test]
    fn phase_deadlines_retry_but_total_deadlines_and_invalid_instructions_stop() {
        assert!(retryable_agent_error(&LearningDeckAgentError::Agent(
            newsly_agent_runtime::AgentRuntimeError::DeadlineExceeded,
        )));
        assert!(!retryable_agent_error(&LearningDeckAgentError::Deadline));
        assert!(!retryable_agent_error(&LearningDeckAgentError::Prompt(
            "timeout".to_owned()
        )));
        assert!(!retryable_agent_error(&LearningDeckAgentError::Cancelled));
        assert!(retryable_agent_error(&LearningDeckAgentError::Validator {
            message: "browser unavailable".to_owned(),
            report: serde_json::Map::new(),
        }));
        assert!(!retryable_agent_error(&LearningDeckAgentError::Artifact {
            message: "invalid file".to_owned(),
            report: serde_json::Map::new(),
            repairable: false,
        }));
    }

    #[tokio::test]
    async fn unavailable_ownership_probe_does_not_cancel_publication() {
        let pool = sqlx::PgPool::connect_lazy("postgresql://127.0.0.1/newsly_probe_test").unwrap();
        pool.close().await;
        let fence = newsly_db::LearningDeckGenerationClaimFence {
            processing_task_id: 1,
            user_id: 1,
            llm_task_id: 1,
            deck_id: 1,
            locked_by: "test".to_owned(),
            lease_token: uuid::Uuid::nil(),
            retry_count: 0,
            executor_runtime: "rust".to_owned(),
            executor_version: 1,
            executor_namespace: "run_llm_task".to_owned(),
        };
        assert!(
            tokio::time::timeout(
                std::time::Duration::from_millis(50),
                super::wait_for_product_cancellation(&pool, &fence)
            )
            .await
            .is_err()
        );
    }

    #[test]
    fn retry_progress_only_claims_saved_work_when_checkpoint_exists() {
        assert_eq!(super::retry_progress_note(None), "Retrying deck generation");
        assert_eq!(
            super::retry_progress_note(Some("private/draft.json")),
            "Retrying from saved progress"
        );
    }

    #[test]
    fn infrastructure_retry_preserves_short_durable_delay() {
        let execution = super::retry_execution("temporary storage failure".to_owned());
        assert!(execution.task_result.retryable);
        assert!(!execution.task_result.deferred);
        assert_eq!(execution.task_result.retry_delay_seconds, Some(5));
    }
}
