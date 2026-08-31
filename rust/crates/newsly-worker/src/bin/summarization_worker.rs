use std::sync::Arc;

use anyhow::{Context, Result, anyhow};
use newsly_db::Database;
use newsly_domain::{ResourceKey, RuntimeOwner};
use newsly_providers::SummarizationGateway;
use newsly_queue::{
    ClaimRequest, ClaimRuntimeScope, QueueKernel, QueueNotificationHub, TaskQueue, TaskType,
};
use newsly_worker::config::{SummarizationWorkerProcessConfig, WorkerLogFormat};
use newsly_worker::summarization::{
    SummarizationBodyStore, SummarizationWorkerServices, SummarizeHandler,
};
use newsly_worker::{HandlerRegistry, WorkerConfig, WorkerKernel};
use secrecy::ExposeSecret;
use tokio::sync::watch;
use tracing_subscriber::EnvFilter;

#[tokio::main]
async fn main() -> Result<()> {
    let config = SummarizationWorkerProcessConfig::from_env()
        .context("invalid Newsly Rust summarization-worker configuration")?;
    initialize_observability(&config.log_filter, config.log_format)
        .context("summarization-worker observability initialization failed")?;

    let database = Database::connect_lazy(&config.database)
        .context("summarization-worker database configuration failed")?;
    database
        .check()
        .await
        .context("summarization-worker PostgreSQL readiness check failed")?;
    let queue = QueueKernel::new(database.pool().clone());
    let gateway =
        SummarizationGateway::from_env().context("summarization provider initialization failed")?;
    let body_store = SummarizationBodyStore::new(config.content_body_local_root.clone())
        .context("summarization body storage initialization failed")?;
    let services = Arc::new(SummarizationWorkerServices::new(
        database.pool().clone(),
        queue.clone(),
        gateway,
        body_store,
        config.max_retries,
        config.briefing_debounce_seconds,
        config.briefing_batch_minimum,
    ));

    let mut handlers = HandlerRegistry::new();
    handlers.register(SummarizeHandler::new(services))?;
    let scope = ClaimRuntimeScope::namespaces(
        RuntimeOwner::Rust,
        [ResourceKey::new(TaskType::Summarize.as_str())?],
    )?;
    let mut claim = ClaimRequest::for_queue(config.worker_id.clone(), TaskQueue::Content, scope);
    claim.task_type = Some(TaskType::Summarize);
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
        queue = %TaskQueue::Content,
        task_type = %TaskType::Summarize,
        "Newsly Rust summarization worker started; runtime ownership must be explicitly cut over before rows are claimable"
    );
    let run_result = worker.run(shutdown_rx).await;
    shutdown_task.abort();
    notification_hub.close().await;
    database.close().await;
    let summary = run_result.context("Newsly Rust summarization worker stopped unexpectedly")?;
    tracing::info!(?summary, "Newsly Rust summarization worker stopped");
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
