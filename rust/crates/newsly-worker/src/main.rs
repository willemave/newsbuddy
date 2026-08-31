use std::sync::Arc;

use anyhow::{Context, Result, anyhow};
use newsly_db::Database;
use newsly_domain::{ResourceKey, RuntimeOwner};
use newsly_extraction::{DocumentExtractorClient, DocumentExtractorConfig};
use newsly_providers::{ContentAnalysisGateway, XLookupGateway};
use newsly_queue::{
    ClaimRequest, ClaimRuntimeScope, QueueKernel, QueueNotificationHub, TaskQueue, TaskType,
};
use newsly_worker::config::{ContentWorkerProcessConfig, WorkerLogFormat};
use newsly_worker::content::{
    AnalyzeUrlHandler, ContentExtractionRuntime, ContentWorkerServices, FirecrawlClient,
    LocalContentBodyStore, ProcessContentHandler,
};
use newsly_worker::{HandlerRegistry, WorkerConfig, WorkerKernel};
use secrecy::ExposeSecret;
use tokio::sync::watch;
use tracing_subscriber::EnvFilter;

#[tokio::main]
async fn main() -> Result<()> {
    let config = ContentWorkerProcessConfig::from_env()
        .context("invalid Newsly Rust content-worker configuration")?;
    initialize_observability(&config.log_filter, config.log_format)
        .context("content-worker observability initialization failed")?;

    let database = Database::connect_lazy(&config.database)
        .context("content-worker database configuration failed")?;
    database
        .check()
        .await
        .context("content-worker PostgreSQL readiness check failed")?;
    let queue = QueueKernel::new(database.pool().clone());

    let mut extractor_config = DocumentExtractorConfig::new(
        config.extractor_url.clone(),
        config.extractor_secret.clone(),
    )
    .context("document extractor configuration failed")?;
    extractor_config.request_timeout = config.extractor_timeout;
    extractor_config.max_response_bytes = config.extractor_max_response_bytes;
    let extractor = DocumentExtractorClient::new(extractor_config)
        .context("document extractor client initialization failed")?;
    let firecrawl = FirecrawlClient::new(
        config.firecrawl_url.clone(),
        config.firecrawl_api_key.clone(),
        config.firecrawl_timeout,
        config.firecrawl_credit_cost_usd,
    )
    .context("Firecrawl client initialization failed")?;
    let body_store = LocalContentBodyStore::new(
        config.content_body_local_root.clone(),
        config.content_body_storage_prefix.clone(),
    )
    .context("content-body storage initialization failed")?;
    let content_analysis = ContentAnalysisGateway::from_env()
        .context("content-analysis provider configuration failed")?;
    let x_lookup = XLookupGateway::new(config.x_api_base_url.clone())
        .context("X lookup provider configuration failed")?;
    let services = Arc::new(ContentWorkerServices::new(
        database.pool().clone(),
        queue.clone(),
        ContentExtractionRuntime::new(extractor, firecrawl),
        body_store,
        content_analysis,
        x_lookup,
        config.x_app_bearer_token.clone(),
        config.extractor_timeout,
        config.max_retries,
    ));

    let mut handlers = HandlerRegistry::new();
    handlers.register(AnalyzeUrlHandler::new(Arc::clone(&services)))?;
    handlers.register(ProcessContentHandler::new(services))?;
    let scope = ClaimRuntimeScope::namespaces(
        RuntimeOwner::Rust,
        [
            ResourceKey::new(TaskType::AnalyzeUrl.as_str())?,
            ResourceKey::new(TaskType::ProcessContent.as_str())?,
        ],
    )?;
    let mut claim = ClaimRequest::for_queue(config.worker_id.clone(), TaskQueue::Content, scope);
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
        task_types = "analyze_url,process_content",
        "Newsly Rust content worker started; only rows stamped for the Rust runtime are claimable"
    );
    let run_result = worker.run(shutdown_rx).await;
    shutdown_task.abort();
    notification_hub.close().await;
    database.close().await;
    let summary = run_result.context("Newsly Rust content worker stopped unexpectedly")?;
    tracing::info!(?summary, "Newsly Rust content worker stopped");
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
