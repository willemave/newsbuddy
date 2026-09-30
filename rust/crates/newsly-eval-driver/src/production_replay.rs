use std::collections::{HashMap, HashSet};

use newsly_domain::{
    NewsLensClusterIdentity, NewsLensReclusterExistingCluster, NewsLensReclusterStory,
    NewsLensReclusteringConfig, NewsLensReclusteringError, NewsLensReclusteringInput,
    NewsLensReclusteringPlan, plan_news_lens_reclustering,
};
use serde::Serialize;
use thiserror::Error;

use crate::replay::{ReplayError, ReplayNewsLensesRequest, validate_and_prepare};

#[derive(Debug, Error)]
pub enum ProductionNewsLensReplayError {
    #[error(transparent)]
    Replay(#[from] ReplayError),
    #[error(transparent)]
    Reclustering(#[from] NewsLensReclusteringError),
}

#[derive(Clone, Debug, Serialize)]
pub struct ProductionNewsLensReplayResponse {
    pub schema_version: u16,
    pub disclosures: Vec<String>,
    pub checkpoints: Vec<ProductionReplayCheckpoint>,
}

#[derive(Clone, Debug, Serialize)]
pub struct ProductionReplayCheckpoint {
    pub cutoff: i64,
    pub plan: NewsLensReclusteringPlan,
    pub prequential: ProductionPrequentialMetrics,
    pub retained_reachability: RetainedReachability,
    pub assignments: Vec<ProductionReplayAssignment>,
}

#[derive(Clone, Debug, Serialize)]
pub struct ProductionPrequentialMetrics {
    pub new_arrival_count: usize,
    pub scored_count: usize,
    pub mean_best_cosine: Option<f64>,
    pub p10_best_cosine: Option<f64>,
    pub mean_top1_top2_margin: Option<f64>,
    pub weak_fraction: Option<f64>,
}

#[derive(Clone, Debug, Serialize)]
pub struct RetainedReachability {
    pub seen: usize,
    pub active: usize,
    pub mixed: usize,
    pub retained: usize,
    pub missing: usize,
}

#[derive(Clone, Copy, Debug, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ProductionAssignmentState {
    Active,
    Mixed,
    Retained,
}

#[derive(Clone, Debug, Serialize)]
pub struct ProductionReplayAssignment {
    pub item_id: String,
    pub event_id: String,
    pub cluster_id: Option<String>,
    pub state: ProductionAssignmentState,
}

/// Replays the production reclustering core over an existing offline replay request.
///
/// # Errors
/// Returns a validation error when the contract, vectors, or clustering bounds are invalid.
#[allow(clippy::too_many_lines)]
pub fn replay_production_news_lenses(
    request: &ReplayNewsLensesRequest,
) -> Result<ProductionNewsLensReplayResponse, ProductionNewsLensReplayError> {
    let items = validate_and_prepare(request)?;
    let dimensions = items[0].vector.len();
    let stories = items
        .iter()
        .map(|item| NewsLensReclusterStory {
            story_id: item.id.clone(),
            event_id: if item.event_id.is_empty() {
                item.id.clone()
            } else {
                item.event_id.clone()
            },
            available_at: item.available_at,
            vector: item.vector.clone(),
        })
        .collect::<Vec<_>>();
    let config = NewsLensReclusteringConfig {
        expected_dimensions: Some(dimensions),
        max_items: request.max_items.min(1_500),
        ..NewsLensReclusteringConfig::default()
    };
    let mut existing = Vec::<NewsLensReclusterExistingCluster>::new();
    let mut retained = HashMap::<String, Option<String>>::new();
    let mut checkpoints = Vec::with_capacity(request.checkpoints.len());
    let mut previous_cutoff = None;
    let mut next_cluster_id = 1usize;
    for &cutoff in &request.checkpoints {
        let arrivals = stories
            .iter()
            .filter(|story| {
                story.available_at <= cutoff
                    && previous_cutoff.is_none_or(|previous| story.available_at > previous)
            })
            .collect::<Vec<_>>();
        let prequential = score_prequential(&arrivals, &existing, config.weak_similarity);
        let mut plan = plan_news_lens_reclustering(&NewsLensReclusteringInput {
            now: cutoff,
            stories: stories.clone(),
            existing_clusters: existing.clone(),
            config: config.clone(),
        })?;
        let mut remapped = HashMap::new();
        for cluster in &mut plan.clusters {
            if matches!(cluster.identity, NewsLensClusterIdentity::New) {
                let stable_id = format!("production-cluster-{next_cluster_id}");
                next_cluster_id += 1;
                remapped.insert(cluster.plan_id.clone(), stable_id.clone());
                cluster.plan_id = stable_id;
            }
        }
        for lineage in &mut plan.lineage {
            if let Some(plan_id) = lineage.new_plan_id.as_mut()
                && let Some(stable_id) = remapped.get(plan_id)
            {
                plan_id.clone_from(stable_id);
            }
        }
        if !plan.no_op {
            existing = plan
                .clusters
                .iter()
                .map(|cluster| NewsLensReclusterExistingCluster {
                    stable_id: cluster.plan_id.clone(),
                    center: cluster.center.clone(),
                    member_story_ids: cluster.story_ids.clone(),
                })
                .collect();
        }
        let active = plan
            .clusters
            .iter()
            .flat_map(|cluster| {
                cluster
                    .story_ids
                    .iter()
                    .map(|story_id| (story_id.clone(), cluster.plan_id.clone()))
            })
            .collect::<HashMap<_, _>>();
        let mixed = plan.mixed_story_ids.iter().cloned().collect::<HashSet<_>>();
        for story in stories.iter().filter(|story| story.available_at <= cutoff) {
            if let Some(cluster_id) = active.get(&story.story_id) {
                retained.insert(story.story_id.clone(), Some(cluster_id.clone()));
            } else if mixed.contains(&story.story_id) {
                retained.insert(story.story_id.clone(), None);
            } else {
                retained.entry(story.story_id.clone()).or_insert(None);
            }
        }
        let mut assignments = stories
            .iter()
            .filter(|story| story.available_at <= cutoff)
            .map(|story| {
                let state = if active.contains_key(&story.story_id) {
                    ProductionAssignmentState::Active
                } else if mixed.contains(&story.story_id) {
                    ProductionAssignmentState::Mixed
                } else {
                    ProductionAssignmentState::Retained
                };
                ProductionReplayAssignment {
                    item_id: story.story_id.clone(),
                    event_id: story.event_id.clone(),
                    cluster_id: retained.get(&story.story_id).cloned().flatten(),
                    state,
                }
            })
            .collect::<Vec<_>>();
        assignments.sort_by(|left, right| left.item_id.cmp(&right.item_id));
        let reachability = RetainedReachability {
            seen: assignments.len(),
            active: assignments
                .iter()
                .filter(|assignment| matches!(assignment.state, ProductionAssignmentState::Active))
                .count(),
            mixed: assignments
                .iter()
                .filter(|assignment| matches!(assignment.state, ProductionAssignmentState::Mixed))
                .count(),
            retained: assignments
                .iter()
                .filter(|assignment| {
                    matches!(assignment.state, ProductionAssignmentState::Retained)
                })
                .count(),
            missing: assignments
                .iter()
                .filter(|assignment| !retained.contains_key(&assignment.item_id))
                .count(),
        };
        checkpoints.push(ProductionReplayCheckpoint {
            cutoff,
            plan,
            prequential,
            retained_reachability: reachability,
            assignments,
        });
        previous_cutoff = Some(cutoff);
    }
    Ok(ProductionNewsLensReplayResponse {
        schema_version: 1,
        disclosures: vec![
            "Runs the production pure clustering core over retrospective final-text vectors."
                .to_owned(),
            "No provider or naming calls are made.".to_owned(),
            "Out-of-window stories retain their last routing identity or explicit mixed coverage."
                .to_owned(),
        ],
        checkpoints,
    })
}

fn score_prequential(
    arrivals: &[&NewsLensReclusterStory],
    existing: &[NewsLensReclusterExistingCluster],
    weak_similarity: f64,
) -> ProductionPrequentialMetrics {
    let mut best_scores = Vec::new();
    let mut margins = Vec::new();
    for story in arrivals {
        let Some(vector) = normalized(&story.vector) else {
            continue;
        };
        let mut scores = existing
            .iter()
            .filter_map(|cluster| normalized(&cluster.center).map(|center| dot(&vector, &center)))
            .collect::<Vec<_>>();
        if scores.is_empty() {
            continue;
        }
        scores.sort_by(|left, right| right.total_cmp(left));
        best_scores.push(scores[0]);
        margins.push(scores[0] - scores.get(1).copied().unwrap_or(0.0));
    }
    best_scores.sort_by(f64::total_cmp);
    let scored_count = best_scores.len();
    ProductionPrequentialMetrics {
        new_arrival_count: arrivals.len(),
        scored_count,
        mean_best_cosine: (!best_scores.is_empty()).then(|| mean(&best_scores)),
        p10_best_cosine: (!best_scores.is_empty())
            .then(|| best_scores[(best_scores.len() - 1) / 10]),
        mean_top1_top2_margin: (!margins.is_empty()).then(|| mean(&margins)),
        weak_fraction: (!best_scores.is_empty()).then(|| {
            ratio(
                best_scores
                    .iter()
                    .filter(|&&score| score < weak_similarity)
                    .count(),
                best_scores.len(),
            )
        }),
    }
}

fn normalized(vector: &[f64]) -> Option<Vec<f64>> {
    let norm = vector.iter().map(|value| value * value).sum::<f64>().sqrt();
    (vector.iter().all(|value| value.is_finite()) && norm > f64::EPSILON)
        .then(|| vector.iter().map(|value| value / norm).collect())
}
fn dot(left: &[f64], right: &[f64]) -> f64 {
    left.iter().zip(right).map(|(a, b)| a * b).sum()
}
fn mean(values: &[f64]) -> f64 {
    values.iter().sum::<f64>() / count(values.len())
}
fn ratio(numerator: usize, denominator: usize) -> f64 {
    count(numerator) / count(denominator)
}
fn count(value: usize) -> f64 {
    f64::from(u32::try_from(value).expect("bounded replay count fits in u32"))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::replay::{ReplayAlgorithm, ReplayItem, ReplayVariant};

    fn item(id: &str, event_id: &str, at: i64, x: f64, y: f64) -> ReplayItem {
        ReplayItem {
            id: id.to_owned(),
            event_id: event_id.to_owned(),
            available_at: at,
            vector: vec![x, y],
            title: String::new(),
        }
    }

    #[test]
    fn replay_preserves_every_seen_story_and_stable_ids() {
        let request = ReplayNewsLensesRequest {
            schema_version: 1,
            items: vec![
                item("a", "e1", 0, 1.0, 0.0),
                item("b", "e2", 1, 1.0, 0.0),
                item("c", "e3", 2, 1.0, 0.0),
                item("d", "e4", 100, 1.0, 0.0),
            ],
            checkpoints: vec![2, 100],
            variants: vec![ReplayVariant {
                label: "ignored".to_owned(),
                algorithm: ReplayAlgorithm::RollingSphericalKmeans,
                lookback_days: 14,
                k: 10,
                warm_start: true,
                decay_half_life_days: None,
                seed: 0,
                max_iterations: 20,
                min_cluster_size: 3,
                match_threshold: 0.55,
                absorb_threshold: 0.45,
                identity_similarity: 0.7,
                confidence_threshold: None,
            }],
            max_items: 1_500,
        };
        let response = replay_production_news_lenses(&request).unwrap();
        assert_eq!(response.checkpoints[0].assignments.len(), 3);
        assert_eq!(response.checkpoints[1].assignments.len(), 4);
        assert_eq!(response.checkpoints[1].retained_reachability.missing, 0);
        assert_eq!(
            response.checkpoints[0].plan.clusters[0].plan_id,
            response.checkpoints[1].plan.clusters[0].plan_id
        );
    }

    #[test]
    fn future_story_does_not_change_prior_checkpoint() {
        let mut request = ReplayNewsLensesRequest {
            schema_version: 1,
            items: vec![
                item("a", "e1", 0, 1.0, 0.0),
                item("b", "e2", 1, 1.0, 0.0),
                item("c", "e3", 2, 1.0, 0.0),
            ],
            checkpoints: vec![2],
            variants: vec![ReplayVariant {
                label: "ignored".to_owned(),
                algorithm: ReplayAlgorithm::RollingSphericalKmeans,
                lookback_days: 14,
                k: 10,
                warm_start: true,
                decay_half_life_days: None,
                seed: 0,
                max_iterations: 20,
                min_cluster_size: 3,
                match_threshold: 0.55,
                absorb_threshold: 0.45,
                identity_similarity: 0.7,
                confidence_threshold: None,
            }],
            max_items: 1_500,
        };
        let before = replay_production_news_lenses(&request).unwrap();
        request.items.push(item("future", "e4", 3, 0.0, 1.0));
        let after = replay_production_news_lenses(&request).unwrap();
        assert_eq!(
            serde_json::to_value(&before.checkpoints[0]).unwrap(),
            serde_json::to_value(&after.checkpoints[0]).unwrap()
        );
    }
}
