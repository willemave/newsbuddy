use std::env;
use std::sync::Arc;

use anyhow::{Context, Result};
use newsly_db::Database;
use newsly_queue::{QueueKernel, TaskType};
use newsly_worker::HandlerRegistry;
use newsly_worker::learning_deck::LearningDeckTaskExecutor;
use newsly_worker::process::{
    QueueWorkerRunConfig, initialize_observability, notification_database_url, run_queue_worker,
};
use newsly_worker::queue_process_config::QueueWorkerProcessConfig;
use newsly_worker::run_llm_task::RunLlmTaskHandler;
use newsly_worker::share_actions::{ShareActionAgentRuntime, ShareActionTaskExecutor};

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
    run_queue_worker(
        database,
        queue,
        handlers,
        notification_database_url(process.database_url()),
        QueueWorkerRunConfig {
            process_name: "Newsly Rust run-llm-task worker",
            worker_id: &process.worker_id,
            lease_duration: process.lease_duration,
            max_retries: process.max_retries,
            task_types: &[TaskType::RunLlmTask],
            task_type_filter: Some(TaskType::RunLlmTask),
        },
    )
    .await
}

fn sandbox_root() -> String {
    env::var("LLM_TASK_SANDBOX_ROOT")
        .ok()
        .map(|value| value.trim().to_owned())
        .filter(|value| value.starts_with('/') && value != "/")
        .unwrap_or_else(|| DEFAULT_SANDBOX_ROOT.to_owned())
}
