use chrono::{DateTime, Utc};
use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};
use thiserror::Error;
use uuid::Uuid;

pub const NEWSLY_TRANSCRIPT_VERSION: u16 = 1;

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct NewslyTranscript {
    pub version: u16,
    pub stream_generation: u64,
    pub messages: Vec<NewslyMessage>,
}

impl Default for NewslyTranscript {
    fn default() -> Self {
        Self {
            version: NEWSLY_TRANSCRIPT_VERSION,
            stream_generation: 0,
            messages: Vec::new(),
        }
    }
}

impl NewslyTranscript {
    /// Verify the durable transcript version and message shape.
    ///
    /// # Errors
    ///
    /// Returns an error for an unsupported version or an empty message.
    pub fn validate(&self) -> Result<(), TranscriptError> {
        if self.version != NEWSLY_TRANSCRIPT_VERSION {
            return Err(TranscriptError::UnsupportedVersion(self.version));
        }
        for (index, message) in self.messages.iter().enumerate() {
            if message.parts.is_empty() {
                return Err(TranscriptError::EmptyMessage(index));
            }
        }
        Ok(())
    }

    /// Convert the frozen `PydanticAI` history representation into the Newsly-owned transcript.
    ///
    /// # Errors
    ///
    /// Returns an error when the legacy object has an unsupported version or malformed messages,
    /// parts, or timestamps.
    pub fn from_legacy_pydantic_ai(value: &Value) -> Result<Self, LegacyHistoryError> {
        let root = value
            .as_object()
            .ok_or(LegacyHistoryError::ExpectedObject)?;
        let version = root.get("version").and_then(Value::as_u64).unwrap_or(1);
        if version != 1 {
            return Err(LegacyHistoryError::UnsupportedVersion(version));
        }
        let stream_generation = root
            .get("stream_generation")
            .and_then(Value::as_u64)
            .unwrap_or_default();
        let messages = root
            .get("messages")
            .and_then(Value::as_array)
            .ok_or(LegacyHistoryError::MissingMessages)?;
        let mut converted = Vec::with_capacity(messages.len());
        for (message_index, message) in messages.iter().enumerate() {
            converted.push(convert_legacy_message(message, message_index)?);
        }
        let transcript = Self {
            version: NEWSLY_TRANSCRIPT_VERSION,
            stream_generation,
            messages: converted,
        };
        transcript.validate()?;
        Ok(transcript)
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct NewslyMessage {
    pub id: Option<Uuid>,
    pub role: MessageRole,
    pub parts: Vec<MessagePart>,
    pub created_at: DateTime<Utc>,
    pub run_id: Option<String>,
    pub provider: Option<String>,
    pub model: Option<String>,
    pub finish_reason: Option<TranscriptFinishReason>,
    #[serde(default)]
    pub usage: ProviderUsage,
    #[serde(default)]
    pub metadata: Map<String, Value>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum MessageRole {
    System,
    User,
    Assistant,
    Tool,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(tag = "direction", content = "part", rename_all = "snake_case")]
pub enum MessagePart {
    Request(RequestPart),
    Assistant(AssistantPart),
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum RequestPart {
    Text {
        text: String,
    },
    ToolResult {
        tool_call_id: String,
        #[serde(default)]
        provider_call_id: Option<String>,
        #[serde(default)]
        provider_item_id: Option<String>,
        tool_name: String,
        content: Value,
        is_error: bool,
    },
    Retry {
        message: String,
        tool_call_id: Option<String>,
    },
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum AssistantPart {
    Text {
        text: String,
    },
    ToolCall {
        tool_call_id: String,
        #[serde(default)]
        provider_call_id: Option<String>,
        #[serde(default)]
        provider_item_id: Option<String>,
        tool_name: String,
        arguments: Value,
        #[serde(default)]
        signature: Option<String>,
        #[serde(default)]
        additional_params: Option<Value>,
    },
    Reasoning {
        /// Provider-owned item identity required when replaying reasoning-linked tool calls.
        /// Older Newsly and `PydanticAI` transcripts omit it, so absence remains wire-compatible.
        #[serde(default)]
        provider_item_id: Option<String>,
        /// The provider-neutral reasoning block variant. `None` decodes version-one transcripts
        /// written before this discriminator existed and is inferred from the populated fields.
        #[serde(default)]
        content_kind: Option<ReasoningContentKind>,
        text: Option<String>,
        signature: Option<String>,
        encrypted_content: Option<String>,
    },
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum ReasoningContentKind {
    Text,
    Encrypted,
    Redacted,
    Summary,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum TranscriptFinishReason {
    Stop,
    Length,
    ToolCall,
    ContentFilter,
    Error,
    Unknown,
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct ProviderUsage {
    pub request_count: u64,
    pub input_tokens: u64,
    pub output_tokens: u64,
    pub cached_input_tokens: u64,
    pub cache_write_tokens: u64,
    pub reasoning_tokens: u64,
    pub input_audio_tokens: u64,
    pub output_audio_tokens: u64,
}

impl ProviderUsage {
    pub fn add_assign(&mut self, other: &Self) {
        self.request_count = self.request_count.saturating_add(other.request_count);
        self.input_tokens = self.input_tokens.saturating_add(other.input_tokens);
        self.output_tokens = self.output_tokens.saturating_add(other.output_tokens);
        self.cached_input_tokens = self
            .cached_input_tokens
            .saturating_add(other.cached_input_tokens);
        self.cache_write_tokens = self
            .cache_write_tokens
            .saturating_add(other.cache_write_tokens);
        self.reasoning_tokens = self.reasoning_tokens.saturating_add(other.reasoning_tokens);
        self.input_audio_tokens = self
            .input_audio_tokens
            .saturating_add(other.input_audio_tokens);
        self.output_audio_tokens = self
            .output_audio_tokens
            .saturating_add(other.output_audio_tokens);
    }
}

fn convert_legacy_message(
    value: &Value,
    message_index: usize,
) -> Result<NewslyMessage, LegacyHistoryError> {
    let message = value
        .as_object()
        .ok_or(LegacyHistoryError::InvalidMessage(message_index))?;
    let kind = string_field(message, "kind", message_index)?;
    let role = match kind {
        "request" => legacy_request_role(message, message_index)?,
        "response" => MessageRole::Assistant,
        other => {
            return Err(LegacyHistoryError::UnknownMessageKind {
                message_index,
                kind: other.to_owned(),
            });
        }
    };
    let parts = message
        .get("parts")
        .and_then(Value::as_array)
        .ok_or(LegacyHistoryError::MissingParts(message_index))?
        .iter()
        .enumerate()
        .map(|(part_index, part)| convert_legacy_part(part, message_index, part_index))
        .collect::<Result<Vec<_>, _>>()?;
    let created_at = parse_timestamp(message.get("timestamp"), message_index)?;
    let usage = convert_legacy_usage(message.get("usage"));
    Ok(NewslyMessage {
        id: None,
        role,
        parts,
        created_at,
        run_id: optional_string(message.get("run_id")),
        provider: optional_string(message.get("provider_name")),
        model: optional_string(message.get("model_name")),
        finish_reason: optional_string(message.get("finish_reason"))
            .as_deref()
            .map(finish_reason),
        usage,
        metadata: message
            .get("metadata")
            .and_then(Value::as_object)
            .cloned()
            .unwrap_or_default(),
    })
}

fn legacy_request_role(
    message: &Map<String, Value>,
    message_index: usize,
) -> Result<MessageRole, LegacyHistoryError> {
    let parts = message
        .get("parts")
        .and_then(Value::as_array)
        .ok_or(LegacyHistoryError::MissingParts(message_index))?;
    if parts.iter().all(|part| {
        part.get("part_kind")
            .and_then(Value::as_str)
            .is_some_and(|kind| kind == "tool-return" || kind == "retry-prompt")
    }) {
        Ok(MessageRole::Tool)
    } else if parts.iter().all(|part| {
        part.get("part_kind")
            .and_then(Value::as_str)
            .is_some_and(|kind| kind == "system-prompt")
    }) {
        Ok(MessageRole::System)
    } else {
        Ok(MessageRole::User)
    }
}

fn convert_legacy_part(
    value: &Value,
    message_index: usize,
    part_index: usize,
) -> Result<MessagePart, LegacyHistoryError> {
    let part = value.as_object().ok_or(LegacyHistoryError::InvalidPart {
        message_index,
        part_index,
    })?;
    let kind =
        part.get("part_kind")
            .and_then(Value::as_str)
            .ok_or(LegacyHistoryError::InvalidPart {
                message_index,
                part_index,
            })?;
    match kind {
        "system-prompt" | "user-prompt" => Ok(MessagePart::Request(RequestPart::Text {
            text: content_as_text(part.get("content")),
        })),
        "tool-return" => Ok(MessagePart::Request(RequestPart::ToolResult {
            tool_call_id: optional_string(part.get("tool_call_id")).unwrap_or_default(),
            provider_call_id: optional_string(part.get("tool_call_id")),
            provider_item_id: None,
            tool_name: optional_string(part.get("tool_name")).unwrap_or_default(),
            content: part.get("content").cloned().unwrap_or(Value::Null),
            is_error: false,
        })),
        "retry-prompt" => Ok(MessagePart::Request(RequestPart::Retry {
            message: content_as_text(part.get("content")),
            tool_call_id: optional_string(part.get("tool_call_id")),
        })),
        "text" => Ok(MessagePart::Assistant(AssistantPart::Text {
            text: content_as_text(part.get("content")),
        })),
        "tool-call" => Ok(MessagePart::Assistant(AssistantPart::ToolCall {
            tool_call_id: optional_string(part.get("tool_call_id")).unwrap_or_default(),
            provider_call_id: optional_string(part.get("tool_call_id")),
            provider_item_id: None,
            tool_name: optional_string(part.get("tool_name")).unwrap_or_default(),
            arguments: part.get("args").cloned().unwrap_or(Value::Null),
            signature: optional_string(part.get("signature")),
            additional_params: part.get("metadata").cloned(),
        })),
        "thinking" => Ok(MessagePart::Assistant(AssistantPart::Reasoning {
            provider_item_id: optional_string(part.get("id")),
            content_kind: None,
            text: optional_string(part.get("content")),
            signature: optional_string(part.get("signature")),
            encrypted_content: optional_string(part.get("encrypted_content")),
        })),
        other => Err(LegacyHistoryError::UnknownPartKind {
            message_index,
            part_index,
            kind: other.to_owned(),
        }),
    }
}

fn string_field<'a>(
    object: &'a Map<String, Value>,
    field: &'static str,
    message_index: usize,
) -> Result<&'a str, LegacyHistoryError> {
    object
        .get(field)
        .and_then(Value::as_str)
        .ok_or(LegacyHistoryError::MissingMessageField {
            message_index,
            field,
        })
}

fn parse_timestamp(
    value: Option<&Value>,
    message_index: usize,
) -> Result<DateTime<Utc>, LegacyHistoryError> {
    value
        .and_then(Value::as_str)
        .ok_or(LegacyHistoryError::InvalidTimestamp(message_index))?
        .parse()
        .map_err(|_| LegacyHistoryError::InvalidTimestamp(message_index))
}

fn optional_string(value: Option<&Value>) -> Option<String> {
    value.and_then(Value::as_str).map(str::to_owned)
}

fn content_as_text(value: Option<&Value>) -> String {
    match value {
        Some(Value::String(text)) => text.clone(),
        Some(value) => serde_json::to_string(value).unwrap_or_default(),
        None => String::new(),
    }
}

fn finish_reason(value: &str) -> TranscriptFinishReason {
    match value {
        "stop" => TranscriptFinishReason::Stop,
        "length" => TranscriptFinishReason::Length,
        "tool_call" | "tool_calls" => TranscriptFinishReason::ToolCall,
        "content_filter" => TranscriptFinishReason::ContentFilter,
        "error" => TranscriptFinishReason::Error,
        _ => TranscriptFinishReason::Unknown,
    }
}

fn convert_legacy_usage(value: Option<&Value>) -> ProviderUsage {
    let usage = value.and_then(Value::as_object);
    let field = |name: &str| {
        usage
            .and_then(|object| object.get(name))
            .and_then(Value::as_u64)
            .unwrap_or_default()
    };
    ProviderUsage {
        request_count: u64::from(usage.is_some()),
        input_tokens: field("input_tokens"),
        output_tokens: field("output_tokens"),
        cached_input_tokens: field("cache_read_tokens"),
        cache_write_tokens: field("cache_write_tokens"),
        reasoning_tokens: usage
            .and_then(|object| object.get("details"))
            .and_then(Value::as_object)
            .and_then(|details| details.get("reasoning_tokens"))
            .and_then(Value::as_u64)
            .unwrap_or_default(),
        input_audio_tokens: field("input_audio_tokens"),
        output_audio_tokens: field("output_audio_tokens"),
    }
}

#[derive(Debug, Error)]
pub enum TranscriptError {
    #[error("unsupported Newsly transcript version {0}")]
    UnsupportedVersion(u16),
    #[error("transcript message {0} has no parts")]
    EmptyMessage(usize),
}

#[derive(Debug, Error)]
pub enum LegacyHistoryError {
    #[error("legacy transcript root must be an object")]
    ExpectedObject,
    #[error("unsupported legacy transcript version {0}")]
    UnsupportedVersion(u64),
    #[error("legacy transcript has no messages array")]
    MissingMessages,
    #[error("legacy message {0} is invalid")]
    InvalidMessage(usize),
    #[error("legacy message {message_index} is missing {field}")]
    MissingMessageField {
        message_index: usize,
        field: &'static str,
    },
    #[error("legacy message {0} has no parts array")]
    MissingParts(usize),
    #[error("legacy message {message_index} has unknown kind {kind}")]
    UnknownMessageKind { message_index: usize, kind: String },
    #[error("legacy message {message_index} part {part_index} is invalid")]
    InvalidPart {
        message_index: usize,
        part_index: usize,
    },
    #[error("legacy message {message_index} part {part_index} has unknown kind {kind}")]
    UnknownPartKind {
        message_index: usize,
        part_index: usize,
        kind: String,
    },
    #[error("legacy message {0} has an invalid timestamp")]
    InvalidTimestamp(usize),
    #[error(transparent)]
    Transcript(#[from] TranscriptError),
}

#[cfg(test)]
mod tests {
    use chrono::{TimeZone as _, Utc};
    use serde_json::{Map, json};

    use super::{
        AssistantPart, MessagePart, MessageRole, NewslyMessage, NewslyTranscript, ProviderUsage,
        RequestPart,
    };

    #[test]
    fn canonical_transcript_parts_have_an_unambiguous_versioned_wire_shape() {
        let created_at = Utc.with_ymd_and_hms(2026, 8, 30, 12, 0, 0).unwrap();
        let transcript = NewslyTranscript {
            messages: vec![
                NewslyMessage {
                    id: None,
                    role: MessageRole::User,
                    parts: vec![MessagePart::Request(RequestPart::Text {
                        text: "Hello".to_owned(),
                    })],
                    created_at,
                    run_id: None,
                    provider: None,
                    model: None,
                    finish_reason: None,
                    usage: ProviderUsage::default(),
                    metadata: Map::default(),
                },
                NewslyMessage {
                    id: None,
                    role: MessageRole::Assistant,
                    parts: vec![MessagePart::Assistant(AssistantPart::Text {
                        text: "World".to_owned(),
                    })],
                    created_at,
                    run_id: None,
                    provider: None,
                    model: None,
                    finish_reason: None,
                    usage: ProviderUsage::default(),
                    metadata: Map::default(),
                },
            ],
            ..NewslyTranscript::default()
        };

        let encoded = serde_json::to_value(&transcript).expect("transcript should serialize");
        assert_eq!(encoded["version"], json!(1));
        assert_eq!(
            encoded.pointer("/messages/0/parts/0"),
            Some(&json!({
                "direction": "request",
                "part": {"kind": "text", "text": "Hello"}
            }))
        );
        assert_eq!(
            encoded.pointer("/messages/1/parts/0"),
            Some(&json!({
                "direction": "assistant",
                "part": {"kind": "text", "text": "World"}
            }))
        );

        let decoded: NewslyTranscript =
            serde_json::from_value(encoded).expect("transcript should deserialize");
        assert_eq!(decoded, transcript);
    }
}
