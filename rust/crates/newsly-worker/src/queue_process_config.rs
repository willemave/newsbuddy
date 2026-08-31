use std::env;
use std::fmt::{self, Debug, Formatter};
use std::time::Duration;

use newsly_db::DatabaseConfig;
use secrecy::SecretString;
use thiserror::Error;

use crate::config::WorkerLogFormat;

/// Shared fail-closed process configuration for queue workers without dedicated provider or
/// storage settings. Provider gateways continue to validate their own environment separately.
#[derive(Clone)]
pub struct QueueWorkerProcessConfig {
    database_url: SecretString,
    pub database: DatabaseConfig,
    pub worker_id: String,
    pub lease_duration: Duration,
    pub max_retries: i32,
    pub log_filter: String,
    pub log_format: WorkerLogFormat,
}

impl Debug for QueueWorkerProcessConfig {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("QueueWorkerProcessConfig")
            .field("database_url", &"[REDACTED]")
            .field("database", &self.database)
            .field("worker_id", &self.worker_id)
            .field("lease_duration", &self.lease_duration)
            .field("max_retries", &self.max_retries)
            .field("log_filter", &self.log_filter)
            .field("log_format", &self.log_format)
            .finish()
    }
}

impl QueueWorkerProcessConfig {
    pub fn from_env(
        application_name: &'static str,
        default_worker_prefix: &'static str,
    ) -> Result<Self, QueueWorkerProcessConfigError> {
        let database_url = env::var("DATABASE_URL")
            .ok()
            .filter(|value| !value.trim().is_empty())
            .map(SecretString::from)
            .ok_or(QueueWorkerProcessConfigError::Missing("DATABASE_URL"))?;
        let mut database = DatabaseConfig::new(database_url.clone(), application_name.to_owned());
        database.max_connections = parse_u32("NEWSLY_RUST_WORKER_DATABASE_MAX_CONNECTIONS", 8)?;
        database.min_connections = parse_u32("NEWSLY_RUST_WORKER_DATABASE_MIN_CONNECTIONS", 0)?;
        database.acquire_timeout = Duration::from_millis(parse_u64(
            "NEWSLY_RUST_WORKER_DATABASE_ACQUIRE_TIMEOUT_MS",
            5_000,
        )?);
        if database.max_connections == 0 || database.min_connections > database.max_connections {
            return Err(QueueWorkerProcessConfigError::Range(
                "NEWSLY_RUST_WORKER_DATABASE connection bounds",
            ));
        }
        let worker_id = env::var("NEWSLY_RUST_WORKER_ID").unwrap_or_else(|_| {
            let host = env::var("HOSTNAME").unwrap_or_else(|_| "local".to_owned());
            format!("{default_worker_prefix}-{host}-{}", std::process::id())
        });
        if worker_id.trim().is_empty() || worker_id.len() > 100 {
            return Err(QueueWorkerProcessConfigError::Range(
                "NEWSLY_RUST_WORKER_ID",
            ));
        }
        let lease_duration =
            Duration::from_secs(parse_u64("NEWSLY_RUST_WORKER_LEASE_SECONDS", 300)?);
        if lease_duration.is_zero() {
            return Err(QueueWorkerProcessConfigError::Range(
                "NEWSLY_RUST_WORKER_LEASE_SECONDS",
            ));
        }
        let max_retries = parse_i64("MAX_TASK_RETRIES", 3)?;
        if !(0..=i64::from(i32::MAX)).contains(&max_retries) {
            return Err(QueueWorkerProcessConfigError::Range("MAX_TASK_RETRIES"));
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
                return Err(QueueWorkerProcessConfigError::Invalid(
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
                .map_err(|_| QueueWorkerProcessConfigError::Range("MAX_TASK_RETRIES"))?,
            log_filter: env::var("RUST_LOG")
                .unwrap_or_else(|_| "newsly_worker=info,newsly_queue=info".to_owned()),
            log_format,
        })
    }

    pub const fn database_url(&self) -> &SecretString {
        &self.database_url
    }
}

fn parse_u64(name: &'static str, default: u64) -> Result<u64, QueueWorkerProcessConfigError> {
    env::var(name).map_or(Ok(default), |value| {
        value
            .parse()
            .map_err(|_| QueueWorkerProcessConfigError::Invalid(name))
    })
}

fn parse_u32(name: &'static str, default: u32) -> Result<u32, QueueWorkerProcessConfigError> {
    env::var(name).map_or(Ok(default), |value| {
        value
            .parse()
            .map_err(|_| QueueWorkerProcessConfigError::Invalid(name))
    })
}

fn parse_i64(name: &'static str, default: i64) -> Result<i64, QueueWorkerProcessConfigError> {
    env::var(name).map_or(Ok(default), |value| {
        value
            .parse()
            .map_err(|_| QueueWorkerProcessConfigError::Invalid(name))
    })
}

#[derive(Debug, Error)]
pub enum QueueWorkerProcessConfigError {
    #[error("missing required setting {0}")]
    Missing(&'static str),
    #[error("setting {0} is invalid")]
    Invalid(&'static str),
    #[error("setting {0} is outside its supported range")]
    Range(&'static str),
}
