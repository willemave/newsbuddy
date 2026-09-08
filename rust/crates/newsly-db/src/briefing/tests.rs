use serde_json::json;
use sqlx::PgPool;

use super::{BriefingNarrationSelection, PrepareNarrationOutcome, prepare_briefing_narration};
use crate::AudioEpisodeProjection;

async fn seed(pool: &PgPool) {
    sqlx::raw_sql(
        r#"
        INSERT INTO users (id, apple_id, email, is_admin, is_active)
        VALUES (100, 'lens-audio', 'lens@example.com', false, true),
               (101, 'other-audio', 'other@example.com', false, true);
        INSERT INTO briefing_lenses (id, user_id, key, tier, title, deck, position, status, centroid_weight)
        VALUES (10, 100, 'ai', 'news', 'AI & Society', '', 0, 'active', 0),
               (20, 100, 'business', 'news', 'Business', '', 1, 'active', 0),
               (30, 100, 'empty', 'news', 'Empty', '', 2, 'active', 0),
               (40, 100, 'retired', 'news', 'Retired', '', 3, 'retired', 0);
        INSERT INTO news_items (id, ingest_key, visibility_scope, owner_user_id, status,
            summary_text, ingested_at, created_at)
        SELECT n, 'lens-audio-' || n, 'user', CASE WHEN n = 17 THEN 101 ELSE 100 END,
               'ready', 'Story ' || n, now(), now()
        FROM generate_series(1, 18) n;
        INSERT INTO briefing_segments (id, lens_id, user_id, blocks, markdown_raw, narration_text,
            source_keys, status, model, prompt_version, warnings, created_at)
        SELECT n, CASE WHEN n = 16 THEN 20 ELSE 10 END, 100, '[]', '', repeat('story ', 500),
               jsonb_build_array('news:' || n), CASE WHEN n = 18 THEN 'retired' ELSE 'active' END,
               'test', 'test', '[]', now() + n * interval '1 minute'
        FROM generate_series(1, 18) n;
        SELECT setval('briefing_segments_id_seq', 100);
        INSERT INTO news_item_read_status (user_id, news_item_id, read_at, created_at)
        VALUES (100, 1, now(), now());
        "#,
    ).execute(pool).await.expect("fixture");
}

async fn prepare(
    pool: &PgPool,
    selection: BriefingNarrationSelection,
) -> Vec<AudioEpisodeProjection> {
    let mut tx = pool.begin().await.expect("transaction");
    let outcome = prepare_briefing_narration(&mut tx, 100, &selection, true)
        .await
        .expect("prepare");
    tx.commit().await.expect("commit");
    let PrepareNarrationOutcome::Ready(episodes) = outcome else {
        panic!("expected ready narration");
    };
    episodes
}

fn planned_keys(episodes: &[AudioEpisodeProjection]) -> Vec<String> {
    episodes
        .iter()
        .flat_map(|episode| {
            episode.source_snapshot["source_keys"]
                .as_array()
                .expect("keys")
                .iter()
                .map(|key| key.as_str().expect("source key").to_owned())
        })
        .collect()
}

#[sqlx::test]
async fn adapted_lens_narration_is_complete_scoped_and_reuses_immutable_audio(pool: PgPool) {
    seed(&pool).await;
    let chapters = prepare(
        &pool,
        BriefingNarrationSelection::AdaptedLens("ai".to_owned()),
    )
    .await;
    // More than the API's 12-segment page, newest first. Excludes read, inaccessible,
    // retired, and other-lens sources before computing windows.
    let expected = (2..=15)
        .rev()
        .map(|id| format!("news:{id}"))
        .collect::<Vec<_>>();
    assert_eq!(planned_keys(&chapters), expected);
    assert_eq!(chapters.len(), 14);
    for (index, chapter) in chapters.iter().enumerate() {
        assert_eq!(
            chapter.chapter_index,
            Some(i32::try_from(index).expect("index"))
        );
        assert_eq!(chapter.source_snapshot["scope"], "lens");
        assert_eq!(chapter.source_snapshot["lens_key"], "ai");
        assert_eq!(chapter.source_snapshot["lens_title"], "AI & Society");
        assert_eq!(chapter.source_snapshot["lens_tier"], "news");
        assert!(chapter.script_text.is_none());
        assert!(chapter.source_snapshot.get("script_text").is_none());
        assert_eq!(
            chapter.source_snapshot["items"]
                .as_array()
                .expect("items")
                .len(),
            1
        );
    }
    let first_id = chapters[0].id;
    sqlx::query("UPDATE audio_episodes SET status = 'completed', script_text = 'Immutable script', audio_storage_path = '/test/audio.mp3' WHERE id::bigint = $1")
        .bind(first_id).execute(&pool).await.expect("complete first chapter");
    let reused = prepare(
        &pool,
        BriefingNarrationSelection::AdaptedLens("ai".to_owned()),
    )
    .await;
    assert_eq!(
        reused.iter().map(|e| e.id).collect::<Vec<_>>(),
        chapters.iter().map(|e| e.id).collect::<Vec<_>>()
    );
    assert_eq!(reused[0].script_text.as_deref(), Some("Immutable script"));
    assert_eq!(reused[0].status, "completed");

    let business = prepare(
        &pool,
        BriefingNarrationSelection::AdaptedLens("business".to_owned()),
    )
    .await;
    assert_eq!(planned_keys(&business), ["news:16"]);
    assert_ne!(business[0].episode_group_id, chapters[0].episode_group_id);
    let old_program = prepare(&pool, BriefingNarrationSelection::NewsProgram).await;
    assert!(planned_keys(&old_program).contains(&"news:16".to_owned()));
    assert_ne!(
        old_program[0].episode_group_id,
        chapters[0].episode_group_id
    );
    let legacy = prepare(&pool, BriefingNarrationSelection::Lens("ai".to_owned())).await;
    assert!(legacy[0].script_text.is_some());
    assert!(legacy[0].source_snapshot.get("scope").is_none());
    assert_ne!(legacy[0].episode_group_id, chapters[0].episode_group_id);

    // Generation has not changed read state. Completion owns only the stored window.
    let read_before: i64 =
        sqlx::query_scalar("SELECT count(*) FROM news_item_read_status WHERE user_id = 100")
            .fetch_one(&pool)
            .await
            .expect("read count");
    assert_eq!(read_before, 1);
    let mut tx = pool.begin().await.expect("transaction");
    let episode = crate::find_user_audio_episode(&pool, 100, first_id)
        .await
        .expect("episode query")
        .expect("episode");
    assert!(
        crate::mark_audio_episode_sources_read(
            &mut tx,
            &episode,
            crate::AudioEpisodeReadTrigger::Play
        )
        .await
        .expect("play does not mark read")
        .is_none()
    );
    crate::mark_audio_episode_sources_read(
        &mut tx,
        &episode,
        crate::AudioEpisodeReadTrigger::Finish,
    )
    .await
    .expect("chapter read marks");
    tx.commit().await.expect("commit read marks");
    let read: Vec<i32> = sqlx::query_scalar(
        "SELECT news_item_id FROM news_item_read_status WHERE user_id = 100 ORDER BY news_item_id",
    )
    .fetch_all(&pool)
    .await
    .expect("read keys");
    assert_eq!(read, [1, 15]);
}

#[sqlx::test]
async fn adapted_lens_narration_never_falls_back_for_empty_or_inaccessible_lenses(pool: PgPool) {
    seed(&pool).await;
    for (user_id, key, empty) in [
        (100, "empty", true),
        (100, "missing", false),
        (100, "retired", false),
        (101, "ai", false),
    ] {
        let mut tx = pool.begin().await.expect("transaction");
        let outcome = prepare_briefing_narration(
            &mut tx,
            user_id,
            &BriefingNarrationSelection::AdaptedLens(key.to_owned()),
            true,
        )
        .await
        .expect("prepare");
        if empty {
            assert!(matches!(outcome, PrepareNarrationOutcome::Empty));
        } else {
            assert!(matches!(outcome, PrepareNarrationOutcome::LensNotFound));
        }
        tx.commit().await.expect("commit");
    }
    let count: i64 = sqlx::query_scalar("SELECT count(*) FROM audio_episodes")
        .fetch_one(&pool)
        .await
        .expect("episode count");
    assert_eq!(count, 0);
}

#[sqlx::test]
async fn adapted_lens_narration_uses_document_chapters_for_articles_and_podcasts(pool: PgPool) {
    seed(&pool).await;
    for (id, tier, kind) in [(50_i32, "longform", "article"), (60, "audio", "podcast")] {
        sqlx::query("INSERT INTO briefing_lenses (id, user_id, key, tier, title, deck, position, status, centroid_weight) VALUES ($1, 100, $2, $3, $2, '', $1, 'active', 0)")
            .bind(id).bind(kind).bind(tier).execute(&pool).await.expect("document lens");
        for offset in 0..2 {
            let content_id = id + offset;
            sqlx::query("INSERT INTO contents (id, content_type, url, title, source, status, content_metadata, is_aggregate) VALUES ($1, $2, $3, $4, 'Publication', 'completed', $5, false)")
                .bind(content_id).bind(kind).bind(format!("https://example.com/{content_id}"))
                .bind(format!("Document {content_id}"))
                .bind(json!({"summary": {"overview": "Long source context"}, "image_generated_at": "2026-09-05T12:00:00Z", "image_url": "/image.png"}))
                .execute(&pool).await.expect("content");
            sqlx::query("INSERT INTO content_status (user_id, content_id, status) VALUES (100, $1, 'inbox')")
                .bind(content_id).execute(&pool).await.expect("membership");
            sqlx::query("INSERT INTO briefing_segments (lens_id, user_id, blocks, markdown_raw, narration_text, source_keys, status, model, prompt_version, warnings) VALUES ($1, 100, '[]', '', 'Short visible prose', $2, 'active', 'test', 'test', '[]')")
                .bind(id).bind(json!([format!("content:{content_id}")])).execute(&pool).await.expect("segment");
        }
        let chapters = prepare(
            &pool,
            BriefingNarrationSelection::AdaptedLens(kind.to_owned()),
        )
        .await;
        assert_eq!(chapters.len(), 2);
        for chapter in chapters {
            assert!(chapter.title.starts_with("Document "));
            assert_eq!(chapter.source_snapshot["lens_tier"], tier);
            assert_eq!(
                chapter.source_snapshot["source_keys"]
                    .as_array()
                    .map(Vec::len),
                Some(1)
            );
            assert_eq!(
                chapter.source_snapshot["items"][0]["source_name"],
                "Publication"
            );
            assert!(chapter.script_text.is_none());
        }
    }
}

#[sqlx::test]
async fn narration_retry_preserves_original_chapters_after_sources_are_read(pool: PgPool) {
    seed(&pool).await;
    let chapters = prepare(
        &pool,
        BriefingNarrationSelection::AdaptedLens("ai".to_owned()),
    )
    .await;
    let group = chapters[0].episode_group_id.as_deref().expect("group");
    sqlx::query("UPDATE audio_episodes SET status = CASE WHEN id::bigint = $1 THEN 'completed' WHEN id::bigint = $2 THEN 'processing' ELSE 'failed' END, script_text = 'Saved script', error_message = 'Failed synthesis' WHERE episode_group_id = $3")
        .bind(chapters[0].id).bind(chapters[2].id).bind(group).execute(&pool).await.expect("chapter states");
    sqlx::query("INSERT INTO news_item_read_status (user_id, news_item_id, read_at, created_at) VALUES (100, 15, now(), now())")
        .execute(&pool).await.expect("first source consumed");
    let fresh = prepare(
        &pool,
        BriefingNarrationSelection::AdaptedLens("ai".to_owned()),
    )
    .await;
    assert_ne!(fresh[0].episode_group_id.as_deref(), Some(group));
    let mut tx = pool.begin().await.expect("transaction");
    assert!(
        super::retry_briefing_narration(&mut tx, 101, group)
            .await
            .expect("wrong owner")
            .is_empty()
    );
    let retry = super::retry_briefing_narration(&mut tx, 100, group)
        .await
        .expect("retry");
    assert_eq!(
        retry.iter().map(|c| c.id).collect::<Vec<_>>(),
        chapters.iter().map(|c| c.id).collect::<Vec<_>>()
    );
    assert_eq!(retry[0].status, "completed");
    assert_eq!(retry[1].status, "pending");
    assert_eq!(retry[2].status, "processing");
    assert!(retry[1].error_message.is_none());
    for (before, after) in chapters.iter().zip(&retry) {
        assert_eq!(before.source_snapshot, after.source_snapshot);
        assert_eq!(before.chapter_index, after.chapter_index);
        assert_eq!(after.script_text.as_deref(), Some("Saved script"));
    }
    let again = super::retry_briefing_narration(&mut tx, 100, group)
        .await
        .expect("repeat retry");
    assert_eq!(
        again.iter().map(|c| (&c.status, c.id)).collect::<Vec<_>>(),
        retry.iter().map(|c| (&c.status, c.id)).collect::<Vec<_>>()
    );
    tx.rollback().await.expect("rollback");
    let rolled_back = super::load_briefing_narration(&pool, 100, group)
        .await
        .expect("original");
    assert_eq!(rolled_back[1].status, "failed");
}
