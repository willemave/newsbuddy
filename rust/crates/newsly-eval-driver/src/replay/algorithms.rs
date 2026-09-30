use std::collections::{HashMap, HashSet};

use super::{DAY_SECONDS, RawResult, ReplayVariant, WorkItem};

const SURROGATE_CENTROID_WEIGHT_CAP: f64 = 32.0;

fn initial_centers(items: &[WorkItem], window: &[usize], k: usize, seed: u64) -> Vec<Vec<f64>> {
    if window.is_empty() {
        return Vec::new();
    }
    let count = k.min(window.len());
    let window_len = u64::try_from(window.len()).expect("bounded replay window fits in u64");
    let first = usize::try_from(mix(seed) % window_len).expect("index fits in usize");
    let mut chosen_positions = vec![first];
    let mut nearest_similarity = window
        .iter()
        .map(|&index| dot(&items[index].vector, &items[window[first]].vector))
        .collect::<Vec<_>>();
    while chosen_positions.len() < count {
        let next_position = (0..window.len())
            .filter(|position| !chosen_positions.contains(position))
            .min_by(|left, right| {
                nearest_similarity[*left]
                    .total_cmp(&nearest_similarity[*right])
                    .then_with(|| items[window[*left]].id.cmp(&items[window[*right]].id))
            })
            .expect("unchosen item exists");
        chosen_positions.push(next_position);
        let next_vector = &items[window[next_position]].vector;
        for (position, &index) in window.iter().enumerate() {
            nearest_similarity[position] =
                nearest_similarity[position].max(dot(&items[index].vector, next_vector));
        }
    }
    chosen_positions
        .into_iter()
        .map(|position| items[window[position]].vector.clone())
        .collect()
}

pub(super) fn spherical_kmeans(
    items: &[WorkItem],
    window: &[usize],
    cutoff: i64,
    variant: &ReplayVariant,
    warm: Option<Vec<Vec<f64>>>,
) -> RawResult {
    if window.is_empty() {
        return empty_raw();
    }
    let target = variant.k.min(window.len());
    let mut centers = warm
        .filter(|centers| !centers.is_empty())
        .unwrap_or_else(|| initial_centers(items, window, target, variant.seed));
    centers.truncate(target);
    if centers.len() < target {
        for center in initial_centers(items, window, target, variant.seed) {
            if centers.len() == target {
                break;
            }
            if !centers
                .iter()
                .any(|existing| dot(existing, &center) > 0.999_999)
            {
                centers.push(center);
            }
        }
    }
    let mut labels = vec![usize::MAX; window.len()];
    for _ in 0..variant.max_iterations {
        let next_labels = window
            .iter()
            .map(|&index| best_center(&items[index].vector, &centers).0)
            .collect::<Vec<_>>();
        let stable = next_labels == labels;
        labels = next_labels;
        let mut sums = vec![vec![0.0; items[window[0]].vector.len()]; centers.len()];
        for (position, &index) in window.iter().enumerate() {
            let weight = decay_weight(
                items[index].available_at,
                cutoff,
                variant.decay_half_life_days,
            );
            add_scaled(&mut sums[labels[position]], &items[index].vector, weight);
        }
        for (center, sum) in centers.iter_mut().zip(sums) {
            if normalize(&sum).is_some() {
                *center = normalized(sum);
            }
        }
        if stable {
            break;
        }
    }
    let optional_labels = window
        .iter()
        .map(|&index| Some(best_center(&items[index].vector, &centers).0))
        .collect();
    scored_result(
        items,
        window,
        centers,
        optional_labels,
        variant.confidence_threshold,
    )
}

pub(super) fn novelty_aware(
    items: &[WorkItem],
    window: &[usize],
    variant: &ReplayVariant,
) -> RawResult {
    let mut centers: Vec<Vec<f64>> = Vec::new();
    let mut groups: Vec<Vec<usize>> = Vec::new();
    for &index in window {
        let choice = centers
            .iter()
            .enumerate()
            .map(|(i, center)| (i, dot(&items[index].vector, center)))
            .max_by(|left, right| left.1.total_cmp(&right.1));
        if let Some((label, _)) =
            choice.filter(|(_, similarity)| *similarity >= variant.match_threshold)
        {
            groups[label].push(index);
            centers[label] = mean_center(items, &groups[label]);
        } else if centers.len() < variant.k {
            centers.push(items[index].vector.clone());
            groups.push(vec![index]);
        }
    }
    let mut mapping = vec![None; centers.len()];
    let mut kept = Vec::new();
    for (old, group) in groups.iter().enumerate() {
        if group.len() >= variant.min_cluster_size {
            mapping[old] = Some(kept.len());
            kept.push(centers[old].clone());
        }
    }
    let labels = window
        .iter()
        .map(|index| {
            groups
                .iter()
                .position(|group| group.contains(index))
                .and_then(|old| mapping[old])
        })
        .collect();
    scored_result(items, window, kept, labels, variant.confidence_threshold)
}

pub(super) fn incremental_surrogate(
    items: &[WorkItem],
    window: &[usize],
    seen: &[usize],
    variant: &ReplayVariant,
    centers: &mut Vec<(Vec<f64>, f64)>,
    assignments: &mut HashMap<usize, usize>,
    processed: &mut HashSet<usize>,
) -> RawResult {
    let newcomers = seen
        .iter()
        .copied()
        .filter(|index| processed.insert(*index))
        .collect::<Vec<_>>();
    let mut unmatched = Vec::new();
    for index in newcomers {
        let best = centers
            .iter()
            .enumerate()
            .map(|(label, (center, _))| (label, dot(&items[index].vector, center)))
            .max_by(|left, right| left.1.total_cmp(&right.1));
        if let Some((label, _)) =
            best.filter(|(_, similarity)| *similarity >= variant.match_threshold)
        {
            update_bounded_center(&mut centers[label], &items[index].vector);
            assignments.insert(index, label);
        } else {
            unmatched.push(index);
        }
    }
    let mut groups: Vec<Vec<usize>> = Vec::new();
    for index in unmatched {
        let group = groups.iter().position(|group| {
            dot(&items[index].vector, &mean_center(items, group)) >= variant.match_threshold
        });
        if let Some(group) = group {
            groups[group].push(index);
        } else {
            groups.push(vec![index]);
        }
    }
    for group in groups {
        if group.len() >= variant.min_cluster_size && centers.len() < variant.k {
            let label = centers.len();
            centers.push((mean_center(items, &group), count_as_f64(group.len())));
            for index in group {
                assignments.insert(index, label);
            }
        } else {
            for index in group {
                let best = centers
                    .iter()
                    .enumerate()
                    .map(|(label, (center, _))| (label, dot(&items[index].vector, center)))
                    .max_by(|left, right| left.1.total_cmp(&right.1));
                if let Some((label, _)) = best
                    .filter(|(_, similarity)| *similarity >= variant.absorb_threshold)
                    .or(best)
                {
                    update_bounded_center(&mut centers[label], &items[index].vector);
                    assignments.insert(index, label);
                } else if centers.len() < variant.k {
                    centers.push((items[index].vector.clone(), 1.0));
                    assignments.insert(index, centers.len() - 1);
                }
            }
        }
    }
    let raw_centers = centers
        .iter()
        .map(|(center, _)| center.clone())
        .collect::<Vec<_>>();
    let labels = window
        .iter()
        .map(|index| assignments.get(index).copied())
        .collect();
    compact_result(scored_result(
        items,
        window,
        raw_centers,
        labels,
        variant.confidence_threshold,
    ))
}

fn update_bounded_center(state: &mut (Vec<f64>, f64), vector: &[f64]) {
    let old_weight = state.1.min(SURROGATE_CENTROID_WEIGHT_CAP);
    for (value, incoming) in state.0.iter_mut().zip(vector) {
        *value = (*value * old_weight + incoming) / (old_weight + 1.0);
    }
    state.0 = normalized(std::mem::take(&mut state.0));
    state.1 = (old_weight + 1.0).min(SURROGATE_CENTROID_WEIGHT_CAP);
}

pub(super) fn assign_to_centers(
    items: &[WorkItem],
    window: &[usize],
    centers: Vec<Vec<f64>>,
    threshold: Option<f64>,
) -> RawResult {
    let labels = window
        .iter()
        .map(|&index| {
            if centers.is_empty() {
                None
            } else {
                Some(best_center(&items[index].vector, &centers).0)
            }
        })
        .collect();
    scored_result(items, window, centers, labels, threshold)
}

fn scored_result(
    items: &[WorkItem],
    window: &[usize],
    centers: Vec<Vec<f64>>,
    mut labels: Vec<Option<usize>>,
    threshold: Option<f64>,
) -> RawResult {
    let mut similarities = Vec::with_capacity(window.len());
    let mut margins = Vec::with_capacity(window.len());
    for (position, &index) in window.iter().enumerate() {
        if let Some(label) = labels[position] {
            let mut scores = centers
                .iter()
                .map(|center| dot(&items[index].vector, center))
                .collect::<Vec<_>>();
            scores.sort_by(|left, right| right.total_cmp(left));
            let similarity = dot(&items[index].vector, &centers[label]);
            let margin =
                scores.first().copied().unwrap_or(0.0) - scores.get(1).copied().unwrap_or(0.0);
            if threshold.is_some_and(|minimum| similarity < minimum) {
                labels[position] = None;
                similarities.push(None);
                margins.push(Some(margin));
            } else {
                similarities.push(Some(similarity));
                margins.push(Some(margin));
            }
        } else {
            similarities.push(None);
            margins.push(None);
        }
    }
    RawResult {
        centers,
        labels,
        similarities,
        margins,
    }
}

pub(super) fn compact_result(raw: RawResult) -> RawResult {
    let used = raw.labels.iter().flatten().copied().collect::<HashSet<_>>();
    let mut mapping = HashMap::new();
    let mut centers = Vec::new();
    for (old, center) in raw.centers.into_iter().enumerate() {
        if used.contains(&old) {
            mapping.insert(old, centers.len());
            centers.push(center);
        }
    }
    RawResult {
        centers,
        labels: raw
            .labels
            .into_iter()
            .map(|label| label.map(|old| mapping[&old]))
            .collect(),
        similarities: raw.similarities,
        margins: raw.margins,
    }
}

pub(super) fn best_center(vector: &[f64], centers: &[Vec<f64>]) -> (usize, f64) {
    centers
        .iter()
        .enumerate()
        .map(|(i, center)| (i, dot(vector, center)))
        .max_by(|left, right| {
            left.1
                .total_cmp(&right.1)
                .then_with(|| right.0.cmp(&left.0))
        })
        .expect("centers nonempty")
}

fn mean_center(items: &[WorkItem], members: &[usize]) -> Vec<f64> {
    let mut sum = vec![0.0; items[members[0]].vector.len()];
    for &index in members {
        add_scaled(&mut sum, &items[index].vector, 1.0);
    }
    normalized(sum)
}

fn add_scaled(target: &mut [f64], vector: &[f64], weight: f64) {
    for (target, value) in target.iter_mut().zip(vector) {
        *target += value * weight;
    }
}

fn normalized(mut vector: Vec<f64>) -> Vec<f64> {
    let _ = normalize(&vector).map(|norm| {
        for value in &mut vector {
            *value /= norm;
        }
    });
    vector
}

fn normalize(vector: &[f64]) -> Option<f64> {
    let norm = vector.iter().map(|value| value * value).sum::<f64>().sqrt();
    (norm > f64::EPSILON).then_some(norm)
}

pub(super) fn dot(left: &[f64], right: &[f64]) -> f64 {
    left.iter().zip(right).map(|(a, b)| a * b).sum()
}

fn decay_weight(available_at: i64, cutoff: i64, half_life: Option<f64>) -> f64 {
    half_life.map_or(1.0, |days| {
        let age_seconds = u32::try_from((cutoff - available_at).max(0))
            .expect("validated replay lookback fits in u32 seconds");
        let seconds_per_day = f64::from(u32::try_from(DAY_SECONDS).expect("day fits in u32"));
        2.0_f64.powf(-(f64::from(age_seconds) / seconds_per_day) / days)
    })
}

fn mix(mut value: u64) -> u64 {
    value = value.wrapping_add(0x9e37_79b9_7f4a_7c15);
    value = (value ^ (value >> 30)).wrapping_mul(0xbf58_476d_1ce4_e5b9);
    value = (value ^ (value >> 27)).wrapping_mul(0x94d0_49bb_1331_11eb);
    value ^ (value >> 31)
}

pub(super) fn count_as_f64(value: usize) -> f64 {
    f64::from(u32::try_from(value).expect("bounded replay collection fits in u32"))
}

fn empty_raw() -> RawResult {
    RawResult {
        centers: Vec::new(),
        labels: Vec::new(),
        similarities: Vec::new(),
        margins: Vec::new(),
    }
}
