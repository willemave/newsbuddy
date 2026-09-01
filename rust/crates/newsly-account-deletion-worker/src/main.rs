use std::sync::Arc;

use anyhow::{Context, Result};
use newsly_account_deletion_worker::{
    AccountDeletionHandler, AccountDeletionProcessConfig, AccountDeletionServices,
    AccountExternalServices, ConfiguredArtifactStore, DirectAgentVmDestroyer, ReqwestXGrantRevoker,
    UnavailableXGrantRevoker, XGrantRevoker,
};
use newsly_db::Database;
use newsly_domain::{ResourceKey, RuntimeOwner};
use newsly_queue::{
    ClaimRequest, ClaimRuntimeScope, QueueKernel, QueueNotificationHub, TaskQueue, TaskType,
};
use newsly_worker::process::{
    initialize_observability, notification_database_url, spawn_shutdown_signal,
};
use newsly_worker::{HandlerRegistry, WorkerConfig, WorkerKernel};

#[tokio::main]
async fn main() -> Result<()> {
    let config = AccountDeletionProcessConfig::from_env()
        .context("invalid Newsly account-deletion worker configuration")?;
    initialize_observability(&config.log_filter, config.log_format)
        .context("account-deletion worker observability initialization failed")?;

    let database = Database::connect_lazy(&config.database)
        .context("account-deletion worker database configuration failed")?;
    database
        .check()
        .await
        .context("account-deletion worker PostgreSQL readiness check failed")?;
    let queue = QueueKernel::new(database.pool().clone());

    let vm = Arc::new(
        DirectAgentVmDestroyer::from_api_key(config.e2b_api_key.clone())
            .context("account-deletion E2B client configuration failed")?,
    );
    let x: Arc<dyn XGrantRevoker> = match ReqwestXGrantRevoker::new(
        &config.x_oauth_token_url,
        config.x_client_id.clone(),
        config.x_client_secret.clone(),
        config.x_token_encryption_key.clone(),
    ) {
        Ok(revoker) => Arc::new(revoker),
        Err(error) => {
            tracing::warn!(error = %error, "X grant revocation is unavailable; account deletion remains locally authoritative");
            Arc::new(UnavailableXGrantRevoker::new(error.to_string()))
        }
    };
    let objects = Arc::new(
        ConfiguredArtifactStore::new(config.artifact_storage.clone())
            .context("account-deletion object-storage configuration failed")?,
    );
    let services = Arc::new(AccountDeletionServices::new(
        database.pool().clone(),
        queue.clone(),
        AccountExternalServices { vm, x, objects },
        config.media_audio_root.clone(),
        config.personal_markdown_root.clone(),
        config.agent_data_mirror_root.clone(),
    ));

    let mut handlers = HandlerRegistry::new();
    handlers.register(AccountDeletionHandler::new(services))?;
    let scope = ClaimRuntimeScope::namespaces(
        RuntimeOwner::Rust,
        [ResourceKey::new(TaskType::DeleteUserAccount.as_str())?],
    )?;
    let mut claim = ClaimRequest::for_queue(config.worker_id.clone(), TaskQueue::Backfill, scope);
    claim.task_type = Some(TaskType::DeleteUserAccount);
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
        task_type = %TaskType::DeleteUserAccount,
        "Newsly Rust account-deletion worker started; only rows stamped for the Rust runtime are claimable"
    );
    let run_result = worker.run(shutdown_rx).await;
    shutdown_task.abort();
    notification_hub.close().await;
    database.close().await;
    let summary = run_result.context("Newsly Rust account-deletion worker stopped unexpectedly")?;
    tracing::info!(?summary, "Newsly Rust account-deletion worker stopped");
    Ok(())
}
