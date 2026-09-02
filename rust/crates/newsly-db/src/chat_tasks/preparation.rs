//! Short transactional preparation for queued chat and dig-deeper work.

use super::{
    CHAT_FAILURE_MESSAGE, CHAT_UNAVAILABLE_MESSAGE, ChatAdvisoryWriteOutcome,
    ChatTaskPreparationOutcome, ChatTaskRejection, ChatTaskRepositoryError, ChatTaskSnapshot,
    ChatTurnKind, ChatTurnProcessingContext, ContentRow, DIG_DEEPER_FAILURE_MESSAGE, Map,
    MessageRow, ORDER_RETRY_DELAY_SECONDS, Postgres, PrepareChatTask, QueueTaskRow,
    QueuedChatTaskKind, SessionRow, Transaction, Utc, Value,
    advisory::initialize_stream_attempt,
    context::{
        build_dig_deeper_prompt, get_or_create_dig_deeper_session, lifecycle_is_valid,
        load_content_material, load_history, load_user_provider_key,
        processing_context_for_session,
    },
    ledger::{cancel_stale_chat_llm_tasks, create_chat_llm_task},
    processing_transcript,
};

/// Prepare one `chat_turn` or `dig_deeper` task without crossing an external-I/O boundary.
///
/// The caller must commit before decrypting a credential, reading object storage, invoking a
/// provider, or acquiring an E2B VM. `processing_tasks.payload` is updated atomically with the
/// first dig-deeper message, making retries idempotent.
pub async fn prepare_chat_task(
    transaction: &mut Transaction<'_, Postgres>,
    request: &PrepareChatTask<'_>,
) -> Result<ChatTaskPreparationOutcome, ChatTaskRepositoryError> {
    validate_prepare_request(request)?;
    let queue_task = load_queue_task_for_update(transaction, request.queue_task_id)
        .await?
        .ok_or(ChatTaskRepositoryError::QueueTaskNotFound)?;
    validate_queue_task(request, &queue_task)?;

    match request.queue_task_kind {
        QueuedChatTaskKind::ChatTurn => prepare_existing_chat_turn(transaction, request).await,
        QueuedChatTaskKind::DigDeeper => {
            prepare_dig_deeper_turn(transaction, request, &queue_task).await
        }
    }
}

pub(super) async fn prepare_existing_chat_turn(
    transaction: &mut Transaction<'_, Postgres>,
    request: &PrepareChatTask<'_>,
) -> Result<ChatTaskPreparationOutcome, ChatTaskRepositoryError> {
    let user_id = payload_positive_id(request.payload, "user_id")?;
    let session_id = payload_positive_id(request.payload, "session_id")?;
    let message_id = payload_positive_id(request.payload, "message_id")?;
    if user_id != request.owner_user_id {
        return Err(ChatTaskRepositoryError::OwnershipMismatch);
    }
    prepare_linked_turn(transaction, request, user_id, session_id, message_id, None).await
}

pub(super) async fn prepare_dig_deeper_turn(
    transaction: &mut Transaction<'_, Postgres>,
    request: &PrepareChatTask<'_>,
    queue_task: &QueueTaskRow,
) -> Result<ChatTaskPreparationOutcome, ChatTaskRepositoryError> {
    let user_id = payload_positive_id(request.payload, "user_id")?;
    if user_id != request.owner_user_id {
        return Err(ChatTaskRepositoryError::OwnershipMismatch);
    }
    let content_id = request
        .content_id
        .or(queue_task.content_id)
        .filter(|value| *value > 0)
        .ok_or(ChatTaskRepositoryError::InvalidPayload(
            "dig_deeper content_id must be positive".to_owned(),
        ))?;
    if queue_task.content_id != Some(content_id) {
        return Err(ChatTaskRepositoryError::OwnershipMismatch);
    }
    let Some(content) = load_content_for_update(transaction, content_id).await? else {
        return Ok(ChatTaskPreparationOutcome::Reject(ChatTaskRejection {
            message_id: None,
            session_id: None,
            user_id,
            llm_task_id: None,
            expected_stream_generation: None,
            public_message: DIG_DEEPER_FAILURE_MESSAGE.to_owned(),
            task_message: "Content not found".to_owned(),
            error_type: "ContentNotFound".to_owned(),
        }));
    };
    if !lock_active_user(transaction, user_id).await? {
        return Ok(ChatTaskPreparationOutcome::SkippedInactiveUser);
    }

    let link_values = [
        request.payload.get("session_id"),
        request.payload.get("message_id"),
        request.payload.get("prompt"),
    ];
    let linked_count = link_values.iter().filter(|value| value.is_some()).count();
    let (session_id, message_id, prompt) = if linked_count > 0 {
        if linked_count != link_values.len() {
            return Err(ChatTaskRepositoryError::InvalidPayload(
                "dig_deeper persisted message link must include session_id, message_id, and prompt"
                    .to_owned(),
            ));
        }
        let session_id = payload_positive_id(request.payload, "session_id")?;
        let message_id = payload_positive_id(request.payload, "message_id")?;
        let prompt = payload_clean_text(request.payload, "prompt")?;
        let valid_session = sqlx::query_scalar::<_, bool>(
            r#"
            SELECT EXISTS(
                SELECT 1 FROM chat_sessions
                WHERE id::bigint = $1 AND user_id::bigint = $2 AND content_id::bigint = $3
            )
            "#,
        )
        .bind(session_id)
        .bind(user_id)
        .bind(content_id)
        .fetch_one(&mut **transaction)
        .await?;
        if !valid_session {
            return Err(ChatTaskRepositoryError::InvalidPersistedLink(
                "dig_deeper session link no longer resolves".to_owned(),
            ));
        }
        (session_id, message_id, prompt)
    } else {
        let session = get_or_create_dig_deeper_session(transaction, user_id, &content).await?;
        let prompt = request
            .payload
            .get("initial_message")
            .and_then(Value::as_str)
            .map(str::trim)
            .filter(|value| !value.is_empty())
            .map(str::to_owned)
            .unwrap_or(build_dig_deeper_prompt(transaction, &content).await?);
        validate_bounded_text(&prompt, 1, 10_000, "dig_deeper prompt")?;
        let context = processing_context_for_session(&session, &prompt, "queue");
        let transcript = processing_transcript(&prompt, Utc::now());
        let message_list = serde_json::to_string(&transcript)?;
        let processing_context = serde_json::to_value(&context)?;
        let message_id = sqlx::query_scalar::<_, i64>(
            r#"
            INSERT INTO chat_messages (
                session_id, message_list, processing_context, created_at, status
            )
            VALUES (
                $1::bigint::integer, $2, $3, timezone('UTC', clock_timestamp()), 'processing'
            )
            RETURNING id::bigint
            "#,
        )
        .bind(session.id)
        .bind(message_list)
        .bind(processing_context)
        .fetch_one(&mut **transaction)
        .await?;
        sqlx::query(
            r#"
            UPDATE processing_tasks
            SET payload = COALESCE(payload::jsonb, '{}'::jsonb) || jsonb_build_object(
                    'session_id', $2::bigint,
                    'message_id', $3::bigint,
                    'prompt', $4::text
                )
            WHERE id::bigint = $1
            "#,
        )
        .bind(request.queue_task_id)
        .bind(session.id)
        .bind(message_id)
        .bind(&prompt)
        .execute(&mut **transaction)
        .await?;
        (session.id, message_id, prompt)
    };

    prepare_linked_turn(
        transaction,
        request,
        user_id,
        session_id,
        message_id,
        Some((&prompt, content_id)),
    )
    .await
}

pub(super) async fn prepare_linked_turn(
    transaction: &mut Transaction<'_, Postgres>,
    request: &PrepareChatTask<'_>,
    user_id: i64,
    session_id: i64,
    message_id: i64,
    expected_dig_deeper: Option<(&str, i64)>,
) -> Result<ChatTaskPreparationOutcome, ChatTaskRepositoryError> {
    let Some(message) = load_message_for_update(transaction, message_id).await? else {
        return Ok(reject(
            user_id,
            None,
            Some(session_id),
            None,
            None,
            CHAT_UNAVAILABLE_MESSAGE,
            "Chat message not found",
            "MessageNotFound",
        ));
    };
    if message.session_id != session_id {
        return Ok(reject(
            user_id,
            Some(message_id),
            Some(session_id),
            None,
            None,
            CHAT_UNAVAILABLE_MESSAGE,
            "Chat task message/session mismatch",
            "MessageSessionMismatch",
        ));
    }
    match message.status.as_str() {
        "completed" => return Ok(ChatTaskPreparationOutcome::Completed),
        "failed" => {
            return Ok(ChatTaskPreparationOutcome::AlreadyFailed {
                message: message
                    .error
                    .unwrap_or_else(|| CHAT_FAILURE_MESSAGE.to_owned()),
            });
        }
        "processing" => {}
        status => {
            return Err(ChatTaskRepositoryError::InvalidMessageStatus(
                status.to_owned(),
            ));
        }
    }

    let Some(session) = load_session_for_update(transaction, session_id).await? else {
        return Ok(reject(
            user_id,
            Some(message_id),
            Some(session_id),
            None,
            None,
            CHAT_UNAVAILABLE_MESSAGE,
            "Chat session not found",
            "SessionNotFound",
        ));
    };
    if session.user_id != user_id || !lock_active_user(transaction, user_id).await? {
        return Ok(reject(
            user_id,
            Some(message_id),
            Some(session_id),
            None,
            None,
            CHAT_UNAVAILABLE_MESSAGE,
            "Chat turn ownership validation failed",
            "OwnershipMismatch",
        ));
    }

    let context_value = message.processing_context.as_ref().ok_or_else(|| {
        ChatTaskRepositoryError::InvalidProcessingContext(
            "chat message has no processing_context".to_owned(),
        )
    });
    let context = match context_value.and_then(|value| {
        serde_json::from_value::<ChatTurnProcessingContext>(value.clone())
            .map_err(ChatTaskRepositoryError::Json)
    }) {
        Ok(context) => {
            if let Err(error) = context.validate() {
                return Ok(reject(
                    user_id,
                    Some(message_id),
                    Some(session_id),
                    None,
                    None,
                    CHAT_UNAVAILABLE_MESSAGE,
                    &error.to_string(),
                    "InvalidProcessingContext",
                ));
            }
            context
        }
        Err(error) => {
            return Ok(reject(
                user_id,
                Some(message_id),
                Some(session_id),
                None,
                None,
                CHAT_UNAVAILABLE_MESSAGE,
                &error.to_string(),
                "InvalidProcessingContext",
            ));
        }
    };
    if context.session.user_id != user_id
        || context.session.effective_session_id != session_id
        || !lifecycle_is_valid(&session, &context)
    {
        return Ok(reject(
            user_id,
            Some(message_id),
            Some(session_id),
            None,
            None,
            CHAT_UNAVAILABLE_MESSAGE,
            "Chat turn lifecycle validation failed",
            "LifecycleInvalid",
        ));
    }
    if let Some((expected_prompt, content_id)) = expected_dig_deeper
        && (context.kind != ChatTurnKind::Article
            || context.user_prompt != expected_prompt
            || context.session.visible_session_id != session_id
            || context.session.content_id != Some(content_id))
    {
        return Err(ChatTaskRepositoryError::InvalidPersistedLink(
            "dig_deeper message context does not match its persisted link".to_owned(),
        ));
    }
    if request.stream_generation > request.max_retries {
        let public_message = if request.queue_task_kind == QueuedChatTaskKind::DigDeeper {
            DIG_DEEPER_FAILURE_MESSAGE
        } else {
            CHAT_FAILURE_MESSAGE
        };
        return Ok(reject(
            user_id,
            Some(message_id),
            Some(session_id),
            None,
            Some(request.stream_generation),
            public_message,
            "Chat turn stopped after repeated worker interruptions",
            "LeaseReclaimBudgetExhausted",
        ));
    }

    let earlier_exists = sqlx::query_scalar::<_, bool>(
        r#"
        SELECT EXISTS(
            SELECT 1 FROM chat_messages
            WHERE session_id::bigint = $1
              AND id::bigint < $2
              AND status = 'processing'
        )
        "#,
    )
    .bind(session_id)
    .bind(message_id)
    .fetch_one(&mut **transaction)
    .await?;
    if earlier_exists {
        return Ok(ChatTaskPreparationOutcome::Deferred {
            retry_delay_seconds: ORDER_RETRY_DELAY_SECONDS,
        });
    }

    match initialize_stream_attempt(transaction, &message, request.stream_generation).await? {
        ChatAdvisoryWriteOutcome::Superseded => {
            return Ok(ChatTaskPreparationOutcome::Superseded);
        }
        ChatAdvisoryWriteOutcome::Terminal | ChatAdvisoryWriteOutcome::Missing => {
            return Ok(ChatTaskPreparationOutcome::Completed);
        }
        ChatAdvisoryWriteOutcome::Applied | ChatAdvisoryWriteOutcome::Unchanged => {}
    }

    let llm_task_id = match context.kind {
        ChatTurnKind::DeepResearch => None,
        ChatTurnKind::Article | ChatTurnKind::Council | ChatTurnKind::Assistant => {
            cancel_stale_chat_llm_tasks(transaction, user_id, request.queue_task_id, message_id)
                .await?;
            Some(
                create_chat_llm_task(
                    transaction,
                    request.queue_task_id,
                    message_id,
                    request.stream_generation,
                    &context,
                )
                .await?,
            )
        }
    };
    let history = load_history(
        transaction,
        session_id,
        message_id,
        request.history_message_limit,
    )
    .await?;
    let provider = canonical_provider(&context.session.model, &context.session.provider);
    let encrypted_provider_key = load_user_provider_key(transaction, user_id, provider).await?;
    let content = match context.session.content_id {
        Some(content_id) => load_content_material(transaction, content_id).await?,
        None => None,
    };
    Ok(ChatTaskPreparationOutcome::Ready(ChatTaskSnapshot {
        queue_task_id: request.queue_task_id,
        user_id,
        session_id,
        visible_session_id: context.session.visible_session_id,
        message_id,
        stream_generation: request.stream_generation,
        context,
        history,
        llm_task_id,
        deep_research_response_id: message.deep_research_response_id,
        encrypted_provider_key,
        content,
        workspace_path: llm_task_id.map_or_else(
            || format!("/data/workspace/chat/{session_id}"),
            |task_id| format!("/data/workspace/tasks/{task_id}"),
        ),
    }))
}

pub(super) async fn load_queue_task_for_update(
    transaction: &mut Transaction<'_, Postgres>,
    task_id: i64,
) -> Result<Option<QueueTaskRow>, sqlx::Error> {
    sqlx::query_as::<_, QueueTaskRow>(
        r#"
        SELECT
            id::bigint AS id,
            task_type,
            content_id::bigint AS content_id,
            owner_user_id::bigint AS owner_user_id,
            payload::jsonb AS payload,
            status
        FROM processing_tasks
        WHERE id::bigint = $1
        FOR UPDATE
        "#,
    )
    .bind(task_id)
    .fetch_optional(&mut **transaction)
    .await
}

pub(super) async fn load_session_for_update(
    transaction: &mut Transaction<'_, Postgres>,
    session_id: i64,
) -> Result<Option<SessionRow>, sqlx::Error> {
    sqlx::query_as::<_, SessionRow>(
        r#"
        SELECT
            id::bigint AS id,
            user_id::bigint AS user_id,
            content_id::bigint AS content_id,
            news_item_id::bigint AS news_item_id,
            parent_session_id::bigint AS parent_session_id,
            title,
            session_type,
            topic,
            context_snapshot,
            council_persona_id,
            council_persona_name,
            council_persona_prompt,
            is_hidden_from_history,
            llm_model,
            llm_provider,
            is_archived
        FROM chat_sessions
        WHERE id::bigint = $1
        FOR UPDATE
        "#,
    )
    .bind(session_id)
    .fetch_optional(&mut **transaction)
    .await
}

pub(super) async fn load_message_for_update(
    transaction: &mut Transaction<'_, Postgres>,
    message_id: i64,
) -> Result<Option<MessageRow>, sqlx::Error> {
    sqlx::query_as::<_, MessageRow>(
        r#"
        SELECT
            id::bigint AS id,
            session_id::bigint AS session_id,
            processing_context::jsonb AS processing_context,
            status,
            error,
            stream_generation,
            deep_research_response_id
        FROM chat_messages
        WHERE id::bigint = $1
        FOR UPDATE
        "#,
    )
    .bind(message_id)
    .fetch_optional(&mut **transaction)
    .await
}

pub(super) async fn load_content_for_update(
    transaction: &mut Transaction<'_, Postgres>,
    content_id: i64,
) -> Result<Option<ContentRow>, sqlx::Error> {
    load_content(transaction, content_id).await
}

pub(super) async fn load_content(
    transaction: &mut Transaction<'_, Postgres>,
    content_id: i64,
) -> Result<Option<ContentRow>, sqlx::Error> {
    sqlx::query_as::<_, ContentRow>(
        r#"
        SELECT
            content.id::bigint AS id,
            content.content_type,
            content.url,
            content.title,
            content.source,
            content.content_metadata::jsonb AS content_metadata,
            source_body.storage_key AS body_storage_key
        FROM contents AS content
        LEFT JOIN content_bodies AS source_body
          ON source_body.content_id = content.id AND source_body.variant = 'source'
        WHERE content.id::bigint = $1
        FOR UPDATE OF content
        "#,
    )
    .bind(content_id)
    .fetch_optional(&mut **transaction)
    .await
}

pub(super) async fn lock_active_user(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
) -> Result<bool, sqlx::Error> {
    Ok(sqlx::query_scalar::<_, i64>(
        "SELECT id::bigint FROM users WHERE id::bigint = $1 AND is_active = TRUE FOR SHARE",
    )
    .bind(user_id)
    .fetch_optional(&mut **transaction)
    .await?
    .is_some())
}

pub(super) fn validate_prepare_request(
    request: &PrepareChatTask<'_>,
) -> Result<(), ChatTaskRepositoryError> {
    validate_positive(request.queue_task_id, "queue_task_id")?;
    validate_positive(request.owner_user_id, "owner_user_id")?;
    if request.stream_generation < 0 || request.max_retries < 0 {
        return Err(ChatTaskRepositoryError::InvalidPayload(
            "stream_generation and max_retries must be nonnegative".to_owned(),
        ));
    }
    if !(2..=200).contains(&request.history_message_limit) {
        return Err(ChatTaskRepositoryError::InvalidPayload(
            "history_message_limit must be in 2..=200".to_owned(),
        ));
    }
    Ok(())
}

pub(super) fn validate_queue_task(
    request: &PrepareChatTask<'_>,
    task: &QueueTaskRow,
) -> Result<(), ChatTaskRepositoryError> {
    if task.id != request.queue_task_id
        || task.task_type != request.queue_task_kind.as_str()
        || task.owner_user_id != Some(request.owner_user_id)
        || task.status != "processing"
    {
        return Err(ChatTaskRepositoryError::OwnershipMismatch);
    }
    let durable_payload = task.payload.as_object().ok_or_else(|| {
        ChatTaskRepositoryError::InvalidPayload("queue task payload must be an object".to_owned())
    })?;
    let durable_user = durable_payload.get("user_id").and_then(Value::as_i64);
    if durable_user != Some(request.owner_user_id) || durable_payload != request.payload {
        return Err(ChatTaskRepositoryError::OwnershipMismatch);
    }
    Ok(())
}

pub(super) fn reject(
    user_id: i64,
    message_id: Option<i64>,
    session_id: Option<i64>,
    llm_task_id: Option<i64>,
    expected_stream_generation: Option<i32>,
    public_message: &str,
    task_message: &str,
    error_type: &str,
) -> ChatTaskPreparationOutcome {
    ChatTaskPreparationOutcome::Reject(ChatTaskRejection {
        message_id,
        session_id,
        user_id,
        llm_task_id,
        expected_stream_generation,
        public_message: public_message.to_owned(),
        task_message: task_message.to_owned(),
        error_type: error_type.to_owned(),
    })
}

pub(super) fn payload_positive_id(
    payload: &Map<String, Value>,
    field: &'static str,
) -> Result<i64, ChatTaskRepositoryError> {
    payload
        .get(field)
        .and_then(Value::as_i64)
        .filter(|value| *value > 0)
        .ok_or_else(|| {
            ChatTaskRepositoryError::InvalidPayload(format!("{field} must be a positive integer"))
        })
}

pub(super) fn payload_clean_text(
    payload: &Map<String, Value>,
    field: &'static str,
) -> Result<String, ChatTaskRepositoryError> {
    payload
        .get(field)
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(str::to_owned)
        .ok_or_else(|| {
            ChatTaskRepositoryError::InvalidPayload(format!("{field} must not be blank"))
        })
}

pub(super) fn validate_positive(value: i64, field: &str) -> Result<(), ChatTaskRepositoryError> {
    if value > 0 {
        Ok(())
    } else {
        Err(ChatTaskRepositoryError::InvalidProcessingContext(format!(
            "{field} must be positive"
        )))
    }
}

pub(super) fn validate_bounded_text(
    value: &str,
    minimum: usize,
    maximum: usize,
    field: &str,
) -> Result<(), ChatTaskRepositoryError> {
    let count = value.chars().count();
    if (minimum..=maximum).contains(&count) {
        Ok(())
    } else {
        Err(ChatTaskRepositoryError::InvalidProcessingContext(format!(
            "{field} must contain {minimum}..={maximum} characters"
        )))
    }
}

pub(super) fn validate_optional_text(
    value: Option<&str>,
    maximum: usize,
    field: &str,
) -> Result<(), ChatTaskRepositoryError> {
    value.map_or(Ok(()), |value| {
        validate_bounded_text(value, 0, maximum, field)
    })
}

pub(super) fn canonical_provider<'a>(model: &'a str, fallback: &'a str) -> &'a str {
    model
        .split_once(':')
        .map(|(provider, _)| provider)
        .filter(|provider| {
            matches!(
                *provider,
                "openai" | "anthropic" | "google" | "google-gla" | "openrouter"
            )
        })
        .map_or(fallback, |provider| {
            if provider == "google-gla" {
                "google"
            } else {
                provider
            }
        })
}

pub(super) fn fallback_nonempty(value: &str, fallback: &str) -> String {
    if value.trim().is_empty() {
        fallback.to_owned()
    } else {
        value.to_owned()
    }
}
