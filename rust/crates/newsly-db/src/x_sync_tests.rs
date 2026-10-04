use chrono::Utc;
use serde_json::{Value, json};
use sqlx::PgPool;

use super::{
    PrepareXSyncOutcome, XSyncConnectionUpdate, persist_x_sync_connection_update, prepare_x_sync,
};

#[sqlx::test]
async fn x_connection_update_persists_json_scopes_and_preserves_them_when_empty(pool: PgPool) {
    let user_id: i64 = sqlx::query_scalar(
        "INSERT INTO users (apple_id, email, is_admin, is_active) VALUES ('x-scopes', 'x-scopes@example.test', FALSE, TRUE) RETURNING id::bigint",
    )
    .fetch_one(&pool)
    .await
    .unwrap();
    let connection_id: i64 = sqlx::query_scalar(
        "INSERT INTO user_integration_connections (user_id, provider, access_token_encrypted, scopes, created_at) VALUES ($1::integer, 'x', 'old-token', '[\"users.read\"]'::json, timezone('UTC', now())) RETURNING id::bigint",
    )
    .bind(user_id)
    .fetch_one(&pool)
    .await
    .unwrap();

    let mut transaction = pool.begin().await.unwrap();
    let PrepareXSyncOutcome::Prepared(plan) =
        prepare_x_sync(&mut transaction, user_id, false, Utc::now(), 60, 60)
            .await
            .unwrap()
    else {
        panic!("active X connection should be prepared")
    };
    assert_eq!(plan.connection_id, connection_id);

    let scopes = vec!["bookmark.read".to_owned(), "users.read".to_owned()];
    persist_x_sync_connection_update(
        &mut transaction,
        &plan,
        &XSyncConnectionUpdate {
            provider_user_id: Some("x-user"),
            provider_username: Some("reader"),
            access_token_encrypted: "new-token",
            refresh_token_encrypted: Some("new-refresh"),
            token_expires_at: None,
            scopes: &scopes,
        },
    )
    .await
    .unwrap();
    transaction.commit().await.unwrap();

    let (access_token, refresh_token, stored_scopes): (String, Option<String>, Value) =
        sqlx::query_as(
            "SELECT access_token_encrypted, refresh_token_encrypted, scopes::jsonb FROM user_integration_connections WHERE id::bigint = $1",
        )
        .bind(connection_id)
        .fetch_one(&pool)
        .await
        .unwrap();
    assert_eq!(access_token, "new-token");
    assert_eq!(refresh_token.as_deref(), Some("new-refresh"));
    assert_eq!(stored_scopes, json!(["bookmark.read", "users.read"]));

    let mut transaction = pool.begin().await.unwrap();
    persist_x_sync_connection_update(
        &mut transaction,
        &plan,
        &XSyncConnectionUpdate {
            provider_user_id: None,
            provider_username: None,
            access_token_encrypted: "newer-token",
            refresh_token_encrypted: Some("newer-refresh"),
            token_expires_at: None,
            scopes: &[],
        },
    )
    .await
    .unwrap();
    transaction.commit().await.unwrap();

    let stored_scopes: Value = sqlx::query_scalar(
        "SELECT scopes::jsonb FROM user_integration_connections WHERE id::bigint = $1",
    )
    .bind(connection_id)
    .fetch_one(&pool)
    .await
    .unwrap();
    assert_eq!(stored_scopes, json!(["bookmark.read", "users.read"]));
}

#[sqlx::test]
async fn canonical_x_snapshot_merge_locks_and_preserves_concurrent_content_fields(pool: PgPool) {
    let id: i64 = sqlx::query_scalar("INSERT INTO contents (url,content_type,status,is_aggregate,content_metadata) VALUES ('https://x.com/test/status/1','article','completed',FALSE,$1) RETURNING id::bigint")
        .bind(json!({"tweet_snapshot":{"text":"old"},"domain":{"tweet_snapshot":{"text":"old"},"keep":1},"processing":{"tweet_snapshot":"misplaced"}}))
        .fetch_one(&pool).await.unwrap();
    let mut content_tx = pool.begin().await.unwrap();
    let stored: Value =
        sqlx::query_scalar("SELECT content_metadata FROM contents WHERE id::bigint=$1 FOR UPDATE")
            .bind(id)
            .fetch_one(&mut *content_tx)
            .await
            .unwrap();
    let mut metadata = stored.as_object().unwrap().clone();
    crate::content_metadata::set_field(&mut metadata, "summary", json!({"title":"concurrent"}));
    sqlx::query("UPDATE contents SET content_metadata=$2 WHERE id::bigint=$1")
        .bind(id)
        .bind(Value::Object(metadata))
        .execute(&mut *content_tx)
        .await
        .unwrap();
    let updates = json!({"tweet_snapshot":{"text":"new"},"tweet_snapshot_included":[{"id":"2"}]});
    let mut x_tx = pool.begin().await.unwrap();
    {
        let merge = super::persist_x_bookmark_snapshot(&mut x_tx, id, &updates);
        tokio::pin!(merge);
        assert!(
            tokio::time::timeout(std::time::Duration::from_millis(50), &mut merge)
                .await
                .is_err()
        );
        content_tx.commit().await.unwrap();
        merge.await.unwrap();
    }
    x_tx.commit().await.unwrap();
    let stored: Value =
        sqlx::query_scalar("SELECT content_metadata FROM contents WHERE id::bigint=$1")
            .bind(id)
            .fetch_one(&pool)
            .await
            .unwrap();
    assert_eq!(stored["tweet_snapshot"], updates["tweet_snapshot"]);
    assert_eq!(
        stored["domain"]["tweet_snapshot"],
        updates["tweet_snapshot"]
    );
    assert_eq!(
        stored["domain"]["tweet_snapshot_included"],
        updates["tweet_snapshot_included"]
    );
    assert!(stored["processing"].get("tweet_snapshot").is_none());
    assert_eq!(stored["summary"]["title"], "concurrent");
    assert_eq!(stored["domain"]["keep"], 1);
}
