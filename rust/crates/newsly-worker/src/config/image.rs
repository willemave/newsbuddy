use super::{WorkerConfigError, load_queue_process, parse_i64};
use crate::queue_process_config::{QueueWorkerProcessConfig, QueueWorkerProcessEnv};
use std::{
    env,
    fmt::{self, Debug, Formatter},
    ops::Deref,
    path::PathBuf,
};

#[derive(Clone)]
pub struct ImageWorkerProcessConfig {
    pub process: QueueWorkerProcessConfig,
    pub images_base_dir: PathBuf,
    pub briefing_debounce_seconds: i64,
    pub briefing_batch_minimum: i64,
}

impl Debug for ImageWorkerProcessConfig {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("ImageWorkerProcessConfig")
            .field("process", &self.process)
            .field("images_base_dir", &self.images_base_dir)
            .field("briefing_debounce_seconds", &self.briefing_debounce_seconds)
            .field("briefing_batch_minimum", &self.briefing_batch_minimum)
            .finish()
    }
}

impl ImageWorkerProcessConfig {
    /// Loads queue, `PostgreSQL`, and local storage settings for the isolated image partition.
    /// Provider credentials and model fallback policy are validated separately by the provider
    /// gateway before the queue loop starts.
    pub fn from_env() -> Result<Self, WorkerConfigError> {
        let process = load_queue_process(
            "newsly-image-worker",
            "rust-image",
            QueueWorkerProcessEnv::dedicated(
                "NEWSLY_IMAGE_DATABASE_MAX_CONNECTIONS",
                "NEWSLY_IMAGE_DATABASE_MIN_CONNECTIONS",
                "NEWSLY_IMAGE_DATABASE_ACQUIRE_TIMEOUT_MS",
                "NEWSLY_IMAGE_DATABASE connection bounds",
                "NEWSLY_IMAGE_WORKER_ID",
                "NEWSLY_IMAGE_WORKER_LEASE_SECONDS",
                "newsly_worker=info,newsly_queue=info,newsly_providers=info",
            ),
        )?;
        let images_base_dir = env::var_os("IMAGES_BASE_DIR")
            .map_or_else(|| PathBuf::from("./data/images"), PathBuf::from);
        let briefing_debounce_seconds = parse_i64("BRIEFING_DEBOUNCE_SECONDS", 900)?;
        if !(0..=86_400).contains(&briefing_debounce_seconds) {
            return Err(WorkerConfigError::InvalidRange("BRIEFING_DEBOUNCE_SECONDS"));
        }
        let briefing_batch_minimum = parse_i64("BRIEFING_WINDOW_MIN", 3)?;
        if !(1..=12).contains(&briefing_batch_minimum) {
            return Err(WorkerConfigError::InvalidRange("BRIEFING_WINDOW_MIN"));
        }
        Ok(Self {
            process,
            images_base_dir,
            briefing_debounce_seconds,
            briefing_batch_minimum,
        })
    }
}

impl Deref for ImageWorkerProcessConfig {
    type Target = QueueWorkerProcessConfig;

    fn deref(&self) -> &Self::Target {
        &self.process
    }
}
