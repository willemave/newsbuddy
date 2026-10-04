use std::sync::Arc;

use anyhow::{Context, Result};
use newsly_db::Database;
use newsly_providers::SummarizationGateway;
use newsly_queue::{QueueKernel, TaskType};
use newsly_worker::HandlerRegistry;
use newsly_worker::config::SummarizationWorkerProcessConfig;
use newsly_worker::process::{
    QueueWorkerRunConfig, initialize_observability, notification_database_url, run_queue_worker,
};
use newsly_worker::summarization::{
    SummarizationBodyStore, SummarizationWorkerServices, SummarizeHandler,
};

#[tokio::main]
pub(crate) async fn main() -> Result<()> {
    let config = SummarizationWorkerProcessConfig::from_env()
        .context("invalid Newsly Rust summarization-worker configuration")?;
    initialize_observability(&config.log_filter, config.log_format)
        .context("summarization-worker observability initialization failed")?;

    let database = Database::connect_lazy(&config.database)
        .context("summarization-worker database configuration failed")?;
    database
        .check()
        .await
        .context("summarization-worker PostgreSQL readiness check failed")?;
    let queue = QueueKernel::new(database.pool().clone());
    let gateway =
        SummarizationGateway::from_env().context("summarization provider initialization failed")?;
    let body_store = SummarizationBodyStore::new(config.content_body_local_root.clone())
        .context("summarization body storage initialization failed")?;
    let services = Arc::new(SummarizationWorkerServices::new(
        database.pool().clone(),
        queue.clone(),
        gateway,
        body_store,
        config.max_retries,
        config.briefing_debounce_seconds,
        config.briefing_batch_minimum,
    ));

    let mut handlers = HandlerRegistry::new();
    handlers.register(SummarizeHandler::new(services))?;
    run_queue_worker(
        database,
        queue,
        handlers,
        notification_database_url(config.database_url()),
        QueueWorkerRunConfig {
            process_name: "Newsly Rust summarization worker",
            worker_id: &config.worker_id,
            lease_duration: config.lease_duration,
            max_retries: config.max_retries,
            task_types: &[TaskType::Summarize],
            task_type_filter: Some(TaskType::Summarize),
        },
    )
    .await
}
