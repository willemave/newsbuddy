use sqlx::migrate::{MigrateError, Migrator};
use sqlx::{PgPool, query_scalar};
use thiserror::Error;

use crate::adoption::{
    AdoptionError, MigrationHistoryError, load_history, validate_complete_history,
    validate_runtime_history_prefix,
};
use crate::fingerprint::{FingerprintError, verify_post_migration_catalog};

pub(crate) static MIGRATOR: Migrator = sqlx::migrate!("./migrations");
pub const BASELINE_VERSION: i64 = 20_260_830_000_000;

pub fn embedded_migration_count() -> usize {
    MIGRATOR.iter().count()
}

/// Applies every migration embedded in this exact binary.
///
/// # Errors
///
/// Existing Alembic-head databases must first use `newsly-db baseline`; invoking ordinary migrate
/// on such a database fails before any baseline SQL can run.
pub async fn run_migrations(pool: &PgPool) -> Result<(), MigrationError> {
    run_migrations_with_barrier(pool, false).await
}

/// Applies every embedded migration, allowing a populated database to cross a reviewed
/// maintenance-barrier migration only when the caller explicitly confirms the barrier.
///
/// Fresh databases are intrinsically isolated and receive the session attestation automatically.
///
/// # Errors
///
/// Returns the same errors as [`run_migrations`], including a fail-closed migration error when a
/// populated database reaches an authority cutover without the confirmed barrier.
pub async fn run_migrations_with_barrier(
    pool: &PgPool,
    maintenance_barrier_confirmed: bool,
) -> Result<(), MigrationError> {
    if embedded_migration_count() == 0 {
        return Err(MigrationError::BaselineNotInstalled);
    }
    let mut connection = pool.acquire().await?;
    sqlx::query("SET search_path TO public, pg_catalog")
        .execute(&mut *connection)
        .await?;

    let alembic_exists: bool =
        query_scalar("SELECT pg_catalog.to_regclass('public.alembic_version') IS NOT NULL")
            .fetch_one(&mut *connection)
            .await?;
    let sqlx_exists: bool =
        query_scalar("SELECT pg_catalog.to_regclass('public._sqlx_migrations') IS NOT NULL")
            .fetch_one(&mut *connection)
            .await?;
    let mut fresh_database = !alembic_exists && !sqlx_exists;
    if alembic_exists {
        if !sqlx_exists {
            return Err(MigrationError::AdoptionRequired);
        }
        let history = load_history(&mut connection).await?;
        validate_runtime_history_prefix(&history)?;
    } else if sqlx_exists {
        let history = load_history(&mut connection).await?;
        if history.is_empty() {
            fresh_database = true;
        } else {
            validate_runtime_history_prefix(&history)?;
        }
    }

    if fresh_database || maintenance_barrier_confirmed {
        sqlx::query("SET newsly.maintenance_barrier_confirmed = 'on'")
            .execute(&mut *connection)
            .await?;
    }

    MIGRATOR.run(&mut *connection).await?;
    let history = load_history(&mut connection).await?;
    validate_complete_history(&history)?;
    verify_post_migration_catalog(&mut connection).await?;
    Ok(())
}

#[derive(Debug, Error)]
pub enum MigrationError {
    #[error(
        "the audited Alembic-head SQLx baseline is not installed; refusing to create SQLx migration history"
    )]
    BaselineNotInstalled,
    #[error(
        "this database already has Alembic schema history; run `newsly-db baseline --maintenance-barrier-confirmed` before ordinary SQLx migrations"
    )]
    AdoptionRequired,
    #[error(transparent)]
    Fingerprint(#[from] FingerprintError),
    #[error(transparent)]
    History(#[from] MigrationHistoryError),
    #[error(transparent)]
    Adoption(#[from] AdoptionError),
    #[error("PostgreSQL operation failed while running migrations")]
    Database(#[from] sqlx::Error),
    #[error("SQLx migration failed")]
    Sqlx(#[from] MigrateError),
}
