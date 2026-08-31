use std::sync::Arc;

use newsly_db::{
    PreparedShareSource, ShareActionPreparation, ShareActionRepositoryError,
    begin_share_action_preparation, finish_share_action_preparation,
};
use newsly_queue::{OwnedWorkPlan, QueueKernel, TaskResult, TaskType};
use serde_json::Value;
use sqlx::PgPool;
use tokio_util::sync::CancellationToken;

use crate::{HandlerExecution, LeaseHealth};

use super::agent::ShareActionAgentRuntime;
use super::finalizer::{ShareActionFailureFinalizer, ShareActionSuccessFinalizer};
use super::submission::{ShareSubmissionPolicy, submit_content_action};
use super::workflows::{ContentActionInput, build_deterministic_chat_action, build_host_action};

/// Outcome used by the shared `run_llm_task` dispatcher.
///
/// `run_llm_task` also owns Learning Deck attempts, so a Share Action executor must decline an
/// unrelated durable row rather than claiming the whole namespace as its own product handler.
#[derive(Debug)]
pub enum ShareActionDispatchOutcome {
    Handled(HandlerExecution),
    NotShareAction,
}

#[derive(Debug, Clone)]
pub struct ShareActionTaskExecutor {
    pool: PgPool,
    queue: QueueKernel,
    agent: Arc<ShareActionAgentRuntime>,
    sandbox_root: String,
    max_retries: i32,
}

impl ShareActionTaskExecutor {
    pub fn new(
        pool: PgPool,
        queue: QueueKernel,
        agent: Arc<ShareActionAgentRuntime>,
        sandbox_root: impl Into<String>,
        max_retries: i32,
    ) -> Self {
        Self {
            pool,
            queue,
            agent,
            sandbox_root: sandbox_root.into(),
            max_retries: max_retries.max(0),
        }
    }

    /// Runs one Share Action row using the queue-owned immutable plan.
    ///
    /// Database preparation commits before the E2B/Rig call starts. Product finalization is
    /// returned to the worker kernel and therefore executes only inside its fresh lease-fenced
    /// transaction.
    pub async fn execute(
        &self,
        plan: &OwnedWorkPlan,
        mut lease: LeaseHealth,
    ) -> ShareActionDispatchOutcome {
        if plan.task_type != TaskType::RunLlmTask {
            return handled_failure(None, "invalid_task_type", "Expected run_llm_task", false);
        }
        let Some(task_id) = positive_payload_id(&plan.payload, "llm_task_id") else {
            return handled_failure(
                None,
                "invalid_payload",
                "Missing or invalid llm_task_id in run_llm_task payload",
                false,
            );
        };
        let Some(user_id) = positive_payload_id(&plan.payload, "user_id") else {
            return handled_failure(
                None,
                "invalid_payload",
                "Missing or invalid user_id in run_llm_task payload",
                false,
            );
        };
        if plan.owner_user_id != Some(user_id) {
            return handled_failure(
                None,
                "ownership_mismatch",
                "LLM task ownership mismatch",
                false,
            );
        }
        let snapshot = {
            let mut transaction = match self.pool.begin().await {
                Ok(transaction) => transaction,
                Err(error) => {
                    return handled_retry(error.to_string());
                }
            };
            let preparation =
                match begin_share_action_preparation(&mut transaction, task_id, user_id).await {
                    Ok(preparation) => preparation,
                    Err(ShareActionRepositoryError::WrongTaskKind) => {
                        return ShareActionDispatchOutcome::NotShareAction;
                    }
                    Err(
                        error @ (ShareActionRepositoryError::TaskNotFound
                        | ShareActionRepositoryError::OwnershipMismatch),
                    ) => {
                        return handled_failure(
                            None,
                            repository_error_type(&error),
                            error.to_string(),
                            false,
                        );
                    }
                    Err(ShareActionRepositoryError::Sqlx(error)) => {
                        return handled_retry(error.to_string());
                    }
                    Err(error) => {
                        return handled_failure(
                            Some((task_id, user_id)),
                            repository_error_type(&error),
                            error.to_string(),
                            false,
                        );
                    }
                };
            let draft = match preparation {
                ShareActionPreparation::Terminal => {
                    if let Err(error) = transaction.commit().await {
                        return handled_retry(error.to_string());
                    }
                    return ShareActionDispatchOutcome::Handled(HandlerExecution::from_result(
                        TaskResult::ok(),
                    ));
                }
                ShareActionPreparation::Ready(draft) => draft,
            };
            if plan.retry_count > self.max_retries {
                return handled_failure(
                    Some((task_id, user_id)),
                    "lease_reclaim_budget_exhausted",
                    "LLM task stopped after repeated worker interruptions",
                    false,
                );
            }
            let source = if draft.prepare_shared_source {
                let Some(url) = clean_input_text(&draft.input, "url") else {
                    return handled_failure(
                        Some((task_id, user_id)),
                        "invalid_task_input",
                        "Share Action URL is missing",
                        false,
                    );
                };
                let action_input = ContentActionInput {
                    url,
                    title: None,
                    platform: None,
                    content_type: None,
                    instruction: clean_input_text(&draft.input, "instruction"),
                    chat_initial_message: clean_input_text(&draft.input, "chat_initial_message"),
                };
                let policy = if draft.mode == "chat" {
                    ShareSubmissionPolicy::chat()
                } else {
                    ShareSubmissionPolicy::content_saved()
                };
                let submitted = match submit_content_action(
                    &mut transaction,
                    &self.queue,
                    user_id,
                    &action_input,
                    policy,
                )
                .await
                {
                    Ok(submitted) => submitted,
                    Err(error) => {
                        return handled_failure(
                            Some((task_id, user_id)),
                            "ShareActionPreparationError",
                            error.to_string(),
                            false,
                        );
                    }
                };
                Some(PreparedShareSource {
                    content_id: submitted.content_id,
                    task_id: submitted.task_id,
                })
            } else {
                None
            };
            let snapshot =
                match finish_share_action_preparation(&mut transaction, draft, source).await {
                    Ok(snapshot) => snapshot,
                    Err(error) => {
                        return handled_failure(
                            Some((task_id, user_id)),
                            repository_error_type(&error),
                            error.to_string(),
                            false,
                        );
                    }
                };
            if let Err(error) = transaction.commit().await {
                return handled_retry(error.to_string());
            }
            snapshot
        };

        if snapshot.mode == "chat" {
            return match build_deterministic_chat_action(&snapshot) {
                Ok(action) => {
                    ShareActionDispatchOutcome::Handled(HandlerExecution::with_finalizer(
                        TaskResult::ok(),
                        ShareActionSuccessFinalizer::deterministic_chat(
                            self.queue.clone(),
                            self.sandbox_root.clone(),
                            snapshot,
                            action,
                        ),
                    ))
                }
                Err(error) => handled_failure(
                    Some((task_id, user_id)),
                    "ShareActionWorkflowError",
                    error.to_string(),
                    false,
                ),
            };
        }

        if lease.ownership_lost() {
            return handled_defer(5);
        }
        let agent = {
            let cancellation = CancellationToken::new();
            let run = self.agent.run(&snapshot, cancellation.clone());
            tokio::pin!(run);
            let agent = tokio::select! {
                result = &mut run => result,
                () = lease.wait_for_ownership_loss() => {
                    cancellation.cancel();
                    run.await
                }
            };
            match agent {
                Ok(agent) => agent,
                Err(error) => {
                    if let Some(delay) = error.deferral_seconds() {
                        return handled_defer(delay);
                    }
                    let sandbox = error
                        .sandbox_identity()
                        .map(|(provider, id)| (provider.to_owned(), id.to_owned()));
                    return handled_agent_failure(
                        task_id,
                        user_id,
                        "ShareActionAgentExecutionError",
                        error.to_string(),
                        sandbox,
                    );
                }
            }
        };
        let host_action =
            match build_host_action(&snapshot, &agent.result, agent.validated_feed.as_ref()) {
                Ok(action) => action,
                Err(error) => {
                    return handled_failure(
                        Some((task_id, user_id)),
                        "ShareActionResultValidationError",
                        error.to_string(),
                        false,
                    );
                }
            };
        ShareActionDispatchOutcome::Handled(HandlerExecution::with_finalizer(
            TaskResult::ok(),
            ShareActionSuccessFinalizer::agent(
                self.queue.clone(),
                self.sandbox_root.clone(),
                snapshot,
                agent,
                host_action,
            ),
        ))
    }
}

fn handled_failure(
    verified_task: Option<(i64, i64)>,
    error_type: impl Into<String>,
    message: impl Into<String>,
    retryable: bool,
) -> ShareActionDispatchOutcome {
    let error_type = error_type.into();
    let message = message.into();
    let execution = match verified_task {
        Some((task_id, user_id)) => HandlerExecution::with_finalizer(
            TaskResult::fail(Some(message.clone()), retryable),
            ShareActionFailureFinalizer {
                task_id,
                user_id,
                error_type,
                message,
                sandbox_provider: None,
                sandbox_id: None,
            },
        ),
        None => HandlerExecution::from_result(TaskResult::fail(Some(message), retryable)),
    };
    ShareActionDispatchOutcome::Handled(execution)
}

fn handled_retry(message: impl Into<String>) -> ShareActionDispatchOutcome {
    ShareActionDispatchOutcome::Handled(HandlerExecution::from_result(TaskResult::fail(
        Some(message.into()),
        true,
    )))
}

fn handled_agent_failure(
    task_id: i64,
    user_id: i64,
    error_type: impl Into<String>,
    message: impl Into<String>,
    sandbox: Option<(String, String)>,
) -> ShareActionDispatchOutcome {
    let (sandbox_provider, sandbox_id) = sandbox
        .map(|(provider, id)| (Some(provider), Some(id)))
        .unwrap_or_default();
    let message = message.into();
    ShareActionDispatchOutcome::Handled(HandlerExecution::with_finalizer(
        TaskResult::fail(Some(message.clone()), false),
        ShareActionFailureFinalizer {
            task_id,
            user_id,
            error_type: error_type.into(),
            message,
            sandbox_provider,
            sandbox_id,
        },
    ))
}

fn handled_defer(retry_delay_seconds: i64) -> ShareActionDispatchOutcome {
    ShareActionDispatchOutcome::Handled(HandlerExecution::from_result(TaskResult::defer(
        retry_delay_seconds,
    )))
}

fn positive_payload_id(payload: &serde_json::Map<String, Value>, field: &str) -> Option<i64> {
    payload
        .get(field)
        .and_then(Value::as_i64)
        .filter(|value| *value > 0)
}

fn clean_input_text(input: &serde_json::Map<String, Value>, field: &str) -> Option<String> {
    input.get(field).and_then(Value::as_str).and_then(|value| {
        let value = value.trim();
        (!value.is_empty()).then(|| value.to_owned())
    })
}

fn repository_error_type(error: &ShareActionRepositoryError) -> &'static str {
    match error {
        ShareActionRepositoryError::TaskNotFound => "LlmTaskError",
        ShareActionRepositoryError::OwnershipMismatch => "ownership_mismatch",
        ShareActionRepositoryError::UserMissingOrInactive => "inactive_user",
        _ => "ShareActionRepositoryError",
    }
}
