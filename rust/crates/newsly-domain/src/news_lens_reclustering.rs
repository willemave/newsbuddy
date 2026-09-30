use std::collections::{HashMap, HashSet};

use serde::{Deserialize, Serialize};
use thiserror::Error;

const DAY_SECONDS: i64 = 86_400;

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct NewsLensReclusterStory {
    pub story_id: String,
    pub event_id: String,
    pub available_at: i64,
    pub vector: Vec<f64>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct NewsLensReclusterExistingCluster {
    pub stable_id: String,
    pub center: Vec<f64>,
    #[serde(default)]
    pub member_story_ids: Vec<String>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct NewsLensReclusteringConfig {
    /// Optional vector width fence supplied by the embedding-model owner.
    pub expected_dimensions: Option<usize>,
    pub lookback_days: u32,
    pub max_items: usize,
    pub max_semantic_clusters: usize,
    pub max_iterations: usize,
    pub minimum_distinct_events: usize,
    pub weak_similarity: f64,
    pub identity_similarity: f64,
    pub representative_count: usize,
}

impl Default for NewsLensReclusteringConfig {
    fn default() -> Self {
        Self {
            expected_dimensions: None,
            lookback_days: 14,
            max_items: 1_500,
            max_semantic_clusters: 10,
            max_iterations: 20,
            minimum_distinct_events: 3,
            weak_similarity: 0.45,
            identity_similarity: 0.70,
            representative_count: 25,
        }
    }
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct NewsLensReclusteringInput {
    pub now: i64,
    pub stories: Vec<NewsLensReclusterStory>,
    #[serde(default)]
    pub existing_clusters: Vec<NewsLensReclusterExistingCluster>,
    #[serde(default)]
    pub config: NewsLensReclusteringConfig,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum NewsLensClusterIdentity {
    Existing { stable_id: String },
    New,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct NewsLensReclusterCluster {
    pub plan_id: String,
    pub identity: NewsLensClusterIdentity,
    pub center: Vec<f64>,
    pub story_ids: Vec<String>,
    pub representative_story_ids: Vec<String>,
    pub distinct_event_count: usize,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum NewsLensLineageKind {
    Continuation,
    SemanticContinuation,
    Split,
    Merge,
    SplitMerge,
    Retired,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct NewsLensLineage {
    pub old_stable_id: String,
    pub new_plan_id: Option<String>,
    pub overlap_count: usize,
    pub centroid_similarity: Option<f64>,
    pub inherited_identity: bool,
    pub kind: NewsLensLineageKind,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct NewsLensReclusterDiagnostics {
    pub input_story_count: usize,
    pub training_story_count: usize,
    pub clustered_story_count: usize,
    pub mixed_story_count: usize,
    pub cluster_count: usize,
    pub mean_cosine_to_center: f64,
    pub p10_cosine_to_center: f64,
    pub mean_top1_top2_margin: f64,
    pub weak_fraction: f64,
    pub mixed_fraction: f64,
    pub weak_story_count: usize,
    pub under_supported_story_count: usize,
    pub births: usize,
    pub deaths: usize,
    pub mean_identity_drift: Option<f64>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct NewsLensReclusteringPlan {
    pub config: NewsLensReclusteringConfig,
    pub no_op: bool,
    pub clusters: Vec<NewsLensReclusterCluster>,
    pub mixed_story_ids: Vec<String>,
    pub lineage: Vec<NewsLensLineage>,
    pub diagnostics: NewsLensReclusterDiagnostics,
}

#[derive(Debug, Error, PartialEq, Eq)]
pub enum NewsLensReclusteringError {
    #[error("invalid reclustering configuration: {0}")]
    InvalidConfig(&'static str),
    #[error("story id and event id must not be empty")]
    EmptyStoryIdentity,
    #[error("duplicate story id {0}")]
    DuplicateStory(String),
    #[error("duplicate existing cluster id {0}")]
    DuplicateCluster(String),
    #[error(
        "{kind} {id} vector must match the inferred/configured width and contain finite values with nonzero norm"
    )]
    InvalidVector { kind: &'static str, id: String },
}

#[derive(Clone)]
struct Story {
    id: String,
    event_id: String,
    vector: Vec<f64>,
}

struct Fit {
    centers: Vec<Vec<f64>>,
    labels: Vec<Option<usize>>,
    similarities: Vec<Option<f64>>,
    margins: Vec<f64>,
}

/// Builds a bounded, deterministic candidate routing partition without I/O.
///
/// Weak stories and clusters below distinct-event support remain reachable through
/// `mixed_story_ids`; they never contribute to fitted semantic centroids.
///
/// # Errors
/// Returns an error for invalid bounds, duplicate identities, or malformed vectors.
#[allow(clippy::too_many_lines)]
pub fn plan_news_lens_reclustering(
    input: &NewsLensReclusteringInput,
) -> Result<NewsLensReclusteringPlan, NewsLensReclusteringError> {
    validate_config(&input.config)?;
    if input.existing_clusters.len() > input.config.max_semantic_clusters {
        return Err(NewsLensReclusteringError::InvalidConfig(
            "existing semantic clusters exceed the configured cap",
        ));
    }
    let input_story_count = input
        .stories
        .iter()
        .filter(|story| story.available_at <= input.now)
        .count();
    let window_start = input
        .now
        .saturating_sub(i64::from(input.config.lookback_days) * DAY_SECONDS);
    let mut selected = input
        .stories
        .iter()
        .filter(|story| story.available_at <= input.now && story.available_at >= window_start)
        .collect::<Vec<_>>();
    selected.sort_by(|left, right| {
        left.available_at
            .cmp(&right.available_at)
            .then_with(|| left.story_id.cmp(&right.story_id))
    });
    if selected.len() > input.config.max_items {
        selected = selected.split_off(selected.len() - input.config.max_items);
    }
    if selected.is_empty() {
        return Ok(empty_plan(input, input_story_count));
    }
    let dimensions = input
        .config
        .expected_dimensions
        .or_else(|| selected.first().map(|story| story.vector.len()));
    let mut story_ids = HashSet::new();
    let stories = selected
        .into_iter()
        .map(|story| {
            if story.story_id.trim().is_empty() || story.event_id.trim().is_empty() {
                return Err(NewsLensReclusteringError::EmptyStoryIdentity);
            }
            if !story_ids.insert(story.story_id.clone()) {
                return Err(NewsLensReclusteringError::DuplicateStory(
                    story.story_id.clone(),
                ));
            }
            Ok(Story {
                id: story.story_id.clone(),
                event_id: story.event_id.clone(),
                vector: normalized_checked(&story.vector, dimensions, "story", &story.story_id)?,
            })
        })
        .collect::<Result<Vec<_>, _>>()?;
    let mut old_ids = HashSet::new();
    let existing = input
        .existing_clusters
        .iter()
        .map(|cluster| {
            if cluster.stable_id.trim().is_empty() || !old_ids.insert(cluster.stable_id.clone()) {
                return Err(NewsLensReclusteringError::DuplicateCluster(
                    cluster.stable_id.clone(),
                ));
            }
            Ok((
                cluster,
                normalized_checked(&cluster.center, dimensions, "cluster", &cluster.stable_id)?,
            ))
        })
        .collect::<Result<Vec<_>, _>>()?;
    let distinct_events = stories
        .iter()
        .map(|story| story.event_id.as_str())
        .collect::<HashSet<_>>()
        .len();
    let support_capacity = (distinct_events / input.config.minimum_distinct_events).max(1);
    let target = input.config.max_semantic_clusters.min(support_capacity);
    let warm = existing
        .iter()
        .take(target)
        .map(|(_, center)| center.clone())
        .collect::<Vec<_>>();
    let fit = fit_spherical(&stories, warm, target, &input.config);
    let mut grouped = vec![Vec::<usize>::new(); fit.centers.len()];
    let mut mixed = Vec::new();
    for (index, label) in fit.labels.iter().enumerate() {
        if let Some(label) = label {
            grouped[*label].push(index);
        } else {
            mixed.push(index);
        }
    }
    let weak_story_count = mixed.len();
    let mut supported = Vec::new();
    for (label, members) in grouped.into_iter().enumerate() {
        let event_count = members
            .iter()
            .map(|&index| stories[index].event_id.as_str())
            .collect::<HashSet<_>>()
            .len();
        if event_count >= input.config.minimum_distinct_events {
            supported.push((label, members, event_count));
        } else {
            mixed.extend(members);
        }
    }
    supported.sort_by(|left, right| stories[left.1[0]].id.cmp(&stories[right.1[0]].id));
    let identity = optimal_identity_matches(
        &stories,
        &supported,
        &fit.centers,
        &existing,
        input.config.identity_similarity,
    );
    let mut clusters = Vec::new();
    for (new_index, (raw_label, members, event_count)) in supported.iter().enumerate() {
        let identity_value =
            identity[new_index].map_or(NewsLensClusterIdentity::New, |old_index| {
                NewsLensClusterIdentity::Existing {
                    stable_id: existing[old_index].0.stable_id.clone(),
                }
            });
        let plan_id = match &identity_value {
            NewsLensClusterIdentity::Existing { stable_id } => stable_id.clone(),
            NewsLensClusterIdentity::New => format!("candidate-{}", new_index + 1),
        };
        clusters.push(NewsLensReclusterCluster {
            plan_id,
            identity: identity_value,
            center: fit.centers[*raw_label].clone(),
            story_ids: members
                .iter()
                .map(|&index| stories[index].id.clone())
                .collect(),
            representative_story_ids: diverse_representatives(
                &stories,
                members,
                &fit.centers[*raw_label],
                input.config.representative_count,
            ),
            distinct_event_count: *event_count,
        });
    }
    mixed.sort_unstable();
    mixed.dedup();
    let mixed_story_ids = mixed
        .iter()
        .map(|&index| stories[index].id.clone())
        .collect::<Vec<_>>();
    let lineage = build_lineage(&clusters, &existing, &identity);
    let similarities = fit
        .similarities
        .iter()
        .flatten()
        .copied()
        .collect::<Vec<_>>();
    let clustered_story_count = clusters
        .iter()
        .map(|cluster| cluster.story_ids.len())
        .sum::<usize>();
    let births = identity.iter().filter(|value| value.is_none()).count();
    let matched_old = identity.iter().flatten().copied().collect::<HashSet<_>>();
    let deaths = existing.len() - matched_old.len();
    let drifts = identity
        .iter()
        .enumerate()
        .filter_map(|(new_index, old)| {
            old.map(|old_index| 1.0 - dot(&clusters[new_index].center, &existing[old_index].1))
        })
        .collect::<Vec<_>>();
    Ok(NewsLensReclusteringPlan {
        config: input.config.clone(),
        no_op: false,
        clusters,
        mixed_story_ids,
        lineage,
        diagnostics: NewsLensReclusterDiagnostics {
            input_story_count,
            training_story_count: stories.len(),
            clustered_story_count,
            mixed_story_count: mixed.len(),
            cluster_count: supported.len(),
            mean_cosine_to_center: mean(&similarities),
            p10_cosine_to_center: p10(&similarities),
            mean_top1_top2_margin: mean(&fit.margins),
            weak_fraction: ratio(weak_story_count, stories.len()),
            mixed_fraction: ratio(mixed.len(), stories.len()),
            weak_story_count,
            under_supported_story_count: mixed.len().saturating_sub(weak_story_count),
            births,
            deaths,
            mean_identity_drift: (!drifts.is_empty()).then(|| mean(&drifts)),
        },
    })
}

fn validate_config(config: &NewsLensReclusteringConfig) -> Result<(), NewsLensReclusteringError> {
    if config.lookback_days == 0
        || config.lookback_days > 365
        || config.max_items == 0
        || config.max_items > 1_500
    {
        return Err(NewsLensReclusteringError::InvalidConfig(
            "lookback must be 1..=365 days and max_items 1..=1500",
        ));
    }
    if config
        .expected_dimensions
        .is_some_and(|dimensions| dimensions == 0 || dimensions > 16_384)
    {
        return Err(NewsLensReclusteringError::InvalidConfig(
            "expected vector dimensions must be in 1..=16384",
        ));
    }
    if config.max_semantic_clusters == 0
        || config.max_semantic_clusters > 10
        || config.max_iterations == 0
        || config.max_iterations > 20
    {
        return Err(NewsLensReclusteringError::InvalidConfig(
            "semantic clusters must be 1..=10 and iterations 1..=20",
        ));
    }
    if config.minimum_distinct_events == 0
        || config.representative_count == 0
        || config.representative_count > 25
    {
        return Err(NewsLensReclusteringError::InvalidConfig(
            "support and representative bounds are invalid",
        ));
    }
    if !config.weak_similarity.is_finite()
        || !config.identity_similarity.is_finite()
        || !(-1.0..=1.0).contains(&config.weak_similarity)
        || !(-1.0..=1.0).contains(&config.identity_similarity)
    {
        return Err(NewsLensReclusteringError::InvalidConfig(
            "similarity thresholds must be finite and within [-1,1]",
        ));
    }
    Ok(())
}

fn fit_spherical(
    stories: &[Story],
    mut centers: Vec<Vec<f64>>,
    target: usize,
    config: &NewsLensReclusteringConfig,
) -> Fit {
    if !centers.is_empty() {
        let warm_labels = score_labels(stories, &centers, config.weak_similarity).0;
        let mut warm_events = vec![HashSet::new(); centers.len()];
        for (story, label) in stories.iter().zip(warm_labels) {
            if let Some(label) = label {
                warm_events[label].insert(story.event_id.as_str());
            }
        }
        centers = centers
            .into_iter()
            .zip(warm_events)
            .filter_map(|(center, events)| {
                (events.len() >= config.minimum_distinct_events).then_some(center)
            })
            .collect();
    }
    fill_farthest(stories, &mut centers, target);
    let mut labels = vec![None; stories.len()];
    for _ in 0..config.max_iterations {
        let assigned = score_labels(stories, &centers, config.weak_similarity).0;
        let mut sums = vec![vec![0.0; stories[0].vector.len()]; centers.len()];
        let mut included_events = vec![HashSet::new(); centers.len()];
        for (story, label) in stories.iter().zip(&assigned) {
            if let Some(label) = label
                && included_events[*label].insert(story.event_id.as_str())
            {
                add(&mut sums[*label], &story.vector);
            }
        }
        for (center, sum) in centers.iter_mut().zip(sums) {
            if norm(&sum) > f64::EPSILON {
                *center = normalized(sum);
            }
        }
        if assigned == labels {
            break;
        }
        labels = assigned;
    }
    let (labels, similarities, margins) = score_labels(stories, &centers, config.weak_similarity);
    Fit {
        centers,
        labels,
        similarities,
        margins,
    }
}

fn fill_farthest(stories: &[Story], centers: &mut Vec<Vec<f64>>, target: usize) {
    if centers.is_empty() {
        centers.push(stories[0].vector.clone());
    }
    while centers.len() < target {
        let next = stories
            .iter()
            .map(|story| {
                let nearest = centers
                    .iter()
                    .map(|center| dot(&story.vector, center))
                    .fold(-1.0, f64::max);
                (story, nearest)
            })
            .min_by(|left, right| {
                left.1
                    .total_cmp(&right.1)
                    .then_with(|| right.0.id.cmp(&left.0.id))
            })
            .expect("nonempty story set")
            .0;
        if centers
            .iter()
            .any(|center| dot(center, &next.vector) > 0.999_999)
        {
            break;
        }
        centers.push(next.vector.clone());
    }
}

fn score_labels(
    stories: &[Story],
    centers: &[Vec<f64>],
    weak: f64,
) -> (Vec<Option<usize>>, Vec<Option<f64>>, Vec<f64>) {
    let mut labels = Vec::with_capacity(stories.len());
    let mut similarities = Vec::with_capacity(stories.len());
    let mut margins = Vec::with_capacity(stories.len());
    for story in stories {
        let mut scores = centers
            .iter()
            .enumerate()
            .map(|(index, center)| (index, dot(&story.vector, center)))
            .collect::<Vec<_>>();
        scores.sort_by(|left, right| {
            right
                .1
                .total_cmp(&left.1)
                .then_with(|| left.0.cmp(&right.0))
        });
        let best = scores[0];
        margins.push(best.1 - scores.get(1).map_or(0.0, |value| value.1));
        if best.1 >= weak {
            labels.push(Some(best.0));
            similarities.push(Some(best.1));
        } else {
            labels.push(None);
            similarities.push(None);
        }
    }
    (labels, similarities, margins)
}

fn optimal_identity_matches(
    stories: &[Story],
    supported: &[(usize, Vec<usize>, usize)],
    centers: &[Vec<f64>],
    existing: &[(&NewsLensReclusterExistingCluster, Vec<f64>)],
    guard: f64,
) -> Vec<Option<usize>> {
    let old_members = existing
        .iter()
        .map(|(cluster, _)| {
            cluster
                .member_story_ids
                .iter()
                .map(String::as_str)
                .collect::<HashSet<_>>()
        })
        .collect::<Vec<_>>();
    let weights = supported
        .iter()
        .map(|(label, members, _)| {
            existing
                .iter()
                .enumerate()
                .map(|(old_index, (_, old_center))| {
                    let similarity = dot(&centers[*label], old_center);
                    let overlap = members
                        .iter()
                        .filter(|&&index| {
                            old_members[old_index].contains(stories[index].id.as_str())
                        })
                        .count();
                    (similarity >= guard).then_some((overlap, similarity))
                })
                .collect::<Vec<_>>()
        })
        .collect::<Vec<_>>();
    let mut memo = HashMap::new();
    identity_dp(0, 0, &weights, &mut memo).1
}

type IdentityScore = (usize, f64);
type IdentityMemo = HashMap<(usize, u16), (IdentityScore, Vec<Option<usize>>)>;
fn identity_dp(
    index: usize,
    used: u16,
    weights: &[Vec<Option<(usize, f64)>>],
    memo: &mut IdentityMemo,
) -> (IdentityScore, Vec<Option<usize>>) {
    if index == weights.len() {
        return ((0, 0.0), Vec::new());
    }
    if let Some(result) = memo.get(&(index, used)) {
        return result.clone();
    }
    let (score, mut tail) = identity_dp(index + 1, used, weights, memo);
    tail.insert(0, None);
    let mut best = (score, tail);
    for old in 0..weights[index].len() {
        if used & (1 << old) != 0 {
            continue;
        }
        let Some((overlap, similarity)) = weights[index][old] else {
            continue;
        };
        let (tail_score, mut choices) = identity_dp(index + 1, used | (1 << old), weights, memo);
        let candidate_score = (tail_score.0 + overlap, tail_score.1 + similarity);
        choices.insert(0, Some(old));
        if candidate_score.0 > best.0.0
            || (candidate_score.0 == best.0.0 && candidate_score.1.total_cmp(&best.0.1).is_gt())
        {
            best = (candidate_score, choices);
        }
    }
    memo.insert((index, used), best.clone());
    best
}

fn build_lineage(
    clusters: &[NewsLensReclusterCluster],
    existing: &[(&NewsLensReclusterExistingCluster, Vec<f64>)],
    identities: &[Option<usize>],
) -> Vec<NewsLensLineage> {
    let old_members = existing
        .iter()
        .map(|(cluster, _)| {
            cluster
                .member_story_ids
                .iter()
                .map(String::as_str)
                .collect::<HashSet<_>>()
        })
        .collect::<Vec<_>>();
    let overlaps = clusters
        .iter()
        .map(|cluster| {
            existing
                .iter()
                .enumerate()
                .map(|(old, _)| {
                    cluster
                        .story_ids
                        .iter()
                        .filter(|id| old_members[old].contains(id.as_str()))
                        .count()
                })
                .collect::<Vec<_>>()
        })
        .collect::<Vec<_>>();
    let old_successors = (0..existing.len())
        .map(|old| overlaps.iter().filter(|row| row[old] > 0).count())
        .collect::<Vec<_>>();
    let new_parents = overlaps
        .iter()
        .map(|row| row.iter().filter(|&&count| count > 0).count())
        .collect::<Vec<_>>();
    let mut result = Vec::new();
    for (new, cluster) in clusters.iter().enumerate() {
        for (old, (_, old_center)) in existing.iter().enumerate() {
            let inherited = identities[new] == Some(old);
            if overlaps[new][old] == 0 && !inherited {
                continue;
            }
            let kind = if overlaps[new][old] == 0 {
                NewsLensLineageKind::SemanticContinuation
            } else if old_successors[old] > 1 && new_parents[new] > 1 {
                NewsLensLineageKind::SplitMerge
            } else if old_successors[old] > 1 {
                NewsLensLineageKind::Split
            } else if new_parents[new] > 1 {
                NewsLensLineageKind::Merge
            } else {
                NewsLensLineageKind::Continuation
            };
            result.push(NewsLensLineage {
                old_stable_id: existing[old].0.stable_id.clone(),
                new_plan_id: Some(cluster.plan_id.clone()),
                overlap_count: overlaps[new][old],
                centroid_similarity: Some(dot(&cluster.center, old_center)),
                inherited_identity: inherited,
                kind,
            });
        }
    }
    for (old, (cluster, _)) in existing.iter().enumerate() {
        if !identities.contains(&Some(old)) {
            result.push(NewsLensLineage {
                old_stable_id: cluster.stable_id.clone(),
                new_plan_id: None,
                overlap_count: 0,
                centroid_similarity: None,
                inherited_identity: false,
                kind: NewsLensLineageKind::Retired,
            });
        }
    }
    result
}

fn diverse_representatives(
    stories: &[Story],
    members: &[usize],
    center: &[f64],
    count: usize,
) -> Vec<String> {
    let mut remaining = members.to_vec();
    let mut selected = Vec::<usize>::new();
    let mut selected_events = HashSet::new();
    let mut scores = members
        .iter()
        .enumerate()
        .map(|(position, &index)| {
            let central = dot(&stories[index].vector, center);
            let recency = ratio(position, members.len()).min(1.0);
            (index, (central, recency, 1.0_f64))
        })
        .collect::<HashMap<_, _>>();
    while selected.len() < count.min(members.len()) {
        let has_new_event = remaining
            .iter()
            .any(|&index| !selected_events.contains(stories[index].event_id.as_str()));
        let next = remaining
            .iter()
            .copied()
            .filter(|&index| {
                !has_new_event || !selected_events.contains(stories[index].event_id.as_str())
            })
            .max_by(|left, right| {
                let score = |index: usize| {
                    let (central, recency, diversity) = scores[&index];
                    central * 0.55 + diversity * 0.30 + recency * 0.15
                };
                score(*left)
                    .total_cmp(&score(*right))
                    .then_with(|| stories[*right].id.cmp(&stories[*left].id))
            })
            .expect("remaining representative exists");
        selected.push(next);
        selected_events.insert(stories[next].event_id.as_str());
        remaining.retain(|&index| index != next);
        for &index in &remaining {
            let distance = 1.0 - dot(&stories[index].vector, &stories[next].vector);
            scores
                .entry(index)
                .and_modify(|(_, _, diversity)| *diversity = diversity.min(distance));
        }
    }
    selected
        .into_iter()
        .map(|index| stories[index].id.clone())
        .collect()
}

fn empty_plan(
    input: &NewsLensReclusteringInput,
    input_story_count: usize,
) -> NewsLensReclusteringPlan {
    NewsLensReclusteringPlan {
        config: input.config.clone(),
        no_op: true,
        clusters: Vec::new(),
        mixed_story_ids: Vec::new(),
        lineage: Vec::new(),
        diagnostics: NewsLensReclusterDiagnostics {
            input_story_count,
            training_story_count: 0,
            clustered_story_count: 0,
            mixed_story_count: 0,
            cluster_count: 0,
            mean_cosine_to_center: 0.0,
            p10_cosine_to_center: 0.0,
            mean_top1_top2_margin: 0.0,
            weak_fraction: 0.0,
            mixed_fraction: 0.0,
            weak_story_count: 0,
            under_supported_story_count: 0,
            births: 0,
            deaths: 0,
            mean_identity_drift: None,
        },
    }
}

fn normalized_checked(
    vector: &[f64],
    expected_dimensions: Option<usize>,
    kind: &'static str,
    id: &str,
) -> Result<Vec<f64>, NewsLensReclusteringError> {
    if vector.is_empty()
        || expected_dimensions.is_some_and(|expected| vector.len() != expected)
        || vector.iter().any(|value| !value.is_finite())
        || norm(vector) <= f64::EPSILON
    {
        return Err(NewsLensReclusteringError::InvalidVector {
            kind,
            id: id.to_owned(),
        });
    }
    Ok(normalized(vector.to_vec()))
}
fn normalized(mut vector: Vec<f64>) -> Vec<f64> {
    let length = norm(&vector);
    for value in &mut vector {
        *value /= length;
    }
    vector
}
fn norm(vector: &[f64]) -> f64 {
    vector.iter().map(|value| value * value).sum::<f64>().sqrt()
}
fn dot(left: &[f64], right: &[f64]) -> f64 {
    left.iter().zip(right).map(|(a, b)| a * b).sum()
}
fn add(target: &mut [f64], vector: &[f64]) {
    for (target, value) in target.iter_mut().zip(vector) {
        *target += value;
    }
}
fn mean(values: &[f64]) -> f64 {
    if values.is_empty() {
        0.0
    } else {
        values.iter().sum::<f64>() / count(values.len())
    }
}
fn p10(values: &[f64]) -> f64 {
    let mut values = values.to_vec();
    values.sort_by(f64::total_cmp);
    values
        .get(values.len().saturating_sub(1) / 10)
        .copied()
        .unwrap_or(0.0)
}
fn ratio(numerator: usize, denominator: usize) -> f64 {
    if denominator == 0 {
        0.0
    } else {
        count(numerator) / count(denominator)
    }
}
fn count(value: usize) -> f64 {
    f64::from(u32::try_from(value).expect("bounded clustering count fits u32"))
}

#[cfg(test)]
#[path = "news_lens_reclustering_tests.rs"]
mod tests;
