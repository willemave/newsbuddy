use serde_json::json;

use super::{BriefingNarrationRequest, BriefingNarrationSelection, narration_selection};

#[test]
fn adapted_lens_narration_requires_an_explicit_valid_lens_key() {
    for key in ["ai-society", "news", "articles"] {
        let request = serde_json::from_value(json!({"scope": "lens", "lens_key": key}))
            .expect("valid request");
        assert_eq!(
            narration_selection(request, true),
            Some(BriefingNarrationSelection::AdaptedLens(key.to_owned()))
        );
    }
    for payload in [
        json!({}),
        json!({"scope": "lens"}),
        json!({"scope": "lens", "lens_key": ""}),
        json!({"scope": "lens", "lens_key": "  "}),
        json!({"scope": "lens", "lens_key": "x".repeat(65)}),
        json!({"scope": "news_program", "lens_key": "ai-society"}),
        json!({"scope": "article_tier", "lens_key": "articles"}),
        json!({"scope": "podcast_tier", "lens_key": "podcasts"}),
    ] {
        let request = serde_json::from_value(payload.clone()).expect("request shape");
        assert_eq!(narration_selection(request, true), None, "{payload}");
    }
    assert!(
        serde_json::from_value::<BriefingNarrationRequest>(json!({"scope": "unsupported"}))
            .is_err()
    );
}

#[test]
fn legacy_narration_requests_keep_their_selection_semantics() {
    for chaptered in [false, true] {
        let request =
            serde_json::from_value(json!({"lens_key": "ai-society"})).expect("legacy request");
        assert_eq!(
            narration_selection(request, chaptered),
            Some(BriefingNarrationSelection::Lens("ai-society".to_owned()))
        );
    }
    for (scope, expected) in [
        ("news_program", BriefingNarrationSelection::NewsProgram),
        ("article_tier", BriefingNarrationSelection::ArticleTier),
        ("podcast_tier", BriefingNarrationSelection::PodcastTier),
    ] {
        let payload = json!({"scope": scope});
        let request = serde_json::from_value(payload.clone()).expect("scope request");
        assert_eq!(narration_selection(request, true), Some(expected));
        let request = serde_json::from_value(payload).expect("scope request");
        assert_eq!(narration_selection(request, false), None);
    }
    let request = serde_json::from_value(json!({"scope": "lens", "lens_key": "ai"}))
        .expect("adapted request");
    assert_eq!(narration_selection(request, false), None);
}

#[sqlx::test(migrations = "../newsly-db/migrations")]
async fn narration_retry_enqueues_atomically_and_deduplicates(pool: sqlx::PgPool) {
    sqlx::raw_sql(r"
        INSERT INTO users (id, apple_id, email, is_admin, is_active)
        VALUES (100, 'narration-retry', 'narration-retry@example.com', false, true);
        INSERT INTO audio_episodes (id, user_id, kind, status, title, input_hash,
            episode_group_id, chapter_index, source_item_ids, source_snapshot,
            prompt_version, audio_content_type, share_enabled)
        VALUES (41, 100, 'briefing_narration', 'completed', 'A', 'a', 'original', 0, '[]', '{}', 5, 'audio/mpeg', false),
               (42, 100, 'briefing_narration', 'failed', 'B', 'b', 'original', 1, '[]', '{}', 5, 'audio/mpeg', false);
    ").execute(&pool).await.expect("fixture");
    // Queue insertion and chapter state must roll back together.
    let mut tx = pool.begin().await.expect("transaction");
    let episodes = super::retry_briefing_narration(&mut tx, 100, "original")
        .await
        .expect("retry");
    super::enqueue_narration(&pool, &mut tx, &episodes, 100, "test")
        .await
        .expect("enqueue");
    tx.rollback().await.expect("rollback");
    let status: String = sqlx::query_scalar("SELECT status FROM audio_episodes WHERE id = 42")
        .fetch_one(&pool)
        .await
        .expect("status");
    assert_eq!(status, "failed");
    let count: i64 = sqlx::query_scalar("SELECT count(*) FROM processing_tasks")
        .fetch_one(&pool)
        .await
        .expect("task count");
    assert_eq!(count, 0);
    for _ in 0..2 {
        let mut tx = pool.begin().await.expect("transaction");
        let episodes = super::retry_briefing_narration(&mut tx, 100, "original")
            .await
            .expect("retry");
        super::enqueue_narration(&pool, &mut tx, &episodes, 100, "test")
            .await
            .expect("enqueue");
        tx.commit().await.expect("commit");
    }
    let tasks: Vec<(String, String)> =
        sqlx::query_as("SELECT dedupe_key, status FROM processing_tasks ORDER BY id")
            .fetch_all(&pool)
            .await
            .expect("tasks");
    assert_eq!(
        tasks,
        [("audio_episode:42".to_owned(), "pending".to_owned())]
    );
}
