use chrono::{DateTime, Utc};
use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use utoipa::ToSchema;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct ContentBodyResponse {
    pub content_id: i64,
    pub variant: String,
    pub kind: String,
    pub format: String,
    pub text: String,
    /// Newsly-owned pointer timestamp, always serialized in UTC when present.
    #[schemars(with = "Option<String>")]
    #[schema(value_type = Option<String>, format = DateTime)]
    pub updated_at: Option<DateTime<Utc>>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct ChatGptUrlResponse {
    pub chat_url: String,
    pub truncated: bool,
}
