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

const LENS_NAMING_ATTEMPTS: usize = 2;

pub(super) async fn plan_semantic_lenses(
    gateway: &BriefingCompositionGateway,
    seed: &PreparedBriefingRefreshSeed,
    config: &BriefingRefreshConfig,
    embedding_batch_size: usize,
) -> Result<BriefingLensAssignmentPlan, SemanticLensPlanningError> {
    let snapshot = &seed.lens_assignment;
    if snapshot.pending_sources.is_empty() {
        return Ok(empty_plan(seed));
    }
    let texts = snapshot
        .pending_sources
        .iter()
        .map(|pending| pending.source.embedding_text())
        .chain(
            snapshot
                .active_lenses
                .iter()
                .map(BriefingSemanticLens::profile_text),
        )
        .collect::<Vec<_>>();
    let mut vectors = Vec::with_capacity(texts.len());
    let mut usage = Vec::new();
    for chunk in texts.chunks(embedding_batch_size.clamp(1, 128)) {
        match gateway.embed(chunk).await {
            Ok(batch) => {
                usage.push(BriefingLensAssignmentUsage {
                    provider: "openrouter".to_owned(),
                    model: batch.model,
                    provider_response_id: batch.provider_response_id,
                    usage: batch.usage,
                    feature: "briefing_category_assignment".to_owned(),
                    operation: "briefing.embed_categories".to_owned(),
                });
                vectors.extend(batch.vectors);
            }
            Err(error) => {
                tracing::warn!(
                    task_id = seed.task_id,
                    user_id = seed.user_id,
                    source_count = snapshot.pending_sources.len(),
                    lens_count = snapshot.active_lenses.len(),
                    error = %error,
                    "Briefing semantic category embedding failed; using bounded non-semantic assignment"
                );
                return plan_nonsemantic_lenses(gateway, seed, config, usage).await;
            }
        }
    }
    if vectors.len() != texts.len() || vectors.is_empty() {
        return Err(SemanticLensPlanningError::EmbeddingShape {
            expected: texts.len(),
            actual: vectors.len(),
        });
    }
    let source_count = snapshot.pending_sources.len();
    let source_vectors = vectors[..source_count].to_vec();
    let profile_vectors = &vectors[source_count..];
    let vector_size = source_vectors.first().map_or(0, Vec::len);
    if vector_size == 0
        || source_vectors
            .iter()
            .any(|vector| vector.len() != vector_size)
        || profile_vectors
            .iter()
            .any(|vector| vector.len() != vector_size)
    {
        return Err(SemanticLensPlanningError::InconsistentEmbeddingWidth);
    }
    let centroid_model = format!("openrouter:{}", gateway.embedding_model());
    let mut lenses = snapshot
        .active_lenses
        .iter()
        .zip(profile_vectors)
        .map(|(lens, profile)| WorkingLens::existing(lens, profile, &centroid_model, vector_size))
        .collect::<Vec<_>>();
    let mut assignments = Vec::new();
    let mut remaining = Vec::new();
    for (pending, vector) in snapshot.pending_sources.iter().cloned().zip(source_vectors) {
        let Some((lens_index, score)) = best_lens(&vector, &lenses) else {
            remaining.push(Candidate { pending, vector });
            continue;
        };
        if score < config.category_similarity {
            remaining.push(Candidate { pending, vector });
            continue;
        }
        let lens_key = lenses[lens_index].key.clone();
        lenses[lens_index].update_centroid(&vector, config.centroid_max_weight, &centroid_model);
        assignments.push(assignment(&pending, lens_key));
    }

    let mut used_keys = snapshot
        .all_lens_keys
        .iter()
        .cloned()
        .collect::<HashSet<_>>();
    let mut active_count = snapshot.active_news_lens_keys.len();
    let mut next_position = snapshot.next_news_position;
    let mut misc_plan = None;
    if active_count >= config.max_news_lenses {
        assign_capped_remaining(
            &mut assignments,
            &remaining,
            &mut lenses,
            snapshot
                .active_news_lens_keys
                .iter()
                .any(|key| key == "misc"),
            config,
            &centroid_model,
        );
        remaining.clear();
    } else {
        let clusters = cluster_candidates(remaining, config.category_cluster_similarity);
        let (mut ready, small) = split_ready_clusters(clusters, config.new_lens_min_items);
        if small
            .iter()
            .map(|cluster| cluster.candidates.len())
            .sum::<usize>()
            >= config.new_lens_min_items
        {
            ready.extend(pack_small_clusters(
                small,
                config.new_lens_min_items,
                config.news_window_max,
            ));
        }
        ready.sort_by(|left, right| {
            right
                .candidates
                .len()
                .cmp(&left.candidates.len())
                .then_with(|| oldest(left).cmp(&oldest(right)))
        });
        for cluster in ready {
            if active_count < config.max_news_lenses {
                let generated = name_cluster(
                    gateway,
                    &cluster.candidates[..cluster.candidates.len().min(config.news_window_max)],
                )
                .await?;
                let (provider, fallback_model) = split_model_spec(gateway.model_spec());
                usage.push(BriefingLensAssignmentUsage {
                    provider: provider.to_owned(),
                    model: nonempty(&generated.model)
                        .unwrap_or(fallback_model)
                        .to_owned(),
                    provider_response_id: generated.provider_response_id,
                    usage: generated.usage,
                    feature: "briefing_lens_naming".to_owned(),
                    operation: "briefing.name_lens".to_owned(),
                });
                let key = unique_lens_key(&generated.name.key, &mut used_keys);
                let mut lens = WorkingLens::new(
                    key.clone(),
                    generated.name.title,
                    generated.name.deck,
                    next_position,
                    cluster.centroid.clone(),
                    cluster.candidates.len(),
                    &centroid_model,
                );
                next_position = next_position.saturating_add(1);
                active_count += 1;
                for candidate in &cluster.candidates {
                    assignments.push(assignment(&candidate.pending, key.clone()));
                }
                lens.changed = false;
                lenses.push(lens);
                continue;
            }
            let Some((lens_index, score)) = best_lens(&cluster.centroid, &lenses) else {
                continue;
            };
            if score >= config.category_absorb_similarity {
                let key = lenses[lens_index].key.clone();
                for candidate in &cluster.candidates {
                    lenses[lens_index].update_centroid(
                        &candidate.vector,
                        config.centroid_max_weight,
                        &centroid_model,
                    );
                    assignments.push(assignment(&candidate.pending, key.clone()));
                }
                continue;
            }
            let fallback_key = if snapshot
                .active_news_lens_keys
                .iter()
                .any(|key| key == "misc")
            {
                Some("misc".to_owned())
            } else if active_count < config.max_news_lenses {
                misc_plan = Some(misc_lens(next_position));
                active_count += 1;
                Some("misc".to_owned())
            } else {
                Some(lenses[lens_index].key.clone())
            };
            if let Some(key) = fallback_key {
                for candidate in &cluster.candidates {
                    if key == lenses[lens_index].key {
                        lenses[lens_index].update_centroid(
                            &candidate.vector,
                            config.centroid_max_weight,
                            &centroid_model,
                        );
                    }
                    assignments.push(assignment(&candidate.pending, key.clone()));
                }
            }
        }
    }

    let assigned_ids = assignments
        .iter()
        .map(|assignment| assignment.pending_id)
        .collect::<HashSet<_>>();
    let stale = snapshot
        .pending_sources
        .iter()
        .filter(|pending| !assigned_ids.contains(&pending.pending_id))
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
        let misc_active = snapshot
            .active_news_lens_keys
            .iter()
            .any(|key| key == "misc")
            || misc_plan.is_some();
        if misc_active || active_count < config.max_news_lenses {
            if !misc_active {
                misc_plan = Some(misc_lens(next_position));
            }
            assignments.extend(
                stale
                    .into_iter()
                    .map(|pending| assignment(pending, "misc".to_owned())),
            );
        }
    }

    let mut new_lenses = lenses
        .iter()
        .filter(|lens| lens.id.is_none())
        .map(WorkingLens::planned_lens)
        .collect::<Vec<_>>();
    if let Some(misc) = misc_plan {
        new_lenses.push(misc);
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
        usage,
    })
}

async fn plan_nonsemantic_lenses(
    gateway: &BriefingCompositionGateway,
    seed: &PreparedBriefingRefreshSeed,
    config: &BriefingRefreshConfig,
    mut usage: Vec<BriefingLensAssignmentUsage>,
) -> Result<BriefingLensAssignmentPlan, SemanticLensPlanningError> {
    let snapshot = &seed.lens_assignment;
    let pending = &snapshot.pending_sources;
    let oldest_is_stale = pending
        .iter()
        .map(|source| source.enqueued_at)
        .min()
        .is_some_and(|oldest| {
            (Utc::now() - oldest).num_seconds() >= config.pending_max_age_seconds
        });
    let should_assign = pending.len() >= config.new_lens_min_items || oldest_is_stale;
    if !should_assign {
        return Ok(BriefingLensAssignmentPlan {
            usage,
            ..empty_plan(seed)
        });
    }
    let mut new_lenses = Vec::new();
    let target = if pending.len() >= config.new_lens_min_items
        && snapshot.active_news_lens_keys.len() < config.max_news_lenses
    {
        let generated = name_cluster(
            gateway,
            &pending
                .iter()
                .take(config.news_window_max)
                .cloned()
                .map(|pending| Candidate {
                    pending,
                    vector: Vec::new(),
                })
                .collect::<Vec<_>>(),
        )
        .await?;
        let (provider, fallback_model) = split_model_spec(gateway.model_spec());
        usage.push(BriefingLensAssignmentUsage {
            provider: provider.to_owned(),
            model: nonempty(&generated.model)
                .unwrap_or(fallback_model)
                .to_owned(),
            provider_response_id: generated.provider_response_id,
            usage: generated.usage,
            feature: "briefing_lens_naming".to_owned(),
            operation: "briefing.name_lens".to_owned(),
        });
        let mut used = snapshot
            .all_lens_keys
            .iter()
            .cloned()
            .collect::<HashSet<_>>();
        let key = unique_lens_key(&generated.name.key, &mut used);
        new_lenses.push(BriefingPlannedLens {
            key: key.clone(),
            title: generated.name.title,
            deck: generated.name.deck,
            position: snapshot.next_news_position,
            centroid: None,
            centroid_weight: 0,
            centroid_model: None,
        });
        Some(key)
    } else if snapshot
        .active_news_lens_keys
        .iter()
        .any(|key| key == "misc")
    {
        Some("misc".to_owned())
    } else if snapshot.active_news_lens_keys.len() < config.max_news_lenses {
        new_lenses.push(misc_lens(snapshot.next_news_position));
        Some("misc".to_owned())
    } else {
        None
    };
    let assignments = if let Some(target) = target {
        pending
            .iter()
            .map(|pending| assignment(pending, target.clone()))
            .collect()
    } else {
        pending
            .iter()
            .zip(snapshot.active_news_lens_keys.iter().cycle())
            .map(|(pending, key)| assignment(pending, key.clone()))
            .collect()
    };
    Ok(BriefingLensAssignmentPlan {
        task_id: seed.task_id,
        user_id: seed.user_id,
        starting_version: seed.starting_version,
        assignments,
        centroid_mutations: Vec::new(),
        new_lenses,
        usage,
    })
}

async fn name_cluster(
    gateway: &BriefingCompositionGateway,
    candidates: &[Candidate],
) -> Result<newsly_providers::GeneratedBriefingLensName, SemanticLensPlanningError> {
    let sources = candidates
        .iter()
        .map(|candidate| composition_source(&candidate.pending))
        .collect::<Vec<_>>();
    let mut last_error = None;
    for attempt in 1..=LENS_NAMING_ATTEMPTS {
        match gateway.name_lens(&sources).await {
            Ok(name) => return Ok(name),
            Err(error) => {
                tracing::warn!(
                    source_count = sources.len(),
                    attempt,
                    max_attempts = LENS_NAMING_ATTEMPTS,
                    error = %error,
                    "Briefing semantic lens naming failed"
                );
                last_error = Some(error);
            }
        }
    }
    Err(SemanticLensPlanningError::Provider(
        last_error.expect("at least one lens naming attempt must run"),
    ))
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
        if group.candidates.len() < minimum && !packed.is_empty() {
            let target = nearest_cluster_index(&group, &packed, Some(maximum))
                .or_else(|| nearest_cluster_index(&group, &packed, None));
            if let Some(index) = target {
                packed[index] = packed.remove(index).merge(group);
                continue;
            }
        }
        packed.push(group);
    }
    packed
        .into_iter()
        .filter(|cluster| cluster.candidates.len() >= minimum)
        .collect()
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

#[derive(Debug, Clone)]
struct WorkingLens {
    id: Option<i64>,
    key: String,
    title: String,
    deck: String,
    position: i32,
    similarity_vector: Vec<f64>,
    centroid: Option<Vec<f64>>,
    centroid_weight: i32,
    centroid_model: String,
    changed: bool,
}

impl WorkingLens {
    fn existing(
        lens: &BriefingSemanticLens,
        profile: &[f64],
        model: &str,
        vector_size: usize,
    ) -> Self {
        let valid_centroid = lens.centroid.as_ref().filter(|centroid| {
            centroid.len() == vector_size && lens.centroid_model.as_deref() == Some(model)
        });
        Self {
            id: Some(lens.id),
            key: lens.key.clone(),
            title: lens.title.clone(),
            deck: lens.deck.clone(),
            position: lens.position,
            similarity_vector: valid_centroid.cloned().unwrap_or_else(|| profile.to_vec()),
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
            similarity_vector: centroid.clone(),
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
        self.centroid = Some(updated.clone());
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

fn best_lens(vector: &[f64], lenses: &[WorkingLens]) -> Option<(usize, f64)> {
    best_similarity_index(vector, lenses, |lens| lens.similarity_vector.as_slice())
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

fn assign_capped_remaining(
    assignments: &mut Vec<BriefingPendingLensAssignment>,
    remaining: &[Candidate],
    lenses: &mut [WorkingLens],
    misc_active: bool,
    config: &BriefingRefreshConfig,
    centroid_model: &str,
) {
    for candidate in remaining {
        let Some((index, score)) = best_lens(&candidate.vector, lenses) else {
            if misc_active {
                assignments.push(assignment(&candidate.pending, "misc".to_owned()));
            }
            continue;
        };
        if score >= config.category_absorb_similarity {
            let key = lenses[index].key.clone();
            lenses[index].update_centroid(
                &candidate.vector,
                config.centroid_max_weight,
                centroid_model,
            );
            assignments.push(assignment(&candidate.pending, key));
        } else if misc_active {
            assignments.push(assignment(&candidate.pending, "misc".to_owned()));
        } else {
            let key = lenses[index].key.clone();
            lenses[index].update_centroid(
                &candidate.vector,
                config.centroid_max_weight,
                centroid_model,
            );
            assignments.push(assignment(&candidate.pending, key));
        }
    }
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

fn cosine(left: &[f64], right: &[f64]) -> f64 {
    if left.is_empty() || left.len() != right.len() {
        return -1.0;
    }
    let numerator = left
        .iter()
        .zip(right)
        .map(|(left, right)| left * right)
        .sum::<f64>();
    let left_norm = left.iter().map(|value| value * value).sum::<f64>().sqrt();
    let right_norm = right.iter().map(|value| value * value).sum::<f64>().sqrt();
    if left_norm <= f64::EPSILON || right_norm <= f64::EPSILON {
        -1.0
    } else {
        numerator / (left_norm * right_norm)
    }
}

fn mean_vector(vectors: &[Vec<f64>]) -> Vec<f64> {
    let Some(width) = vectors.first().map(Vec::len).filter(|width| *width > 0) else {
        return Vec::new();
    };
    let valid = vectors
        .iter()
        .filter(|vector| vector.len() == width)
        .collect::<Vec<_>>();
    if valid.is_empty() {
        return Vec::new();
    }
    let mut totals = vec![0.0; width];
    for vector in &valid {
        for (total, value) in totals.iter_mut().zip(vector.iter()) {
            *total += value;
        }
    }
    let count = valid.len() as f64;
    totals.into_iter().map(|total| total / count).collect()
}

fn split_model_spec(spec: &str) -> (&str, &str) {
    spec.split_once(':').unwrap_or(("openai", spec))
}

fn nonempty(value: &str) -> Option<&str> {
    (!value.trim().is_empty()).then_some(value)
}

#[derive(Debug, Error)]
pub(super) enum SemanticLensPlanningError {
    #[error("Briefing semantic embedding shape mismatch: expected {expected}, got {actual}")]
    EmbeddingShape { expected: usize, actual: usize },
    #[error("Briefing semantic embeddings have inconsistent dimensions")]
    InconsistentEmbeddingWidth,
    #[error(transparent)]
    Provider(#[from] BriefingCompositionGatewayError),
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn unique_keys_are_normalized_and_suffixed() {
        let mut used = HashSet::from(["news-public-infrastructure".to_owned()]);
        assert_eq!(
            unique_lens_key("News-Public Infrastructure", &mut used),
            "news-public-infrastructure-2"
        );
    }

    #[test]
    fn centroid_model_change_resets_instead_of_blending() {
        let lens = BriefingSemanticLens {
            id: 1,
            key: "news-ai".to_owned(),
            title: "AI".to_owned(),
            deck: "Artificial intelligence systems.".to_owned(),
            position: 2,
            centroid: Some(vec![1.0, 0.0]),
            centroid_weight: 20,
            centroid_model: Some("openrouter:old".to_owned()),
            routing_rule: None,
            updated_at: Utc::now(),
        };
        let mut working = WorkingLens::existing(&lens, &[0.5, 0.5], "openrouter:new", 2);
        working.update_centroid(&[0.0, 1.0], 32, "openrouter:new");
        assert_eq!(working.centroid, Some(vec![0.0, 1.0]));
        assert_eq!(working.centroid_weight, 1);
        assert_eq!(working.similarity_vector, vec![0.5, 0.5]);
    }

    #[test]
    fn routing_keeps_the_first_lens_on_equal_similarity() {
        let lenses = vec![
            WorkingLens::new(
                "news-first".to_owned(),
                "First".to_owned(),
                "First semantic category.".to_owned(),
                2,
                vec![1.0, 0.0],
                1,
                "openrouter:model",
            ),
            WorkingLens::new(
                "news-second".to_owned(),
                "Second".to_owned(),
                "Second semantic category.".to_owned(),
                3,
                vec![1.0, 0.0],
                1,
                "openrouter:model",
            ),
        ];
        assert_eq!(best_lens(&[1.0, 0.0], &lenses), Some((0, 1.0)));
    }

    #[test]
    fn greedy_clustering_matches_similarity_boundary() {
        let pending = |id| BriefingUnassignedSource {
            pending_id: id,
            source_kind: "news".to_owned(),
            source_id: id,
            enqueued_at: Utc::now(),
            source: newsly_db::BriefingRefreshSource {
                source_key: format!("news:{id}"),
                kind: "news".to_owned(),
                id,
                title: format!("Source {id}"),
                source_name: None,
                summary: None,
                key_points: Vec::new(),
                url: None,
                image_url: None,
                thumbnail_url: None,
                published_at: None,
                briefing_context: None,
            },
        };
        let clusters = cluster_candidates(
            vec![
                Candidate {
                    pending: pending(1),
                    vector: vec![1.0, 0.0],
                },
                Candidate {
                    pending: pending(2),
                    vector: vec![0.99, 0.01],
                },
                Candidate {
                    pending: pending(3),
                    vector: vec![0.0, 1.0],
                },
            ],
            0.9,
        );
        assert_eq!(clusters.len(), 2);
        assert_eq!(clusters[0].candidates.len(), 2);
    }
}
