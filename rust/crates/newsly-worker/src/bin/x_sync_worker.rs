use std::sync::Arc;

use anyhow::{Context, Result};
use newsly_db::Database;
use newsly_providers::{IntegrationTokenCipher, XSyncGateway};
use newsly_queue::{QueueKernel, TaskType};
use newsly_worker::HandlerRegistry;
use newsly_worker::config::XSyncWorkerProcessConfig;
use newsly_worker::process::{
    QueueWorkerRunConfig, initialize_observability, notification_database_url, run_queue_worker,
};
use newsly_worker::x_sync::{XSyncIntegrationHandler, XSyncWorkerServices};
use secrecy::ExposeSecret;

#[tokio::main]
pub(crate) async fn main() -> Result<()> {
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
    run_queue_worker(
        database,
        queue,
        handlers,
        notification_database_url(config.database_url()),
        QueueWorkerRunConfig {
            process_name: "Newsly Rust X-sync worker",
            worker_id: &config.worker_id,
            lease_duration: config.lease_duration,
            max_retries: config.max_retries,
            task_types: &[TaskType::SyncIntegration],
            task_type_filter: Some(TaskType::SyncIntegration),
        },
    )
    .await
}
