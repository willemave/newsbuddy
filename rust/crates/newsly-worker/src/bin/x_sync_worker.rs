use std::sync::Arc;

use anyhow::{Context, Result, anyhow};
use newsly_db::Database;
use newsly_domain::{ResourceKey, RuntimeOwner};
use newsly_providers::{IntegrationTokenCipher, XSyncGateway};
use newsly_queue::{
    ClaimRequest, ClaimRuntimeScope, QueueKernel, QueueNotificationHub, TaskQueue, TaskType,
};
use newsly_worker::config::{WorkerLogFormat, XSyncWorkerProcessConfig};
use newsly_worker::x_sync::{XSyncIntegrationHandler, XSyncWorkerServices};
use newsly_worker::{HandlerRegistry, WorkerConfig, WorkerKernel};
use secrecy::ExposeSecret;
use tokio::sync::watch;
use tracing_subscriber::EnvFilter;

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

    let notification_url = normalize_listener_url(config.database_url().expose_secret());
    let notification_hub = QueueNotificationHub::spawn(notification_url);
    let notifications = notification_hub.subscribe();
    let mut worker = WorkerKernel::new(queue, handlers, worker_config, Some(notifications))?;
    let (shutdown_tx, shutdown_rx) = watch::channel(false);
    let shutdown_task = tokio::spawn(async move {
        wait_for_shutdown_signal().await;
        shutdown_tx.send_replace(true);
    });

    tracing::info!(
        worker_id = %config.worker_id,
        queue = %TaskQueue::Twitter,
        task_type = %TaskType::SyncIntegration,
        sync_enabled = config.sync_enabled,
        "Newsly Rust X-sync worker started; runtime ownership must be explicitly cut over before rows are claimable"
    );
    let run_result = worker.run(shutdown_rx).await;
    shutdown_task.abort();
    notification_hub.close().await;
    database.close().await;
    let summary = run_result.context("Newsly Rust X-sync worker stopped unexpectedly")?;
    tracing::info!(?summary, "Newsly Rust X-sync worker stopped");
    Ok(())
}

fn initialize_observability(filter: &str, format: WorkerLogFormat) -> Result<()> {
    let filter = EnvFilter::try_new(filter).context("RUST_LOG contains an invalid filter")?;
    match format {
        WorkerLogFormat::Json => tracing_subscriber::fmt()
            .with_env_filter(filter)
            .json()
            .with_current_span(true)
            .with_span_list(true)
            .try_init()
            .map_err(|error| anyhow!("could not install JSON tracing subscriber: {error}"))?,
        WorkerLogFormat::Pretty => tracing_subscriber::fmt()
            .with_env_filter(filter)
            .pretty()
            .try_init()
            .map_err(|error| anyhow!("could not install pretty tracing subscriber: {error}"))?,
    }
    Ok(())
}

fn normalize_listener_url(value: &str) -> String {
    for prefix in [
        "postgresql+psycopg://",
        "postgresql+psycopg2://",
        "postgresql+asyncpg://",
    ] {
        if let Some(remainder) = value.strip_prefix(prefix) {
            return format!("postgresql://{remainder}");
        }
    }
    value.to_owned()
}

async fn wait_for_shutdown_signal() {
    let interrupt = async {
        tokio::signal::ctrl_c()
            .await
            .expect("failed to install Ctrl+C handler");
    };

    #[cfg(unix)]
    let terminate = async {
        tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())
            .expect("failed to install SIGTERM handler")
            .recv()
            .await;
    };

    #[cfg(not(unix))]
    let terminate = std::future::pending::<()>();

    tokio::select! {
        () = interrupt => {},
        () = terminate => {},
    }
}
