use chrono::{DateTime, Utc};
use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};
use utoipa::ToSchema;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
#[serde(rename_all = "snake_case")]
pub enum ScraperType {
    Substack,
    Atom,
    PodcastRss,
    Youtube,
    Reddit,
    Aggregator,
}

impl ScraperType {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Substack => "substack",
            Self::Atom => "atom",
            Self::PodcastRss => "podcast_rss",
            Self::Youtube => "youtube",
            Self::Reddit => "reddit",
            Self::Aggregator => "aggregator",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Deserialize, JsonSchema, ToSchema)]
pub struct CreateUserScraperConfig {
    pub scraper_type: ScraperType,
    pub display_name: Option<String>,
    #[serde(default)]
    pub config: Map<String, Value>,
    #[serde(default = "default_active")]
    pub is_active: bool,
}

#[derive(Debug, Clone, Default, PartialEq, Deserialize, JsonSchema, ToSchema)]
pub struct UpdateUserScraperConfig {
    pub display_name: Option<String>,
    pub config: Option<Map<String, Value>>,
    pub is_active: Option<bool>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct SubscribeToFeedRequest {
    pub feed_url: String,
    pub feed_type: String,
    pub display_name: Option<String>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
#[serde(rename_all = "snake_case")]
pub enum FeedSubscriptionOutcome {
    Created,
    Reactivated,
    AlreadySubscribed,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct ScraperConfigStatsResponse {
    pub total_count: i64,
    pub completed_count: i64,
    pub unread_count: i64,
    pub processing_count: i64,
    #[schemars(with = "Option<String>")]
    #[schema(value_type = Option<String>, format = DateTime)]
    pub latest_processed_at: Option<DateTime<Utc>>,
    #[schemars(with = "Option<String>")]
    #[schema(value_type = Option<String>, format = DateTime)]
    pub latest_publication_at: Option<DateTime<Utc>>,
    #[schemars(with = "Option<String>")]
    #[schema(value_type = Option<String>, format = DateTime)]
    pub next_expected_at: Option<DateTime<Utc>>,
    pub average_interval_hours: Option<f64>,
    #[serde(default)]
    pub interval_sample_size: usize,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct ScraperConfigResponse {
    pub id: i64,
    pub scraper_type: String,
    pub display_name: Option<String>,
    pub config: Map<String, Value>,
    pub feed_url: Option<String>,
    pub limit: Option<i64>,
    pub is_active: bool,
    #[schemars(with = "String")]
    #[schema(value_type = String, format = DateTime)]
    pub created_at: DateTime<Utc>,
    pub stats: Option<ScraperConfigStatsResponse>,
    pub subscription_outcome: Option<FeedSubscriptionOutcome>,
    pub backfill_task_id: Option<i64>,
}

const fn default_active() -> bool {
    true
}
