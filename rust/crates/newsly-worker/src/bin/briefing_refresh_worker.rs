use std::sync::Arc;

use anyhow::{Context, Result};
use newsly_db::Database;
use newsly_providers::{
    BriefingCompositionGateway, NewsCategoryGateway, NewsFilterGateway, ProviderCredentials,
};
use newsly_queue::{QueueKernel, TaskType};
use newsly_worker::HandlerRegistry;
use newsly_worker::briefing_refresh::{
    BriefingRefreshHandler, BriefingRefreshWorkerConfig, BriefingRefreshWorkerServices,
    PrepareNewsLensHandler, ReclusterNewsLensesHandler,
};
use newsly_worker::process::{
    QueueWorkerRunConfig, initialize_observability, notification_database_url, run_queue_worker,
};
use newsly_worker::queue_process_config::QueueWorkerProcessConfig;

#[tokio::main]
pub(crate) async fn run(task_type: TaskType) -> Result<()> {
    let (process_name, worker_id) = match task_type {
        TaskType::PrepareNewsLens => ("newsly-news-lens-worker", "rust-news-lens"),
        TaskType::ReclusterNewsLenses => ("newsly-news-recluster-worker", "rust-news-recluster"),
        TaskType::BriefingRefresh => ("newsly-briefing-refresh-worker", "rust-briefing-refresh"),
        _ => anyhow::bail!("unsupported Briefing worker task type"),
    };
    let process = QueueWorkerProcessConfig::from_env(process_name, worker_id)
        .context("invalid Newsly Rust Briefing-refresh worker process configuration")?;
    initialize_observability(&process.log_filter, process.log_format)
        .context("Briefing-refresh worker observability initialization failed")?;
    let briefing = BriefingRefreshWorkerConfig::from_env()
        .context("invalid Newsly Rust Briefing-refresh configuration")?;

    let database = Database::connect_lazy(&process.database)
        .context("Briefing-refresh worker database configuration failed")?;
    database
        .check()
        .await
        .context("Briefing-refresh worker PostgreSQL readiness check failed")?;
    let queue = QueueKernel::new(database.pool().clone());
    let gateway = BriefingCompositionGateway::from_env()
        .context("Briefing provider initialization failed")?;
    let services = Arc::new(BriefingRefreshWorkerServices::new(
        database.pool().clone(),
        queue.clone(),
        gateway,
        NewsFilterGateway::new(ProviderCredentials::from_env().openrouter)
            .context("News filter provider initialization failed")?,
        NewsCategoryGateway::new(ProviderCredentials::from_env().openrouter)
            .context("News category provider initialization failed")?,
        briefing,
    ));

    let mut handlers = HandlerRegistry::new();
    match task_type {
        TaskType::PrepareNewsLens => handlers.register(PrepareNewsLensHandler::new(services))?,
        TaskType::ReclusterNewsLenses => {
            handlers.register(ReclusterNewsLensesHandler::new(services)?)?;
        }
        _ => handlers.register(BriefingRefreshHandler::new(services))?,
    }
    run_queue_worker(
        database,
        queue,
        handlers,
        notification_database_url(process.database_url()),
        QueueWorkerRunConfig {
            process_name: "Newsly Rust Briefing refresh worker",
            worker_id: &process.worker_id,
            lease_duration: process.lease_duration,
            max_retries: process.max_retries,
            task_types: std::slice::from_ref(&task_type),
            task_type_filter: None,
        },
    )
    .await
}
