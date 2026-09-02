use std::env;
use std::sync::Arc;
use std::time::Duration;

use anyhow::{Context, Result, anyhow};
use newsly_db::Database;
use newsly_domain::{ResourceKey, RuntimeOwner};
use newsly_e2b::FeedValidator;
use newsly_providers::OnboardingGateway;
use newsly_queue::{
    ClaimRequest, ClaimRuntimeScope, QueueKernel, QueueNotificationHub, TaskQueue, TaskType,
};
use newsly_worker::feed_discovery::{DiscoverFeedsHandler, FeedDiscoveryWorkerServices};
use newsly_worker::process::{
    initialize_observability, notification_database_url, spawn_shutdown_signal,
};
use newsly_worker::queue_process_config::QueueWorkerProcessConfig;
use newsly_worker::{HandlerRegistry, WorkerConfig, WorkerKernel};
use secrecy::SecretString;

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
    let feed_validator = FeedValidator::new(
        optional_secret_alias(&["LLM_TASK_SANDBOX_E2B_API_KEY", "E2B_API_KEY"]),
        &env::var("NEWSLY_AGENT_VM_TEMPLATE_ID").unwrap_or_else(|_| "newsly-agent".to_owned()),
        Duration::from_secs(parse_positive_u64("LLM_TASK_SANDBOX_TIMEOUT_SECONDS", 300)?),
    )
    .context("feed-discovery E2B validator initialization failed")?;
    let favorite_limit = parse_positive_u64("DISCOVERY_MAX_FAVORITES", 20)?;
    let minimum_favorites = parse_positive_u64("DISCOVERY_MIN_FAVORITES", 3)?;
    let services = Arc::new(FeedDiscoveryWorkerServices::new(
        database.pool().clone(),
        queue.clone(),
        provider,
        feed_validator,
        config.max_retries,
        i64::try_from(favorite_limit).context("DISCOVERY_MAX_FAVORITES is too large")?,
        usize::try_from(minimum_favorites).context("DISCOVERY_MIN_FAVORITES is too large")?,
    ));

    let mut handlers = HandlerRegistry::new();
    handlers.register(DiscoverFeedsHandler::new(services))?;
    let scope = ClaimRuntimeScope::namespaces(
        RuntimeOwner::Rust,
        [ResourceKey::new(TaskType::DiscoverFeeds.as_str())?],
    )?;
    let mut claim = ClaimRequest::for_queue(config.worker_id.clone(), TaskQueue::Content, scope);
    claim.task_type = Some(TaskType::DiscoverFeeds);
    claim.lease_duration = config.lease_duration;
    let mut worker_config = WorkerConfig::new(claim);
    worker_config.max_retries = config.max_retries;

    let notification_hub =
        QueueNotificationHub::spawn(notification_database_url(config.database_url()));
    let notifications = notification_hub.subscribe();
    let mut worker = WorkerKernel::new(queue, handlers, worker_config, Some(notifications))?;
    let (shutdown_rx, shutdown_task) = spawn_shutdown_signal();

    tracing::info!(
        worker_id = %config.worker_id,
        queue = %TaskQueue::Content,
        task_type = %TaskType::DiscoverFeeds,
        "Newsly Rust feed-discovery worker started; only rows stamped for the Rust runtime are claimable"
    );
    let run_result = worker.run(shutdown_rx).await;
    shutdown_task.abort();
    notification_hub.close().await;
    database.close().await;
    let summary = run_result.context("Newsly Rust feed-discovery worker stopped unexpectedly")?;
    tracing::info!(?summary, "Newsly Rust feed-discovery worker stopped");
    Ok(())
}

fn optional_secret_alias(names: &[&str]) -> Option<SecretString> {
    names.iter().find_map(|name| {
        env::var(name)
            .ok()
            .filter(|value| !value.trim().is_empty())
            .map(SecretString::from)
    })
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
