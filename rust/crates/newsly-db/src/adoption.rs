use sqlx::migrate::MigrateError;
use sqlx::{Connection, PgConnection, query_as, query_scalar};
use thiserror::Error;

use crate::fingerprint::{
    FingerprintError, verify_baseline_fingerprint, verify_post_migration_catalog,
};
use crate::migrations::{BASELINE_VERSION, MIGRATOR};
use crate::{DatabaseConfig, DatabaseConfigError};

// `hashtextextended('newsly.sqlx.baseline-adoption', 0)` resolved once and pinned so every
// release and PostgreSQL version contends on the same session-level lock.
const ADOPTION_ADVISORY_LOCK_KEY: i64 = -7_050_487_948_454_250_724;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct AdoptionReport {
    pub baseline_version: i64,
    pub pending_migrations_applied: usize,
}

/// Verifies and adopts a database already built through the frozen Alembic head.
///
/// This function is intentionally exposed only for the `newsly-db baseline` operator command.
/// Callers must stop and drain every application and migration writer before invoking it. The
/// dedicated connection and advisory lock close the verification-to-SQLx-history race among
/// cooperating Newsly migration processes; they cannot make an active Alembic writer safe.
///
/// # Errors
///
/// Fails closed when the lock is held, the catalog/data/role fingerprint differs, `SQLx` history is
/// not empty or an exact embedded prefix through the baseline, or a pending migration fails.
pub async fn adopt_existing_database(
    config: &DatabaseConfig,
) -> Result<AdoptionReport, AdoptionError> {
    let options = config.connect_options()?;
    let mut connection = PgConnection::connect_with(&options).await?;
    sqlx::query("SET search_path TO public, pg_catalog")
        .execute(&mut connection)
        .await?;

    let acquired: bool = query_scalar("SELECT pg_catalog.pg_try_advisory_lock($1)")
        .bind(ADOPTION_ADVISORY_LOCK_KEY)
        .fetch_one(&mut connection)
        .await?;
    if !acquired {
        return Err(AdoptionError::AdvisoryLockUnavailable);
    }

    let result = adopt_while_locked(&mut connection).await;
    let unlock_result = query_scalar::<_, bool>("SELECT pg_catalog.pg_advisory_unlock($1)")
        .bind(ADOPTION_ADVISORY_LOCK_KEY)
        .fetch_one(&mut connection)
        .await;

    match result {
        Err(error) => Err(error),
        Ok(report) => {
            let unlocked = unlock_result?;
            if !unlocked {
                return Err(AdoptionError::AdvisoryUnlockFailed);
            }
            Ok(report)
        }
    }
}

/// Performs the read-only eligibility checks used before entering the maintenance barrier.
///
/// # Errors
///
/// Returns an error when the connection cannot be established, the catalog/data/role evidence
/// differs, or existing `SQLx` history is not empty or the exact baseline prefix.
pub async fn verify_existing_baseline(config: &DatabaseConfig) -> Result<(), AdoptionError> {
    let options = config.connect_options()?;
    let mut connection = PgConnection::connect_with(&options).await?;
    sqlx::query("SET search_path TO public, pg_catalog")
        .execute(&mut connection)
        .await?;
    verify_baseline_fingerprint(&mut connection).await?;
    validate_adoption_history(&load_history(&mut connection).await?)?;
    Ok(())
}

async fn adopt_while_locked(
    connection: &mut PgConnection,
) -> Result<AdoptionReport, AdoptionError> {
    let history = load_history(connection).await?;
    if history.is_empty() {
        verify_baseline_fingerprint(connection).await?;
        validate_adoption_history(&history)?;

        MIGRATOR
            .skip(&mut *connection, Some(BASELINE_VERSION))
            .await?;
        validate_baseline_recorded(&load_history(connection).await?)?;
    } else {
        // A checksum-valid prefix can only be produced atomically by this embedded migrator. It
        // is therefore the recovery marker for interruption after `skip` or between later
        // transactional migrations. When only the baseline marker exists, the database must still
        // match the exact pre-migration fingerprint before the first post-baseline migration runs.
        // Longer prefixes have already changed that catalog and are checked against their exact
        // embedded checksums plus the final catalog invariants below.
        validate_runtime_history_prefix(&history)?;
        if history.len() == 1 {
            verify_baseline_fingerprint(connection).await?;
        }
    }

    let pending_migrations_applied = expected_up_migrations(None)
        .len()
        .saturating_sub(load_history(connection).await?.len());
    sqlx::query("SET newsly.maintenance_barrier_confirmed = 'on'")
        .execute(&mut *connection)
        .await?;
    MIGRATOR.run(&mut *connection).await?;
    validate_complete_history(&load_history(connection).await?)?;
    verify_post_migration_catalog(connection).await?;

    Ok(AdoptionReport {
        baseline_version: BASELINE_VERSION,
        pending_migrations_applied,
    })
}

#[derive(Debug, Clone, PartialEq, Eq, sqlx::FromRow)]
pub(crate) struct HistoryRow {
    version: i64,
    description: String,
    success: bool,
    checksum: Vec<u8>,
    execution_time: i64,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct ExpectedMigration {
    version: i64,
    description: String,
    checksum: Vec<u8>,
}

pub(crate) async fn load_history(
    connection: &mut PgConnection,
) -> Result<Vec<HistoryRow>, AdoptionError> {
    let history_exists: bool =
        query_scalar("SELECT pg_catalog.to_regclass('public._sqlx_migrations') IS NOT NULL")
            .fetch_one(&mut *connection)
            .await?;
    if !history_exists {
        return Ok(Vec::new());
    }

    query_as::<_, HistoryRow>(
        r"
        SELECT version, description, success, checksum, execution_time
        FROM public._sqlx_migrations
        ORDER BY version
        ",
    )
    .fetch_all(connection)
    .await
    .map_err(AdoptionError::Sqlx)
}

fn expected_up_migrations(maximum_version: Option<i64>) -> Vec<ExpectedMigration> {
    MIGRATOR
        .iter()
        .filter(|migration| migration.migration_type.is_up_migration())
        .filter(|migration| maximum_version.is_none_or(|maximum| migration.version <= maximum))
        .map(|migration| ExpectedMigration {
            version: migration.version,
            description: migration.description.to_string(),
            checksum: migration.checksum.to_vec(),
        })
        .collect()
}

fn validate_adoption_history(history: &[HistoryRow]) -> Result<(), MigrationHistoryError> {
    if let Some(newer) = history.iter().find(|row| row.version > BASELINE_VERSION) {
        return Err(MigrationHistoryError::NewerVersion(newer.version));
    }
    validate_prefix(
        history,
        &expected_up_migrations(Some(BASELINE_VERSION)),
        true,
    )
}

fn validate_baseline_recorded(history: &[HistoryRow]) -> Result<(), MigrationHistoryError> {
    validate_prefix(
        history,
        &expected_up_migrations(Some(BASELINE_VERSION)),
        false,
    )
}

pub(crate) fn validate_runtime_history_prefix(
    history: &[HistoryRow],
) -> Result<(), MigrationHistoryError> {
    if history.is_empty() {
        return Err(MigrationHistoryError::MissingVersion(BASELINE_VERSION));
    }
    validate_prefix(history, &expected_up_migrations(None), true)
}

pub(crate) fn validate_complete_history(
    history: &[HistoryRow],
) -> Result<(), MigrationHistoryError> {
    validate_prefix(history, &expected_up_migrations(None), false)
}

fn validate_prefix(
    history: &[HistoryRow],
    expected: &[ExpectedMigration],
    partial_prefix_allowed: bool,
) -> Result<(), MigrationHistoryError> {
    if history.len() > expected.len() {
        let unexpected = history
            .get(expected.len())
            .expect("history is known to be longer than expected");
        return Err(MigrationHistoryError::UnexpectedVersion(unexpected.version));
    }
    for (index, row) in history.iter().enumerate() {
        if !row.success {
            return Err(MigrationHistoryError::DirtyVersion(row.version));
        }
        let expected_migration = &expected[index];
        if row.version != expected_migration.version {
            return Err(MigrationHistoryError::Gap {
                expected: expected_migration.version,
                actual: row.version,
            });
        }
        if row.description != expected_migration.description {
            return Err(MigrationHistoryError::DescriptionMismatch(row.version));
        }
        if row.checksum != expected_migration.checksum {
            return Err(MigrationHistoryError::ChecksumMismatch(row.version));
        }
        if row.execution_time < -1 {
            return Err(MigrationHistoryError::InvalidExecutionTime(row.version));
        }
        if row.execution_time == -1 && row.version != BASELINE_VERSION {
            return Err(MigrationHistoryError::UnexpectedSkippedVersion(row.version));
        }
    }

    if !partial_prefix_allowed && history.len() != expected.len() {
        let missing = expected
            .get(history.len())
            .map_or(BASELINE_VERSION, |migration| migration.version);
        return Err(MigrationHistoryError::MissingVersion(missing));
    }
    Ok(())
}

#[derive(Debug, Error, PartialEq, Eq)]
pub enum MigrationHistoryError {
    #[error("SQLx migration history contains dirty version {0}")]
    DirtyVersion(i64),
    #[error("SQLx migration history contains newer version {0} before baseline adoption")]
    NewerVersion(i64),
    #[error("SQLx migration history contains unknown version {0}")]
    UnexpectedVersion(i64),
    #[error("SQLx migration history is missing required version {0}")]
    MissingVersion(i64),
    #[error("SQLx migration history has a gap: expected {expected}, found {actual}")]
    Gap { expected: i64, actual: i64 },
    #[error("SQLx migration description does not match embedded version {0}")]
    DescriptionMismatch(i64),
    #[error("SQLx migration checksum does not match embedded version {0}")]
    ChecksumMismatch(i64),
    #[error("SQLx migration history has invalid execution time for version {0}")]
    InvalidExecutionTime(i64),
    #[error("only the audited baseline may be recorded as skipped; found skipped version {0}")]
    UnexpectedSkippedVersion(i64),
}

#[derive(Debug, Error)]
pub enum AdoptionError {
    #[error(transparent)]
    Config(#[from] DatabaseConfigError),
    #[error("could not acquire the Newsly baseline-adoption advisory lock")]
    AdvisoryLockUnavailable,
    #[error("the Newsly baseline-adoption advisory lock was not held at release")]
    AdvisoryUnlockFailed,
    #[error(transparent)]
    Fingerprint(#[from] FingerprintError),
    #[error(transparent)]
    History(#[from] MigrationHistoryError),
    #[error("SQLx migration adoption failed")]
    Migrate(#[from] MigrateError),
    #[error("PostgreSQL operation failed during baseline adoption")]
    Sqlx(#[from] sqlx::Error),
}

#[cfg(test)]
mod tests {
    use sqlx::{Executor, PgPool};

    use crate::fingerprint::{FingerprintError, verify_baseline_fingerprint};
    use crate::migrations::{MIGRATOR, run_migrations};

    use super::{
        BASELINE_VERSION, HistoryRow, MigrationHistoryError, adopt_while_locked,
        expected_up_migrations, validate_adoption_history, validate_baseline_recorded,
        validate_complete_history,
    };

    const BASELINE_SQL: &str =
        include_str!("../migrations/20260830000000_alembic_20260829_02_baseline.sql");

    fn exact_row(index: usize) -> HistoryRow {
        let migration = expected_up_migrations(None)
            .into_iter()
            .nth(index)
            .expect("fixture migration should exist");
        HistoryRow {
            version: migration.version,
            description: migration.description,
            success: true,
            checksum: migration.checksum,
            execution_time: if index == 0 { -1 } else { 0 },
        }
    }

    #[test]
    fn empty_adoption_history_is_allowed() {
        validate_adoption_history(&[]).expect("empty history should be adoptable");
    }

    #[test]
    fn exact_baseline_prefix_is_resumable() {
        validate_adoption_history(&[exact_row(0)]).expect("baseline prefix should be resumable");
        validate_baseline_recorded(&[exact_row(0)]).expect("baseline should be recorded");
    }

    #[test]
    fn dirty_and_checksum_mismatched_histories_are_rejected() {
        let mut dirty = exact_row(0);
        dirty.success = false;
        assert_eq!(
            validate_adoption_history(&[dirty]),
            Err(MigrationHistoryError::DirtyVersion(BASELINE_VERSION))
        );

        let mut mismatched = exact_row(0);
        mismatched.checksum[0] ^= 0xff;
        assert_eq!(
            validate_adoption_history(&[mismatched]),
            Err(MigrationHistoryError::ChecksumMismatch(BASELINE_VERSION))
        );
    }

    #[test]
    fn gaps_and_non_baseline_skips_are_rejected() {
        let runtime = exact_row(1);
        assert_eq!(
            validate_complete_history(&[exact_row(0), exact_row(2)]),
            Err(MigrationHistoryError::Gap {
                expected: runtime.version,
                actual: exact_row(2).version,
            })
        );

        let mut skipped_runtime = runtime;
        skipped_runtime.execution_time = -1;
        assert_eq!(
            validate_complete_history(&[exact_row(0), skipped_runtime, exact_row(2)]),
            Err(MigrationHistoryError::UnexpectedSkippedVersion(
                exact_row(1).version
            ))
        );
    }

    #[test]
    fn malformed_history_metadata_is_rejected() {
        let mut wrong_description = exact_row(0);
        wrong_description.description.push_str("-unexpected");
        assert_eq!(
            validate_adoption_history(&[wrong_description]),
            Err(MigrationHistoryError::DescriptionMismatch(BASELINE_VERSION))
        );

        let mut invalid_execution_time = exact_row(0);
        invalid_execution_time.execution_time = -2;
        assert_eq!(
            validate_adoption_history(&[invalid_execution_time]),
            Err(MigrationHistoryError::InvalidExecutionTime(
                BASELINE_VERSION
            ))
        );
    }

    #[test]
    fn newer_history_is_rejected_during_adoption() {
        let rows = [exact_row(0), exact_row(1)];
        assert_eq!(
            validate_adoption_history(&rows),
            Err(MigrationHistoryError::NewerVersion(rows[1].version))
        );
    }

    #[test]
    fn complete_history_requires_every_embedded_up_migration() {
        assert!(matches!(
            validate_complete_history(&[exact_row(0)]),
            Err(MigrationHistoryError::MissingVersion(_))
        ));
        let rows: Vec<_> = (0..expected_up_migrations(None).len())
            .map(exact_row)
            .collect();
        validate_complete_history(&rows).expect("full history should validate");
    }

    #[sqlx::test(migrations = false)]
    async fn fresh_database_migrates_from_baseline_through_sqlx_head(pool: PgPool) {
        run_migrations(&pool)
            .await
            .expect("fresh database should migrate to the embedded head");
        let runtime_registry_exists: bool = sqlx::query_scalar(
            "SELECT pg_catalog.to_regclass('public.runtime_ownership') IS NOT NULL",
        )
        .fetch_one(&pool)
        .await
        .expect("catalog query should succeed");
        assert!(runtime_registry_exists);
        let python_owner_count: i64 = sqlx::query_scalar(
            "SELECT count(*)::bigint FROM runtime_ownership WHERE active_owner = 'python'",
        )
        .fetch_one(&pool)
        .await
        .expect("ownership query should succeed");
        assert_eq!(python_owner_count, 0);
        let executor_default_count: i64 = sqlx::query_scalar(
            r"
            SELECT count(*)::bigint
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'processing_tasks'
              AND column_name IN (
                  'executor_runtime',
                  'executor_version',
                  'executor_namespace'
              )
              AND column_default IS NOT NULL
            ",
        )
        .fetch_one(&pool)
        .await
        .expect("column default query should succeed");
        assert_eq!(executor_default_count, 0);
    }

    #[sqlx::test(migrations = false)]
    async fn direct_migrator_allows_a_fresh_database_without_a_barrier(pool: PgPool) {
        MIGRATOR
            .run(&pool)
            .await
            .expect("fresh database should not require a maintenance barrier");
    }

    #[sqlx::test(migrations = false)]
    async fn skipped_baseline_cannot_cross_authority_without_a_barrier(pool: PgPool) {
        pool.execute(BASELINE_SQL)
            .await
            .expect("baseline fixture should install");
        let mut connection = pool
            .acquire()
            .await
            .expect("connection should be available");
        MIGRATOR
            .skip(&mut *connection, Some(BASELINE_VERSION))
            .await
            .expect("adopted baseline marker should be recorded");

        let error = MIGRATOR
            .run(&mut *connection)
            .await
            .expect_err("adopted baseline must require the maintenance barrier");
        assert!(
            error
                .to_string()
                .contains("requires a confirmed maintenance barrier"),
            "unexpected migration error: {error}"
        );
    }

    #[sqlx::test(migrations = false)]
    async fn adoption_transfers_only_active_python_work_to_rust(pool: PgPool) {
        pool.execute(BASELINE_SQL)
            .await
            .expect("baseline fixture should install");
        sqlx::query(
            r"
            INSERT INTO processing_tasks (task_type, payload, status, queue_name)
            VALUES
                ('summarize', '{}'::json, 'pending', 'content'),
                ('summarize', '{}'::json, 'completed', 'content')
            ",
        )
        .execute(&pool)
        .await
        .expect("legacy queue fixtures should insert");
        let mut connection = pool
            .acquire()
            .await
            .expect("connection should be available");

        adopt_while_locked(&mut connection)
            .await
            .expect("Alembic snapshot should adopt");

        let rows = sqlx::query_as::<_, (String, String, i64, String)>(
            r"
            SELECT status, executor_runtime, executor_version, executor_namespace
            FROM processing_tasks
            ORDER BY id
            ",
        )
        .fetch_all(&mut *connection)
        .await
        .expect("transferred queue fixtures should load");
        assert_eq!(
            rows,
            vec![
                (
                    "pending".to_owned(),
                    "rust".to_owned(),
                    2,
                    "summarize".to_owned(),
                ),
                (
                    "completed".to_owned(),
                    "python".to_owned(),
                    1,
                    "summarize".to_owned(),
                ),
            ]
        );
    }

    #[sqlx::test(migrations = false)]
    async fn alembic_snapshot_adopts_and_rerun_is_idempotent(pool: PgPool) {
        pool.execute(BASELINE_SQL)
            .await
            .expect("baseline fixture should install");
        let mut connection = pool
            .acquire()
            .await
            .expect("connection should be available");

        let first = adopt_while_locked(&mut connection)
            .await
            .expect("Alembic snapshot should adopt");
        assert_eq!(
            first.pending_migrations_applied,
            expected_up_migrations(None).len() - 1
        );

        let second = adopt_while_locked(&mut connection)
            .await
            .expect("adoption rerun should be idempotent");
        assert_eq!(second.pending_migrations_applied, 0);
    }

    #[sqlx::test(migrations = false)]
    async fn exact_baseline_history_prefix_resumes_pending_migrations(pool: PgPool) {
        pool.execute(BASELINE_SQL)
            .await
            .expect("baseline fixture should install");
        let mut connection = pool
            .acquire()
            .await
            .expect("connection should be available");
        MIGRATOR
            .skip(&mut *connection, Some(BASELINE_VERSION))
            .await
            .expect("baseline history should be recorded");

        let report = adopt_while_locked(&mut connection)
            .await
            .expect("checksum-valid prefix should resume");
        assert_eq!(
            report.pending_migrations_applied,
            expected_up_migrations(None).len() - 1
        );
    }

    #[sqlx::test(migrations = false)]
    async fn schema_drift_is_rejected_before_history_is_written(pool: PgPool) {
        pool.execute(BASELINE_SQL)
            .await
            .expect("baseline fixture should install");
        pool.execute("ALTER TABLE public.users ADD COLUMN unreviewed_drift text")
            .await
            .expect("fixture drift should install");
        let mut connection = pool
            .acquire()
            .await
            .expect("connection should be available");

        let error = verify_baseline_fingerprint(&mut connection)
            .await
            .expect_err("schema drift must fail verification");
        assert!(matches!(error, FingerprintError::SnapshotMismatch { .. }));
    }

    #[sqlx::test(migrations = false)]
    async fn required_data_drift_is_rejected(pool: PgPool) {
        pool.execute(BASELINE_SQL)
            .await
            .expect("baseline fixture should install");
        pool.execute(
            r"
            INSERT INTO public.users (apple_id, email, is_admin, is_active)
            VALUES ('baseline-test-user', 'baseline-test@example.invalid', false, true);
            INSERT INTO public.chat_sessions (
                user_id,
                llm_model,
                llm_provider,
                council_mode,
                is_hidden_from_history
            )
            VALUES (1, 'cerebras:retired', 'cerebras', false, false)
            ",
        )
        .await
        .expect("fixture data drift should install");
        let mut connection = pool
            .acquire()
            .await
            .expect("connection should be available");

        let error = verify_baseline_fingerprint(&mut connection)
            .await
            .expect_err("required data drift must fail verification");
        assert!(matches!(error, FingerprintError::SnapshotMismatch { .. }));
    }

    #[sqlx::test(migrations = false)]
    async fn retired_reddit_aggregator_data_is_rejected(pool: PgPool) {
        pool.execute(BASELINE_SQL)
            .await
            .expect("baseline fixture should install");
        pool.execute(
            r#"
            INSERT INTO public.users (apple_id, email, is_admin, is_active)
            VALUES ('reddit-baseline-test-user', 'reddit-baseline-test@example.invalid', false, true);
            INSERT INTO public.user_scraper_configs (
                user_id,
                scraper_type,
                feed_url,
                config
            )
            VALUES (1, 'aggregator', 'aggregator://reddit', '{"key":"reddit"}')
            "#,
        )
        .await
        .expect("fixture data drift should install");
        let mut connection = pool
            .acquire()
            .await
            .expect("connection should be available");

        let error = verify_baseline_fingerprint(&mut connection)
            .await
            .expect_err("retired Reddit aggregator state must fail verification");
        assert!(matches!(error, FingerprintError::SnapshotMismatch { .. }));
    }

    #[sqlx::test(migrations = false)]
    async fn alembic_head_mismatch_is_rejected(pool: PgPool) {
        pool.execute(BASELINE_SQL)
            .await
            .expect("baseline fixture should install");
        pool.execute("UPDATE public.alembic_version SET version_num = 'wrong_head'")
            .await
            .expect("fixture head should update");
        let mut connection = pool
            .acquire()
            .await
            .expect("connection should be available");

        let error = verify_baseline_fingerprint(&mut connection)
            .await
            .expect_err("wrong Alembic head must fail verification");
        assert!(matches!(error, FingerprintError::AlembicHead { .. }));
    }
}
