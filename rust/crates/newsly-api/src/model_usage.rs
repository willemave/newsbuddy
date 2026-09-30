use std::sync::{Arc, Mutex, MutexGuard};

use newsly_agent_runtime::{AgentEvent, AgentEventSink, AgentRuntimeError};
use newsly_db::{NewAgentModelUsage, record_agent_model_usage};
use newsly_providers::OnboardingGateway;
use sqlx::PgPool;
use tokio::task::JoinHandle;

use crate::AppState;

pub(super) fn onboarding_gateway_with_usage(
    state: &AppState,
    user_id: Option<i64>,
    operation: &str,
) -> (OnboardingGateway, Arc<ApiAgentUsageSink>) {
    let usage = Arc::new(ApiAgentUsageSink::new(
        state.database.pool().clone(),
        user_id,
        operation,
        None,
    ));
    let events: Arc<dyn AgentEventSink> = usage.clone();
    (state.onboarding.with_events(events), usage)
}

#[derive(Debug)]
pub(super) struct ApiAgentUsageSink {
    pool: PgPool,
    operation: String,
    user_id: Option<i64>,
    content_id: Option<i64>,
    tasks: Mutex<Vec<JoinHandle<()>>>,
}

impl ApiAgentUsageSink {
    pub(super) fn new(
        pool: PgPool,
        user_id: Option<i64>,
        operation: impl Into<String>,
        content_id: Option<i64>,
    ) -> Self {
        Self {
            pool,
            operation: operation.into(),
            user_id,
            content_id,
            tasks: Mutex::new(Vec::new()),
        }
    }

    pub(super) async fn finish(&self) {
        let tasks = std::mem::take(&mut *lock(&self.tasks));
        for task in tasks {
            if let Err(error) = task.await {
                tracing::warn!(error = %error, operation = self.operation, "API agent usage task failed");
            }
        }
    }
}

impl AgentEventSink for ApiAgentUsageSink {
    fn publish(&self, event: AgentEvent) -> Result<(), AgentRuntimeError> {
        let AgentEvent::Usage { observation } = event else {
            return Ok(());
        };
        let pool = self.pool.clone();
        let operation = self.operation.clone();
        let user_id = self.user_id;
        let content_id = self.content_id;
        lock(&self.tasks).push(tokio::spawn(async move {
            let result = async {
                let mut transaction = pool.begin().await?;
                record_agent_model_usage(
                    &mut transaction,
                    &NewAgentModelUsage {
                        observation: &observation,
                        operation: &operation,
                        source: "api",
                        task_id: None,
                        content_id,
                        session_id: None,
                        message_id: None,
                        user_id,
                    },
                )
                .await?;
                transaction.commit().await?;
                Ok::<(), newsly_db::AgentUsageRepositoryError>(())
            }
            .await;
            if let Err(error) = result {
                tracing::error!(error = %error, operation, "API agent usage persistence failed");
            }
        }));
        Ok(())
    }
}

fn lock<T>(mutex: &Mutex<T>) -> MutexGuard<'_, T> {
    mutex
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner)
}
