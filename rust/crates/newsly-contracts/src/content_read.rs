use std::collections::BTreeMap;

use chrono::{DateTime, Utc};
use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};
use utoipa::ToSchema;

use crate::{ContentStatus, ContentType, SummaryVersion};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
#[serde(rename_all = "snake_case")]
pub enum ContentClassification {
    ToRead,
    Skip,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
#[serde(rename_all = "snake_case")]
pub enum SummaryKind {
    LongStructured,
    LongInterleaved,
    LongBullets,
    LongEditorialNarrative,
    ShortNews,
    LongformArtifact,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
#[serde(rename_all = "snake_case")]
pub enum SavedSource {
    Knowledge,
    XBookmark,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct PaginationMetadata {
    pub next_cursor: Option<String>,
    #[serde(default)]
    pub has_more: bool,
    pub page_size: usize,
    pub total: Option<i64>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct DetectedFeed {
    pub url: String,
    #[serde(rename = "type")]
    pub feed_type: String,
    pub title: Option<String>,
    #[serde(default = "default_feed_format")]
    pub format: String,
}

fn default_feed_format() -> String {
    "rss".to_owned()
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct ContentSummaryBulletPoint {
    pub text: String,
    pub category: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct ContentSummaryQuote {
    pub text: String,
    pub context: Option<String>,
    pub attribution: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct ContentSummaryResponse {
    pub id: i64,
    pub content_type: ContentType,
    pub url: String,
    pub source_url: Option<String>,
    pub discussion_url: Option<String>,
    pub title: Option<String>,
    pub source: Option<String>,
    pub platform: Option<String>,
    pub status: ContentStatus,
    pub short_summary: Option<String>,
    #[schemars(with = "String")]
    #[schema(value_type = String, format = DateTime)]
    pub created_at: DateTime<Utc>,
    #[schemars(with = "Option<String>")]
    #[schema(value_type = Option<String>, format = DateTime)]
    pub processed_at: Option<DateTime<Utc>>,
    pub classification: Option<ContentClassification>,
    #[schemars(with = "Option<String>")]
    #[schema(value_type = Option<String>, format = DateTime)]
    pub publication_date: Option<DateTime<Utc>>,
    #[serde(default)]
    pub is_read: bool,
    #[serde(default)]
    pub is_saved_to_knowledge: bool,
    #[schemars(with = "Option<String>")]
    #[schema(value_type = Option<String>, format = DateTime)]
    pub knowledge_saved_at: Option<DateTime<Utc>>,
    pub news_article_url: Option<String>,
    pub news_discussion_url: Option<String>,
    pub news_key_points: Option<Vec<String>>,
    pub news_summary: Option<String>,
    pub user_status: Option<String>,
    pub image_url: Option<String>,
    pub thumbnail_url: Option<String>,
    pub primary_topic: Option<String>,
    pub top_comment: Option<BTreeMap<String, String>>,
    pub comment_count: Option<i64>,
    pub feed_preview: Option<Map<String, Value>>,
    pub artifact_type: Option<String>,
    pub preview_bullets: Option<Vec<String>>,
    pub reason_to_read: Option<String>,
    pub key_takeaway: Option<String>,
    pub saved_source: Option<SavedSource>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct ContentListResponse {
    pub contents: Vec<ContentSummaryResponse>,
    pub available_dates: Vec<String>,
    pub content_types: Vec<ContentType>,
    pub meta: PaginationMetadata,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema, ToSchema)]
// These flags describe independent content capabilities and per-user facts on the public wire.
#[allow(clippy::struct_excessive_bools)]
pub struct ContentDetailResponse {
    pub id: i64,
    pub content_type: ContentType,
    pub url: String,
    pub source_url: Option<String>,
    pub discussion_url: Option<String>,
    pub title: Option<String>,
    pub display_title: String,
    pub source: Option<String>,
    pub status: ContentStatus,
    pub error_message: Option<String>,
    pub retry_count: i32,
    pub metadata: Map<String, Value>,
    #[schemars(with = "String")]
    #[schema(value_type = String, format = DateTime)]
    pub created_at: DateTime<Utc>,
    #[schemars(with = "Option<String>")]
    #[schema(value_type = Option<String>, format = DateTime)]
    pub updated_at: Option<DateTime<Utc>>,
    #[schemars(with = "Option<String>")]
    #[schema(value_type = Option<String>, format = DateTime)]
    pub processed_at: Option<DateTime<Utc>>,
    pub checked_out_by: Option<String>,
    #[schemars(with = "Option<String>")]
    #[schema(value_type = Option<String>, format = DateTime)]
    pub checked_out_at: Option<DateTime<Utc>>,
    #[schemars(with = "Option<String>")]
    #[schema(value_type = Option<String>, format = DateTime)]
    pub publication_date: Option<DateTime<Utc>>,
    #[serde(default)]
    pub is_read: bool,
    #[serde(default)]
    pub is_saved_to_knowledge: bool,
    pub summary: Option<String>,
    pub short_summary: Option<String>,
    pub summary_kind: Option<SummaryKind>,
    pub summary_version: Option<SummaryVersion>,
    pub structured_summary: Option<Map<String, Value>>,
    pub longform_artifact: Option<Map<String, Value>>,
    pub feed_preview: Option<Map<String, Value>>,
    pub artifact_type: Option<String>,
    pub preview_bullets: Option<Vec<String>>,
    pub reason_to_read: Option<String>,
    #[serde(default)]
    pub bullet_points: Vec<ContentSummaryBulletPoint>,
    #[serde(default)]
    pub quotes: Vec<ContentSummaryQuote>,
    #[serde(default)]
    pub topics: Vec<String>,
    pub full_markdown: Option<String>,
    #[serde(default)]
    pub body_available: bool,
    pub body_kind: Option<String>,
    pub body_format: Option<String>,
    pub news_article_url: Option<String>,
    pub news_discussion_url: Option<String>,
    pub news_key_points: Option<Vec<String>>,
    pub news_summary: Option<String>,
    pub image_url: Option<String>,
    pub thumbnail_url: Option<String>,
    pub detected_feed: Option<DetectedFeed>,
    #[serde(default)]
    pub can_subscribe: bool,
}
