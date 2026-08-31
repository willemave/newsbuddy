use std::sync::Arc;

use newsly_agent_runtime::{NewslyTranscript, ProviderUsage};
use newsly_db::{
    ChatAdvisoryWriteOutcome, ChatTaskPreparationOutcome, ChatTaskRejection, ChatTaskSnapshot,
    PrepareChatTask, QueuedChatTaskKind, cancel_chat_llm_task_attempt, prepare_chat_task,
    write_chat_partial,
};
use newsly_providers::OpenAiBackgroundResponses;
use newsly_queue::{OwnedWorkPlan, TaskResult, TaskType};
use sqlx::PgPool;
use tokio_util::sync::CancellationToken;

use crate::{HandlerExecution, HandlerFuture, LeaseHealth, TaskHandler};

use super::agent::{ChatAgentError, ChatAgentRuntime};
use super::deep_research::{DeepResearchError, run_deep_research};
use super::finalizer::{ChatFailureFinalizer, ChatSuccessFinalizer};

const CHAT_FAILURE_MESSAGE: &str = "This chat turn could not be completed. Please retry.";
const DEEP_RESEARCH_FAILURE_MESSAGE: &str =
    "This research turn could not be completed. Please retry.";
const DIG_DEEPER_FAILURE_MESSAGE: &str = "Dig-deeper chat turn failed";

#[derive(Debug, Clone)]
pub struct ChatTaskServices {
    pool: PgPool,
    queue: newsly_queue::QueueKernel,
    agent: Arc<ChatAgentRuntime>,
    deep_research: Arc<dyn OpenAiBackgroundResponses>,
    max_retries: i32,
}

impl ChatTaskServices {
    pub fn new(
        pool: PgPool,
        queue: newsly_queue::QueueKernel,
        agent: Arc<ChatAgentRuntime>,
        deep_research: Arc<dyn OpenAiBackgroundResponses>,
        max_retries: i32,
    ) -> Self {
        Self {
            pool,
            queue,
            agent,
            deep_research,
            max_retries: max_retries.max(0),
        }
    }
}

#[derive(Debug, Clone)]
pub struct ChatPartitionHandler {
    services: Arc<ChatTaskServices>,
    kind: QueuedChatTaskKind,
}

impl ChatPartitionHandler {
    pub fn chat_turn(services: Arc<ChatTaskServices>) -> Self {
        Self {
            services,
            kind: QueuedChatTaskKind::ChatTurn,
        }
    }

    pub fn dig_deeper(services: Arc<ChatTaskServices>) -> Self {
        Self {
            services,
            kind: QueuedChatTaskKind::DigDeeper,
        }
    }

    async fn execute_inner(
        &self,
        plan: &OwnedWorkPlan,
        mut lease: LeaseHealth,
    ) -> HandlerExecution {
        let Some(owner_user_id) = plan.owner_user_id.filter(|value| *value > 0) else {
            return failure("Queued chat work requires a positive owner_user_id", false);
        };
        let snapshot = {
            let mut transaction = match self.services.pool.begin().await {
                Ok(transaction) => transaction,
                Err(error) => return failure(error.to_string(), true),
            };
            let preparation = prepare_chat_task(
                &mut transaction,
                &PrepareChatTask {
                    queue_task_id: plan.task_id,
                    queue_task_kind: self.kind,
                    owner_user_id,
                    content_id: plan.content_id,
                    payload: &plan.payload,
                    stream_generation: plan.retry_count,
                    max_retries: self.services.max_retries,
                    history_message_limit: self.services.agent.config().history_message_limit,
                },
            )
            .await;
            let preparation = match preparation {
                Ok(preparation) => preparation,
                Err(error) => {
                    let retryable = matches!(error, newsly_db::ChatTaskRepositoryError::Sqlx(_));
                    return failure(error.to_string(), retryable);
                }
            };
            if let Err(error) = transaction.commit().await {
                return failure(error.to_string(), true);
            }
            match preparation {
                ChatTaskPreparationOutcome::Ready(snapshot) => snapshot,
                ChatTaskPreparationOutcome::Completed
                | ChatTaskPreparationOutcome::SkippedInactiveUser
                | ChatTaskPreparationOutcome::Superseded => {
                    return HandlerExecution::from_result(TaskResult::ok());
                }
                ChatTaskPreparationOutcome::AlreadyFailed { message } => {
                    return failure(message, false);
                }
                ChatTaskPreparationOutcome::Deferred {
                    retry_delay_seconds,
                } => {
                    return HandlerExecution::from_result(TaskResult::defer(retry_delay_seconds));
                }
                ChatTaskPreparationOutcome::Reject(rejection) => {
                    let task_message = rejection.task_message.clone();
                    return HandlerExecution::with_finalizer(
                        TaskResult::fail(Some(task_message), false),
                        ChatFailureFinalizer::new(self.services.queue.clone(), rejection),
                    );
                }
            }
        };

        if lease.ownership_lost() {
            self.cancel_attempt(
                &snapshot,
                "Chat turn cancelled before external work because queue ownership changed",
            )
            .await;
            return HandlerExecution::from_result(TaskResult::ok());
        }
        let cancellation = CancellationToken::new();
        let external_snapshot = snapshot.clone();
        let execution = self.run_external(&external_snapshot, cancellation.child_token());
        tokio::pin!(execution);
        let result = tokio::select! {
            result = &mut execution => result,
            () = lease.wait_for_ownership_loss() => {
                cancellation.cancel();
                execution.await
            }
        };
        let completed = match result {
            Ok(completed) => completed,
            Err(ChatExecutionError::OwnershipLost) => {
                self.cancel_attempt(
                    &snapshot,
                    "Chat turn cancelled during external work because queue ownership changed",
                )
                .await;
                return HandlerExecution::from_result(TaskResult::ok());
            }
            Err(ChatExecutionError::Retry(message)) => return failure(message, true),
            Err(ChatExecutionError::Terminal(message)) => {
                let rejection = rejection_for_snapshot(&snapshot, &message, self.kind);
                return HandlerExecution::with_finalizer(
                    TaskResult::fail(Some(message), false),
                    ChatFailureFinalizer::new(self.services.queue.clone(), rejection),
                );
            }
        };

        let mut transaction = match self.services.pool.begin().await {
            Ok(transaction) => transaction,
            Err(error) => {
                self.cancel_attempt(
                    &snapshot,
                    "Chat turn cancelled because its final partial could not start",
                )
                .await;
                return failure(error.to_string(), true);
            }
        };
        let partial = write_chat_partial(
            &mut transaction,
            snapshot.message_id,
            snapshot.stream_generation,
            &completed.output_text,
        )
        .await;
        let partial = match partial {
            Ok(partial) => partial,
            Err(error) => {
                self.cancel_attempt(
                    &snapshot,
                    "Chat turn cancelled because its final partial could not be written",
                )
                .await;
                return failure(error.to_string(), true);
            }
        };
        if let Err(error) = transaction.commit().await {
            self.cancel_attempt(
                &snapshot,
                "Chat turn cancelled because its final partial commit was uncertain",
            )
            .await;
            return failure(error.to_string(), true);
        }
        match partial {
            ChatAdvisoryWriteOutcome::Applied | ChatAdvisoryWriteOutcome::Unchanged => {}
            ChatAdvisoryWriteOutcome::Superseded
            | ChatAdvisoryWriteOutcome::Terminal
            | ChatAdvisoryWriteOutcome::Missing => {
                self.cancel_attempt(
                    &snapshot,
                    "Chat turn cancelled because its message became terminal or superseded",
                )
                .await;
                return HandlerExecution::from_result(TaskResult::ok());
            }
        }

        let usage_source = usage_source(&snapshot);
        HandlerExecution::with_finalizer(
            TaskResult::ok(),
            ChatSuccessFinalizer::new(
                self.services.queue.clone(),
                snapshot,
                completed.transcript,
                completed.render_metadata,
                completed.output_text,
                completed.tool_names,
                completed.model_provider,
                completed.model_name,
                completed.provider_response_id,
                completed.usage,
                usage_source,
            ),
        )
    }

    async fn run_external(
        &self,
        snapshot: &ChatTaskSnapshot,
        cancellation: CancellationToken,
    ) -> Result<CompletedChatWork, ChatExecutionError> {
        if snapshot.context.kind == newsly_db::ChatTurnKind::DeepResearch {
            return match run_deep_research(
                &self.services.agent,
                Arc::clone(&self.services.deep_research),
                &self.services.pool,
                snapshot,
                cancellation,
            )
            .await
            {
                Ok(run) => Ok(CompletedChatWork {
                    output_text: run.output_text,
                    transcript: run.turn_transcript,
                    render_metadata: None,
                    tool_names: Vec::new(),
                    model_provider: "deep_research".to_owned(),
                    model_name: run.model_name,
                    provider_response_id: Some(run.provider_response_id),
                    usage: run.usage,
                }),
                Err(DeepResearchError::Cancelled | DeepResearchError::OwnershipLost) => {
                    Err(ChatExecutionError::OwnershipLost)
                }
                Err(DeepResearchError::PollingBudgetExhausted) => Err(ChatExecutionError::Retry(
                    "Deep research is still running".to_owned(),
                )),
                Err(error) => Err(ChatExecutionError::Terminal(error.to_string())),
            };
        }
        match self.services.agent.run(snapshot, cancellation).await {
            Ok(run) => Ok(CompletedChatWork {
                output_text: run.outcome.output_text.clone(),
                transcript: run.turn_transcript,
                render_metadata: run.render_metadata,
                tool_names: run.tool_names,
                model_provider: run.model_provider,
                model_name: run.outcome.model_name.clone(),
                provider_response_id: run.outcome.provider_response_id.clone(),
                usage: run.outcome.usage.clone(),
            }),
            Err(ChatAgentError::Cancelled) => Err(ChatExecutionError::OwnershipLost),
            Err(error) => Err(ChatExecutionError::Terminal(error.to_string())),
        }
    }

    async fn cancel_attempt(&self, snapshot: &ChatTaskSnapshot, note: &str) {
        let Some(task_id) = snapshot.llm_task_id else {
            return;
        };
        let result = async {
            let mut transaction = self.services.pool.begin().await?;
            cancel_chat_llm_task_attempt(&mut transaction, task_id, snapshot.user_id, note).await?;
            transaction.commit().await?;
            Ok::<(), newsly_db::ChatTaskRepositoryError>(())
        }
        .await;
        if let Err(error) = result {
            tracing::warn!(
                llm_task_id = task_id,
                message_id = snapshot.message_id,
                error = %error,
                "could not cancel detached chat LLM attempt"
            );
        }
    }
}

impl TaskHandler for ChatPartitionHandler {
    fn task_type(&self) -> TaskType {
        match self.kind {
            QueuedChatTaskKind::ChatTurn => TaskType::ChatTurn,
            QueuedChatTaskKind::DigDeeper => TaskType::DigDeeper,
        }
    }

    fn execute(&self, plan: Arc<OwnedWorkPlan>, lease: LeaseHealth) -> HandlerFuture<'_> {
        Box::pin(async move { self.execute_inner(&plan, lease).await })
    }
}

#[derive(Debug)]
struct CompletedChatWork {
    output_text: String,
    transcript: NewslyTranscript,
    render_metadata: Option<serde_json::Value>,
    tool_names: Vec<String>,
    model_provider: String,
    model_name: String,
    provider_response_id: Option<String>,
    usage: ProviderUsage,
}

#[derive(Debug)]
enum ChatExecutionError {
    OwnershipLost,
    Retry(String),
    Terminal(String),
}

fn rejection_for_snapshot(
    snapshot: &ChatTaskSnapshot,
    message: &str,
    kind: QueuedChatTaskKind,
) -> ChatTaskRejection {
    let public_message = if snapshot.context.kind == newsly_db::ChatTurnKind::DeepResearch {
        DEEP_RESEARCH_FAILURE_MESSAGE
    } else if kind == QueuedChatTaskKind::DigDeeper {
        DIG_DEEPER_FAILURE_MESSAGE
    } else {
        CHAT_FAILURE_MESSAGE
    };
    ChatTaskRejection {
        message_id: Some(snapshot.message_id),
        session_id: Some(snapshot.session_id),
        user_id: snapshot.user_id,
        llm_task_id: snapshot.llm_task_id,
        expected_stream_generation: Some(snapshot.stream_generation),
        public_message: public_message.to_owned(),
        task_message: message.to_owned(),
        error_type: "ChatExecutionError".to_owned(),
    }
}

fn failure(message: impl Into<String>, retryable: bool) -> HandlerExecution {
    HandlerExecution::from_result(TaskResult::fail(Some(message.into()), retryable))
}

fn usage_source(snapshot: &ChatTaskSnapshot) -> String {
    match snapshot.context.kind {
        newsly_db::ChatTurnKind::Article | newsly_db::ChatTurnKind::Council => "async".to_owned(),
        newsly_db::ChatTurnKind::Assistant => snapshot.context.source.clone(),
        newsly_db::ChatTurnKind::DeepResearch => "deep_research".to_owned(),
    }
}
