//! Durable preparation and lease-fenced publication for queued chat work.
//!
//! This module deliberately keeps provider, E2B, and object-storage work out of PostgreSQL
//! transactions. A worker first obtains an immutable [`ChatTaskSnapshot`], performs external work,
//! and later applies one bounded terminal mutation from inside the queue kernel's exact lease
//! fence. Advisory partial text and tool progress use a second, monotonic `stream_generation`
//! fence so a reclaimed attempt cannot overwrite a newer attempt while it is still winding down.

use chrono::Utc;
use newsly_agent_runtime::{MessagePart, NewslyTranscript, ProviderUsage, RequestPart};
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value, json};
use sqlx::{Acquire, FromRow, Postgres, Transaction};
use thiserror::Error;

use crate::chat_council::{
    CouncilCandidateCompletion, CouncilRunContext, finalize_council_candidate,
    finalize_failed_council_candidate,
};
use crate::chat_transcripts::{ChatTranscriptError, decode_transcript, processing_transcript};

const DEFAULT_MODEL: &str = "openai:gpt-5.6-terra";
const DEFAULT_PROVIDER: &str = "openai";
const KNOWLEDGE_SESSION_TYPE: &str = "knowledge_chat";
const CHAT_FAILURE_MESSAGE: &str = "This chat turn could not be completed. Please retry.";
const CHAT_UNAVAILABLE_MESSAGE: &str = "This chat is no longer available.";
const DIG_DEEPER_FAILURE_MESSAGE: &str =
    "Dig-deeper chat stopped after repeated worker interruptions";
const ORDER_RETRY_DELAY_SECONDS: i64 = 2;
const CHAT_HISTORY_MAX_TOKENS: usize = 16_000;
const HISTORICAL_TOOL_RESULT_MAX_TOKENS: usize = 2_000;
const TOKEN_CHARS_PER_TOKEN: usize = 4;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum QueuedChatTaskKind {
    ChatTurn,
    DigDeeper,
}

impl QueuedChatTaskKind {
    const fn as_str(self) -> &'static str {
        match self {
            Self::ChatTurn => "chat_turn",
            Self::DigDeeper => "dig_deeper",
        }
    }
}

#[derive(Debug, Clone)]
pub struct PrepareChatTask<'a> {
    pub queue_task_id: i64,
    pub queue_task_kind: QueuedChatTaskKind,
    pub owner_user_id: i64,
    pub content_id: Option<i64>,
    pub payload: &'a Map<String, Value>,
    pub stream_generation: i32,
    pub max_retries: i32,
    pub history_message_limit: i64,
}

#[derive(Debug, Clone, PartialEq)]
pub enum ChatTaskPreparationOutcome {
    Ready(ChatTaskSnapshot),
    Completed,
    AlreadyFailed { message: String },
    SkippedInactiveUser,
    Deferred { retry_delay_seconds: i64 },
    Reject(ChatTaskRejection),
    Superseded,
}

#[derive(Debug, Clone, PartialEq)]
pub struct ChatTaskRejection {
    pub message_id: Option<i64>,
    pub session_id: Option<i64>,
    pub user_id: i64,
    pub llm_task_id: Option<i64>,
    pub expected_stream_generation: Option<i32>,
    pub public_message: String,
    pub task_message: String,
    pub error_type: String,
}

#[derive(Debug, Clone, PartialEq)]
pub struct ChatTaskSnapshot {
    pub queue_task_id: i64,
    pub user_id: i64,
    pub session_id: i64,
    pub visible_session_id: i64,
    pub message_id: i64,
    pub stream_generation: i32,
    pub context: ChatTurnProcessingContext,
    pub history: NewslyTranscript,
    pub llm_task_id: Option<i64>,
    pub deep_research_response_id: Option<String>,
    pub encrypted_provider_key: Option<String>,
    pub content: Option<ChatContentMaterial>,
    pub vm_namespace: String,
    pub workspace_path: String,
    pub shared_workspace_path: String,
}

#[derive(Debug, Clone, PartialEq)]
pub struct ChatContentMaterial {
    pub content_id: i64,
    pub content_type: String,
    pub url: String,
    pub title: Option<String>,
    pub source: Option<String>,
    pub metadata: Value,
    pub body_storage_key: Option<String>,
    pub fallback_body: Option<String>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ChatTurnKind {
    Article,
    Assistant,
    Council,
    DeepResearch,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ChatTurnProcessingContext {
    pub version: u8,
    pub kind: ChatTurnKind,
    pub user_prompt: String,
    pub source: String,
    pub session: ChatTurnSessionSnapshot,
    #[serde(default)]
    pub screen_context: Option<AssistantScreenContext>,
    #[serde(default)]
    pub council_run: Option<CouncilRunContext>,
}

impl ChatTurnProcessingContext {
    fn validate(&self) -> Result<(), ChatTaskRepositoryError> {
        if self.version != 1 {
            return Err(ChatTaskRepositoryError::InvalidProcessingContext(
                "unsupported processing context version".to_owned(),
            ));
        }
        validate_bounded_text(&self.user_prompt, 1, 10_000, "user_prompt")?;
        validate_bounded_text(&self.source, 1, 50, "source")?;
        self.session.validate()?;
        match (self.kind, &self.screen_context, &self.council_run) {
            (ChatTurnKind::Assistant, Some(context), None) => context.validate(),
            (ChatTurnKind::Assistant, None, None) => {
                Err(ChatTaskRepositoryError::InvalidProcessingContext(
                    "assistant chat turns require screen_context".to_owned(),
                ))
            }
            (ChatTurnKind::Council, None, Some(context)) => context
                .validate()
                .map_err(ChatTaskRepositoryError::InvalidProcessingContext),
            (
                ChatTurnKind::Council | ChatTurnKind::Article | ChatTurnKind::DeepResearch,
                None,
                None,
            ) => Ok(()),
            (_, Some(_), _) => Err(ChatTaskRepositoryError::InvalidProcessingContext(
                "only assistant chat turns accept screen_context".to_owned(),
            )),
            (_, None, Some(_)) => Err(ChatTaskRepositoryError::InvalidProcessingContext(
                "only council chat turns accept council_run".to_owned(),
            )),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ChatTurnSessionSnapshot {
    pub user_id: i64,
    pub effective_session_id: i64,
    pub visible_session_id: i64,
    pub model: String,
    pub provider: String,
    #[serde(default)]
    pub title: Option<String>,
    #[serde(default)]
    pub session_type: Option<String>,
    #[serde(default)]
    pub content_id: Option<i64>,
    #[serde(default)]
    pub news_item_id: Option<i64>,
    #[serde(default)]
    pub parent_session_id: Option<i64>,
    #[serde(default)]
    pub topic: Option<String>,
    #[serde(default)]
    pub context_snapshot: Option<String>,
    #[serde(default)]
    pub is_hidden_from_history: bool,
    #[serde(default)]
    pub council_persona_id: Option<String>,
    #[serde(default)]
    pub council_persona_name: Option<String>,
    #[serde(default)]
    pub council_persona_prompt: Option<String>,
}

impl ChatTurnSessionSnapshot {
    fn validate(&self) -> Result<(), ChatTaskRepositoryError> {
        for (value, name) in [
            (self.user_id, "session.user_id"),
            (self.effective_session_id, "session.effective_session_id"),
            (self.visible_session_id, "session.visible_session_id"),
        ] {
            validate_positive(value, name)?;
        }
        for (value, name) in [
            (self.content_id, "session.content_id"),
            (self.news_item_id, "session.news_item_id"),
            (self.parent_session_id, "session.parent_session_id"),
        ] {
            if let Some(value) = value {
                validate_positive(value, name)?;
            }
        }
        validate_bounded_text(&self.model, 1, 100, "session.model")?;
        validate_bounded_text(&self.provider, 1, 50, "session.provider")?;
        validate_optional_text(self.title.as_deref(), 500, "session.title")?;
        validate_optional_text(self.session_type.as_deref(), 50, "session.session_type")?;
        validate_optional_text(self.topic.as_deref(), 500, "session.topic")?;
        validate_optional_text(
            self.council_persona_id.as_deref(),
            64,
            "session.council_persona_id",
        )?;
        validate_optional_text(
            self.council_persona_name.as_deref(),
            120,
            "session.council_persona_name",
        )
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AssistantScreenContext {
    #[serde(default = "default_screen_type")]
    pub screen_type: String,
    #[serde(default)]
    pub screen_title: Option<String>,
    #[serde(default)]
    pub content_id: Option<i64>,
    #[serde(default)]
    pub news_item_id: Option<i64>,
    #[serde(default)]
    pub visible_content_ids: Vec<i64>,
    #[serde(default)]
    pub visible_news_item_ids: Vec<i64>,
    #[serde(default)]
    pub selected_topic: Option<String>,
    #[serde(default)]
    pub query: Option<String>,
    #[serde(default)]
    pub note: Option<String>,
    #[serde(default)]
    pub assistant_action: Option<String>,
}

impl AssistantScreenContext {
    fn validate(&self) -> Result<(), ChatTaskRepositoryError> {
        validate_bounded_text(&self.screen_type, 0, 64, "screen_context.screen_type")?;
        if self.content_id.is_some() && self.news_item_id.is_some() {
            return Err(ChatTaskRepositoryError::InvalidProcessingContext(
                "screen_context content_id and news_item_id are mutually exclusive".to_owned(),
            ));
        }
        if self.visible_content_ids.len() > 12 || self.visible_news_item_ids.len() > 12 {
            return Err(ChatTaskRepositoryError::InvalidProcessingContext(
                "screen_context visible id lists exceed 12 entries".to_owned(),
            ));
        }
        for value in self
            .content_id
            .into_iter()
            .chain(self.news_item_id)
            .chain(self.visible_content_ids.iter().copied())
            .chain(self.visible_news_item_ids.iter().copied())
        {
            validate_positive(value, "screen_context id")?;
        }
        validate_optional_text(
            self.screen_title.as_deref(),
            200,
            "screen_context.screen_title",
        )?;
        validate_optional_text(
            self.selected_topic.as_deref(),
            200,
            "screen_context.selected_topic",
        )?;
        validate_optional_text(self.query.as_deref(), 200, "screen_context.query")?;
        validate_optional_text(self.note.as_deref(), 1_500, "screen_context.note")?;
        validate_optional_text(
            self.assistant_action.as_deref(),
            100,
            "screen_context.assistant_action",
        )
    }
}

fn default_screen_type() -> String {
    "unknown".to_owned()
}

#[derive(Debug, Clone, PartialEq)]
pub struct ChatTurnPublication<'a> {
    pub snapshot: &'a ChatTaskSnapshot,
    pub turn_transcript: &'a NewslyTranscript,
    pub render_metadata: Option<&'a Value>,
    pub output_text: &'a str,
    pub tool_names: &'a [String],
    pub model_provider: &'a str,
    pub model_name: &'a str,
    pub provider_response_id: Option<&'a str>,
    pub usage: &'a ProviderUsage,
    pub usage_source: &'a str,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ChatTerminalMutationOutcome {
    Applied,
    AlreadyCompleted,
    AlreadyFailed,
    Superseded,
    Missing,
}

#[derive(Debug, Clone, PartialEq)]
pub struct ChatToolProgress<'a> {
    pub tool_name: &'a str,
    pub status: &'a str,
    pub detail: Option<&'a str>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ChatAdvisoryWriteOutcome {
    Applied,
    Unchanged,
    Superseded,
    Terminal,
    Missing,
}

#[derive(Debug, FromRow)]
struct QueueTaskRow {
    id: i64,
    task_type: String,
    content_id: Option<i64>,
    owner_user_id: Option<i64>,
    payload: Value,
    status: String,
}

#[derive(Debug, Clone, FromRow)]
struct SessionRow {
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
    is_hidden_from_history: bool,
    llm_model: String,
    llm_provider: String,
    is_archived: bool,
}

#[derive(Debug, Clone, FromRow)]
struct MessageRow {
    id: i64,
    session_id: i64,
    processing_context: Option<Value>,
    status: String,
    error: Option<String>,
    stream_generation: Option<i32>,
    deep_research_response_id: Option<String>,
}

#[derive(Debug, Clone, FromRow)]
struct ContentRow {
    id: i64,
    content_type: String,
    url: String,
    title: Option<String>,
    source: Option<String>,
    content_metadata: Value,
    body_storage_key: Option<String>,
}

#[derive(Debug, Clone, FromRow)]
struct DiscussionRow {
    discussion_data: Value,
}

#[derive(Debug, Error)]
pub enum ChatTaskRepositoryError {
    #[error("queued chat database operation failed")]
    Sqlx(#[from] sqlx::Error),
    #[error("queued chat JSON operation failed")]
    Json(#[from] serde_json::Error),
    #[error("queued chat transcript operation failed")]
    Transcript(#[from] ChatTranscriptError),
    #[error("queued chat durable transcript is invalid")]
    RuntimeTranscript(#[from] newsly_agent_runtime::TranscriptError),
    #[error(transparent)]
    Council(#[from] crate::chat_council::CouncilRepositoryError),
    #[error("queued chat task was not found")]
    QueueTaskNotFound,
    #[error("queued chat ownership validation failed")]
    OwnershipMismatch,
    #[error("queued chat payload is invalid: {0}")]
    InvalidPayload(String),
    #[error("queued chat processing context is invalid: {0}")]
    InvalidProcessingContext(String),
    #[error("dig-deeper persisted state is invalid: {0}")]
    InvalidPersistedLink(String),
    #[error("chat message has unsupported status {0}")]
    InvalidMessageStatus(String),
    #[error("chat session disappeared during preparation")]
    SessionDisappeared,
    #[error("chat provider result is invalid: {0}")]
    InvalidProviderResult(String),
}

mod advisory;
mod context;
mod ledger;
mod preparation;
mod publication;

use preparation::{validate_bounded_text, validate_optional_text, validate_positive};

pub use advisory::{
    persist_deep_research_response_id, write_chat_partial, write_chat_tool_progress,
};
pub use ledger::cancel_chat_llm_task_attempt;
pub use preparation::prepare_chat_task;
pub use publication::{fail_chat_turn, publish_chat_turn};
