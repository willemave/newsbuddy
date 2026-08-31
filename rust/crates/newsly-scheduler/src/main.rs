use anyhow::{Context, Result, anyhow};
use newsly_db::Database;
use newsly_scheduler::{Scheduler, SchedulerConfig, SchedulerLogFormat, SchedulerRepository};
use tokio::sync::watch;
use tracing_subscriber::EnvFilter;

#[tokio::main]
async fn main() -> Result<()> {
    let config = SchedulerConfig::from_env().context("invalid Newsly scheduler configuration")?;
    initialize_observability(&config.log_filter, config.log_format)
        .context("scheduler observability initialization failed")?;
    let database = Database::connect_lazy(&config.database)
        .context("scheduler database configuration failed")?;
    database
        .check()
        .await
        .context("scheduler PostgreSQL readiness check failed")?;
    tracing::info!(
        instance_id = %config.instance_id,
        poll_seconds = config.poll_interval.as_secs(),
        "Newsly native Rust scheduler started"
    );
    let repository = SchedulerRepository::new(database.pool().clone());
    let scheduler = Scheduler::new(repository, config);
    let (shutdown_tx, shutdown_rx) = watch::channel(false);
    let shutdown_task = tokio::spawn(async move {
        wait_for_shutdown_signal().await;
        shutdown_tx.send_replace(true);
    });

    scheduler.run(shutdown_rx).await;
    shutdown_task.abort();
    database.close().await;
    tracing::info!("Newsly native Rust scheduler stopped");
    Ok(())
}

fn initialize_observability(filter: &str, format: SchedulerLogFormat) -> Result<()> {
    let filter = EnvFilter::try_new(filter).context("RUST_LOG contains an invalid filter")?;
    match format {
        SchedulerLogFormat::Json => tracing_subscriber::fmt()
            .with_env_filter(filter)
            .json()
            .with_current_span(true)
            .with_span_list(true)
            .try_init()
            .map_err(|error| anyhow!("could not install JSON tracing subscriber: {error}"))?,
        SchedulerLogFormat::Pretty => tracing_subscriber::fmt()
            .with_env_filter(filter)
            .pretty()
            .try_init()
            .map_err(|error| anyhow!("could not install pretty tracing subscriber: {error}"))?,
    }
    Ok(())
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
