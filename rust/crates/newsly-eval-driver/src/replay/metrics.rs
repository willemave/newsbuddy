use std::collections::{HashMap, HashSet};

use super::algorithms::{count_as_f64, dot};
use super::{ClusterState, IdentityResult, PreReclusterFit, RawResult, ReplayMetrics, WorkItem};

pub(super) fn pre_recluster_fit(
    items: &[WorkItem],
    arrivals: &[usize],
    previous_centers: &[Vec<f64>],
    match_threshold: f64,
) -> PreReclusterFit {
    if previous_centers.is_empty() {
        return PreReclusterFit {
            count: arrivals.len(),
            ..PreReclusterFit::default()
        };
    }
    let mut similarities = Vec::with_capacity(arrivals.len());
    let mut margins = Vec::with_capacity(arrivals.len());
    for &index in arrivals {
        let mut scores = previous_centers
            .iter()
            .map(|center| dot(&items[index].vector, center))
            .collect::<Vec<_>>();
        scores.sort_by(|left, right| right.total_cmp(left));
        similarities.push(scores[0]);
        margins.push(scores[0] - scores.get(1).copied().unwrap_or(0.0));
    }
    let match_count = similarities
        .iter()
        .filter(|&&value| value >= match_threshold)
        .count();
    let weak_count = similarities.iter().filter(|&&value| value < 0.45).count();
    PreReclusterFit {
        count: arrivals.len(),
        similarities,
        margins,
        weak_count,
        match_count,
    }
}

pub(super) fn match_identities(
    window: &[usize],
    raw: &RawResult,
    previous: &HashMap<usize, Option<String>>,
    old_clusters: &[ClusterState],
    minimum_similarity: f64,
    next_key: &mut usize,
) -> IdentityResult {
    let mut candidates = Vec::new();
    for raw_label in 0..raw.centers.len() {
        let mut overlap = HashMap::<String, usize>::new();
        for (position, &index) in window.iter().enumerate() {
            if raw.labels[position] == Some(raw_label)
                && let Some(Some(key)) = previous.get(&index)
            {
                *overlap.entry(key.clone()).or_default() += 1;
            }
        }
        for old in old_clusters {
            let similarity = dot(&raw.centers[raw_label], &old.center);
            if similarity >= minimum_similarity {
                candidates.push((
                    overlap.get(&old.key).copied().unwrap_or(0),
                    similarity,
                    raw_label,
                    old.key.clone(),
                ));
            }
        }
    }
    candidates.sort_by(|left, right| {
        right
            .0
            .cmp(&left.0)
            .then_with(|| right.1.total_cmp(&left.1))
            .then_with(|| left.2.cmp(&right.2))
            .then_with(|| left.3.cmp(&right.3))
    });
    let mut keys = vec![String::new(); raw.centers.len()];
    let mut used_old = HashSet::new();
    let mut drifts = Vec::new();
    for (_, similarity, raw_label, key) in candidates {
        if keys[raw_label].is_empty() && used_old.insert(key.clone()) {
            keys[raw_label] = key;
            drifts.push(1.0 - similarity);
        }
    }
    let mut births = 0;
    for key in &mut keys {
        if key.is_empty() {
            *key = format!("cluster-{next_key}");
            *next_key += 1;
            births += 1;
        }
    }
    let deaths = old_clusters
        .iter()
        .filter(|cluster| !used_old.contains(&cluster.key))
        .count();
    let mean_drift =
        (!drifts.is_empty()).then(|| drifts.iter().sum::<f64>() / count_as_f64(drifts.len()));
    IdentityResult {
        keys,
        births,
        deaths,
        mean_drift,
    }
}

pub(super) fn build_cluster_states(
    window: &[usize],
    raw: &RawResult,
    keys: &[String],
) -> Vec<ClusterState> {
    (0..raw.centers.len())
        .filter_map(|label| {
            let members = window
                .iter()
                .enumerate()
                .filter(|(position, _)| raw.labels[*position] == Some(label))
                .map(|(_, index)| *index)
                .collect::<Vec<_>>();
            (!members.is_empty()).then(|| ClusterState {
                key: keys[label].clone(),
                center: raw.centers[label].clone(),
                members,
            })
        })
        .collect()
}

#[allow(clippy::too_many_arguments)]
pub(super) fn metrics(
    window: &[usize],
    seen: &[usize],
    raw: &RawResult,
    current: &HashMap<usize, Option<String>>,
    previous: &HashMap<usize, Option<String>>,
    clusters: &[ClusterState],
    identity: &IdentityResult,
    pre_recluster: &PreReclusterFit,
    elapsed_ms: f64,
) -> ReplayMetrics {
    let clustered = raw.labels.iter().filter(|label| label.is_some()).count();
    let mut similarities = raw
        .similarities
        .iter()
        .flatten()
        .copied()
        .collect::<Vec<_>>();
    similarities.sort_by(f64::total_cmp);
    let margins = raw.margins.iter().flatten().copied().collect::<Vec<_>>();
    let shared = current
        .keys()
        .filter(|index| previous.contains_key(index))
        .copied()
        .collect::<Vec<_>>();
    let ari = (!shared.is_empty()).then(|| adjusted_rand(&shared, previous, current));
    let movement = (!shared.is_empty()).then(|| {
        ratio(
            shared
                .iter()
                .filter(|index| previous.get(index) != current.get(index))
                .count(),
            shared.len(),
        )
    });
    let largest = clusters
        .iter()
        .map(|cluster| cluster.members.len())
        .max()
        .unwrap_or(0);
    let mut pre_similarities = pre_recluster.similarities.clone();
    pre_similarities.sort_by(f64::total_cmp);
    ReplayMetrics {
        n_seen: seen.len(),
        n_window: window.len(),
        n_clustered: clustered,
        noise_fraction: ratio(window.len() - clustered, window.len()),
        cluster_count: clusters.len(),
        mean_cosine_to_center: mean(&similarities),
        p10_cosine: p10(&similarities),
        mean_top1_top2_margin: mean(&margins),
        largest_cluster_fraction: ratio(largest, clustered),
        new_arrival_count: pre_recluster.count,
        pre_recluster_mean_cosine: (!pre_similarities.is_empty()).then(|| mean(&pre_similarities)),
        pre_recluster_p10_cosine: (!pre_similarities.is_empty()).then(|| p10(&pre_similarities)),
        pre_recluster_mean_margin: (!pre_recluster.margins.is_empty())
            .then(|| mean(&pre_recluster.margins)),
        pre_recluster_weak_fraction: (!pre_similarities.is_empty())
            .then(|| ratio(pre_recluster.weak_count, pre_similarities.len())),
        pre_recluster_match_coverage: (!pre_similarities.is_empty())
            .then(|| ratio(pre_recluster.match_count, pre_similarities.len())),
        adjusted_rand_shared_active: ari,
        matched_movement_shared_active: movement,
        centroid_drift: identity.mean_drift,
        births: identity.births,
        deaths: identity.deaths,
        elapsed_ms,
    }
}

pub(super) fn adjusted_rand(
    indices: &[usize],
    left: &HashMap<usize, Option<String>>,
    right: &HashMap<usize, Option<String>>,
) -> f64 {
    let mut table = HashMap::<(Option<String>, Option<String>), usize>::new();
    let mut rows = HashMap::<Option<String>, usize>::new();
    let mut cols = HashMap::<Option<String>, usize>::new();
    for index in indices {
        let a = left[index].clone();
        let b = right[index].clone();
        *table.entry((a.clone(), b.clone())).or_default() += 1;
        *rows.entry(a).or_default() += 1;
        *cols.entry(b).or_default() += 1;
    }
    let cells = table.values().map(|&n| choose2(n)).sum::<f64>();
    let row_sum = rows.values().map(|&n| choose2(n)).sum::<f64>();
    let col_sum = cols.values().map(|&n| choose2(n)).sum::<f64>();
    let total = choose2(indices.len());
    if total == 0.0 {
        return 1.0;
    }
    let expected = row_sum * col_sum / total;
    let maximum = 0.5 * (row_sum + col_sum);
    if (maximum - expected).abs() <= f64::EPSILON {
        1.0
    } else {
        (cells - expected) / (maximum - expected)
    }
}

pub(super) fn representatives(
    items: &[WorkItem],
    cluster: &ClusterState,
    count: usize,
) -> Vec<String> {
    let mut members = cluster.members.clone();
    members.sort_by(|left, right| {
        dot(&items[*right].vector, &cluster.center)
            .total_cmp(&dot(&items[*left].vector, &cluster.center))
            .then_with(|| items[*left].id.cmp(&items[*right].id))
    });
    members
        .into_iter()
        .take(count)
        .map(|index| items[index].id.clone())
        .collect()
}

fn choose2(value: usize) -> f64 {
    count_as_f64(value.saturating_mul(value.saturating_sub(1)) / 2)
}

fn ratio(numerator: usize, denominator: usize) -> f64 {
    if denominator == 0 {
        0.0
    } else {
        count_as_f64(numerator) / count_as_f64(denominator)
    }
}

fn mean(values: &[f64]) -> f64 {
    if values.is_empty() {
        0.0
    } else {
        values.iter().sum::<f64>() / count_as_f64(values.len())
    }
}

fn p10(values: &[f64]) -> f64 {
    if values.is_empty() {
        0.0
    } else {
        values[(values.len() - 1) / 10]
    }
}
