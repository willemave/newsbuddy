use super::{
    WorkerConfigError, WorkerLogFormat, parse_i32, parse_i64, parse_u32, parse_u64, required_secret,
};
use newsly_db::DatabaseConfig;
use secrecy::SecretString;
use std::{
    env,
    fmt::{self, Debug, Formatter},
    path::PathBuf,
    time::Duration,
};

#[derive(Clone)]
pub struct ImageWorkerProcessConfig {
    database_url: SecretString,
    pub database: DatabaseConfig,
    pub worker_id: String,
    pub lease_duration: Duration,
    pub max_retries: i32,
    pub log_filter: String,
    pub log_format: WorkerLogFormat,
    pub images_base_dir: PathBuf,
    pub briefing_debounce_seconds: i64,
    pub briefing_batch_minimum: i64,
}

impl Debug for ImageWorkerProcessConfig {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("ImageWorkerProcessConfig")
            .field("database_url", &"[REDACTED]")
            .field("database", &self.database)
            .field("worker_id", &self.worker_id)
            .field("lease_duration", &self.lease_duration)
            .field("max_retries", &self.max_retries)
            .field("log_filter", &self.log_filter)
            .field("log_format", &self.log_format)
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
        let database_url = required_secret("DATABASE_URL")?;
        let mut database = DatabaseConfig::new(database_url.clone(), "newsly-image-worker");
        database.max_connections = parse_u32("NEWSLY_IMAGE_DATABASE_MAX_CONNECTIONS", 8)?;
        database.min_connections = parse_u32("NEWSLY_IMAGE_DATABASE_MIN_CONNECTIONS", 0)?;
        database.acquire_timeout = Duration::from_millis(parse_u64(
            "NEWSLY_IMAGE_DATABASE_ACQUIRE_TIMEOUT_MS",
            5_000,
        )?);
        if database.max_connections == 0 || database.min_connections > database.max_connections {
            return Err(WorkerConfigError::InvalidRange(
                "NEWSLY_IMAGE_DATABASE connection bounds",
            ));
        }
        let worker_id = env::var("NEWSLY_IMAGE_WORKER_ID").unwrap_or_else(|_| {
            let host = env::var("HOSTNAME").unwrap_or_else(|_| "local".to_owned());
            format!("rust-image-{host}-{}", std::process::id())
        });
        if worker_id.trim().is_empty() || worker_id.len() > 100 {
            return Err(WorkerConfigError::InvalidRange("NEWSLY_IMAGE_WORKER_ID"));
        }
        let lease_duration =
            Duration::from_secs(parse_u64("NEWSLY_IMAGE_WORKER_LEASE_SECONDS", 300)?);
        if lease_duration.is_zero() {
            return Err(WorkerConfigError::InvalidRange(
                "NEWSLY_IMAGE_WORKER_LEASE_SECONDS",
            ));
        }
        let max_retries = parse_i32("MAX_TASK_RETRIES", 3)?;
        if max_retries < 0 {
            return Err(WorkerConfigError::InvalidRange("MAX_TASK_RETRIES"));
        }
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
            database_url,
            database,
            worker_id,
            lease_duration,
            max_retries,
            log_filter: env::var("RUST_LOG").unwrap_or_else(|_| {
                "newsly_worker=info,newsly_queue=info,newsly_providers=info".to_owned()
            }),
            log_format: WorkerLogFormat::parse(
                &env::var("NEWSLY_RUST_LOG_FORMAT").unwrap_or_else(|_| "json".to_owned()),
            )?,
            images_base_dir,
            briefing_debounce_seconds,
            briefing_batch_minimum,
        })
    }

    pub const fn database_url(&self) -> &SecretString {
        &self.database_url
    }
}
