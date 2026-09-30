use std::collections::{HashMap, HashSet};
use std::time::Instant;

mod algorithms;
mod metrics;
mod types;

#[cfg(test)]
use algorithms::best_center;
use algorithms::{
    assign_to_centers, compact_result, incremental_surrogate, novelty_aware, spherical_kmeans,
};
#[cfg(test)]
use metrics::adjusted_rand;
use metrics::{
    build_cluster_states, match_identities, metrics, pre_recluster_fit, representatives,
};

#[cfg(test)]
mod tests;

pub use types::*;

const DAY_SECONDS: i64 = 86_400;

/// Replays deterministic offline clustering variants over time checkpoints.
///
/// # Errors
/// Returns [`ReplayError`] for malformed input or unsafe experiment bounds.
pub fn replay_news_lenses(
    request: &ReplayNewsLensesRequest,
) -> Result<ReplayNewsLensesResponse, ReplayError> {
    let items = validate_and_prepare(request)?;
    let variants = request
        .variants
        .iter()
        .map(|variant| replay_variant(&items, &request.checkpoints, variant, request.max_items))
        .collect();
    Ok(ReplayNewsLensesResponse {
        schema_version: 1,
        disclosures: vec![
            "This is an offline geometric simulation; embedding metrics do not establish human category quality.".into(),
            "incremental_surrogate is production-inspired and is not the exact production clustering path.".into(),
            "incremental_surrogate uses configured match/absorb thresholds, minimum group size, and cluster cap, plus centroid weight cap 32 and a weak forced fallback.".into(),
            "Cluster identities use deterministic maximum-overlap greedy matching, gated by centroid similarity; this is not Hungarian matching.".into(),
            "All replay decisions obey checkpoint availability and make no future items visible.".into(),
            "The replay is a retrospective corpus counterfactual when vectors were backfilled after event time.".into(),
            "confidence_threshold gates assigned cosine similarity; pre_recluster_match_coverage uses match_threshold.".into(),
        ],
        variants,
    })
}

pub(super) fn validate_and_prepare(
    request: &ReplayNewsLensesRequest,
) -> Result<Vec<WorkItem>, ReplayError> {
    if request.schema_version != 1 {
        return Err(ReplayError::UnsupportedVersion(request.schema_version));
    }
    if request.max_items == 0 || request.max_items > 10_000 {
        return Err(ReplayError::InvalidMaxItems);
    }
    if request.items.is_empty() || request.checkpoints.is_empty() || request.variants.is_empty() {
        return Err(ReplayError::EmptyInput);
    }
    if request
        .checkpoints
        .windows(2)
        .any(|pair| pair[0] >= pair[1])
    {
        return Err(ReplayError::CheckpointsNotIncreasing);
    }
    let dimension = request.items[0].vector.len();
    let mut seen = HashSet::new();
    let mut items = Vec::with_capacity(request.items.len());
    for item in &request.items {
        if item.id.trim().is_empty() {
            return Err(ReplayError::EmptyItemId);
        }
        if !seen.insert(item.id.clone()) {
            return Err(ReplayError::DuplicateItem(item.id.clone()));
        }
        if item.vector.len() != dimension {
            return Err(ReplayError::Dimension {
                id: item.id.clone(),
                actual: item.vector.len(),
                expected: dimension,
            });
        }
        if dimension == 0 || item.vector.iter().any(|value| !value.is_finite()) {
            return Err(ReplayError::InvalidVector {
                id: item.id.clone(),
                reason: "values must be finite and dimension nonzero",
            });
        }
        let norm = item
            .vector
            .iter()
            .map(|value| value * value)
            .sum::<f64>()
            .sqrt();
        if norm <= f64::EPSILON {
            return Err(ReplayError::InvalidVector {
                id: item.id.clone(),
                reason: "norm must be nonzero",
            });
        }
        items.push(WorkItem {
            id: item.id.clone(),
            event_id: if item.event_id.is_empty() {
                item.id.clone()
            } else {
                item.event_id.clone()
            },
            available_at: item.available_at,
            vector: item.vector.iter().map(|value| value / norm).collect(),
        });
    }
    items.sort_by(|left, right| {
        left.available_at
            .cmp(&right.available_at)
            .then_with(|| left.id.cmp(&right.id))
    });
    for variant in &request.variants {
        validate_variant(variant)?;
    }
    Ok(items)
}

fn validate_variant(variant: &ReplayVariant) -> Result<(), ReplayError> {
    let bad = |reason| ReplayError::InvalidVariant {
        label: variant.label.clone(),
        reason,
    };
    if variant.label.trim().is_empty() {
        return Err(bad("label must not be empty"));
    }
    if variant.lookback_days == 0
        || variant.lookback_days > 3_650
        || variant.k == 0
        || variant.k > 100
    {
        return Err(bad("lookback must be in 1..=3650 and k in 1..=100"));
    }
    if variant.max_iterations == 0 || variant.max_iterations > 20 {
        return Err(bad("max_iterations must be in 1..=20"));
    }
    if variant.min_cluster_size == 0 {
        return Err(bad("min_cluster_size must be positive"));
    }
    for value in [
        variant.match_threshold,
        variant.absorb_threshold,
        variant.identity_similarity,
    ] {
        if !value.is_finite() || !(-1.0..=1.0).contains(&value) {
            return Err(bad(
                "similarity thresholds must be finite and within [-1,1]",
            ));
        }
    }
    if variant
        .confidence_threshold
        .is_some_and(|value| !value.is_finite() || value < 0.0)
    {
        return Err(bad("confidence_threshold must be finite and nonnegative"));
    }
    if variant
        .decay_half_life_days
        .is_some_and(|value| !value.is_finite() || value <= 0.0)
    {
        return Err(bad("decay half-life must be finite and positive"));
    }
    Ok(())
}

#[allow(clippy::too_many_lines)]
fn replay_variant(
    items: &[WorkItem],
    checkpoints: &[i64],
    variant: &ReplayVariant,
    max_items: usize,
) -> ReplayVariantResult {
    let mut results = Vec::new();
    let mut previous_active = HashMap::<usize, Option<String>>::new();
    let mut retained = HashMap::<usize, Option<String>>::new();
    let mut previous_clusters = Vec::<ClusterState>::new();
    let mut previous_predictor_centers = Vec::<Vec<f64>>::new();
    let mut previous_window = HashSet::<usize>::new();
    let mut next_key = 1usize;
    let mut frozen_centers: Option<Vec<Vec<f64>>> = None;
    let mut incremental_centers = Vec::<(Vec<f64>, f64)>::new();
    let mut incremental_labels = HashMap::<usize, usize>::new();
    let mut processed = HashSet::<usize>::new();
    let mut previous_cutoff = None;

    for &cutoff in checkpoints {
        let started = Instant::now();
        let seen = items
            .iter()
            .enumerate()
            .filter(|(_, item)| item.available_at <= cutoff)
            .map(|(index, _)| index)
            .collect::<Vec<_>>();
        let window_start = cutoff - i64::from(variant.lookback_days) * DAY_SECONDS;
        let mut window = seen
            .iter()
            .copied()
            .filter(|&index| items[index].available_at >= window_start)
            .collect::<Vec<_>>();
        if window.len() > max_items {
            window = window.split_off(window.len() - max_items);
        }
        let window_set = window.iter().copied().collect::<HashSet<_>>();
        let training_change = TrainingChange {
            entered: window_set.difference(&previous_window).count(),
            exited: previous_window.difference(&window_set).count(),
        };

        let arrivals = seen
            .iter()
            .copied()
            .filter(|&index| previous_cutoff.is_none_or(|prior| items[index].available_at > prior))
            .collect::<Vec<_>>();
        let pre_recluster = pre_recluster_fit(
            items,
            &arrivals,
            &previous_predictor_centers,
            variant.match_threshold,
        );
        let raw = compact_result(match variant.algorithm {
            ReplayAlgorithm::FrozenInitial => {
                if frozen_centers.is_none() && !window.is_empty() {
                    let mut fit_variant = variant.clone();
                    fit_variant.confidence_threshold = None;
                    let fitted = spherical_kmeans(items, &window, cutoff, &fit_variant, None);
                    frozen_centers = Some(fitted.centers);
                }
                let centers = frozen_centers.as_deref().unwrap_or(&[]);
                assign_to_centers(
                    items,
                    &window,
                    centers.to_vec(),
                    variant.confidence_threshold,
                )
            }
            ReplayAlgorithm::IncrementalSurrogate => incremental_surrogate(
                items,
                &window,
                &window,
                variant,
                &mut incremental_centers,
                &mut incremental_labels,
                &mut processed,
            ),
            ReplayAlgorithm::RollingSphericalKmeans => {
                let warm = if variant.warm_start {
                    Some(
                        previous_clusters
                            .iter()
                            .map(|cluster| cluster.center.clone())
                            .collect(),
                    )
                } else {
                    None
                };
                spherical_kmeans(items, &window, cutoff, variant, warm)
            }
            ReplayAlgorithm::NoveltyAware => novelty_aware(items, &window, variant),
        });
        let identity = match_identities(
            &window,
            &raw,
            &previous_active,
            &previous_clusters,
            variant.identity_similarity,
            &mut next_key,
        );
        let mut current = HashMap::new();
        for (position, &item_index) in window.iter().enumerate() {
            current.insert(
                item_index,
                raw.labels[position].map(|label| identity.keys[label].clone()),
            );
        }
        for (&item_index, label) in &current {
            retained.insert(item_index, label.clone());
        }
        let clusters = build_cluster_states(&window, &raw, &identity.keys);
        previous_predictor_centers = match variant.algorithm {
            ReplayAlgorithm::FrozenInitial => frozen_centers.clone().unwrap_or_default(),
            ReplayAlgorithm::IncrementalSurrogate => incremental_centers
                .iter()
                .map(|(center, _)| center.clone())
                .collect(),
            ReplayAlgorithm::RollingSphericalKmeans | ReplayAlgorithm::NoveltyAware => {
                raw.centers.clone()
            }
        };
        let metrics = metrics(
            &window,
            &seen,
            &raw,
            &current,
            &previous_active,
            &clusters,
            &identity,
            &pre_recluster,
            started.elapsed().as_secs_f64() * 1000.0,
        );
        let cluster_output = clusters
            .iter()
            .map(|cluster| ReplayCluster {
                cluster_key: cluster.key.clone(),
                representative_ids: representatives(items, cluster, 5),
            })
            .collect();
        let mut assignments = seen
            .iter()
            .map(|&item_index| {
                let active_position = window.iter().position(|&index| index == item_index);
                ReplayAssignment {
                    item_id: items[item_index].id.clone(),
                    event_id: items[item_index].event_id.clone(),
                    cluster_key: retained.get(&item_index).cloned().flatten(),
                    state: if active_position.is_some() {
                        AssignmentState::Active
                    } else {
                        AssignmentState::Retained
                    },
                    cosine_to_center: active_position
                        .and_then(|position| raw.similarities[position]),
                    margin: active_position.and_then(|position| raw.margins[position]),
                }
            })
            .collect::<Vec<_>>();
        assignments.sort_by(|left, right| left.item_id.cmp(&right.item_id));
        results.push(ReplayCheckpoint {
            cutoff,
            training_change,
            metrics,
            clusters: cluster_output,
            assignments,
        });
        previous_active = current;
        previous_clusters = clusters;
        previous_window = window_set;
        previous_cutoff = Some(cutoff);
    }
    ReplayVariantResult {
        label: variant.label.clone(),
        algorithm: variant.algorithm,
        config: variant.clone(),
        checkpoints: results,
    }
}
