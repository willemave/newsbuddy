use chrono::{DateTime, Utc};
use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use utoipa::ToSchema;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
#[serde(rename_all = "snake_case")]
pub enum AgentLibraryDocumentVariant {
    Source,
    Summary,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
#[serde(rename_all = "snake_case")]
pub enum AgentSearchResultKind {
    Web,
    Podcast,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct AgentLibraryDocumentResponse {
    pub relative_path: String,
    pub content_id: i64,
    pub variant: AgentLibraryDocumentVariant,
    #[schemars(with = "Option<String>")]
    #[schema(value_type = Option<String>, format = DateTime)]
    pub updated_at: Option<DateTime<Utc>>,
    pub size_bytes: usize,
    pub checksum_sha256: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct AgentLibraryManifestResponse {
    #[schemars(with = "String")]
    #[schema(value_type = String, format = DateTime)]
    pub generated_at: DateTime<Utc>,
    #[serde(default = "default_true")]
    pub include_source: bool,
    pub documents: Vec<AgentLibraryDocumentResponse>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct AgentLibraryFileResponse {
    pub relative_path: String,
    pub content_id: i64,
    pub variant: AgentLibraryDocumentVariant,
    #[schemars(with = "Option<String>")]
    #[schema(value_type = Option<String>, format = DateTime)]
    pub updated_at: Option<DateTime<Utc>>,
    pub checksum_sha256: String,
    pub text: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct AgentSearchRequest {
    #[schemars(length(min = 2, max = 200))]
    #[schema(min_length = 2, max_length = 200)]
    pub query: String,
    #[serde(default = "default_search_limit")]
    #[schemars(range(min = 1, max = 25))]
    #[schema(minimum = 1, maximum = 25, default = 10)]
    pub limit: usize,
    #[serde(default = "default_true")]
    pub include_podcasts: bool,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct AgentSearchResultResponse {
    pub kind: AgentSearchResultKind,
    pub title: String,
    pub url: String,
    pub snippet: Option<String>,
    pub source: Option<String>,
    pub provider: Option<String>,
    pub feed_url: Option<String>,
    pub published_at: Option<String>,
    pub score: Option<f64>,
}

#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct AgentSearchResponse {
    #[serde(default)]
    pub results: Vec<AgentSearchResultResponse>,
}

const fn default_true() -> bool {
    true
}

const fn default_search_limit() -> usize {
    10
}
