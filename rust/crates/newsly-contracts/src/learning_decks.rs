use chrono::{DateTime, Utc};
use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};
use utoipa::ToSchema;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
#[serde(rename_all = "snake_case")]
pub enum LearningDeckSourceKind {
    Content,
    GithubRepo,
}

impl LearningDeckSourceKind {
    #[must_use]
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Content => "content",
            Self::GithubRepo => "github_repo",
        }
    }
}

impl TryFrom<&str> for LearningDeckSourceKind {
    type Error = String;

    fn try_from(value: &str) -> Result<Self, Self::Error> {
        match value {
            "content" => Ok(Self::Content),
            "github_repo" => Ok(Self::GithubRepo),
            other => Err(format!("unsupported Learning Deck source kind {other:?}")),
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
#[serde(rename_all = "snake_case")]
pub enum LearningDeckRunStatus {
    Queued,
    Preparing,
    Generating,
    Validating,
    Publishing,
    Completed,
    Failed,
    Cancelled,
}

impl LearningDeckRunStatus {
    /// Projects the canonical LLM-task ledger into the compatibility run status.
    ///
    /// # Errors
    ///
    /// Returns the unknown durable status when it cannot be represented on the wire.
    pub fn from_attempt_status(value: &str) -> Result<Self, String> {
        match value {
            "queued" => Ok(Self::Queued),
            "preparing" => Ok(Self::Preparing),
            "running" | "awaiting_approval" | "generating" => Ok(Self::Generating),
            "validating" => Ok(Self::Validating),
            "applying" | "publishing" => Ok(Self::Publishing),
            "completed" => Ok(Self::Completed),
            "failed" => Ok(Self::Failed),
            "cancelled" => Ok(Self::Cancelled),
            other => Err(format!(
                "unsupported Learning Deck attempt status {other:?}"
            )),
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
#[serde(rename_all = "snake_case")]
pub enum LearningDeckStatus {
    Ready,
    Queued,
    Preparing,
    Generating,
    Validating,
    Publishing,
    Completed,
    Failed,
    Cancelled,
}

impl From<LearningDeckRunStatus> for LearningDeckStatus {
    fn from(value: LearningDeckRunStatus) -> Self {
        match value {
            LearningDeckRunStatus::Queued => Self::Queued,
            LearningDeckRunStatus::Preparing => Self::Preparing,
            LearningDeckRunStatus::Generating => Self::Generating,
            LearningDeckRunStatus::Validating => Self::Validating,
            LearningDeckRunStatus::Publishing => Self::Publishing,
            LearningDeckRunStatus::Completed => Self::Completed,
            LearningDeckRunStatus::Failed => Self::Failed,
            LearningDeckRunStatus::Cancelled => Self::Cancelled,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize, JsonSchema, ToSchema)]
pub struct LearningDeckCreateRequest {
    pub content_id: Option<i64>,
    pub news_item_id: Option<i64>,
    pub url: Option<String>,
    pub interests_prompt: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct LearningDeckTimelineEntry {
    pub status: LearningDeckRunStatus,
    pub note: String,
    #[schemars(with = "String")]
    #[schema(value_type = String, format = DateTime)]
    pub created_at: DateTime<Utc>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct LearningDeckRunResponse {
    pub id: i64,
    pub status: LearningDeckRunStatus,
    pub interests_prompt: Option<String>,
    #[serde(default)]
    pub timeline: Vec<LearningDeckTimelineEntry>,
    pub error_message: Option<String>,
    #[schemars(with = "Option<String>")]
    #[schema(value_type = Option<String>, format = DateTime)]
    pub started_at: Option<DateTime<Utc>>,
    #[schemars(with = "Option<String>")]
    #[schema(value_type = Option<String>, format = DateTime)]
    pub completed_at: Option<DateTime<Utc>>,
    #[schemars(with = "String")]
    #[schema(value_type = String, format = DateTime)]
    pub created_at: DateTime<Utc>,
    #[schemars(with = "Option<String>")]
    #[schema(value_type = Option<String>, format = DateTime)]
    pub updated_at: Option<DateTime<Utc>>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct LearningDeckResponse {
    pub id: i64,
    pub title: String,
    pub source_kind: LearningDeckSourceKind,
    pub source_url: Option<String>,
    pub source_content_id: Option<i64>,
    pub source_title: Option<String>,
    #[serde(default)]
    pub source_metadata: Map<String, Value>,
    pub status: Option<LearningDeckStatus>,
    #[serde(default)]
    pub share_enabled: bool,
    #[serde(default)]
    pub viewer_available: bool,
    #[serde(default)]
    pub source_notes_available: bool,
    pub thumbnail_url: Option<String>,
    pub latest_successful_run_id: Option<i64>,
    pub latest_run: Option<LearningDeckRunResponse>,
    #[schemars(with = "String")]
    #[schema(value_type = String, format = DateTime)]
    pub created_at: DateTime<Utc>,
    #[schemars(with = "Option<String>")]
    #[schema(value_type = Option<String>, format = DateTime)]
    pub updated_at: Option<DateTime<Utc>>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct LearningDeckListResponse {
    #[serde(default)]
    pub decks: Vec<LearningDeckResponse>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct LearningDeckUrlResponse {
    pub url: String,
    #[schemars(with = "Option<String>")]
    #[schema(value_type = Option<String>, format = DateTime)]
    pub expires_at: Option<DateTime<Utc>>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct LearningDeckShareResponse {
    pub share_enabled: bool,
    pub share_url: Option<String>,
}
