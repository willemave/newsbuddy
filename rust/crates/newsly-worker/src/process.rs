//! Shared queue-worker process plumbing.
//!
//! Service construction stays in each binary so provider and task ownership remain explicit.
//! This module owns only the identical observability, database-listener URL, and shutdown setup.

use anyhow::{Context, Result, anyhow};
use secrecy::{ExposeSecret, SecretString};
use tokio::sync::watch;
use tokio::task::JoinHandle;
use tracing_subscriber::EnvFilter;

use crate::config::WorkerLogFormat;

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
