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
use sqlx::{FromRow, Postgres, Transaction};
use thiserror::Error;

use crate::chat_transcripts::{ChatTranscriptError, decode_transcript, processing_transcript};

use crate::chat::DEFAULT_CHAT_MODEL as DEFAULT_MODEL;
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
    pub message_id: i64,
    pub stream_generation: i32,
    pub context: ChatTurnProcessingContext,
    pub history: NewslyTranscript,
    pub llm_task_id: Option<i64>,
    pub encrypted_provider_key: Option<String>,
    pub content: Option<ChatContentMaterial>,
    pub workspace_path: String,
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
    // Decode-only compatibility for messages written before council retirement.
    // newsly-db removes these named fields once no pre-cutover queued rows remain.
    #[serde(default, skip_serializing)]
    pub council_run: Option<Value>,
}

impl ChatTurnProcessingContext {
    pub(crate) fn new(
        kind: ChatTurnKind,
        user_prompt: &str,
        source: &str,
        session: ChatTurnSessionSnapshot,
        screen_context: Option<AssistantScreenContext>,
    ) -> Self {
        Self {
            version: 1,
            kind,
            user_prompt: user_prompt.to_owned(),
            source: source.to_owned(),
            session,
            screen_context,
            council_run: None,
        }
    }

    fn validate(&self) -> Result<(), ChatTaskRepositoryError> {
        if self.version != 1 {
            return Err(ChatTaskRepositoryError::InvalidProcessingContext(
                "unsupported processing context version".to_owned(),
            ));
        }
        validate_bounded_text(&self.user_prompt, 1, 10_000, "user_prompt")?;
        validate_bounded_text(&self.source, 1, 50, "source")?;
        self.session.validate()?;
        if self.council_run.is_some() {
            return Err(ChatTaskRepositoryError::InvalidProcessingContext(
                "retired branch context cannot execute".to_owned(),
            ));
        }
        match (self.kind, &self.screen_context) {
            (ChatTurnKind::Assistant, Some(context)) => context.validate(),
            (ChatTurnKind::Assistant, None) => {
                Err(ChatTaskRepositoryError::InvalidProcessingContext(
                    "assistant chat turns require screen_context".to_owned(),
                ))
            }
            (ChatTurnKind::Article | ChatTurnKind::DeepResearch, None) => Ok(()),
            (_, Some(_)) => Err(ChatTaskRepositoryError::InvalidProcessingContext(
                "only assistant chat turns accept screen_context".to_owned(),
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
    // Decode-only fields written on ordinary turns by older binaries. Keep this bounded
    // vocabulary until those queued rows have settled; never publish it on new turns.
    #[serde(default, skip_serializing)]
    pub parent_session_id: Option<i64>,
    #[serde(default)]
    pub topic: Option<String>,
    #[serde(default)]
    pub context_snapshot: Option<String>,
    #[serde(default, skip_serializing)]
    pub is_hidden_from_history: bool,
    #[serde(default, skip_serializing)]
    pub council_persona_id: Option<String>,
    #[serde(default, skip_serializing)]
    pub council_persona_name: Option<String>,
    #[serde(default, skip_serializing)]
    pub council_persona_prompt: Option<String>,
}

impl ChatTurnSessionSnapshot {
    pub(crate) fn new(user_id: i64, session_id: i64, model: String, provider: String) -> Self {
        Self {
            user_id,
            effective_session_id: session_id,
            visible_session_id: session_id,
            model,
            provider,
            title: None,
            session_type: None,
            content_id: None,
            news_item_id: None,
            topic: None,
            context_snapshot: None,
            parent_session_id: None,
            is_hidden_from_history: false,
            council_persona_id: None,
            council_persona_name: None,
            council_persona_prompt: None,
        }
    }

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
        Ok(())
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
    pub(crate) fn validate(&self) -> Result<(), ChatTaskRepositoryError> {
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
    pub usage: &'a ProviderUsage,
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

use crate::chat::ChatSessionRow as SessionRow;

#[derive(Debug, Clone, FromRow)]
struct MessageRow {
    id: i64,
    session_id: i64,
    processing_context: Option<Value>,
    status: String,
    error: Option<String>,
    stream_generation: Option<i32>,
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

pub use advisory::{write_chat_partial, write_chat_tool_progress};
pub use ledger::cancel_chat_llm_task_attempt;
pub use preparation::prepare_chat_task;
pub use publication::{fail_chat_turn, publish_chat_turn};

#[cfg(test)]
mod retirement_tests {
    use super::ChatTurnProcessingContext;
    use serde_json::json;

    fn legacy_context() -> serde_json::Value {
        json!({
            "version": 1, "kind": "article", "user_prompt": "Explain this source",
            "source": "realtime", "screen_context": null, "council_run": null,
            "session": {
                "user_id": 1, "effective_session_id": 2, "visible_session_id": 2,
                "model": "openai:gpt-5.6-sol", "provider": "openai",
                "parent_session_id": null, "is_hidden_from_history": false,
                "council_persona_id": null, "council_persona_name": null,
                "council_persona_prompt": null
            }
        })
    }

    #[test]
    fn older_ordinary_turns_still_decode_and_do_not_reemit_retired_fields() {
        let context: ChatTurnProcessingContext = serde_json::from_value(legacy_context()).unwrap();
        context.validate().unwrap();
        let encoded = serde_json::to_value(context).unwrap();
        assert!(encoded.get("council_run").is_none());
        for key in [
            "parent_session_id",
            "is_hidden_from_history",
            "council_persona_id",
            "council_persona_name",
            "council_persona_prompt",
        ] {
            assert!(encoded["session"].get(key).is_none(), "{key}");
        }
    }

    #[test]
    fn retired_turns_cannot_reach_execution() {
        let mut value = legacy_context();
        value["kind"] = json!("council");
        assert!(serde_json::from_value::<ChatTurnProcessingContext>(value).is_err());
        let mut value = legacy_context();
        value["council_run"] = json!({"parent_message_id": 3});
        let context: ChatTurnProcessingContext = serde_json::from_value(value).unwrap();
        assert!(context.validate().is_err());
    }

    #[test]
    fn decoder_tolerance_is_limited_to_named_retired_fields() {
        let mut value = legacy_context();
        value["session"]["unrecognized_field"] = json!(true);
        assert!(serde_json::from_value::<ChatTurnProcessingContext>(value).is_err());
    }
}
