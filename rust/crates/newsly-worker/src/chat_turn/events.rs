use std::collections::HashMap;
use std::sync::{Mutex, MutexGuard};

use newsly_agent_runtime::{AgentEvent, AgentEventSink, AgentRuntimeError};
use newsly_db::{ChatToolProgress, write_chat_tool_progress};
use sqlx::PgPool;
use tokio::sync::mpsc;
use tokio::task::JoinHandle;
use tokio::time::{Duration, Instant};

const TOOL_PROGRESS_WRITE_INTERVAL: Duration = Duration::from_millis(250);

/// Converts synchronous Rig events into serialized, fresh-transaction progress writes.
///
/// `TextDelta` is intentionally ignored. Rig emits text for tool-bearing intermediate assistant
/// messages as well as the terminal message; publishing those deltas would expose hidden planning
/// prose. The handler writes the validated final output exactly once after the agent completes.
#[derive(Debug)]
pub(super) struct ChatEvents {
    sender: Mutex<Option<mpsc::UnboundedSender<AgentEvent>>>,
    task: Mutex<Option<JoinHandle<()>>>,
    tool_names: Mutex<Vec<String>>,
}

impl ChatEvents {
    pub(super) fn new(pool: PgPool, message_id: i64, stream_generation: i32) -> Self {
        let (sender, mut receiver) = mpsc::unbounded_channel::<AgentEvent>();
        let task = tokio::spawn(async move {
            let mut names_by_id = HashMap::<String, String>::new();
            let mut last_progress_by_id = HashMap::<String, Instant>::new();
            while let Some(event) = receiver.recv().await {
                let progress = match event {
                    AgentEvent::ToolCallStarted { id, name } => {
                        last_progress_by_id.insert(id.clone(), Instant::now());
                        names_by_id.insert(id, name.clone());
                        Some((name, "running", Some("Tool started".to_owned())))
                    }
                    AgentEvent::ToolProgress { id, text } => {
                        let now = Instant::now();
                        let should_write = last_progress_by_id.get(&id).is_none_or(|previous| {
                            now.duration_since(*previous) >= TOOL_PROGRESS_WRITE_INTERVAL
                        });
                        if should_write {
                            last_progress_by_id.insert(id.clone(), now);
                            names_by_id
                                .get(&id)
                                .cloned()
                                .map(|name| (name, "running", Some(text)))
                        } else {
                            None
                        }
                    }
                    AgentEvent::ToolCallFinished { id, is_error } => {
                        last_progress_by_id.remove(&id);
                        names_by_id
                            .remove(&id)
                            .map(|name| (name, if is_error { "failed" } else { "completed" }, None))
                    }
                    AgentEvent::TextDelta { .. }
                    | AgentEvent::ModelRequestStarted { .. }
                    | AgentEvent::Usage { .. }
                    | AgentEvent::Completed => None,
                };
                let Some((tool_name, status, detail)) = progress else {
                    continue;
                };
                let mut transaction = match pool.begin().await {
                    Ok(transaction) => transaction,
                    Err(error) => {
                        tracing::warn!(message_id, error = %error, "chat tool progress checkout failed");
                        continue;
                    }
                };
                let result = write_chat_tool_progress(
                    &mut transaction,
                    message_id,
                    stream_generation,
                    &ChatToolProgress {
                        tool_name: &tool_name,
                        status,
                        detail: detail.as_deref(),
                    },
                )
                .await;
                match result {
                    Ok(outcome) => {
                        if let Err(error) = transaction.commit().await {
                            tracing::warn!(message_id, error = %error, "chat tool progress commit failed");
                            continue;
                        }
                        if matches!(
                            outcome,
                            newsly_db::ChatAdvisoryWriteOutcome::Superseded
                                | newsly_db::ChatAdvisoryWriteOutcome::Terminal
                                | newsly_db::ChatAdvisoryWriteOutcome::Missing
                        ) {
                            break;
                        }
                    }
                    Err(error) => {
                        tracing::warn!(message_id, error = %error, "chat tool progress write failed");
                    }
                }
            }
        });
        Self {
            sender: Mutex::new(Some(sender)),
            task: Mutex::new(Some(task)),
            tool_names: Mutex::new(Vec::new()),
        }
    }

    pub(super) fn tool_names(&self) -> Vec<String> {
        lock(&self.tool_names).clone()
    }

    pub(super) async fn finish(&self) {
        lock(&self.sender).take();
        let task = lock(&self.task).take();
        if let Some(task) = task
            && let Err(error) = task.await
        {
            tracing::warn!(error = %error, "chat tool progress task failed");
        }
    }
}

impl AgentEventSink for ChatEvents {
    fn publish(&self, event: AgentEvent) -> Result<(), AgentRuntimeError> {
        if let AgentEvent::ToolCallStarted { name, .. } = &event {
            let mut names = lock(&self.tool_names);
            if !names.contains(name) {
                names.push(name.clone());
            }
        }
        lock(&self.sender)
            .as_ref()
            .ok_or_else(|| AgentRuntimeError::EventSink("chat event sink is closed".to_owned()))?
            .send(event)
            .map_err(|_| AgentRuntimeError::EventSink("chat event queue is closed".to_owned()))
    }
}

fn lock<T>(mutex: &Mutex<T>) -> MutexGuard<'_, T> {
    mutex
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner)
}
