use std::sync::Arc;

use anyhow::{Context, Result};
use newsly_db::Database;
use newsly_providers::ScrapeGateway;
use newsly_queue::{QueueKernel, TaskType};
use newsly_worker::HandlerRegistry;
use newsly_worker::process::{
    QueueWorkerRunConfig, initialize_observability, notification_database_url, run_queue_worker,
};
use newsly_worker::queue_process_config::QueueWorkerProcessConfig;
use newsly_worker::scrape::{ScrapeHandler, ScrapeWorkerServices};

#[tokio::main]
pub(crate) async fn main() -> Result<()> {
    let config = QueueWorkerProcessConfig::from_env("newsly-scrape-worker", "rust-scrape")
        .context("invalid Newsly Rust scrape-worker configuration")?;
    initialize_observability(&config.log_filter, config.log_format)
        .context("scrape-worker observability initialization failed")?;

    let database = Database::connect_lazy(&config.database)
        .context("scrape-worker database configuration failed")?;
    database
        .check()
        .await
        .context("scrape-worker PostgreSQL readiness check failed")?;
    let queue = QueueKernel::new(database.pool().clone());
    let gateway = ScrapeGateway::from_env().context("scrape provider initialization failed")?;
    let services = Arc::new(ScrapeWorkerServices::new(
        database.pool().clone(),
        queue.clone(),
        gateway,
    ));

    let mut handlers = HandlerRegistry::new();
    handlers.register(ScrapeHandler::new(services))?;
    run_queue_worker(
        database,
        queue,
        handlers,
        notification_database_url(config.database_url()),
        QueueWorkerRunConfig {
            process_name: "Newsly Rust scrape worker",
            worker_id: &config.worker_id,
            lease_duration: config.lease_duration,
            max_retries: config.max_retries,
            task_types: &[TaskType::Scrape],
            task_type_filter: Some(TaskType::Scrape),
        },
    )
    .await
}
