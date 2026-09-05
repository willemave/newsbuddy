use super::{
    ClaimedTask, FinalizationOutcome, Postgres, QueueError, QueueModelError, ResolvedFinalization,
    TaskResult, TaskTransition, Transaction, finalize_resolved,
};

/// A short-lived capability proving that the exact claim still owns its live lease.
///
/// The queue row is held under `FOR UPDATE` until [`Self::finish`]. Product persistence and
/// downstream enqueueing may use [`Self::transaction_mut`], but commit remains owned by this
/// capability so those writes cannot escape without the corresponding queue transition.
pub struct FencedFinalization {
    pub(super) transaction: Transaction<'static, Postgres>,
    pub(super) claim: ClaimedTask,
    pub(super) resolved: ResolvedFinalization,
}

impl std::fmt::Debug for FencedFinalization {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("FencedFinalization")
            .field("task_id", &self.claim.id)
            .field("outcome", &self.resolved.outcome)
            .finish_non_exhaustive()
    }
}

impl FencedFinalization {
    /// Exposes only the already-fenced transaction. Dropping this capability rolls it back.
    pub fn transaction_mut(&mut self) -> &mut Transaction<'static, Postgres> {
        &mut self.transaction
    }

    /// Re-resolves the queue transition after bounded product persistence discovers that the
    /// external-work result cannot be published as originally reported.
    ///
    /// The exact claim remains locked by this capability, so replacing the result cannot race a
    /// retry or a different runtime owner. This is intentionally limited to the finalization
    /// transaction; handlers must not use it to retry external work or to bypass lease fencing.
    ///
    /// # Errors
    ///
    /// Returns a model error when the replacement result is internally inconsistent.
    pub fn replace_result(
        &mut self,
        result: &TaskResult,
        max_retries: i32,
    ) -> Result<(), QueueModelError> {
        self.resolved = ResolvedFinalization::from_result(&self.claim, result, max_retries)?;
        Ok(())
    }

    pub fn terminal_failure(&self) -> Option<&str> {
        (self.resolved.outcome == FinalizationOutcome::Failed).then(|| {
            self.resolved
                .error_message
                .as_deref()
                .unwrap_or("Task failed")
        })
    }

    /// Commits intermediate output without completing the task, under the same exact lease.
    ///
    /// # Errors
    /// Returns a database error; an expired lease rolls back the checkpoint.
    pub async fn checkpoint(mut self) -> Result<bool, QueueError> {
        let live: bool = sqlx::query_scalar(
            "SELECT lease_expires_at > timezone('UTC', clock_timestamp()) FROM processing_tasks WHERE id::bigint = $1",
        ).bind(self.claim.id).fetch_one(&mut *self.transaction).await?;
        if !live {
            return Ok(false);
        }
        self.transaction.commit().await?;
        Ok(true)
    }

    /// Applies the queue transition and commits it with every caller-added write.
    ///
    /// A lease can expire while a caller performs its bounded persistence. In that case the final
    /// compare-and-set returns `None` and dropping the transaction rolls every product write back.
    ///
    /// # Errors
    ///
    /// Returns a database or durable-value error. The transaction rolls back on every error.
    pub async fn finish(mut self) -> Result<Option<TaskTransition>, QueueError> {
        let transition =
            finalize_resolved(&mut self.transaction, &self.claim, &self.resolved).await?;
        let Some(transition) = transition else {
            return Ok(None);
        };
        self.transaction.commit().await?;
        Ok(Some(transition))
    }
}
