use std::collections::BTreeMap;

use chrono::{DateTime, Utc};
use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use utoipa::ToSchema;

#[derive(
    Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema,
)]
#[serde(rename_all = "snake_case")]
pub enum DiscussionMode {
    #[default]
    None,
    Comments,
    DiscussionList,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct DiscussionLinkResponse {
    pub url: String,
    #[serde(default = "default_link_source")]
    pub source: String,
    pub comment_id: Option<String>,
    pub group_label: Option<String>,
    pub title: Option<String>,
}

fn default_link_source() -> String {
    "unknown".to_owned()
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct DiscussionCommentResponse {
    pub comment_id: String,
    pub parent_id: Option<String>,
    pub author: Option<String>,
    pub text: String,
    pub compact_text: Option<String>,
    #[serde(default)]
    pub depth: i64,
    /// Provider-owned timestamp. Kept as text because upstream formats are heterogeneous.
    pub created_at: Option<String>,
    pub source_url: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct DiscussionItemResponse {
    pub title: String,
    pub url: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct DiscussionGroupResponse {
    pub label: String,
    #[serde(default)]
    pub items: Vec<DiscussionItemResponse>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct DiscussionSummaryTopicResponse {
    pub title: String,
    pub summary: String,
    pub stance: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct DiscussionSummaryLinkResponse {
    pub url: String,
    pub title: Option<String>,
    pub reason: Option<String>,
    pub source_comment_id: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct DiscussionSummaryCommentResponse {
    pub comment_id: Option<String>,
    pub author: Option<String>,
    pub text: String,
    pub reason: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct DiscussionSummaryResponse {
    pub overview: String,
    #[serde(default)]
    pub topics: Vec<DiscussionSummaryTopicResponse>,
    #[serde(default)]
    pub notable_links: Vec<DiscussionSummaryLinkResponse>,
    #[serde(default)]
    pub representative_comments: Vec<DiscussionSummaryCommentResponse>,
    pub external_discussion_url: Option<String>,
    /// Newsly-owned generation timestamp. Unlike provider comment dates, this is guaranteed UTC.
    #[schemars(with = "Option<String>")]
    #[schema(value_type = Option<String>, format = DateTime)]
    pub generated_at: Option<DateTime<Utc>>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct ContentDiscussionResponse {
    pub content_id: i64,
    pub status: String,
    #[serde(default)]
    pub mode: DiscussionMode,
    pub platform: Option<String>,
    pub source_url: Option<String>,
    pub discussion_url: Option<String>,
    /// Newsly-owned fetch timestamp, serialized as an RFC 3339 UTC value.
    #[schemars(with = "Option<String>")]
    #[schema(value_type = Option<String>, format = DateTime)]
    pub fetched_at: Option<DateTime<Utc>>,
    pub error_message: Option<String>,
    #[serde(default)]
    pub comments: Vec<DiscussionCommentResponse>,
    #[serde(default)]
    pub discussion_groups: Vec<DiscussionGroupResponse>,
    #[serde(default)]
    pub links: Vec<DiscussionLinkResponse>,
    pub summary: Option<DiscussionSummaryResponse>,
    pub comment_count: Option<i64>,
    /// Platform-specific metrics are intentionally heterogeneous at this boundary.
    #[serde(default)]
    pub stats: BTreeMap<String, Value>,
}
