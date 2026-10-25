//! Attempt-owned LLM ledger transitions. Model usage is recorded at response observation.

use super::{
    ChatTaskRepositoryError, ChatTurnKind, ChatTurnProcessingContext, ChatTurnPublication,
    Postgres, Transaction,
    context::status_entry,
    json,
    preparation::{canonical_provider, validate_positive},
};
use newsly_domain::truncate_chars;

/// Cancel the attempt-owned LLM ledger without mutating canonical chat state.
///
/// This deliberately does not require a queue lease: an attempt owns its unique ledger row even
/// after losing the lease that fences the chat message. The user and task id are both checked so a
/// stale worker cannot affect a different account's workflow.
pub async fn cancel_chat_llm_task_attempt(
    transaction: &mut Transaction<'_, Postgres>,
    task_id: i64,
    user_id: i64,
    note: &str,
) -> Result<(), ChatTaskRepositoryError> {
    validate_positive(task_id, "llm_task_id")?;
    validate_positive(user_id, "user_id")?;
    let entry = status_entry("cancelled", "cancelled", note);
    sqlx::query(
        r#"
        UPDATE llm_tasks
        SET status = 'cancelled',
            workflow_state = 'cancelled',
            error_type = 'LeaseOwnershipLost',
            error_message = $3,
            completed_at = timezone('UTC', clock_timestamp()),
            updated_at = timezone('UTC', clock_timestamp()),
            status_history = COALESCE(status_history, '[]'::jsonb) || jsonb_build_array($4::jsonb)
        WHERE id::bigint = $1
          AND user_id::bigint = $2
          AND status NOT IN ('completed', 'failed', 'cancelled')
        "#,
    )
    .bind(task_id)
    .bind(user_id)
    .bind(note)
    .bind(entry)
    .execute(&mut **transaction)
    .await?;
    Ok(())
}

pub(super) async fn create_chat_llm_task(
    transaction: &mut Transaction<'_, Postgres>,
    queue_task_id: i64,
    message_id: i64,
    stream_generation: i32,
    context: &ChatTurnProcessingContext,
) -> Result<i64, ChatTaskRepositoryError> {
    let (task_kind, mode, workflow_key, prompt_pack, allowed_actions, tool_policy, note) =
        match context.kind {
            ChatTurnKind::Article => (
                "article_chat",
                "article_chat",
                "chat.article.v1",
                "chat.article",
                json!([]),
                json!({"execute_bash": true, "web_search": true, "files": "read_write"}),
                "Running async article chat agent",
            ),
            ChatTurnKind::Assistant => (
                "assistant_chat",
                "contextual_assistant",
                "chat.contextual_assistant.v1",
                "chat.contextual_assistant",
                json!([
                    "subscribe_to_feed",
                    "save_to_knowledge",
                    "remove_from_knowledge",
                    "mark_content_read",
                    "mark_content_unread",
                    "create_learning_deck"
                ]),
                json!({
                    "execute_bash": true,
                    "web_search": true,
                    "files": "read_write",
                    "app_tools": "host_managed"
                }),
                "Running contextual assistant agent",
            ),
            ChatTurnKind::DeepResearch => {
                return Err(ChatTaskRepositoryError::InvalidProcessingContext(
                    "deep research does not use the generic chat LLM ledger".to_owned(),
                ));
            }
        };
    let created = status_entry("preparing", "preparing", "LLM task created");
    let running = status_entry("running", "running", note);
    let input = json!({
        "chat_session_id": context.session.effective_session_id,
        "content_id": context.session.content_id,
        "news_item_id": context.session.news_item_id,
        "source": context.source,
        "screen_type": context.screen_context.as_ref().map(|value| value.screen_type.as_str()),
        "assistant_action": context.screen_context.as_ref().and_then(|value| value.assistant_action.as_deref()),
        "queue_task_id": queue_task_id,
        "message_id": message_id,
        "prompt_chars": context.user_prompt.chars().count(),
        "model": context.session.model,
        "stream_generation": stream_generation,
    });
    let task_id = sqlx::query_scalar::<_, i64>(
        r#"
        INSERT INTO llm_tasks (
            user_id, task_kind, mode, workflow_key, workflow_version,
            workflow_state, status, approval_policy, allowed_actions, tool_policy,
            workspace_path, prompt_pack,
            input_json, output_json, artifact_manifest, usage_json, status_history,
            model_provider, model_name, created_at, updated_at, started_at
        )
        VALUES (
            $1::bigint::integer, $2, $3, $4, 1,
            'running', 'running', $5, $6, $7,
            NULL, $8,
            $9, '{}'::jsonb, '{}'::jsonb, '{}'::jsonb,
            jsonb_build_array($10::jsonb, $11::jsonb),
            $12, $13,
            timezone('UTC', clock_timestamp()), timezone('UTC', clock_timestamp()),
            timezone('UTC', clock_timestamp())
        )
        RETURNING id::bigint
        "#,
    )
    .bind(context.session.user_id)
    .bind(task_kind)
    .bind(mode)
    .bind(workflow_key)
    .bind(json!({"default": "approval_required"}))
    .bind(allowed_actions)
    .bind(tool_policy)
    .bind(prompt_pack)
    .bind(input)
    .bind(created)
    .bind(running)
    .bind(canonical_provider(
        &context.session.model,
        &context.session.provider,
    ))
    .bind(&context.session.model)
    .fetch_one(&mut **transaction)
    .await?;
    sqlx::query(
        r#"
        UPDATE llm_tasks
        SET workspace_path = $2
        WHERE id::bigint = $1
        "#,
    )
    .bind(task_id)
    .bind(format!("/data/workspace/tasks/{task_id}"))
    .execute(&mut **transaction)
    .await?;
    Ok(task_id)
}

pub(super) async fn cancel_stale_chat_llm_tasks(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
    queue_task_id: i64,
    message_id: i64,
) -> Result<(), ChatTaskRepositoryError> {
    let entry = status_entry(
        "cancelled",
        "cancelled",
        "Chat turn attempt was superseded by a reclaimed queue lease",
    );
    sqlx::query(
        r#"
        UPDATE llm_tasks
        SET status = 'cancelled',
            workflow_state = 'cancelled',
            error_type = 'LeaseOwnershipLost',
            error_message = 'Chat turn attempt was superseded by a reclaimed queue lease',
            completed_at = timezone('UTC', clock_timestamp()),
            updated_at = timezone('UTC', clock_timestamp()),
            status_history = COALESCE(status_history, '[]'::jsonb) || jsonb_build_array($4::jsonb)
        WHERE user_id::bigint = $1
          AND task_kind IN ('article_chat', 'assistant_chat')
          AND input_json @> jsonb_build_object('queue_task_id', $2::bigint)
          AND input_json @> jsonb_build_object('message_id', $3::bigint)
          AND status NOT IN ('completed', 'failed', 'cancelled')
        "#,
    )
    .bind(user_id)
    .bind(queue_task_id)
    .bind(message_id)
    .bind(entry)
    .execute(&mut **transaction)
    .await?;
    Ok(())
}

pub(super) async fn complete_chat_llm_task(
    transaction: &mut Transaction<'_, Postgres>,
    task_id: i64,
    publication: &ChatTurnPublication<'_>,
) -> Result<(), ChatTaskRepositoryError> {
    let note = match publication.snapshot.context.kind {
        ChatTurnKind::Assistant => "Contextual assistant turn completed",
        _ => "Async article chat turn completed",
    };
    let output = json!({
        "chat_session_id": publication.snapshot.session_id,
        "message_id": publication.snapshot.message_id,
        "content_id": publication.snapshot.context.session.content_id,
        "news_item_id": publication.snapshot.context.session.news_item_id,
        "output_chars": publication.output_text.chars().count(),
        "new_message_count": publication.turn_transcript.messages.len(),
        "tool_names": publication.tool_names,
    });
    let usage = serde_json::to_value(publication.usage)?;
    let entry = status_entry("completed", "completed", note);
    sqlx::query(
        r#"
        UPDATE llm_tasks
        SET status = 'completed',
            workflow_state = 'completed',
            output_json = $2,
            usage_json = $3,
            model_provider = $4,
            model_name = $5,
            error_type = NULL,
            error_message = NULL,
            completed_at = timezone('UTC', clock_timestamp()),
            updated_at = timezone('UTC', clock_timestamp()),
            status_history = COALESCE(status_history, '[]'::jsonb) || jsonb_build_array($6::jsonb)
        WHERE id::bigint = $1
          AND user_id::bigint = $7
          AND status NOT IN ('completed', 'failed', 'cancelled')
        "#,
    )
    .bind(task_id)
    .bind(output)
    .bind(usage)
    .bind(publication.model_provider)
    .bind(publication.model_name)
    .bind(entry)
    .bind(publication.snapshot.user_id)
    .execute(&mut **transaction)
    .await?;
    Ok(())
}

pub(super) async fn fail_chat_llm_task(
    transaction: &mut Transaction<'_, Postgres>,
    task_id: i64,
    error_type: &str,
    error_message: &str,
) -> Result<(), ChatTaskRepositoryError> {
    let entry = status_entry("failed", "failed", "Async chat turn failed");
    sqlx::query(
        r#"
        UPDATE llm_tasks
        SET status = 'failed',
            workflow_state = 'failed',
            error_type = $2,
            error_message = $3,
            completed_at = timezone('UTC', clock_timestamp()),
            updated_at = timezone('UTC', clock_timestamp()),
            status_history = COALESCE(status_history, '[]'::jsonb) || jsonb_build_array($4::jsonb)
        WHERE id::bigint = $1
          AND status NOT IN ('completed', 'failed', 'cancelled')
        "#,
    )
    .bind(task_id)
    .bind(truncate_chars(error_type, 128))
    .bind(error_message)
    .bind(entry)
    .execute(&mut **transaction)
    .await?;
    Ok(())
}
