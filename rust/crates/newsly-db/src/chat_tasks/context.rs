//! Durable history, content material, and immutable chat execution context construction.

use super::{
    CHAT_HISTORY_MAX_TOKENS, ChatContentMaterial, ChatTaskRepositoryError, ChatTurnKind,
    ChatTurnProcessingContext, ChatTurnSessionSnapshot, ContentRow, DEFAULT_MODEL,
    DEFAULT_PROVIDER, DiscussionRow, HISTORICAL_TOOL_RESULT_MAX_TOKENS, KNOWLEDGE_SESSION_TYPE,
    MessagePart, NewslyTranscript, Postgres, RequestPart, Serialize, SessionRow,
    TOKEN_CHARS_PER_TOKEN, Transaction, Utc, Value, decode_transcript, json,
    preparation::{fallback_nonempty, load_content, load_session_for_update},
};

pub(super) async fn load_history(
    transaction: &mut Transaction<'_, Postgres>,
    session_id: i64,
    excluded_message_id: i64,
    limit: i64,
) -> Result<NewslyTranscript, ChatTaskRepositoryError> {
    let rows = sqlx::query_as::<_, (i64, String)>(
        r#"
        SELECT id::bigint, message_list
        FROM chat_messages
        WHERE session_id::bigint = $1
          AND id::bigint <> $2
          AND status = 'completed'
        ORDER BY created_at DESC, id DESC
        LIMIT $3
        "#,
    )
    .bind(session_id)
    .bind(excluded_message_id)
    .bind(limit.max(1))
    .fetch_all(&mut **transaction)
    .await?;
    let mut newest_turns = Vec::new();
    let mut used_tokens = 0_usize;
    for (message_id, raw) in rows {
        match decode_transcript(&raw) {
            Ok(mut turn) => {
                bound_historical_tool_results(&mut turn);
                let turn_tokens = serialized_token_estimate(&turn.messages)?;
                if used_tokens.saturating_add(turn_tokens) > CHAT_HISTORY_MAX_TOKENS {
                    break;
                }
                used_tokens = used_tokens.saturating_add(turn_tokens);
                newest_turns.push(turn.messages);
            }
            Err(error) => tracing::warn!(
                message_id,
                error = %error,
                "skipping malformed historical chat transcript"
            ),
        }
    }
    let messages = newest_turns.into_iter().rev().flatten().collect::<Vec<_>>();
    let history = NewslyTranscript {
        messages,
        ..NewslyTranscript::default()
    };
    history.validate()?;
    Ok(history)
}

pub(super) fn bound_historical_tool_results(transcript: &mut NewslyTranscript) {
    for message in &mut transcript.messages {
        for part in &mut message.parts {
            let MessagePart::Request(RequestPart::ToolResult { content, .. }) = part else {
                continue;
            };
            let serialized = match &*content {
                Value::String(value) => value.clone(),
                value => serde_json::to_string(value).unwrap_or_else(|_| value.to_string()),
            };
            if estimate_tokens(&serialized) > HISTORICAL_TOOL_RESULT_MAX_TOKENS {
                *content = Value::String(truncate_to_token_budget(
                    &serialized,
                    HISTORICAL_TOOL_RESULT_MAX_TOKENS,
                ));
            }
        }
    }
}

pub(super) fn serialized_token_estimate<T: Serialize>(
    value: &T,
) -> Result<usize, serde_json::Error> {
    serde_json::to_string(value).map(|serialized| estimate_tokens(&serialized))
}

pub(super) fn estimate_tokens(value: &str) -> usize {
    value.chars().count().div_ceil(TOKEN_CHARS_PER_TOKEN).max(1)
}

pub(super) fn truncate_to_token_budget(value: &str, max_tokens: usize) -> String {
    let max_chars = max_tokens.saturating_mul(TOKEN_CHARS_PER_TOKEN);
    if value.chars().count() <= max_chars {
        return value.to_owned();
    }
    let mut truncated = value.chars().take(max_chars).collect::<String>();
    truncated.push_str("...");
    truncated
}

pub(super) async fn load_user_provider_key(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
    provider: &str,
) -> Result<Option<String>, sqlx::Error> {
    if !matches!(provider, "openai" | "anthropic") {
        return Ok(None);
    }
    sqlx::query_scalar::<_, String>(
        r#"
        SELECT access_token_encrypted
        FROM user_integration_connections
        WHERE user_id::bigint = $1
          AND provider = $2
          AND is_active = TRUE
          AND COALESCE(access_token_encrypted, '') <> ''
        ORDER BY id DESC
        LIMIT 1
        "#,
    )
    .bind(user_id)
    .bind(provider)
    .fetch_optional(&mut **transaction)
    .await
}

pub(super) async fn load_content_material(
    transaction: &mut Transaction<'_, Postgres>,
    content_id: i64,
) -> Result<Option<ChatContentMaterial>, sqlx::Error> {
    let row = load_content(transaction, content_id).await?;
    Ok(row.map(|row| ChatContentMaterial {
        content_id: row.id,
        content_type: row.content_type,
        url: row.url,
        title: row.title,
        source: row.source,
        fallback_body: fallback_content_body(&row.content_metadata),
        metadata: row.content_metadata,
        body_storage_key: row.body_storage_key,
    }))
}

pub(super) async fn get_or_create_dig_deeper_session(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
    content: &ContentRow,
) -> Result<SessionRow, ChatTaskRepositoryError> {
    let title = content_display_title(content);
    let existing_id = sqlx::query_scalar::<_, i64>(
        r#"
        SELECT id::bigint
        FROM chat_sessions
        WHERE content_id::bigint = $1 AND user_id::bigint = $2 AND is_archived = FALSE
        ORDER BY id
        LIMIT 1
        FOR UPDATE
        "#,
    )
    .bind(content.id)
    .bind(user_id)
    .fetch_optional(&mut **transaction)
    .await?;
    if let Some(session_id) = existing_id {
        sqlx::query(
            r#"
            UPDATE chat_sessions
            SET title = $2, updated_at = timezone('UTC', clock_timestamp())
            WHERE id::bigint = $1 AND title IS DISTINCT FROM $2
            "#,
        )
        .bind(session_id)
        .bind(&title)
        .execute(&mut **transaction)
        .await?;
        return load_session_for_update(transaction, session_id)
            .await?
            .ok_or(ChatTaskRepositoryError::SessionDisappeared);
    }
    let context_snapshot = format!(
        "Screen Type: {KNOWLEDGE_SESSION_TYPE}\nScreen Title: Knowledge\nVisible Content:\n- [{}] {} ({}) — {}\n{}\nUser ID: {user_id}",
        content.id,
        title,
        content.source.as_deref().unwrap_or("unknown"),
        content.url,
        content_short_summary(&content.content_metadata)
            .map(|summary| format!("  Short Summary: {summary}"))
            .unwrap_or_default(),
    );
    let session_id = sqlx::query_scalar::<_, i64>(
        r#"
        INSERT INTO chat_sessions (
            user_id, content_id, title, session_type, context_snapshot,
            llm_provider, llm_model, created_at, updated_at,
            council_mode, is_hidden_from_history, is_archived
        )
        VALUES (
            $1::bigint::integer, $2::bigint::integer, $3, $4, $5,
            $6, $7, timezone('UTC', clock_timestamp()), timezone('UTC', clock_timestamp()),
            FALSE, FALSE, FALSE
        )
        RETURNING id::bigint
        "#,
    )
    .bind(user_id)
    .bind(content.id)
    .bind(title)
    .bind(KNOWLEDGE_SESSION_TYPE)
    .bind(context_snapshot)
    .bind(DEFAULT_PROVIDER)
    .bind(DEFAULT_MODEL)
    .fetch_one(&mut **transaction)
    .await?;
    load_session_for_update(transaction, session_id)
        .await?
        .ok_or(ChatTaskRepositoryError::SessionDisappeared)
}

pub(super) async fn build_dig_deeper_prompt(
    transaction: &mut Transaction<'_, Postgres>,
    content: &ContentRow,
) -> Result<String, sqlx::Error> {
    let mut prompt = format!(
        "Dig deeper into the key points of {}. For each main point, explain reasoning, supporting evidence, and include a bit more detail explaining the point. Also pull out key ideas from the discussion context when available, and add more insights from the discussion, including notable agreements and disagreements. Keep answers concise and numbered.",
        content_display_title(content),
    );
    let discussion = sqlx::query_as::<_, DiscussionRow>(
        r#"
        SELECT discussion_data::jsonb AS discussion_data
        FROM content_discussions
        WHERE content_id::bigint = $1
        ORDER BY id DESC
        LIMIT 1
        "#,
    )
    .bind(content.id)
    .fetch_optional(&mut **transaction)
    .await?;
    if let Some(context) = discussion.and_then(|row| compact_discussion(&row.discussion_data)) {
        prompt.push_str("\n\n");
        prompt.push_str(&context);
    }
    Ok(prompt)
}

pub(super) fn compact_discussion(value: &Value) -> Option<String> {
    let object = value.as_object()?;
    let mut comments = Vec::new();
    if let Some(values) = object.get("compact_comments").and_then(Value::as_array) {
        for value in values.iter().filter_map(Value::as_str) {
            let snippet = compact_text(value, 220);
            if !snippet.is_empty() && !comments.contains(&snippet) {
                comments.push(snippet);
            }
            if comments.len() == 8 {
                break;
            }
        }
    }
    if comments.len() < 8
        && let Some(values) = object.get("comments").and_then(Value::as_array)
    {
        for value in values {
            let Some(comment) = value.as_object() else {
                continue;
            };
            let raw = comment
                .get("compact_text")
                .or_else(|| comment.get("text"))
                .and_then(Value::as_str);
            let Some(raw) = raw else { continue };
            let snippet = compact_text(raw, 220);
            if !snippet.is_empty() && !comments.contains(&snippet) {
                comments.push(snippet);
            }
            if comments.len() == 8 {
                break;
            }
        }
    }
    if comments.is_empty() {
        return None;
    }
    Some(format!(
        "Discussion context:\nComment highlights:\n{}",
        comments
            .into_iter()
            .map(|value| format!("- {value}"))
            .collect::<Vec<_>>()
            .join("\n")
    ))
}

pub(super) fn processing_context_for_session(
    session: &SessionRow,
    user_prompt: &str,
    source: &str,
) -> ChatTurnProcessingContext {
    ChatTurnProcessingContext {
        version: 1,
        kind: ChatTurnKind::Article,
        user_prompt: user_prompt.to_owned(),
        source: source.to_owned(),
        session: ChatTurnSessionSnapshot {
            user_id: session.user_id,
            effective_session_id: session.id,
            visible_session_id: session.id,
            model: fallback_nonempty(&session.llm_model, DEFAULT_MODEL),
            provider: fallback_nonempty(&session.llm_provider, DEFAULT_PROVIDER),
            title: session.title.clone(),
            session_type: session.session_type.clone(),
            content_id: session.content_id,
            news_item_id: session.news_item_id,
            parent_session_id: session.parent_session_id,
            topic: session.topic.clone(),
            context_snapshot: session.context_snapshot.clone(),
            is_hidden_from_history: session.is_hidden_from_history,
            council_persona_id: session.council_persona_id.clone(),
            council_persona_name: session.council_persona_name.clone(),
            council_persona_prompt: session.council_persona_prompt.clone(),
        },
        screen_context: None,
        council_run: None,
    }
}

pub(super) fn lifecycle_is_valid(
    session: &SessionRow,
    context: &ChatTurnProcessingContext,
) -> bool {
    if session.is_archived {
        return false;
    }
    let snapshot = &context.session;
    if context.kind == ChatTurnKind::Council {
        session.is_hidden_from_history
            && session.parent_session_id == Some(snapshot.visible_session_id)
            && snapshot.parent_session_id == Some(snapshot.visible_session_id)
    } else {
        !session.is_hidden_from_history
    }
}

pub(super) fn content_display_title(content: &ContentRow) -> String {
    let summary = content.content_metadata.get("summary");
    let title = summary
        .and_then(Value::as_object)
        .and_then(|value| value.get("title"))
        .and_then(Value::as_str)
        .or(content.title.as_deref())
        .map(str::trim)
        .filter(|value| !value.is_empty());
    title
        .map(|value| truncate_chars(value, 500))
        .or_else(|| content_short_summary(&content.content_metadata))
        .map_or_else(
            || "this content".to_owned(),
            |value| truncate_chars(&value, 120),
        )
}

pub(super) fn content_short_summary(metadata: &Value) -> Option<String> {
    match metadata.get("summary") {
        Some(Value::String(value)) => clean_owned(value),
        Some(Value::Object(summary)) => ["one_line", "overview", "summary", "hook", "takeaway"]
            .into_iter()
            .find_map(|field| {
                summary
                    .get(field)
                    .and_then(Value::as_str)
                    .and_then(clean_owned)
            }),
        _ => None,
    }
}

pub(super) fn fallback_content_body(metadata: &Value) -> Option<String> {
    ["transcript", "content_to_summarize", "content"]
        .into_iter()
        .find_map(|field| {
            metadata
                .get(field)
                .and_then(Value::as_str)
                .and_then(clean_owned)
        })
        .or_else(|| {
            metadata
                .get("summary")
                .and_then(Value::as_object)
                .and_then(|summary| summary.get("full_markdown"))
                .and_then(Value::as_str)
                .and_then(clean_owned)
        })
}

pub(super) fn clean_owned(value: &str) -> Option<String> {
    let value = value.trim();
    (!value.is_empty()).then(|| value.to_owned())
}

pub(super) fn compact_text(value: &str, maximum: usize) -> String {
    let compact = value.split_whitespace().collect::<Vec<_>>().join(" ");
    if compact.chars().count() <= maximum {
        compact
    } else {
        format!(
            "{}...",
            truncate_chars(&compact, maximum.saturating_sub(3)).trim_end()
        )
    }
}

pub(super) fn truncate_chars(value: &str, maximum: usize) -> String {
    value.chars().take(maximum).collect()
}

pub(super) fn status_entry(status: &str, workflow_state: &str, note: &str) -> Value {
    json!({
        "status": status,
        "workflow_state": workflow_state,
        "note": note,
        "created_at": Utc::now().naive_utc().format("%Y-%m-%dT%H:%M:%S%.6f").to_string(),
    })
}

pub(super) fn i64_bound(value: u64) -> i64 {
    i64::try_from(value).unwrap_or(i64::MAX)
}
