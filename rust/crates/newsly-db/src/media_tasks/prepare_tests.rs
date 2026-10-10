use serde_json::json;
use sqlx::PgPool;
use uuid::Uuid;

use super::prepare_media_content;

#[sqlx::test]
async fn prepare_media_content_returns_owned_podcast_snapshot(pool: PgPool) {
    let source_url = "https://example.com/podcast/feed.xml";
    for metadata in [
        json!({"audio_url": "https://example.com/episode.mp3"}),
        json!({}),
    ] {
        let url = format!("https://example.com/podcast/{}", Uuid::new_v4());
        let content_id = sqlx::query_scalar::<_, i32>(
            r#"
            INSERT INTO contents (
                content_type, url, source_url, platform, status, is_aggregate, content_metadata
            ) VALUES ('podcast', $1, $2, 'podcast', 'processing', FALSE, $3::json)
            RETURNING id
            "#,
        )
        .bind(&url)
        .bind(source_url)
        .bind(&metadata)
        .fetch_one(&pool)
        .await
        .expect("insert podcast fixture");

        let mut transaction = pool.begin().await.expect("begin media preparation");
        let snapshot = prepare_media_content(&mut transaction, i64::from(content_id))
            .await
            .expect("prepare podcast snapshot")
            .expect("podcast exists");
        transaction
            .commit()
            .await
            .expect("release preparation transaction");

        assert_eq!(snapshot.id, i64::from(content_id));
        assert_eq!(snapshot.content_type, "podcast");
        assert_eq!(snapshot.url, url);
        assert_eq!(snapshot.source_url.as_deref(), Some(source_url));
        assert_eq!(snapshot.platform.as_deref(), Some("podcast"));
        assert_eq!(snapshot.status, "processing");
        assert_eq!(snapshot.content_metadata, metadata);
    }

    let mut transaction = pool
        .begin()
        .await
        .expect("begin missing-content preparation");
    assert!(
        prepare_media_content(&mut transaction, i64::MAX)
            .await
            .expect("prepare missing content")
            .is_none()
    );
    transaction
        .commit()
        .await
        .expect("release missing-content transaction");
}
