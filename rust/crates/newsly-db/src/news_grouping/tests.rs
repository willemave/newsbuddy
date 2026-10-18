use super::*;
use sqlx::PgPool;

#[sqlx::test(migrations = false)]
async fn scope_guard_is_nonblocking_and_separates_exact_visibility_owners(pool: PgPool) {
    crate::run_migrations(&pool).await.unwrap();
    let mut first = pool.begin().await.unwrap();
    let mut second = pool.begin().await.unwrap();
    assert!(try_lock_scope(&mut first, "global", None).await.unwrap());
    assert!(!try_lock_scope(&mut second, "global", None).await.unwrap());
    assert!(
        try_lock_scope(&mut second, "global", Some(7))
            .await
            .unwrap()
    );
    assert!(try_lock_scope(&mut second, "user", Some(7)).await.unwrap());
    assert!(try_lock_scope(&mut first, "user", Some(8)).await.unwrap());
    first.rollback().await.unwrap();
    assert!(try_lock_scope(&mut second, "global", None).await.unwrap());
}

#[sqlx::test(migrations = false)]
async fn contention_classifier_requires_our_stable_constraint(pool: PgPool) {
    for (sql, expected) in [
        (
            "DO $$ BEGIN RAISE EXCEPTION 'busy' USING ERRCODE='40001', CONSTRAINT='news_grouping_scope_busy'; END $$",
            true,
        ),
        (
            "DO $$ BEGIN RAISE EXCEPTION 'busy' USING ERRCODE='40001', CONSTRAINT='other_conflict'; END $$",
            false,
        ),
    ] {
        let error = sqlx::query(sql).execute(&pool).await.unwrap_err();
        assert_eq!(is_contention(&error), expected);
    }
    assert!(!is_contention(&sqlx::Error::RowNotFound));
}

async fn ready_item(pool: &PgPool, key: &str, scope: &str, owner: Option<i32>) -> i32 {
    sqlx::query_scalar("INSERT INTO news_items(ingest_key,visibility_scope,owner_user_id,status,raw_metadata,ingested_at,created_at) VALUES($1,$2,$3,'ready','{}',now(),now()) RETURNING id")
        .bind(key).bind(scope).bind(owner).fetch_one(pool).await.unwrap()
}

#[sqlx::test(migrations = false)]
async fn ready_writer_matrix_fails_closed_but_volatile_and_private_preparation_writes_continue(
    pool: PgPool,
) {
    crate::run_migrations(&pool).await.unwrap();
    let id = ready_item(&pool, "matrix", "global", None).await;
    sqlx::query(
        "UPDATE news_items SET raw_metadata='{\"aggregator\":{\"metadata\":{}}}' WHERE id=$1",
    )
    .bind(id)
    .execute(&pool)
    .await
    .unwrap();
    let mut guard = pool.begin().await.unwrap();
    assert!(try_lock_scope(&mut guard, "global", None).await.unwrap());
    for sql in [
        "UPDATE news_items SET status='failed' WHERE id=$1",
        "UPDATE news_items SET visibility_scope='user',owner_user_id=7 WHERE id=$1",
        "UPDATE news_items SET representative_news_item_id=123 WHERE id=$1",
        "UPDATE news_items SET summary_text='Changed' WHERE id=$1",
        "UPDATE news_items SET summary_key_points='[\"Changed\"]'::json WHERE id=$1",
        "UPDATE news_items SET canonical_story_url='https://changed.example/story' WHERE id=$1",
        "UPDATE news_items SET canonical_item_url='https://changed.example/item' WHERE id=$1",
        "UPDATE news_items SET article_url='https://changed.example/article' WHERE id=$1",
        "UPDATE news_items SET discussion_url='https://changed.example/discussion' WHERE id=$1",
        "UPDATE news_items SET platform='changed',source_external_id='changed' WHERE id=$1",
        "UPDATE news_items SET source_type='changed',source_label='changed' WHERE id=$1",
        "UPDATE news_items SET article_domain='changed.example' WHERE id=$1",
        "UPDATE news_items SET cluster_size=2 WHERE id=$1",
        "UPDATE news_items SET ingested_at=ingested_at-interval '15 days' WHERE id=$1",
        "UPDATE news_items SET raw_metadata='{\"summary\":{\"title\":\"Changed\"}}' WHERE id=$1",
        "UPDATE news_items SET raw_metadata='{\"article\":{\"title\":\"Changed\"}}' WHERE id=$1",
        "UPDATE news_items SET raw_metadata='{\"cluster\":{\"related_titles\":[\"Changed\"]}}' WHERE id=$1",
        "UPDATE news_items SET raw_metadata='{\"future_matching_input\":true}' WHERE id=$1",
        "DELETE FROM news_items WHERE id=$1",
    ] {
        let error = sqlx::query(sql).bind(id).execute(&pool).await.unwrap_err();
        assert!(
            is_contention(&error),
            "expected scope conflict for {sql}: {error}"
        );
    }
    let error = sqlx::query("INSERT INTO news_items(ingest_key,visibility_scope,status,ingested_at,created_at) VALUES('ready-insert','global','ready',now(),now())")
        .execute(&pool).await.unwrap_err();
    assert!(is_contention(&error));
    // These are precisely the source fingerprint's volatile paths, plus non-input clocks.
    sqlx::query("UPDATE news_items SET raw_metadata='{\"summary_checkpoint\":{},\"processing_error\":\"retry\",\"comment_count\":2,\"scraped_at\":\"later\",\"aggregator\":{\"metadata\":{\"score\":2,\"comments_count\":3,\"upvote_ratio\":0.5}}}',updated_at=now(),processed_at=now(),enrichment_updated_at=now() WHERE id=$1")
        .bind(id).execute(&pool).await.unwrap();
    sqlx::query("INSERT INTO news_items(ingest_key,visibility_scope,status,ingested_at,created_at) VALUES('private-preparation','global','new',now(),now())")
        .execute(&pool).await.unwrap();
    sqlx::query("UPDATE news_items SET status='processing',summary_text='Private preparation' WHERE ingest_key='private-preparation'")
        .execute(&pool).await.unwrap();
    guard.rollback().await.unwrap();
}

#[sqlx::test(migrations = false)]
async fn scope_move_locks_both_keys_and_rejects_cross_owner_group_membership(pool: PgPool) {
    crate::run_migrations(&pool).await.unwrap();
    let id = ready_item(&pool, "scope-move", "user", Some(10)).await;
    for held_owner in [10_i64, 20] {
        let mut guard = pool.begin().await.unwrap();
        assert!(
            try_lock_scope(&mut guard, "user", Some(held_owner))
                .await
                .unwrap()
        );
        let error = sqlx::query("UPDATE news_items SET owner_user_id=20 WHERE id=$1")
            .bind(id)
            .execute(&pool)
            .await
            .unwrap_err();
        assert!(is_contention(&error));
        guard.rollback().await.unwrap();
        let mut probe = pool.begin().await.unwrap();
        assert!(try_lock_scope(&mut probe, "user", Some(10)).await.unwrap());
        assert!(try_lock_scope(&mut probe, "user", Some(20)).await.unwrap());
        probe.rollback().await.unwrap();
    }
    let member = ready_item(&pool, "scope-member", "user", Some(10)).await;
    sqlx::query("UPDATE news_items SET representative_news_item_id=$2 WHERE id=$1")
        .bind(member)
        .bind(id)
        .execute(&pool)
        .await
        .unwrap();
    for grouped_id in [id, member] {
        let error = sqlx::query("UPDATE news_items SET owner_user_id=20 WHERE id=$1")
            .bind(grouped_id)
            .execute(&pool)
            .await
            .unwrap_err();
        assert_eq!(
            error.as_database_error().unwrap().code().as_deref(),
            Some("23514")
        );
        assert_eq!(
            error.as_database_error().unwrap().constraint(),
            Some("news_grouping_scope_membership")
        );
    }
}

#[sqlx::test(migrations = false)]
async fn two_integer_keys_do_not_alias_bigint_scheduler_keys_or_null_owners(pool: PgPool) {
    crate::run_migrations(&pool).await.unwrap();
    let mut first = pool.begin().await.unwrap();
    let mut second = pool.begin().await.unwrap();
    sqlx::query("SELECT pg_advisory_xact_lock(19018001::bigint)")
        .execute(&mut *first)
        .await
        .unwrap();
    assert!(try_lock_scope(&mut second, "global", None).await.unwrap());
    assert!(try_lock_scope(&mut first, "global", Some(0)).await.unwrap());
    assert!(try_lock_scope(&mut first, "user", None).await.unwrap());
    assert!(try_lock_scope(&mut first, "user", Some(0)).await.unwrap());
}

#[sqlx::test(migrations = false)]
async fn metadata_guard_projection_preserves_unknown_and_malformed_inputs(pool: PgPool) {
    crate::run_migrations(&pool).await.unwrap();
    let volatile = serde_json::json!({
        "summary_checkpoint":{}, "processing_error":"retry", "comment_count":2,
        "scraped_at":"now", "unknown_source_evidence":"retained",
        "aggregator":{"metadata":{"score":2,"comments_count":3,"upvote_ratio":0.5,"author":"retained"}}
    });
    let normalized: serde_json::Value =
        sqlx::query_scalar("SELECT newsly_news_grouping_metadata($1::json)")
            .bind(sqlx::types::Json(volatile))
            .fetch_one(&pool)
            .await
            .unwrap();
    assert_eq!(
        normalized,
        serde_json::json!({"unknown_source_evidence":"retained","aggregator":{"metadata":{"author":"retained"}}})
    );
    for value in [
        serde_json::json!(null),
        serde_json::json!([]),
        serde_json::json!({"aggregator":["unexpected"]}),
    ] {
        let normalized: serde_json::Value =
            sqlx::query_scalar("SELECT newsly_news_grouping_metadata($1::json)")
                .bind(sqlx::types::Json(value.clone()))
                .fetch_one(&pool)
                .await
                .unwrap();
        assert_eq!(normalized, value);
    }
}

#[sqlx::test(migrations = false)]
async fn shared_deferral_budget_tracks_count_and_age_independently_of_provider_retries(
    pool: PgPool,
) {
    crate::run_migrations(&pool).await.unwrap();
    let task_id: i64 = sqlx::query_scalar("INSERT INTO processing_tasks(task_type,status,queue_name,executor_runtime,executor_version,executor_namespace) VALUES('scrape','pending','content','rust',1,'scrape') RETURNING id::bigint")
        .fetch_one(&pool).await.unwrap();
    let mut transaction = pool.begin().await.unwrap();
    let initial = record_deferral(&mut transaction, task_id).await.unwrap();
    assert_eq!(initial.count, 1);
    assert!(!initial.exhausted);
    assert!((1..=3).contains(&initial.delay_seconds));
    sqlx::query("UPDATE news_grouping_deferrals SET first_deferred_at=clock_timestamp()-interval '1 hour' WHERE task_id=$1")
        .bind(task_id).execute(&mut *transaction).await.unwrap();
    let aged = record_deferral(&mut transaction, task_id).await.unwrap();
    assert!(aged.exhausted);
    assert!(aged.age_seconds >= f64::from(MAX_CONFLICT_AGE_SECONDS));
    sqlx::query("UPDATE news_grouping_deferrals SET first_deferred_at=clock_timestamp(),deferral_count=$2 WHERE task_id=$1")
        .bind(task_id).bind(MAX_DEFERRALS-1).execute(&mut *transaction).await.unwrap();
    let counted = record_deferral(&mut transaction, task_id).await.unwrap();
    assert_eq!(counted.count, MAX_DEFERRALS);
    assert!(counted.exhausted);
    let retries: i32 = sqlx::query_scalar("SELECT retry_count FROM processing_tasks WHERE id=$1")
        .bind(task_id)
        .fetch_one(&mut *transaction)
        .await
        .unwrap();
    assert_eq!(retries, 0);
}

#[sqlx::test(migrations = false)]
async fn provider_retry_or_lease_recovery_starts_a_new_conflict_episode(pool: PgPool) {
    crate::run_migrations(&pool).await.unwrap();
    let task:i64 = sqlx::query_scalar("INSERT INTO processing_tasks(task_type,status,queue_name,executor_runtime,executor_version,executor_namespace) VALUES('scrape','pending','content','rust',1,'scrape') RETURNING id::bigint").fetch_one(&pool).await.unwrap();
    let mut tx = pool.begin().await.unwrap();
    assert_eq!(record_deferral(&mut tx, task).await.unwrap().count, 1);
    sqlx::query("UPDATE news_grouping_deferrals SET first_deferred_at=clock_timestamp()-interval '2 hours',deferral_count=$2 WHERE task_id::bigint=$1").bind(task).bind(MAX_DEFERRALS).execute(&mut *tx).await.unwrap();
    assert!(record_deferral(&mut tx, task).await.unwrap().exhausted);
    sqlx::query("UPDATE processing_tasks SET retry_count=retry_count+1 WHERE id::bigint=$1")
        .bind(task)
        .execute(&mut *tx)
        .await
        .unwrap();
    let next = record_deferral(&mut tx, task).await.unwrap();
    assert_eq!(next.count, 1);
    assert!(!next.exhausted);
    assert!(next.age_seconds < 1.0);
    assert_eq!(record_deferral(&mut tx, task).await.unwrap().count, 2);
    clear_deferral(&mut tx, task).await.unwrap();
    assert_eq!(record_deferral(&mut tx, task).await.unwrap().count, 1);
}
