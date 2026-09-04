use chrono::Utc;
use serde_json::{Value, json};
use sqlx::PgPool;

use super::{FeedBackfillEntry, FeedBackfillOrigin, persist_feed_backfill};

async fn insert_user(pool: &PgPool) -> i64 {
    sqlx::query_scalar::<_, i64>(
        r#"
        INSERT INTO users (apple_id, email, is_admin, is_active)
        VALUES ('feed-backfill-test', 'feed-backfill@example.com', false, true)
        RETURNING id::bigint
        "#,
    )
    .fetch_one(pool)
    .await
    .expect("test user should insert")
}

fn podcast_entry() -> FeedBackfillEntry {
    FeedBackfillEntry {
        url: "https://example.com/episodes/1".to_owned(),
        source_url: "https://example.com/episodes/1".to_owned(),
        title: Some("Episode one".to_owned()),
        source: Some("Configured show name".to_owned()),
        platform: "podcast".to_owned(),
        metadata: json!({
            "feed_config_id": 42,
            "feed_url": "https://example.com/feed.xml",
            "audio_url": "https://cdn.example.com/episodes/1.mp3",
            "duration_seconds": 1234,
        }),
        published_at: Some(Utc::now()),
        content_type: "podcast".to_owned(),
    }
}

#[sqlx::test]
async fn feed_backfill_preserves_normalized_metadata_and_existing_membership(pool: PgPool) {
    let user_id = insert_user(&pool).await;
    let entry = podcast_entry();
    let mut transaction = pool.begin().await.expect("transaction should begin");
    let inserted = persist_feed_backfill(
        &mut transaction,
        user_id,
        FeedBackfillOrigin::DownloadMore,
        std::slice::from_ref(&entry),
    )
    .await
    .expect("backfill should persist");
    transaction
        .commit()
        .await
        .expect("transaction should commit");

    assert_eq!(inserted.saved, 1);
    let content_id = inserted.content_ids[0];
    let (source, platform, metadata) =
        sqlx::query_as::<_, (Option<String>, Option<String>, Value)>(
            "SELECT source, platform, content_metadata::jsonb FROM contents WHERE id::bigint = $1",
        )
        .bind(content_id)
        .fetch_one(&pool)
        .await
        .expect("content should exist");
    assert_eq!(source.as_deref(), Some("Configured show name"));
    assert_eq!(platform.as_deref(), Some("podcast"));
    assert_eq!(
        metadata.get("audio_url").and_then(Value::as_str),
        Some("https://cdn.example.com/episodes/1.mp3")
    );
    assert_eq!(
        metadata.get("submitted_via").and_then(Value::as_str),
        Some("download_more")
    );

    sqlx::query(
        "UPDATE content_status SET status = 'archived' WHERE user_id::bigint = $1 AND content_id::bigint = $2",
    )
    .bind(user_id)
    .bind(content_id)
    .execute(&pool)
    .await
    .expect("membership should update");

    let mut transaction = pool.begin().await.expect("transaction should begin");
    let duplicate = persist_feed_backfill(
        &mut transaction,
        user_id,
        FeedBackfillOrigin::Background,
        &[entry],
    )
    .await
    .expect("duplicate backfill should succeed");
    transaction
        .commit()
        .await
        .expect("transaction should commit");
    assert_eq!(duplicate.duplicates, 1);
    let status = sqlx::query_scalar::<_, String>(
        "SELECT status FROM content_status WHERE user_id::bigint = $1 AND content_id::bigint = $2",
    )
    .bind(user_id)
    .bind(content_id)
    .fetch_one(&pool)
    .await
    .expect("membership should exist");
    assert_eq!(status, "archived");
}
