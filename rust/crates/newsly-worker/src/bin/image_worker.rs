use std::sync::Arc;

use anyhow::{Context, Result};
use newsly_db::Database;
use newsly_domain::{ResourceKey, RuntimeOwner};
use newsly_providers::ImageGenerationGateway;
use newsly_queue::{
    ClaimRequest, ClaimRuntimeScope, QueueKernel, QueueNotificationHub, TaskQueue, TaskType,
};
use newsly_worker::config::ImageWorkerProcessConfig;
use newsly_worker::image_generation::{GenerateImageHandler, ImageFileStore, ImageWorkerServices};
use newsly_worker::process::{
    initialize_observability, notification_database_url, spawn_shutdown_signal,
};
use newsly_worker::{HandlerRegistry, WorkerConfig, WorkerKernel};

#[tokio::main]
async fn main() -> Result<()> {
    let config = ImageWorkerProcessConfig::from_env()
        .context("invalid Newsly Rust image-worker configuration")?;
    initialize_observability(&config.log_filter, config.log_format)
        .context("image-worker observability initialization failed")?;

    let database = Database::connect_lazy(&config.database)
        .context("image-worker database configuration failed")?;
    database
        .check()
        .await
        .context("image-worker PostgreSQL readiness check failed")?;
    let queue = QueueKernel::new(database.pool().clone());
    let gateway = ImageGenerationGateway::from_env()
        .context("image-generation provider initialization failed")?;
    let file_store = ImageFileStore::new(config.images_base_dir.clone(), gateway.max_image_bytes())
        .context("image file storage initialization failed")?;
    let services = Arc::new(ImageWorkerServices::new(
        database.pool().clone(),
        gateway,
        file_store,
    ));

    let mut handlers = HandlerRegistry::new();
    handlers.register(GenerateImageHandler::new(services))?;
    let scope = ClaimRuntimeScope::namespaces(
        RuntimeOwner::Rust,
        [ResourceKey::new(TaskType::GenerateImage.as_str())?],
    )?;
    let mut claim = ClaimRequest::for_queue(config.worker_id.clone(), TaskQueue::Image, scope);
    claim.task_type = Some(TaskType::GenerateImage);
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
        queue = %TaskQueue::Image,
        task_type = %TaskType::GenerateImage,
        "Newsly Rust image worker started; only rows stamped for the Rust runtime are claimable"
    );
    let run_result = worker.run(shutdown_rx).await;
    shutdown_task.abort();
    notification_hub.close().await;
    database.close().await;
    let summary = run_result.context("Newsly Rust image worker stopped unexpectedly")?;
    tracing::info!(?summary, "Newsly Rust image worker stopped");
    Ok(())
}
