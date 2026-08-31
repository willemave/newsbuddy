use chrono::{DateTime, Utc};
use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use utoipa::ToSchema;

use crate::PaginationMetadata;

macro_rules! string_enum {
    ($name:ident { $($variant:ident => $value:literal),+ $(,)? }) => {
        #[derive(
            Debug,
            Clone,
            Copy,
            PartialEq,
            Eq,
            Serialize,
            Deserialize,
            JsonSchema,
            ToSchema,
        )]
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
    };
}

string_enum!(LlmProvider {
    Openai => "openai",
    Anthropic => "anthropic",
    Openrouter => "openrouter",
    DeepResearch => "deep_research",
});

string_enum!(MessageProcessingStatus {
    Processing => "processing",
    Completed => "completed",
    Failed => "failed",
});

string_enum!(ChatMessageRole {
    User => "user",
    Assistant => "assistant",
    System => "system",
    Tool => "tool",
});

string_enum!(ChatMessageDisplayType {
    Message => "message",
    ProcessSummary => "process_summary",
});

string_enum!(FeedFormat {
    Rss => "rss",
    Atom => "atom",
});

string_enum!(FeedType {
    Atom => "atom",
    Substack => "substack",
    PodcastRss => "podcast_rss",
    Youtube => "youtube",
});

const fn default_message_status() -> MessageProcessingStatus {
    MessageProcessingStatus::Completed
}

const fn default_processing_status() -> MessageProcessingStatus {
    MessageProcessingStatus::Processing
}

const fn default_display_type() -> ChatMessageDisplayType {
    ChatMessageDisplayType::Message
}

const fn default_feed_format() -> FeedFormat {
    FeedFormat::Rss
}

const fn default_true() -> bool {
    true
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct CreateChatSessionRequest {
    pub content_id: Option<i64>,
    pub news_item_id: Option<i64>,
    #[schemars(length(max = 500))]
    #[schema(max_length = 500)]
    pub topic: Option<String>,
    pub llm_provider: Option<LlmProvider>,
    #[schemars(length(max = 100))]
    #[schema(max_length = 100)]
    pub llm_model_hint: Option<String>,
    #[schemars(length(max = 2_000))]
    #[schema(max_length = 2_000)]
    pub initial_message: Option<String>,
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct UpdateChatSessionRequest {
    pub llm_provider: Option<LlmProvider>,
    #[schemars(length(max = 100))]
    #[schema(max_length = 100)]
    pub llm_model_hint: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct SendChatMessageRequest {
    #[schemars(length(min = 1, max = 10_000))]
    #[schema(min_length = 1, max_length = 10_000)]
    pub message: String,
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct AssistantScreenContextDto {
    #[serde(default = "default_screen_type")]
    #[schemars(length(max = 64))]
    #[schema(max_length = 64, default = "unknown")]
    pub screen_type: String,
    #[schemars(length(max = 200))]
    #[schema(max_length = 200)]
    pub screen_title: Option<String>,
    #[schemars(range(min = 1))]
    #[schema(minimum = 1)]
    pub content_id: Option<i64>,
    #[schemars(range(min = 1))]
    #[schema(minimum = 1)]
    pub news_item_id: Option<i64>,
    #[serde(default)]
    #[schemars(length(max = 12))]
    #[schema(max_items = 12)]
    pub visible_content_ids: Vec<i64>,
    #[serde(default)]
    #[schemars(length(max = 12))]
    #[schema(max_items = 12)]
    pub visible_news_item_ids: Vec<i64>,
    #[schemars(length(max = 200))]
    #[schema(max_length = 200)]
    pub selected_topic: Option<String>,
    #[schemars(length(max = 200))]
    #[schema(max_length = 200)]
    pub query: Option<String>,
    #[schemars(length(max = 1_500))]
    #[schema(max_length = 1_500)]
    pub note: Option<String>,
    #[schemars(length(max = 100))]
    #[schema(max_length = 100)]
    pub assistant_action: Option<String>,
}

fn default_screen_type() -> String {
    "unknown".to_owned()
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct AssistantTurnRequest {
    #[schemars(length(min = 1, max = 10_000))]
    #[schema(min_length = 1, max_length = 10_000)]
    pub message: String,
    #[schemars(range(min = 1))]
    #[schema(minimum = 1)]
    pub session_id: Option<i64>,
    #[serde(default)]
    pub screen_context: AssistantScreenContextDto,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct AssistantFeedOption {
    #[schemars(length(min = 8, max = 40))]
    #[schema(min_length = 8, max_length = 40)]
    pub id: String,
    #[schemars(length(min = 1, max = 300))]
    #[schema(min_length = 1, max_length = 300)]
    pub title: String,
    #[schemars(length(min = 1, max = 2_048))]
    #[schema(min_length = 1, max_length = 2_048)]
    pub site_url: String,
    #[schemars(length(min = 1, max = 2_048))]
    #[schema(min_length = 1, max_length = 2_048)]
    pub feed_url: String,
    pub feed_type: FeedType,
    #[serde(default = "default_feed_format")]
    pub feed_format: FeedFormat,
    #[schemars(length(max = 600))]
    #[schema(max_length = 600)]
    pub description: Option<String>,
    #[schemars(length(max = 600))]
    #[schema(max_length = 600)]
    pub rationale: Option<String>,
    #[schemars(length(max = 2_048))]
    #[schema(max_length = 2_048)]
    pub evidence_url: Option<String>,
    #[serde(default)]
    pub is_subscribed: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct CouncilCandidate {
    #[schemars(length(min = 1, max = 50))]
    #[schema(min_length = 1, max_length = 50)]
    pub persona_id: String,
    #[schemars(length(min = 1, max = 80))]
    #[schema(min_length = 1, max_length = 80)]
    pub persona_name: String,
    #[schemars(range(min = 1))]
    #[schema(minimum = 1)]
    pub child_session_id: i64,
    #[schemars(length(min = 1))]
    #[schema(min_length = 1)]
    pub content: String,
    #[serde(default = "default_completed_status")]
    #[schemars(length(min = 1, max = 32))]
    #[schema(min_length = 1, max_length = 32, default = "completed")]
    pub status: String,
    #[schemars(range(min = 0, max = 3))]
    #[schema(minimum = 0, maximum = 3)]
    pub order: i32,
}

fn default_completed_status() -> String {
    "completed".to_owned()
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct ChatMessageDto {
    pub id: i64,
    pub source_message_id: Option<i64>,
    #[serde(default)]
    pub display_key: String,
    pub session_id: i64,
    pub role: ChatMessageRole,
    pub content: String,
    #[schemars(with = "String")]
    #[schema(value_type = String, format = DateTime)]
    pub timestamp: DateTime<Utc>,
    #[serde(default = "default_display_type")]
    pub display_type: ChatMessageDisplayType,
    pub process_label: Option<String>,
    #[serde(default = "default_message_status")]
    pub status: MessageProcessingStatus,
    pub error: Option<String>,
    #[serde(default)]
    pub feed_options: Vec<AssistantFeedOption>,
    #[serde(default)]
    pub council_candidates: Vec<CouncilCandidate>,
    pub active_council_child_session_id: Option<i64>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
// These flags are independent wire facts, not mutually exclusive state-machine states.
#[allow(clippy::struct_excessive_bools)]
pub struct ChatSessionSummaryDto {
    pub id: i64,
    #[schemars(required)]
    #[schema(required = true)]
    pub title: Option<String>,
    #[schemars(required)]
    #[schema(required = true)]
    pub content_id: Option<i64>,
    #[schemars(required)]
    #[schema(required = true)]
    pub news_item_id: Option<i64>,
    #[schemars(required)]
    #[schema(required = true)]
    pub session_type: Option<String>,
    #[schemars(required)]
    #[schema(required = true)]
    pub topic: Option<String>,
    pub llm_model: String,
    pub llm_provider: String,
    #[schemars(with = "String")]
    #[schema(value_type = String, format = DateTime)]
    pub created_at: DateTime<Utc>,
    #[schemars(with = "Option<String>")]
    #[schemars(required)]
    #[schema(value_type = Option<String>, format = DateTime, required = true)]
    pub updated_at: Option<DateTime<Utc>>,
    #[schemars(with = "Option<String>")]
    #[schemars(required)]
    #[schema(value_type = Option<String>, format = DateTime, required = true)]
    pub last_message_at: Option<DateTime<Utc>>,
    pub is_archived: bool,
    pub article_title: Option<String>,
    pub article_url: Option<String>,
    pub article_summary: Option<String>,
    pub article_source: Option<String>,
    pub article_image_url: Option<String>,
    pub article_thumbnail_url: Option<String>,
    #[serde(default)]
    pub has_pending_message: bool,
    #[serde(default)]
    pub is_waiting_for_content: bool,
    #[serde(default)]
    pub is_saved_to_knowledge: bool,
    #[serde(default = "default_true")]
    pub has_messages: bool,
    pub last_message_preview: Option<String>,
    pub last_message_role: Option<String>,
    #[serde(default)]
    pub council_mode: bool,
    pub active_child_session_id: Option<i64>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct ChatSessionDetailDto {
    pub session: ChatSessionSummaryDto,
    pub messages: Vec<ChatMessageDto>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct ChatSessionListResponse {
    pub sessions: Vec<ChatSessionSummaryDto>,
    pub meta: PaginationMetadata,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct CreateChatSessionResponse {
    pub session: ChatSessionSummaryDto,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct SendMessageResponse {
    pub session_id: i64,
    pub user_message: ChatMessageDto,
    pub message_id: i64,
    #[serde(default = "default_processing_status")]
    pub status: MessageProcessingStatus,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct AssistantTurnResponse {
    pub session: ChatSessionSummaryDto,
    pub user_message: ChatMessageDto,
    pub message_id: i64,
    #[serde(default = "default_processing_status")]
    pub status: MessageProcessingStatus,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct ChatToolProgressDto {
    pub tool_name: String,
    pub status: String,
    pub detail: Option<String>,
    #[schemars(with = "Option<String>")]
    #[schema(value_type = Option<String>, format = DateTime)]
    pub updated_at: Option<DateTime<Utc>>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct MessageStatusResponse {
    pub message_id: i64,
    pub status: MessageProcessingStatus,
    pub assistant_message: Option<ChatMessageDto>,
    pub partial_assistant_message: Option<ChatMessageDto>,
    #[schemars(range(min = 0))]
    #[schema(minimum = 0)]
    pub stream_generation: Option<i32>,
    #[schemars(range(min = 0))]
    #[schema(minimum = 0)]
    pub stream_revision: Option<i32>,
    pub tool_progress: Option<ChatToolProgressDto>,
    #[schemars(range(min = 0))]
    #[schema(minimum = 0)]
    pub tool_progress_revision: Option<i32>,
    pub error: Option<String>,
}
