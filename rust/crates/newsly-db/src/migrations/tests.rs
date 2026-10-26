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

const DECK_QUEUE_VERSION: i64 = 20_261_024_000_000;

async fn legacy_deck_delivery(pool: &PgPool) -> i64 {
    let user: i64 = sqlx::query_scalar("INSERT INTO users(apple_id,email,is_active,is_admin) VALUES('queue-cutover','queue-cutover@example.com',true,false) RETURNING id::bigint")
        .fetch_one(pool).await.unwrap();
    let deck: i64 = sqlx::query_scalar("INSERT INTO learning_decks(user_id,source_kind,source_identity,source_metadata,title,artifact_object_keys,share_enabled) VALUES($1,'github_repo','queue-cutover','{}','Deck','[]',false) RETURNING id::bigint")
        .bind(user).fetch_one(pool).await.unwrap();
    let task: i64 = sqlx::query_scalar("INSERT INTO llm_tasks(user_id,task_kind,mode,workflow_key,workflow_version,workflow_state,status,approval_policy,allowed_actions,tool_policy,input_json,output_json,artifact_manifest,usage_json,status_history,subject_id) VALUES($1,'learning_deck','learning_deck_presentation','learning_deck.presentation.v1',1,'running','running','{}','[]','{}','{}','{}','{}','{}','[]',$2) RETURNING id::bigint")
        .bind(user).bind(deck).fetch_one(pool).await.unwrap();
    sqlx::query_scalar("INSERT INTO processing_tasks(task_type,queue_name,status,owner_user_id,payload,executor_runtime,executor_version,executor_namespace) VALUES('run_llm_task','llm','processing',$1,json_build_object('llm_task_id',$2::bigint,'user_id',$1::bigint),'rust',1,'run_llm_task') RETURNING id::bigint")
        .bind(user).bind(task).fetch_one(pool).await.unwrap()
}

#[sqlx::test(migrations = false)]
async fn deck_queue_cutover_requires_barrier_and_backfills_existing_deliveries(pool: PgPool) {
    prefix(&pool, DECK_QUEUE_VERSION).await;
    let delivery = legacy_deck_delivery(&pool).await;
    let error = run_migrations_with_barrier(&pool, false).await.unwrap_err();
    assert!(
        format!("{error:?}").contains("confirmed maintenance barrier"),
        "{error:?}"
    );
    let marker: Option<String> = sqlx::query_scalar(
        "SELECT payload->>'task_kind' FROM processing_tasks WHERE id::bigint=$1",
    )
    .bind(delivery)
    .fetch_one(&pool)
    .await
    .unwrap();
    assert!(marker.is_none(), "rejected cutover cannot modify payloads");
    run_migrations_with_barrier(&pool, true).await.unwrap();
    verify_migration_history(&pool).await.unwrap();
    let marker: String = sqlx::query_scalar(
        "SELECT payload->>'task_kind' FROM processing_tasks WHERE id::bigint=$1",
    )
    .bind(delivery)
    .fetch_one(&pool)
    .await
    .unwrap();
    assert_eq!(marker, "learning_deck");
    let old_absent: bool =
        sqlx::query_scalar("SELECT to_regclass('uq_llm_tasks_learning_deck_user_active') IS NULL")
            .fetch_one(&pool)
            .await
            .unwrap();
    assert!(old_absent);
    let valid: bool = sqlx::query_scalar("SELECT indisvalid AND indisready FROM pg_index WHERE indexrelid='uq_processing_tasks_learning_deck_owner_processing'::regclass")
        .fetch_one(&pool).await.unwrap();
    assert!(valid);
}

#[sqlx::test(migrations = false)]
async fn deck_queue_cutover_duplicate_claims_roll_back_backfill_and_indexes(pool: PgPool) {
    prefix(&pool, DECK_QUEUE_VERSION).await;
    let delivery = legacy_deck_delivery(&pool).await;
    sqlx::query("INSERT INTO processing_tasks(task_type,queue_name,status,owner_user_id,payload,executor_runtime,executor_version,executor_namespace) SELECT task_type,queue_name,status,owner_user_id,payload,executor_runtime,executor_version,executor_namespace FROM processing_tasks WHERE id::bigint=$1")
        .bind(delivery).execute(&pool).await.unwrap();
    let error = run_migrations_with_barrier(&pool, true).await.unwrap_err();
    assert!(
        format!("{error:?}").contains("multiple reserved Learning Deck tasks"),
        "{error:?}"
    );
    let marker_count: i64 = sqlx::query_scalar(
        "SELECT count(*) FROM processing_tasks WHERE payload->>'task_kind'='learning_deck'",
    )
    .fetch_one(&pool)
    .await
    .unwrap();
    assert_eq!(marker_count, 0, "backfill rolls back with the migration");
    let old_present: bool = sqlx::query_scalar(
        "SELECT to_regclass('uq_llm_tasks_learning_deck_user_active') IS NOT NULL",
    )
    .fetch_one(&pool)
    .await
    .unwrap();
    assert!(old_present);
    let recorded: bool =
        sqlx::query_scalar("SELECT EXISTS(SELECT 1 FROM _sqlx_migrations WHERE version=$1)")
            .bind(DECK_QUEUE_VERSION)
            .fetch_one(&pool)
            .await
            .unwrap();
    assert!(!recorded);
}

#[sqlx::test(migrations = false)]
async fn deck_queue_cutover_rejects_multiple_pending_generation_reservations(pool: PgPool) {
    prefix(&pool, DECK_QUEUE_VERSION).await;
    let delivery = legacy_deck_delivery(&pool).await;
    sqlx::query("UPDATE processing_tasks SET status='pending' WHERE id::bigint=$1")
        .bind(delivery)
        .execute(&pool)
        .await
        .unwrap();
    sqlx::query("INSERT INTO learning_deck_generation_state(llm_task_id,input_fingerprint,deadline_at) SELECT (payload->>'llm_task_id')::integer,$2,now()+interval '10 minutes' FROM processing_tasks WHERE id::bigint=$1")
        .bind(delivery).bind("a".repeat(64)).execute(&pool).await.unwrap();
    sqlx::query("INSERT INTO processing_tasks(task_type,queue_name,status,owner_user_id,payload,executor_runtime,executor_version,executor_namespace) SELECT task_type,queue_name,status,owner_user_id,payload,executor_runtime,executor_version,executor_namespace FROM processing_tasks WHERE id::bigint=$1")
        .bind(delivery).execute(&pool).await.unwrap();
    let error = run_migrations_with_barrier(&pool, true).await.unwrap_err();
    assert!(
        format!("{error:?}").contains("multiple reserved Learning Deck tasks"),
        "{error:?}"
    );
    let count: i64 = sqlx::query_scalar("SELECT count(*) FROM processing_tasks WHERE status='pending' AND payload->>'task_kind' IS NULL")
        .fetch_one(&pool).await.unwrap();
    assert_eq!(
        count, 2,
        "rejected cutover preserves both deliveries and rolls back the backfill"
    );
}

#[sqlx::test(migrations = false)]
async fn deck_queue_generation_lookup_uses_expression_index_with_history(pool: PgPool) {
    prefix(&pool, DECK_QUEUE_VERSION).await;
    let user: i64 = sqlx::query_scalar("INSERT INTO users(apple_id,email,is_active,is_admin) VALUES('generation-history','generation-history@example.com',true,false) RETURNING id::bigint")
        .fetch_one(&pool).await.unwrap();
    // Retained completed generations must not turn each delivery reservation
    // lookup into a scan of retained generation history.
    sqlx::query(
        "WITH history AS (
            INSERT INTO llm_tasks(user_id,task_kind,mode,workflow_key,workflow_version,
                workflow_state,status,approval_policy,allowed_actions,tool_policy,input_json,
                output_json,artifact_manifest,usage_json,status_history)
            SELECT $1,'learning_deck','learning_deck_presentation','learning_deck.presentation.v1',
                1,'completed','completed','{}','[]','{}','{}','{}','{}','{}','[]'
            FROM generate_series(1,10000)
            RETURNING id
        )
        INSERT INTO learning_deck_generation_state(llm_task_id,input_fingerprint,deadline_at)
        SELECT id,repeat('a',64),now()-interval '1 day' FROM history",
    )
    .bind(user)
    .execute(&pool)
    .await
    .unwrap();
    let task: i64 = sqlx::query_scalar(
        "SELECT llm_task_id::bigint FROM learning_deck_generation_state ORDER BY llm_task_id OFFSET 5000 LIMIT 1",
    ).fetch_one(&pool).await.unwrap();
    let delivery: i64 = sqlx::query_scalar(
        "INSERT INTO processing_tasks(task_type,queue_name,status,owner_user_id,payload,
            executor_runtime,executor_version,executor_namespace)
         VALUES('run_llm_task','llm','pending',$1,
            json_build_object('llm_task_id',$2::bigint,'user_id',$1::bigint),
            'rust',1,'run_llm_task') RETURNING id::bigint",
    )
    .bind(user)
    .bind(task)
    .fetch_one(&pool)
    .await
    .unwrap();
    run_migrations_with_barrier(&pool, true).await.unwrap();
    let valid: bool = sqlx::query_scalar(
        "SELECT indisvalid AND indisready FROM pg_index
         WHERE indexrelid='idx_learning_deck_generation_state_task_id_text'::regclass",
    )
    .fetch_one(&pool)
    .await
    .unwrap();
    assert!(
        valid,
        "generation lookup expression index must be ready and valid"
    );
    sqlx::query("ANALYZE learning_deck_generation_state")
        .execute(&pool)
        .await
        .unwrap();
    sqlx::query("ANALYZE processing_tasks")
        .execute(&pool)
        .await
        .unwrap();
    // Keep the same correlated text comparison as the queue and migration guards;
    // in particular, never cast an untrusted JSON ID into an integer.
    let plan: Vec<String> = sqlx::query_scalar(
        "EXPLAIN (COSTS OFF)
         SELECT EXISTS (
             SELECT 1 FROM learning_deck_generation_state AS generation
             WHERE generation.llm_task_id::text = delivery.payload ->> 'llm_task_id'
         )
         FROM processing_tasks AS delivery WHERE delivery.id::bigint=$1",
    )
    .bind(delivery)
    .fetch_all(&pool)
    .await
    .unwrap();
    assert!(
        plan.iter()
            .any(|line| line.contains("idx_learning_deck_generation_state_task_id_text")),
        "populated reservation lookup must use the matching expression index: {plan:#?}"
    );
}

#[sqlx::test(migrations = false)]
async fn deck_queue_cutover_backfills_live_and_terminal_share_action_deliveries(pool: PgPool) {
    prefix(&pool, DECK_QUEUE_VERSION).await;
    let user: i64 = sqlx::query_scalar("INSERT INTO users(apple_id,email,is_active,is_admin) VALUES('share-category-cutover','share-category@example.com',true,false) RETURNING id::bigint")
        .fetch_one(&pool).await.unwrap();
    let mut deliveries = Vec::new();
    for status in ["queued", "completed"] {
        let task: i64 = sqlx::query_scalar("INSERT INTO llm_tasks(user_id,task_kind,mode,workflow_key,workflow_version,workflow_state,status,approval_policy,allowed_actions,tool_policy,input_json,output_json,artifact_manifest,usage_json,status_history) VALUES($1,'share_action','add_content','share_action',1,$2,$2,'{}','[]','{}','{}','{}','{}','{}','[]') RETURNING id::bigint")
            .bind(user).bind(status).fetch_one(&pool).await.unwrap();
        let delivery: i64 = sqlx::query_scalar("INSERT INTO processing_tasks(task_type,queue_name,status,owner_user_id,payload,executor_runtime,executor_version,executor_namespace) VALUES('run_llm_task','llm','pending',$1,json_build_object('llm_task_id',$2::bigint,'user_id',$1::bigint),'rust',1,'run_llm_task') RETURNING id::bigint")
            .bind(user).bind(task).fetch_one(&pool).await.unwrap();
        deliveries.push((task, delivery, status));
    }
    run_migrations_with_barrier(&pool, true).await.unwrap();
    for (task, delivery, status) in deliveries {
        let category: String = sqlx::query_scalar(
            "SELECT payload->>'task_kind' FROM processing_tasks WHERE id::bigint=$1",
        )
        .bind(delivery)
        .fetch_one(&pool)
        .await
        .unwrap();
        assert_eq!(category, "share_action");
        let mut tx = pool.begin().await.unwrap();
        let dispatch = crate::classify_llm_task(&mut tx, task, user).await.unwrap();
        let expected = if status == "completed" {
            crate::LlmTaskDispatchKind::Terminal
        } else {
            crate::LlmTaskDispatchKind::ShareAction
        };
        assert_eq!(dispatch, expected);
        tx.rollback().await.unwrap();
    }
}
