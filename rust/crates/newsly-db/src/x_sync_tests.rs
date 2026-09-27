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
