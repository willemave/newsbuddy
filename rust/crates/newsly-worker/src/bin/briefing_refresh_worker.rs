use std::sync::Arc;

use anyhow::{Context, Result};
use newsly_db::Database;
use newsly_domain::{ResourceKey, RuntimeOwner};
use newsly_providers::BriefingCompositionGateway;
use newsly_queue::{
    ClaimRequest, ClaimRuntimeScope, QueueKernel, QueueNotificationHub, TaskQueue, TaskType,
};
use newsly_worker::briefing_refresh::{
    BriefingRefreshHandler, BriefingRefreshWorkerConfig, BriefingRefreshWorkerServices,
    PrepareNewsLensHandler,
};
use newsly_worker::process::{
    initialize_observability, notification_database_url, spawn_shutdown_signal,
};
use newsly_worker::queue_process_config::QueueWorkerProcessConfig;
use newsly_worker::{HandlerRegistry, WorkerConfig, WorkerKernel};

#[tokio::main]
pub(crate) async fn run(preparation_only: bool) -> Result<()> {
    let task_type = if preparation_only {
        TaskType::PrepareNewsLens
    } else {
        TaskType::BriefingRefresh
    };
    let process_name = if preparation_only {
        "newsly-news-lens-worker"
    } else {
        "newsly-briefing-refresh-worker"
    };
    let worker_id = if preparation_only {
        "rust-news-lens"
    } else {
        "rust-briefing-refresh"
    };
    let process = QueueWorkerProcessConfig::from_env(process_name, worker_id)
        .context("invalid Newsly Rust Briefing-refresh worker process configuration")?;
    let briefing = BriefingRefreshWorkerConfig::from_env()
        .context("invalid Newsly Rust Briefing-refresh configuration")?;
    initialize_observability(&process.log_filter, process.log_format)
        .context("Briefing-refresh worker observability initialization failed")?;

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
        briefing,
    ));

    let mut handlers = HandlerRegistry::new();
    if preparation_only {
        handlers.register(PrepareNewsLensHandler::new(services))?;
    } else {
        handlers.register(BriefingRefreshHandler::new(services))?;
    }
    let scope =
        ClaimRuntimeScope::namespaces(RuntimeOwner::Rust, [ResourceKey::new(task_type.as_str())?])?;
    let mut claim = ClaimRequest::for_queue(process.worker_id.clone(), TaskQueue::Llm, scope);

    claim.lease_duration = process.lease_duration;
    let mut worker_config = WorkerConfig::new(claim);
    worker_config.max_retries = process.max_retries;

    let notification_hub =
        QueueNotificationHub::spawn(notification_database_url(process.database_url()));
    let notifications = notification_hub.subscribe();
    let mut worker = WorkerKernel::new(queue, handlers, worker_config, Some(notifications))?;
    let (shutdown_rx, shutdown_task) = spawn_shutdown_signal();

    tracing::info!(
        worker_id = %process.worker_id,
        queue = %TaskQueue::Llm,
        task_type = %task_type,
        "Newsly Rust Briefing refresh worker started; only rows stamped for the Rust runtime are claimable"
    );
    let run_result = worker.run(shutdown_rx).await;
    shutdown_task.abort();
    notification_hub.close().await;
    database.close().await;
    let summary = run_result.context("Newsly Rust Briefing refresh worker stopped unexpectedly")?;
    tracing::info!(?summary, "Newsly Rust Briefing refresh worker stopped");
    Ok(())
}
