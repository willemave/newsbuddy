use serde::{Deserialize, Serialize};
use thiserror::Error;

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ReplayNewsLensesRequest {
    pub schema_version: u16,
    pub items: Vec<ReplayItem>,
    pub checkpoints: Vec<i64>,
    pub variants: Vec<ReplayVariant>,
    #[serde(default = "default_max_items")]
    pub max_items: usize,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ReplayItem {
    pub id: String,
    pub available_at: i64,
    pub vector: Vec<f64>,
    #[serde(default)]
    pub title: String,
    #[serde(default)]
    pub event_id: String,
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum ReplayAlgorithm {
    FrozenInitial,
    IncrementalSurrogate,
    RollingSphericalKmeans,
    NoveltyAware,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ReplayVariant {
    pub label: String,
    pub algorithm: ReplayAlgorithm,
    #[serde(default = "default_lookback_days")]
    pub lookback_days: u32,
    #[serde(default = "default_k")]
    pub k: usize,
    #[serde(default)]
    pub warm_start: bool,
    #[serde(default)]
    pub decay_half_life_days: Option<f64>,
    #[serde(default)]
    pub seed: u64,
    #[serde(default = "default_iterations")]
    pub max_iterations: usize,
    #[serde(default = "default_min_cluster_size")]
    pub min_cluster_size: usize,
    #[serde(default = "default_match_threshold")]
    pub match_threshold: f64,
    #[serde(default = "default_absorb_threshold")]
    pub absorb_threshold: f64,
    #[serde(default = "default_identity_similarity")]
    pub identity_similarity: f64,
    #[serde(default)]
    pub confidence_threshold: Option<f64>,
}

#[derive(Clone, Debug, Serialize)]
pub struct ReplayNewsLensesResponse {
    pub schema_version: u16,
    pub disclosures: Vec<String>,
    pub variants: Vec<ReplayVariantResult>,
}

#[derive(Clone, Debug, Serialize)]
pub struct ReplayVariantResult {
    pub label: String,
    pub algorithm: ReplayAlgorithm,
    pub config: ReplayVariant,
    pub checkpoints: Vec<ReplayCheckpoint>,
}

#[derive(Clone, Debug, Serialize)]
pub struct ReplayCheckpoint {
    pub cutoff: i64,
    pub training_change: TrainingChange,
    pub metrics: ReplayMetrics,
    pub clusters: Vec<ReplayCluster>,
    pub assignments: Vec<ReplayAssignment>,
}

#[derive(Clone, Debug, Serialize)]
pub struct TrainingChange {
    pub entered: usize,
    pub exited: usize,
}

#[derive(Clone, Debug, Serialize)]
pub struct ReplayMetrics {
    pub n_seen: usize,
    pub n_window: usize,
    pub n_clustered: usize,
    pub noise_fraction: f64,
    pub cluster_count: usize,
    pub mean_cosine_to_center: f64,
    pub p10_cosine: f64,
    pub mean_top1_top2_margin: f64,
    pub largest_cluster_fraction: f64,
    pub new_arrival_count: usize,
    pub pre_recluster_mean_cosine: Option<f64>,
    pub pre_recluster_p10_cosine: Option<f64>,
    pub pre_recluster_mean_margin: Option<f64>,
    pub pre_recluster_weak_fraction: Option<f64>,
    pub pre_recluster_match_coverage: Option<f64>,
    pub adjusted_rand_shared_active: Option<f64>,
    pub matched_movement_shared_active: Option<f64>,
    pub centroid_drift: Option<f64>,
    pub births: usize,
    pub deaths: usize,
    pub elapsed_ms: f64,
}

#[derive(Clone, Debug, Serialize)]
pub struct ReplayCluster {
    pub cluster_key: String,
    pub representative_ids: Vec<String>,
}

#[derive(Clone, Copy, Debug, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum AssignmentState {
    Active,
    Retained,
}

#[derive(Clone, Debug, Serialize)]
pub struct ReplayAssignment {
    pub item_id: String,
    pub event_id: String,
    pub cluster_key: Option<String>,
    pub state: AssignmentState,
    pub cosine_to_center: Option<f64>,
    pub margin: Option<f64>,
}

#[derive(Debug, Error)]
pub enum ReplayError {
    #[error("unsupported replay schema version {0}; expected 1")]
    UnsupportedVersion(u16),
    #[error("max_items must be between 1 and 10000")]
    InvalidMaxItems,
    #[error("replay requires at least one item, checkpoint, and variant")]
    EmptyInput,
    #[error("item id must not be empty")]
    EmptyItemId,
    #[error("duplicate item id {0}")]
    DuplicateItem(String),
    #[error("item {id} has invalid vector: {reason}")]
    InvalidVector { id: String, reason: &'static str },
    #[error("item {id} has dimension {actual}; expected {expected}")]
    Dimension {
        id: String,
        actual: usize,
        expected: usize,
    },
    #[error("checkpoints must be strictly increasing")]
    CheckpointsNotIncreasing,
    #[error("variant {label} is invalid: {reason}")]
    InvalidVariant { label: String, reason: &'static str },
}

#[derive(Clone)]
pub(crate) struct WorkItem {
    pub(crate) id: String,
    pub(crate) event_id: String,
    pub(crate) available_at: i64,
    pub(crate) vector: Vec<f64>,
}

#[derive(Clone)]
pub(super) struct ClusterState {
    pub key: String,
    pub center: Vec<f64>,
    pub members: Vec<usize>,
}

pub(super) struct RawResult {
    pub centers: Vec<Vec<f64>>,
    pub labels: Vec<Option<usize>>,
    pub similarities: Vec<Option<f64>>,
    pub margins: Vec<Option<f64>>,
}

#[derive(Default)]
pub(super) struct IdentityResult {
    pub keys: Vec<String>,
    pub births: usize,
    pub deaths: usize,
    pub mean_drift: Option<f64>,
}

#[derive(Default)]
pub(super) struct PreReclusterFit {
    pub count: usize,
    pub similarities: Vec<f64>,
    pub margins: Vec<f64>,
    pub weak_count: usize,
    pub match_count: usize,
}

fn default_max_items() -> usize {
    1_500
}
fn default_lookback_days() -> u32 {
    14
}
fn default_k() -> usize {
    10
}
fn default_iterations() -> usize {
    20
}
fn default_min_cluster_size() -> usize {
    3
}
fn default_match_threshold() -> f64 {
    0.55
}
fn default_absorb_threshold() -> f64 {
    0.45
}
fn default_identity_similarity() -> f64 {
    0.7
}
