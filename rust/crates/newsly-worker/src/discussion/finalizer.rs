use std::error::Error;

use newsly_queue::TaskResult;
use sqlx::{Postgres, Transaction};

use crate::{HandlerFinalizerFuture, TaskFinalizer, TaskFinalizerResult};

use super::model::{DiscussionApplyOutcome, DiscussionFinalizationPlan};
use super::repository::apply_discussion_mutation;

#[derive(Debug, Clone)]
pub(super) struct DiscussionFinalizer {
    plan: DiscussionFinalizationPlan,
}

impl DiscussionFinalizer {
    pub(super) const fn new(plan: DiscussionFinalizationPlan) -> Self {
        Self { plan }
    }

    async fn apply_inner(
        &self,
        transaction: &mut Transaction<'static, Postgres>,
    ) -> Result<TaskFinalizerResult, Box<dyn Error + Send + Sync>> {
        let result = match apply_discussion_mutation(transaction, &self.plan).await? {
            DiscussionApplyOutcome::Applied => TaskFinalizerResult::Keep,
            DiscussionApplyOutcome::ClaimLost {
                retry_after_seconds,
            } => TaskFinalizerResult::Override(TaskResult::defer(retry_after_seconds)),
            DiscussionApplyOutcome::IdentityChanged => {
                TaskFinalizerResult::Override(TaskResult::defer(1))
            }
            DiscussionApplyOutcome::NewsItemMissing => {
                TaskFinalizerResult::Override(TaskResult::fail(
                    Some("news item disappeared before finalization".to_owned()),
                    false,
                ))
            }
        };
        Ok(result)
    }
}

impl TaskFinalizer for DiscussionFinalizer {
    fn apply<'a>(
        &'a self,
        transaction: &'a mut Transaction<'static, Postgres>,
    ) -> HandlerFinalizerFuture<'a> {
        Box::pin(async move { self.apply_inner(transaction).await })
    }
}
