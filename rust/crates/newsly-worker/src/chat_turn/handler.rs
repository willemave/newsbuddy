use std::sync::Arc;

use newsly_agent_runtime::{NewslyTranscript, ProviderUsage};
use newsly_db::{
    ChatAdvisoryWriteOutcome, ChatTaskPreparationOutcome, ChatTaskRejection, ChatTaskSnapshot,
    PrepareChatTask, QueuedChatTaskKind, cancel_chat_llm_task_attempt, prepare_chat_task,
    write_chat_partial,
};
use newsly_queue::{OwnedWorkPlan, TaskResult, TaskType};
use sqlx::PgPool;
use tokio_util::sync::CancellationToken;

use crate::{HandlerExecution, HandlerFuture, LeaseHealth, TaskHandler};

use super::agent::{ChatAgentError, ChatAgentRunKind, ChatAgentRuntime};
use super::finalizer::{ChatFailureFinalizer, ChatSuccessFinalizer};

const CHAT_FAILURE_MESSAGE: &str = "This chat turn could not be completed. Please retry.";
const DEEP_RESEARCH_FAILURE_MESSAGE: &str =
    "Dedicated Deep Research has been retired. Use regular chat.";
const DIG_DEEPER_FAILURE_MESSAGE: &str = "Dig-deeper chat turn failed";

#[derive(Debug, Clone)]
pub struct ChatTaskServices {
    pool: PgPool,
    agent: Arc<ChatAgentRuntime>,
    max_retries: i32,
}

impl ChatTaskServices {
    pub fn new(pool: PgPool, agent: Arc<ChatAgentRuntime>, max_retries: i32) -> Self {
        Self {
            pool,
            agent,
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
                        ChatFailureFinalizer::new(rejection),
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
        let execution = self.run_external(
            &external_snapshot,
            cancellation.child_token(),
            lease.claim().clone(),
        );
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
            Err(ChatExecutionError::Terminal(message)) => {
                let rejection = rejection_for_snapshot(&snapshot, &message, self.kind);
                return HandlerExecution::with_finalizer(
                    TaskResult::fail(Some(message), false),
                    ChatFailureFinalizer::new(rejection),
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

        HandlerExecution::with_finalizer(
            TaskResult::ok(),
            ChatSuccessFinalizer::new(snapshot, completed),
        )
    }

    async fn run_external(
        &self,
        snapshot: &ChatTaskSnapshot,
        cancellation: CancellationToken,
        claim: newsly_queue::ClaimedTask,
    ) -> Result<CompletedChatWork, ChatExecutionError> {
        let kind = match (retired_research(snapshot), snapshot.context.kind) {
            (false, newsly_db::ChatTurnKind::Assistant) => ChatAgentRunKind::Assistant,
            (false, newsly_db::ChatTurnKind::Article) => ChatAgentRunKind::Article,
            _ => {
                return Err(ChatExecutionError::Terminal(
                    DEEP_RESEARCH_FAILURE_MESSAGE.to_owned(),
                ));
            }
        };
        match self
            .services
            .agent
            .run(snapshot, kind, cancellation, claim)
            .await
        {
            Ok(run) => Ok(CompletedChatWork {
                output_text: run.outcome.output_text.clone(),
                transcript: run.turn_transcript,
                render_metadata: run.render_metadata,
                tool_names: run.tool_names,
                model_provider: run.model_provider,
                model_name: run.outcome.model_name.clone(),
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
        Box::pin(async move { Box::pin(self.execute_inner(&plan, lease)).await })
    }
}

#[derive(Debug)]
pub(super) struct CompletedChatWork {
    pub(super) output_text: String,
    pub(super) transcript: NewslyTranscript,
    pub(super) render_metadata: Option<serde_json::Value>,
    pub(super) tool_names: Vec<String>,
    pub(super) model_provider: String,
    pub(super) model_name: String,
    pub(super) usage: ProviderUsage,
}

#[derive(Debug)]
enum ChatExecutionError {
    OwnershipLost,
    Terminal(String),
}

fn rejection_for_snapshot(
    snapshot: &ChatTaskSnapshot,
    message: &str,
    kind: QueuedChatTaskKind,
) -> ChatTaskRejection {
    let public_message = if retired_research(snapshot) {
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

fn retired_research(snapshot: &ChatTaskSnapshot) -> bool {
    snapshot.context.kind == newsly_db::ChatTurnKind::DeepResearch
        || newsly_domain::is_retired_deep_research(
            Some(&snapshot.context.session.provider),
            snapshot.context.session.session_type.as_deref(),
            Some(&snapshot.context.session.model),
        )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ordinary_turns_with_historical_research_models_are_rejected() {
        let mut snapshot = ChatTaskSnapshot {
            queue_task_id: 1,
            user_id: 2,
            session_id: 3,
            message_id: 4,
            stream_generation: 1,
            context: serde_json::from_value(serde_json::json!({
                "version": 1, "kind": "article", "user_prompt": "Continue", "source": "chat",
                "session": {
                    "user_id": 2, "effective_session_id": 3, "visible_session_id": 3,
                    "provider": "openai", "model": " OPENAI:O4-MINI-DEEP-RESEARCH ",
                    "session_type": "knowledge_chat"
                }
            }))
            .unwrap(),
            history: NewslyTranscript::default(),
            llm_task_id: None,
            encrypted_provider_key: None,
            content: None,
            workspace_path: "/workspace/task".to_owned(),
        };
        assert!(retired_research(&snapshot));
        let rejection =
            rejection_for_snapshot(&snapshot, "retired research", QueuedChatTaskKind::ChatTurn);
        assert_eq!(rejection.public_message, DEEP_RESEARCH_FAILURE_MESSAGE);
        assert_eq!(rejection.expected_stream_generation, Some(1));

        snapshot.context.session.model = "openai:gpt-6.1-sol".to_owned();
        assert!(!retired_research(&snapshot));
        snapshot.context.kind = newsly_db::ChatTurnKind::DeepResearch;
        assert!(retired_research(&snapshot));
    }
}
