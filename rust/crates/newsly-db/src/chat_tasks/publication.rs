//! Exact-generation terminal publication for successful and failed chat turns.

use super::{
    ChatTaskRejection, ChatTaskRepositoryError, ChatTerminalMutationOutcome, ChatTurnPublication,
    CouncilCandidateCompletion, Postgres, Transaction, Utc, finalize_council_candidate,
    finalize_failed_council_candidate,
    ledger::{complete_chat_llm_task, fail_chat_llm_task, record_chat_usage_best_effort},
};

/// Publish one completed turn inside the queue kernel's exact lease-fenced transaction.
pub async fn publish_chat_turn(
    transaction: &mut Transaction<'_, Postgres>,
    publication: &ChatTurnPublication<'_>,
) -> Result<ChatTerminalMutationOutcome, ChatTaskRepositoryError> {
    publication.turn_transcript.validate()?;
    if publication.output_text.trim().is_empty() {
        return Err(ChatTaskRepositoryError::InvalidProviderResult(
            "chat response must not be empty".to_owned(),
        ));
    }
    let snapshot = publication.snapshot;
    let row = sqlx::query_as::<_, (String, Option<i32>, i64)>(
        r#"
        SELECT status, stream_generation, session_id::bigint
        FROM chat_messages
        WHERE id::bigint = $1
        FOR UPDATE
        "#,
    )
    .bind(snapshot.message_id)
    .fetch_optional(&mut **transaction)
    .await?;
    let Some((status, generation, session_id)) = row else {
        return Ok(ChatTerminalMutationOutcome::Missing);
    };
    if session_id != snapshot.session_id {
        return Err(ChatTaskRepositoryError::OwnershipMismatch);
    }
    match status.as_str() {
        "completed" => return Ok(ChatTerminalMutationOutcome::AlreadyCompleted),
        "failed" => return Ok(ChatTerminalMutationOutcome::AlreadyFailed),
        "processing" => {}
        other => {
            return Err(ChatTaskRepositoryError::InvalidMessageStatus(
                other.to_owned(),
            ));
        }
    }
    if generation != Some(snapshot.stream_generation) {
        return Ok(ChatTerminalMutationOutcome::Superseded);
    }
    let owned_session = sqlx::query_scalar::<_, bool>(
        r#"
        SELECT EXISTS(
            SELECT 1 FROM chat_sessions AS session
            JOIN users AS account ON account.id = session.user_id AND account.is_active = TRUE
            WHERE session.id::bigint = $1 AND session.user_id::bigint = $2
        )
        "#,
    )
    .bind(snapshot.session_id)
    .bind(snapshot.user_id)
    .fetch_one(&mut **transaction)
    .await?;
    if !owned_session {
        return Ok(ChatTerminalMutationOutcome::Missing);
    }
    let message_list = serde_json::to_string(publication.turn_transcript)?;
    sqlx::query(
        r#"
        UPDATE chat_messages
        SET message_list = $3,
            render_metadata = $4,
            status = 'completed',
            error = NULL,
            partial_text = NULL,
            tool_progress = NULL,
            stream_updated_at = timezone('UTC', clock_timestamp()),
            tool_progress_updated_at = timezone('UTC', clock_timestamp())
        WHERE id::bigint = $1 AND stream_generation = $2 AND status = 'processing'
        "#,
    )
    .bind(snapshot.message_id)
    .bind(snapshot.stream_generation)
    .bind(message_list)
    .bind(publication.render_metadata)
    .execute(&mut **transaction)
    .await?;
    let now = Utc::now().naive_utc();
    sqlx::query(
        r#"
        UPDATE chat_sessions
        SET last_message_at = $2, updated_at = $2
        WHERE id::bigint = $1
        "#,
    )
    .bind(snapshot.session_id)
    .bind(now)
    .execute(&mut **transaction)
    .await?;
    if snapshot.visible_session_id != snapshot.session_id {
        sqlx::query(
            r#"
            UPDATE chat_sessions
            SET last_message_at = $2, updated_at = $2
            WHERE id::bigint = $1 AND user_id::bigint = $3
            "#,
        )
        .bind(snapshot.visible_session_id)
        .bind(now)
        .bind(snapshot.user_id)
        .execute(&mut **transaction)
        .await?;
    }
    if let Some(llm_task_id) = snapshot.llm_task_id {
        complete_chat_llm_task(transaction, llm_task_id, publication).await?;
    }
    if let Some(council_run) = snapshot.context.council_run.as_ref() {
        finalize_council_candidate(
            transaction,
            council_run,
            snapshot.user_id,
            snapshot.session_id,
            snapshot.message_id,
            CouncilCandidateCompletion::Completed(publication.output_text),
        )
        .await?;
    }
    record_chat_usage_best_effort(transaction, publication).await?;
    Ok(ChatTerminalMutationOutcome::Applied)
}

/// Publish a stable user-facing failure while the queue kernel holds the exact task lease.
pub async fn fail_chat_turn(
    transaction: &mut Transaction<'_, Postgres>,
    rejection: &ChatTaskRejection,
) -> Result<ChatTerminalMutationOutcome, ChatTaskRepositoryError> {
    let Some(message_id) = rejection.message_id else {
        if let Some(llm_task_id) = rejection.llm_task_id {
            fail_chat_llm_task(
                transaction,
                llm_task_id,
                &rejection.error_type,
                &rejection.task_message,
            )
            .await?;
        }
        return Ok(ChatTerminalMutationOutcome::Missing);
    };
    let row = sqlx::query_as::<_, (String, Option<i32>, i64)>(
        r#"
        SELECT status, stream_generation, session_id::bigint
        FROM chat_messages
        WHERE id::bigint = $1
        FOR UPDATE
        "#,
    )
    .bind(message_id)
    .fetch_optional(&mut **transaction)
    .await?;
    let Some((status, generation, session_id)) = row else {
        return Ok(ChatTerminalMutationOutcome::Missing);
    };
    if rejection
        .session_id
        .is_some_and(|expected| expected != session_id)
    {
        return Err(ChatTaskRepositoryError::OwnershipMismatch);
    }
    match status.as_str() {
        "completed" => return Ok(ChatTerminalMutationOutcome::AlreadyCompleted),
        "failed" => return Ok(ChatTerminalMutationOutcome::AlreadyFailed),
        "processing" => {}
        other => {
            return Err(ChatTaskRepositoryError::InvalidMessageStatus(
                other.to_owned(),
            ));
        }
    }
    if rejection
        .expected_stream_generation
        .is_some_and(|expected| generation != Some(expected))
    {
        return Ok(ChatTerminalMutationOutcome::Superseded);
    }
    sqlx::query(
        r#"
        UPDATE chat_messages
        SET status = 'failed',
            render_metadata = NULL,
            error = $2,
            partial_text = NULL,
            tool_progress = NULL,
            stream_updated_at = timezone('UTC', clock_timestamp()),
            tool_progress_updated_at = timezone('UTC', clock_timestamp())
        WHERE id::bigint = $1 AND status = 'processing'
        "#,
    )
    .bind(message_id)
    .bind(&rejection.public_message)
    .execute(&mut **transaction)
    .await?;
    if let Some(llm_task_id) = rejection.llm_task_id {
        fail_chat_llm_task(
            transaction,
            llm_task_id,
            &rejection.error_type,
            &rejection.task_message,
        )
        .await?;
    }
    finalize_failed_council_candidate(
        transaction,
        message_id,
        rejection.user_id,
        &rejection.public_message,
    )
    .await?;
    Ok(ChatTerminalMutationOutcome::Applied)
}
