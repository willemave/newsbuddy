use std::sync::Arc;

use anyhow::{Context, Result};
use newsly_db::Database;
use newsly_providers::ScrapeGateway;
use newsly_queue::{QueueKernel, TaskType};
use newsly_worker::HandlerRegistry;
use newsly_worker::feed_backfill::{BackfillFeedsHandler, FeedBackfillWorkerServices};
use newsly_worker::process::{
    QueueWorkerRunConfig, initialize_observability, notification_database_url, run_queue_worker,
};
use newsly_worker::queue_process_config::QueueWorkerProcessConfig;

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
        ScrapeGateway::from_env().context("feed-backfill provider initialization failed")?;
    let services = Arc::new(FeedBackfillWorkerServices::new(
        database.pool().clone(),
        queue.clone(),
        provider,
    ));

    let mut handlers = HandlerRegistry::new();
    handlers.register(BackfillFeedsHandler::new(services))?;
    run_queue_worker(
        database,
        queue,
        handlers,
        notification_database_url(config.database_url()),
        QueueWorkerRunConfig {
            process_name: "Newsly Rust feed-backfill worker",
            worker_id: &config.worker_id,
            lease_duration: config.lease_duration,
            max_retries: config.max_retries,
            task_types: &[TaskType::BackfillFeeds],
            task_type_filter: Some(TaskType::BackfillFeeds),
        },
    )
    .await
}
