use std::sync::Arc;

use anyhow::{Context, Result};
use newsly_db::Database;
use newsly_providers::ImageGenerationGateway;
use newsly_queue::{QueueKernel, TaskType};
use newsly_worker::HandlerRegistry;
use newsly_worker::config::ImageWorkerProcessConfig;
use newsly_worker::image_generation::{GenerateImageHandler, ImageFileStore, ImageWorkerServices};
use newsly_worker::process::{
    QueueWorkerRunConfig, initialize_observability, notification_database_url, run_queue_worker,
};

#[tokio::main]
pub(crate) async fn main() -> Result<()> {
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
        queue.clone(),
        config.briefing_debounce_seconds,
        config.briefing_batch_minimum,
    ));

    let mut handlers = HandlerRegistry::new();
    handlers.register(GenerateImageHandler::new(services))?;
    run_queue_worker(
        database,
        queue,
        handlers,
        notification_database_url(config.database_url()),
        QueueWorkerRunConfig {
            process_name: "Newsly Rust image worker",
            worker_id: &config.worker_id,
            lease_duration: config.lease_duration,
            max_retries: config.max_retries,
            task_types: &[TaskType::GenerateImage],
            task_type_filter: Some(TaskType::GenerateImage),
        },
    )
    .await
}
