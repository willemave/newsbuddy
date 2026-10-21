use serde_json::Value;
use sqlx::PgPool;

use super::{ContentSubmissionInput, SubmissionTaskResolution, apply_content_submission};

fn inbox_input(user_id: i64) -> ContentSubmissionInput<'static> {
    ContentSubmissionInput {
        url: "https://example.test/explicit-readd",
        title: None,
        platform: None,
        instruction: None,
        crawl_links: false,
        subscribe_to_feed: false,
        share_and_chat: false,
        chat_initial_message: None,
        save_to_knowledge_and_mark_read: false,
        deck_source_only: false,
        user_id,
        submitted_via: "share_action",
    }
}

async fn fixture(pool: &PgPool, membership: &str) -> (i64, i64) {
    crate::run_migrations(pool).await.unwrap();
    let user_id = sqlx::query_scalar::<_, i64>(
        "INSERT INTO users (apple_id, email, is_admin, is_active) VALUES ('explicit-readd', 'explicit-readd@example.test', FALSE, TRUE) RETURNING id::bigint",
    ).fetch_one(pool).await.unwrap();
    let mut transaction = pool.begin().await.unwrap();
    let first = apply_content_submission(&mut transaction, &inbox_input(user_id))
        .await
        .unwrap();
    transaction.commit().await.unwrap();
    sqlx::query(
        "UPDATE contents SET content_type = 'article', status = 'completed' WHERE id::bigint = $1",
    )
    .bind(first.content_id)
    .execute(pool)
    .await
    .unwrap();
    sqlx::query("UPDATE content_status SET status = $2, updated_at = '2020-01-01' WHERE content_id::bigint = $1")
        .bind(first.content_id).bind(membership).execute(pool).await.unwrap();
    (user_id, first.content_id)
}

async fn state(pool: &PgPool, user_id: i64, content_id: i64) -> Value {
    sqlx::query_scalar(
        "SELECT jsonb_build_object('membership', to_jsonb(s), 'read', to_jsonb(r), 'save', to_jsonb(k)) FROM content_status s LEFT JOIN content_read_status r USING (user_id, content_id) LEFT JOIN content_knowledge_saves k USING (user_id, content_id) WHERE s.user_id::bigint = $1 AND s.content_id::bigint = $2",
    ).bind(user_id).bind(content_id).fetch_one(pool).await.unwrap()
}

#[sqlx::test(migrations = false)]
async fn explicit_briefing_readd_revives_archive_preserving_read_and_knowledge(pool: PgPool) {
    let (user_id, content_id) = fixture(&pool, "archived").await;
    let mut transaction = pool.begin().await.unwrap();
    crate::mark_content_read(&mut transaction, user_id, content_id)
        .await
        .unwrap();
    crate::save_content_to_knowledge(&mut transaction, user_id, content_id)
        .await
        .unwrap();
    transaction.commit().await.unwrap();
    let before = state(&pool, user_id, content_id).await;
    let mut transaction = pool.begin().await.unwrap();
    let applied = apply_content_submission(&mut transaction, &inbox_input(user_id))
        .await
        .unwrap();
    transaction.commit().await.unwrap();
    assert_eq!(applied.content_id, content_id);
    assert!(applied.already_exists);
    assert_eq!(applied.task_resolution, SubmissionTaskResolution::None);
    let after = state(&pool, user_id, content_id).await;
    assert_eq!(after["membership"]["status"], "inbox");
    assert_eq!(
        after["membership"]["created_at"],
        before["membership"]["created_at"]
    );
    assert_ne!(
        after["membership"]["updated_at"],
        before["membership"]["updated_at"]
    );
    assert_eq!(after["read"], before["read"]);
    assert_eq!(after["save"], before["save"]);
}

#[sqlx::test(migrations = false)]
async fn explicit_briefing_readd_is_idempotent_for_existing_inbox(pool: PgPool) {
    let (user_id, content_id) = fixture(&pool, "inbox").await;
    let before = state(&pool, user_id, content_id).await;
    for channel in ["share_action", "ios_app", "cli", ""] {
        let mut input = inbox_input(user_id);
        input.submitted_via = channel;
        let mut transaction = pool.begin().await.unwrap();
        apply_content_submission(&mut transaction, &input)
            .await
            .unwrap();
        transaction.commit().await.unwrap();
        assert_eq!(state(&pool, user_id, content_id).await, before);
    }
}

#[sqlx::test(migrations = false)]
async fn non_briefing_outcomes_preserve_archived_membership(pool: PgPool) {
    let (user_id, content_id) = fixture(&pool, "archived").await;
    let before = state(&pool, user_id, content_id).await;
    for outcome in ["knowledge", "chat", "deck", "subscription", "x_bookmarks"] {
        let mut input = inbox_input(user_id);
        match outcome {
            "knowledge" => input.save_to_knowledge_and_mark_read = true,
            "chat" => input.share_and_chat = true,
            "deck" => input.deck_source_only = true,
            "subscription" => input.subscribe_to_feed = true,
            "x_bookmarks" => input.submitted_via = " x_bookmarks ",
            _ => unreachable!(),
        }
        let mut transaction = pool.begin().await.unwrap();
        apply_content_submission(&mut transaction, &input)
            .await
            .unwrap();
        transaction.commit().await.unwrap();
        let after = state(&pool, user_id, content_id).await;
        assert_eq!(after["membership"], before["membership"], "{outcome}");
        if outcome == "knowledge" {
            assert!(!after["read"].is_null());
            assert!(!after["save"].is_null());
        }
    }
}

#[sqlx::test(migrations = false)]
async fn x_bookmark_submission_preserves_read_and_saved_state_without_inbox(pool: PgPool) {
    let (user_id, content_id) = fixture(&pool, "archived").await;
    let mut transaction = pool.begin().await.unwrap();
    crate::save_content_to_knowledge(&mut transaction, user_id, content_id)
        .await
        .unwrap();
    transaction.commit().await.unwrap();
    let before = state(&pool, user_id, content_id).await;
    let mut input = inbox_input(user_id);
    input.submitted_via = "x_bookmarks";
    let mut transaction = pool.begin().await.unwrap();
    apply_content_submission(&mut transaction, &input)
        .await
        .unwrap();
    transaction.commit().await.unwrap();
    assert_eq!(state(&pool, user_id, content_id).await, before);
    sqlx::query(
        "DELETE FROM content_status WHERE user_id::bigint = $1 AND content_id::bigint = $2",
    )
    .bind(user_id)
    .bind(content_id)
    .execute(&pool)
    .await
    .unwrap();
    let mut transaction = pool.begin().await.unwrap();
    apply_content_submission(&mut transaction, &input)
        .await
        .unwrap();
    transaction.commit().await.unwrap();
    let count = sqlx::query_scalar::<_, i64>("SELECT count(*) FROM content_status WHERE user_id::bigint = $1 AND content_id::bigint = $2")
        .bind(user_id).bind(content_id).fetch_one(&pool).await.unwrap();
    assert_eq!(count, 0);
}

#[sqlx::test(migrations = false)]
async fn explicit_briefing_readd_keeps_unread_unsaved_state_and_other_users_archive(pool: PgPool) {
    let (user_id, content_id) = fixture(&pool, "archived").await;
    let other_user_id = sqlx::query_scalar::<_, i64>(
        "INSERT INTO users (apple_id, email, is_admin, is_active) VALUES ('other-readd', 'other-readd@example.test', FALSE, TRUE) RETURNING id::bigint",
    ).fetch_one(&pool).await.unwrap();
    sqlx::query("INSERT INTO content_status (user_id, content_id, status) VALUES ($1::bigint::integer, $2::bigint::integer, 'archived')")
        .bind(other_user_id).bind(content_id).execute(&pool).await.unwrap();
    let other_before = state(&pool, other_user_id, content_id).await;
    let mut transaction = pool.begin().await.unwrap();
    apply_content_submission(&mut transaction, &inbox_input(user_id))
        .await
        .unwrap();
    transaction.commit().await.unwrap();
    let after = state(&pool, user_id, content_id).await;
    assert_eq!(after["membership"]["status"], "inbox");
    assert!(after["read"].is_null());
    assert!(after["save"].is_null());
    assert_eq!(state(&pool, other_user_id, content_id).await, other_before);
}
