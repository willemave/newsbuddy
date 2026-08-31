use std::sync::Arc;

use anyhow::{Context, Result, anyhow};
use newsly_db::Database;
use newsly_domain::{ResourceKey, RuntimeOwner};
use newsly_providers::{MediaGateway, MediaGatewayConfig, OpenAiTranscriptionGateway};
use newsly_queue::{
    ClaimRequest, ClaimRuntimeScope, QueueKernel, QueueNotificationHub, TaskQueue, TaskType,
};
use newsly_worker::config::WorkerLogFormat;
use newsly_worker::media::{
    DownloadTweetVideoAudioHandler, MediaFileStore, MediaWorkerProcessConfig, MediaWorkerServices,
    ProcessPodcastMediaHandler, TranscribeTweetVideoHandler,
};
use newsly_worker::{HandlerRegistry, WorkerConfig, WorkerKernel};
use secrecy::ExposeSecret;
use tokio::sync::watch;
use tracing_subscriber::EnvFilter;

#[tokio::main]
async fn main() -> Result<()> {
    let config = MediaWorkerProcessConfig::from_env()
        .context("invalid Newsly Rust media-worker configuration")?;
    initialize_observability(&config.log_filter, config.log_format)
        .context("media-worker observability initialization failed")?;

    let database = Database::connect_lazy(&config.database)
        .context("media-worker database configuration failed")?;
    database
        .check()
        .await
        .context("media-worker PostgreSQL readiness check failed")?;
    let queue = QueueKernel::new(database.pool().clone());
    let media_gateway = MediaGateway::new(MediaGatewayConfig {
        request_timeout: config.request_timeout,
        yt_dlp_timeout: config.yt_dlp_timeout,
        ffmpeg_timeout: config.ffmpeg_timeout,
        max_media_bytes: config.max_media_bytes,
        max_redirects: config.max_redirects,
        yt_dlp_binary: config.yt_dlp_binary.clone(),
        ffmpeg_binary: config.ffmpeg_binary.clone(),
        youtube_cookie_file: config.youtube_cookie_file.clone(),
        youtube_player_client: config.youtube_player_client.clone(),
        youtube_po_token_provider: config.youtube_po_token_provider.clone(),
        youtube_po_token_base_url: config.youtube_po_token_base_url.clone(),
        itunes_country: config.itunes_country.clone(),
    })
    .context("media download gateway initialization failed")?;
    let transcription_gateway = OpenAiTranscriptionGateway::new(
        &config.openai_api_key,
        config.openai_api_base.as_deref(),
        config.transcription_timeout,
    )
    .context("media transcription gateway initialization failed")?;
    let file_store = MediaFileStore::new(
        config.podcast_scratch_root.clone(),
        config.tweet_media_root.clone(),
        config.content_body_local_root.clone(),
        config.content_body_storage_prefix.clone(),
        config.max_media_bytes,
    )
    .context("media file storage initialization failed")?;
    let services = Arc::new(MediaWorkerServices::new(
        database.pool().clone(),
        queue.clone(),
        media_gateway,
        transcription_gateway,
        file_store,
        config.tweet_video_enabled,
    ));

    let mut handlers = HandlerRegistry::new();
    handlers.register(ProcessPodcastMediaHandler::new(Arc::clone(&services)))?;
    handlers.register(DownloadTweetVideoAudioHandler::new(Arc::clone(&services)))?;
    handlers.register(TranscribeTweetVideoHandler::new(services))?;
    let scope = ClaimRuntimeScope::namespaces(
        RuntimeOwner::Rust,
        [
            ResourceKey::new(TaskType::ProcessPodcastMedia.as_str())?,
            ResourceKey::new(TaskType::DownloadTweetVideoAudio.as_str())?,
            ResourceKey::new(TaskType::TranscribeTweetVideo.as_str())?,
        ],
    )?;
    let mut claim = ClaimRequest::for_queue(config.worker_id.clone(), TaskQueue::Media, scope);
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
        queue = %TaskQueue::Media,
        task_types = "process_podcast_media,download_tweet_video_audio,transcribe_tweet_video",
        tweet_video_enabled = config.tweet_video_enabled,
        "Newsly Rust media worker started; only rows stamped for the Rust runtime are claimable"
    );
    let run_result = worker.run(shutdown_rx).await;
    shutdown_task.abort();
    notification_hub.close().await;
    database.close().await;
    let summary = run_result.context("Newsly Rust media worker stopped unexpectedly")?;
    tracing::info!(?summary, "Newsly Rust media worker stopped");
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
