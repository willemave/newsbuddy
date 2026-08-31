use chrono::{DateTime, Utc};
use newsly_agent_runtime::ProviderUsage;
use newsly_domain::NewsRelationDocument;
use newsly_providers::{LinkCandidate, NewsSummary, RelevantLink};
use serde_json::Value;

use crate::content::UsageWrite;

use super::storage::StagedNewsBody;

#[derive(Debug, Clone, PartialEq, Eq)]
pub(super) struct BodyPointer {
    pub(super) storage_provider: String,
    pub(super) storage_key: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(super) enum BodySource {
    Inline(String),
    Stored(BodyPointer),
    None,
}

#[derive(Debug, Clone)]
pub(super) struct NewsSnapshot {
    pub(super) id: i64,
    pub(super) owner_user_id: Option<i64>,
    pub(super) visibility_scope: String,
    pub(super) platform: Option<String>,
    pub(super) source_type: Option<String>,
    pub(super) source_label: Option<String>,
    pub(super) source_external_id: Option<String>,
    pub(super) canonical_item_url: Option<String>,
    pub(super) canonical_story_url: Option<String>,
    pub(super) article_url: Option<String>,
    pub(super) article_domain: Option<String>,
    pub(super) discussion_url: Option<String>,
    pub(super) summary_key_points: Vec<String>,
    pub(super) summary_text: Option<String>,
    pub(super) raw_metadata: Value,
    pub(super) ingested_at: DateTime<Utc>,
    pub(super) body_source: BodySource,
    pub(super) fingerprint: String,
}

impl NewsSnapshot {
    pub(super) fn relation_document(&self, summary: &NewsSummary) -> NewsRelationDocument {
        NewsRelationDocument {
            id: self.id,
            primary_title: Some(summary.title.clone()),
            related_titles: Vec::new(),
            summary_key_points: summary.key_points.clone(),
            summary_text: Some(summary.summary.clone()),
            article_domain: self.article_domain.clone(),
            source_label: self.source_label.clone(),
            platform: self.platform.clone(),
            exact_relation_key: super::input::exact_relation_key(self),
            ingested_at: Some(self.ingested_at),
        }
    }
}

#[derive(Debug, Clone)]
pub(super) enum EnrichmentPreparation {
    NotFound,
    Existing {
        snapshot: NewsSnapshot,
    },
    Metadata {
        snapshot: NewsSnapshot,
        text: String,
        source_url: Option<String>,
    },
    Content {
        snapshot: NewsSnapshot,
        content_id: i64,
        final_url: Option<String>,
        source_metadata: Option<Value>,
    },
    Extract {
        snapshot: NewsSnapshot,
        article_url: String,
    },
    Skip {
        snapshot: NewsSnapshot,
        reason: String,
    },
}

impl EnrichmentPreparation {
    pub(super) fn snapshot(&self) -> Option<&NewsSnapshot> {
        match self {
            Self::NotFound => None,
            Self::Existing { snapshot }
            | Self::Metadata { snapshot, .. }
            | Self::Content { snapshot, .. }
            | Self::Extract { snapshot, .. }
            | Self::Skip { snapshot, .. } => Some(snapshot),
        }
    }
}

#[derive(Debug, Clone)]
pub(super) enum EnrichmentMutation {
    Existing,
    Metadata {
        text: String,
        source_url: Option<String>,
    },
    Content {
        content_id: i64,
        article_url: String,
        final_url: Option<String>,
        extracted_chars: i32,
        source_metadata: Option<Value>,
    },
    Storage {
        article_url: String,
        final_url: String,
        title: Option<String>,
        article_domain: Option<String>,
        extraction_method: String,
        body: StagedNewsBody,
    },
    Skipped {
        article_url: Option<String>,
        reason: String,
    },
    Failed {
        article_url: Option<String>,
        final_url: Option<String>,
        strategy: Option<String>,
        reason: String,
    },
}

#[derive(Debug, Clone)]
pub(super) struct EnrichmentFinalizationPlan {
    pub(super) task_id: i64,
    pub(super) snapshot: NewsSnapshot,
    pub(super) mutation: EnrichmentMutation,
    pub(super) usage: Vec<UsageWrite>,
    pub(super) finalized_at: DateTime<Utc>,
}

#[derive(Debug, Clone)]
pub(super) struct ProcessPreparation {
    pub(super) snapshot: NewsSnapshot,
    pub(super) reusable_summary: Option<NewsSummary>,
    pub(super) reusable_representative_id: Option<i64>,
}

#[derive(Debug, Clone)]
pub(super) struct RelationCandidate {
    pub(super) document: NewsRelationDocument,
    pub(super) fingerprint: String,
}

#[derive(Debug, Clone)]
pub(super) struct ModelUsageWrite {
    pub(super) provider: String,
    pub(super) model: String,
    pub(super) feature: &'static str,
    pub(super) operation: &'static str,
    pub(super) provider_response_id: Option<String>,
    pub(super) usage: ProviderUsage,
    pub(super) metadata: Value,
}

#[derive(Debug, Clone)]
pub(super) struct ProcessMutation {
    pub(super) summary: NewsSummary,
    pub(super) used_existing_summary: bool,
    pub(super) item_document: NewsRelationDocument,
    pub(super) candidates: Vec<RelationCandidate>,
    pub(super) accepted_ids: Vec<i64>,
    pub(super) relevant_links: Option<Vec<RelevantLink>>,
    pub(super) relation_trace: Value,
    pub(super) usage: Vec<ModelUsageWrite>,
}

#[derive(Debug, Clone)]
pub(super) struct ProcessFinalizationPlan {
    pub(super) task_id: i64,
    pub(super) snapshot: NewsSnapshot,
    pub(super) mutation: Option<ProcessMutation>,
    pub(super) failure: Option<String>,
    pub(super) failure_usage: Vec<ModelUsageWrite>,
    pub(super) terminal_failure: bool,
    pub(super) finalized_at: DateTime<Utc>,
    pub(super) briefing_debounce_seconds: i64,
    pub(super) briefing_batch_minimum: i64,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(super) enum NewsApplyOutcome {
    Applied,
    NewsItemMissing,
    SourceChanged,
    CandidateChanged,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(super) struct RelevantLinkInput {
    pub(super) title: Option<String>,
    pub(super) source_url: Option<String>,
    pub(super) candidates: Vec<LinkCandidate>,
}
