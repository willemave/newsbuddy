use std::collections::BTreeMap;

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use utoipa::ToSchema;

use crate::{OnboardingDiscoveryLaneStatus, OnboardingFastDiscoverResponse};

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct OnboardingProfileRequest {
    #[schemars(length(min = 1, max = 120))]
    #[schema(min_length = 1, max_length = 120)]
    pub first_name: String,
    #[serde(default)]
    #[schemars(length(max = 12))]
    #[schema(max_items = 12)]
    pub interest_topics: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct OnboardingProfileResponse {
    pub profile_summary: String,
    #[serde(default)]
    pub inferred_topics: Vec<String>,
    #[serde(default)]
    pub candidate_sources: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct OnboardingVoiceParseRequest {
    #[schemars(length(min = 3, max = 6_000))]
    #[schema(min_length = 3, max_length = 6_000)]
    pub transcript: String,
    #[schemars(length(max = 20))]
    #[schema(max_length = 20)]
    pub locale: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct OnboardingVoiceParseResponse {
    pub first_name: Option<String>,
    #[serde(default)]
    pub interest_topics: Vec<String>,
    #[schemars(range(min = 0.0, max = 1.0))]
    #[schema(minimum = 0.0, maximum = 1.0)]
    pub confidence: Option<f64>,
    #[serde(default)]
    pub missing_fields: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct OnboardingAudioDiscoverRequest {
    #[schemars(length(min = 3, max = 8_000))]
    #[schema(min_length = 3, max_length = 8_000)]
    pub transcript: String,
    #[schemars(length(max = 20))]
    #[schema(max_length = 20)]
    pub locale: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct OnboardingAudioDiscoverResponse {
    pub run_id: i64,
    pub run_status: String,
    pub topic_summary: Option<String>,
    #[serde(default)]
    pub inferred_topics: Vec<String>,
    #[serde(default)]
    pub lanes: Vec<OnboardingDiscoveryLaneStatus>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
#[serde(rename_all = "snake_case")]
pub enum OnboardingAudioLaneTarget {
    Feeds,
    Podcasts,
    Reddit,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct OnboardingAudioLanePreview {
    pub name: String,
    pub goal: String,
    pub target: OnboardingAudioLaneTarget,
    #[serde(default)]
    pub queries: Vec<String>,
    #[serde(default)]
    pub include_social: bool,
    #[serde(default)]
    pub exa_results_per_query: i64,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct OnboardingAudioLanePreviewResponse {
    pub topic_summary: String,
    #[serde(default)]
    pub inferred_topics: Vec<String>,
    #[serde(default)]
    pub lanes: Vec<OnboardingAudioLanePreview>,
    #[serde(default)]
    pub used_fallback: bool,
    pub fallback_reason: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct OnboardingFastDiscoverRequest {
    #[schemars(length(min = 3))]
    #[schema(min_length = 3)]
    pub profile_summary: String,
    #[serde(default)]
    #[schemars(length(max = 12))]
    #[schema(max_items = 12)]
    pub inferred_topics: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct OnboardingSelectedAggregator {
    #[schemars(length(min = 1, max = 64))]
    #[schema(min_length = 1, max_length = 64)]
    pub key: String,
    pub title: Option<String>,
    #[serde(default)]
    pub topics: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema, ToSchema, Default)]
pub struct OnboardingCompleteRequest {
    /// Server-owned discovery run whose persisted proposals are being confirmed. This is `null`
    /// only for the explicit non-personalized path, which cannot select discovered suggestions.
    pub discovery_run_id: Option<i64>,
    #[serde(default)]
    #[schemars(length(max = 100))]
    #[schema(max_items = 100)]
    pub selected_suggestion_ids: Vec<i64>,
    #[serde(default)]
    pub selected_aggregators: Vec<OnboardingSelectedAggregator>,
    #[schemars(length(max = 50))]
    #[schema(max_length = 50)]
    pub twitter_username: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct OnboardingCompleteResponse {
    pub status: String,
    pub task_id: Option<i64>,
    pub inbox_count_estimate: i64,
    pub configured_source_count: i64,
    pub longform_status: String,
    pub has_completed_onboarding: bool,
    pub has_completed_new_user_tutorial: bool,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct AgentOnboardingStartRequest {
    #[schemars(length(min = 1, max = 4_000))]
    #[schema(min_length = 1, max_length = 4_000)]
    pub brief: String,
    pub preferences: Option<BTreeMap<String, Value>>,
    #[serde(default)]
    pub seed_urls: Vec<String>,
    #[serde(default)]
    pub seed_feeds: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct AgentOnboardingStartResponse {
    pub run_id: i64,
    pub status: String,
    pub job_id: Option<i64>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema, Default)]
pub struct AgentOnboardingCompleteRequest {
    #[serde(default)]
    pub accept_all: bool,
    #[serde(default)]
    #[schemars(length(max = 100))]
    #[schema(max_items = 100)]
    pub selected_suggestion_ids: Vec<i64>,
    #[serde(default)]
    pub selected_aggregators: Vec<OnboardingSelectedAggregator>,
}

// Keep the response alias explicit at the contract boundary. Both fast-discovery callers expose
// the same Python wire object and should never drift into parallel DTOs.
pub type AgentOnboardingSuggestionsResponse = OnboardingFastDiscoverResponse;
