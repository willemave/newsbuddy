use std::error::Error;

use chrono::{Duration, Utc};
use newsly_db::{BriefingRefreshConfig, BriefingRefreshPublication, apply_briefing_refresh};
use newsly_queue::{EnqueueRequest, QueueKernel, TaskType};
use serde_json::{Map, Value};
use sqlx::{Postgres, Transaction};

use crate::{HandlerFinalizerFuture, TaskFinalizer, TaskFinalizerResult};

#[derive(Debug, Clone)]
pub(super) struct BriefingRefreshFinalizer {
    queue: QueueKernel,
    publication: BriefingRefreshPublication,
    config: BriefingRefreshConfig,
}

impl BriefingRefreshFinalizer {
    pub(super) const fn new(
        queue: QueueKernel,
        publication: BriefingRefreshPublication,
        config: BriefingRefreshConfig,
    ) -> Self {
        Self {
            queue,
            publication,
            config,
        }
    }

    async fn apply_inner(
        &self,
        transaction: &mut Transaction<'static, Postgres>,
    ) -> Result<TaskFinalizerResult, Box<dyn Error + Send + Sync>> {
        let outcome = apply_briefing_refresh(transaction, &self.publication, &self.config).await?;
        let user_id = self.publication.prepared.user_id;
        let requests = vec![sweep_request(user_id, outcome.next_sweep_delay_seconds)];
        self.queue
            .enqueue_many_in_transaction(transaction, requests)
            .await?;
        tracing::info!(
            task_id = self.publication.prepared.task_id,
            user_id,
            mode = self.publication.prepared.mode.as_str(),
            version = outcome.version,
            appended_segments = outcome.appended_segments,
            compacted_segments = outcome.compacted_segments,
            retired_segments = outcome.retired_segments,
            stale = outcome.stale,
            next_sweep_delay_seconds = outcome.next_sweep_delay_seconds,
            "Briefing refresh finalized behind the exact queue lease"
        );
        Ok(TaskFinalizerResult::Keep)
    }
}

impl TaskFinalizer for BriefingRefreshFinalizer {
    fn apply<'a>(
        &'a self,
        transaction: &'a mut Transaction<'static, Postgres>,
    ) -> HandlerFinalizerFuture<'a> {
        Box::pin(async move { self.apply_inner(transaction).await })
    }
}

fn sweep_request(user_id: i64, delay_seconds: i64) -> EnqueueRequest {
    let mut request = EnqueueRequest::new(TaskType::BriefingRefresh);
    request.payload = Some(Map::from_iter([
        ("user_id".to_owned(), Value::from(user_id)),
        ("mode".to_owned(), Value::from("sweep")),
    ]));
    request.owner_user_id = Some(user_id);
    request.dedupe = Some(true);
    request.dedupe_key = Some(format!("briefing_refresh:{user_id}:sweep"));
    request.available_at = Some(Utc::now() + Duration::seconds(delay_seconds.clamp(0, 86_400)));
    request
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn sweep_request_is_owned_and_deduped() {
        let request = sweep_request(42, 90);
        assert_eq!(request.task_type, TaskType::BriefingRefresh);
        assert_eq!(request.owner_user_id, Some(42));
        assert_eq!(
            request.dedupe_key.as_deref(),
            Some("briefing_refresh:42:sweep")
        );
        assert_eq!(
            request
                .payload
                .as_ref()
                .and_then(|payload| payload.get("mode"))
                .and_then(Value::as_str),
            Some("sweep")
        );
    }
}
