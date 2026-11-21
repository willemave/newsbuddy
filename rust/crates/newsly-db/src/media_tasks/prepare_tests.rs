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

#[sqlx::test]
async fn deck_podcast_is_readable_at_summary_handoff(pool: PgPool) {
    use super::{
        MediaApplyOutcome, MediaMutation, MediaNextTask, MediaTranscriptPointer,
        apply_media_mutation,
    };

    let content_id: i64 = sqlx::query_scalar(
        "INSERT INTO contents (content_type, url, status, is_aggregate, content_metadata)
         VALUES ('podcast', 'https://example.test/episode', 'processing', FALSE, $1) RETURNING id::bigint",
    )
    .bind(json!({"processing": {"deck_source_only": true}}))
    .fetch_one(&pool)
    .await
    .unwrap();
    let transcript = "The episode transcript";
    let mut tx = pool.begin().await.unwrap();
    let result = apply_media_mutation(
        &mut tx,
        content_id,
        &MediaMutation::PodcastCompleted {
            body: MediaTranscriptPointer {
                storage_provider: "local".to_owned(),
                storage_bucket: None,
                storage_key: "fixture/transcript.txt".to_owned(),
                content_format: "text".to_owned(),
                sha256: "a".repeat(64),
                byte_size: 22,
                char_count: 22,
            },
            transcript: transcript.to_owned(),
            transcription_at: chrono::Utc::now(),
            transcription_service: "fixture".to_owned(),
            detected_language: None,
            resolved_audio_url: None,
            resolved_feed_url: None,
            resolved_episode_title: None,
        },
    )
    .await
    .unwrap();
    assert_eq!(
        result,
        MediaApplyOutcome::Applied {
            next_task: Some(MediaNextTask::Summarize)
        }
    );
    let (status, body_count): (String, i32) = sqlx::query_as(
        "SELECT content.status, body.char_count FROM contents AS content
         JOIN content_bodies AS body ON body.content_id = content.id AND body.variant = 'source'
         WHERE content.id = $1",
    )
    .bind(content_id)
    .fetch_one(&mut *tx)
    .await
    .unwrap();
    assert_eq!((status.as_str(), body_count), ("completed", 22));
}
