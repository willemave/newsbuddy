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
    pub const ALL: [Self; 6] = [
        Self::Substack,
        Self::Atom,
        Self::PodcastRss,
        Self::Youtube,
        Self::Reddit,
        Self::Aggregator,
    ];

    pub const fn supports_backfill(self) -> bool {
        matches!(self, Self::Substack | Self::Atom | Self::PodcastRss)
    }

    pub const fn requires_feed_probe(self) -> bool {
        self.supports_backfill()
    }

    pub const fn supports_feed_history(self) -> bool {
        self.supports_backfill() || matches!(self, Self::Youtube)
    }

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

impl std::str::FromStr for ScraperType {
    type Err = &'static str;

    fn from_str(value: &str) -> Result<Self, Self::Err> {
        Self::ALL
            .into_iter()
            .find(|kind| kind.as_str() == value)
            .ok_or("Unsupported scraper_type")
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
    #[serde(default)]
    #[schemars(with = "Option<String>")]
    #[schema(value_type = Option<String>, format = DateTime)]
    pub last_fetch_at: Option<DateTime<Utc>>,
    #[serde(default)]
    pub ingestion_error: Option<String>,
    pub total_count: i64,
    pub completed_count: i64,
    pub unread_count: i64,
    pub processing_count: i64,
    #[serde(default)]
    pub failed_count: i64,
    #[serde(default)]
    pub access_gate_count: i64,
    #[serde(default)]
    pub running_count: i64,
    #[serde(default)]
    pub queued_count: i64,
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

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct FeedHistoryItem {
    pub id: i64,
    pub title: String,
    pub content_type: String,
    pub status: String,
    pub stage: Option<String>,
    #[schemars(with = "Option<String>")]
    #[schema(value_type = Option<String>, format = DateTime)]
    pub processed_at: Option<DateTime<Utc>>,
    #[schemars(with = "Option<String>")]
    #[schema(value_type = Option<String>, format = DateTime)]
    pub publication_at: Option<DateTime<Utc>>,
    pub duration_seconds: Option<i64>,
    pub reading_minutes: Option<i64>,
}

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct FeedHistoryResponse {
    pub items: Vec<FeedHistoryItem>,
    pub next_offset: Option<i64>,
}

#[cfg(test)]
mod tests {
    use super::ScraperType;

    #[test]
    fn scraper_types_parse_wire_values_and_preserve_feed_capabilities() {
        for kind in ScraperType::ALL {
            assert_eq!(kind.as_str().parse::<ScraperType>(), Ok(kind));
            assert_eq!(serde_json::to_value(kind).unwrap(), kind.as_str());
        }
        assert!("rss".parse::<ScraperType>().is_err());
        assert!("YouTube".parse::<ScraperType>().is_err());
        assert!(ScraperType::Atom.supports_backfill());
        assert!(ScraperType::PodcastRss.requires_feed_probe());
        assert!(!ScraperType::Youtube.supports_backfill());
        assert!(ScraperType::Youtube.supports_feed_history());
        assert!(!ScraperType::Aggregator.supports_feed_history());
    }
}
