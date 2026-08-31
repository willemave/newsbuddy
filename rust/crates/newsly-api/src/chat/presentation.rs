use newsly_contracts::{
    AssistantFeedOption, ChatMessageDisplayType, ChatMessageDto, ChatMessageRole,
    ChatSessionDetailDto, ChatSessionSummaryDto, CouncilCandidate, MessageProcessingStatus,
    MessageStatusResponse,
};
use newsly_db::{
    ChatMessageProjection, ChatMessageStatusProjection, ChatSessionDetailProjection,
    ChatSessionProjection, StagedChatTurn,
};

pub(super) fn session(value: ChatSessionProjection) -> ChatSessionSummaryDto {
    ChatSessionSummaryDto {
        id: value.id,
        title: value.title,
        content_id: value.content_id,
        news_item_id: value.news_item_id,
        session_type: value.session_type,
        topic: value.topic,
        llm_model: value.llm_model,
        llm_provider: value.llm_provider,
        created_at: value.created_at,
        updated_at: value.updated_at,
        last_message_at: value.last_message_at,
        is_archived: value.is_archived,
        article_title: value.article_title,
        article_url: value.article_url,
        article_summary: value.article_summary,
        article_source: value.article_source,
        article_image_url: value.article_image_url,
        article_thumbnail_url: value.article_thumbnail_url,
        has_pending_message: value.has_pending_message,
        is_waiting_for_content: value.is_waiting_for_content,
        is_saved_to_knowledge: value.is_saved_to_knowledge,
        has_messages: value.has_messages,
        last_message_preview: value.last_message_preview,
        last_message_role: value.last_message_role,
        council_mode: value.council_mode,
        active_child_session_id: value.active_child_session_id,
    }
}

pub(super) fn detail(value: ChatSessionDetailProjection) -> Result<ChatSessionDetailDto, String> {
    Ok(ChatSessionDetailDto {
        session: session(value.session),
        messages: value
            .messages
            .into_iter()
            .map(message)
            .collect::<Result<Vec<_>, _>>()?,
    })
}

pub(super) fn message(value: ChatMessageProjection) -> Result<ChatMessageDto, String> {
    let role = role(&value.role)?;
    let display_type = display_type(&value.display_type)?;
    let status = status(&value.status)?;
    let metadata = (
        values::<AssistantFeedOption>(value.feed_options, "feed option"),
        values::<CouncilCandidate>(value.council_candidates, "council candidate"),
    );
    let (feed_options, council_candidates, active_council_child_session_id) = match metadata {
        (Ok(feed_options), Ok(council_candidates)) => (
            feed_options,
            council_candidates,
            value.active_council_child_session_id,
        ),
        (feed_options, council_candidates) => {
            tracing::warn!(
                feed_options_error = ?feed_options.err(),
                council_candidates_error = ?council_candidates.err(),
                source_message_id = value.source_message_id,
                "ignoring malformed durable chat render metadata"
            );
            (Vec::new(), Vec::new(), None)
        }
    };
    Ok(ChatMessageDto {
        id: value.id,
        source_message_id: Some(value.source_message_id),
        display_key: format!(
            "server|{}|{}|{}",
            value.source_message_id,
            role.as_str(),
            display_type.as_str()
        ),
        session_id: value.session_id,
        role,
        content: value.content,
        timestamp: value.timestamp,
        display_type,
        process_label: value.process_label,
        status,
        error: value.error,
        feed_options,
        council_candidates,
        active_council_child_session_id,
    })
}

pub(super) fn processing_user(staged: &StagedChatTurn) -> ChatMessageDto {
    ChatMessageDto {
        id: staged.message_id,
        source_message_id: Some(staged.message_id),
        display_key: format!("server|{}|user|message", staged.message_id),
        session_id: staged.visible_session_id,
        role: ChatMessageRole::User,
        content: staged.user_prompt.clone(),
        timestamp: staged.created_at,
        display_type: ChatMessageDisplayType::Message,
        process_label: None,
        status: MessageProcessingStatus::Processing,
        error: None,
        feed_options: Vec::new(),
        council_candidates: Vec::new(),
        active_council_child_session_id: None,
    }
}

pub(super) fn message_status(
    value: ChatMessageStatusProjection,
) -> Result<MessageStatusResponse, String> {
    Ok(MessageStatusResponse {
        message_id: value.message_id,
        status: status(&value.status)?,
        assistant_message: value.assistant_message.map(message).transpose()?,
        partial_assistant_message: value.partial_assistant_message.map(message).transpose()?,
        stream_generation: value.stream_generation,
        stream_revision: value.stream_revision,
        tool_progress: value
            .tool_progress
            .map(|progress| {
                serde_json::from_value(progress.value)
                    .map_err(|error| format!("invalid chat tool progress: {error}"))
            })
            .transpose()?,
        tool_progress_revision: value.tool_progress_revision,
        error: value.error,
    })
}

fn status(value: &str) -> Result<MessageProcessingStatus, String> {
    match value {
        "processing" => Ok(MessageProcessingStatus::Processing),
        "completed" => Ok(MessageProcessingStatus::Completed),
        "failed" => Ok(MessageProcessingStatus::Failed),
        other => Err(format!("invalid durable chat message status: {other}")),
    }
}

fn role(value: &str) -> Result<ChatMessageRole, String> {
    match value {
        "user" => Ok(ChatMessageRole::User),
        "assistant" => Ok(ChatMessageRole::Assistant),
        "system" => Ok(ChatMessageRole::System),
        "tool" => Ok(ChatMessageRole::Tool),
        other => Err(format!("invalid durable chat message role: {other}")),
    }
}

fn display_type(value: &str) -> Result<ChatMessageDisplayType, String> {
    match value {
        "message" => Ok(ChatMessageDisplayType::Message),
        "process_summary" => Ok(ChatMessageDisplayType::ProcessSummary),
        other => Err(format!("invalid durable chat display type: {other}")),
    }
}

fn values<T: serde::de::DeserializeOwned>(
    values: Vec<serde_json::Value>,
    label: &str,
) -> Result<Vec<T>, String> {
    values
        .into_iter()
        .map(|value| {
            serde_json::from_value(value).map_err(|error| format!("invalid {label}: {error}"))
        })
        .collect()
}
