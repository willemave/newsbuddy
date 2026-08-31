use newsly_agent_runtime::{NewslyTranscript, ProviderUsage};
use newsly_db::{
    ChatTaskRejection, ChatTaskSnapshot, ChatTerminalMutationOutcome, ChatTurnPublication,
    cancel_chat_llm_task_attempt, fail_chat_turn, publish_chat_turn, visible_council_session_id,
};
use newsly_queue::{QueueKernel, TaskResult};
use serde_json::Value;

use crate::share_actions::submission::enqueue_chat_session_sync;
use crate::{HandlerFinalizerFuture, TaskFinalizer, TaskFinalizerResult};

#[derive(Debug)]
pub(super) struct ChatSuccessFinalizer {
    queue: QueueKernel,
    snapshot: ChatTaskSnapshot,
    transcript: NewslyTranscript,
    render_metadata: Option<Value>,
    output_text: String,
    tool_names: Vec<String>,
    model_provider: String,
    model_name: String,
    provider_response_id: Option<String>,
    usage: ProviderUsage,
    usage_source: String,
}

impl ChatSuccessFinalizer {
    #[allow(clippy::too_many_arguments)]
    pub(super) fn new(
        queue: QueueKernel,
        snapshot: ChatTaskSnapshot,
        transcript: NewslyTranscript,
        render_metadata: Option<Value>,
        output_text: String,
        tool_names: Vec<String>,
        model_provider: String,
        model_name: String,
        provider_response_id: Option<String>,
        usage: ProviderUsage,
        usage_source: String,
    ) -> Self {
        Self {
            queue,
            snapshot,
            transcript,
            render_metadata,
            output_text,
            tool_names,
            model_provider,
            model_name,
            provider_response_id,
            usage,
            usage_source,
        }
    }
}

impl TaskFinalizer for ChatSuccessFinalizer {
    fn apply<'a>(
        &'a self,
        transaction: &'a mut sqlx::Transaction<'static, sqlx::Postgres>,
    ) -> HandlerFinalizerFuture<'a> {
        Box::pin(async move {
            let outcome = publish_chat_turn(
                transaction,
                &ChatTurnPublication {
                    snapshot: &self.snapshot,
                    turn_transcript: &self.transcript,
                    render_metadata: self.render_metadata.as_ref(),
                    output_text: &self.output_text,
                    tool_names: &self.tool_names,
                    model_provider: &self.model_provider,
                    model_name: &self.model_name,
                    provider_response_id: self.provider_response_id.as_deref(),
                    usage: &self.usage,
                    usage_source: &self.usage_source,
                },
            )
            .await?;
            let result = match outcome {
                ChatTerminalMutationOutcome::Applied => {
                    enqueue_chat_session_sync(
                        transaction,
                        &self.queue,
                        self.snapshot.user_id,
                        self.snapshot.session_id,
                    )
                    .await?;
                    if self.snapshot.visible_session_id != self.snapshot.session_id {
                        enqueue_chat_session_sync(
                            transaction,
                            &self.queue,
                            self.snapshot.user_id,
                            self.snapshot.visible_session_id,
                        )
                        .await?;
                    }
                    TaskFinalizerResult::Keep
                }
                ChatTerminalMutationOutcome::AlreadyCompleted
                | ChatTerminalMutationOutcome::AlreadyFailed
                | ChatTerminalMutationOutcome::Superseded
                | ChatTerminalMutationOutcome::Missing => {
                    cancel_attempt_if_present(
                        transaction,
                        &self.snapshot,
                        "Chat turn attempt ended after its message became terminal or superseded",
                    )
                    .await?;
                    if outcome == ChatTerminalMutationOutcome::AlreadyFailed {
                        TaskFinalizerResult::Override(TaskResult::fail(
                            Some("Chat message was already failed".to_owned()),
                            false,
                        ))
                    } else {
                        TaskFinalizerResult::Override(TaskResult::ok())
                    }
                }
            };
            Ok(result)
        })
    }
}

#[derive(Debug)]
pub(super) struct ChatFailureFinalizer {
    queue: QueueKernel,
    rejection: ChatTaskRejection,
}

impl ChatFailureFinalizer {
    pub(super) fn new(queue: QueueKernel, rejection: ChatTaskRejection) -> Self {
        Self { queue, rejection }
    }
}

impl TaskFinalizer for ChatFailureFinalizer {
    fn apply<'a>(
        &'a self,
        transaction: &'a mut sqlx::Transaction<'static, sqlx::Postgres>,
    ) -> HandlerFinalizerFuture<'a> {
        Box::pin(async move {
            let outcome = fail_chat_turn(transaction, &self.rejection).await?;
            if outcome == ChatTerminalMutationOutcome::Applied
                && let Some(session_id) = self.rejection.session_id
                && let Some(visible_session_id) =
                    visible_council_session_id(transaction, self.rejection.user_id, session_id)
                        .await?
            {
                enqueue_chat_session_sync(
                    transaction,
                    &self.queue,
                    self.rejection.user_id,
                    visible_session_id,
                )
                .await?;
            }
            if outcome != ChatTerminalMutationOutcome::Applied
                && let Some(task_id) = self.rejection.llm_task_id
            {
                cancel_chat_llm_task_attempt(
                    transaction,
                    task_id,
                    self.rejection.user_id,
                    "Failed chat attempt ended after its message became terminal or superseded",
                )
                .await?;
            }
            let result = match outcome {
                ChatTerminalMutationOutcome::AlreadyCompleted
                | ChatTerminalMutationOutcome::Superseded => {
                    TaskFinalizerResult::Override(TaskResult::ok())
                }
                ChatTerminalMutationOutcome::Applied
                | ChatTerminalMutationOutcome::AlreadyFailed
                | ChatTerminalMutationOutcome::Missing => TaskFinalizerResult::Keep,
            };
            Ok(result)
        })
    }
}

async fn cancel_attempt_if_present(
    transaction: &mut sqlx::Transaction<'static, sqlx::Postgres>,
    snapshot: &ChatTaskSnapshot,
    note: &str,
) -> Result<(), newsly_db::ChatTaskRepositoryError> {
    if let Some(task_id) = snapshot.llm_task_id {
        cancel_chat_llm_task_attempt(transaction, task_id, snapshot.user_id, note).await?;
    }
    Ok(())
}
