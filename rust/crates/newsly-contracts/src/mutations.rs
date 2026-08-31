use chrono::{DateTime, Utc};
use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};
use utoipa::ToSchema;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
#[serde(rename_all = "snake_case")]
pub enum OperationStatus {
    Success,
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize, JsonSchema, ToSchema)]
#[serde(deny_unknown_fields)]
pub struct SubmitFeedbackRequest {
    pub message: String,
    #[serde(default = "default_feedback_source")]
    pub source: String,
    pub app_version: Option<String>,
    pub build_number: Option<String>,
    pub platform: Option<String>,
    pub os_version: Option<String>,
    pub device_model: Option<String>,
}

fn default_feedback_source() -> String {
    "ios_settings".to_owned()
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct SubmitFeedbackResponse {
    pub status: OperationStatus,
    pub feedback_id: i64,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
#[serde(rename_all = "snake_case")]
pub enum ContentInteractionType {
    Opened,
}

#[derive(Debug, Clone, PartialEq, Deserialize, JsonSchema, ToSchema)]
pub struct RecordContentInteractionRequest {
    pub interaction_id: String,
    pub content_id: i64,
    pub interaction_type: ContentInteractionType,
    #[schemars(with = "Option<String>")]
    #[schema(value_type = Option<String>, format = DateTime)]
    pub occurred_at: Option<DateTime<Utc>>,
    pub surface: Option<String>,
    #[serde(default)]
    pub context_data: Map<String, Value>,
}

impl ContentInteractionType {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Opened => "opened",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct RecordContentInteractionResponse {
    pub status: OperationStatus,
    pub recorded: bool,
    pub interaction_id: String,
    pub analytics_interaction_id: Option<i64>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct BulkMarkReadRequest {
    pub content_ids: Vec<i64>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct MarkReadResponse {
    pub status: OperationStatus,
    pub content_id: i64,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct MarkUnreadResponse {
    pub status: OperationStatus,
    pub content_id: i64,
    pub removed_records: u64,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct BulkMarkReadResponse {
    pub status: OperationStatus,
    pub marked_count: usize,
    pub failed_ids: Vec<i64>,
    pub total_requested: usize,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
#[serde(rename_all = "snake_case")]
pub enum KnowledgeMutationStatus {
    Success,
    NotFound,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct KnowledgeMutationResponse {
    pub status: KnowledgeMutationStatus,
    pub content_id: i64,
    pub is_saved_to_knowledge: bool,
    pub message: String,
}
