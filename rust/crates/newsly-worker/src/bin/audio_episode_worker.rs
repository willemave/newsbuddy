use std::sync::Arc;

use anyhow::{Context, Result};
use newsly_db::Database;
use newsly_providers::{AudioEpisodeGateway, AudioEpisodeGatewayConfig};
use newsly_queue::{QueueKernel, TaskType};
use newsly_worker::HandlerRegistry;
use newsly_worker::audio_episode::{
    AudioEpisodeFileStore, AudioEpisodeWorkerServices, GenerateAudioEpisodeHandler,
};
use newsly_worker::config::AudioEpisodeWorkerProcessConfig;
use newsly_worker::process::{
    QueueWorkerRunConfig, initialize_observability, notification_database_url, run_queue_worker,
};

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
    run_queue_worker(
        database,
        queue,
        handlers,
        notification_database_url(config.database_url()),
        QueueWorkerRunConfig {
            process_name: "Newsly Rust audio worker",
            worker_id: &config.worker_id,
            lease_duration: config.lease_duration,
            max_retries: config.max_retries,
            task_types: &[TaskType::GenerateAudioEpisode],
            task_type_filter: Some(TaskType::GenerateAudioEpisode),
        },
    )
    .await
}
