//! Immutable cached-vector inputs and atomic nightly routing publication.
use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use sqlx::{Postgres, Transaction};

use super::{BriefingRefreshSource, BriefingSemanticLens};

mod naming;
mod publication;
mod snapshot;
pub use naming::{cached_naming, record_naming_attempt};

pub use publication::{apply_partition, load_candidate, save_candidate};
pub use snapshot::{load_snapshot, lock_snapshot_inputs};

#[derive(Debug, Clone)]
pub struct NewsCategoryStory {
    pub source: BriefingRefreshSource,
    /// Canonical relation representative. Visible-news snapshots normally contain
    /// one row per representative, while this explicit identity keeps support
    /// correct if that selection broadens later.
    pub event_id: i64,
    pub available_at: DateTime<Utc>,
    pub vector: Vec<f64>,
}

#[derive(Debug, Clone, PartialEq, Eq, sqlx::FromRow)]
pub struct NewsCategoryPending {
    pub id: i64,
    pub source_id: i64,
    pub lens_key: Option<String>,
}

#[derive(Debug, Clone)]
pub struct NewsCategorySnapshot {
    pub user_id: i64,
    pub cutoff: DateTime<Utc>,
    pub model: String,
    pub stories: Vec<NewsCategoryStory>,
    pub lenses: Vec<BriefingSemanticLens>,
    pub all_keys: Vec<String>,
    pub pending: Vec<NewsCategoryPending>,
    /// Includes corpus, eligibility configuration, routing profiles and pending ownership.
    pub fingerprint: String,
    /// Corpus alone allows unchanged-input detection despite normal Briefing version bumps.
    pub corpus_hash: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct NewsCategoryPublicationLens {
    pub key: String,
    pub title: String,
    pub deck: String,
    pub routing_rule: String,
    pub centroid: Vec<f64>,
    pub story_ids: Vec<i64>,
}

#[derive(Debug, Clone, Serialize, Deserialize, sqlx::FromRow)]
pub struct NewsCategoryCandidate {
    pub input_hash: String,
    pub candidate: Value,
    pub naming_result: Option<Value>,
}

pub async fn snapshot_is_current(
    tx: &mut Transaction<'_, Postgres>,
    snapshot: &NewsCategorySnapshot,
) -> Result<bool, super::BriefingRefreshRepositoryError> {
    lock_snapshot_inputs(tx, snapshot).await?;
    let current = load_snapshot(tx, snapshot.user_id, snapshot.cutoff, &snapshot.model).await?;
    Ok(current.is_some_and(|current| current.fingerprint == snapshot.fingerprint))
}

#[cfg(test)]
mod tests;
