use chrono::{DateTime, Utc};
use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use utoipa::ToSchema;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
#[serde(rename_all = "snake_case")]
pub enum CliLinkStatus {
    Pending,
    Approved,
    Claimed,
    Expired,
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct CliLinkStartRequest {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub device_name: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct CliLinkStartResponse {
    pub session_id: String,
    pub status: CliLinkStatus,
    pub poll_token: String,
    pub approve_url: String,
    #[schemars(with = "String")]
    #[schema(value_type = String, format = DateTime)]
    pub expires_at: DateTime<Utc>,
    #[serde(default = "default_poll_interval_seconds")]
    pub poll_interval_seconds: i32,
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize, JsonSchema, ToSchema)]
pub struct CliLinkApproveRequest {
    pub approve_token: String,
    pub device_name: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct CliLinkApproveResponse {
    pub session_id: String,
    pub status: CliLinkStatus,
    pub key_prefix: String,
    #[schemars(with = "String")]
    #[schema(value_type = String, format = DateTime)]
    pub expires_at: DateTime<Utc>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct CliLinkPollResponse {
    pub session_id: String,
    pub status: CliLinkStatus,
    #[schemars(with = "String")]
    #[schema(value_type = String, format = DateTime)]
    pub expires_at: DateTime<Utc>,
    pub api_key: Option<String>,
    pub key_prefix: Option<String>,
}

const fn default_poll_interval_seconds() -> i32 {
    2
}
