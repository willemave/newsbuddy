use std::sync::Arc;

use anyhow::{Context, Result};
use newsly_db::Database;
use newsly_domain::{ResourceKey, RuntimeOwner};
use newsly_providers::ContentMiscGateway;
use newsly_queue::{
    ClaimRequest, ClaimRuntimeScope, QueueKernel, QueueNotificationHub, TaskQueue, TaskType,
};
use newsly_worker::feed_backfill::{BackfillFeedsHandler, FeedBackfillWorkerServices};
use newsly_worker::process::{
    initialize_observability, notification_database_url, spawn_shutdown_signal,
};
use newsly_worker::queue_process_config::QueueWorkerProcessConfig;
use newsly_worker::{HandlerRegistry, WorkerConfig, WorkerKernel};

#[tokio::main]
pub(crate) async fn main() -> Result<()> {
    let config =
        QueueWorkerProcessConfig::from_env("newsly-feed-backfill-worker", "rust-feed-backfill")
            .context("invalid Newsly Rust feed-backfill worker configuration")?;
    initialize_observability(&config.log_filter, config.log_format)
        .context("feed-backfill worker observability initialization failed")?;

    let database = Database::connect_lazy(&config.database)
        .context("feed-backfill worker database configuration failed")?;
    database
        .check()
        .await
        .context("feed-backfill worker PostgreSQL readiness check failed")?;
    let queue = QueueKernel::new(database.pool().clone());
    let provider =
        ContentMiscGateway::from_env().context("feed-backfill provider initialization failed")?;
    let services = Arc::new(FeedBackfillWorkerServices::new(
        database.pool().clone(),
        queue.clone(),
        provider,
    ));

    let mut handlers = HandlerRegistry::new();
    handlers.register(BackfillFeedsHandler::new(services))?;
    let scope = ClaimRuntimeScope::namespaces(
        RuntimeOwner::Rust,
        [ResourceKey::new(TaskType::BackfillFeeds.as_str())?],
    )?;
    let mut claim = ClaimRequest::for_queue(config.worker_id.clone(), TaskQueue::Backfill, scope);
    claim.task_type = Some(TaskType::BackfillFeeds);
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
        queue = %TaskQueue::Backfill,
        task_type = %TaskType::BackfillFeeds,
        "Newsly Rust feed-backfill worker started; only rows stamped for the Rust runtime are claimable"
    );
    let run_result = worker.run(shutdown_rx).await;
    shutdown_task.abort();
    notification_hub.close().await;
    database.close().await;
    let summary = run_result.context("Newsly Rust feed-backfill worker stopped unexpectedly")?;
    tracing::info!(?summary, "Newsly Rust feed-backfill worker stopped");
    Ok(())
}
