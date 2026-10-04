use std::sync::Arc;

use anyhow::{Context, Result};
use newsly_account_deletion_worker::{
    AccountDeletionHandler, AccountDeletionProcessConfig, AccountDeletionServices,
    AccountExternalServices, ConfiguredArtifactStore, ReqwestXGrantRevoker,
    UnavailableXGrantRevoker, XGrantRevoker,
};
use newsly_db::Database;
use newsly_queue::{QueueKernel, TaskType};
use newsly_worker::HandlerRegistry;
use newsly_worker::process::{
    QueueWorkerRunConfig, initialize_observability, notification_database_url, run_queue_worker,
};

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
    let objects = Arc::new(ConfiguredArtifactStore::new(
        config.artifact_storage.clone(),
    ));
    let services = Arc::new(AccountDeletionServices::new(
        database.pool().clone(),
        queue.clone(),
        AccountExternalServices { x, objects },
        config.media_audio_root.clone(),
        config.personal_markdown_root.clone(),
    ));

    let mut handlers = HandlerRegistry::new();
    handlers.register(AccountDeletionHandler::new(services))?;
    run_queue_worker(
        database,
        queue,
        handlers,
        notification_database_url(config.database_url()),
        QueueWorkerRunConfig {
            process_name: "Newsly Rust account-deletion worker",
            worker_id: &config.worker_id,
            lease_duration: config.lease_duration,
            max_retries: config.max_retries,
            task_types: &[TaskType::DeleteUserAccount],
            task_type_filter: Some(TaskType::DeleteUserAccount),
        },
    )
    .await
}
