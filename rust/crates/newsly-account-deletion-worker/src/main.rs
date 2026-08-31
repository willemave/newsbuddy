use std::sync::Arc;

use anyhow::{Context, Result, anyhow};
use newsly_account_deletion_worker::{
    AccountDeletionHandler, AccountDeletionProcessConfig, AccountDeletionServices,
    AccountExternalServices, ConfiguredArtifactStore, DirectAgentVmDestroyer, ReqwestXGrantRevoker,
    UnavailableXGrantRevoker, WorkerLogFormat, XGrantRevoker,
};
use newsly_db::Database;
use newsly_domain::{ResourceKey, RuntimeOwner};
use newsly_queue::{
    ClaimRequest, ClaimRuntimeScope, QueueKernel, QueueNotificationHub, TaskQueue, TaskType,
};
use newsly_worker::{HandlerRegistry, WorkerConfig, WorkerKernel};
use secrecy::ExposeSecret;
use tokio::sync::watch;
use tracing_subscriber::EnvFilter;

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
        queue = %TaskQueue::Backfill,
        task_type = %TaskType::DeleteUserAccount,
        "Newsly Rust account-deletion worker started; runtime ownership must be explicitly cut over before rows are claimable"
    );
    let run_result = worker.run(shutdown_rx).await;
    shutdown_task.abort();
    notification_hub.close().await;
    database.close().await;
    let summary = run_result.context("Newsly Rust account-deletion worker stopped unexpectedly")?;
    tracing::info!(?summary, "Newsly Rust account-deletion worker stopped");
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
