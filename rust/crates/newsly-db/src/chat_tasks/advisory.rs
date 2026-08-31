//! Generation-fenced partial text, tool progress, and resumable response identities.

use super::{
    ChatAdvisoryWriteOutcome, ChatTaskRepositoryError, ChatToolProgress, MessageRow, Postgres,
    Transaction, Utc, context::truncate_chars, json, preparation::validate_bounded_text,
};

/// Persist one cumulative final-response snapshot for the exact processing generation.
pub async fn write_chat_partial(
    transaction: &mut Transaction<'_, Postgres>,
    message_id: i64,
    stream_generation: i32,
    text: &str,
) -> Result<ChatAdvisoryWriteOutcome, ChatTaskRepositoryError> {
    if text.trim().is_empty() {
        return Ok(ChatAdvisoryWriteOutcome::Unchanged);
    }
    let row = sqlx::query_as::<_, (String, Option<i32>, Option<String>)>(
        r#"
        SELECT status, stream_generation, partial_text
        FROM chat_messages
        WHERE id::bigint = $1
        FOR UPDATE
        "#,
    )
    .bind(message_id)
    .fetch_optional(&mut **transaction)
    .await?;
    let Some((status, current_generation, current_text)) = row else {
        return Ok(ChatAdvisoryWriteOutcome::Missing);
    };
    if status != "processing" {
        return Ok(ChatAdvisoryWriteOutcome::Terminal);
    }
    if current_generation != Some(stream_generation) {
        return Ok(ChatAdvisoryWriteOutcome::Superseded);
    }
    if current_text.as_deref() == Some(text) {
        return Ok(ChatAdvisoryWriteOutcome::Unchanged);
    }
    sqlx::query(
        r#"
        UPDATE chat_messages
        SET partial_text = $3,
            stream_revision = COALESCE(stream_revision, 0) + 1,
            stream_updated_at = timezone('UTC', clock_timestamp())
        WHERE id::bigint = $1 AND stream_generation = $2 AND status = 'processing'
        "#,
    )
    .bind(message_id)
    .bind(stream_generation)
    .bind(text)
    .execute(&mut **transaction)
    .await?;
    Ok(ChatAdvisoryWriteOutcome::Applied)
}

/// Persist tool progress independently from user-visible partial response text.
pub async fn write_chat_tool_progress(
    transaction: &mut Transaction<'_, Postgres>,
    message_id: i64,
    stream_generation: i32,
    progress: &ChatToolProgress<'_>,
) -> Result<ChatAdvisoryWriteOutcome, ChatTaskRepositoryError> {
    validate_bounded_text(progress.tool_name, 1, 128, "tool_progress.tool_name")?;
    validate_bounded_text(progress.status, 1, 32, "tool_progress.status")?;
    let row = sqlx::query_as::<_, (String, Option<i32>)>(
        r#"
        SELECT status, stream_generation
        FROM chat_messages
        WHERE id::bigint = $1
        FOR UPDATE
        "#,
    )
    .bind(message_id)
    .fetch_optional(&mut **transaction)
    .await?;
    let Some((status, current_generation)) = row else {
        return Ok(ChatAdvisoryWriteOutcome::Missing);
    };
    if status != "processing" {
        return Ok(ChatAdvisoryWriteOutcome::Terminal);
    }
    if current_generation != Some(stream_generation) {
        return Ok(ChatAdvisoryWriteOutcome::Superseded);
    }
    let detail = progress.detail.map(|value| truncate_chars(value, 2_000));
    let value = json!({
        "tool_name": progress.tool_name,
        "status": progress.status,
        "detail": detail,
        "updated_at": Utc::now().to_rfc3339(),
    });
    sqlx::query(
        r#"
        UPDATE chat_messages
        SET tool_progress = $3,
            tool_progress_revision = COALESCE(tool_progress_revision, 0) + 1,
            tool_progress_updated_at = timezone('UTC', clock_timestamp())
        WHERE id::bigint = $1 AND stream_generation = $2 AND status = 'processing'
        "#,
    )
    .bind(message_id)
    .bind(stream_generation)
    .bind(value)
    .execute(&mut **transaction)
    .await?;
    Ok(ChatAdvisoryWriteOutcome::Applied)
}

/// Compare-and-set a newly created OpenAI background response identity before polling.
pub async fn persist_deep_research_response_id(
    transaction: &mut Transaction<'_, Postgres>,
    message_id: i64,
    stream_generation: i32,
    response_id: &str,
) -> Result<Option<String>, ChatTaskRepositoryError> {
    validate_bounded_text(response_id, 1, 255, "deep_research_response_id")?;
    let row = sqlx::query_as::<_, (String, Option<i32>, Option<String>)>(
        r#"
        SELECT status, stream_generation, deep_research_response_id
        FROM chat_messages
        WHERE id::bigint = $1
        FOR UPDATE
        "#,
    )
    .bind(message_id)
    .fetch_optional(&mut **transaction)
    .await?;
    let Some((status, generation, existing)) = row else {
        return Ok(None);
    };
    if status != "processing" || generation != Some(stream_generation) {
        return Ok(None);
    }
    if let Some(existing) = existing {
        return Ok(Some(existing));
    }
    sqlx::query(
        r#"
        UPDATE chat_messages
        SET deep_research_response_id = $3
        WHERE id::bigint = $1 AND stream_generation = $2 AND status = 'processing'
        "#,
    )
    .bind(message_id)
    .bind(stream_generation)
    .bind(response_id)
    .execute(&mut **transaction)
    .await?;
    Ok(Some(response_id.to_owned()))
}

pub(super) async fn initialize_stream_attempt(
    transaction: &mut Transaction<'_, Postgres>,
    message: &MessageRow,
    stream_generation: i32,
) -> Result<ChatAdvisoryWriteOutcome, ChatTaskRepositoryError> {
    if message.status != "processing" {
        return Ok(ChatAdvisoryWriteOutcome::Terminal);
    }
    if message
        .stream_generation
        .is_some_and(|current| current > stream_generation)
    {
        return Ok(ChatAdvisoryWriteOutcome::Superseded);
    }
    if message.stream_generation == Some(stream_generation) {
        return Ok(ChatAdvisoryWriteOutcome::Unchanged);
    }
    sqlx::query(
        r#"
        UPDATE chat_messages
        SET partial_text = NULL,
            stream_generation = $2,
            stream_revision = 0,
            stream_updated_at = NULL,
            tool_progress = NULL,
            tool_progress_revision = 0,
            tool_progress_updated_at = NULL
        WHERE id::bigint = $1 AND status = 'processing'
        "#,
    )
    .bind(message.id)
    .bind(stream_generation)
    .execute(&mut **transaction)
    .await?;
    Ok(ChatAdvisoryWriteOutcome::Applied)
}
