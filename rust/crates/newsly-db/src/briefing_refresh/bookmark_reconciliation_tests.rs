use serde_json::{Value, json};
use sqlx::PgPool;
use uuid::Uuid;

use crate::incident_batches::bookmarks::{BookmarkReconciliationScope, reconcile_legacy_bookmarks};

struct Fixture {
    owner: i64,
    target: i64,
    ordinary: i64,
    segment: i64,
}

async fn fixture(pool: &PgPool) -> Fixture {
    crate::run_migrations(pool).await.unwrap();
    let owner: i64 = sqlx::query_scalar("INSERT INTO users(apple_id,email,is_active,is_admin) VALUES('legacy-bookmarks','legacy-bookmarks@example.test',true,false) RETURNING id::bigint")
        .fetch_one(pool).await.unwrap();
    sqlx::query("INSERT INTO briefing_states(user_id,version,masthead_title,masthead_deck) VALUES($1::bigint::integer,5,'Test','Test')")
        .bind(owner).execute(pool).await.unwrap();
    let lens: i64 = sqlx::query_scalar("INSERT INTO briefing_lenses(user_id,key,tier,title,deck,position,status,centroid_weight) VALUES($1::bigint::integer,'articles','longform','Articles','Test',1,'active',0) RETURNING id::bigint")
        .bind(owner).fetch_one(pool).await.unwrap();
    let mut ids = Vec::new();
    for (url, channel) in [
        ("https://x.com/i/status/12345", "x_bookmarks"),
        ("https://example.test/ordinary", "share_action"),
    ] {
        let metadata = json!({"submitted_via":channel,"submitted_by_user_id":owner,"summary":{"overview":"Stored summary"},"image_generated_at":"2026-03-27T12:00:00Z","image_url":"/static/test.png"});
        let id: i64 = sqlx::query_scalar("INSERT INTO contents(content_type,url,source,platform,status,classification,content_metadata,is_aggregate,created_at,updated_at) VALUES('article',$1,'self submission','twitter','completed','skip',$2,false,'2026-03-27','2026-03-27') RETURNING id::bigint")
            .bind(url).bind(metadata).fetch_one(pool).await.unwrap();
        sqlx::query("INSERT INTO content_status(user_id,content_id,status,created_at,updated_at) VALUES($1::bigint::integer,$2::bigint::integer,'inbox','2026-03-27','2026-03-27')")
            .bind(owner).bind(id).execute(pool).await.unwrap();
        ids.push(id);
    }
    let segment: i64 = sqlx::query_scalar("INSERT INTO briefing_segments(lens_id,user_id,blocks,markdown_raw,narration_text,source_keys,status,model,prompt_version,warnings) VALUES($1::bigint::integer,$2::bigint::integer,'[]','Stored article','Stored narration',$3,'active','test','test','[]') RETURNING id::bigint")
        .bind(lens).bind(owner).bind(json!([format!("content:{}",ids[0])])).fetch_one(pool).await.unwrap();
    sqlx::query("INSERT INTO briefing_pending_sources(user_id,lens_key,source_kind,source_id) VALUES($1::bigint::integer,'articles','content',$2::bigint::integer)")
        .bind(owner).bind(ids[0]).execute(pool).await.unwrap();
    Fixture {
        owner,
        target: ids[0],
        ordinary: ids[1],
        segment,
    }
}

fn scope(f: &Fixture) -> BookmarkReconciliationScope {
    BookmarkReconciliationScope {
        user_id: f.owner,
        content_ids: vec![f.target],
        imported_before: "2026-05-10T00:00:00".parse().unwrap(),
        expected_version: 5,
    }
}

async fn state(pool: &PgPool, f: &Fixture) -> Value {
    sqlx::query_scalar("SELECT jsonb_build_object('content',to_jsonb(c),'membership',to_jsonb(s),'save',to_jsonb(k),'read',to_jsonb(r),'segment',to_jsonb(b),'version',(SELECT version FROM briefing_states WHERE user_id=$1),'pending',(SELECT count(*) FROM briefing_pending_sources WHERE user_id=$1 AND source_kind='content' AND source_id=$2),'batches',(SELECT count(*) FROM operator_incident_batches)) FROM contents c JOIN content_status s ON s.content_id=c.id AND s.user_id=$1 LEFT JOIN content_knowledge_saves k ON k.content_id=c.id AND k.user_id=$1 LEFT JOIN content_read_status r ON r.content_id=c.id AND r.user_id=$1 JOIN briefing_segments b ON b.id=$3 WHERE c.id=$2")
        .bind(f.owner as i32).bind(f.target as i32).bind(f.segment as i32).fetch_one(pool).await.unwrap()
}

#[sqlx::test(migrations = false)]
async fn bookmark_reconciliation_is_atomic_durable_and_idempotent(pool: PgPool) {
    let f = fixture(&pool).await;
    let before = state(&pool, &f).await;
    let batch = Uuid::new_v4();
    let preview = reconcile_legacy_bookmarks(
        &pool,
        &scope(&f),
        batch,
        "test",
        "approved exact cohort",
        false,
    )
    .await
    .unwrap();
    assert!(!preview.applied);
    assert_eq!(preview.bookmarks.len(), 1);
    assert_eq!(preview.segments.len(), 1);
    assert_eq!(preview.pending.len(), 1);
    assert_eq!(
        state(&pool, &f).await,
        before,
        "dry-run rolls back every state"
    );
    let result = reconcile_legacy_bookmarks(
        &pool,
        &scope(&f),
        batch,
        "test",
        "approved exact cohort",
        true,
    )
    .await
    .unwrap();
    assert!(result.applied);
    assert!(!result.replayed);
    let after = state(&pool, &f).await;
    assert_eq!(after["content"], before["content"]);
    assert_eq!(after["read"], before["read"]);
    assert_eq!(after["membership"]["status"], "archived");
    assert_eq!(after["save"]["saved_at"], "2026-03-27T00:00:00+00:00");
    assert_eq!(after["segment"]["status"], "retired");
    assert_eq!(after["version"], 6);
    assert_eq!(after["pending"], 0);
    assert_eq!(after["batches"], 1);
    assert!(
        crate::find_visible_content_detail(&pool, f.owner, f.target)
            .await
            .unwrap()
            .is_some(),
        "Knowledge preserves source access"
    );
    let audit: Value =
        sqlx::query_scalar("SELECT targets FROM operator_incident_batches WHERE batch_id=$1")
            .bind(batch)
            .fetch_one(&pool)
            .await
            .unwrap();
    assert_eq!(audit["before"]["bookmarks"][0]["saved_at"], Value::Null);
    assert_eq!(audit["before"]["segments"][0]["status"], "active");
    let applied_timestamps_match: bool = sqlx::query_scalar("SELECT s.updated_at=b.applied_at AT TIME ZONE 'UTC' AND segment.updated_at=b.applied_at AT TIME ZONE 'UTC' AND k.created_at=b.applied_at FROM operator_incident_batches b JOIN content_status s ON s.user_id=$2 AND s.content_id=$3 JOIN content_knowledge_saves k ON k.user_id=s.user_id AND k.content_id=s.content_id JOIN briefing_segments segment ON segment.id=$4 WHERE b.batch_id=$1")
        .bind(batch).bind(f.owner as i32).bind(f.target as i32).bind(f.segment as i32).fetch_one(&pool).await.unwrap();
    assert!(
        applied_timestamps_match,
        "audit identifies the exact after-state for a guarded rollback"
    );
    let repeated = reconcile_legacy_bookmarks(
        &pool,
        &scope(&f),
        batch,
        "test",
        "approved exact cohort",
        true,
    )
    .await
    .unwrap();
    assert!(repeated.replayed);
    assert_eq!(state(&pool, &f).await, after);
    let mut wrong_scope = scope(&f);
    wrong_scope.content_ids = vec![f.ordinary];
    assert!(
        reconcile_legacy_bookmarks(
            &pool,
            &wrong_scope,
            batch,
            "test",
            "approved exact cohort",
            true
        )
        .await
        .is_err()
    );
    for full in [false, true] {
        let mut tx = pool.begin().await.unwrap();
        super::preparation::seed_content_pending(&mut tx, f.owner, full)
            .await
            .unwrap();
        let ids:Vec<i64>=sqlx::query_scalar("SELECT source_id::bigint FROM briefing_pending_sources WHERE user_id::bigint=$1 AND source_kind='content' ORDER BY source_id")
            .bind(f.owner).fetch_all(&mut *tx).await.unwrap();
        assert_eq!(
            ids,
            vec![f.ordinary],
            "repair survives incremental/full; ordinary legacy skips remain eligible"
        );
        let sources = super::load_eligible_sources_for_keys(
            &mut tx,
            f.owner,
            &[format!("content:{}", f.target)],
        )
        .await
        .unwrap();
        assert!(
            sources.is_empty(),
            "stale composition revalidation rejects archived source"
        );
        tx.rollback().await.unwrap();
    }
}

#[sqlx::test(migrations = false)]
async fn bookmark_reconciliation_preserves_existing_save_and_read(pool: PgPool) {
    let f = fixture(&pool).await;
    sqlx::query("INSERT INTO content_knowledge_saves(user_id,content_id,saved_at) VALUES($1::bigint::integer,$2::bigint::integer,'2026-04-01')")
        .bind(f.owner).bind(f.target).execute(&pool).await.unwrap();
    let mut tx = pool.begin().await.unwrap();
    crate::mark_content_read(&mut tx, f.owner, f.target)
        .await
        .unwrap();
    tx.commit().await.unwrap();
    let before = state(&pool, &f).await;
    reconcile_legacy_bookmarks(&pool, &scope(&f), Uuid::new_v4(), "test", "approved", true)
        .await
        .unwrap();
    let after = state(&pool, &f).await;
    assert_eq!(after["save"], before["save"]);
    assert_eq!(after["read"], before["read"]);
}

#[sqlx::test(migrations = false)]
async fn bookmark_reconciliation_rejects_stale_version_and_changed_provenance(pool: PgPool) {
    let f = fixture(&pool).await;
    let before = state(&pool, &f).await;
    let mut stale = scope(&f);
    stale.expected_version = 4;
    assert!(
        reconcile_legacy_bookmarks(&pool, &stale, Uuid::new_v4(), "test", "approved", true)
            .await
            .is_err()
    );
    let mut wrong = scope(&f);
    wrong.content_ids.push(f.ordinary);
    assert!(
        reconcile_legacy_bookmarks(&pool, &wrong, Uuid::new_v4(), "test", "approved", true)
            .await
            .is_err()
    );
    assert_eq!(state(&pool, &f).await, before);
    sqlx::query("UPDATE content_status SET updated_at=now() WHERE user_id::bigint=$1 AND content_id::bigint=$2")
        .bind(f.owner).bind(f.target).execute(&pool).await.unwrap();
    assert!(
        reconcile_legacy_bookmarks(&pool, &scope(&f), Uuid::new_v4(), "test", "approved", true)
            .await
            .is_err(),
        "later intentional re-add is protected"
    );
}

#[sqlx::test(migrations = false)]
async fn bookmark_reconciliation_rejects_mixed_coverage(pool: PgPool) {
    let f = fixture(&pool).await;
    sqlx::query("UPDATE briefing_segments SET source_keys=$2 WHERE id::bigint=$1")
        .bind(f.segment)
        .bind(json!([
            format!("content:{}", f.target),
            format!("content:{}", f.ordinary)
        ]))
        .execute(&pool)
        .await
        .unwrap();
    let before = state(&pool, &f).await;
    assert!(
        reconcile_legacy_bookmarks(&pool, &scope(&f), Uuid::new_v4(), "test", "approved", true)
            .await
            .is_err()
    );
    assert_eq!(state(&pool, &f).await, before);
}

#[sqlx::test(migrations = false)]
async fn bookmark_reconciliation_rejects_existing_audio(pool: PgPool) {
    let f = fixture(&pool).await;
    sqlx::query("INSERT INTO audio_episodes(user_id,kind,status,title,input_hash,source_snapshot,source_item_ids,share_enabled,prompt_version,audio_content_type) VALUES($1::bigint::integer,'briefing_narration','pending','Existing','bookmark-audio',$2,'[]',false,1,'audio/mpeg')")
        .bind(f.owner).bind(json!({"source_keys":[format!("content:{}",f.target)]})).execute(&pool).await.unwrap();
    let before = state(&pool, &f).await;
    assert!(
        reconcile_legacy_bookmarks(&pool, &scope(&f), Uuid::new_v4(), "test", "approved", true)
            .await
            .is_err()
    );
    assert_eq!(state(&pool, &f).await, before);
}

#[sqlx::test(migrations = false)]
async fn bookmark_reconciliation_fences_publication_by_user_lock(pool: PgPool) {
    let f = fixture(&pool).await;
    let mut publication = pool.begin().await.unwrap();
    sqlx::query("SELECT id FROM users WHERE id::bigint=$1 FOR SHARE")
        .bind(f.owner)
        .execute(&mut *publication)
        .await
        .unwrap();
    let repair = tokio::spawn({
        let pool = pool.clone();
        let scope = scope(&f);
        async move {
            reconcile_legacy_bookmarks(&pool, &scope, Uuid::new_v4(), "test", "approved", true)
                .await
        }
    });
    tokio::time::sleep(std::time::Duration::from_millis(50)).await;
    assert!(
        !repair.is_finished(),
        "repair waits for an existing publisher"
    );
    sqlx::query("UPDATE briefing_states SET version=6 WHERE user_id::bigint=$1")
        .bind(f.owner)
        .execute(&mut *publication)
        .await
        .unwrap();
    publication.commit().await.unwrap();
    assert!(
        repair.await.unwrap().is_err(),
        "post-publication version mismatch aborts repair"
    );
}

#[sqlx::test(migrations = false)]
async fn bookmark_reconciliation_allows_concurrent_read_foreign_keys(pool: PgPool) {
    let f = fixture(&pool).await;
    let mut reader = pool.begin().await.unwrap();
    sqlx::query("SET LOCAL lock_timeout='500ms'")
        .execute(&mut *reader)
        .await
        .unwrap();
    sqlx::query("SELECT version FROM briefing_states WHERE user_id::bigint=$1 FOR UPDATE")
        .bind(f.owner)
        .execute(&mut *reader)
        .await
        .unwrap();
    let repair = tokio::spawn({
        let pool = pool.clone();
        let scope = scope(&f);
        async move {
            reconcile_legacy_bookmarks(&pool, &scope, Uuid::new_v4(), "test", "approved", true)
                .await
        }
    });
    tokio::time::sleep(std::time::Duration::from_millis(50)).await;
    crate::mark_content_read(&mut reader, f.owner, f.target)
        .await
        .unwrap();
    sqlx::query("UPDATE briefing_states SET version=6 WHERE user_id::bigint=$1")
        .bind(f.owner)
        .execute(&mut *reader)
        .await
        .unwrap();
    reader.commit().await.unwrap();
    assert!(
        repair.await.unwrap().is_err(),
        "read commits; repair rejects the changed version"
    );
}

#[sqlx::test(migrations = false)]
async fn bookmark_reconciliation_aborts_when_concurrent_save_wins(pool: PgPool) {
    let f = fixture(&pool).await;
    let mut saver = pool.begin().await.unwrap();
    crate::save_content_to_knowledge(&mut saver, f.owner, f.target)
        .await
        .unwrap();
    let repair = tokio::spawn({
        let pool = pool.clone();
        let scope = scope(&f);
        async move {
            reconcile_legacy_bookmarks(&pool, &scope, Uuid::new_v4(), "test", "approved", true)
                .await
        }
    });
    let mut waiting = false;
    for _ in 0..100 {
        waiting=sqlx::query_scalar("SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE datname=current_database() AND wait_event_type='Lock' AND strpos(query,'INSERT INTO content_knowledge_saves(user_id,content_id,saved_at,created_at)')>0)")
            .fetch_one(&pool).await.unwrap();
        if waiting {
            break;
        }
        tokio::time::sleep(std::time::Duration::from_millis(10)).await;
    }
    saver.commit().await.unwrap();
    assert!(
        waiting,
        "repair reached the concurrent save conflict without a foreign-key deadlock"
    );
    assert!(
        repair.await.unwrap().is_err(),
        "repair refuses an inaccurate before-state"
    );
    let after = state(&pool, &f).await;
    assert_eq!(after["membership"]["status"], "inbox");
    assert_eq!(after["segment"]["status"], "active");
    assert_eq!(after["batches"], 0);
    assert_eq!(after["version"], 5);
    assert!(
        !after["save"].is_null(),
        "the user's winning save is preserved"
    );
}

#[sqlx::test(migrations = false)]
async fn explicit_readd_restores_unread_item_for_next_queued_refresh(pool: PgPool) {
    let f = fixture(&pool).await;
    reconcile_legacy_bookmarks(&pool, &scope(&f), Uuid::new_v4(), "test", "approved", true)
        .await
        .unwrap();
    let repaired = state(&pool, &f).await;
    let mut tx = pool.begin().await.unwrap();
    let input = crate::ContentSubmissionInput {
        url: "https://x.com/i/status/12345",
        title: None,
        platform: None,
        instruction: None,
        crawl_links: false,
        subscribe_to_feed: false,
        share_and_chat: false,
        chat_initial_message: None,
        save_to_knowledge_and_mark_read: false,
        deck_source_only: false,
        user_id: f.owner,
        submitted_via: "share_action",
    };
    let applied = crate::apply_content_submission(&mut tx, &input)
        .await
        .unwrap();
    assert_eq!(applied.content_id, f.target);
    super::preparation::seed_content_pending(&mut tx, f.owner, false)
        .await
        .unwrap();
    tx.commit().await.unwrap();
    let after = state(&pool, &f).await;
    assert_eq!(after["membership"]["status"], "inbox");
    assert_eq!(after["pending"], 1);
    assert!(after["read"].is_null());
    assert!(!after["save"].is_null());
    assert_eq!(
        after["save"], repaired["save"],
        "re-add preserves the historical Knowledge save"
    );
}
