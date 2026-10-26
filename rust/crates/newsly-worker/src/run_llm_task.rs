use std::error::Error;
use std::sync::Arc;

use newsly_db::{
    LlmTaskDispatchKind, LlmTaskDispatchRepositoryError, UnsupportedLlmTaskOutcome,
    classify_llm_task, fail_unsupported_llm_task,
};
use newsly_queue::{LlmTaskDeliveryKind, OwnedWorkPlan, TaskResult, TaskType};
use serde_json::Value;
use sqlx::{PgPool, Postgres, Transaction};

use crate::learning_deck::LearningDeckTaskExecutor;
use crate::share_actions::ShareActionTaskExecutor;
use crate::{
    HandlerExecution, HandlerFinalizerFuture, HandlerFuture, HandlerInterruptionPolicy,
    LeaseHealth, TaskFinalizer, TaskFinalizerResult, TaskHandler,
};

/// Sole Rust owner of the shared `run_llm_task` queue namespace.
///
/// Product executors remain separate because Share Actions and Learning Decks have independent
/// persistence and artifact contracts. This dispatcher performs only bounded classification and
/// never keeps its transaction alive while either product runs E2B/Rig.
#[derive(Debug, Clone)]
pub struct RunLlmTaskHandler {
    pool: PgPool,
    share_actions: ShareActionTaskExecutor,
    learning_decks: LearningDeckTaskExecutor,
}

impl RunLlmTaskHandler {
    pub const fn new(
        pool: PgPool,
        share_actions: ShareActionTaskExecutor,
        learning_decks: LearningDeckTaskExecutor,
    ) -> Self {
        Self {
            pool,
            share_actions,
            learning_decks,
        }
    }

    async fn execute_inner(
        &self,
        plan: Arc<OwnedWorkPlan>,
        lease: LeaseHealth,
    ) -> HandlerExecution {
        let Some(task_id) = positive_payload_id(&plan.payload, "llm_task_id") else {
            return HandlerExecution::from_result(TaskResult::fail(
                Some("Missing or invalid llm_task_id in run_llm_task payload".to_owned()),
                false,
            ));
        };
        let Some(user_id) = positive_payload_id(&plan.payload, "user_id") else {
            return HandlerExecution::from_result(TaskResult::fail(
                Some("Missing or invalid user_id in run_llm_task payload".to_owned()),
                false,
            ));
        };
        if plan.owner_user_id != Some(user_id) {
            return HandlerExecution::from_result(TaskResult::fail(
                Some("LLM task ownership mismatch".to_owned()),
                false,
            ));
        }
        let kind = match classify_delivery(&self.pool, task_id, user_id, &plan.payload).await {
            Ok(kind) => kind,
            Err(result) => return HandlerExecution::from_result(result),
        };
        match kind {
            LlmTaskDispatchKind::Terminal => HandlerExecution::from_result(TaskResult::ok()),
            LlmTaskDispatchKind::ShareAction => {
                self.share_actions
                    .execute(&plan, lease, task_id, user_id)
                    .await
            }
            LlmTaskDispatchKind::LearningDeck => {
                Box::pin(self.learning_decks.execute(&plan, lease, task_id, user_id)).await
            }
            LlmTaskDispatchKind::Unsupported { task_kind } => {
                unsupported_execution(task_id, user_id, &task_kind)
            }
        }
    }
}

// Complete the bounded database/category check before dispatch can reach a provider executor.
async fn classify_delivery(
    pool: &PgPool,
    task_id: i64,
    user_id: i64,
    payload: &serde_json::Map<String, Value>,
) -> Result<LlmTaskDispatchKind, TaskResult> {
    let classification = async {
        let mut transaction = pool.begin().await?;
        let kind = classify_llm_task(&mut transaction, task_id, user_id).await?;
        transaction.commit().await?;
        Ok::<_, LlmTaskDispatchRepositoryError>(kind)
    }
    .await;
    let kind = classification.map_err(|error| match error {
        LlmTaskDispatchRepositoryError::Sqlx(error) => {
            TaskResult::fail(Some(error.to_string()), true)
        }
        error => TaskResult::fail(Some(error.to_string()), false),
    })?;
    let expected = match &kind {
        LlmTaskDispatchKind::LearningDeck => Some(LlmTaskDeliveryKind::LearningDeck),
        LlmTaskDispatchKind::ShareAction => Some(LlmTaskDeliveryKind::ShareAction),
        LlmTaskDispatchKind::Terminal | LlmTaskDispatchKind::Unsupported { .. } => None,
    };
    if let Some(expected) = expected
        && payload.get("task_kind").and_then(Value::as_str) != Some(expected.as_str())
    {
        // Durable data must agree with the category used by the queue's owner fence.
        return Err(TaskResult::fail(
            Some("LLM task delivery category does not match its workflow".to_owned()),
            false,
        ));
    }
    Ok(kind)
}

impl TaskHandler for RunLlmTaskHandler {
    fn task_type(&self) -> TaskType {
        TaskType::RunLlmTask
    }

    fn interruption_policy(&self) -> HandlerInterruptionPolicy {
        HandlerInterruptionPolicy::DrainBounded
    }

    fn execute(&self, plan: Arc<OwnedWorkPlan>, lease: LeaseHealth) -> HandlerFuture<'_> {
        Box::pin(self.execute_inner(plan, lease))
    }
}

pub(crate) fn unsupported_execution(
    task_id: i64,
    user_id: i64,
    task_kind: &str,
) -> HandlerExecution {
    let message = format!("Unsupported LLM task kind: {task_kind}");
    HandlerExecution::with_finalizer(
        TaskResult::fail(Some(message), false),
        UnsupportedLlmTaskFinalizer { task_id, user_id },
    )
}

#[derive(Debug)]
struct UnsupportedLlmTaskFinalizer {
    task_id: i64,
    user_id: i64,
}

impl TaskFinalizer for UnsupportedLlmTaskFinalizer {
    fn apply<'a>(
        &'a self,
        transaction: &'a mut Transaction<'static, Postgres>,
    ) -> HandlerFinalizerFuture<'a> {
        Box::pin(async move {
            match fail_unsupported_llm_task(transaction, self.task_id, self.user_id).await {
                Ok(UnsupportedLlmTaskOutcome::Failed { message }) => Ok(
                    TaskFinalizerResult::Override(TaskResult::fail(Some(message), false)),
                ),
                Ok(UnsupportedLlmTaskOutcome::Terminal) => {
                    Ok(TaskFinalizerResult::Override(TaskResult::ok()))
                }
                Err(error) => Err(Box::new(error) as Box<dyn Error + Send + Sync>),
            }
        })
    }
}

fn positive_payload_id(payload: &serde_json::Map<String, Value>, field: &str) -> Option<i64> {
    payload
        .get(field)
        .and_then(Value::as_i64)
        .filter(|value| *value > 0)
}

#[cfg(test)]
#[path = "run_llm_task_tests.rs"]
mod tests;
