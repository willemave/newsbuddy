use std::env;
use std::sync::Arc;

use anyhow::{Context, Result, anyhow};
use newsly_db::Database;
use newsly_domain::{ResourceKey, RuntimeOwner};
use newsly_queue::{
    ClaimRequest, ClaimRuntimeScope, QueueKernel, QueueNotificationHub, TaskQueue, TaskType,
};
use newsly_worker::config::WorkerLogFormat;
use newsly_worker::learning_deck::LearningDeckTaskExecutor;
use newsly_worker::queue_process_config::QueueWorkerProcessConfig;
use newsly_worker::run_llm_task::RunLlmTaskHandler;
use newsly_worker::share_actions::{ShareActionAgentRuntime, ShareActionTaskExecutor};
use newsly_worker::{HandlerRegistry, WorkerConfig, WorkerKernel};
use secrecy::ExposeSecret;
use tokio::sync::watch;
use tracing_subscriber::EnvFilter;

const DEFAULT_SANDBOX_ROOT: &str = "/data/workspace";

#[tokio::main]
async fn main() -> Result<()> {
    let process =
        QueueWorkerProcessConfig::from_env("newsly-run-llm-task-worker", "rust-run-llm-task")
            .context("invalid Newsly Rust run-llm-task worker process configuration")?;
    initialize_observability(&process.log_filter, process.log_format)
        .context("run-llm-task worker observability initialization failed")?;

    let database = Database::connect_lazy(&process.database)
        .context("run-llm-task worker database configuration failed")?;
    database
        .check()
        .await
        .context("run-llm-task worker PostgreSQL readiness check failed")?;
    let queue = QueueKernel::new(database.pool().clone());
    let share_agent = Arc::new(
        ShareActionAgentRuntime::from_env(database.pool().clone())
            .context("Share Action agent initialization failed")?,
    );
    let share_actions = ShareActionTaskExecutor::new(
        database.pool().clone(),
        queue.clone(),
        share_agent,
        sandbox_root(),
        process.max_retries,
    );
    let learning_decks =
        LearningDeckTaskExecutor::from_env(database.pool().clone(), process.max_retries)
            .context("Learning Deck task executor initialization failed")?;

    let mut handlers = HandlerRegistry::new();
    handlers.register(RunLlmTaskHandler::new(
        database.pool().clone(),
        share_actions,
        learning_decks,
    ))?;
    let scope = ClaimRuntimeScope::namespaces(
        RuntimeOwner::Rust,
        [ResourceKey::new(TaskType::RunLlmTask.as_str())?],
    )?;
    let mut claim = ClaimRequest::for_queue(process.worker_id.clone(), TaskQueue::Llm, scope);
    claim.task_type = Some(TaskType::RunLlmTask);
    claim.lease_duration = process.lease_duration;
    let mut worker_config = WorkerConfig::new(claim);
    worker_config.max_retries = process.max_retries;

    let notification_url = normalize_listener_url(process.database_url().expose_secret());
    let notification_hub = QueueNotificationHub::spawn(notification_url);
    let notifications = notification_hub.subscribe();
    let mut worker = WorkerKernel::new(queue, handlers, worker_config, Some(notifications))?;
    let (shutdown_tx, shutdown_rx) = watch::channel(false);
    let shutdown_task = tokio::spawn(async move {
        wait_for_shutdown_signal().await;
        shutdown_tx.send_replace(true);
    });

    tracing::info!(
        worker_id = %process.worker_id,
        queue = %TaskQueue::Llm,
        task_type = %TaskType::RunLlmTask,
        "Newsly Rust run-llm-task worker started; runtime ownership must be explicitly cut over before rows are claimable"
    );
    let run_result = worker.run(shutdown_rx).await;
    shutdown_task.abort();
    notification_hub.close().await;
    database.close().await;
    let summary = run_result.context("Newsly Rust run-llm-task worker stopped unexpectedly")?;
    tracing::info!(?summary, "Newsly Rust run-llm-task worker stopped");
    Ok(())
}

fn sandbox_root() -> String {
    env::var("LLM_TASK_SANDBOX_ROOT")
        .ok()
        .map(|value| value.trim().to_owned())
        .filter(|value| value.starts_with('/') && value != "/")
        .unwrap_or_else(|| DEFAULT_SANDBOX_ROOT.to_owned())
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
