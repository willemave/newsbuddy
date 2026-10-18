use std::collections::HashSet;

use chrono::Utc;
use newsly_db::{
    BriefingLensAssignmentPlan, BriefingLensAssignmentUsage, BriefingLensCentroidMutation,
    BriefingPendingLensAssignment, BriefingPlannedLens, BriefingRefreshConfig,
    BriefingSemanticLens, BriefingUnassignedSource, PreparedBriefingRefreshSeed,
};
use newsly_providers::{
    BriefingCompositionGateway, BriefingCompositionGatewayError, BriefingCompositionSource,
};
use thiserror::Error;

mod naming;
mod vectors;

use naming::{name_cluster, name_clusters, naming_usage};

use vectors::{cosine, mean_vector};

use super::category_routing::CategoryRoute;
use super::{centroid_model, nonempty, split_model_spec};

const LENS_NAMING_ATTEMPTS: usize = 2;
pub(super) const LENS_NAMING_PARALLELISM: usize = 4;

pub(super) async fn plan_semantic_lenses(
    gateway: &BriefingCompositionGateway,
    seed: &PreparedBriefingRefreshSeed,
    config: &BriefingRefreshConfig,
    routes: &[CategoryRoute],
    source_vectors: Vec<Vec<f64>>,
    new_category_budget: usize,
    lease: &crate::LeaseHealth,
) -> Result<BriefingLensAssignmentPlan, SemanticLensPlanningError> {
    plan_semantic_lenses_with_namer(
        gateway,
        seed,
        config,
        routes,
        source_vectors,
        new_category_budget,
        |candidates| async move {
            name_cluster(gateway, &candidates, config.new_lens_min_items, lease).await
        },
    )
    .await
}

async fn plan_semantic_lenses_with_namer<F, Fut>(
    gateway: &BriefingCompositionGateway,
    seed: &PreparedBriefingRefreshSeed,
    config: &BriefingRefreshConfig,
    routes: &[CategoryRoute],
    source_vectors: Vec<Vec<f64>>,
    new_category_budget: usize,
    namer: F,
) -> Result<BriefingLensAssignmentPlan, SemanticLensPlanningError>
where
    F: Fn(Vec<Candidate>) -> Fut,
    Fut: std::future::Future<
            Output = Result<newsly_providers::GeneratedBriefingLensName, SemanticLensPlanningError>,
        >,
{
    let mut usage = Vec::new();
    let result = async {
        let snapshot = &seed.lens_assignment;
        if snapshot.pending_sources.is_empty() {
            return Ok(empty_plan(seed));
        }
        let vector_size = source_vectors.first().map_or(0, Vec::len);
        let centroid_model = centroid_model(gateway.embedding_model());
        if source_vectors.len() != snapshot.pending_sources.len() {
            return Err(SemanticLensPlanningError::EmbeddingShape {
                expected: snapshot.pending_sources.len(),
                actual: source_vectors.len(),
            });
        }
        if vector_size == 0
            || source_vectors
                .iter()
                .any(|vector| vector.len() != vector_size)
        {
            return Err(SemanticLensPlanningError::InconsistentEmbeddingWidth);
        }
        let mut lenses = snapshot
            .active_lenses
            .iter()
            .map(|lens| WorkingLens::existing(lens, &centroid_model, vector_size))
            .collect::<Vec<_>>();
        let mut assignments = Vec::new();
        let mut remaining = Vec::new();
        if routes.len() != snapshot.pending_sources.len() {
            return Err(SemanticLensPlanningError::InvalidRoutes);
        }
        for ((pending, vector), route) in snapshot
            .pending_sources
            .iter()
            .cloned()
            .zip(source_vectors)
            .zip(routes)
        {
            match route {
                CategoryRoute::Existing(key) => {
                    let Some(lens) = lenses.iter_mut().find(|lens| &lens.key == key) else {
                        return Err(SemanticLensPlanningError::InvalidRoutes);
                    };
                    lens.update_centroid(&vector, config.centroid_max_weight, &centroid_model);
                    assignments.push(assignment(&pending, key.clone()));
                }
                CategoryRoute::Discover => remaining.push(Candidate { pending, vector }),
                CategoryRoute::General => assignments.push(assignment(&pending, "misc".to_owned())),
            }
        }
        let mut deferred_ids = HashSet::new();

        let mut used_keys = snapshot
            .all_lens_keys
            .iter()
            .cloned()
            .collect::<HashSet<_>>();
        let mut active_count = snapshot
            .active_news_lens_keys
            .iter()
            .filter(|key| *key != "misc")
            .count();
        let mut next_position = snapshot.next_news_position;
        if active_count >= config.max_news_lenses {
            assignments.extend(
                remaining
                    .iter()
                    .map(|candidate| assignment(&candidate.pending, "misc".to_owned())),
            );
            remaining.clear();
        } else {
            let clusters = cluster_candidates(remaining, config.category_cluster_similarity);
            let (mut ready, small) = split_ready_clusters(clusters, config.new_lens_min_items);
            rank_clusters(&mut ready);
            let mut packed =
                pack_small_clusters(small, config.new_lens_min_items, config.news_window_max);
            rank_clusters(&mut packed);
            ready.extend(packed);
            // Only the central bounded sample is shown to the naming/membership model.
            for cluster in &mut ready {
                cluster.candidates.sort_by(|left, right| {
                    cosine(&right.vector, &cluster.centroid)
                        .total_cmp(&cosine(&left.vector, &cluster.centroid))
                });
            }
            // First editions review one parallel batch before publishing one window;
            // other proposals remain in the durable frozen cohort.
            ready.truncate(new_category_budget);
            let names = name_clusters(
                &ready,
                config.max_news_lenses.saturating_sub(active_count),
                config.news_window_max,
                |candidates| namer(candidates.to_vec()),
            )
            .await?;
            usage.extend(
                names
                    .names
                    .iter()
                    .flatten()
                    .map(|name| naming_usage(name, gateway.naming_model_spec())),
            );
            usage.extend(names.failed_usage);
            let mut names = names.names.into_iter();
            for cluster in ready {
                let Some(generated) = names.next() else {
                    // No further naming call is allowed this sweep. At the category
                    // cap, retain coverage without inventing an unrelated match.
                    if active_count >= config.max_news_lenses {
                        assignments.extend(
                            cluster
                                .candidates
                                .iter()
                                .map(|candidate| assignment(&candidate.pending, "misc".to_owned())),
                        );
                    }
                    continue;
                };
                let Some(generated) = generated else {
                    // An exhausted naming call cannot justify a category or a
                    // general assignment. Retry its sources in a later task.
                    continue;
                };
                let shown =
                    &cluster.candidates[..cluster.candidates.len().min(config.news_window_max)];
                generated
                    .name
                    .validate_membership(
                        &shown
                            .iter()
                            .map(|candidate| composition_source(&candidate.pending))
                            .collect::<Vec<_>>(),
                    )
                    .map_err(SemanticLensPlanningError::InvalidMembership)?;
                let members = shown
                    .iter()
                    .filter(|candidate| {
                        generated
                            .name
                            .member_source_keys
                            .contains(&candidate.pending.source.source_key)
                    })
                    .collect::<Vec<_>>();
                if members.len() < config.new_lens_min_items {
                    assignments.extend(
                        shown
                            .iter()
                            .map(|candidate| assignment(&candidate.pending, "misc".to_owned())),
                    );
                    continue;
                }
                let accepted_centroid = mean_vector(
                    &members
                        .iter()
                        .map(|candidate| candidate.vector.clone())
                        .collect::<Vec<_>>(),
                );
                let key = unique_lens_key(&generated.name.key, &mut used_keys);
                let lens = WorkingLens::new(
                    key.clone(),
                    generated.name.title,
                    generated.name.deck,
                    next_position,
                    accepted_centroid,
                    members.len(),
                    &centroid_model,
                );
                next_position = next_position.saturating_add(1);
                active_count += 1;
                for candidate in shown {
                    let target = if generated
                        .name
                        .member_source_keys
                        .contains(&candidate.pending.source.source_key)
                    {
                        key.clone()
                    } else {
                        "misc".to_owned()
                    };
                    assignments.push(assignment(&candidate.pending, target));
                }
                // A new category is not part of this task's frozen Jev choices. Its
                // unshown sources wait for the next task, including during onboarding.
                for candidate in &cluster.candidates[shown.len()..] {
                    if seed.mode == newsly_db::BriefingRefreshMode::Full {
                        // A full rebuild replaces the old edition atomically. Preserve
                        // unreviewed coverage in general news instead of hiding it.
                        assignments.push(assignment(&candidate.pending, "misc".to_owned()));
                    } else {
                        deferred_ids.insert(candidate.pending.pending_id);
                    }
                }
                lenses.push(lens);
            }
        }

        let assigned_ids = assignments
            .iter()
            .map(|assignment| assignment.pending_id)
            .collect::<HashSet<_>>();
        let stale = snapshot
            .pending_sources
            .iter()
            .filter(|pending| {
                !assigned_ids.contains(&pending.pending_id)
                    && !deferred_ids.contains(&pending.pending_id)
            })
            .collect::<Vec<_>>();
        if !stale.is_empty()
            && stale.len() < config.new_lens_min_items
            && stale
                .iter()
                .map(|pending| pending.enqueued_at)
                .min()
                .is_some_and(|oldest| {
                    (Utc::now() - oldest).num_seconds() >= config.pending_max_age_seconds
                })
        {
            assignments.extend(
                stale
                    .into_iter()
                    .map(|pending| assignment(pending, "misc".to_owned())),
            );
        }

        let mut new_lenses = lenses
            .iter()
            .filter(|lens| lens.id.is_none())
            .map(WorkingLens::planned_lens)
            .collect::<Vec<_>>();
        if assignments
            .iter()
            .any(|assignment| assignment.lens_key == "misc")
            && !snapshot
                .active_news_lens_keys
                .iter()
                .any(|key| key == "misc")
        {
            new_lenses.push(misc_lens(next_position));
        }

        let centroid_mutations = lenses
            .iter()
            .filter_map(WorkingLens::centroid_mutation)
            .collect();
        Ok(BriefingLensAssignmentPlan {
            task_id: seed.task_id,
            user_id: seed.user_id,
            starting_version: seed.starting_version,
            assignments,
            centroid_mutations,
            new_lenses,
            usage: Vec::new(),
        })
    }
    .await;
    match result {
        Ok(mut plan) => {
            plan.usage = usage;
            Ok(plan)
        }
        Err(error) => Err(error.with_usage(usage)),
    }
}

fn composition_source(pending: &BriefingUnassignedSource) -> BriefingCompositionSource {
    let source = &pending.source;
    BriefingCompositionSource {
        source_key: source.source_key.clone(),
        kind: source.kind.clone(),
        id: source.id,
        title: source.title.clone(),
        source_name: source.source_name.clone(),
        summary: source.summary.clone(),
        key_points: source.key_points.clone(),
        url: source.url.clone(),
        image_url: source.image_url.clone(),
        thumbnail_url: source.thumbnail_url.clone(),
        published_at: source.published_at.map(|value| value.to_rfc3339()),
        briefing_context: source.briefing_context.clone(),
    }
}

/// Isolate failed stories without changing semantic assignments for healthy siblings.
pub(super) fn assign_failed_sources(
    plan: &mut BriefingLensAssignmentPlan,
    seed: &PreparedBriefingRefreshSeed,
    failed: &[BriefingUnassignedSource],
) {
    if failed.is_empty() {
        return;
    }
    let active = &seed.lens_assignment.active_news_lens_keys;
    if !active.iter().any(|key| key == "misc")
        && !plan.new_lenses.iter().any(|lens| lens.key == "misc")
    {
        plan.new_lenses.push(misc_lens(
            seed.lens_assignment.next_news_position
                + i32::try_from(plan.new_lenses.len()).unwrap_or(0),
        ));
    }
    plan.assignments.extend(
        failed
            .iter()
            .map(|source| assignment(source, "misc".to_owned())),
    );
}

/// Full replaces the entire edition, so every source needs an assignment even
/// when bounded routing, vector readiness, or naming left it unreviewed.
pub(super) fn complete_full_assignments(
    plan: &mut BriefingLensAssignmentPlan,
    seed: &PreparedBriefingRefreshSeed,
) {
    if seed.mode != newsly_db::BriefingRefreshMode::Full {
        return;
    }
    let assigned = plan
        .assignments
        .iter()
        .map(|row| row.pending_id)
        .collect::<HashSet<_>>();
    let remaining = seed
        .lens_assignment
        .pending_sources
        .iter()
        .filter(|source| !assigned.contains(&source.pending_id))
        .cloned()
        .collect::<Vec<_>>();
    assign_failed_sources(plan, seed, &remaining);
}

fn empty_plan(seed: &PreparedBriefingRefreshSeed) -> BriefingLensAssignmentPlan {
    BriefingLensAssignmentPlan {
        task_id: seed.task_id,
        user_id: seed.user_id,
        starting_version: seed.starting_version,
        assignments: Vec::new(),
        centroid_mutations: Vec::new(),
        new_lenses: Vec::new(),
        usage: Vec::new(),
    }
}

fn assignment(
    pending: &BriefingUnassignedSource,
    lens_key: String,
) -> BriefingPendingLensAssignment {
    BriefingPendingLensAssignment {
        pending_id: pending.pending_id,
        source_kind: pending.source_kind.clone(),
        source_id: pending.source_id,
        lens_key,
    }
}

fn misc_lens(position: i32) -> BriefingPlannedLens {
    BriefingPlannedLens {
        key: "misc".to_owned(),
        title: "Briefs".to_owned(),
        deck: "A mixed desk of fast reads that did not form a larger category yet.".to_owned(),
        position,
        centroid: None,
        centroid_weight: 0,
        centroid_model: None,
    }
}

#[derive(Debug, Clone)]
struct Candidate {
    pending: BriefingUnassignedSource,
    vector: Vec<f64>,
}

#[derive(Debug, Clone)]
struct Cluster {
    candidates: Vec<Candidate>,
    centroid: Vec<f64>,
}

impl Cluster {
    fn new(candidate: Candidate) -> Self {
        let centroid = candidate.vector.clone();
        Self {
            candidates: vec![candidate],
            centroid,
        }
    }

    fn add(&mut self, candidate: Candidate) {
        self.candidates.push(candidate);
        self.centroid = mean_vector(
            &self
                .candidates
                .iter()
                .map(|candidate| candidate.vector.clone())
                .collect::<Vec<_>>(),
        );
    }

    fn merge(mut self, other: Self) -> Self {
        for candidate in other.candidates {
            self.add(candidate);
        }
        self
    }
}

fn cluster_candidates(candidates: Vec<Candidate>, threshold: f64) -> Vec<Cluster> {
    let mut clusters: Vec<Cluster> = Vec::new();
    for candidate in candidates {
        let best = best_similarity_index(&candidate.vector, &clusters, |cluster| {
            cluster.centroid.as_slice()
        });
        if let Some((index, _score)) = best.filter(|(_, score)| *score >= threshold) {
            clusters[index].add(candidate);
        } else {
            clusters.push(Cluster::new(candidate));
        }
    }
    clusters
}

fn split_ready_clusters(clusters: Vec<Cluster>, minimum: usize) -> (Vec<Cluster>, Vec<Cluster>) {
    clusters
        .into_iter()
        .partition(|cluster| cluster.candidates.len() >= minimum)
}

fn pack_small_clusters(
    mut remaining: Vec<Cluster>,
    minimum: usize,
    maximum: usize,
) -> Vec<Cluster> {
    let mut packed: Vec<Cluster> = Vec::new();
    while !remaining.is_empty() {
        let seed_index = most_connected_cluster_index(&remaining);
        let mut group = remaining.remove(seed_index);
        while !remaining.is_empty() && group.candidates.len() < maximum {
            let Some(index) = nearest_cluster_index(&group, &remaining, Some(maximum)) else {
                break;
            };
            group = group.merge(remaining.remove(index));
        }
        packed.push(group);
    }
    packed
        .into_iter()
        .filter(|cluster| cluster.candidates.len() >= minimum)
        .collect()
}

fn rank_clusters(clusters: &mut [Cluster]) {
    clusters.sort_by(|left, right| {
        right
            .candidates
            .len()
            .cmp(&left.candidates.len())
            .then_with(|| oldest(left).cmp(&oldest(right)))
    });
}

fn most_connected_cluster_index(clusters: &[Cluster]) -> usize {
    if clusters.len() == 1 {
        return 0;
    }
    let mut best_index = 0;
    let mut best_score = -1.0;
    for (index, cluster) in clusters.iter().enumerate() {
        let scores = clusters
            .iter()
            .enumerate()
            .filter(|(other_index, _)| *other_index != index)
            .map(|(_, other)| cosine(&cluster.centroid, &other.centroid))
            .collect::<Vec<_>>();
        let score = scores.iter().sum::<f64>() / scores.len() as f64;
        if score > best_score {
            best_index = index;
            best_score = score;
        }
    }
    best_index
}

fn nearest_cluster_index(
    base: &Cluster,
    candidates: &[Cluster],
    maximum: Option<usize>,
) -> Option<usize> {
    let mut best = None;
    let mut best_score = -1.0;
    for (index, candidate) in candidates.iter().enumerate() {
        if maximum
            .is_some_and(|maximum| base.candidates.len() + candidate.candidates.len() > maximum)
        {
            continue;
        }
        let score = cosine(&base.centroid, &candidate.centroid);
        if score > best_score {
            best = Some(index);
            best_score = score;
        }
    }
    best
}

fn oldest(cluster: &Cluster) -> chrono::DateTime<Utc> {
    cluster
        .candidates
        .iter()
        .map(|candidate| candidate.pending.enqueued_at)
        .min()
        .unwrap_or_else(Utc::now)
}

fn compatible_centroid<'a>(
    lens: &'a BriefingSemanticLens,
    model: &str,
    size: usize,
) -> Option<&'a Vec<f64>> {
    lens.centroid.as_ref().filter(|vector| {
        !vector.is_empty()
            && vector.len() == size
            && lens.centroid_model.as_deref() == Some(model)
            && vector.iter().all(|v| v.is_finite())
            && vector.iter().any(|v| *v != 0.0)
    })
}

#[derive(Debug, Clone)]
struct WorkingLens {
    id: Option<i64>,
    key: String,
    title: String,
    deck: String,
    position: i32,
    centroid: Option<Vec<f64>>,
    centroid_weight: i32,
    centroid_model: String,
    changed: bool,
}

impl WorkingLens {
    fn existing(lens: &BriefingSemanticLens, model: &str, vector_size: usize) -> Self {
        let valid_centroid = compatible_centroid(lens, model, vector_size);
        Self {
            id: Some(lens.id),
            key: lens.key.clone(),
            title: lens.title.clone(),
            deck: lens.deck.clone(),
            position: lens.position,
            centroid: valid_centroid.cloned(),
            centroid_weight: valid_centroid.map_or(0, |_| lens.centroid_weight.max(1)),
            centroid_model: model.to_owned(),
            changed: false,
        }
    }

    #[allow(clippy::too_many_arguments)]
    fn new(
        key: String,
        title: String,
        deck: String,
        position: i32,
        centroid: Vec<f64>,
        weight: usize,
        model: &str,
    ) -> Self {
        Self {
            id: None,
            key,
            title,
            deck,
            position,
            centroid: Some(centroid),
            centroid_weight: i32::try_from(weight).unwrap_or(i32::MAX),
            centroid_model: model.to_owned(),
            changed: false,
        }
    }

    fn update_centroid(&mut self, vector: &[f64], max_weight: i32, model: &str) {
        let Some(current) = self
            .centroid
            .as_ref()
            .filter(|current| current.len() == vector.len() && self.centroid_model == model)
        else {
            self.centroid = Some(vector.to_vec());
            self.centroid_weight = 1;
            model.clone_into(&mut self.centroid_model);
            self.changed = true;
            return;
        };
        let weight = self.centroid_weight.max(1);
        let capped = weight.min(max_weight);
        let denominator = f64::from(capped + 1);
        let updated = current
            .iter()
            .zip(vector)
            .map(|(current, next)| ((*current * f64::from(capped)) + next) / denominator)
            .collect::<Vec<_>>();
        self.centroid = Some(updated);
        self.centroid_weight = (weight + 1).min(max_weight);
        self.changed = true;
    }

    fn centroid_mutation(&self) -> Option<BriefingLensCentroidMutation> {
        (self.id.is_some() && self.changed).then(|| BriefingLensCentroidMutation {
            lens_id: self.id.expect("existing changed lens has an ID"),
            lens_key: self.key.clone(),
            centroid: self.centroid.clone().unwrap_or_default(),
            centroid_weight: self.centroid_weight,
            centroid_model: self.centroid_model.clone(),
        })
    }

    fn planned_lens(&self) -> BriefingPlannedLens {
        BriefingPlannedLens {
            key: self.key.clone(),
            title: self.title.clone(),
            deck: self.deck.clone(),
            position: self.position,
            centroid: self.centroid.clone(),
            centroid_weight: self.centroid_weight,
            centroid_model: Some(self.centroid_model.clone()),
        }
    }
}

fn best_similarity_index<T>(
    vector: &[f64],
    candidates: &[T],
    candidate_vector: impl Fn(&T) -> &[f64],
) -> Option<(usize, f64)> {
    let mut best = None;
    let mut best_score = -1.0;
    for (index, candidate) in candidates.iter().enumerate() {
        let score = cosine(vector, candidate_vector(candidate));
        if score > best_score {
            best = Some((index, score));
            best_score = score;
        }
    }
    best
}

fn unique_lens_key(raw: &str, used: &mut HashSet<String>) -> String {
    let normalized = raw
        .trim()
        .to_ascii_lowercase()
        .strip_prefix("news-")
        .unwrap_or(raw.trim())
        .chars()
        .fold((String::new(), false), |(mut key, dash), character| {
            if character.is_ascii_alphanumeric() {
                key.push(character);
                (key, false)
            } else if !dash && !key.is_empty() {
                key.push('-');
                (key, true)
            } else {
                (key, dash)
            }
        })
        .0
        .trim_matches('-')
        .to_owned();
    let base = format!(
        "news-{}",
        if normalized.is_empty() {
            "updates"
        } else {
            &normalized
        }
    );
    let base: String = base
        .chars()
        .take(64)
        .collect::<String>()
        .trim_end_matches('-')
        .to_owned();
    let mut key = base.clone();
    let mut suffix = 2;
    while used.contains(&key) {
        let suffix_text = format!("-{suffix}");
        let prefix: String = base
            .chars()
            .take(64_usize.saturating_sub(suffix_text.len()))
            .collect::<String>()
            .trim_end_matches('-')
            .to_owned();
        key = format!("{prefix}{suffix_text}");
        suffix += 1;
    }
    used.insert(key.clone());
    key
}

#[derive(Debug, Error)]
pub(super) enum SemanticLensPlanningError {
    #[error("queue ownership was lost during category naming")]
    OwnershipLost,
    #[error("category decisions do not match the prepared sources and categories")]
    InvalidRoutes,
    #[error("Briefing semantic embedding shape mismatch: expected {expected}, got {actual}")]
    EmbeddingShape { expected: usize, actual: usize },
    #[error("Briefing semantic embeddings have inconsistent dimensions")]
    InconsistentEmbeddingWidth,
    #[error("invalid Briefing category membership: {0}")]
    InvalidMembership(String),
    #[error("{cause}")]
    Observed {
        #[source]
        cause: Box<Self>,
        usage: Vec<BriefingLensAssignmentUsage>,
    },
    #[error(transparent)]
    Provider(#[from] BriefingCompositionGatewayError),
}

impl SemanticLensPlanningError {
    pub(super) fn observed_usage(&self) -> &[BriefingLensAssignmentUsage] {
        match self {
            Self::Observed { usage, .. } => usage,
            _ => &[],
        }
    }

    #[cfg(test)]
    fn cause(&self) -> &Self {
        match self {
            Self::Observed { cause, .. } => cause.cause(),
            other => other,
        }
    }

    fn into_observed(self) -> (Self, Vec<BriefingLensAssignmentUsage>) {
        match self {
            Self::Observed { cause, usage } => (*cause, usage),
            other => (other, Vec::new()),
        }
    }

    fn with_usage(self, mut extra: Vec<BriefingLensAssignmentUsage>) -> Self {
        let (cause, mut observed) = self.into_observed();
        extra.append(&mut observed);
        if extra.is_empty() {
            cause
        } else {
            Self::Observed {
                cause: Box::new(cause),
                usage: extra,
            }
        }
    }
}

#[cfg(test)]
mod tests;
