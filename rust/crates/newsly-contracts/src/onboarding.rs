use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use utoipa::ToSchema;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
#[serde(rename_all = "snake_case")]
pub enum OnboardingSuggestionType {
    Substack,
    Atom,
    PodcastRss,
    Reddit,
}

impl TryFrom<&str> for OnboardingSuggestionType {
    type Error = String;

    fn try_from(value: &str) -> Result<Self, Self::Error> {
        match value {
            "substack" => Ok(Self::Substack),
            "atom" => Ok(Self::Atom),
            "podcast_rss" => Ok(Self::PodcastRss),
            "reddit" => Ok(Self::Reddit),
            other => Err(format!("unsupported onboarding suggestion type {other:?}")),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct OnboardingSuggestion {
    /// Present for suggestions persisted under a discovery run. Synchronous preview suggestions
    /// are not selectable completion resources and therefore carry `null`.
    pub id: Option<i64>,
    pub suggestion_type: OnboardingSuggestionType,
    pub title: Option<String>,
    pub site_url: Option<String>,
    pub feed_url: Option<String>,
    pub subreddit: Option<String>,
    pub rationale: Option<String>,
    pub score: Option<f64>,
    pub is_default: bool,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct OnboardingFastDiscoverResponse {
    #[serde(default)]
    pub recommended_pods: Vec<OnboardingSuggestion>,
    #[serde(default)]
    pub recommended_substacks: Vec<OnboardingSuggestion>,
    #[serde(default)]
    pub recommended_subreddits: Vec<OnboardingSuggestion>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct OnboardingDiscoveryLaneStatus {
    pub name: String,
    pub status: String,
    pub completed_queries: i32,
    pub query_count: i32,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct OnboardingDiscoveryStatusResponse {
    pub run_id: i64,
    pub run_status: String,
    pub topic_summary: Option<String>,
    #[serde(default)]
    pub inferred_topics: Vec<String>,
    #[serde(default)]
    pub lanes: Vec<OnboardingDiscoveryLaneStatus>,
    pub suggestions: Option<OnboardingFastDiscoverResponse>,
    pub error_message: Option<String>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct OnboardingTutorialResponse {
    pub has_completed_new_user_tutorial: bool,
}
