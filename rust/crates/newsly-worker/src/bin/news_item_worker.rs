use std::sync::Arc;

use anyhow::{Context, Result};
use newsly_db::Database;
use newsly_extraction::{DocumentExtractorClient, DocumentExtractorConfig};
use newsly_providers::NewsItemGateway;
use newsly_queue::{QueueKernel, TaskType};
use newsly_worker::HandlerRegistry;
use newsly_worker::config::NewsItemWorkerProcessConfig;
use newsly_worker::content::{ContentExtractionRuntime, FirecrawlClient};
use newsly_worker::news_item::{
    EnrichNewsItemArticleHandler, NewsArticleBodyStore, NewsItemWorkerServices,
    ProcessNewsItemHandler,
};
use newsly_worker::process::{
    QueueWorkerRunConfig, initialize_observability, notification_database_url, run_queue_worker,
};

#[derive(Debug, Clone, Copy)]
pub(crate) enum NewsItemWorkerMode {
    Extraction,
    Processing,
}

impl NewsItemWorkerMode {
    const fn task_types(self) -> &'static [TaskType] {
        match self {
            Self::Extraction => &[TaskType::EnrichNewsItemArticle],
            Self::Processing => &[TaskType::ProcessNewsItem],
        }
    }

    const fn application_name(self) -> &'static str {
        match self {
            Self::Extraction => "newsly-news-extraction-worker",
            Self::Processing => "newsly-news-processing-worker",
        }
    }
}

#[tokio::main]
pub(crate) async fn run(mode: NewsItemWorkerMode) -> Result<()> {
    let mut config = NewsItemWorkerProcessConfig::from_env()
        .context("invalid Newsly Rust news-item-worker configuration")?;
    mode.application_name()
        .clone_into(&mut config.process.database.application_name);
    initialize_observability(&config.log_filter, config.log_format)
        .context("news-item-worker observability initialization failed")?;

    let database = Database::connect_lazy(&config.database)
        .context("news-item-worker database configuration failed")?;
    database
        .check()
        .await
        .context("news-item-worker PostgreSQL readiness check failed")?;
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
    let gateway =
        NewsItemGateway::from_env().context("news-item provider initialization failed")?;
    let body_store = NewsArticleBodyStore::new(
        config.content_body_local_root.clone(),
        config.content_body_storage_prefix.clone(),
    )
    .context("news article-body storage initialization failed")?;
    let extraction_timeout = chrono::Duration::from_std(config.extractor_timeout)
        .context("news article extraction timeout is outside chrono bounds")?;
    let services = Arc::new(NewsItemWorkerServices::new(
        database.pool().clone(),
        queue.clone(),
        gateway,
        ContentExtractionRuntime::new(extractor, firecrawl, database.pool().clone()),
        body_store,
        config.max_retries,
        config.relation_thresholds,
        extraction_timeout,
        config.briefing_debounce_seconds,
        config.briefing_batch_minimum,
    ));

    let mut handlers = HandlerRegistry::new();
    handlers.register(EnrichNewsItemArticleHandler::new(Arc::clone(&services)))?;
    handlers.register(ProcessNewsItemHandler::new(services))?;
    run_queue_worker(
        database,
        queue,
        handlers,
        notification_database_url(config.database_url()),
        QueueWorkerRunConfig {
            process_name: mode.application_name(),
            worker_id: &config.worker_id,
            lease_duration: config.lease_duration,
            max_retries: config.max_retries,
            task_types: mode.task_types(),
            task_type_filter: None,
        },
    )
    .await
}

#[cfg(test)]
mod tests {
    use super::{NewsItemWorkerMode, TaskType};

    #[test]
    fn extraction_and_processing_claim_disjoint_tasks() {
        assert_eq!(
            NewsItemWorkerMode::Extraction.task_types(),
            &[TaskType::EnrichNewsItemArticle]
        );
        assert_eq!(
            NewsItemWorkerMode::Processing.task_types(),
            &[TaskType::ProcessNewsItem]
        );
        assert_ne!(
            NewsItemWorkerMode::Extraction.application_name(),
            NewsItemWorkerMode::Processing.application_name()
        );
    }
}
