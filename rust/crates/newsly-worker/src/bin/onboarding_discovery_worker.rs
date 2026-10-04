use std::sync::Arc;

use anyhow::{Context, Result};
use newsly_db::Database;
use newsly_providers::{FeedValidator, OnboardingGateway};
use newsly_queue::{QueueKernel, TaskType};
use newsly_worker::HandlerRegistry;
use newsly_worker::onboarding_discovery::{
    OnboardingDiscoverHandler, OnboardingDiscoveryWorkerServices,
};
use newsly_worker::process::{
    QueueWorkerRunConfig, initialize_observability, notification_database_url, run_queue_worker,
};
use newsly_worker::queue_process_config::QueueWorkerProcessConfig;

#[tokio::main]
pub(crate) async fn main() -> Result<()> {
    let config = QueueWorkerProcessConfig::from_env(
        "newsly-onboarding-discovery-worker",
        "rust-onboarding-discovery",
    )
    .context("invalid Newsly Rust onboarding-discovery worker configuration")?;
    initialize_observability(&config.log_filter, config.log_format)
        .context("onboarding-discovery worker observability initialization failed")?;

    let database = Database::connect_lazy(&config.database)
        .context("onboarding-discovery worker database configuration failed")?;
    database
        .check()
        .await
        .context("onboarding-discovery worker PostgreSQL readiness check failed")?;
    let queue = QueueKernel::new(database.pool().clone());
    let provider = OnboardingGateway::from_env()
        .context("onboarding-discovery provider initialization failed")?;
    let feed_validator = FeedValidator::new();
    let services = Arc::new(OnboardingDiscoveryWorkerServices::new(
        database.pool().clone(),
        provider,
        feed_validator,
        config.max_retries,
    ));

    let mut handlers = HandlerRegistry::new();
    handlers.register(OnboardingDiscoverHandler::new(services))?;
    run_queue_worker(
        database,
        queue,
        handlers,
        notification_database_url(config.database_url()),
        QueueWorkerRunConfig {
            process_name: "Newsly Rust onboarding-discovery worker",
            worker_id: &config.worker_id,
            lease_duration: config.lease_duration,
            max_retries: config.max_retries,
            task_types: &[TaskType::OnboardingDiscover],
            task_type_filter: Some(TaskType::OnboardingDiscover),
        },
    )
    .await
}
