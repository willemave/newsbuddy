use sqlx::PgPool;
use sqlx::postgres::PgPoolOptions;
use thiserror::Error;

use crate::{DatabaseConfig, DatabaseConfigError};

#[derive(Debug, Clone)]
pub struct Database {
    pool: PgPool,
}

impl Database {
    /// Build a pool without performing network I/O.
    ///
    /// Startup therefore remains observable when `PostgreSQL` is unavailable;
    /// readiness is the authority for whether traffic may be routed here.
    ///
    /// # Errors
    ///
    /// Returns [`DatabaseError::Config`] if the database URL is invalid.
    pub fn connect_lazy(config: &DatabaseConfig) -> Result<Self, DatabaseError> {
        let options = config.connect_options()?;
        let pool = PgPoolOptions::new()
            .max_connections(config.max_connections)
            .min_connections(config.min_connections)
            .acquire_timeout(config.acquire_timeout)
            .idle_timeout(Some(config.idle_timeout))
            .max_lifetime(Some(config.max_lifetime))
            .connect_lazy_with(options);
        Ok(Self { pool })
    }

    pub const fn pool(&self) -> &PgPool {
        &self.pool
    }

    /// Performs a minimal database round trip.
    ///
    /// # Errors
    ///
    /// Returns [`DatabaseError::Sqlx`] when the query fails, or
    /// [`DatabaseError::UnexpectedHealthValue`] if `PostgreSQL` does not return `1`.
    pub async fn check(&self) -> Result<(), DatabaseError> {
        let value = sqlx::query_scalar!(r#"SELECT 1 AS "value!""#)
            .fetch_one(&self.pool)
            .await?;
        if value == 1 {
            Ok(())
        } else {
            Err(DatabaseError::UnexpectedHealthValue(value))
        }
    }

    pub async fn close(self) {
        self.pool.close().await;
    }
}

#[derive(Debug, Error)]
pub enum DatabaseError {
    #[error(transparent)]
    Config(#[from] DatabaseConfigError),
    #[error("PostgreSQL operation failed")]
    Sqlx(#[from] sqlx::Error),
    #[error("PostgreSQL health query returned unexpected value {0}")]
    UnexpectedHealthValue(i32),
}
