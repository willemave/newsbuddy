use std::env;
use std::fmt::{self, Debug, Formatter};
use std::path::PathBuf;
use std::time::Duration;

use newsly_db::DatabaseConfig;
use secrecy::SecretString;

use crate::config::WorkerLogFormat;

#[derive(Clone)]
pub struct AgentDataWorkerProcessConfig {
    database_url: SecretString,
    pub database: DatabaseConfig,
    pub worker_id: String,
    pub lease_duration: Duration,
    pub max_retries: i32,
    pub log_filter: String,
    pub log_format: WorkerLogFormat,
    pub mirror_root: PathBuf,
    pub content_body_local_root: PathBuf,
    pub max_document_bytes: usize,
    pub backfill_batch_size: i64,
    pub index_debounce_seconds: i64,
}

impl Debug for AgentDataWorkerProcessConfig {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("AgentDataWorkerProcessConfig")
            .field("database_url", &"[REDACTED]")
            .field("database", &self.database)
            .field("worker_id", &self.worker_id)
            .field("lease_duration", &self.lease_duration)
            .field("max_retries", &self.max_retries)
            .field("log_filter", &self.log_filter)
            .field("log_format", &self.log_format)
            .field("mirror_root", &self.mirror_root)
            .field("content_body_local_root", &self.content_body_local_root)
            .field("max_document_bytes", &self.max_document_bytes)
            .field("backfill_batch_size", &self.backfill_batch_size)
            .field("index_debounce_seconds", &self.index_debounce_seconds)
            .finish()
    }
}

impl AgentDataWorkerProcessConfig {
    /// Loads the isolated Agent Data worker configuration without provider credentials.
    pub fn from_env() -> Result<Self, AgentDataWorkerConfigError> {
        let database_url = env::var("DATABASE_URL")
            .ok()
            .filter(|value| !value.trim().is_empty())
            .map(SecretString::from)
            .ok_or(AgentDataWorkerConfigError::Missing("DATABASE_URL"))?;
        let mut database = DatabaseConfig::new(database_url.clone(), "newsly-agent-data-worker");
        database.max_connections = parse_u32("NEWSLY_AGENT_DATA_DATABASE_MAX_CONNECTIONS", 8)?;
        database.min_connections = parse_u32("NEWSLY_AGENT_DATA_DATABASE_MIN_CONNECTIONS", 0)?;
        database.acquire_timeout = Duration::from_millis(parse_u64(
            "NEWSLY_AGENT_DATA_DATABASE_ACQUIRE_TIMEOUT_MS",
            5_000,
        )?);
        if database.max_connections == 0 || database.min_connections > database.max_connections {
            return Err(AgentDataWorkerConfigError::Range(
                "NEWSLY_AGENT_DATA_DATABASE connection bounds",
            ));
        }
        let worker_id = env::var("NEWSLY_AGENT_DATA_WORKER_ID").unwrap_or_else(|_| {
            let host = env::var("HOSTNAME").unwrap_or_else(|_| "local".to_owned());
            format!("rust-agent-data-{host}-{}", std::process::id())
        });
        if worker_id.trim().is_empty() || worker_id.len() > 100 {
            return Err(AgentDataWorkerConfigError::Range(
                "NEWSLY_AGENT_DATA_WORKER_ID",
            ));
        }
        let lease_duration =
            Duration::from_secs(parse_u64("NEWSLY_AGENT_DATA_WORKER_LEASE_SECONDS", 300)?);
        if lease_duration.is_zero() {
            return Err(AgentDataWorkerConfigError::Range(
                "NEWSLY_AGENT_DATA_WORKER_LEASE_SECONDS",
            ));
        }
        let max_retries = parse_i64("MAX_TASK_RETRIES", 3)?;
        if !(0..=i64::from(i32::MAX)).contains(&max_retries) {
            return Err(AgentDataWorkerConfigError::Range("MAX_TASK_RETRIES"));
        }
        let max_document_bytes = parse_usize("AGENT_DATA_DOCUMENT_MAX_BYTES", 200_000)?;
        if !(10_000..=2_000_000).contains(&max_document_bytes) {
            return Err(AgentDataWorkerConfigError::Range(
                "AGENT_DATA_DOCUMENT_MAX_BYTES",
            ));
        }
        let backfill_batch_size = parse_i64("AGENT_DATA_BACKFILL_BATCH_SIZE", 500)?;
        if !(25..=2_000).contains(&backfill_batch_size) {
            return Err(AgentDataWorkerConfigError::Range(
                "AGENT_DATA_BACKFILL_BATCH_SIZE",
            ));
        }
        let index_debounce_seconds = parse_i64("AGENT_DATA_INDEX_DEBOUNCE_SECONDS", 300)?;
        if !(0..=86_400).contains(&index_debounce_seconds) {
            return Err(AgentDataWorkerConfigError::Range(
                "AGENT_DATA_INDEX_DEBOUNCE_SECONDS",
            ));
        }
        let log_format = match env::var("NEWSLY_RUST_LOG_FORMAT")
            .unwrap_or_else(|_| "json".to_owned())
            .trim()
            .to_ascii_lowercase()
            .as_str()
        {
            "json" => WorkerLogFormat::Json,
            "pretty" | "text" => WorkerLogFormat::Pretty,
            _ => {
                return Err(AgentDataWorkerConfigError::Invalid(
                    "NEWSLY_RUST_LOG_FORMAT",
                ));
            }
        };
        Ok(Self {
            database_url,
            database,
            worker_id,
            lease_duration,
            max_retries: i32::try_from(max_retries)
                .map_err(|_| AgentDataWorkerConfigError::Range("MAX_TASK_RETRIES"))?,
            log_filter: env::var("RUST_LOG")
                .unwrap_or_else(|_| "newsly_worker=info,newsly_queue=info".to_owned()),
            log_format,
            mirror_root: env::var_os("AGENT_DATA_MIRROR_ROOT")
                .map_or_else(|| PathBuf::from("./data/agent_user_data"), PathBuf::from),
            content_body_local_root: env::var_os("CONTENT_BODY_LOCAL_ROOT")
                .map_or_else(|| PathBuf::from("./data/content_bodies"), PathBuf::from),
            max_document_bytes,
            backfill_batch_size,
            index_debounce_seconds,
        })
    }

    pub const fn database_url(&self) -> &SecretString {
        &self.database_url
    }
}

fn parse_u64(name: &'static str, default: u64) -> Result<u64, AgentDataWorkerConfigError> {
    env::var(name).map_or(Ok(default), |value| {
        value
            .parse()
            .map_err(|_| AgentDataWorkerConfigError::Invalid(name))
    })
}

fn parse_u32(name: &'static str, default: u32) -> Result<u32, AgentDataWorkerConfigError> {
    env::var(name).map_or(Ok(default), |value| {
        value
            .parse()
            .map_err(|_| AgentDataWorkerConfigError::Invalid(name))
    })
}

fn parse_i64(name: &'static str, default: i64) -> Result<i64, AgentDataWorkerConfigError> {
    env::var(name).map_or(Ok(default), |value| {
        value
            .parse()
            .map_err(|_| AgentDataWorkerConfigError::Invalid(name))
    })
}

fn parse_usize(name: &'static str, default: usize) -> Result<usize, AgentDataWorkerConfigError> {
    env::var(name).map_or(Ok(default), |value| {
        value
            .parse()
            .map_err(|_| AgentDataWorkerConfigError::Invalid(name))
    })
}

#[derive(Debug, thiserror::Error)]
pub enum AgentDataWorkerConfigError {
    #[error("missing required setting {0}")]
    Missing(&'static str),
    #[error("setting {0} is invalid")]
    Invalid(&'static str),
    #[error("setting {0} is outside its supported range")]
    Range(&'static str),
}
