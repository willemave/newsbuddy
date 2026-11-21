use chrono::{TimeZone, Utc};
use sqlx::PgPool;

use super::{ChatRecordAccess, get_chat_session_summary, list_chat_sessions};

#[sqlx::test]
async fn batched_session_list_matches_single_session_summaries(pool: PgPool) {
    crate::run_migrations(&pool).await.unwrap();
    sqlx::raw_sql(
        r#"
        INSERT INTO users (id, apple_id, email, is_active, is_admin) VALUES
            (9501, 'chat-list-one', 'one@chat-list.test', true, false);
        INSERT INTO contents (id, content_type, url, status, is_aggregate, content_metadata) VALUES
            (9501, 'article', 'https://chat-list.test/saved', 'completed', false, '{"title":"Saved"}'),
            (9502, 'article', 'https://chat-list.test/plain', 'completed', false, '{"title":"Plain"}');
        INSERT INTO content_status(user_id, content_id, status) VALUES
            (9501, 9501, 'inbox'), (9501, 9502, 'inbox');
        INSERT INTO content_knowledge_saves(user_id, content_id) VALUES (9501, 9501);
        INSERT INTO chat_sessions (id, user_id, content_id, title, session_type, llm_model, llm_provider, last_message_at) VALUES
            (9601, 9501, 9501, 'With messages', 'article_brain', 'openai:gpt-5.6-sol', 'openai', '2026-10-03 12:03:00'),
            (9602, 9501, 9502, 'Empty article', 'article_brain', 'openai:gpt-5.6-sol', 'openai', '2026-10-03 12:02:00'),
            (9603, 9501, NULL, 'Topic only', 'topic', '', '', '2026-10-03 12:01:00');
        "#,
    )
    .execute(&pool)
    .await
    .unwrap();
    let transcript = serde_json::to_string(&crate::chat_transcripts::processing_transcript(
        "Hello there",
        Utc.with_ymd_and_hms(2026, 10, 3, 12, 0, 0).unwrap(),
    ))
    .unwrap();
    for status in ["completed", "processing"] {
        sqlx::query("INSERT INTO chat_messages(session_id,message_list,status) VALUES(9601,$1,$2)")
            .bind(&transcript)
            .bind(status)
            .execute(&pool)
            .await
            .unwrap();
    }

    let listed = list_chat_sessions(&pool, 9501, None, None, None, 10)
        .await
        .unwrap();
    assert_eq!(
        listed.iter().map(|session| session.id).collect::<Vec<_>>(),
        [9601, 9602, 9603]
    );
    assert!(listed[0].has_pending_message);
    assert!(listed[0].is_saved_to_knowledge);
    assert!(listed[0].last_message_preview.is_some());
    assert!(!listed[1].has_pending_message);
    assert!(!listed[1].is_saved_to_knowledge);
    assert!(listed[1].last_message_preview.is_none());
    for session in &listed {
        let ChatRecordAccess::Found(single) = get_chat_session_summary(&pool, 9501, session.id)
            .await
            .unwrap()
        else {
            panic!("session {} should be readable", session.id);
        };
        assert_eq!(&single, session);
    }
}
