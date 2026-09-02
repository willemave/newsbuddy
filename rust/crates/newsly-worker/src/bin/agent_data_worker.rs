use std::sync::Arc;

use anyhow::{Context, Result};
use newsly_db::Database;
use newsly_domain::{ResourceKey, RuntimeOwner};
use newsly_queue::{
    ClaimRequest, ClaimRuntimeScope, QueueKernel, QueueNotificationHub, TaskQueue, TaskType,
};
use newsly_worker::agent_data::{
    AgentDataBackfillServices, AgentDataIndexServices, AgentDataMirrorStore, AgentDataSyncServices,
    AgentDataWorkerProcessConfig, BackfillAgentDataHandler, IndexAgentDataHandler,
    ReconcileAgentDataHandler, SyncAgentDataHandler,
};
use newsly_worker::process::{
    initialize_observability, notification_database_url, spawn_shutdown_signal,
};
use newsly_worker::{HandlerRegistry, WorkerConfig, WorkerKernel};

#[tokio::main]
pub(crate) async fn main() -> Result<()> {
    let config = AgentDataWorkerProcessConfig::from_env()
        .context("invalid Newsly Rust Agent Data worker configuration")?;
    initialize_observability(&config.log_filter, config.log_format)
        .context("Agent Data worker observability initialization failed")?;

    let database = Database::connect_lazy(&config.database)
        .context("Agent Data worker database configuration failed")?;
    database
        .check()
        .await
        .context("Agent Data worker PostgreSQL readiness check failed")?;
    let queue = QueueKernel::new(database.pool().clone());
    let store = AgentDataMirrorStore::new(config.mirror_root.clone())?
        .with_content_body_environment(config.content_body_local_root.clone())?;
    let sync_services = Arc::new(AgentDataSyncServices::new(
        database.pool().clone(),
        queue.clone(),
        store.clone(),
        config.max_document_bytes,
        config.index_debounce_seconds,
    ));
    let index_services = Arc::new(AgentDataIndexServices::new(database.pool().clone(), store));
    let backfill_services = Arc::new(AgentDataBackfillServices::new(
        Arc::clone(&sync_services),
        Arc::clone(&index_services),
        config.backfill_batch_size,
    ));

    let mut handlers = HandlerRegistry::new();
    handlers.register(SyncAgentDataHandler::new(sync_services))?;
    handlers.register(IndexAgentDataHandler::new(index_services))?;
    handlers.register(BackfillAgentDataHandler::new(Arc::clone(
        &backfill_services,
    )))?;
    handlers.register(ReconcileAgentDataHandler::new(backfill_services))?;
    let task_types = [
        TaskType::SyncAgentData,
        TaskType::IndexAgentData,
        TaskType::BackfillAgentData,
        TaskType::ReconcileAgentData,
    ];
    let scope = ClaimRuntimeScope::namespaces(
        RuntimeOwner::Rust,
        task_types
            .iter()
            .map(|task_type| ResourceKey::new(task_type.as_str()))
            .collect::<Result<Vec<_>, _>>()?,
    )?;
    let mut claim = ClaimRequest::for_queue(config.worker_id.clone(), TaskQueue::Backfill, scope);
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
        task_types = ?task_types.map(newsly_queue::TaskType::as_str),
        "Newsly Rust Agent Data worker started; only rows stamped for the Rust runtime are claimable"
    );
    let run_result = worker.run(shutdown_rx).await;
    shutdown_task.abort();
    notification_hub.close().await;
    database.close().await;
    let summary = run_result.context("Newsly Rust Agent Data worker stopped unexpectedly")?;
    tracing::info!(?summary, "Newsly Rust Agent Data worker stopped");
    Ok(())
}
