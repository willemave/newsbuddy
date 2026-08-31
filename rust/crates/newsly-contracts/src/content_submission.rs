use std::fmt::{self, Display, Formatter};
use std::str::FromStr;

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use utoipa::ToSchema;

macro_rules! string_enum {
    ($name:ident { $($variant:ident => $value:literal),+ $(,)? }) => {
        #[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
        #[serde(rename_all = "snake_case")]
        pub enum $name {
            $($variant),+
        }

        impl $name {
            pub const fn as_str(self) -> &'static str {
                match self {
                    $(Self::$variant => $value),+
                }
            }
        }

        impl Display for $name {
            fn fmt(&self, formatter: &mut Formatter<'_>) -> fmt::Result {
                formatter.write_str(self.as_str())
            }
        }

        impl FromStr for $name {
            type Err = UnknownContentValue;

            fn from_str(value: &str) -> Result<Self, Self::Err> {
                match value {
                    $($value => Ok(Self::$variant)),+,
                    _ => Err(UnknownContentValue {
                        kind: stringify!($name),
                        value: value.to_owned(),
                    }),
                }
            }
        }
    };
}

string_enum!(ContentType {
    Article => "article",
    Podcast => "podcast",
    News => "news",
    InsightReport => "insight_report",
    Unknown => "unknown",
});

string_enum!(ContentStatus {
    New => "new",
    Pending => "pending",
    Processing => "processing",
    AwaitingImage => "awaiting_image",
    Completed => "completed",
    Failed => "failed",
    Skipped => "skipped",
});

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
/// Request to submit a user-provided URL for processing.
#[allow(clippy::struct_excessive_bools)] // The public wire contract exposes independent actions.
pub struct SubmitContentRequest {
    /// URL to submit (HTTP/HTTPS only).
    pub url: String,
    /// Optional content-type hint. New records are still persisted as unknown until analysis.
    pub content_type: Option<ContentType>,
    /// Optional title supplied by the client or share sheet.
    #[schemars(length(max = 500))]
    #[schema(max_length = 500)]
    pub title: Option<String>,
    /// Optional platform hint, such as `spotify` or `substack`.
    #[schemars(length(max = 50))]
    #[schema(max_length = 50)]
    pub platform: Option<String>,
    /// Optional analysis instruction. `note` remains accepted as the legacy input alias.
    #[serde(alias = "note")]
    #[schemars(length(max = 4_000))]
    #[schema(max_length = 4_000)]
    pub instruction: Option<String>,
    /// Whether analysis should create content items for relevant discovered links.
    #[serde(default)]
    pub crawl_links: bool,
    /// Whether to detect and subscribe to a feed instead of saving the URL as inbox content.
    #[serde(default)]
    pub subscribe_to_feed: bool,
    /// Whether to mark the content read and start a dig-deeper chat when it is ready.
    #[serde(default)]
    pub share_and_chat: bool,
    /// Optional first user message for the share-and-chat session.
    #[schemars(length(max = 2_000))]
    #[schema(max_length = 2_000)]
    pub chat_initial_message: Option<String>,
    /// Whether to mark the content read and save it to the user's Knowledge library.
    #[serde(default)]
    pub save_to_knowledge_and_mark_read: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
/// Result of creating or reusing one submitted URL.
pub struct ContentSubmissionResponse {
    /// Created or reused Content identifier.
    pub content_id: i64,
    /// Current durable Content type.
    pub content_type: ContentType,
    /// Current durable processing status.
    pub status: ContentStatus,
    /// Normalized platform for an existing record; new submissions return null until analysis.
    pub platform: Option<String>,
    /// Whether the URL matched an existing Content row.
    #[serde(default)]
    pub already_exists: bool,
    /// Human-readable submission result.
    pub message: String,
    /// Reused or newly queued analysis task identifier, when analysis is needed.
    pub task_id: Option<i64>,
    /// Durable source attribution.
    pub source: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct UnknownContentValue {
    kind: &'static str,
    value: String,
}

impl Display for UnknownContentValue {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> fmt::Result {
        write!(formatter, "unknown {} value {:?}", self.kind, self.value)
    }
}

impl std::error::Error for UnknownContentValue {}

#[cfg(test)]
mod tests {
    use super::SubmitContentRequest;

    #[test]
    fn note_is_an_input_alias_for_instruction() {
        let request: SubmitContentRequest = serde_json::from_value(serde_json::json!({
            "url": "https://example.com/article",
            "note": "Follow every link"
        }))
        .unwrap();

        assert_eq!(request.instruction.as_deref(), Some("Follow every link"));
        assert!(!request.crawl_links);
        assert!(!request.subscribe_to_feed);
    }
}
