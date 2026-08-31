use std::fmt::{self, Debug, Formatter};
use std::time::Duration;

use secrecy::{ExposeSecret, SecretString};
use sqlx::postgres::PgConnectOptions;
use std::str::FromStr;
use thiserror::Error;

const SQLALCHEMY_POSTGRES_PREFIXES: [&str; 3] = [
    "postgresql+psycopg://",
    "postgresql+psycopg2://",
    "postgresql+asyncpg://",
];

#[derive(Clone)]
pub struct DatabaseConfig {
    url: SecretString,
    pub application_name: String,
    pub max_connections: u32,
    pub min_connections: u32,
    pub acquire_timeout: Duration,
    pub idle_timeout: Duration,
    pub max_lifetime: Duration,
}

impl Debug for DatabaseConfig {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("DatabaseConfig")
            .field("url", &"[REDACTED]")
            .field("application_name", &self.application_name)
            .field("max_connections", &self.max_connections)
            .field("min_connections", &self.min_connections)
            .field("acquire_timeout", &self.acquire_timeout)
            .field("idle_timeout", &self.idle_timeout)
            .field("max_lifetime", &self.max_lifetime)
            .finish()
    }
}

impl DatabaseConfig {
    pub fn new(url: SecretString, application_name: impl Into<String>) -> Self {
        Self {
            url,
            application_name: application_name.into(),
            max_connections: 5,
            min_connections: 0,
            acquire_timeout: Duration::from_secs(5),
            idle_timeout: Duration::from_secs(10 * 60),
            max_lifetime: Duration::from_secs(30 * 60),
        }
    }

    /// Parses the secret URL into `SQLx` `PostgreSQL` connection options.
    ///
    /// # Errors
    ///
    /// Returns [`DatabaseConfigError::InvalidUrl`] when the configured URL cannot be parsed.
    pub fn connect_options(&self) -> Result<PgConnectOptions, DatabaseConfigError> {
        let normalized = normalize_database_url(self.url.expose_secret());
        PgConnectOptions::from_str(normalized.as_ref())
            .map(|options| options.application_name(&self.application_name))
            .map_err(DatabaseConfigError::InvalidUrl)
    }
}

fn normalize_database_url(value: &str) -> std::borrow::Cow<'_, str> {
    for prefix in SQLALCHEMY_POSTGRES_PREFIXES {
        if let Some(remainder) = value.strip_prefix(prefix) {
            return std::borrow::Cow::Owned(format!("postgresql://{remainder}"));
        }
    }
    std::borrow::Cow::Borrowed(value)
}

#[derive(Debug, Error)]
pub enum DatabaseConfigError {
    #[error("DATABASE_URL is not a valid PostgreSQL connection URL")]
    InvalidUrl(#[source] sqlx::Error),
}
