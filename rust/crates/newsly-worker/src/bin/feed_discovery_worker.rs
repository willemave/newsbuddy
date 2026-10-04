use std::env;
use std::sync::Arc;

use anyhow::{Context, Result, anyhow};
use newsly_db::Database;
use newsly_providers::{FeedValidator, OnboardingGateway};
use newsly_queue::{QueueKernel, TaskType};
use newsly_worker::HandlerRegistry;
use newsly_worker::feed_discovery::{DiscoverFeedsHandler, FeedDiscoveryWorkerServices};
use newsly_worker::process::{
    QueueWorkerRunConfig, initialize_observability, notification_database_url, run_queue_worker,
};
use newsly_worker::queue_process_config::QueueWorkerProcessConfig;

#[tokio::main]
pub(crate) async fn main() -> Result<()> {
    let config =
        QueueWorkerProcessConfig::from_env("newsly-feed-discovery-worker", "rust-feed-discovery")
            .context("invalid Newsly Rust feed-discovery worker configuration")?;
    initialize_observability(&config.log_filter, config.log_format)
        .context("feed-discovery worker observability initialization failed")?;

    let database = Database::connect_lazy(&config.database)
        .context("feed-discovery worker database configuration failed")?;
    database
        .check()
        .await
        .context("feed-discovery worker PostgreSQL readiness check failed")?;
    let queue = QueueKernel::new(database.pool().clone());
    let provider =
        OnboardingGateway::from_env().context("feed-discovery provider initialization failed")?;
    let feed_validator = FeedValidator::new();
    let favorite_limit = parse_positive_u64("DISCOVERY_MAX_FAVORITES", 20)?;
    let minimum_favorites = parse_positive_u64("DISCOVERY_MIN_FAVORITES", 3)?;
    let services = Arc::new(FeedDiscoveryWorkerServices::new(
        database.pool().clone(),
        provider,
        feed_validator,
        config.max_retries,
        i64::try_from(favorite_limit).context("DISCOVERY_MAX_FAVORITES is too large")?,
        usize::try_from(minimum_favorites).context("DISCOVERY_MIN_FAVORITES is too large")?,
    ));

    let mut handlers = HandlerRegistry::new();
    handlers.register(DiscoverFeedsHandler::new(services))?;
    run_queue_worker(
        database,
        queue,
        handlers,
        notification_database_url(config.database_url()),
        QueueWorkerRunConfig {
            process_name: "Newsly Rust feed-discovery worker",
            worker_id: &config.worker_id,
            lease_duration: config.lease_duration,
            max_retries: config.max_retries,
            task_types: &[TaskType::DiscoverFeeds],
            task_type_filter: Some(TaskType::DiscoverFeeds),
        },
    )
    .await
}

fn parse_positive_u64(name: &'static str, default: u64) -> Result<u64> {
    let value = env::var(name).map_or(Ok(default), |value| {
        value
            .parse::<u64>()
            .with_context(|| format!("{name} must be an integer"))
    })?;
    if value == 0 {
        return Err(anyhow!("{name} must be positive"));
    }
    Ok(value)
}
