use std::env;
use std::sync::Arc;

use anyhow::{Context, Result};
use newsly_db::Database;
use newsly_domain::{ResourceKey, RuntimeOwner};
use newsly_queue::{
    ClaimRequest, ClaimRuntimeScope, QueueKernel, QueueNotificationHub, TaskQueue, TaskType,
};
use newsly_worker::learning_deck::LearningDeckTaskExecutor;
use newsly_worker::process::{
    initialize_observability, notification_database_url, spawn_shutdown_signal,
};
use newsly_worker::queue_process_config::QueueWorkerProcessConfig;
use newsly_worker::run_llm_task::RunLlmTaskHandler;
use newsly_worker::share_actions::{ShareActionAgentRuntime, ShareActionTaskExecutor};
use newsly_worker::{HandlerRegistry, WorkerConfig, WorkerKernel};

const DEFAULT_SANDBOX_ROOT: &str = "/data/workspace";

#[tokio::main]
pub(crate) async fn main() -> Result<()> {
    let process =
        QueueWorkerProcessConfig::from_env("newsly-run-llm-task-worker", "rust-run-llm-task")
            .context("invalid Newsly Rust run-llm-task worker process configuration")?;
    initialize_observability(&process.log_filter, process.log_format)
        .context("run-llm-task worker observability initialization failed")?;

    let database = Database::connect_lazy(&process.database)
        .context("run-llm-task worker database configuration failed")?;
    database
        .check()
        .await
        .context("run-llm-task worker PostgreSQL readiness check failed")?;
    let queue = QueueKernel::new(database.pool().clone());
    let share_agent = Arc::new(
        ShareActionAgentRuntime::from_env(database.pool().clone())
            .context("Share Action agent initialization failed")?,
    );
    let share_actions = ShareActionTaskExecutor::new(
        database.pool().clone(),
        queue.clone(),
        share_agent,
        sandbox_root(),
        process.max_retries,
    );
    let learning_decks =
        LearningDeckTaskExecutor::from_env(database.pool().clone(), process.max_retries)
            .context("Learning Deck task executor initialization failed")?;

    let mut handlers = HandlerRegistry::new();
    handlers.register(RunLlmTaskHandler::new(
        database.pool().clone(),
        share_actions,
        learning_decks,
    ))?;
    let scope = ClaimRuntimeScope::namespaces(
        RuntimeOwner::Rust,
        [ResourceKey::new(TaskType::RunLlmTask.as_str())?],
    )?;
    let mut claim = ClaimRequest::for_queue(process.worker_id.clone(), TaskQueue::Llm, scope);
    claim.task_type = Some(TaskType::RunLlmTask);
    claim.lease_duration = process.lease_duration;
    let mut worker_config = WorkerConfig::new(claim);
    worker_config.max_retries = process.max_retries;

    let notification_hub =
        QueueNotificationHub::spawn(notification_database_url(process.database_url()));
    let notifications = notification_hub.subscribe();
    let mut worker = WorkerKernel::new(queue, handlers, worker_config, Some(notifications))?;
    let (shutdown_rx, shutdown_task) = spawn_shutdown_signal();

    tracing::info!(
        worker_id = %process.worker_id,
        queue = %TaskQueue::Llm,
        task_type = %TaskType::RunLlmTask,
        "Newsly Rust run-llm-task worker started; only rows stamped for the Rust runtime are claimable"
    );
    let run_result = worker.run(shutdown_rx).await;
    shutdown_task.abort();
    notification_hub.close().await;
    database.close().await;
    let summary = run_result.context("Newsly Rust run-llm-task worker stopped unexpectedly")?;
    tracing::info!(?summary, "Newsly Rust run-llm-task worker stopped");
    Ok(())
}

fn sandbox_root() -> String {
    env::var("LLM_TASK_SANDBOX_ROOT")
        .ok()
        .map(|value| value.trim().to_owned())
        .filter(|value| value.starts_with('/') && value != "/")
        .unwrap_or_else(|| DEFAULT_SANDBOX_ROOT.to_owned())
}
