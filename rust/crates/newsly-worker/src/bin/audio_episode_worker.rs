use std::sync::Arc;

use anyhow::{Context, Result};
use newsly_db::Database;
use newsly_domain::{ResourceKey, RuntimeOwner};
use newsly_providers::{AudioEpisodeGateway, AudioEpisodeGatewayConfig};
use newsly_queue::{
    ClaimRequest, ClaimRuntimeScope, QueueKernel, QueueNotificationHub, TaskQueue, TaskType,
};
use newsly_worker::audio_episode::{
    AudioEpisodeFileStore, AudioEpisodeWorkerServices, GenerateAudioEpisodeHandler,
};
use newsly_worker::config::AudioEpisodeWorkerProcessConfig;
use newsly_worker::process::{
    initialize_observability, notification_database_url, spawn_shutdown_signal,
};
use newsly_worker::{HandlerRegistry, WorkerConfig, WorkerKernel};

#[tokio::main]
pub(crate) async fn main() -> Result<()> {
    let config = AudioEpisodeWorkerProcessConfig::from_env()
        .context("invalid Newsly Rust audio worker configuration")?;
    initialize_observability(&config.log_filter, config.log_format)
        .context("audio worker observability initialization failed")?;

    let database = Database::connect_lazy(&config.database)
        .context("audio worker database configuration failed")?;
    database
        .check()
        .await
        .context("audio worker PostgreSQL readiness check failed")?;
    let queue = QueueKernel::new(database.pool().clone());
    let gateway = AudioEpisodeGateway::new(AudioEpisodeGatewayConfig {
        credentials: config.provider_credentials.clone(),
        openrouter_policy: config.openrouter_policy.clone(),
        script_model: config.script_model.clone(),
        script_timeout: config.script_timeout,
        elevenlabs_api_base: config.elevenlabs_api_base.clone(),
        elevenlabs_api_key: config.elevenlabs_api_key.clone(),
        host_voice_id: config.host_voice_id.clone(),
        guest_voice_id: config.guest_voice_id.clone(),
        tts_model: config.tts_model.clone(),
        output_format: config.output_format.clone(),
        voice_speed: config.voice_speed,
        max_parallel_tts_requests: config.max_parallel_tts_requests,
        max_tts_response_bytes: config.max_tts_response_bytes,
        ffmpeg_binary: config.ffmpeg_binary.clone(),
    })
    .context("audio provider initialization failed")?;
    let file_store = AudioEpisodeFileStore::new(config.media_root.clone())
        .context("audio file storage initialization failed")?;
    let services = Arc::new(AudioEpisodeWorkerServices::new(
        database.pool().clone(),
        gateway,
        file_store,
        config.max_retries,
    ));

    let mut handlers = HandlerRegistry::new();
    handlers.register(GenerateAudioEpisodeHandler::new(services))?;
    let scope = ClaimRuntimeScope::namespaces(
        RuntimeOwner::Rust,
        [ResourceKey::new(TaskType::GenerateAudioEpisode.as_str())?],
    )?;
    let mut claim =
        ClaimRequest::for_queue(config.worker_id.clone(), TaskQueue::AudioEpisode, scope);
    claim.task_type = Some(TaskType::GenerateAudioEpisode);
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
        queue = %TaskQueue::AudioEpisode,
        task_type = %TaskType::GenerateAudioEpisode,
        "Newsly Rust audio worker started; only rows stamped for the Rust runtime are claimable"
    );
    let run_result = worker.run(shutdown_rx).await;
    shutdown_task.abort();
    notification_hub.close().await;
    database.close().await;
    let summary = run_result.context("Newsly Rust audio worker stopped unexpectedly")?;
    tracing::info!(?summary, "Newsly Rust audio worker stopped");
    Ok(())
}
