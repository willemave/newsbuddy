use std::collections::BTreeSet;

use chrono::{DateTime, NaiveDateTime, Utc};
use serde_json::{Value, json};
use sqlx::{AssertSqlSafe, FromRow, PgPool, Postgres, Transaction};
use thiserror::Error;

use crate::chat_transcripts::{
    ChatTranscriptError, DisplayMessageProjection, RenderMetadataProjection, StoredChatMessage,
    display_messages, latest_assistant_text, latest_message_preview, overlay_subscription_state,
    processing_transcript,
};

const DEFAULT_PROVIDER: &str = "openai";
const DEFAULT_MODEL: &str = "openai:gpt-5.6-terra";
const KNOWLEDGE_SESSION_TYPE: &str = "knowledge_chat";

#[derive(Debug, Clone, PartialEq)]
pub struct ChatSessionProjection {
    pub id: i64,
    pub title: Option<String>,
    pub content_id: Option<i64>,
    pub news_item_id: Option<i64>,
    pub session_type: Option<String>,
    pub topic: Option<String>,
    pub llm_model: String,
    pub llm_provider: String,
    pub created_at: DateTime<Utc>,
    pub updated_at: Option<DateTime<Utc>>,
    pub last_message_at: Option<DateTime<Utc>>,
    pub is_archived: bool,
    pub article_title: Option<String>,
    pub article_url: Option<String>,
    pub article_summary: Option<String>,
    pub article_source: Option<String>,
    pub article_image_url: Option<String>,
    pub article_thumbnail_url: Option<String>,
    pub has_pending_message: bool,
    pub is_waiting_for_content: bool,
    pub is_saved_to_knowledge: bool,
    pub has_messages: bool,
    pub last_message_preview: Option<String>,
    pub last_message_role: Option<String>,
    pub council_mode: bool,
    pub active_child_session_id: Option<i64>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct ChatSessionDetailProjection {
    pub session: ChatSessionProjection,
    pub messages: Vec<ChatMessageProjection>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct ChatMessageProjection {
    pub id: i64,
    pub source_message_id: i64,
    pub session_id: i64,
    pub role: String,
    pub content: String,
    pub timestamp: DateTime<Utc>,
    pub display_type: String,
    pub process_label: Option<String>,
    pub status: String,
    pub error: Option<String>,
    pub feed_options: Vec<Value>,
    pub council_candidates: Vec<Value>,
    pub active_council_child_session_id: Option<i64>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct ChatToolProgressProjection {
    pub value: Value,
}

#[derive(Debug, Clone, PartialEq)]
pub struct ChatMessageStatusProjection {
    pub message_id: i64,
    pub status: String,
    pub assistant_message: Option<ChatMessageProjection>,
    pub partial_assistant_message: Option<ChatMessageProjection>,
    pub stream_generation: Option<i32>,
    pub stream_revision: Option<i32>,
    pub tool_progress: Option<ChatToolProgressProjection>,
    pub tool_progress_revision: Option<i32>,
    pub error: Option<String>,
}

#[derive(Debug, Clone, PartialEq)]
pub enum ChatRecordAccess<T> {
    Found(T),
    NotFound,
    Forbidden,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ChatListCursor {
    pub last_id: i64,
    pub last_activity_at: DateTime<Utc>,
}

#[derive(Debug, Clone)]
pub struct CreateChatSessionInput<'a> {
    pub user_id: i64,
    pub content_id: Option<i64>,
    pub news_item_id: Option<i64>,
    pub topic: Option<&'a str>,
    pub initial_message: Option<&'a str>,
    pub session_type: &'a str,
    pub llm_provider: &'a str,
    pub llm_model: &'a str,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CreateChatSessionOutcome {
    Created { session_id: i64 },
    ContentNotFound,
    NewsItemNotFound,
    UserInactive,
}

#[derive(Debug, Clone)]
pub struct UpdateChatSessionInput<'a> {
    pub user_id: i64,
    pub session_id: i64,
    pub llm_provider: Option<&'a str>,
    pub llm_model: Option<&'a str>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ChatMutationOutcome {
    Applied,
    NotFound,
    Forbidden,
}

#[derive(Debug, Clone)]
pub struct StageChatMessageInput<'a> {
    pub user_id: i64,
    pub session_id: i64,
    pub user_prompt: &'a str,
}

#[derive(Debug, Clone)]
pub struct StageAssistantTurnInput<'a> {
    pub user_id: i64,
    pub session_id: Option<i64>,
    pub user_prompt: &'a str,
    pub screen_context: &'a Value,
}

#[derive(Debug, Clone, PartialEq)]
pub struct StagedChatTurn {
    pub visible_session_id: i64,
    pub effective_session_id: i64,
    pub message_id: i64,
    pub created_at: DateTime<Utc>,
    pub user_prompt: String,
    pub processing_context: Value,
}

#[derive(Debug, Clone, PartialEq)]
pub enum StageChatTurnOutcome {
    Staged(StagedChatTurn),
    NotFound,
    Forbidden,
    Archived,
    Hidden,
    NoActiveCouncilBranch,
    NewsItemNotFound,
    UserInactive,
}

#[derive(Debug, Clone, FromRow)]
struct ChatSessionRow {
    id: i64,
    user_id: i64,
    content_id: Option<i64>,
    news_item_id: Option<i64>,
    parent_session_id: Option<i64>,
    title: Option<String>,
    session_type: Option<String>,
    topic: Option<String>,
    context_snapshot: Option<String>,
    council_persona_id: Option<String>,
    council_persona_name: Option<String>,
    council_persona_prompt: Option<String>,
    council_mode: bool,
    active_child_session_id: Option<i64>,
    branch_start_message_id: Option<i64>,
    is_hidden_from_history: bool,
    llm_model: String,
    llm_provider: String,
    created_at: NaiveDateTime,
    updated_at: Option<NaiveDateTime>,
    last_message_at: Option<NaiveDateTime>,
    is_archived: bool,
}

#[derive(Debug, Clone, FromRow)]
struct ContentPresentationRow {
    url: String,
    title: Option<String>,
    source: Option<String>,
    content_metadata: Value,
}

#[derive(Debug, Clone, FromRow)]
struct NewsPresentationRow {
    platform: Option<String>,
    source_type: Option<String>,
    source_label: Option<String>,
    canonical_story_url: Option<String>,
    article_url: Option<String>,
    article_domain: Option<String>,
    discussion_url: Option<String>,
    summary_key_points: Value,
    summary_text: Option<String>,
    raw_metadata: Value,
}

#[derive(Debug, Clone, FromRow)]
struct ChatMessageRow {
    id: i64,
    session_id: i64,
    message_list: String,
    render_metadata: Option<Value>,
    created_at: NaiveDateTime,
    status: String,
    error: Option<String>,
    partial_text: Option<String>,
    stream_generation: Option<i32>,
    stream_revision: Option<i32>,
    stream_updated_at: Option<DateTime<Utc>>,
    tool_progress: Option<Value>,
    tool_progress_revision: Option<i32>,
}

impl From<&ChatMessageRow> for StoredChatMessage {
    fn from(row: &ChatMessageRow) -> Self {
        Self {
            id: row.id,
            session_id: row.session_id,
            message_list: row.message_list.clone(),
            render_metadata: row.render_metadata.clone(),
            created_at: row.created_at,
            status: row.status.clone(),
            error: row.error.clone(),
        }
    }
}

#[derive(Debug, Clone, Default)]
struct ArticlePresentation {
    title: Option<String>,
    url: Option<String>,
    summary: Option<String>,
    source: Option<String>,
    image_url: Option<String>,
    thumbnail_url: Option<String>,
}

/// List visible, unarchived chat sessions using the Python-compatible activity cursor.
///
/// # Errors
///
/// Returns [`ChatRepositoryError`] when PostgreSQL cannot load the list or one durable transcript
/// cannot be represented.
pub async fn list_chat_sessions(
    pool: &PgPool,
    user_id: i64,
    content_id: Option<i64>,
    news_item_id: Option<i64>,
    cursor: Option<ChatListCursor>,
    limit: i64,
) -> Result<Vec<ChatSessionProjection>, ChatRepositoryError> {
    let cursor_activity = cursor.map(|value| value.last_activity_at.naive_utc());
    let cursor_id = cursor.map(|value| value.last_id);
    let rows = sqlx::query_as::<_, ChatSessionRow>(AssertSqlSafe(format!(
        "{}\n{}",
        SESSION_PROJECTION_SQL,
        r"
        WHERE user_id::bigint = $1
          AND is_archived = FALSE
          AND is_hidden_from_history = FALSE
          AND ($2::bigint IS NULL OR content_id::bigint = $2)
          AND ($3::bigint IS NULL OR news_item_id::bigint = $3)
          AND (
                $4::timestamp IS NULL
                OR COALESCE(last_message_at, created_at) < $4
                OR (
                    COALESCE(last_message_at, created_at) = $4
                    AND id::bigint < $5
                )
          )
        ORDER BY COALESCE(last_message_at, created_at) DESC, id DESC
        LIMIT $6
        "
    )))
    .bind(user_id)
    .bind(content_id)
    .bind(news_item_id)
    .bind(cursor_activity)
    .bind(cursor_id)
    .bind(limit)
    .fetch_all(pool)
    .await?;

    let mut projections = Vec::with_capacity(rows.len());
    for row in rows {
        projections.push(present_session(pool, user_id, row).await?);
    }
    Ok(projections)
}

/// Load one chat-session summary while preserving missing-versus-forbidden semantics.
///
/// # Errors
///
/// Returns [`ChatRepositoryError`] for PostgreSQL or durable transcript failures.
pub async fn get_chat_session_summary(
    pool: &PgPool,
    user_id: i64,
    session_id: i64,
) -> Result<ChatRecordAccess<ChatSessionProjection>, ChatRepositoryError> {
    let Some(row) = load_session(pool, session_id).await? else {
        return Ok(ChatRecordAccess::NotFound);
    };
    if row.user_id != user_id {
        return Ok(ChatRecordAccess::Forbidden);
    }
    Ok(ChatRecordAccess::Found(
        present_session(pool, user_id, row).await?,
    ))
}

/// Load one owned chat session and its display transcript.
///
/// # Errors
///
/// Returns [`ChatRepositoryError`] for PostgreSQL or durable transcript failures.
pub async fn get_chat_session_detail(
    pool: &PgPool,
    user_id: i64,
    session_id: i64,
) -> Result<ChatRecordAccess<ChatSessionDetailProjection>, ChatRepositoryError> {
    let Some(row) = load_session(pool, session_id).await? else {
        return Ok(ChatRecordAccess::NotFound);
    };
    if row.user_id != user_id {
        return Ok(ChatRecordAccess::Forbidden);
    }

    let active_feed_urls = load_active_feed_urls(pool, user_id).await?;
    let parent_rows = load_message_rows(pool, session_id, None).await?;
    let parent_stored = parent_rows
        .iter()
        .map(StoredChatMessage::from)
        .collect::<Vec<_>>();
    let mut messages = display_messages(&parent_stored, session_id, &active_feed_urls)
        .into_iter()
        .map(Into::into)
        .collect::<Vec<_>>();

    if row.council_mode
        && let Some(active_child_id) = row.active_child_session_id
        && let Some(child) = load_session(pool, active_child_id).await?
        && child.parent_session_id == Some(row.id)
        && child.is_hidden_from_history
    {
        let child_rows = load_message_rows(pool, child.id, child.branch_start_message_id).await?;
        let child_stored = child_rows
            .iter()
            .map(StoredChatMessage::from)
            .collect::<Vec<_>>();
        messages.extend(
            display_messages(&child_stored, session_id, &active_feed_urls)
                .into_iter()
                .map(Into::into),
        );
    }

    let session = present_session(pool, user_id, row).await?;
    Ok(ChatRecordAccess::Found(ChatSessionDetailProjection {
        session,
        messages,
    }))
}

impl From<DisplayMessageProjection> for ChatMessageProjection {
    fn from(value: DisplayMessageProjection) -> Self {
        Self {
            id: value.id,
            source_message_id: value.source_message_id,
            session_id: value.session_id,
            role: value.role.to_owned(),
            content: value.content,
            timestamp: value.timestamp,
            display_type: value.display_type.to_owned(),
            process_label: value.process_label,
            status: value.status,
            error: value.error,
            feed_options: value.feed_options,
            council_candidates: value.council_candidates,
            active_council_child_session_id: value.active_council_child_session_id,
        }
    }
}

/// Insert a chat session without retaining any provider or filesystem resource.
///
/// # Errors
///
/// Returns [`ChatRepositoryError`] when PostgreSQL or context serialization fails.
pub async fn create_chat_session(
    transaction: &mut Transaction<'_, Postgres>,
    input: &CreateChatSessionInput<'_>,
) -> Result<CreateChatSessionOutcome, ChatRepositoryError> {
    if !lock_active_user(transaction, input.user_id).await? {
        return Ok(CreateChatSessionOutcome::UserInactive);
    }

    let content = if let Some(content_id) = input.content_id {
        let content = load_content_in_transaction(transaction, content_id).await?;
        if content.is_none() {
            return Ok(CreateChatSessionOutcome::ContentNotFound);
        }
        content
    } else {
        None
    };
    let news = if let Some(news_item_id) = input.news_item_id {
        let news =
            load_visible_news_in_transaction(transaction, input.user_id, news_item_id).await?;
        if news.is_none() {
            return Ok(CreateChatSessionOutcome::NewsItemNotFound);
        }
        news
    } else {
        None
    };

    let article_title = content
        .as_ref()
        .map(content_title)
        .or_else(|| news.as_ref().map(news_title));
    let title = create_session_title(article_title.as_deref(), input.topic, input.initial_message);
    let context_snapshot = if input.session_type == KNOWLEDGE_SESSION_TYPE
        || input.content_id.is_some()
        || input.news_item_id.is_some()
    {
        let context = json!({
            "screen_type": KNOWLEDGE_SESSION_TYPE,
            "screen_title": "Knowledge",
            "content_id": input.content_id,
            "news_item_id": input.news_item_id,
            "visible_content_ids": [],
            "visible_news_item_ids": [],
            "selected_topic": input.topic,
            "note": input.initial_message.map(|value| truncate_chars(value, 500)),
        });
        Some(build_screen_context_snapshot(transaction, input.user_id, &context).await?)
    } else {
        None
    };

    let session_id = sqlx::query_scalar::<_, i64>(
        r"
        INSERT INTO chat_sessions (
            user_id, content_id, news_item_id, title, session_type, topic,
            context_snapshot, council_mode, is_hidden_from_history,
            llm_model, llm_provider, created_at, updated_at, is_archived
        )
        VALUES (
            $1::bigint::integer, $2::bigint::integer, $3::bigint::integer, $4, $5, $6,
            $7, FALSE, FALSE, $8, $9,
            timezone('UTC', clock_timestamp()), timezone('UTC', clock_timestamp()), FALSE
        )
        RETURNING id::bigint
        ",
    )
    .bind(input.user_id)
    .bind(input.content_id)
    .bind(input.news_item_id)
    .bind(title)
    .bind(input.session_type)
    .bind(input.topic)
    .bind(context_snapshot)
    .bind(input.llm_model)
    .bind(input.llm_provider)
    .fetch_one(&mut **transaction)
    .await?;

    Ok(CreateChatSessionOutcome::Created { session_id })
}

/// Update an owned session model selection.
///
/// # Errors
///
/// Returns [`ChatRepositoryError`] when PostgreSQL cannot lock or update the session.
pub async fn update_chat_session(
    transaction: &mut Transaction<'_, Postgres>,
    input: &UpdateChatSessionInput<'_>,
) -> Result<ChatMutationOutcome, ChatRepositoryError> {
    let Some(row) = load_session_for_update(transaction, input.session_id).await? else {
        return Ok(ChatMutationOutcome::NotFound);
    };
    if row.user_id != input.user_id {
        return Ok(ChatMutationOutcome::Forbidden);
    }
    if let (Some(provider), Some(model)) = (input.llm_provider, input.llm_model) {
        sqlx::query(
            r"
            UPDATE chat_sessions
            SET llm_provider = $2,
                llm_model = $3,
                updated_at = timezone('UTC', clock_timestamp())
            WHERE id::bigint = $1
            ",
        )
        .bind(input.session_id)
        .bind(provider)
        .bind(model)
        .execute(&mut **transaction)
        .await?;
    }
    Ok(ChatMutationOutcome::Applied)
}

/// Archive an owned session and every hidden council child atomically.
///
/// # Errors
///
/// Returns [`ChatRepositoryError`] when PostgreSQL cannot lock or archive the session.
pub async fn archive_chat_session(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
    session_id: i64,
) -> Result<ChatMutationOutcome, ChatRepositoryError> {
    let Some(row) = load_session_for_update(transaction, session_id).await? else {
        return Ok(ChatMutationOutcome::NotFound);
    };
    if row.user_id != user_id {
        return Ok(ChatMutationOutcome::Forbidden);
    }
    if !row.is_archived {
        sqlx::query(
            r"
            UPDATE chat_sessions
            SET is_archived = TRUE,
                updated_at = timezone('UTC', clock_timestamp())
            WHERE id::bigint = $1
               OR ($2 AND parent_session_id::bigint = $1)
            ",
        )
        .bind(session_id)
        .bind(row.council_mode)
        .execute(&mut **transaction)
        .await?;
    }
    Ok(ChatMutationOutcome::Applied)
}

/// Stage an immutable user turn and its processing row inside the caller's queue transaction.
///
/// # Errors
///
/// Returns [`ChatRepositoryError`] when PostgreSQL or Newsly transcript serialization fails.
pub async fn stage_chat_message(
    transaction: &mut Transaction<'_, Postgres>,
    input: &StageChatMessageInput<'_>,
) -> Result<StageChatTurnOutcome, ChatRepositoryError> {
    let Some(parent) = load_session_for_update(transaction, input.session_id).await? else {
        return Ok(StageChatTurnOutcome::NotFound);
    };
    if parent.user_id != input.user_id {
        return Ok(StageChatTurnOutcome::Forbidden);
    }
    if parent.is_archived {
        return Ok(StageChatTurnOutcome::Archived);
    }
    if parent.is_hidden_from_history {
        return Ok(StageChatTurnOutcome::Hidden);
    }

    let (effective, kind, source, screen_context) = if parent.council_mode {
        let Some(active_child_id) = parent.active_child_session_id else {
            return Ok(StageChatTurnOutcome::NoActiveCouncilBranch);
        };
        let Some(child) = load_session_for_update(transaction, active_child_id).await? else {
            return Ok(StageChatTurnOutcome::NoActiveCouncilBranch);
        };
        if child.parent_session_id != Some(parent.id) || !child.is_hidden_from_history {
            return Ok(StageChatTurnOutcome::NoActiveCouncilBranch);
        }
        (child, "council", "council", None)
    } else if parent.session_type.as_deref() == Some("deep_research") {
        (parent.clone(), "deep_research", "realtime", None)
    } else if is_assistant_session_type(parent.session_type.as_deref()) {
        let context = json!({
            "screen_type": parent.session_type.as_deref().unwrap_or("unknown"),
            "screen_title": parent.title,
            "content_id": parent.content_id,
            "news_item_id": parent.news_item_id,
            "visible_content_ids": [],
            "visible_news_item_ids": [],
            "selected_topic": Value::Null,
            "query": Value::Null,
            "note": Value::Null,
            "assistant_action": Value::Null,
        });
        (parent.clone(), "assistant", "assistant", Some(context))
    } else {
        (parent.clone(), "article", "realtime", None)
    };

    let staged = stage_turn_for_sessions(
        transaction,
        &parent,
        &effective,
        input.user_prompt,
        kind,
        source,
        screen_context.as_ref(),
    )
    .await?;
    Ok(StageChatTurnOutcome::Staged(staged))
}

/// Create or refresh an assistant session, then stage the queued turn in the same transaction.
///
/// # Errors
///
/// Returns [`ChatRepositoryError`] when PostgreSQL or Newsly transcript serialization fails.
pub async fn stage_assistant_turn(
    transaction: &mut Transaction<'_, Postgres>,
    input: &StageAssistantTurnInput<'_>,
) -> Result<StageChatTurnOutcome, ChatRepositoryError> {
    let primary_news_id = json_positive_i64(input.screen_context, "news_item_id");
    let primary_news = if let Some(news_item_id) = primary_news_id {
        let item =
            load_visible_news_in_transaction(transaction, input.user_id, news_item_id).await?;
        if item.is_none() {
            return Ok(StageChatTurnOutcome::NewsItemNotFound);
        }
        item
    } else {
        None
    };

    let context_snapshot =
        build_screen_context_snapshot(transaction, input.user_id, input.screen_context).await?;
    let content_id = json_positive_i64(input.screen_context, "content_id");
    let selected_topic = json_string(input.screen_context, "selected_topic");
    let screen_title = json_string(input.screen_context, "screen_title");

    let session = if let Some(session_id) = input.session_id {
        let Some(mut row) = load_session_for_update(transaction, session_id).await? else {
            return Ok(StageChatTurnOutcome::NotFound);
        };
        if row.user_id != input.user_id {
            return Ok(StageChatTurnOutcome::Forbidden);
        }
        if row.is_archived {
            return Ok(StageChatTurnOutcome::Archived);
        }
        if row.is_hidden_from_history {
            return Ok(StageChatTurnOutcome::Hidden);
        }

        let mut title = screen_title
            .clone()
            .or_else(|| row.title.clone())
            .unwrap_or_else(|| "Knowledge Chat".to_owned());
        if let Some(content_id) = content_id
            && let Some(content) = load_content_in_transaction(transaction, content_id).await?
        {
            title = content_title(&content);
        } else if let Some(news) = &primary_news {
            title = news_title(news);
        }
        title = truncate_chars(&title, 500);

        sqlx::query(
            r"
            UPDATE chat_sessions
            SET context_snapshot = $2,
                content_id = $3::bigint::integer,
                news_item_id = $4::bigint::integer,
                topic = $5,
                title = $6,
                updated_at = timezone('UTC', clock_timestamp())
            WHERE id::bigint = $1
            ",
        )
        .bind(row.id)
        .bind(&context_snapshot)
        .bind(content_id)
        .bind(primary_news_id)
        .bind(&selected_topic)
        .bind(&title)
        .execute(&mut **transaction)
        .await?;
        row.context_snapshot = Some(context_snapshot);
        row.content_id = content_id;
        row.news_item_id = primary_news_id;
        row.topic = selected_topic;
        row.title = Some(title);
        row.updated_at = Some(Utc::now().naive_utc());
        row
    } else {
        if !lock_active_user(transaction, input.user_id).await? {
            return Ok(StageChatTurnOutcome::UserInactive);
        }
        let mut title = screen_title.unwrap_or_else(|| "Knowledge Chat".to_owned());
        if let Some(content_id) = content_id
            && let Some(content) = load_content_in_transaction(transaction, content_id).await?
        {
            title = content.title.unwrap_or(title);
        } else if let Some(news) = &primary_news {
            title = news_title(news);
        } else if let Some(derived) = derive_chat_session_title(input.user_prompt) {
            title = derived;
        } else if let Some(topic) = &selected_topic {
            title.clone_from(topic);
        }
        title = truncate_chars(&title, 500);

        let session_id = sqlx::query_scalar::<_, i64>(
            r"
            INSERT INTO chat_sessions (
                user_id, content_id, news_item_id, title, session_type, topic,
                context_snapshot, council_mode, is_hidden_from_history,
                llm_model, llm_provider, created_at, updated_at, is_archived
            )
            VALUES (
                $1::bigint::integer, $2::bigint::integer, $3::bigint::integer, $4,
                $5, $6, $7, FALSE, FALSE, $8, $9,
                timezone('UTC', clock_timestamp()), timezone('UTC', clock_timestamp()), FALSE
            )
            RETURNING id::bigint
            ",
        )
        .bind(input.user_id)
        .bind(content_id)
        .bind(primary_news_id)
        .bind(&title)
        .bind(KNOWLEDGE_SESSION_TYPE)
        .bind(&selected_topic)
        .bind(&context_snapshot)
        .bind(DEFAULT_MODEL)
        .bind(DEFAULT_PROVIDER)
        .fetch_one(&mut **transaction)
        .await?;
        load_session_for_update(transaction, session_id)
            .await?
            .ok_or(ChatRepositoryError::InsertedSessionMissing)?
    };

    let staged = stage_turn_for_sessions(
        transaction,
        &session,
        &session,
        input.user_prompt,
        "assistant",
        "assistant",
        Some(input.screen_context),
    )
    .await?;
    Ok(StageChatTurnOutcome::Staged(staged))
}

/// Load the current retry-fenced status of a user-visible async message.
///
/// # Errors
///
/// Returns [`ChatRepositoryError`] for PostgreSQL or malformed terminal transcript state.
pub async fn get_chat_message_status(
    pool: &PgPool,
    user_id: i64,
    message_id: i64,
) -> Result<ChatRecordAccess<ChatMessageStatusProjection>, ChatRepositoryError> {
    let row = sqlx::query_as::<_, ChatMessageRow>(MESSAGE_PROJECTION_BY_ID_SQL)
        .bind(message_id)
        .fetch_optional(pool)
        .await?;
    let Some(row) = row else {
        return Ok(ChatRecordAccess::NotFound);
    };
    let session = load_session(pool, row.session_id).await?;
    let Some(session) = session else {
        return Ok(ChatRecordAccess::Forbidden);
    };
    if session.user_id != user_id {
        return Ok(ChatRecordAccess::Forbidden);
    }
    let visible_session_id = session.parent_session_id.unwrap_or(session.id);

    let projection = match row.status.as_str() {
        "processing" => {
            let partial_assistant_message = row.partial_text.as_ref().and_then(|partial| {
                (!partial.trim().is_empty()).then(|| ChatMessageProjection {
                    id: 1_000_000_000_i64.saturating_add(message_id),
                    source_message_id: message_id,
                    session_id: visible_session_id,
                    role: "assistant".to_owned(),
                    content: partial.clone(),
                    timestamp: row
                        .stream_updated_at
                        .unwrap_or_else(|| row.created_at.and_utc()),
                    display_type: "message".to_owned(),
                    process_label: None,
                    status: "processing".to_owned(),
                    error: None,
                    feed_options: Vec::new(),
                    council_candidates: Vec::new(),
                    active_council_child_session_id: None,
                })
            });
            ChatMessageStatusProjection {
                message_id,
                status: row.status,
                assistant_message: None,
                partial_assistant_message,
                stream_generation: row.stream_generation,
                stream_revision: row.stream_revision,
                tool_progress: row
                    .tool_progress
                    .map(|value| ChatToolProgressProjection { value }),
                tool_progress_revision: row.tool_progress_revision,
                error: None,
            }
        }
        "failed" => ChatMessageStatusProjection {
            message_id,
            status: row.status,
            assistant_message: None,
            partial_assistant_message: None,
            stream_generation: None,
            stream_revision: None,
            tool_progress: None,
            tool_progress_revision: None,
            error: row.error,
        },
        "completed" => {
            let assistant_content = latest_assistant_text(&row.message_list)?;
            let metadata = RenderMetadataProjection::from_value(row.render_metadata.as_ref());
            let active_feed_urls = load_active_feed_urls(pool, user_id).await?;
            let assistant_message = ChatMessageProjection {
                id: 1_000_000_000_i64.saturating_add(message_id),
                source_message_id: message_id,
                session_id: visible_session_id,
                role: "assistant".to_owned(),
                content: assistant_content,
                timestamp: row.created_at.and_utc(),
                display_type: "message".to_owned(),
                process_label: None,
                status: "completed".to_owned(),
                error: None,
                feed_options: overlay_subscription_state(metadata.feed_options, &active_feed_urls),
                council_candidates: metadata.council_candidates,
                active_council_child_session_id: metadata.active_council_child_session_id,
            };
            ChatMessageStatusProjection {
                message_id,
                status: row.status,
                assistant_message: Some(assistant_message),
                partial_assistant_message: None,
                stream_generation: None,
                stream_revision: None,
                tool_progress: None,
                tool_progress_revision: None,
                error: None,
            }
        }
        status => return Err(ChatRepositoryError::InvalidMessageStatus(status.to_owned())),
    };
    Ok(ChatRecordAccess::Found(projection))
}

async fn stage_turn_for_sessions(
    transaction: &mut Transaction<'_, Postgres>,
    visible: &ChatSessionRow,
    effective: &ChatSessionRow,
    user_prompt: &str,
    kind: &str,
    source: &str,
    screen_context: Option<&Value>,
) -> Result<StagedChatTurn, ChatRepositoryError> {
    let now = Utc::now();
    let transcript = processing_transcript(user_prompt, now);
    let message_list = serde_json::to_string(&transcript)?;
    let processing_context = json!({
        "version": 1,
        "kind": kind,
        "user_prompt": user_prompt,
        "source": source,
        "session": {
            "user_id": effective.user_id,
            "effective_session_id": effective.id,
            "visible_session_id": visible.id,
            "model": effective.llm_model,
            "provider": effective.llm_provider,
            "title": effective.title,
            "session_type": effective.session_type,
            "content_id": effective.content_id,
            "news_item_id": effective.news_item_id,
            "parent_session_id": effective.parent_session_id,
            "topic": effective.topic,
            "context_snapshot": effective.context_snapshot,
            "is_hidden_from_history": effective.is_hidden_from_history,
            "council_persona_id": effective.council_persona_id,
            "council_persona_name": effective.council_persona_name,
            "council_persona_prompt": effective.council_persona_prompt,
        },
        "screen_context": screen_context,
    });

    let inserted = sqlx::query_as::<_, InsertedMessageRow>(
        r"
        INSERT INTO chat_messages (
            session_id, message_list, processing_context, created_at, status
        )
        VALUES (
            $1::bigint::integer, $2, $3,
            timezone('UTC', clock_timestamp()), 'processing'
        )
        RETURNING id::bigint AS id, created_at
        ",
    )
    .bind(effective.id)
    .bind(message_list)
    .bind(&processing_context)
    .fetch_one(&mut **transaction)
    .await?;

    sqlx::query(
        r"
        UPDATE chat_sessions
        SET last_message_at = $2,
            updated_at = $2
        WHERE id::bigint = $1
        ",
    )
    .bind(effective.id)
    .bind(inserted.created_at)
    .execute(&mut **transaction)
    .await?;
    if visible.id != effective.id {
        sqlx::query(
            r"
            UPDATE chat_sessions
            SET last_message_at = $2,
                updated_at = $2
            WHERE id::bigint = $1
            ",
        )
        .bind(visible.id)
        .bind(inserted.created_at)
        .execute(&mut **transaction)
        .await?;
    }

    Ok(StagedChatTurn {
        visible_session_id: visible.id,
        effective_session_id: effective.id,
        message_id: inserted.id,
        created_at: inserted.created_at.and_utc(),
        user_prompt: user_prompt.to_owned(),
        processing_context,
    })
}

#[derive(Debug, Clone, FromRow)]
struct InsertedMessageRow {
    id: i64,
    created_at: NaiveDateTime,
}

async fn present_session(
    pool: &PgPool,
    user_id: i64,
    row: ChatSessionRow,
) -> Result<ChatSessionProjection, ChatRepositoryError> {
    let preview_session_id = if row.council_mode {
        match row.active_child_session_id {
            Some(child_id) => load_session(pool, child_id)
                .await?
                .filter(|child| {
                    child.parent_session_id == Some(row.id) && child.is_hidden_from_history
                })
                .map_or(row.id, |child| child.id),
            None => row.id,
        }
    } else {
        row.id
    };

    let has_pending_message = sqlx::query_scalar::<_, bool>(
        r"
        SELECT EXISTS(
            SELECT 1 FROM chat_messages
            WHERE session_id::bigint = $1 AND status = 'processing'
        )
        ",
    )
    .bind(preview_session_id)
    .fetch_one(pool)
    .await?;
    let has_messages = sqlx::query_scalar::<_, bool>(
        "SELECT EXISTS(SELECT 1 FROM chat_messages WHERE session_id::bigint = $1)",
    )
    .bind(row.id)
    .fetch_one(pool)
    .await?;
    let last_message = sqlx::query_as::<_, ChatMessageRow>(AssertSqlSafe(format!(
        "{MESSAGE_PROJECTION_SQL} WHERE session_id::bigint = $1 ORDER BY id DESC LIMIT 1"
    )))
    .bind(preview_session_id)
    .fetch_optional(pool)
    .await?;
    let (last_message_preview, last_message_role) = last_message
        .as_ref()
        .and_then(|message| latest_message_preview(&message.message_list))
        .map_or((None, None), |(preview, role)| {
            (Some(preview), Some(role.to_owned()))
        });

    let is_saved_to_knowledge = if let Some(content_id) = row.content_id {
        sqlx::query_scalar::<_, bool>(
            r"
            SELECT EXISTS(
                SELECT 1 FROM content_knowledge_saves
                WHERE user_id::bigint = $1 AND content_id::bigint = $2
            )
            ",
        )
        .bind(user_id)
        .bind(content_id)
        .fetch_one(pool)
        .await?
    } else {
        false
    };
    let is_waiting_for_content =
        if !has_messages && row.session_type.as_deref() == Some(KNOWLEDGE_SESSION_TYPE) {
            if let Some(content_id) = row.content_id {
                content_awaiting_first_chat_turn(pool, user_id, content_id).await?
            } else {
                false
            }
        } else {
            false
        };
    let article = article_presentation(pool, &row).await?;

    Ok(ChatSessionProjection {
        id: row.id,
        title: row.title,
        content_id: row.content_id,
        news_item_id: row.news_item_id,
        session_type: row.session_type,
        topic: row.topic,
        llm_model: if row.llm_model.is_empty() {
            DEFAULT_MODEL.to_owned()
        } else {
            row.llm_model
        },
        llm_provider: if row.llm_provider.is_empty() {
            DEFAULT_PROVIDER.to_owned()
        } else {
            row.llm_provider
        },
        created_at: row.created_at.and_utc(),
        updated_at: row.updated_at.map(|value| value.and_utc()),
        last_message_at: row.last_message_at.map(|value| value.and_utc()),
        is_archived: row.is_archived,
        article_title: article.title,
        article_url: article.url,
        article_summary: article.summary,
        article_source: article.source,
        article_image_url: article.image_url,
        article_thumbnail_url: article.thumbnail_url,
        has_pending_message,
        is_waiting_for_content,
        is_saved_to_knowledge,
        has_messages,
        last_message_preview,
        last_message_role,
        council_mode: row.council_mode,
        active_child_session_id: row.active_child_session_id,
    })
}

async fn article_presentation(
    pool: &PgPool,
    session: &ChatSessionRow,
) -> Result<ArticlePresentation, ChatRepositoryError> {
    if let Some(content_id) = session.content_id {
        if let Some(content) = load_content(pool, content_id).await? {
            return Ok(content_presentation(&content));
        }
    } else if let Some(news_item_id) = session.news_item_id
        && let Some(news) = load_news(pool, news_item_id).await?
    {
        return Ok(news_presentation(&news));
    }
    Ok(ArticlePresentation::default())
}

fn content_presentation(content: &ContentPresentationRow) -> ArticlePresentation {
    let metadata = content
        .content_metadata
        .as_object()
        .cloned()
        .unwrap_or_default();
    ArticlePresentation {
        title: Some(content_title(content)),
        url: Some(content.url.clone()),
        summary: extract_short_summary(metadata.get("summary")),
        source: content.source.clone(),
        image_url: metadata
            .get("image_url")
            .and_then(Value::as_str)
            .map(str::to_owned),
        thumbnail_url: metadata
            .get("thumbnail_url")
            .and_then(Value::as_str)
            .map(str::to_owned),
    }
}

fn news_presentation(news: &NewsPresentationRow) -> ArticlePresentation {
    ArticlePresentation {
        title: Some(news_title(news)),
        url: news
            .article_url
            .clone()
            .or_else(|| news.canonical_story_url.clone()),
        summary: news.summary_text.clone(),
        source: first_non_empty([
            news.source_label.as_deref(),
            news.article_domain.as_deref(),
            news.platform.as_deref(),
            news.source_type.as_deref(),
        ]),
        image_url: None,
        thumbnail_url: None,
    }
}

fn content_title(content: &ContentPresentationRow) -> String {
    let metadata = content.content_metadata.as_object();
    let summary = metadata
        .and_then(|value| value.get("summary"))
        .and_then(Value::as_object);
    let title = summary
        .and_then(|value| value.get("title"))
        .and_then(Value::as_str)
        .or_else(|| {
            summary
                .and_then(|value| value.get("feed_preview"))
                .and_then(Value::as_object)
                .and_then(|value| value.get("title"))
                .and_then(Value::as_str)
        })
        .or(content.title.as_deref())
        .filter(|value| !value.trim().is_empty());
    title
        .map(|value| truncate_chars(value.trim(), 500))
        .or_else(|| extract_short_summary(metadata.and_then(|value| value.get("summary"))))
        .map_or_else(|| "Untitled".to_owned(), |value| summarize_as_title(&value))
}

fn news_title(news: &NewsPresentationRow) -> String {
    let metadata = news.raw_metadata.as_object();
    let summary_title = metadata
        .and_then(|value| value.get("summary"))
        .and_then(Value::as_object)
        .and_then(|value| value.get("title"))
        .and_then(Value::as_str);
    let related_title = metadata
        .and_then(|value| value.get("cluster"))
        .and_then(Value::as_object)
        .and_then(|value| value.get("related_titles"))
        .and_then(Value::as_array)
        .and_then(|values| values.first())
        .and_then(Value::as_str);
    let article_title = metadata
        .and_then(|value| value.get("article"))
        .and_then(Value::as_object)
        .and_then(|value| value.get("title"))
        .and_then(Value::as_str);
    [summary_title, related_title, article_title]
        .into_iter()
        .flatten()
        .find(|value| !value.trim().is_empty())
        .map(|value| truncate_chars(value.trim(), 500))
        .or_else(|| news.summary_text.as_deref().map(summarize_as_title))
        .unwrap_or_else(|| "Untitled News Item".to_owned())
}

fn extract_short_summary(value: Option<&Value>) -> Option<String> {
    match value {
        Some(Value::String(value)) if !value.is_empty() => Some(value.clone()),
        Some(Value::Object(summary)) => {
            for field in ["one_line", "overview", "summary", "hook", "takeaway"] {
                if let Some(value) = summary.get(field).and_then(Value::as_str)
                    && !value.is_empty()
                {
                    return Some(value.to_owned());
                }
            }
            summary
                .get("artifact")
                .and_then(Value::as_object)
                .and_then(|value| value.get("payload"))
                .and_then(Value::as_object)
                .and_then(|value| value.get("overview"))
                .and_then(Value::as_str)
                .filter(|value| !value.is_empty())
                .map(str::to_owned)
        }
        _ => None,
    }
}

async fn build_screen_context_snapshot(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
    context: &Value,
) -> Result<String, ChatRepositoryError> {
    let screen_type = json_string(context, "screen_type").unwrap_or_else(|| "unknown".to_owned());
    let mut lines = vec![format!("Screen Type: {screen_type}")];
    append_context_line(
        &mut lines,
        "Screen Title",
        json_string(context, "screen_title"),
    );
    append_context_line(
        &mut lines,
        "Selected Topic",
        json_string(context, "selected_topic"),
    );
    append_context_line(&mut lines, "Query", json_string(context, "query"));
    append_context_line(&mut lines, "Client Note", json_string(context, "note"));
    append_context_line(
        &mut lines,
        "Assistant Action",
        json_string(context, "assistant_action"),
    );

    let mut content_ids = Vec::new();
    push_unique_positive(&mut content_ids, json_positive_i64(context, "content_id"));
    for id in json_i64_array(context, "visible_content_ids") {
        push_unique_positive(&mut content_ids, Some(id));
    }
    if !content_ids.is_empty() {
        lines.push("Visible Content:".to_owned());
        for content_id in content_ids {
            let Some(content) = load_content_in_transaction(transaction, content_id).await? else {
                continue;
            };
            let source = content
                .source
                .as_deref()
                .filter(|value| !value.trim().is_empty())
                .map_or_else(String::new, |value| format!(" ({value})"));
            lines.push(format!(
                "- [{content_id}] {}{source} — {}",
                content_title(&content),
                content.url
            ));
            if let Some(summary) = extract_short_summary(
                content
                    .content_metadata
                    .as_object()
                    .and_then(|value| value.get("summary")),
            ) {
                lines.push(format!("  Short Summary: {summary}"));
            }
            let excerpt_limit = if screen_type == "learning_deck"
                && json_positive_i64(context, "content_id") == Some(content_id)
            {
                1_800
            } else {
                420
            };
            if let Some(excerpt) = content_excerpt(&content.content_metadata, excerpt_limit) {
                lines.push(format!("  Transcript Excerpt: {excerpt}"));
            }
        }
    }

    let mut news_item_ids = Vec::new();
    push_unique_positive(
        &mut news_item_ids,
        json_positive_i64(context, "news_item_id"),
    );
    for id in json_i64_array(context, "visible_news_item_ids") {
        push_unique_positive(&mut news_item_ids, Some(id));
    }
    if !news_item_ids.is_empty() {
        lines.push("Visible News Items:".to_owned());
        for news_item_id in news_item_ids {
            let Some(news) =
                load_visible_news_in_transaction(transaction, user_id, news_item_id).await?
            else {
                continue;
            };
            let source = first_non_empty([
                news.source_label.as_deref(),
                news.article_domain.as_deref(),
                news.platform.as_deref(),
                news.source_type.as_deref(),
            ])
            .map_or_else(String::new, |value| format!(" ({value})"));
            lines.push(format!(
                "- [news:{news_item_id}] {}{source}",
                news_title(&news)
            ));
            if let Some(url) = &news.article_url {
                lines.push(format!("  Article URL: {url}"));
            }
            if news.canonical_story_url != news.article_url
                && let Some(url) = &news.canonical_story_url
            {
                lines.push(format!("  Story URL: {url}"));
            }
            if let Some(url) = &news.discussion_url {
                lines.push(format!("  Discussion URL: {url}"));
            }
            if let Some(summary) = &news.summary_text {
                lines.push(format!("  Summary: {summary}"));
            }
            if let Some(points) = news.summary_key_points.as_array()
                && !points.is_empty()
            {
                let rendered = points
                    .iter()
                    .take(5)
                    .map(value_as_display_text)
                    .collect::<Vec<_>>()
                    .join("; ");
                lines.push(format!("  Key Points: {rendered}"));
            }
        }
    }
    Ok(lines.join("\n"))
}

async fn load_session(
    pool: &PgPool,
    session_id: i64,
) -> Result<Option<ChatSessionRow>, sqlx::Error> {
    sqlx::query_as::<_, ChatSessionRow>(AssertSqlSafe(format!(
        "{SESSION_PROJECTION_SQL} WHERE id::bigint = $1"
    )))
    .bind(session_id)
    .fetch_optional(pool)
    .await
}

async fn load_session_for_update(
    transaction: &mut Transaction<'_, Postgres>,
    session_id: i64,
) -> Result<Option<ChatSessionRow>, sqlx::Error> {
    sqlx::query_as::<_, ChatSessionRow>(AssertSqlSafe(format!(
        "{SESSION_PROJECTION_SQL} WHERE id::bigint = $1 FOR UPDATE"
    )))
    .bind(session_id)
    .fetch_optional(&mut **transaction)
    .await
}

async fn load_content(
    pool: &PgPool,
    content_id: i64,
) -> Result<Option<ContentPresentationRow>, sqlx::Error> {
    sqlx::query_as::<_, ContentPresentationRow>(CONTENT_PRESENTATION_SQL)
        .bind(content_id)
        .fetch_optional(pool)
        .await
}

async fn load_content_in_transaction(
    transaction: &mut Transaction<'_, Postgres>,
    content_id: i64,
) -> Result<Option<ContentPresentationRow>, sqlx::Error> {
    sqlx::query_as::<_, ContentPresentationRow>(CONTENT_PRESENTATION_SQL)
        .bind(content_id)
        .fetch_optional(&mut **transaction)
        .await
}

async fn load_news(
    pool: &PgPool,
    news_item_id: i64,
) -> Result<Option<NewsPresentationRow>, sqlx::Error> {
    sqlx::query_as::<_, NewsPresentationRow>(NEWS_PRESENTATION_SQL)
        .bind(news_item_id)
        .fetch_optional(pool)
        .await
}

async fn load_visible_news_in_transaction(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
    news_item_id: i64,
) -> Result<Option<NewsPresentationRow>, sqlx::Error> {
    sqlx::query_as::<_, NewsPresentationRow>(AssertSqlSafe(format!(
        "{NEWS_PRESENTATION_SQL} AND {VISIBLE_NEWS_CLAUSE}"
    )))
    .bind(news_item_id)
    .bind(user_id)
    .fetch_optional(&mut **transaction)
    .await
}

async fn load_message_rows(
    pool: &PgPool,
    session_id: i64,
    min_message_id_exclusive: Option<i64>,
) -> Result<Vec<ChatMessageRow>, sqlx::Error> {
    sqlx::query_as::<_, ChatMessageRow>(AssertSqlSafe(format!(
        "{}\n{}",
        MESSAGE_PROJECTION_SQL,
        r"
        WHERE session_id::bigint = $1
          AND ($2::bigint IS NULL OR id::bigint > $2)
        ORDER BY created_at, id
        "
    )))
    .bind(session_id)
    .bind(min_message_id_exclusive)
    .fetch_all(pool)
    .await
}

async fn load_active_feed_urls(
    pool: &PgPool,
    user_id: i64,
) -> Result<BTreeSet<String>, sqlx::Error> {
    let urls = sqlx::query_scalar::<_, Option<String>>(
        r"
        SELECT feed_url
        FROM user_scraper_configs
        WHERE user_id::bigint = $1
          AND is_active = TRUE
          AND feed_url IS NOT NULL
        ",
    )
    .bind(user_id)
    .fetch_all(pool)
    .await?;
    Ok(urls
        .into_iter()
        .flatten()
        .filter(|url| !url.trim().is_empty())
        .map(|url| crate::canonicalize_feed_url(&url))
        .collect())
}

async fn content_awaiting_first_chat_turn(
    pool: &PgPool,
    user_id: i64,
    content_id: i64,
) -> Result<bool, sqlx::Error> {
    sqlx::query_scalar::<_, bool>(
        r"
        SELECT EXISTS(
            SELECT 1
            FROM contents
            WHERE id::bigint = $1
              AND (
                    COALESCE(
                        content_metadata::jsonb->'processing'->'share_and_chat_requests',
                        '[]'::jsonb
                    ) @> jsonb_build_array(jsonb_build_object('user_id', $2::bigint))
                    OR COALESCE(
                        content_metadata::jsonb->'share_and_chat_requests',
                        '[]'::jsonb
                    ) @> jsonb_build_array(jsonb_build_object('user_id', $2::bigint))
              )
        ) OR EXISTS(
            SELECT 1
            FROM processing_tasks
            WHERE content_id::bigint = $1
              AND task_type = 'dig_deeper'
              AND status IN ('pending', 'processing')
              AND payload::jsonb @> jsonb_build_object('user_id', $2::bigint)
        )
        ",
    )
    .bind(content_id)
    .bind(user_id)
    .fetch_one(pool)
    .await
}

async fn lock_active_user(
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

fn create_session_title(
    article_title: Option<&str>,
    topic: Option<&str>,
    initial_message: Option<&str>,
) -> String {
    match (article_title, topic) {
        (Some(article), Some(topic)) => truncate_chars(&format!("{article} - {topic}"), 500),
        (Some(article), None) => truncate_chars(article, 500),
        (None, Some(topic)) => truncate_chars(topic, 500),
        (None, None) => initial_message
            .and_then(derive_chat_session_title)
            .unwrap_or_else(|| "New Chat".to_owned()),
    }
}

fn derive_chat_session_title(message: &str) -> Option<String> {
    let mut skipping_label_block = false;
    for raw_line in message.lines() {
        let candidate = raw_line.split_whitespace().collect::<Vec<_>>().join(" ");
        if candidate.is_empty() {
            skipping_label_block = false;
            continue;
        }
        if skipping_label_block {
            continue;
        }
        if looks_like_internal_label(&candidate) {
            skipping_label_block = true;
            continue;
        }
        if candidate.chars().count() <= 80 {
            return Some(candidate);
        }
        let prefix = candidate.chars().take(80).collect::<String>();
        let truncated = prefix
            .rsplit_once(' ')
            .map_or(prefix.as_str(), |(head, _)| head)
            .trim_end_matches([' ', ',', ';', ':', '-']);
        return Some(format!("{truncated}…"));
    }
    None
}

fn looks_like_internal_label(value: &str) -> bool {
    let Some(label) = value.strip_suffix(':') else {
        return false;
    };
    let chars = label.chars().count();
    chars <= 31
        && label.chars().next().is_some_and(char::is_alphabetic)
        && label.chars().all(|character| {
            character.is_alphanumeric()
                || character == '_'
                || character == ' '
                || character == '/'
                || character == '-'
        })
}

fn is_assistant_session_type(value: Option<&str>) -> bool {
    matches!(
        value,
        Some("knowledge_chat" | "assistant_quick" | "article_brain" | "topic" | "weekly_discovery")
    )
}

fn json_string(value: &Value, field: &str) -> Option<String> {
    value
        .get(field)
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(str::to_owned)
}

fn json_positive_i64(value: &Value, field: &str) -> Option<i64> {
    value
        .get(field)
        .and_then(Value::as_i64)
        .filter(|value| *value > 0)
}

fn json_i64_array(value: &Value, field: &str) -> Vec<i64> {
    value
        .get(field)
        .and_then(Value::as_array)
        .map(|values| values.iter().filter_map(Value::as_i64).collect())
        .unwrap_or_default()
}

fn push_unique_positive(values: &mut Vec<i64>, value: Option<i64>) {
    if let Some(value) = value.filter(|value| *value > 0)
        && !values.contains(&value)
    {
        values.push(value);
    }
}

fn append_context_line(lines: &mut Vec<String>, label: &str, value: Option<String>) {
    if let Some(value) = value {
        lines.push(format!("{label}: {value}"));
    }
}

fn content_excerpt(metadata: &Value, max_chars: usize) -> Option<String> {
    let metadata = metadata.as_object()?;
    let candidates = [
        metadata.get("excerpt"),
        metadata.get("transcript"),
        metadata.get("content"),
        metadata
            .get("summary")
            .and_then(Value::as_object)
            .and_then(|summary| summary.get("full_markdown")),
    ];
    for candidate in candidates.into_iter().flatten() {
        let Some(text) = candidate.as_str() else {
            continue;
        };
        let compact = text.split_whitespace().collect::<Vec<_>>().join(" ");
        if compact.is_empty() {
            continue;
        }
        if compact.chars().count() <= max_chars {
            return Some(compact);
        }
        let mut excerpt = compact
            .chars()
            .take(max_chars.saturating_sub(3))
            .collect::<String>();
        excerpt = excerpt.trim_end().to_owned();
        excerpt.push_str("...");
        return Some(excerpt);
    }
    None
}

fn summarize_as_title(value: &str) -> String {
    let compact = value.split_whitespace().collect::<Vec<_>>().join(" ");
    if compact.chars().count() <= 120 {
        return compact;
    }
    let excerpt = truncate_chars(&compact, 120);
    format!("{}…", excerpt.trim_end())
}

fn first_non_empty<'a>(values: impl IntoIterator<Item = Option<&'a str>>) -> Option<String> {
    values
        .into_iter()
        .flatten()
        .map(str::trim)
        .find(|value| !value.is_empty())
        .map(str::to_owned)
}

fn truncate_chars(value: &str, max_chars: usize) -> String {
    value.chars().take(max_chars).collect()
}

fn value_as_display_text(value: &Value) -> String {
    match value {
        Value::String(value) => value.clone(),
        _ => value.to_string(),
    }
}

const SESSION_PROJECTION_SQL: &str = r"
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
        council_mode,
        active_child_session_id::bigint AS active_child_session_id,
        branch_start_message_id::bigint AS branch_start_message_id,
        is_hidden_from_history,
        llm_model,
        llm_provider,
        created_at,
        updated_at,
        last_message_at,
        is_archived
    FROM chat_sessions
";

const CONTENT_PRESENTATION_SQL: &str = r"
    SELECT
        url,
        title,
        source,
        content_metadata
    FROM contents
    WHERE id::bigint = $1
";

const NEWS_PRESENTATION_SQL: &str = r"
    SELECT
        platform,
        source_type,
        source_label,
        canonical_story_url,
        article_url,
        article_domain,
        discussion_url,
        summary_key_points,
        summary_text,
        raw_metadata
    FROM news_items
    WHERE id::bigint = $1
";

const VISIBLE_NEWS_CLAUSE: &str = r"
    (
        (visibility_scope = 'user' AND owner_user_id::bigint = $2)
        OR (
            visibility_scope = 'global'
            AND EXISTS (
                SELECT 1
                FROM user_scraper_configs config
                WHERE config.user_id::bigint = $2
                  AND config.scraper_type = 'aggregator'
                  AND config.is_active = TRUE
                  AND lower(COALESCE(config.config::jsonb->>'key', '')) = lower(COALESCE(news_items.platform, ''))
                  AND (
                        lower(COALESCE(news_items.platform, '')) <> 'brutalist'
                        OR CASE
                            WHEN jsonb_typeof(config.config::jsonb->'topics') = 'array'
                            THEN jsonb_array_length(config.config::jsonb->'topics') = 0
                            ELSE TRUE
                        END
                        OR lower(COALESCE(news_items.raw_metadata::jsonb->'aggregator'->>'topic', '')) IN (
                            SELECT lower(topic.value)
                            FROM jsonb_array_elements_text(
                                CASE
                                    WHEN jsonb_typeof(config.config::jsonb->'topics') = 'array'
                                    THEN config.config::jsonb->'topics'
                                    ELSE '[]'::jsonb
                                END
                            ) AS topic(value)
                        )
                  )
            )
        )
    )
";

const MESSAGE_PROJECTION_SQL: &str = r"
    SELECT
        id::bigint AS id,
        session_id::bigint AS session_id,
        message_list,
        render_metadata,
        created_at,
        status,
        error,
        partial_text,
        stream_generation,
        stream_revision,
        stream_updated_at,
        tool_progress,
        tool_progress_revision
    FROM chat_messages
";

const MESSAGE_PROJECTION_BY_ID_SQL: &str = r"
    SELECT
        id::bigint AS id,
        session_id::bigint AS session_id,
        message_list,
        render_metadata,
        created_at,
        status,
        error,
        partial_text,
        stream_generation,
        stream_revision,
        stream_updated_at,
        tool_progress,
        tool_progress_revision
    FROM chat_messages
    WHERE id::bigint = $1
";

#[derive(Debug, Error)]
pub enum ChatRepositoryError {
    #[error("chat database operation failed")]
    Sqlx(#[from] sqlx::Error),
    #[error("chat transcript operation failed")]
    Transcript(#[from] ChatTranscriptError),
    #[error("chat transcript serialization failed")]
    Json(#[from] serde_json::Error),
    #[error("inserted chat session could not be reloaded")]
    InsertedSessionMissing,
    #[error("chat message has unsupported status {0}")]
    InvalidMessageStatus(String),
}
