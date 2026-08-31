use chrono::{DateTime, Utc};
use newsly_agent_runtime::ProviderUsage;
use newsly_extraction::{ExtractIntent, ExtractionMethod, ExtractionTiming, UsageEvent};
use serde_json::{Map, Value};
use sqlx::FromRow;

use super::storage::StagedContentBody;

const TERMINAL_CONTENT_STATUSES: [&str; 3] = ["completed", "failed", "skipped"];

#[derive(Debug, Clone, FromRow)]
pub(super) struct ContentSnapshot {
    pub(super) content_type: String,
    pub(super) url: String,
    pub(super) title: Option<String>,
    pub(super) status: String,
    pub(super) content_metadata: Value,
    pub(super) platform: Option<String>,
    pub(super) body_storage_provider: Option<String>,
    pub(super) body_storage_key: Option<String>,
}

impl ContentSnapshot {
    pub(super) fn source_body_pointer(&self) -> Option<ContentBodyPointer> {
        Some(ContentBodyPointer {
            storage_provider: self.body_storage_provider.clone()?,
            storage_key: self.body_storage_key.clone()?,
        })
    }

    pub(super) fn is_terminal(&self) -> bool {
        TERMINAL_CONTENT_STATUSES.contains(&self.status.as_str())
    }
}

#[derive(Debug, Clone)]
pub(super) struct ContentBodyPointer {
    pub(super) storage_provider: String,
    pub(super) storage_key: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(super) struct FeedCandidate {
    pub(super) url: String,
    pub(super) feed_type: String,
    pub(super) title: Option<String>,
}

impl FeedCandidate {
    pub(super) fn to_json(&self) -> Value {
        let mut value = Map::new();
        value.insert("url".to_owned(), Value::String(self.url.clone()));
        value.insert("type".to_owned(), Value::String(self.feed_type.clone()));
        if let Some(title) = &self.title {
            value.insert("title".to_owned(), Value::String(title.clone()));
        }
        value.insert(
            "source".to_owned(),
            Value::String("document_extractor".to_owned()),
        );
        Value::Object(value)
    }
}

#[derive(Debug, Clone)]
pub(crate) struct ExtractionUsageBatch {
    pub(crate) request_id: String,
    pub(crate) intent: ExtractIntent,
    pub(crate) method: Option<ExtractionMethod>,
    pub(crate) events: Vec<UsageEvent>,
}

#[derive(Debug, Clone)]
pub(crate) struct FirecrawlUsage {
    pub(crate) request_id: String,
    pub(crate) url: String,
    pub(crate) status_code: u16,
    pub(crate) cost_usd: Option<f64>,
}

#[derive(Debug, Clone)]
pub(crate) struct ModelUsageWrite {
    pub(crate) provider: String,
    pub(crate) model: String,
    pub(crate) response_id: Option<String>,
    pub(crate) usage: ProviderUsage,
}

#[derive(Debug, Clone)]
pub(crate) struct XUsageWrite {
    pub(crate) request_id: String,
    pub(crate) operation: &'static str,
    pub(crate) resource_count: usize,
}

#[derive(Debug, Clone)]
pub(crate) enum UsageWrite {
    Extraction(ExtractionUsageBatch),
    Firecrawl(FirecrawlUsage),
    Model(ModelUsageWrite),
    X(XUsageWrite),
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(super) struct InstructionLinkPlan {
    pub(super) url: String,
    pub(super) title: Option<String>,
    pub(super) context: Option<String>,
    pub(super) content_type: Option<String>,
    pub(super) platform: Option<String>,
    pub(super) source: Option<String>,
}

#[derive(Debug, Clone)]
pub(crate) struct ExtractedArticle {
    pub(crate) original_url: String,
    pub(crate) final_url: String,
    pub(crate) title: String,
    pub(super) author: Option<String>,
    pub(super) published_at: Option<DateTime<Utc>>,
    pub(crate) body: String,
    pub(super) feed_candidates: Vec<FeedCandidate>,
    pub(crate) extraction_method: String,
    pub(super) warnings: Vec<String>,
    pub(super) timings: Vec<ExtractionTiming>,
    pub(super) used_firecrawl: bool,
}

#[derive(Debug, Clone)]
pub(super) enum ContentMutation {
    AnalyzeClassified {
        content_type: String,
        platform: Option<String>,
        metadata_updates: Map<String, Value>,
        subscribe_to_feed: bool,
        scrub_instruction: bool,
    },
    AnalyzeSuccess {
        content_type: String,
        platform: Option<String>,
        title: String,
        body: StagedContentBody,
        body_char_count: usize,
        feed_candidates: Vec<FeedCandidate>,
        extraction_method: String,
        warnings: Vec<String>,
        timings: Vec<ExtractionTiming>,
        metadata_updates: Map<String, Value>,
        instruction_links: Vec<InstructionLinkPlan>,
        subscribe_to_feed: bool,
        scrub_instruction: bool,
    },
    AnalyzeTweet {
        target_url: String,
        content_type: String,
        platform: String,
        title: Option<String>,
        metadata_updates: Map<String, Value>,
        body: Option<StagedContentBody>,
        body_char_count: usize,
        scrub_instruction: bool,
    },
    ProcessArticle {
        article: ExtractedArticle,
        body: StagedContentBody,
        subscribe_to_feed: bool,
    },
    PodcastHandoff,
    ExtractionFailure {
        stage: &'static str,
        reason: String,
        code: String,
        terminal: bool,
        scrub_instruction: bool,
    },
}

#[derive(Debug, Clone)]
pub(super) struct ContentFinalizationPlan {
    pub(super) task_id: i64,
    pub(super) content_id: i64,
    pub(super) mutation: ContentMutation,
    pub(super) usage: Vec<UsageWrite>,
}
