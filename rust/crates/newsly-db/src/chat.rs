use newsly_domain::truncate_chars;
use std::collections::BTreeSet;

use chrono::{DateTime, NaiveDateTime, Utc};
use serde_json::Value;
use sqlx::{AssertSqlSafe, FromRow, PgPool, Postgres, Transaction};
use thiserror::Error;

use crate::chat_tasks::{
    AssistantScreenContext, ChatTaskRepositoryError, ChatTurnKind, ChatTurnProcessingContext,
    ChatTurnSessionSnapshot,
};
use crate::chat_transcripts::{
    ChatTranscriptError, DisplayMessageProjection, RenderMetadataProjection, StoredChatMessage,
    display_messages, latest_assistant_text, latest_message_preview, overlay_subscription_state,
    processing_transcript,
};

const DEFAULT_PROVIDER: &str = "openai";
pub const DEFAULT_CHAT_MODEL: &str = "openai:gpt-6.1-sol";
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
    pub screen_context: &'a AssistantScreenContext,
}

#[derive(Debug, Clone, PartialEq)]
pub struct StagedChatTurn {
    pub session_id: i64,
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
    ContentNotFound,
    NewsItemNotFound,
    UserInactive,
}

#[derive(Debug, Clone, FromRow)]
pub(crate) struct ChatSessionRow {
    pub(crate) id: i64,
    pub(crate) user_id: i64,
    pub(crate) content_id: Option<i64>,
    pub(crate) news_item_id: Option<i64>,
    pub(crate) title: Option<String>,
    pub(crate) session_type: Option<String>,
    pub(crate) topic: Option<String>,
    pub(crate) context_snapshot: Option<String>,
    pub(crate) llm_model: String,
    pub(crate) llm_provider: String,
    pub(crate) created_at: NaiveDateTime,
    pub(crate) updated_at: Option<NaiveDateTime>,
    pub(crate) last_message_at: Option<NaiveDateTime>,
    pub(crate) is_archived: bool,
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

/// List visible, unarchived chat sessions using the stable activity cursor.
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
        WHERE user_id = $1::bigint
          AND is_archived = FALSE
          AND ($2::bigint IS NULL OR content_id = $2::bigint)
          AND ($3::bigint IS NULL OR news_item_id = $3::bigint)
          AND (
                $4::timestamp IS NULL
                OR COALESCE(last_message_at, created_at) < $4
                OR (
                    COALESCE(last_message_at, created_at) = $4
                    AND id < $5::bigint
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

    let mut states = load_session_message_states(pool, user_id, &rows).await?;
    let content_ids = rows
        .iter()
        .filter_map(|row| row.content_id)
        .collect::<BTreeSet<_>>()
        .into_iter()
        .collect::<Vec<_>>();
    let news_ids = rows
        .iter()
        .filter_map(|row| {
            (row.content_id.is_none())
                .then_some(row.news_item_id)
                .flatten()
        })
        .collect::<BTreeSet<_>>()
        .into_iter()
        .collect::<Vec<_>>();
    let waiting_candidates = rows
        .iter()
        .filter_map(|row| {
            let has_messages = states.get(&row.id).is_some_and(|state| state.has_messages);
            (!has_messages && row.session_type.as_deref() == Some(KNOWLEDGE_SESSION_TYPE))
                .then_some(row.content_id)
                .flatten()
        })
        .collect::<BTreeSet<_>>()
        .into_iter()
        .collect::<Vec<_>>();
    let contents_by_id = load_contents_by_ids(pool, &content_ids).await?;
    let news_by_id = load_news_by_ids(pool, &news_ids).await?;
    let waiting_content_ids = load_waiting_content_ids(pool, user_id, &waiting_candidates).await?;

    let mut projections = Vec::with_capacity(rows.len());
    for row in rows {
        let state = states.remove(&row.id).unwrap_or_default();
        let is_waiting_for_content = !state.has_messages
            && row.session_type.as_deref() == Some(KNOWLEDGE_SESSION_TYPE)
            && row
                .content_id
                .is_some_and(|content_id| waiting_content_ids.contains(&content_id));
        let article = if let Some(content_id) = row.content_id {
            contents_by_id
                .get(&content_id)
                .map(content_presentation)
                .unwrap_or_default()
        } else if let Some(news_item_id) = row.news_item_id {
            news_by_id
                .get(&news_item_id)
                .map(news_presentation)
                .unwrap_or_default()
        } else {
            ArticlePresentation::default()
        };
        projections.push(build_session_projection(
            row,
            state,
            is_waiting_for_content,
            article,
        ));
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
    let rows = load_message_rows(pool, session_id).await?;
    let parent_stored = rows.iter().map(StoredChatMessage::from).collect::<Vec<_>>();
    let messages = display_messages(&parent_stored, session_id, &active_feed_urls)
        .into_iter()
        .map(Into::into)
        .collect::<Vec<_>>();

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
        let content =
            load_visible_content_in_transaction(transaction, input.user_id, content_id).await?;
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
        let context = AssistantScreenContext {
            screen_type: KNOWLEDGE_SESSION_TYPE.to_owned(),
            screen_title: Some("Knowledge".to_owned()),
            content_id: input.content_id,
            news_item_id: input.news_item_id,
            visible_content_ids: Vec::new(),
            visible_news_item_ids: Vec::new(),
            selected_topic: input.topic.map(str::to_owned),
            query: None,
            note: input
                .initial_message
                .map(|value| truncate_chars(value, 500)),
            assistant_action: None,
        };
        Some(build_screen_context_snapshot(transaction, input.user_id, &context).await?)
    } else {
        None
    };

    let session_id = sqlx::query_scalar::<_, i64>(
        r"
        INSERT INTO chat_sessions (
            user_id, content_id, news_item_id, title, session_type, topic,
            context_snapshot,
            llm_model, llm_provider, created_at, updated_at
        )
        VALUES (
            $1::bigint::integer, $2::bigint::integer, $3::bigint::integer, $4, $5, $6,
            $7, $8, $9,
            timezone('UTC', clock_timestamp()), timezone('UTC', clock_timestamp())
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
            WHERE id = $1::bigint
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

/// Archive an owned session atomically.
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
            WHERE id = $1::bigint
            ",
        )
        .bind(session_id)
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
    let Some(session) = load_session_for_update(transaction, input.session_id).await? else {
        return Ok(StageChatTurnOutcome::NotFound);
    };
    if session.user_id != input.user_id {
        return Ok(StageChatTurnOutcome::Forbidden);
    }
    if session.is_archived {
        return Ok(StageChatTurnOutcome::Archived);
    }

    let (kind, source, screen_context) = if session.session_type.as_deref() == Some("deep_research")
    {
        (ChatTurnKind::DeepResearch, "realtime", None)
    } else if is_assistant_session_type(session.session_type.as_deref()) {
        let context = AssistantScreenContext {
            screen_type: session
                .session_type
                .as_deref()
                .unwrap_or("unknown")
                .to_owned(),
            screen_title: session
                .title
                .as_deref()
                .map(|value| truncate_chars(value, 200)),
            content_id: session.content_id,
            news_item_id: session.news_item_id,
            visible_content_ids: Vec::new(),
            visible_news_item_ids: Vec::new(),
            selected_topic: None,
            query: None,
            note: None,
            assistant_action: None,
        };
        (ChatTurnKind::Assistant, "assistant", Some(context))
    } else {
        (ChatTurnKind::Article, "realtime", None)
    };

    let staged = stage_session_turn(
        transaction,
        &session,
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
    input.screen_context.validate()?;
    let content_id = input.screen_context.content_id;
    let primary_content = if let Some(content_id) = content_id {
        let item =
            load_visible_content_in_transaction(transaction, input.user_id, content_id).await?;
        if item.is_none() {
            return Ok(StageChatTurnOutcome::ContentNotFound);
        }
        item
    } else {
        None
    };
    let primary_news_id = input.screen_context.news_item_id;
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
    let selected_topic = input.screen_context.selected_topic.clone();
    let screen_title = input.screen_context.screen_title.clone();

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

        let mut title = screen_title
            .clone()
            .or_else(|| row.title.clone())
            .unwrap_or_else(|| "Knowledge Chat".to_owned());
        if let Some(content) = &primary_content {
            title = content_title(content);
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
            WHERE id = $1::bigint
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
        if let Some(content) = &primary_content {
            title = content_display_title(content.title.as_deref(), &content.content_metadata)
                .unwrap_or(title);
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
                context_snapshot,
                llm_model, llm_provider, created_at, updated_at
            )
            VALUES (
                $1::bigint::integer, $2::bigint::integer, $3::bigint::integer, $4,
                $5, $6, $7, $8, $9,
                timezone('UTC', clock_timestamp()), timezone('UTC', clock_timestamp())
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
        .bind(DEFAULT_CHAT_MODEL)
        .bind(DEFAULT_PROVIDER)
        .fetch_one(&mut **transaction)
        .await?;
        load_session_for_update(transaction, session_id)
            .await?
            .ok_or(ChatRepositoryError::InsertedSessionMissing)?
    };

    let staged = stage_session_turn(
        transaction,
        &session,
        input.user_prompt,
        ChatTurnKind::Assistant,
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
    let row = sqlx::query_as::<_, ChatMessageRow>(AssertSqlSafe(format!(
        "{MESSAGE_PROJECTION_SQL} WHERE id = $1::bigint"
    )))
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

    let projection = match row.status.as_str() {
        "processing" => {
            let partial_assistant_message = row.partial_text.as_ref().and_then(|partial| {
                (!partial.trim().is_empty()).then(|| ChatMessageProjection {
                    id: 1_000_000_000_i64.saturating_add(message_id),
                    source_message_id: message_id,
                    session_id: session.id,
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
                session_id: session.id,
                role: "assistant".to_owned(),
                content: assistant_content,
                timestamp: row.created_at.and_utc(),
                display_type: "message".to_owned(),
                process_label: None,
                status: "completed".to_owned(),
                error: None,
                feed_options: overlay_subscription_state(metadata.feed_options, &active_feed_urls),
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

async fn stage_session_turn(
    transaction: &mut Transaction<'_, Postgres>,
    session: &ChatSessionRow,
    user_prompt: &str,
    kind: ChatTurnKind,
    source: &str,
    screen_context: Option<&AssistantScreenContext>,
) -> Result<StagedChatTurn, ChatRepositoryError> {
    if let Some(context) = screen_context {
        context.validate()?;
    }
    let now = Utc::now();
    let transcript = processing_transcript(user_prompt, now);
    let message_list = serde_json::to_string(&transcript)?;
    let snapshot = ChatTurnSessionSnapshot {
        title: session.title.clone(),
        session_type: session.session_type.clone(),
        content_id: session.content_id,
        news_item_id: session.news_item_id,
        topic: session.topic.clone(),
        context_snapshot: session.context_snapshot.clone(),
        ..ChatTurnSessionSnapshot::new(
            session.user_id,
            session.id,
            session.llm_model.clone(),
            session.llm_provider.clone(),
        )
    };
    let processing_context = serde_json::to_value(ChatTurnProcessingContext::new(
        kind,
        user_prompt,
        source,
        snapshot,
        screen_context.cloned(),
    ))?;

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
    .bind(session.id)
    .bind(message_list)
    .bind(&processing_context)
    .fetch_one(&mut **transaction)
    .await?;

    sqlx::query(
        r"
        UPDATE chat_sessions
        SET last_message_at = $2,
            updated_at = $2
        WHERE id = $1::bigint
        ",
    )
    .bind(session.id)
    .bind(inserted.created_at)
    .execute(&mut **transaction)
    .await?;
    Ok(StagedChatTurn {
        session_id: session.id,
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
    let state = load_session_message_states(pool, user_id, std::slice::from_ref(&row))
        .await?
        .remove(&row.id)
        .unwrap_or_default();
    present_session_with_state(pool, user_id, row, state).await
}

async fn present_session_with_state(
    pool: &PgPool,
    user_id: i64,
    row: ChatSessionRow,
    state: SessionMessageState,
) -> Result<ChatSessionProjection, ChatRepositoryError> {
    let is_waiting_for_content =
        if !state.has_messages && row.session_type.as_deref() == Some(KNOWLEDGE_SESSION_TYPE) {
            if let Some(content_id) = row.content_id {
                content_awaiting_first_chat_turn(pool, user_id, content_id).await?
            } else {
                false
            }
        } else {
            false
        };
    let article = article_presentation(pool, &row).await?;
    Ok(build_session_projection(
        row,
        state,
        is_waiting_for_content,
        article,
    ))
}

fn build_session_projection(
    row: ChatSessionRow,
    state: SessionMessageState,
    is_waiting_for_content: bool,
    article: ArticlePresentation,
) -> ChatSessionProjection {
    let SessionMessageState {
        has_pending_message,
        has_messages,
        last_message_list,
        is_saved_to_knowledge,
        ..
    } = state;
    let (last_message_preview, last_message_role) = last_message_list
        .as_deref()
        .and_then(latest_message_preview)
        .map_or((None, None), |(preview, role)| {
            (Some(preview), Some(role.to_owned()))
        });

    ChatSessionProjection {
        id: row.id,
        title: row.title,
        content_id: row.content_id,
        news_item_id: row.news_item_id,
        session_type: row.session_type,
        topic: row.topic,
        llm_model: if row.llm_model.is_empty() {
            DEFAULT_CHAT_MODEL.to_owned()
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
    }
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

async fn load_session(
    pool: &PgPool,
    session_id: i64,
) -> Result<Option<ChatSessionRow>, sqlx::Error> {
    sqlx::query_as::<_, ChatSessionRow>(AssertSqlSafe(format!(
        "{SESSION_PROJECTION_SQL} WHERE id = $1::bigint"
    )))
    .bind(session_id)
    .fetch_optional(pool)
    .await
}

pub(crate) async fn load_session_for_update(
    transaction: &mut Transaction<'_, Postgres>,
    session_id: i64,
) -> Result<Option<ChatSessionRow>, sqlx::Error> {
    sqlx::query_as::<_, ChatSessionRow>(AssertSqlSafe(format!(
        "{SESSION_PROJECTION_SQL} WHERE id = $1::bigint FOR UPDATE"
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

async fn load_visible_content_in_transaction(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
    content_id: i64,
) -> Result<Option<ContentPresentationRow>, sqlx::Error> {
    sqlx::query_as::<_, ContentPresentationRow>(AssertSqlSafe(format!(
        "{CONTENT_PRESENTATION_SQL} AND {VISIBLE_CONTENT_CLAUSE}"
    )))
    .bind(content_id)
    .bind(user_id)
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
) -> Result<Vec<ChatMessageRow>, sqlx::Error> {
    sqlx::query_as::<_, ChatMessageRow>(AssertSqlSafe(format!(
        "{}\n{}",
        MESSAGE_PROJECTION_SQL,
        r"
        WHERE session_id = $1::bigint
        ORDER BY created_at, id
        "
    )))
    .bind(session_id)
    .fetch_all(pool)
    .await
}

/// Canonical feed URLs of the user's active subscriptions, including `aggregator://<key>`.
pub async fn load_active_feed_urls(
    pool: &PgPool,
    user_id: i64,
) -> Result<BTreeSet<String>, sqlx::Error> {
    let urls = sqlx::query_scalar::<_, Option<String>>(
        r"
        SELECT feed_url
        FROM user_scraper_configs
        WHERE user_id = $1::bigint
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
    Ok(load_waiting_content_ids(pool, user_id, &[content_id])
        .await?
        .contains(&content_id))
}

pub(crate) async fn lock_active_user(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
) -> Result<bool, sqlx::Error> {
    Ok(sqlx::query_scalar::<_, i64>(
        "SELECT id::bigint FROM users WHERE id = $1::bigint AND is_active = TRUE FOR SHARE",
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

const SESSION_PROJECTION_SQL: &str = r"
    SELECT
        id::bigint AS id,
        user_id::bigint AS user_id,
        content_id::bigint AS content_id,
        news_item_id::bigint AS news_item_id,
        title,
        session_type,
        topic,
        context_snapshot,
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
    WHERE id = $1::bigint
";

const VISIBLE_CONTENT_CLAUSE: &str = r"
    (
        EXISTS (
            SELECT 1
            FROM content_knowledge_saves AS knowledge_save
            WHERE knowledge_save.user_id = $2::bigint
              AND knowledge_save.content_id = contents.id
        )
        OR EXISTS (
            SELECT 1
            FROM content_status AS user_status
            WHERE user_status.user_id = $2::bigint
              AND user_status.content_id = contents.id
              AND user_status.status = 'inbox'
        )
        OR EXISTS (
            SELECT 1
            FROM chat_sessions AS owned_session
            WHERE owned_session.user_id = $2::bigint
              AND owned_session.content_id = contents.id
        )
        OR EXISTS (
            SELECT 1
            FROM learning_decks AS owned_deck
            WHERE owned_deck.user_id = $2::bigint
              AND owned_deck.source_content_id = contents.id
              AND owned_deck.deleted_at IS NULL
        )
    )
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
    WHERE id = $1::bigint
";

const VISIBLE_NEWS_CLAUSE: &str = r"
    (
        (visibility_scope = 'user' AND owner_user_id = $2::bigint)
        OR (
            visibility_scope = 'global'
            AND EXISTS (
                SELECT 1
                FROM user_scraper_configs config
                WHERE config.user_id = $2::bigint
                  AND config.scraper_type = 'aggregator'
                  AND config.is_active = TRUE
                  AND aggregator_config_admits(
                      config.config::jsonb, news_items.platform, news_items.raw_metadata
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

#[derive(Debug, Error)]
pub enum ChatRepositoryError {
    #[error("chat database operation failed")]
    Sqlx(#[from] sqlx::Error),
    #[error("chat transcript operation failed")]
    Transcript(#[from] ChatTranscriptError),
    #[error("chat transcript serialization failed")]
    Json(#[from] serde_json::Error),
    #[error("chat screen context is invalid")]
    InvalidScreenContext(#[from] ChatTaskRepositoryError),
    #[error("inserted chat session could not be reloaded")]
    InsertedSessionMissing,
    #[error("chat message has unsupported status {0}")]
    InvalidMessageStatus(String),
}

#[cfg(test)]
mod screen_context_tests;

#[cfg(test)]
#[path = "chat/session_list_tests.rs"]
mod session_list_tests;

#[path = "chat/presentation.rs"]
mod presentation;
pub(crate) use presentation::{
    build_screen_context_snapshot, content_display_title, extract_short_summary,
};
use presentation::{
    content_presentation, content_title, load_contents_by_ids, load_news_by_ids,
    load_waiting_content_ids, news_presentation, news_title,
};

#[path = "chat/session_state.rs"]
mod session_state;
use session_state::{SessionMessageState, load_session_message_states};
