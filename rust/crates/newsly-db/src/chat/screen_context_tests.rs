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
        let content_id = sqlx::query_scalar::<_, i64>(
            "INSERT INTO contents (content_type,url,status,is_aggregate,content_metadata) VALUES ('article',$1,'completed',FALSE,$2) RETURNING id::bigint",
        ).bind(format!("https://example.com/{title}"))
        .bind(json!({"title":title,"excerpt":excerpt,"summary":{"title":title,"one_line":"Summary"}}))
        .fetch_one(&pool).await.unwrap();
        sqlx::query(
            "INSERT INTO content_status (user_id,content_id,status) VALUES ($1,$2,'inbox')",
        )
        .bind(user_id)
        .bind(content_id)
        .execute(&pool)
        .await
        .unwrap();
        ids.push(content_id);
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

#[sqlx::test(migrations = "./migrations")]
async fn chat_enforces_c3_content_visibility_across_create_stage_and_screen_context(pool: PgPool) {
    use super::{
        CreateChatSessionInput, CreateChatSessionOutcome, StageAssistantTurnInput,
        StageChatTurnOutcome, create_chat_session, stage_assistant_turn,
    };
    let owner_id = sqlx::query_scalar::<_, i64>(
        "INSERT INTO users (apple_id,email,is_admin,is_active) VALUES ('c3-owner','c3-owner@example.com',FALSE,TRUE) RETURNING id::bigint",
    )
    .fetch_one(&pool)
    .await
    .unwrap();
    let other_id = sqlx::query_scalar::<_, i64>(
        "INSERT INTO users (apple_id,email,is_admin,is_active) VALUES ('c3-other','c3-other@example.com',FALSE,TRUE) RETURNING id::bigint",
    )
    .fetch_one(&pool)
    .await
    .unwrap();
    let private_content_id = sqlx::query_scalar::<_, i64>(
        "INSERT INTO contents (content_type,url,status,is_aggregate,content_metadata) VALUES ('article','https://example.com/private','completed',FALSE,$1) RETURNING id::bigint",
    )
    .bind(json!({"title":"Secret Title","excerpt":"Secret Excerpt","summary":{"title":"Secret Title","one_line":"Secret Summary"}}))
    .fetch_one(&pool)
    .await
    .unwrap();
    sqlx::query("INSERT INTO content_status (user_id,content_id,status) VALUES ($1,$2,'inbox')")
        .bind(owner_id)
        .bind(private_content_id)
        .execute(&pool)
        .await
        .unwrap();

    let mut transaction = pool.begin().await.unwrap();
    let create_outcome = create_chat_session(
        &mut transaction,
        &CreateChatSessionInput {
            user_id: other_id,
            content_id: Some(private_content_id),
            news_item_id: None,
            topic: None,
            initial_message: None,
            session_type: "knowledge_chat",
            llm_provider: "openai",
            llm_model: "openai:gpt-6.1-sol",
        },
    )
    .await
    .unwrap();
    assert_eq!(create_outcome, CreateChatSessionOutcome::ContentNotFound);

    let stage_context: AssistantScreenContext = serde_json::from_value(json!({
        "screen_type": "knowledge_chat",
        "content_id": private_content_id
    }))
    .unwrap();
    let stage_outcome = stage_assistant_turn(
        &mut transaction,
        &StageAssistantTurnInput {
            user_id: other_id,
            session_id: None,
            user_prompt: "Summarize this",
            screen_context: &stage_context,
        },
    )
    .await
    .unwrap();
    assert_eq!(stage_outcome, StageChatTurnOutcome::ContentNotFound);

    let visible_ids_context: AssistantScreenContext = serde_json::from_value(json!({
        "screen_type": "knowledge_chat",
        "visible_content_ids": [private_content_id]
    }))
    .unwrap();
    let rendered = build_screen_context_snapshot(&mut transaction, other_id, &visible_ids_context)
        .await
        .unwrap();
    assert!(!rendered.contains("Secret Title"));
    assert!(!rendered.contains("Secret Summary"));
    assert!(!rendered.contains("Secret Excerpt"));
    assert!(!rendered.contains("Visible Content:"));
}

#[sqlx::test(migrations = "./migrations")]
async fn learning_deck_ownership_grants_content_visibility_until_soft_deleted(pool: PgPool) {
    use super::{
        CreateChatSessionInput, CreateChatSessionOutcome, StageAssistantTurnInput,
        StageChatTurnOutcome, create_chat_session, stage_assistant_turn,
    };
    let owner_id = sqlx::query_scalar::<_, i64>(
        "INSERT INTO users (apple_id,email,is_admin,is_active) VALUES ('deck-owner','deck-owner@example.com',FALSE,TRUE) RETURNING id::bigint",
    )
    .fetch_one(&pool)
    .await
    .unwrap();
    let other_id = sqlx::query_scalar::<_, i64>(
        "INSERT INTO users (apple_id,email,is_admin,is_active) VALUES ('deck-other','deck-other@example.com',FALSE,TRUE) RETURNING id::bigint",
    )
    .fetch_one(&pool)
    .await
    .unwrap();
    let deck_content_id = sqlx::query_scalar::<_, i64>(
        "INSERT INTO contents (content_type,url,status,is_aggregate,content_metadata) VALUES ('article','https://example.com/deck-source','completed',FALSE,$1) RETURNING id::bigint",
    )
    .bind(json!({"title":"Deck Source Title","summary":{"title":"Deck Source Title","one_line":"Deck Source Summary"}}))
    .fetch_one(&pool)
    .await
    .unwrap();
    sqlx::query(
        "INSERT INTO learning_decks (user_id,source_kind,source_identity,source_content_id,source_metadata,title,artifact_object_keys,share_enabled) VALUES ($1,'article','deck-source-1',$2,'{}'::jsonb,'My Deck','{}'::jsonb,FALSE)",
    )
    .bind(owner_id)
    .bind(deck_content_id)
    .execute(&pool)
    .await
    .unwrap();
    let deleted_deck_content_id = sqlx::query_scalar::<_, i64>(
        "INSERT INTO contents (content_type,url,status,is_aggregate,content_metadata) VALUES ('article','https://example.com/deleted-deck-source','completed',FALSE,$1) RETURNING id::bigint",
    )
    .bind(json!({"title":"Deleted Deck Source","summary":{"title":"Deleted Deck Source","one_line":"Deleted Deck Summary"}}))
    .fetch_one(&pool)
    .await
    .unwrap();
    sqlx::query(
        "INSERT INTO learning_decks (user_id,source_kind,source_identity,source_content_id,source_metadata,title,artifact_object_keys,share_enabled,deleted_at) VALUES ($1,'article','deck-source-2',$2,'{}'::jsonb,'Deleted Deck','{}'::jsonb,FALSE,timezone('UTC', now()))",
    )
    .bind(owner_id)
    .bind(deleted_deck_content_id)
    .execute(&pool)
    .await
    .unwrap();

    let mut transaction = pool.begin().await.unwrap();
    let owner_create = create_chat_session(
        &mut transaction,
        &CreateChatSessionInput {
            user_id: owner_id,
            content_id: Some(deck_content_id),
            news_item_id: None,
            topic: None,
            initial_message: None,
            session_type: "knowledge_chat",
            llm_provider: "openai",
            llm_model: "openai:gpt-6.1-sol",
        },
    )
    .await
    .unwrap();
    assert!(matches!(
        owner_create,
        CreateChatSessionOutcome::Created { .. }
    ));

    let other_create = create_chat_session(
        &mut transaction,
        &CreateChatSessionInput {
            user_id: other_id,
            content_id: Some(deck_content_id),
            news_item_id: None,
            topic: None,
            initial_message: None,
            session_type: "knowledge_chat",
            llm_provider: "openai",
            llm_model: "openai:gpt-6.1-sol",
        },
    )
    .await
    .unwrap();
    assert_eq!(other_create, CreateChatSessionOutcome::ContentNotFound);

    let deck_context: AssistantScreenContext = serde_json::from_value(json!({
        "screen_type": "learning_deck",
        "content_id": deck_content_id
    }))
    .unwrap();
    let owner_rendered = build_screen_context_snapshot(&mut transaction, owner_id, &deck_context)
        .await
        .unwrap();
    assert!(owner_rendered.contains("Deck Source Title"));

    let deleted_deck_context: AssistantScreenContext = serde_json::from_value(json!({
        "screen_type": "learning_deck",
        "content_id": deleted_deck_content_id
    }))
    .unwrap();
    let deleted_stage = stage_assistant_turn(
        &mut transaction,
        &StageAssistantTurnInput {
            user_id: owner_id,
            session_id: None,
            user_prompt: "Explain this deck",
            screen_context: &deleted_deck_context,
        },
    )
    .await
    .unwrap();
    assert_eq!(deleted_stage, StageChatTurnOutcome::ContentNotFound);

    let deleted_rendered =
        build_screen_context_snapshot(&mut transaction, owner_id, &deleted_deck_context)
            .await
            .unwrap();
    assert!(!deleted_rendered.contains("Deleted Deck Source"));
    assert!(!deleted_rendered.contains("Visible Content:"));
}

#[sqlx::test(migrations = "./migrations")]
async fn c3_visibility_excludes_archived_sessions_and_supports_revocation_and_saved_access(
    pool: PgPool,
) {
    use super::{
        CreateChatSessionInput, CreateChatSessionOutcome, StageAssistantTurnInput,
        StageChatTurnOutcome, create_chat_session, stage_assistant_turn,
    };
    let owner_id = sqlx::query_scalar::<_, i64>(
        "INSERT INTO users (apple_id,email,is_admin,is_active) VALUES ('c3-ext-owner','c3-ext-owner@example.com',FALSE,TRUE) RETURNING id::bigint",
    )
    .fetch_one(&pool)
    .await
    .unwrap();
    let other_id = sqlx::query_scalar::<_, i64>(
        "INSERT INTO users (apple_id,email,is_admin,is_active) VALUES ('c3-ext-other','c3-ext-other@example.com',FALSE,TRUE) RETURNING id::bigint",
    )
    .fetch_one(&pool)
    .await
    .unwrap();

    // 1. Archived chat session alone does NOT grant visibility; saving to knowledge DOES.
    let archived_content_id = sqlx::query_scalar::<_, i64>(
        "INSERT INTO contents (content_type,url,status,is_aggregate,content_metadata) VALUES ('article','https://example.com/archived-only','completed',FALSE,$1) RETURNING id::bigint",
    )
    .bind(json!({"title":"Archived Link Title","summary":{"title":"Archived Link Title","one_line":"Archived Link Summary"}}))
    .fetch_one(&pool)
    .await
    .unwrap();
    sqlx::query(
        "INSERT INTO chat_sessions (user_id,content_id,title,session_type,llm_model,llm_provider,is_archived) VALUES ($1,$2,'Old','knowledge_chat','openai:gpt-6.1-sol','openai',TRUE)",
    )
    .bind(owner_id)
    .bind(archived_content_id)
    .execute(&pool)
    .await
    .unwrap();

    let mut transaction = pool.begin().await.unwrap();
    let archived_create = create_chat_session(
        &mut transaction,
        &CreateChatSessionInput {
            user_id: owner_id,
            content_id: Some(archived_content_id),
            news_item_id: None,
            topic: None,
            initial_message: None,
            session_type: "knowledge_chat",
            llm_provider: "openai",
            llm_model: "openai:gpt-6.1-sol",
        },
    )
    .await
    .unwrap();
    assert_eq!(archived_create, CreateChatSessionOutcome::ContentNotFound);

    // Saving the same content to Knowledge restores access even when the historical session is archived.
    sqlx::query("INSERT INTO content_knowledge_saves (user_id,content_id) VALUES ($1,$2)")
        .bind(owner_id)
        .bind(archived_content_id)
        .execute(&mut *transaction)
        .await
        .unwrap();
    let saved_create = create_chat_session(
        &mut transaction,
        &CreateChatSessionInput {
            user_id: owner_id,
            content_id: Some(archived_content_id),
            news_item_id: None,
            topic: None,
            initial_message: None,
            session_type: "knowledge_chat",
            llm_provider: "openai",
            llm_model: "openai:gpt-6.1-sol",
        },
    )
    .await
    .unwrap();
    assert!(matches!(
        saved_create,
        CreateChatSessionOutcome::Created { .. }
    ));

    // 2. Staging on an existing session with a foreign content_id is rejected.
    let existing_other_session_id = sqlx::query_scalar::<_, i64>(
        "INSERT INTO chat_sessions (user_id,title,session_type,llm_model,llm_provider) VALUES ($1,'General','knowledge_chat','openai:gpt-6.1-sol','openai') RETURNING id::bigint",
    )
    .bind(other_id)
    .fetch_one(&mut *transaction)
    .await
    .unwrap();
    let foreign_context: AssistantScreenContext = serde_json::from_value(json!({
        "screen_type": "knowledge_chat",
        "content_id": archived_content_id
    }))
    .unwrap();
    let foreign_stage = stage_assistant_turn(
        &mut transaction,
        &StageAssistantTurnInput {
            user_id: other_id,
            session_id: Some(existing_other_session_id),
            user_prompt: "Read this foreign article",
            screen_context: &foreign_context,
        },
    )
    .await
    .unwrap();
    assert_eq!(foreign_stage, StageChatTurnOutcome::ContentNotFound);

    // 3. Soft-deleting an owned learning deck revokes visibility on the SAME content_id when no active chat exists.
    let revocable_content_id = sqlx::query_scalar::<_, i64>(
        "INSERT INTO contents (content_type,url,status,is_aggregate,content_metadata) VALUES ('article','https://example.com/revocable-deck','completed',FALSE,$1) RETURNING id::bigint",
    )
    .bind(json!({"title":"Revocable Deck Title","summary":{"title":"Revocable Deck Title","one_line":"Summary"}}))
    .fetch_one(&mut *transaction)
    .await
    .unwrap();
    let revocable_deck_id = sqlx::query_scalar::<_, i64>(
        "INSERT INTO learning_decks (user_id,source_kind,source_identity,source_content_id,source_metadata,title,artifact_object_keys,share_enabled) VALUES ($1,'article','revocable-1',$2,'{}'::jsonb,'Revocable','{}'::jsonb,FALSE) RETURNING id::bigint",
    )
    .bind(owner_id)
    .bind(revocable_content_id)
    .fetch_one(&mut *transaction)
    .await
    .unwrap();
    let revocable_ctx: AssistantScreenContext = serde_json::from_value(json!({
        "screen_type": "learning_deck",
        "content_id": revocable_content_id
    }))
    .unwrap();
    assert!(
        build_screen_context_snapshot(&mut transaction, owner_id, &revocable_ctx)
            .await
            .unwrap()
            .contains("Revocable Deck Title")
    );
    sqlx::query("UPDATE learning_decks SET deleted_at = timezone('UTC', now()) WHERE id = $1")
        .bind(revocable_deck_id)
        .execute(&mut *transaction)
        .await
        .unwrap();
    assert!(
        !build_screen_context_snapshot(&mut transaction, owner_id, &revocable_ctx)
            .await
            .unwrap()
            .contains("Revocable Deck Title")
    );
}
