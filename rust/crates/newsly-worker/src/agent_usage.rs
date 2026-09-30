use std::sync::{Mutex, MutexGuard};

use newsly_agent_runtime::{
    AgentEvent, AgentEventSink, AgentModelUsageObservation, AgentRuntimeError,
};
use newsly_db::{NewAgentModelUsage, record_agent_model_usage};
use sqlx::PgPool;
use tokio::sync::mpsc;
use tokio::task::JoinHandle;

#[derive(Debug, Clone)]
pub(crate) struct AgentUsageAttribution {
    pub operation: String,
    pub source: String,
    pub task_id: i64,
    pub content_id: Option<i64>,
    pub session_id: Option<i64>,
    pub message_id: Option<i64>,
    pub user_id: Option<i64>,
}

/// Drains model observations into independent short transactions.
///
/// This ledger is intentionally separate from the queue's lease-fenced product finalizer: a
/// response remains billable when a later tool, validation, cancellation, or ownership check
/// prevents publication.
#[derive(Debug)]
pub(crate) struct AgentUsageRecorder {
    sender: Mutex<Option<mpsc::UnboundedSender<AgentModelUsageObservation>>>,
    task: Mutex<Option<JoinHandle<()>>>,
}

#[derive(Debug)]
pub(crate) struct AgentUsageSink {
    recorder: AgentUsageRecorder,
}

impl AgentUsageSink {
    pub(crate) fn new(pool: PgPool, attribution: AgentUsageAttribution) -> Self {
        Self {
            recorder: AgentUsageRecorder::new(pool, attribution),
        }
    }

    pub(crate) async fn finish(&self) {
        self.recorder.finish().await;
    }
}

impl AgentEventSink for AgentUsageSink {
    fn publish(&self, event: AgentEvent) -> Result<(), AgentRuntimeError> {
        if let AgentEvent::Usage { observation } = event {
            self.recorder.publish(*observation)?;
        }
        Ok(())
    }
}

impl AgentUsageRecorder {
    pub(crate) fn new(pool: PgPool, attribution: AgentUsageAttribution) -> Self {
        let (sender, mut receiver) = mpsc::unbounded_channel::<AgentModelUsageObservation>();
        let task = tokio::spawn(async move {
            while let Some(observation) = receiver.recv().await {
                for attempt in 1..=3 {
                    let result = persist(&pool, &attribution, &observation).await;
                    match result {
                        Ok(()) => break,
                        Err(error) if attempt < 3 => {
                            tracing::warn!(
                                task_id = attribution.task_id,
                                sequence = observation.sequence,
                                attempt,
                                error = %error,
                                "agent model usage persistence will retry"
                            );
                            tokio::time::sleep(std::time::Duration::from_millis(25)).await;
                        }
                        Err(error) => {
                            tracing::error!(
                                task_id = attribution.task_id,
                                sequence = observation.sequence,
                                provider = observation.provider,
                                model = observation.model,
                                error = %error,
                                "agent model usage persistence exhausted retries"
                            );
                        }
                    }
                }
            }
        });
        Self {
            sender: Mutex::new(Some(sender)),
            task: Mutex::new(Some(task)),
        }
    }

    pub(crate) fn publish(
        &self,
        observation: AgentModelUsageObservation,
    ) -> Result<(), AgentRuntimeError> {
        lock(&self.sender)
            .as_ref()
            .ok_or_else(|| AgentRuntimeError::EventSink("usage recorder is closed".to_owned()))?
            .send(observation)
            .map_err(|_| AgentRuntimeError::EventSink("usage recorder task stopped".to_owned()))
    }

    pub(crate) async fn finish(&self) {
        lock(&self.sender).take();
        let task = lock(&self.task).take();
        if let Some(task) = task
            && let Err(error) = task.await
        {
            tracing::warn!(error = %error, "agent usage recorder task failed");
        }
    }
}

async fn persist(
    pool: &PgPool,
    attribution: &AgentUsageAttribution,
    observation: &AgentModelUsageObservation,
) -> Result<(), newsly_db::AgentUsageRepositoryError> {
    let mut transaction = pool.begin().await?;
    record_agent_model_usage(
        &mut transaction,
        &NewAgentModelUsage {
            observation,
            operation: &attribution.operation,
            source: &attribution.source,
            task_id: Some(attribution.task_id),
            content_id: attribution.content_id,
            session_id: attribution.session_id,
            message_id: attribution.message_id,
            user_id: attribution.user_id,
        },
    )
    .await?;
    transaction.commit().await?;
    Ok(())
}

fn lock<T>(mutex: &Mutex<T>) -> MutexGuard<'_, T> {
    mutex
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner)
}
