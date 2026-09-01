use std::sync::Arc;

use anyhow::{Context, Result};
use newsly_db::Database;
use newsly_domain::{ResourceKey, RuntimeOwner};
use newsly_providers::{MediaGateway, MediaGatewayConfig, OpenAiTranscriptionGateway};
use newsly_queue::{
    ClaimRequest, ClaimRuntimeScope, QueueKernel, QueueNotificationHub, TaskQueue, TaskType,
};
use newsly_worker::media::{
    DownloadTweetVideoAudioHandler, MediaFileStore, MediaWorkerProcessConfig, MediaWorkerServices,
    ProcessPodcastMediaHandler, TranscribeTweetVideoHandler,
};
use newsly_worker::process::{
    initialize_observability, notification_database_url, spawn_shutdown_signal,
};
use newsly_worker::{HandlerRegistry, WorkerConfig, WorkerKernel};

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

    let notification_hub =
        QueueNotificationHub::spawn(notification_database_url(config.database_url()));
    let notifications = notification_hub.subscribe();
    let mut worker = WorkerKernel::new(queue, handlers, worker_config, Some(notifications))?;
    let (shutdown_rx, shutdown_task) = spawn_shutdown_signal();

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
