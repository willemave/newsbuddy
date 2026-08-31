use chrono::{DateTime, Utc};
use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use utoipa::ToSchema;

use crate::{
    ContentStatus, ContentSummaryResponse, ContentType, PaginationMetadata, UserLlmProvider,
};

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct ConvertNewsResponse {
    pub status: String,
    pub new_content_id: i64,
    pub original_content_id: i64,
    pub already_exists: bool,
    pub message: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct ConvertNewsItemResponse {
    #[serde(default = "success_status")]
    #[schema(default = "success")]
    pub status: String,
    pub news_item_id: i64,
    pub new_content_id: i64,
    pub already_exists: bool,
    pub message: String,
}

fn success_status() -> String {
    "success".to_owned()
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize, JsonSchema, ToSchema)]
#[serde(deny_unknown_fields)]
pub struct DownloadMoreRequest {
    #[schemars(range(min = 1, max = 50))]
    #[schema(minimum = 1, maximum = 50)]
    pub count: usize,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct DownloadMoreResponse {
    pub status: String,
    #[schema(minimum = 1, maximum = 50)]
    pub requested_count: usize,
    #[schema(minimum = 1)]
    pub base_limit: usize,
    #[schema(minimum = 1)]
    pub target_limit: usize,
    #[schema(minimum = 0)]
    pub scraped: usize,
    #[schema(minimum = 0)]
    pub saved: usize,
    #[schema(minimum = 0)]
    pub duplicates: usize,
    #[schema(minimum = 0)]
    pub errors: usize,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
#[serde(rename_all = "snake_case")]
pub enum NarrationTargetType {
    Content,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct NarrationResponse {
    pub target_type: NarrationTargetType,
    pub target_id: i64,
    pub title: String,
    pub narration_text: String,
}

#[derive(
    Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema,
)]
#[serde(rename_all = "snake_case")]
pub enum TweetLength {
    Short,
    #[default]
    Medium,
    Long,
}

impl TweetLength {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Short => "short",
            Self::Medium => "medium",
            Self::Long => "long",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize, JsonSchema, ToSchema)]
#[serde(deny_unknown_fields)]
pub struct TweetSuggestionsRequest {
    #[schemars(length(max = 500))]
    #[schema(max_length = 500)]
    pub message: Option<String>,
    #[serde(default = "default_creativity")]
    #[schemars(range(min = 1, max = 10))]
    #[schema(minimum = 1, maximum = 10, default = 5)]
    pub creativity: u8,
    #[serde(default)]
    #[schema(default = "medium")]
    pub length: TweetLength,
    pub llm_provider: Option<UserLlmProvider>,
}

const fn default_creativity() -> u8 {
    5
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct TweetSuggestion {
    #[schema(minimum = 1, maximum = 3)]
    pub id: u8,
    pub text: String,
    pub style_label: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct TweetSuggestionsResponse {
    pub content_id: i64,
    #[schema(minimum = 1, maximum = 10)]
    pub creativity: u8,
    pub length: TweetLength,
    #[serde(default = "default_tweet_model")]
    #[schema(default = "openai:gpt-5.6-luna")]
    pub model: String,
    #[schema(min_items = 3, max_items = 3)]
    pub suggestions: Vec<TweetSuggestion>,
}

fn default_tweet_model() -> String {
    "openai:gpt-5.6-luna".to_owned()
}

#[derive(
    Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema,
)]
#[serde(rename_all = "snake_case")]
pub enum SubmissionKind {
    #[default]
    Content,
    FeedSubscription,
    LearningDeck,
}

#[derive(
    Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema,
)]
#[serde(rename_all = "snake_case")]
pub enum SubmissionOutcome {
    Queued,
    #[default]
    Processing,
    Completed,
    NoAction,
    Failed,
    Skipped,
    Subscribed,
    AlreadySubscribed,
    FeedNotFound,
    FeedFetchFailed,
    FeedSubscriptionFailed,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct SubmissionFeedInitialDownloadResponse {
    pub requested_count: Option<i64>,
    pub ran: Option<bool>,
    pub status: Option<String>,
    pub reason: Option<String>,
    pub error: Option<String>,
    pub config_id: Option<i64>,
    pub base_limit: Option<i64>,
    pub target_limit: Option<i64>,
    pub scraped: Option<i64>,
    pub saved: Option<i64>,
    pub duplicates: Option<i64>,
    pub errors: Option<i64>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct SubmissionFeedSubscriptionResponse {
    pub status: String,
    pub feed_url: Option<String>,
    pub feed_type: Option<String>,
    pub created: Option<bool>,
    pub config_id: Option<i64>,
    pub initial_download: Option<SubmissionFeedInitialDownloadResponse>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
enum ContentResultKind {
    #[serde(rename = "content")]
    Content,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
enum FeedSubscriptionResultKind {
    #[serde(rename = "feed_subscription")]
    FeedSubscription,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
enum LearningDeckResultKind {
    #[serde(rename = "learning_deck")]
    LearningDeck,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
enum NoActionResultKind {
    #[serde(rename = "no_action")]
    NoAction,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
#[serde(deny_unknown_fields)]
pub struct SubmissionContentResult {
    #[schema(inline)]
    result_kind: ContentResultKind,
    pub outcome: SubmissionOutcome,
}

impl SubmissionContentResult {
    pub const fn new(outcome: SubmissionOutcome) -> Self {
        Self {
            result_kind: ContentResultKind::Content,
            outcome,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
#[serde(deny_unknown_fields)]
pub struct SubmissionFeedSubscriptionResult {
    #[schema(inline)]
    result_kind: FeedSubscriptionResultKind,
    pub outcome: SubmissionOutcome,
    pub detected_feed: Option<crate::DetectedFeed>,
    pub subscription: Option<SubmissionFeedSubscriptionResponse>,
}

impl SubmissionFeedSubscriptionResult {
    pub fn new(
        outcome: SubmissionOutcome,
        detected_feed: Option<crate::DetectedFeed>,
        subscription: Option<SubmissionFeedSubscriptionResponse>,
    ) -> Self {
        Self {
            result_kind: FeedSubscriptionResultKind::FeedSubscription,
            outcome,
            detected_feed,
            subscription,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
#[serde(deny_unknown_fields)]
pub struct SubmissionLearningDeckResult {
    #[schema(inline)]
    result_kind: LearningDeckResultKind,
    pub outcome: SubmissionOutcome,
}

impl SubmissionLearningDeckResult {
    pub const fn new(outcome: SubmissionOutcome) -> Self {
        Self {
            result_kind: LearningDeckResultKind::LearningDeck,
            outcome,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
#[serde(deny_unknown_fields)]
pub struct SubmissionNoActionResult {
    #[schema(inline)]
    result_kind: NoActionResultKind,
    pub rationale: String,
}

impl SubmissionNoActionResult {
    pub fn new(rationale: String) -> Self {
        Self {
            result_kind: NoActionResultKind::NoAction,
            rationale,
        }
    }
}

/// Canonical typed outcome for one submitted item.
///
/// The legacy top-level submission fields remain temporarily on
/// [`SubmissionStatusResponse`] for installed clients. New clients use this union so feed-only,
/// Learning Deck, and no-action data cannot appear on unrelated result kinds.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
#[serde(untagged)]
#[schema(discriminator(
    property_name = "result_kind",
    mapping(
        ("content" = "#/components/schemas/SubmissionContentResult"),
        ("feed_subscription" = "#/components/schemas/SubmissionFeedSubscriptionResult"),
        ("learning_deck" = "#/components/schemas/SubmissionLearningDeckResult"),
        ("no_action" = "#/components/schemas/SubmissionNoActionResult")
    )
))]
pub enum SubmissionResult {
    Content(SubmissionContentResult),
    FeedSubscription(Box<SubmissionFeedSubscriptionResult>),
    LearningDeck(SubmissionLearningDeckResult),
    NoAction(SubmissionNoActionResult),
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct SubmissionStatusResponse {
    pub id: i64,
    pub content_type: ContentType,
    pub url: String,
    pub source_url: Option<String>,
    pub title: Option<String>,
    pub status: ContentStatus,
    pub error_message: Option<String>,
    #[schemars(with = "String")]
    #[schema(value_type = String, format = DateTime)]
    pub created_at: DateTime<Utc>,
    #[schemars(with = "Option<String>")]
    #[schema(value_type = Option<String>, format = DateTime)]
    pub processed_at: Option<DateTime<Utc>>,
    pub submitted_via: Option<String>,
    #[serde(default = "default_true")]
    #[schema(default = true)]
    pub is_self_submission: bool,
    pub result: SubmissionResult,
    /// Installed-client compatibility field. New clients use `result`.
    #[serde(default)]
    #[schema(default = "content")]
    pub submission_kind: SubmissionKind,
    /// Installed-client compatibility field. New clients use `result`.
    #[serde(default)]
    #[schema(default = "processing")]
    pub outcome: SubmissionOutcome,
    /// Installed-client compatibility field. New clients use `result`.
    pub rationale: Option<String>,
    /// Installed-client compatibility field. New clients use `result`.
    pub detected_feed: Option<crate::DetectedFeed>,
    /// Installed-client compatibility field. New clients use `result`.
    pub feed_subscription: Option<SubmissionFeedSubscriptionResponse>,
}

const fn default_true() -> bool {
    true
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct SubmissionStatusListResponse {
    pub submissions: Vec<SubmissionStatusResponse>,
    pub meta: PaginationMetadata,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct PodcastEpisodeSearchResultResponse {
    pub title: String,
    pub episode_url: String,
    pub podcast_title: Option<String>,
    pub source: Option<String>,
    pub snippet: Option<String>,
    pub feed_url: Option<String>,
    pub published_at: Option<String>,
    pub provider: Option<String>,
    pub score: Option<f64>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct PodcastEpisodeSearchResponse {
    #[serde(default)]
    pub results: Vec<PodcastEpisodeSearchResultResponse>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct MixedSearchFeedResultResponse {
    pub id: String,
    pub title: String,
    pub site_url: String,
    pub feed_url: String,
    pub feed_type: String,
    pub feed_format: String,
    pub description: Option<String>,
    pub rationale: Option<String>,
    pub evidence_url: Option<String>,
    #[serde(default)]
    #[schema(default = false)]
    pub is_subscribed: bool,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct MixedSearchResponse {
    pub query: String,
    #[serde(default)]
    pub content: Vec<ContentSummaryResponse>,
    #[serde(default)]
    pub feeds: Vec<MixedSearchFeedResultResponse>,
    #[serde(default)]
    pub podcasts: Vec<PodcastEpisodeSearchResultResponse>,
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::*;

    #[test]
    fn submission_result_is_strictly_discriminated_and_round_trips() {
        let result = SubmissionResult::FeedSubscription(Box::new(
            SubmissionFeedSubscriptionResult::new(SubmissionOutcome::Subscribed, None, None),
        ));
        let encoded = serde_json::to_value(&result).expect("serialize submission result");
        assert_eq!(
            encoded,
            json!({
                "result_kind": "feed_subscription",
                "outcome": "subscribed",
                "detected_feed": null,
                "subscription": null
            })
        );
        assert_eq!(
            serde_json::from_value::<SubmissionResult>(encoded)
                .expect("deserialize submission result"),
            result
        );
        assert!(
            serde_json::from_value::<SubmissionResult>(json!({
                "result_kind": "content",
                "outcome": "completed",
                "subscription": null
            }))
            .is_err()
        );
    }
}
