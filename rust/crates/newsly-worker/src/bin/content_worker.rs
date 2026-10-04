use std::sync::Arc;

use anyhow::{Context, Result};
use newsly_db::Database;
use newsly_extraction::{DocumentExtractorClient, DocumentExtractorConfig};
use newsly_providers::{ContentAnalysisGateway, XLookupGateway};
use newsly_queue::{QueueKernel, TaskType};
use newsly_worker::HandlerRegistry;
use newsly_worker::config::ContentWorkerProcessConfig;
use newsly_worker::content::{
    AnalyzeUrlHandler, ContentExtractionRuntime, ContentWorkerServices, FirecrawlClient,
    LocalContentBodyStore, ProcessContentHandler,
};
use newsly_worker::process::{
    QueueWorkerRunConfig, initialize_observability, notification_database_url, run_queue_worker,
};

#[tokio::main]
pub(crate) async fn main() -> Result<()> {
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
    run_queue_worker(
        database,
        queue,
        handlers,
        notification_database_url(config.database_url()),
        QueueWorkerRunConfig {
            process_name: "Newsly Rust content worker",
            worker_id: &config.worker_id,
            lease_duration: config.lease_duration,
            max_retries: config.max_retries,
            task_types: &[TaskType::AnalyzeUrl, TaskType::ProcessContent],
            task_type_filter: None,
        },
    )
    .await
}
