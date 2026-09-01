use std::sync::Arc;

use anyhow::{Context, Result};
use newsly_db::Database;
use newsly_domain::{ResourceKey, RuntimeOwner};
use newsly_providers::{IntegrationTokenCipher, XSyncGateway};
use newsly_queue::{
    ClaimRequest, ClaimRuntimeScope, QueueKernel, QueueNotificationHub, TaskQueue, TaskType,
};
use newsly_worker::config::XSyncWorkerProcessConfig;
use newsly_worker::process::{
    initialize_observability, notification_database_url, spawn_shutdown_signal,
};
use newsly_worker::x_sync::{XSyncIntegrationHandler, XSyncWorkerServices};
use newsly_worker::{HandlerRegistry, WorkerConfig, WorkerKernel};
use secrecy::ExposeSecret;

#[tokio::main]
async fn main() -> Result<()> {
    let config = XSyncWorkerProcessConfig::from_env()
        .context("invalid Newsly Rust X-sync worker configuration")?;
    initialize_observability(&config.log_filter, config.log_format)
        .context("X-sync worker observability initialization failed")?;

    let database = Database::connect_lazy(&config.database)
        .context("X-sync worker database configuration failed")?;
    database
        .check()
        .await
        .context("X-sync worker PostgreSQL readiness check failed")?;
    let queue = QueueKernel::new(database.pool().clone());

    let (gateway, token_cipher) = if config.sync_enabled {
        let client_id = config
            .client_id
            .as_ref()
            .context("enabled X sync requires X_CLIENT_ID")?;
        let encryption_key = config
            .token_encryption_key
            .as_ref()
            .context("enabled X sync requires X_TOKEN_ENCRYPTION_KEY")?;
        (
            Some(
                XSyncGateway::new(
                    client_id.expose_secret(),
                    config.client_secret.clone(),
                    config.token_url.clone(),
                    config.api_base_url.clone(),
                )
                .context("X sync provider initialization failed")?,
            ),
            Some(
                IntegrationTokenCipher::new(encryption_key)
                    .context("X integration-token cipher initialization failed")?,
            ),
        )
    } else {
        (None, None)
    };
    let services = Arc::new(XSyncWorkerServices::new(
        database.pool().clone(),
        queue.clone(),
        config.sync_enabled,
        gateway,
        token_cipher,
        config.sync_min_interval_minutes,
        config.bookmark_min_interval_minutes,
        config.posts_read_cost_usd,
        config.users_read_cost_usd,
    ));

    let mut handlers = HandlerRegistry::new();
    handlers.register(XSyncIntegrationHandler::new(services))?;
    let scope = ClaimRuntimeScope::namespaces(
        RuntimeOwner::Rust,
        [ResourceKey::new(TaskType::SyncIntegration.as_str())?],
    )?;
    let mut claim = ClaimRequest::for_queue(config.worker_id.clone(), TaskQueue::Twitter, scope);
    claim.task_type = Some(TaskType::SyncIntegration);
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
        queue = %TaskQueue::Twitter,
        task_type = %TaskType::SyncIntegration,
        sync_enabled = config.sync_enabled,
        "Newsly Rust X-sync worker started; only rows stamped for the Rust runtime are claimable"
    );
    let run_result = worker.run(shutdown_rx).await;
    shutdown_task.abort();
    notification_hub.close().await;
    database.close().await;
    let summary = run_result.context("Newsly Rust X-sync worker stopped unexpectedly")?;
    tracing::info!(?summary, "Newsly Rust X-sync worker stopped");
    Ok(())
}
