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
        let env = QueueWorkerProcessEnv::standard();
        Self::from_env_with(application_name, default_worker_prefix, env)
    }

    pub fn from_env_with(
        application_name: &'static str,
        default_worker_prefix: &'static str,
        names: QueueWorkerProcessEnv,
    ) -> Result<Self, QueueWorkerProcessConfigError> {
        let database_url = required_secret(
            "DATABASE_URL",
            names.trim_database_url,
            names.empty_database_url_is_missing,
        )?;
        let mut database = DatabaseConfig::new(database_url.clone(), application_name.to_owned());
        database.max_connections = parse_u32(
            names.database_max_connections,
            names.default_max_connections,
        )?;
        database.min_connections = parse_u32(names.database_min_connections, 0)?;
        database.acquire_timeout =
            Duration::from_millis(parse_u64(names.database_acquire_timeout_ms, 5_000)?);
        if database.max_connections == 0 || database.min_connections > database.max_connections {
            return Err(QueueWorkerProcessConfigError::Range(names.database_bounds));
        }
        let worker_id = env::var(names.worker_id).unwrap_or_else(|_| {
            let host = env::var("HOSTNAME").unwrap_or_else(|_| "local".to_owned());
            format!("{default_worker_prefix}-{host}-{}", std::process::id())
        });
        if worker_id.trim().is_empty() || worker_id.len() > 100 {
            return Err(QueueWorkerProcessConfigError::Range(names.worker_id));
        }
        let lease_duration = Duration::from_secs(parse_u64(names.lease_seconds, 300)?);
        if lease_duration.is_zero() {
            return Err(QueueWorkerProcessConfigError::Range(names.lease_seconds));
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
                .unwrap_or_else(|_| names.default_log_filter.to_owned()),
            log_format,
        })
    }

    pub const fn database_url(&self) -> &SecretString {
        &self.database_url
    }
}

#[derive(Debug, Clone, Copy)]
pub struct QueueWorkerProcessEnv {
    pub database_max_connections: &'static str,
    pub database_min_connections: &'static str,
    pub database_acquire_timeout_ms: &'static str,
    pub database_bounds: &'static str,
    pub default_max_connections: u32,
    pub worker_id: &'static str,
    pub lease_seconds: &'static str,
    pub default_log_filter: &'static str,
    pub trim_database_url: bool,
    pub empty_database_url_is_missing: bool,
}

impl QueueWorkerProcessEnv {
    pub const fn standard() -> Self {
        Self {
            database_max_connections: "NEWSLY_RUST_WORKER_DATABASE_MAX_CONNECTIONS",
            database_min_connections: "NEWSLY_RUST_WORKER_DATABASE_MIN_CONNECTIONS",
            database_acquire_timeout_ms: "NEWSLY_RUST_WORKER_DATABASE_ACQUIRE_TIMEOUT_MS",
            database_bounds: "NEWSLY_RUST_WORKER_DATABASE connection bounds",
            default_max_connections: 8,
            worker_id: "NEWSLY_RUST_WORKER_ID",
            lease_seconds: "NEWSLY_RUST_WORKER_LEASE_SECONDS",
            default_log_filter: "newsly_worker=info,newsly_queue=info,newsly_providers=info",
            trim_database_url: true,
            empty_database_url_is_missing: true,
        }
    }

    pub const fn dedicated(
        database_max_connections: &'static str,
        database_min_connections: &'static str,
        database_acquire_timeout_ms: &'static str,
        database_bounds: &'static str,
        worker_id: &'static str,
        lease_seconds: &'static str,
        default_log_filter: &'static str,
    ) -> Self {
        Self {
            database_max_connections,
            database_min_connections,
            database_acquire_timeout_ms,
            database_bounds,
            default_max_connections: 8,
            worker_id,
            lease_seconds,
            default_log_filter,
            trim_database_url: false,
            empty_database_url_is_missing: false,
        }
    }
}

fn required_secret(
    name: &'static str,
    trim: bool,
    empty_is_missing: bool,
) -> Result<SecretString, QueueWorkerProcessConfigError> {
    let value = env::var(name).map_err(|error| match error {
        env::VarError::NotPresent => QueueWorkerProcessConfigError::Missing(name),
        env::VarError::NotUnicode(_) => QueueWorkerProcessConfigError::InvalidUnicode(name),
    })?;
    let empty = if trim {
        value.trim().is_empty()
    } else {
        value.is_empty()
    };
    if empty {
        return Err(if empty_is_missing {
            QueueWorkerProcessConfigError::Missing(name)
        } else {
            QueueWorkerProcessConfigError::Empty(name)
        });
    }
    Ok(SecretString::from(value))
}

fn parse_u64(name: &'static str, default: u64) -> Result<u64, QueueWorkerProcessConfigError> {
    parse_number(name, default)
}

fn parse_u32(name: &'static str, default: u32) -> Result<u32, QueueWorkerProcessConfigError> {
    parse_number(name, default)
}

fn parse_i64(name: &'static str, default: i64) -> Result<i64, QueueWorkerProcessConfigError> {
    parse_number(name, default)
}

fn parse_number<T>(name: &'static str, default: T) -> Result<T, QueueWorkerProcessConfigError>
where
    T: std::str::FromStr,
{
    match env::var(name) {
        Ok(value) => value
            .parse()
            .map_err(|_| QueueWorkerProcessConfigError::InvalidNumber(name, value)),
        Err(env::VarError::NotPresent) => Ok(default),
        Err(env::VarError::NotUnicode(_)) => {
            Err(QueueWorkerProcessConfigError::InvalidUnicode(name))
        }
    }
}

#[derive(Debug, Error)]
pub enum QueueWorkerProcessConfigError {
    #[error("missing required setting {0}")]
    Missing(&'static str),
    #[error("required setting {0} cannot be empty")]
    Empty(&'static str),
    #[error("setting {0} is not valid Unicode")]
    InvalidUnicode(&'static str),
    #[error("setting {0} has invalid number {1:?}")]
    InvalidNumber(&'static str, String),
    #[error("setting {0} is invalid")]
    Invalid(&'static str),
    #[error("setting {0} is outside its supported range")]
    Range(&'static str),
}
