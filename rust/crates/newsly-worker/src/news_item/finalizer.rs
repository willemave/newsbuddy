use std::error::Error;

use newsly_queue::{QueueKernel, TaskResult};
use sqlx::{Postgres, Transaction};

use crate::{HandlerFinalizerFuture, TaskFinalizer, TaskFinalizerResult};

use super::model::{EnrichmentFinalizationPlan, NewsApplyOutcome, ProcessFinalizationPlan};
use super::repository::{apply_enrichment, apply_processing};

#[derive(Debug, Clone)]
pub(super) struct EnrichmentFinalizer {
    queue: QueueKernel,
    plan: EnrichmentFinalizationPlan,
}

impl EnrichmentFinalizer {
    pub(super) const fn new(queue: QueueKernel, plan: EnrichmentFinalizationPlan) -> Self {
        Self { queue, plan }
    }
}

impl TaskFinalizer for EnrichmentFinalizer {
    fn apply<'a>(
        &'a self,
        transaction: &'a mut Transaction<'static, Postgres>,
    ) -> HandlerFinalizerFuture<'a> {
        Box::pin(async move {
            let result = match apply_enrichment(transaction, &self.queue, &self.plan).await? {
                NewsApplyOutcome::Applied => TaskFinalizerResult::Keep,
                NewsApplyOutcome::NewsItemMissing => {
                    TaskFinalizerResult::Override(TaskResult::fail(
                        Some("news item disappeared before finalization".to_owned()),
                        false,
                    ))
                }
                NewsApplyOutcome::SourceChanged | NewsApplyOutcome::CandidateChanged => {
                    TaskFinalizerResult::Override(TaskResult::defer(1))
                }
            };
            Ok::<_, Box<dyn Error + Send + Sync>>(result)
        })
    }
}

#[derive(Debug, Clone)]
pub(super) struct ProcessNewsFinalizer {
    queue: QueueKernel,
    plan: ProcessFinalizationPlan,
}

impl ProcessNewsFinalizer {
    pub(super) const fn new(queue: QueueKernel, plan: ProcessFinalizationPlan) -> Self {
        Self { queue, plan }
    }
}

impl TaskFinalizer for ProcessNewsFinalizer {
    fn apply<'a>(
        &'a self,
        transaction: &'a mut Transaction<'static, Postgres>,
    ) -> HandlerFinalizerFuture<'a> {
        Box::pin(async move {
            let result = match apply_processing(transaction, &self.queue, &self.plan).await? {
                NewsApplyOutcome::Applied => TaskFinalizerResult::Keep,
                NewsApplyOutcome::NewsItemMissing => {
                    TaskFinalizerResult::Override(TaskResult::fail(
                        Some("news item disappeared before finalization".to_owned()),
                        false,
                    ))
                }
                NewsApplyOutcome::SourceChanged | NewsApplyOutcome::CandidateChanged => {
                    TaskFinalizerResult::Override(TaskResult::defer(1))
                }
            };
            Ok::<_, Box<dyn Error + Send + Sync>>(result)
        })
    }
}
