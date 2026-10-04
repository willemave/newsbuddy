use std::sync::Arc;

use anyhow::{Context, Result};
use newsly_db::Database;
use newsly_providers::ContentMiscGateway;
use newsly_queue::{QueueKernel, TaskType};
use newsly_worker::HandlerRegistry;
use newsly_worker::config::DiscussionWorkerProcessConfig;
use newsly_worker::discussion::{
    DiscussionObjectStore, DiscussionWorkerServices, FetchNewsItemDiscussionHandler,
};
use newsly_worker::process::{
    QueueWorkerRunConfig, initialize_observability, notification_database_url, run_queue_worker,
};

#[tokio::main]
pub(crate) async fn main() -> Result<()> {
    let config = DiscussionWorkerProcessConfig::from_env()
        .context("invalid Newsly Rust discussion-worker configuration")?;
    initialize_observability(&config.log_filter, config.log_format)
        .context("discussion-worker observability initialization failed")?;

    let database = Database::connect_lazy(&config.database)
        .context("discussion-worker database configuration failed")?;
    database
        .check()
        .await
        .context("discussion-worker PostgreSQL readiness check failed")?;
    let queue = QueueKernel::new(database.pool().clone());
    let gateway =
        ContentMiscGateway::from_env().context("discussion provider initialization failed")?;
    let object_store = DiscussionObjectStore::new(
        config.content_body_local_root.clone(),
        config.content_body_storage_prefix.clone(),
    )
    .context("discussion object-store initialization failed")?;
    let services = Arc::new(DiscussionWorkerServices::new(
        database.pool().clone(),
        gateway,
        object_store,
    ));

    let mut handlers = HandlerRegistry::new();
    handlers.register(FetchNewsItemDiscussionHandler::new(services))?;
    run_queue_worker(
        database,
        queue,
        handlers,
        notification_database_url(config.database_url()),
        QueueWorkerRunConfig {
            process_name: "Newsly Rust discussion worker",
            worker_id: &config.worker_id,
            lease_duration: config.lease_duration,
            max_retries: config.max_retries,
            task_types: &[TaskType::FetchNewsItemDiscussion],
            task_type_filter: Some(TaskType::FetchNewsItemDiscussion),
        },
    )
    .await
}
