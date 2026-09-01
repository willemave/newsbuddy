use std::sync::Arc;

use anyhow::{Context, Result};
use newsly_db::Database;
use newsly_domain::{ResourceKey, RuntimeOwner};
use newsly_providers::ScrapeGateway;
use newsly_queue::{
    ClaimRequest, ClaimRuntimeScope, QueueKernel, QueueNotificationHub, TaskQueue, TaskType,
};
use newsly_worker::process::{
    initialize_observability, notification_database_url, spawn_shutdown_signal,
};
use newsly_worker::queue_process_config::QueueWorkerProcessConfig;
use newsly_worker::scrape::{ScrapeHandler, ScrapeWorkerServices};
use newsly_worker::{HandlerRegistry, WorkerConfig, WorkerKernel};

#[tokio::main]
async fn main() -> Result<()> {
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
    let scope = ClaimRuntimeScope::namespaces(
        RuntimeOwner::Rust,
        [ResourceKey::new(TaskType::Scrape.as_str())?],
    )?;
    let mut claim = ClaimRequest::for_queue(config.worker_id.clone(), TaskQueue::Content, scope);
    claim.task_type = Some(TaskType::Scrape);
    claim.lease_duration = config.lease_duration;
    let mut worker_config = WorkerConfig::new(claim);
    worker_config.max_retries = config.max_retries;

    let notification_hub =
        QueueNotificationHub::spawn(notification_database_url(config.database_url()));
    let notifications = notification_hub.subscribe();
    let mut worker = WorkerKernel::new(queue, handlers, worker_config, Some(notifications))?;
    let (shutdown_rx, shutdown_task) = spawn_shutdown_signal();

    tracing::info!(
        worker_id = %config.worker_id,
        queue = %TaskQueue::Content,
        task_type = %TaskType::Scrape,
        "Newsly Rust scrape worker started; only rows stamped for the Rust runtime are claimable"
    );
    let run_result = worker.run(shutdown_rx).await;
    shutdown_task.abort();
    notification_hub.close().await;
    database.close().await;
    let summary = run_result.context("Newsly Rust scrape worker stopped unexpectedly")?;
    tracing::info!(?summary, "Newsly Rust scrape worker stopped");
    Ok(())
}
