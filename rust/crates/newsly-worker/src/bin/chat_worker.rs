use std::sync::Arc;

use anyhow::{Context, Result};
use newsly_db::Database;
use newsly_queue::{QueueKernel, TaskType};
use newsly_worker::HandlerRegistry;
use newsly_worker::chat_turn::{ChatAgentRuntime, ChatPartitionHandler, ChatTaskServices};
use newsly_worker::process::{
    QueueWorkerRunConfig, initialize_observability, notification_database_url, run_queue_worker,
};
use newsly_worker::queue_process_config::QueueWorkerProcessConfig;

#[tokio::main]
pub(crate) async fn main() -> Result<()> {
    let process = QueueWorkerProcessConfig::from_env("newsly-chat-worker", "rust-chat")
        .context("invalid Newsly Rust chat-worker process configuration")?;
    initialize_observability(&process.log_filter, process.log_format)
        .context("chat-worker observability initialization failed")?;

    let database = Database::connect_lazy(&process.database)
        .context("chat-worker database configuration failed")?;
    database
        .check()
        .await
        .context("chat-worker PostgreSQL readiness check failed")?;
    let queue = QueueKernel::new(database.pool().clone());
    let agent = Arc::new(
        ChatAgentRuntime::from_env(database.pool().clone(), queue.clone())
            .context("chat agent initialization failed")?,
    );
    let services = Arc::new(ChatTaskServices::new(
        database.pool().clone(),
        agent,
        process.max_retries,
    ));

    let mut handlers = HandlerRegistry::new();
    handlers.register(ChatPartitionHandler::chat_turn(Arc::clone(&services)))?;
    handlers.register(ChatPartitionHandler::dig_deeper(services))?;
    run_queue_worker(
        database,
        queue,
        handlers,
        notification_database_url(process.database_url()),
        QueueWorkerRunConfig {
            process_name: "Newsly Rust chat worker",
            worker_id: &process.worker_id,
            lease_duration: process.lease_duration,
            max_retries: process.max_retries,
            task_types: &[TaskType::ChatTurn, TaskType::DigDeeper],
            task_type_filter: None,
        },
    )
    .await
}
