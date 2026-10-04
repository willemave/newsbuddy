//! Shared queue-worker process plumbing.
//!
//! Service construction stays in each process entrypoint so provider and task ownership remain explicit.
//! This module owns the identical worker-kernel, notification, database-close, and shutdown lifecycle.

use std::time::Duration;

use anyhow::{Context, Result, anyhow};
use newsly_db::Database;
use newsly_domain::{ResourceKey, RuntimeOwner};
use newsly_queue::{
    ClaimRequest, ClaimRuntimeScope, QueueKernel, QueueNotificationHub, TaskQueue, TaskType,
};
use secrecy::{ExposeSecret, SecretString};
use tokio::sync::watch;
use tokio::task::JoinHandle;
use tracing_subscriber::EnvFilter;

use crate::config::WorkerLogFormat;
use crate::{HandlerRegistry, WorkerConfig, WorkerKernel};

#[derive(Debug, Clone, Copy)]
pub struct QueueWorkerRunConfig<'a> {
    pub process_name: &'static str,
    pub worker_id: &'a str,
    pub lease_duration: Duration,
    pub max_retries: i32,
    pub task_types: &'a [TaskType],
    pub task_type_filter: Option<TaskType>,
}

/// Runs the common queue-worker lifecycle after process-specific readiness and service setup.
///
/// # Errors
///
/// Returns an error when the task scope is empty or spans queues, when worker construction fails,
/// or when the worker loop stops unexpectedly. The notification hub and database pool are closed
/// before any error is returned.
pub async fn run_queue_worker(
    database: Database,
    queue: QueueKernel,
    handlers: HandlerRegistry,
    notification_url: String,
    run_config: QueueWorkerRunConfig<'_>,
) -> Result<()> {
    let notification_hub = QueueNotificationHub::spawn(notification_url);
    let notifications = notification_hub.subscribe();

    let run_result = async {
        let queue_name = queue_for_task_scope(run_config.task_types)?;
        let namespaces = run_config
            .task_types
            .iter()
            .map(|task_type| ResourceKey::new(task_type.as_str()))
            .collect::<Result<Vec<_>, _>>()?;
        let scope = ClaimRuntimeScope::namespaces(RuntimeOwner::Rust, namespaces)?;
        let mut claim = ClaimRequest::for_queue(run_config.worker_id.to_owned(), queue_name, scope);
        claim.task_type = run_config.task_type_filter;
        claim.lease_duration = run_config.lease_duration;
        let mut worker_config = WorkerConfig::new(claim);
        worker_config.max_retries = run_config.max_retries;
        let mut worker = WorkerKernel::new(queue, handlers, worker_config, Some(notifications))?;
        let (shutdown_rx, shutdown_task) = spawn_shutdown_signal();

        tracing::info!(
            process = run_config.process_name,
            worker_id = run_config.worker_id,
            queue = %queue_name,
            task_types = ?run_config.task_types,
            task_type_filter = ?run_config.task_type_filter,
            "queue worker started; only rows stamped for the Rust runtime are claimable"
        );
        let result = worker.run(shutdown_rx).await;
        shutdown_task.abort();
        result.with_context(|| format!("{} stopped unexpectedly", run_config.process_name))
    }
    .await;

    notification_hub.close().await;
    database.close().await;
    let summary = run_result?;
    tracing::info!(
        process = run_config.process_name,
        ?summary,
        "queue worker stopped"
    );
    Ok(())
}

fn queue_for_task_scope(task_types: &[TaskType]) -> Result<TaskQueue> {
    let first = task_types
        .first()
        .copied()
        .ok_or_else(|| anyhow!("queue worker task scope must not be empty"))?;
    let queue = first.spec().queue;
    if let Some(mismatched) = task_types
        .iter()
        .copied()
        .find(|task_type| task_type.spec().queue != queue)
    {
        return Err(anyhow!(
            "queue worker task scope mixes {first} on {queue} with {mismatched} on {}",
            mismatched.spec().queue
        ));
    }
    Ok(queue)
}

/// Installs the configured tracing subscriber for a worker process.
///
/// # Errors
///
/// Returns an error for an invalid filter or when another subscriber is already installed.
pub fn initialize_observability(filter: &str, format: WorkerLogFormat) -> Result<()> {
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

/// Returns the native `PostgreSQL` URL expected by the notification listener.
#[must_use]
pub fn notification_database_url(value: &SecretString) -> String {
    newsly_db::normalize_database_url(value.expose_secret()).into_owned()
}

/// Starts the process signal watcher used by queue-worker kernels.
#[must_use]
pub fn spawn_shutdown_signal() -> (watch::Receiver<bool>, JoinHandle<()>) {
    let (shutdown_tx, shutdown_rx) = watch::channel(false);
    let shutdown_task = tokio::spawn(async move {
        wait_for_shutdown_signal().await;
        shutdown_tx.send_replace(true);
    });
    (shutdown_rx, shutdown_task)
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

#[cfg(test)]
mod tests {
    use super::queue_for_task_scope;
    use newsly_queue::{TaskQueue, TaskType};

    #[test]
    fn task_scope_derives_its_authoritative_queue() {
        assert_eq!(
            queue_for_task_scope(&[TaskType::AnalyzeUrl, TaskType::ProcessContent])
                .expect("content tasks share a queue"),
            TaskQueue::Content
        );
    }

    #[test]
    fn task_scope_rejects_empty_and_mixed_queues() {
        assert!(queue_for_task_scope(&[]).is_err());
        assert!(queue_for_task_scope(&[TaskType::Scrape, TaskType::GenerateImage]).is_err());
    }
}
