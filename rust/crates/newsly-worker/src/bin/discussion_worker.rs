use std::sync::Arc;

use anyhow::{Context, Result};
use newsly_db::Database;
use newsly_domain::{ResourceKey, RuntimeOwner};
use newsly_providers::ContentMiscGateway;
use newsly_queue::{
    ClaimRequest, ClaimRuntimeScope, QueueKernel, QueueNotificationHub, TaskQueue, TaskType,
};
use newsly_worker::config::DiscussionWorkerProcessConfig;
use newsly_worker::discussion::{
    DiscussionObjectStore, DiscussionWorkerServices, FetchNewsItemDiscussionHandler,
};
use newsly_worker::process::{
    initialize_observability, notification_database_url, spawn_shutdown_signal,
};
use newsly_worker::{HandlerRegistry, WorkerConfig, WorkerKernel};

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
    let scope = ClaimRuntimeScope::namespaces(
        RuntimeOwner::Rust,
        [ResourceKey::new(
            TaskType::FetchNewsItemDiscussion.as_str(),
        )?],
    )?;
    let mut claim = ClaimRequest::for_queue(config.worker_id.clone(), TaskQueue::Discussion, scope);
    claim.task_type = Some(TaskType::FetchNewsItemDiscussion);
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
        queue = %TaskQueue::Discussion,
        task_type = %TaskType::FetchNewsItemDiscussion,
        "Newsly Rust discussion worker started; only rows stamped for the Rust runtime are claimable"
    );
    let run_result = worker.run(shutdown_rx).await;
    shutdown_task.abort();
    notification_hub.close().await;
    database.close().await;
    let summary = run_result.context("Newsly Rust discussion worker stopped unexpectedly")?;
    tracing::info!(?summary, "Newsly Rust discussion worker stopped");
    Ok(())
}
