use sqlx::{PgPool, migrate::Migrator};

use super::{MIGRATOR, run_migrations_with_barrier, verify_migration_history};

const GROUPING_VERSION: i64 = 20_261_018_000_001;
const INDEX_VERSION: i64 = 20_261_018_000_002;

#[test]
fn deployment_probe_tracks_post_adoption_barrier_migrations() {
    let listed: Vec<i64> = include_str!(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/../../../scripts/deploy/maintenance-migrations"
    ))
    .lines()
    .map(|version| version.parse().unwrap())
    .collect();
    let guarded: Vec<i64> = MIGRATOR
        .iter()
        .filter(|migration| {
            migration.version >= 20_261_002_010_000
                && migration
                    .sql
                    .as_str()
                    .contains("newsly.maintenance_barrier_confirmed")
        })
        .map(|migration| migration.version)
        .collect();
    assert_eq!(listed, guarded);
}

async fn prefix(pool: &PgPool, before: i64) {
    Migrator::with_migrations(
        MIGRATOR
            .iter()
            .filter(|migration| migration.version < before)
            .cloned()
            .collect(),
    )
    .run(pool)
    .await
    .unwrap();
}

async fn populate(pool: &PgPool) {
    sqlx::query("INSERT INTO news_items(ingest_key,visibility_scope,platform,status,ingested_at,created_at) VALUES('cutover-test','global','hackernews','new',now(),now())")
        .execute(pool).await.unwrap();
}

async fn assert_index_absent(pool: &PgPool) {
    let absent: bool = sqlx::query_scalar(
        "SELECT to_regclass('public.uq_processing_tasks_briefing_owner_processing') IS NULL",
    )
    .fetch_one(pool)
    .await
    .unwrap();
    assert!(absent);
    let recorded: bool =
        sqlx::query_scalar("SELECT EXISTS(SELECT 1 FROM _sqlx_migrations WHERE version=$1)")
            .bind(INDEX_VERSION)
            .fetch_one(pool)
            .await
            .unwrap();
    assert!(!recorded);
}

#[sqlx::test(migrations = false)]
async fn grouping_cutover_refuses_populated_online_database(pool: PgPool) {
    prefix(&pool, GROUPING_VERSION).await;
    populate(&pool).await;
    verify_migration_history(&pool).await.unwrap();
    let error = run_migrations_with_barrier(&pool, false).await.unwrap_err();
    assert!(
        format!("{error:?}").contains("confirmed maintenance barrier"),
        "{error:?}"
    );
    let trigger_exists: bool = sqlx::query_scalar(
        "SELECT EXISTS(SELECT 1 FROM pg_trigger WHERE tgname='news_grouping_write_guard')",
    )
    .fetch_one(&pool)
    .await
    .unwrap();
    assert!(!trigger_exists);
    assert_index_absent(&pool).await;
    run_migrations_with_barrier(&pool, true).await.unwrap();
    verify_migration_history(&pool).await.unwrap();
    let valid: bool = sqlx::query_scalar("SELECT indisvalid AND indisready FROM pg_index WHERE indexrelid='uq_processing_tasks_briefing_owner_processing'::regclass")
        .fetch_one(&pool).await.unwrap();
    assert!(valid);
}

#[sqlx::test(migrations = false)]
async fn resumed_owner_index_still_requires_current_barrier(pool: PgPool) {
    prefix(&pool, INDEX_VERSION).await;
    populate(&pool).await;
    // An earlier use of a pooled connection must not attest this invocation.
    sqlx::query("SET newsly.maintenance_barrier_confirmed = 'on'")
        .execute(&pool)
        .await
        .unwrap();
    let error = run_migrations_with_barrier(&pool, false).await.unwrap_err();
    assert!(
        format!("{error:?}").contains("confirmed maintenance barrier"),
        "{error:?}"
    );
    assert_index_absent(&pool).await;
    run_migrations_with_barrier(&pool, true).await.unwrap();
}

#[sqlx::test(migrations = false)]
async fn duplicate_owner_rejection_rolls_back_index_and_history(pool: PgPool) {
    prefix(&pool, INDEX_VERSION).await;
    let user_id: i64 = sqlx::query_scalar("INSERT INTO users(apple_id,email,is_active,is_admin) VALUES('cutover-owner','cutover@example.com',true,false) RETURNING id::bigint")
        .fetch_one(&pool).await.unwrap();
    sqlx::query("INSERT INTO processing_tasks(task_type,payload,status,queue_name,owner_user_id,locked_at,locked_by,lease_token,lease_expires_at,executor_runtime,executor_version,executor_namespace)
        SELECT 'briefing_refresh',json_build_object('user_id',$1::bigint),'processing','llm',$1::bigint::integer,now(),'old-worker',gen_random_uuid(),now()+interval '5 minutes','rust',1,'briefing_refresh' FROM generate_series(1,2)")
        .bind(user_id).execute(&pool).await.unwrap();
    let error = run_migrations_with_barrier(&pool, true).await.unwrap_err();
    assert!(
        format!("{error:?}").contains("multiple processing Briefing tasks"),
        "{error:?}"
    );
    assert_index_absent(&pool).await;
    // Model a settled old owner in this isolated fixture; deploy never edits task ownership.
    sqlx::query("UPDATE processing_tasks SET status='completed',lease_token=NULL,locked_by=NULL,locked_at=NULL,lease_expires_at=NULL WHERE id=(SELECT min(id) FROM processing_tasks)")
        .execute(&pool).await.unwrap();
    run_migrations_with_barrier(&pool, true).await.unwrap();
}

#[sqlx::test(migrations = false)]
async fn history_preflight_accepts_fresh_and_prefix_without_mutation(pool: PgPool) {
    verify_migration_history(&pool).await.unwrap();
    let absent: bool = sqlx::query_scalar("SELECT to_regclass('public._sqlx_migrations') IS NULL")
        .fetch_one(&pool)
        .await
        .unwrap();
    assert!(absent);
    prefix(&pool, GROUPING_VERSION).await;
    let before: i64 = sqlx::query_scalar("SELECT count(*) FROM _sqlx_migrations")
        .fetch_one(&pool)
        .await
        .unwrap();
    verify_migration_history(&pool).await.unwrap();
    let after: i64 = sqlx::query_scalar("SELECT count(*) FROM _sqlx_migrations")
        .fetch_one(&pool)
        .await
        .unwrap();
    assert_eq!(before, after);
}

#[sqlx::test(migrations = false)]
async fn history_preflight_rejects_changed_checksum_before_cutover(pool: PgPool) {
    prefix(&pool, GROUPING_VERSION).await;
    sqlx::query("UPDATE _sqlx_migrations SET checksum=decode('00','hex') WHERE version=(SELECT max(version) FROM _sqlx_migrations)")
        .execute(&pool).await.unwrap();
    let error = verify_migration_history(&pool).await.unwrap_err();
    assert!(error.to_string().contains("checksum"), "{error:?}");
    assert_index_absent(&pool).await;
}
