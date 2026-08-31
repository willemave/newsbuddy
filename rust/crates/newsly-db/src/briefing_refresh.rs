use std::collections::{BTreeMap, BTreeSet, HashMap, HashSet};

use chrono::{DateTime, NaiveDateTime, Utc};
use newsly_agent_runtime::ProviderUsage;
use serde_json::{Map, Value, json};
use sqlx::{AssertSqlSafe, FromRow, Postgres, Transaction};
use thiserror::Error;
use uuid::Uuid;

const DEFAULT_MASTHEAD_DECK: &str = "A fresh edition will appear as unread sources arrive.";
const FIXED_LENSES: [(&str, &str, &str, &str, i32); 2] = [
    (
        "podcasts",
        "audio",
        "Podcasts",
        "Unheard episodes ready for a focused listen.",
        0,
    ),
    (
        "articles",
        "longform",
        "Articles",
        "Long reads and essays waiting in your queue.",
        1,
    ),
];

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BriefingRefreshMode {
    Append,
    Sweep,
    Full,
}

impl BriefingRefreshMode {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Append => "append",
            Self::Sweep => "sweep",
            Self::Full => "full",
        }
    }
}

impl TryFrom<&str> for BriefingRefreshMode {
    type Error = BriefingRefreshRepositoryError;

    fn try_from(value: &str) -> Result<Self, Self::Error> {
        match value {
            "append" => Ok(Self::Append),
            "sweep" => Ok(Self::Sweep),
            "full" => Ok(Self::Full),
            _ => Err(BriefingRefreshRepositoryError::InvalidMode(
                value.to_owned(),
            )),
        }
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct BriefingRefreshConfig {
    pub masthead_title: String,
    pub window_min: usize,
    pub news_window_max: usize,
    pub new_lens_min_items: usize,
    pub pending_max_age_seconds: i64,
    pub max_news_lenses: usize,
    pub category_similarity: f64,
    pub category_cluster_similarity: f64,
    pub category_absorb_similarity: f64,
    pub centroid_max_weight: i32,
    pub sweep_seconds: i64,
    pub lens_idle_days: i64,
}

impl BriefingRefreshConfig {
    pub fn validate(&self) -> Result<(), BriefingRefreshRepositoryError> {
        if self.masthead_title.trim().is_empty()
            || self.masthead_title.chars().count() > 220
            || !(1..=12).contains(&self.window_min)
            || !(2..=4).contains(&self.news_window_max)
            || !(2..=20).contains(&self.new_lens_min_items)
            || !(60..=86_400).contains(&self.pending_max_age_seconds)
            || !(3..=30).contains(&self.max_news_lenses)
            || !(0.0..=1.0).contains(&self.category_similarity)
            || !(0.0..=1.0).contains(&self.category_cluster_similarity)
            || !(0.0..=1.0).contains(&self.category_absorb_similarity)
            || !self.category_similarity.is_finite()
            || !self.category_cluster_similarity.is_finite()
            || !self.category_absorb_similarity.is_finite()
            || !(4..=500).contains(&self.centroid_max_weight)
            || !(60..=86_400).contains(&self.sweep_seconds)
            || !(1..=90).contains(&self.lens_idle_days)
        {
            return Err(BriefingRefreshRepositoryError::InvalidConfig);
        }
        Ok(())
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BriefingRefreshLens {
    pub id: i64,
    pub key: String,
    pub tier: String,
    pub title: String,
    pub deck: String,
    pub position: i32,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BriefingRefreshSource {
    pub source_key: String,
    pub kind: String,
    pub id: i64,
    pub title: String,
    pub source_name: Option<String>,
    pub summary: Option<String>,
    pub key_points: Vec<String>,
    pub url: Option<String>,
    pub image_url: Option<String>,
    pub thumbnail_url: Option<String>,
    pub published_at: Option<DateTime<Utc>>,
    pub briefing_context: Option<String>,
}

impl BriefingRefreshSource {
    pub fn embedding_text(&self) -> String {
        let mut parts = vec![self.title.clone()];
        if let Some(summary) = self
            .summary
            .as_deref()
            .filter(|value| !value.trim().is_empty())
        {
            parts.push(summary.to_owned());
        }
        if !self.key_points.is_empty() {
            parts.push(self.key_points.join(" "));
        }
        parts.join("\n")
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BriefingPendingIdentity {
    pub id: i64,
    pub source_kind: String,
    pub source_id: i64,
    pub lens_key: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BriefingAppendBatch {
    pub lens: BriefingRefreshLens,
    pub pending_rows: Vec<BriefingPendingIdentity>,
    pub sources: Vec<BriefingRefreshSource>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BriefingDonorIdentity {
    pub segment_id: i64,
    pub source_keys: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BriefingCompactionBatch {
    pub lens: BriefingRefreshLens,
    pub donors: Vec<BriefingDonorIdentity>,
    pub planned_source_keys: Vec<String>,
    pub sources: Vec<BriefingRefreshSource>,
    pub repair_required: bool,
}

#[derive(Debug, Clone, PartialEq)]
pub struct BriefingSemanticLens {
    pub id: i64,
    pub key: String,
    pub title: String,
    pub deck: String,
    pub position: i32,
    pub centroid: Option<Vec<f64>>,
    pub centroid_weight: i32,
    pub centroid_model: Option<String>,
    pub routing_rule: Option<String>,
    pub updated_at: DateTime<Utc>,
}

impl BriefingSemanticLens {
    pub fn profile_text(&self) -> String {
        [
            self.title.as_str(),
            self.deck.as_str(),
            self.routing_rule.as_deref().unwrap_or(""),
        ]
        .into_iter()
        .filter(|value| !value.trim().is_empty())
        .collect::<Vec<_>>()
        .join("\n")
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BriefingUnassignedSource {
    pub pending_id: i64,
    pub source_kind: String,
    pub source_id: i64,
    pub enqueued_at: DateTime<Utc>,
    pub source: BriefingRefreshSource,
}

#[derive(Debug, Clone, PartialEq)]
pub struct BriefingLensAssignmentSnapshot {
    pub pending_sources: Vec<BriefingUnassignedSource>,
    pub active_lenses: Vec<BriefingSemanticLens>,
    pub active_news_lens_keys: Vec<String>,
    pub all_lens_keys: Vec<String>,
    pub next_news_position: i32,
}

#[derive(Debug, Clone, PartialEq)]
pub struct PreparedBriefingRefreshSeed {
    pub task_id: i64,
    pub user_id: i64,
    pub mode: BriefingRefreshMode,
    pub starting_version: i32,
    pub pending_added: usize,
    pub prepared_state_changed: bool,
    pub lens_assignment: BriefingLensAssignmentSnapshot,
    pub claim_fence: BriefingRefreshClaimFence,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BriefingRefreshClaimFence {
    pub locked_by: String,
    pub lease_token: Uuid,
    pub retry_count: i32,
    pub executor_runtime: String,
    pub executor_version: i64,
    pub executor_namespace: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BriefingPendingLensAssignment {
    pub pending_id: i64,
    pub source_kind: String,
    pub source_id: i64,
    pub lens_key: String,
}

#[derive(Debug, Clone, PartialEq)]
pub struct BriefingLensCentroidMutation {
    pub lens_id: i64,
    pub lens_key: String,
    pub centroid: Vec<f64>,
    pub centroid_weight: i32,
    pub centroid_model: String,
}

#[derive(Debug, Clone, PartialEq)]
pub struct BriefingPlannedLens {
    pub key: String,
    pub title: String,
    pub deck: String,
    pub position: i32,
    pub centroid: Option<Vec<f64>>,
    pub centroid_weight: i32,
    pub centroid_model: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BriefingLensAssignmentUsage {
    pub provider: String,
    pub model: String,
    pub provider_response_id: Option<String>,
    pub usage: ProviderUsage,
    pub feature: String,
    pub operation: String,
}

#[derive(Debug, Clone, PartialEq)]
pub struct BriefingLensAssignmentPlan {
    pub task_id: i64,
    pub user_id: i64,
    pub starting_version: i32,
    pub assignments: Vec<BriefingPendingLensAssignment>,
    pub centroid_mutations: Vec<BriefingLensCentroidMutation>,
    pub new_lenses: Vec<BriefingPlannedLens>,
    pub usage: Vec<BriefingLensAssignmentUsage>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PreparedBriefingRefresh {
    pub task_id: i64,
    pub user_id: i64,
    pub mode: BriefingRefreshMode,
    pub starting_version: i32,
    pub prepared_state_changed: bool,
    pub append_batches: Vec<BriefingAppendBatch>,
    pub compaction_batches: Vec<BriefingCompactionBatch>,
}

#[derive(Debug, Clone, PartialEq)]
pub enum PrepareBriefingRefreshOutcome {
    Disabled { version: i32 },
    Ready(PreparedBriefingRefreshSeed),
}

#[derive(Debug, Clone, PartialEq)]
pub enum ApplyBriefingLensAssignmentOutcome {
    Stale,
    Ready(PreparedBriefingRefresh),
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BriefingSegmentUsage {
    pub provider: String,
    pub model: String,
    pub provider_response_id: Option<String>,
    pub usage: ProviderUsage,
    pub operation: String,
}

#[derive(Debug, Clone, PartialEq)]
pub struct ComposedBriefingSegment {
    pub lens: BriefingRefreshLens,
    pub blocks: Value,
    pub markdown_raw: String,
    pub narration_text: String,
    pub source_keys: Vec<String>,
    pub event_groups: Vec<Vec<String>>,
    pub model: String,
    pub prompt_version: String,
    pub input_tokens: Option<i32>,
    pub output_tokens: Option<i32>,
    pub generation_ms: i32,
    pub warnings: Vec<String>,
    pub usage: BriefingSegmentUsage,
}

#[derive(Debug, Clone, PartialEq)]
pub struct ComposedBriefingAppend {
    pub pending_rows: Vec<BriefingPendingIdentity>,
    pub segment: ComposedBriefingSegment,
}

#[derive(Debug, Clone, PartialEq)]
pub struct ComposedBriefingCompaction {
    pub donors: Vec<BriefingDonorIdentity>,
    pub planned_source_keys: Vec<String>,
    pub segments: Vec<ComposedBriefingSegment>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BriefingEmbeddingUsage {
    pub provider: String,
    pub model: String,
    pub provider_response_id: Option<String>,
    pub usage: ProviderUsage,
}

#[derive(Debug, Clone, PartialEq)]
pub struct BriefingRefreshPublication {
    pub prepared: PreparedBriefingRefresh,
    pub append_segments: Vec<ComposedBriefingAppend>,
    pub compactions: Vec<ComposedBriefingCompaction>,
    pub embedding_usage: Vec<BriefingEmbeddingUsage>,
    pub finalized_at: DateTime<Utc>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BriefingRefreshApplyOutcome {
    pub version: i32,
    pub appended_segments: usize,
    pub compacted_segments: usize,
    pub retired_segments: usize,
    pub stale: bool,
    pub next_sweep_delay_seconds: i64,
}

#[derive(Debug, FromRow)]
struct PendingContentRow {
    pending_id: i64,
    lens_key: String,
    enqueued_at: NaiveDateTime,
    id: i64,
    content_type: String,
    url: String,
    source_url: Option<String>,
    title: Option<String>,
    source: Option<String>,
    metadata: Value,
    created_at: NaiveDateTime,
    publication_date: Option<NaiveDateTime>,
}

#[derive(Debug, FromRow)]
struct PendingNewsRow {
    pending_id: i64,
    lens_key: String,
    enqueued_at: NaiveDateTime,
    id: i64,
    summary_text: Option<String>,
    summary_key_points: Value,
    raw_metadata: Value,
    article_url: Option<String>,
    canonical_story_url: Option<String>,
    canonical_item_url: Option<String>,
    published_at: Option<NaiveDateTime>,
    processed_at: Option<NaiveDateTime>,
    ingested_at: NaiveDateTime,
    created_at: NaiveDateTime,
}

#[derive(Debug, FromRow)]
struct SourceContentRow {
    id: i64,
    content_type: String,
    url: String,
    source_url: Option<String>,
    title: Option<String>,
    source: Option<String>,
    metadata: Value,
    created_at: NaiveDateTime,
    publication_date: Option<NaiveDateTime>,
}

#[derive(Debug, FromRow)]
struct SourceNewsRow {
    id: i64,
    summary_text: Option<String>,
    summary_key_points: Value,
    raw_metadata: Value,
    article_url: Option<String>,
    canonical_story_url: Option<String>,
    canonical_item_url: Option<String>,
    published_at: Option<NaiveDateTime>,
    processed_at: Option<NaiveDateTime>,
    ingested_at: NaiveDateTime,
    created_at: NaiveDateTime,
}

#[derive(Debug, FromRow)]
struct LensRow {
    id: i64,
    key: String,
    tier: String,
    title: String,
    deck: String,
    position: i32,
}

impl From<LensRow> for BriefingRefreshLens {
    fn from(row: LensRow) -> Self {
        Self {
            id: row.id,
            key: row.key,
            tier: row.tier,
            title: row.title,
            deck: row.deck,
            position: row.position,
        }
    }
}

#[derive(Debug, FromRow)]
struct SegmentRow {
    id: i64,
    lens_id: i64,
    lens_key: String,
    lens_tier: String,
    lens_title: String,
    lens_deck: String,
    lens_position: i32,
    source_keys: Value,
    event_groups: Option<Value>,
}

#[derive(Debug, FromRow)]
struct UnassignedNewsRow {
    pending_id: i64,
    enqueued_at: NaiveDateTime,
    id: i64,
    summary_text: Option<String>,
    summary_key_points: Value,
    raw_metadata: Value,
    article_url: Option<String>,
    canonical_story_url: Option<String>,
    canonical_item_url: Option<String>,
    published_at: Option<NaiveDateTime>,
    processed_at: Option<NaiveDateTime>,
    ingested_at: NaiveDateTime,
    created_at: NaiveDateTime,
}

#[derive(Debug, FromRow)]
struct SemanticLensRow {
    id: i64,
    key: String,
    title: String,
    deck: String,
    position: i32,
    centroid: Option<Value>,
    centroid_weight: i32,
    centroid_model: Option<String>,
    routing_rule: Option<String>,
    updated_at: NaiveDateTime,
}

#[derive(Debug, Error)]
pub enum BriefingRefreshRepositoryError {
    #[error("unsupported Briefing refresh mode {0:?}")]
    InvalidMode(String),
    #[error("Briefing refresh configuration is invalid")]
    InvalidConfig,
    #[error("Briefing refresh claim ownership was lost before preparation")]
    ClaimOwnershipLost,
    #[error("Briefing lens assignment plan is invalid: {0}")]
    InvalidLensAssignmentPlan(String),
    #[error("stored Briefing lens centroid is invalid")]
    InvalidStoredCentroid,
    #[error("Briefing pending-source ownership changed during publication")]
    PendingOwnershipLost,
    #[error("Briefing refresh PostgreSQL operation failed")]
    Sqlx(#[from] sqlx::Error),
}

mod lens_assignment;
mod preparation;
mod publication;
mod sources;

pub use lens_assignment::apply_briefing_lens_assignment;
pub use preparation::prepare_briefing_refresh;
pub use publication::apply_briefing_refresh;

#[cfg(test)]
mod tests;
