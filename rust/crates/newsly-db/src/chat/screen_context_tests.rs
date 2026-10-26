use serde_json::json;
use sqlx::PgPool;

use super::{AssistantScreenContext, build_screen_context_snapshot};

#[test]
fn content_titles_share_nonblank_fallback_and_bounded_display_policy() {
    let title = super::content_display_title;
    assert_eq!(
        title(
            Some("Stored"),
            &json!({"summary": {
                "title": "  ", "feed_preview": {"title": "  Feed\n title  "}
            }})
        )
        .as_deref(),
        Some("Feed title")
    );
    assert_eq!(
        title(Some("Stored"), &json!({"summary":{"title":" "}})).as_deref(),
        Some("Stored")
    );
    assert_eq!(
        title(
            Some("Original"),
            &json!({"domain":{"summary":{"title":"Reader headline"}}})
        )
        .as_deref(),
        Some("Reader headline")
    );
    assert_eq!(
        title(
            None,
            &json!({"summary":{"artifact":{"payload":{"overview":"Artifact overview"}}}})
        )
        .as_deref(),
        Some("Artifact overview")
    );
    assert_eq!(title(Some(" "), &json!({"summary":{"one_line":" "}})), None);
    assert_eq!(
        title(Some(&"é".repeat(121)), &json!({})).unwrap(),
        format!("{}…", "é".repeat(120))
    );
}

#[sqlx::test(migrations = "./migrations")]
async fn screen_context_snapshot_preserves_order_trimming_and_excerpt_limits(pool: PgPool) {
    let user_id = sqlx::query_scalar::<_, i64>(
        "INSERT INTO users (apple_id,email,is_admin,is_active) VALUES ('screen-golden','screen@example.com',FALSE,TRUE) RETURNING id::bigint",
    ).fetch_one(&pool).await.unwrap();
    let mut ids = Vec::new();
    for (title, excerpt) in [
        ("Primary", "a".repeat(1_900)),
        ("Secondary", "b".repeat(500)),
    ] {
        ids.push(sqlx::query_scalar::<_, i64>(
            "INSERT INTO contents (content_type,url,status,is_aggregate,content_metadata) VALUES ('article',$1,'completed',FALSE,$2) RETURNING id::bigint",
        ).bind(format!("https://example.com/{title}"))
        .bind(json!({"title":title,"excerpt":excerpt,"summary":{"title":title,"one_line":"Summary"}}))
        .fetch_one(&pool).await.unwrap());
    }
    let context: AssistantScreenContext = serde_json::from_value(json!({
        "screen_type":" learning_deck ","screen_title":" Reader ","content_id":ids[0],
        "visible_content_ids":[ids[1],ids[0],0,-1],"selected_topic":" topic ",
        "query":" q ","note":" ","assistant_action":" explain "
    }))
    .unwrap();
    let mut transaction = pool.begin().await.unwrap();
    let rendered = build_screen_context_snapshot(&mut transaction, user_id, &context)
        .await
        .unwrap();
    let expected = format!(
        "Screen Type: learning_deck\nScreen Title: Reader\nSelected Topic: topic\nQuery: q\nAssistant Action: explain\nVisible Content:\n- [{}] Primary — https://example.com/Primary\n  Short Summary: Summary\n  Transcript Excerpt: {}...\n- [{}] Secondary — https://example.com/Secondary\n  Short Summary: Summary\n  Transcript Excerpt: {}...",
        ids[0],
        "a".repeat(1_797),
        ids[1],
        "b".repeat(417)
    );
    assert_eq!(rendered, expected);
    assert_eq!(
        build_screen_context_snapshot(
            &mut transaction,
            user_id,
            &serde_json::from_value(json!({"screen_type":" "})).unwrap()
        )
        .await
        .unwrap(),
        "Screen Type: unknown"
    );
}

#[sqlx::test(migrations = "./migrations")]
async fn staged_followup_bounds_title_before_encoding_and_rejects_invalid_context(pool: PgPool) {
    use super::{
        StageAssistantTurnInput, StageChatMessageInput, StageChatTurnOutcome, stage_assistant_turn,
        stage_chat_message,
    };
    let user_id = sqlx::query_scalar::<_, i64>(
        "INSERT INTO users (apple_id,email,is_admin,is_active) VALUES ('screen-stage','stage@example.com',FALSE,TRUE) RETURNING id::bigint",
    ).fetch_one(&pool).await.unwrap();
    let session_id = sqlx::query_scalar::<_, i64>(
        "INSERT INTO chat_sessions (user_id,title,session_type,llm_model,llm_provider) VALUES ($1,$2,'knowledge_chat','openai:gpt-5.6-sol','openai') RETURNING id::bigint",
    ).bind(user_id).bind("t".repeat(500)).fetch_one(&pool).await.unwrap();
    let mut transaction = pool.begin().await.unwrap();
    let StageChatTurnOutcome::Staged(staged) = stage_chat_message(
        &mut transaction,
        &StageChatMessageInput {
            user_id,
            session_id,
            user_prompt: "Explain this",
        },
    )
    .await
    .unwrap() else {
        panic!("expected staged turn")
    };
    let payload: crate::chat_tasks::ChatTurnProcessingContext =
        serde_json::from_value(staged.processing_context).unwrap();
    payload.screen_context.as_ref().unwrap().validate().unwrap();
    assert_eq!(
        payload
            .screen_context
            .unwrap()
            .screen_title
            .unwrap()
            .chars()
            .count(),
        200
    );
    let invalid =
        serde_json::from_value(json!({"screen_type":"knowledge_chat","visible_content_ids":[0]}))
            .unwrap();
    assert!(
        stage_assistant_turn(
            &mut transaction,
            &StageAssistantTurnInput {
                user_id,
                session_id: None,
                user_prompt: "Invalid",
                screen_context: &invalid
            }
        )
        .await
        .is_err()
    );
    let count = sqlx::query_scalar::<_, i64>("SELECT count(*) FROM chat_messages")
        .fetch_one(&mut *transaction)
        .await
        .unwrap();
    assert_eq!(count, 1);
}
