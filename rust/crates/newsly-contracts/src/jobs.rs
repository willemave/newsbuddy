use chrono::{DateTime, Utc};
use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};
use utoipa::ToSchema;

/// Public status projection for one durable processing task.
///
/// This deliberately exposes only the stable job contract. Internal queue
/// payloads and failure diagnostics are not part of the polling boundary.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct JobStatusResponse {
    pub id: i64,
    pub task_type: String,
    pub status: String,
    pub queue_name: String,
    pub content_id: Option<i64>,
    #[serde(default)]
    pub payload: Map<String, Value>,
    #[serde(default)]
    pub retry_count: i32,
    #[schemars(with = "Option<String>")]
    #[schema(value_type = Option<String>, format = DateTime)]
    pub created_at: Option<DateTime<Utc>>,
    #[schemars(with = "Option<String>")]
    #[schema(value_type = Option<String>, format = DateTime)]
    pub started_at: Option<DateTime<Utc>>,
    #[schemars(with = "Option<String>")]
    #[schema(value_type = Option<String>, format = DateTime)]
    pub completed_at: Option<DateTime<Utc>>,
    pub error_message: Option<String>,
}
