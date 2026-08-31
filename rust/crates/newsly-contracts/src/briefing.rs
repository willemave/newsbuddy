use chrono::{DateTime, Utc};
use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use utoipa::ToSchema;

use crate::ContentType;

pub const BRIEFING_DIG_FRAGMENT_MAX_LENGTH: usize = 2_000;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
#[serde(rename_all = "snake_case")]
pub enum BriefingTier {
    Audio,
    Longform,
    News,
}

impl TryFrom<&str> for BriefingTier {
    type Error = String;

    fn try_from(value: &str) -> Result<Self, Self::Error> {
        match value {
            "audio" => Ok(Self::Audio),
            "longform" => Ok(Self::Longform),
            "news" => Ok(Self::News),
            other => Err(format!("unsupported Briefing tier {other:?}")),
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
#[serde(rename_all = "snake_case")]
pub enum BriefingFirstRunPhase {
    Active,
    Ready,
    WaitingForContent,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
#[serde(rename_all = "snake_case")]
pub enum BriefingFirstRunSourceOutcome {
    Processed,
    Unavailable,
}

impl TryFrom<&str> for BriefingFirstRunSourceOutcome {
    type Error = String;

    fn try_from(value: &str) -> Result<Self, Self::Error> {
        match value {
            "processed" => Ok(Self::Processed),
            "unavailable" => Ok(Self::Unavailable),
            other => Err(format!("unsupported first-run source outcome {other:?}")),
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
#[serde(rename_all = "snake_case")]
pub enum BriefingRunKind {
    Text,
    SourceLink,
    Insight,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
#[serde(rename_all = "snake_case")]
pub enum BriefingBlockType {
    Passage,
    Figure,
    Pullquote,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
#[serde(rename_all = "snake_case")]
pub enum BriefingFigurePlacement {
    Inset,
    Full,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
#[serde(rename_all = "snake_case")]
pub enum BriefingFigureAlignment {
    Left,
    Right,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct BriefingLensSummary {
    pub key: String,
    pub tier: BriefingTier,
    pub title: String,
    pub deck: String,
    pub position: i32,
    pub segment_count: usize,
    pub unread_source_count: usize,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct BriefingFirstRunSourceProgress {
    pub display_name: String,
    #[schemars(range(min = 0))]
    #[schema(minimum = 0)]
    pub processed_item_count: i32,
    pub outcome: BriefingFirstRunSourceOutcome,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct BriefingFirstRunProgress {
    pub run_id: i64,
    pub revision: i32,
    pub phase: BriefingFirstRunPhase,
    pub connected_source_count: usize,
    #[serde(default)]
    pub completed_sources: Vec<BriefingFirstRunSourceProgress>,
    #[serde(default)]
    pub active_sources: Vec<String>,
    #[serde(default)]
    pub queued_sources: Vec<String>,
    #[serde(default)]
    pub ready_category_keys: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct BriefingIndexResponse {
    pub version: i32,
    pub masthead_title: String,
    pub masthead_deck: String,
    #[schemars(with = "Option<String>")]
    #[schema(value_type = Option<String>, format = DateTime)]
    pub generated_at: Option<DateTime<Utc>>,
    #[serde(default)]
    pub lenses: Vec<BriefingLensSummary>,
    pub first_run: Option<BriefingFirstRunProgress>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct BriefingRunDto {
    pub kind: BriefingRunKind,
    pub text: String,
    pub source_key: Option<String>,
    pub insight_id: Option<String>,
    #[serde(default)]
    pub bold: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct BriefingParagraphDto {
    #[serde(default)]
    pub runs: Vec<BriefingRunDto>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct BriefingBlockDto {
    #[serde(rename = "type")]
    pub block_type: BriefingBlockType,
    pub weight: Option<String>,
    pub paragraphs: Option<Vec<BriefingParagraphDto>>,
    pub source_key: Option<String>,
    pub image_url: Option<String>,
    pub thumbnail_url: Option<String>,
    pub caption: Option<String>,
    pub placement: Option<BriefingFigurePlacement>,
    pub alignment: Option<BriefingFigureAlignment>,
    pub text: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct BriefingSegmentDto {
    pub id: i64,
    #[schemars(with = "String")]
    #[schema(value_type = String, format = DateTime)]
    pub created_at: DateTime<Utc>,
    pub status: String,
    pub narration_text: String,
    #[serde(default)]
    pub blocks: Vec<BriefingBlockDto>,
    #[serde(default)]
    pub source_keys: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct BriefingDiscussionDto {
    pub platform: String,
    pub comment_count: Option<i32>,
    pub summary_status: String,
    pub overview: Option<String>,
    pub top_comment_author: Option<String>,
    pub top_comment_text: Option<String>,
    pub external_url: Option<String>,
    #[schemars(with = "Option<String>")]
    #[schema(value_type = Option<String>, format = DateTime)]
    pub updated_at: Option<DateTime<Utc>>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct BriefingSourceDto {
    pub source_key: String,
    pub kind: String,
    pub id: i64,
    pub title: String,
    pub summary: Option<String>,
    pub key_points: Option<Vec<String>>,
    pub url: Option<String>,
    pub image_url: Option<String>,
    pub thumbnail_url: Option<String>,
    #[schemars(with = "Option<String>")]
    #[schema(value_type = Option<String>, format = DateTime)]
    pub published_at: Option<DateTime<Utc>>,
    pub content_type: Option<ContentType>,
    #[serde(default)]
    pub read: bool,
    pub discussion: Option<BriefingDiscussionDto>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct BriefingLensResponse {
    pub version: i32,
    pub lens: BriefingLensSummary,
    #[serde(default)]
    pub segments: Vec<BriefingSegmentDto>,
    #[serde(default)]
    pub sources: Vec<BriefingSourceDto>,
    pub next_cursor: Option<String>,
    #[serde(default)]
    pub has_more: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize, JsonSchema, ToSchema)]
pub struct BriefingReadMarkRequest {
    #[schemars(length(min = 1))]
    #[schema(min_items = 1)]
    pub source_keys: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct BriefingReadMarkResponse {
    #[schemars(range(min = 0))]
    #[schema(minimum = 0)]
    pub marked: usize,
    #[schemars(range(min = 0))]
    #[schema(minimum = 0)]
    pub retired: usize,
    pub version: i32,
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize, JsonSchema, ToSchema)]
pub struct BriefingDigSearchRequest {
    #[schemars(length(min = 3, max = 2_000))]
    #[schema(min_length = 3, max_length = 2_000)]
    pub fragment: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct BriefingDigSearchResult {
    pub title: String,
    pub url: String,
    pub snippet: Option<String>,
    pub published_date: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct BriefingDigSearchResponse {
    #[serde(default)]
    pub results: Vec<BriefingDigSearchResult>,
    #[schemars(range(min = 0))]
    #[schema(minimum = 0)]
    pub elapsed_ms: u64,
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize, JsonSchema, ToSchema)]
pub struct BriefingDigSummarizeRequest {
    #[schemars(length(min = 3, max = 2_000))]
    #[schema(min_length = 3, max_length = 2_000)]
    pub fragment: String,
    #[schemars(length(max = 2_000))]
    #[schema(max_length = 2_000)]
    pub passage_context: String,
    #[serde(default)]
    pub results: Vec<BriefingDigSearchResult>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct BriefingDigSummarizeResponse {
    pub summary: String,
    pub model: String,
    #[schemars(range(min = 0))]
    #[schema(minimum = 0)]
    pub elapsed_ms: u64,
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize, JsonSchema, ToSchema)]
pub struct BriefingNarrationRequest {
    #[schemars(length(min = 1, max = 64))]
    #[schema(min_length = 1, max_length = 64)]
    pub lens_key: String,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
#[serde(rename_all = "snake_case")]
pub enum AudioEpisodeKind {
    FastNewsDigest,
    ContentCouncilDiscussion,
    NewsItemDiscussion,
    CustomNarration,
    BriefingNarration,
}

impl TryFrom<&str> for AudioEpisodeKind {
    type Error = String;

    fn try_from(value: &str) -> Result<Self, Self::Error> {
        match value {
            "fast_news_digest" => Ok(Self::FastNewsDigest),
            "content_council_discussion" => Ok(Self::ContentCouncilDiscussion),
            "news_item_discussion" => Ok(Self::NewsItemDiscussion),
            "custom_narration" => Ok(Self::CustomNarration),
            "briefing_narration" => Ok(Self::BriefingNarration),
            other => Err(format!("unsupported audio episode kind {other:?}")),
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
#[serde(rename_all = "snake_case")]
pub enum AudioEpisodeStatus {
    Pending,
    Processing,
    Completed,
    Failed,
}

impl TryFrom<&str> for AudioEpisodeStatus {
    type Error = String;

    fn try_from(value: &str) -> Result<Self, Self::Error> {
        match value {
            "pending" => Ok(Self::Pending),
            "processing" => Ok(Self::Processing),
            "completed" => Ok(Self::Completed),
            "failed" => Ok(Self::Failed),
            other => Err(format!("unsupported audio episode status {other:?}")),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct AudioEpisodeResponse {
    pub id: i64,
    pub kind: AudioEpisodeKind,
    pub status: AudioEpisodeStatus,
    pub title: String,
    pub source_content_id: Option<i64>,
    #[serde(default)]
    pub source_item_ids: Vec<i64>,
    #[serde(default)]
    pub source_content_ids: Vec<i64>,
    #[serde(default)]
    pub source_count: usize,
    #[serde(default)]
    pub source_titles: Vec<String>,
    #[serde(default)]
    pub read_on_play_content_ids: Vec<i64>,
    #[serde(default)]
    pub read_on_play_news_item_ids: Vec<i64>,
    pub duration_seconds: Option<i32>,
    pub audio_url: Option<String>,
    pub stream_url: Option<String>,
    pub script_text: Option<String>,
    pub error_message: Option<String>,
    #[schemars(with = "String")]
    #[schema(value_type = String, format = DateTime)]
    pub created_at: DateTime<Utc>,
    #[schemars(with = "Option<String>")]
    #[schema(value_type = Option<String>, format = DateTime)]
    pub updated_at: Option<DateTime<Utc>>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct BriefingNarrationResponse {
    #[schemars(length(min = 64, max = 64))]
    #[schema(min_length = 64, max_length = 64)]
    pub episode_group_id: String,
    #[schemars(length(min = 1, max = 64))]
    #[schema(min_length = 1, max_length = 64)]
    pub lens_key: String,
    #[schemars(length(min = 1))]
    #[schema(min_length = 1)]
    pub title: String,
    pub status: AudioEpisodeStatus,
    pub playable: bool,
    #[schemars(range(min = 0))]
    #[schema(minimum = 0)]
    pub duration_seconds: i32,
    #[schemars(length(min = 1))]
    #[schema(min_items = 1)]
    pub chapters: Vec<AudioEpisodeResponse>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct BriefingRefreshResponse {
    pub enqueued: bool,
    pub version: i32,
}
