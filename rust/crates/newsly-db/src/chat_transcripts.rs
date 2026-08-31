use std::collections::{BTreeMap, BTreeSet};

use chrono::{DateTime, NaiveDateTime, Utc};
use newsly_agent_runtime::{
    AssistantPart, MessagePart, MessageRole, NewslyMessage, NewslyTranscript, ProviderUsage,
    RequestPart,
};
use serde_json::{Map, Value};
use thiserror::Error;

const SEARCH_TOOL_NAMES: [&str; 2] = ["exa_web_search", "search_personal_library"];
const INTERNAL_PROMPT_SENTINELS: [&str; 4] = [
    "Use the provided session context below",
    "Provided reference context is available below",
    "You are starting a new conversation about the article described in your context",
    "Turn instructions:",
];

#[derive(Debug, Clone, PartialEq)]
pub(crate) struct DisplayMessageProjection {
    pub id: i64,
    pub source_message_id: i64,
    pub session_id: i64,
    pub role: &'static str,
    pub content: String,
    pub timestamp: DateTime<Utc>,
    pub display_type: &'static str,
    pub process_label: Option<String>,
    pub status: String,
    pub error: Option<String>,
    pub feed_options: Vec<Value>,
    pub council_candidates: Vec<Value>,
    pub active_council_child_session_id: Option<i64>,
}

#[derive(Debug, Clone, PartialEq, Default)]
pub(crate) struct RenderMetadataProjection {
    pub feed_options: Vec<Value>,
    pub council_candidates: Vec<Value>,
    pub active_council_child_session_id: Option<i64>,
}

impl RenderMetadataProjection {
    pub(crate) fn from_value(value: Option<&Value>) -> Self {
        let Some(object) = value.and_then(Value::as_object) else {
            return Self::default();
        };
        Self {
            feed_options: value_array(object.get("feed_options")),
            council_candidates: value_array(object.get("council_candidates")),
            active_council_child_session_id: object
                .get("active_council_child_session_id")
                .and_then(Value::as_i64)
                .filter(|value| *value > 0),
        }
    }
}

#[derive(Debug, Clone, PartialEq)]
pub(crate) struct StoredChatMessage {
    pub id: i64,
    pub session_id: i64,
    pub message_list: String,
    pub render_metadata: Option<Value>,
    pub created_at: NaiveDateTime,
    pub status: String,
    pub error: Option<String>,
}

pub(crate) fn processing_transcript(
    user_prompt: &str,
    created_at: DateTime<Utc>,
) -> NewslyTranscript {
    NewslyTranscript {
        stream_generation: 0,
        messages: vec![NewslyMessage {
            id: None,
            role: MessageRole::User,
            parts: vec![MessagePart::Request(RequestPart::Text {
                text: user_prompt.to_owned(),
            })],
            created_at,
            run_id: None,
            provider: None,
            model: None,
            finish_reason: None,
            usage: ProviderUsage::default(),
            metadata: Map::new(),
        }],
        ..NewslyTranscript::default()
    }
}

pub(crate) fn decode_transcript(raw: &str) -> Result<NewslyTranscript, ChatTranscriptError> {
    let value: Value = serde_json::from_str(raw)?;
    if value.is_object()
        && let Ok(transcript) = serde_json::from_value::<NewslyTranscript>(value.clone())
    {
        transcript.validate()?;
        return Ok(transcript);
    }

    let legacy = if value.is_array() {
        serde_json::json!({"version": 1, "messages": value})
    } else {
        value
    };
    Ok(NewslyTranscript::from_legacy_pydantic_ai(&legacy)?)
}

pub(crate) fn display_messages(
    rows: &[StoredChatMessage],
    session_id: i64,
    active_feed_urls: &BTreeSet<String>,
) -> Vec<DisplayMessageProjection> {
    let mut messages = Vec::new();
    let mut display_id = 0_i64;
    for row in rows {
        let transcript = match decode_transcript(&row.message_list) {
            Ok(transcript) => transcript,
            Err(error) => {
                tracing::warn!(
                    message_id = row.id,
                    error = %error,
                    "skipping malformed chat transcript row"
                );
                continue;
            }
        };
        let render_metadata = RenderMetadataProjection::from_value(row.render_metadata.as_ref());
        let timestamp = row.created_at.and_utc();
        let mut user_text_emitted = false;
        let mut assistant_responses = Vec::new();
        let mut tool_names = Vec::new();

        for message in &transcript.messages {
            match message.role {
                MessageRole::User if !user_text_emitted => {
                    for part in &message.parts {
                        let MessagePart::Request(RequestPart::Text { text }) = part else {
                            continue;
                        };
                        let Some(visible_text) = visible_user_prompt(text) else {
                            continue;
                        };
                        user_text_emitted = true;
                        display_id += 1;
                        messages.push(DisplayMessageProjection {
                            id: display_id,
                            source_message_id: row.id,
                            session_id,
                            role: "user",
                            content: visible_text,
                            timestamp,
                            display_type: "message",
                            process_label: None,
                            status: row.status.clone(),
                            error: row.error.clone(),
                            feed_options: Vec::new(),
                            council_candidates: Vec::new(),
                            active_council_child_session_id: None,
                        });
                        break;
                    }
                }
                MessageRole::Assistant => {
                    let mut response_parts = Vec::new();
                    for part in &message.parts {
                        match part {
                            MessagePart::Assistant(AssistantPart::Text { text })
                                if !text.is_empty() =>
                            {
                                response_parts.push(text.clone());
                            }
                            MessagePart::Assistant(AssistantPart::ToolCall {
                                tool_name, ..
                            }) => tool_names.push(tool_name.clone()),
                            _ => {}
                        }
                    }
                    if !response_parts.is_empty() {
                        assistant_responses.push(response_parts.join("\n\n"));
                    }
                }
                _ => {}
            }
        }

        let tool_counts = count_tools(&tool_names);
        let has_intermediate_text = assistant_responses.len() > 1;
        if let Some(label) = process_summary_label(&tool_counts, has_intermediate_text) {
            display_id += 1;
            messages.push(DisplayMessageProjection {
                id: display_id,
                source_message_id: row.id,
                session_id,
                role: "tool",
                content: process_summary_detail(&tool_counts, has_intermediate_text)
                    .unwrap_or_else(|| label.clone()),
                timestamp,
                display_type: "process_summary",
                process_label: Some(label),
                status: row.status.clone(),
                error: row.error.clone(),
                feed_options: Vec::new(),
                council_candidates: Vec::new(),
                active_council_child_session_id: None,
            });
        }

        if let Some(content) = assistant_responses.pop() {
            display_id += 1;
            messages.push(DisplayMessageProjection {
                id: display_id,
                source_message_id: row.id,
                session_id,
                role: "assistant",
                content,
                timestamp,
                display_type: "message",
                process_label: None,
                status: row.status.clone(),
                error: row.error.clone(),
                feed_options: overlay_subscription_state(
                    render_metadata.feed_options,
                    active_feed_urls,
                ),
                council_candidates: render_metadata.council_candidates,
                active_council_child_session_id: render_metadata.active_council_child_session_id,
            });
        }
    }
    messages
}

pub(crate) fn latest_message_preview(raw: &str) -> Option<(String, &'static str)> {
    let transcript = decode_transcript(raw).ok()?;
    for message in transcript.messages.iter().rev() {
        match message.role {
            MessageRole::Assistant => {
                for part in message.parts.iter().rev() {
                    if let MessagePart::Assistant(AssistantPart::Text { text }) = part
                        && !text.is_empty()
                    {
                        return Some((truncate_chars(text, 200), "assistant"));
                    }
                }
            }
            MessageRole::User => {
                for part in message.parts.iter().rev() {
                    if let MessagePart::Request(RequestPart::Text { text }) = part
                        && let Some(visible) = visible_user_prompt(text)
                    {
                        return Some((truncate_chars(&visible, 200), "user"));
                    }
                }
            }
            _ => {}
        }
    }
    None
}

pub(crate) fn latest_assistant_text(raw: &str) -> Result<String, ChatTranscriptError> {
    let transcript = decode_transcript(raw)?;
    for message in transcript.messages.iter().rev() {
        if message.role != MessageRole::Assistant {
            continue;
        }
        for part in message.parts.iter().rev() {
            if let MessagePart::Assistant(AssistantPart::Text { text }) = part
                && !text.is_empty()
            {
                return Ok(text.clone());
            }
        }
    }
    Err(ChatTranscriptError::MissingAssistantText)
}

pub(crate) fn overlay_subscription_state(
    options: Vec<Value>,
    active_feed_urls: &BTreeSet<String>,
) -> Vec<Value> {
    options
        .into_iter()
        .map(|mut option| {
            let is_subscribed = option
                .get("feed_url")
                .and_then(Value::as_str)
                .is_some_and(|url| active_feed_urls.contains(&crate::canonicalize_feed_url(url)));
            if let Some(object) = option.as_object_mut() {
                object.insert("is_subscribed".to_owned(), Value::Bool(is_subscribed));
            }
            option
        })
        .collect()
}

pub(crate) fn visible_user_prompt(text: &str) -> Option<String> {
    let text = text.trim();
    if text.is_empty() {
        return None;
    }
    if let Some((_, request)) = text.split_once("User request:\n") {
        let mut request = request;
        for suffix in [
            "\n\nCurrent context:",
            "\n\nSession Context:",
            "\n\nArticle Context:",
        ] {
            if let Some((visible, _)) = request.split_once(suffix) {
                request = visible;
                break;
            }
        }
        let request = request.trim();
        return (!request.is_empty()).then(|| request.to_owned());
    }
    if INTERNAL_PROMPT_SENTINELS
        .iter()
        .any(|sentinel| text.contains(sentinel))
    {
        return None;
    }
    Some(text.to_owned())
}

fn value_array(value: Option<&Value>) -> Vec<Value> {
    value.and_then(Value::as_array).cloned().unwrap_or_default()
}

fn count_tools(names: &[String]) -> BTreeMap<String, usize> {
    let mut counts = BTreeMap::new();
    for name in names {
        let name = name.trim();
        if !name.is_empty() {
            *counts.entry(name.to_owned()).or_insert(0) += 1;
        }
    }
    counts
}

fn process_summary_label(
    tool_counts: &BTreeMap<String, usize>,
    has_intermediate_text: bool,
) -> Option<String> {
    let call_count = tool_counts.values().sum::<usize>();
    if call_count > 0 {
        let tool_label = if call_count == 1 { "tool" } else { "tools" };
        let searched = tool_counts.keys().any(|name| {
            SEARCH_TOOL_NAMES
                .iter()
                .any(|search_name| name.eq_ignore_ascii_case(search_name))
        });
        return Some(if searched {
            format!("Thinking • Executed {call_count} {tool_label} and reviewed sources")
        } else {
            format!("Thinking • Executed {call_count} {tool_label} and reviewed results")
        });
    }
    has_intermediate_text.then(|| "Thinking • Considered the request".to_owned())
}

fn process_summary_detail(
    tool_counts: &BTreeMap<String, usize>,
    has_intermediate_text: bool,
) -> Option<String> {
    if !tool_counts.is_empty() {
        let total = tool_counts.values().sum::<usize>();
        let label = if total == 1 {
            "tool call"
        } else {
            "tool calls"
        };
        let mut lines = vec![format!("Executed {total} {label}:")];
        for (name, count) in tool_counts {
            let suffix = if *count > 1 {
                format!(" x{count}")
            } else {
                String::new()
            };
            lines.push(format!("• {name}{suffix}"));
        }
        return Some(lines.join("\n"));
    }
    has_intermediate_text
        .then(|| "Prepared intermediate context before writing the final answer.".to_owned())
}

fn truncate_chars(value: &str, max_chars: usize) -> String {
    value.chars().take(max_chars).collect()
}

#[derive(Debug, Error)]
pub enum ChatTranscriptError {
    #[error("chat transcript JSON is invalid")]
    Json(#[from] serde_json::Error),
    #[error("Newsly chat transcript is invalid")]
    Transcript(#[from] newsly_agent_runtime::TranscriptError),
    #[error("legacy chat transcript is invalid")]
    Legacy(#[from] newsly_agent_runtime::LegacyHistoryError),
    #[error("completed chat transcript has no assistant text")]
    MissingAssistantText,
}

#[cfg(test)]
mod tests {
    use chrono::TimeZone as _;

    use super::{decode_transcript, latest_assistant_text, processing_transcript};

    #[test]
    fn accepts_newsly_and_legacy_transcripts() {
        let now = chrono::Utc.with_ymd_and_hms(2026, 8, 30, 12, 0, 0).unwrap();
        let transcript = processing_transcript("Hello", now);
        let encoded = serde_json::to_string(&transcript).unwrap();
        let encoded_value: serde_json::Value = serde_json::from_str(&encoded).unwrap();
        assert_eq!(
            encoded_value.pointer("/messages/0/parts/0"),
            Some(&serde_json::json!({
                "direction": "request",
                "part": {"kind": "text", "text": "Hello"}
            }))
        );
        assert_eq!(decode_transcript(&encoded).unwrap(), transcript);

        let legacy = serde_json::json!([{
            "kind": "response",
            "parts": [{"part_kind": "text", "content": "World"}],
            "timestamp": "2026-08-30T12:00:00Z"
        }]);
        assert_eq!(latest_assistant_text(&legacy.to_string()).unwrap(), "World");
    }
}
