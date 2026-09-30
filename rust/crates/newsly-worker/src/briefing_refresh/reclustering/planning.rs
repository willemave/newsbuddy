use chrono::NaiveDate;
use newsly_db::news_category_reclustering::{NewsCategoryPublicationLens, NewsCategorySnapshot};
use newsly_domain::{
    NewsLensClusterIdentity, NewsLensLineage, NewsLensReclusterCluster,
    NewsLensReclusterDiagnostics, NewsLensReclusterExistingCluster, NewsLensReclusterStory,
    NewsLensReclusteringConfig, NewsLensReclusteringInput, NewsLensReclusteringPlan,
};
use newsly_providers::{
    BRIEFING_LENS_NAMING_MAX_INPUT_BYTES, BriefingLensNamingBatch, BriefingLensNamingBatchRequest,
    BriefingLensNamingCategory, BriefingLensNamingStory,
};
use serde::{Deserialize, Serialize};
use std::collections::HashMap;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub(super) struct TrackedCluster {
    pub cluster: NewsLensReclusterCluster,
    pub supported_nights: u32,
    pub published: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub(super) struct CandidateState {
    pub schema_version: u32,
    pub local_date: NaiveDate,
    pub model: String,
    pub clusters: Vec<TrackedCluster>,
    pub mixed_story_ids: Vec<String>,
    pub lineage: Vec<NewsLensLineage>,
    pub diagnostics: NewsLensReclusterDiagnostics,
}

pub(super) fn input(
    snapshot: &NewsCategorySnapshot,
    prior: Option<&CandidateState>,
) -> NewsLensReclusteringInput {
    let model = format!("openrouter:{}", snapshot.model);
    let width = snapshot.stories.first().map(|s| s.vector.len());
    let mut existing_clusters = Vec::new();
    let compatible_prior = prior.filter(|prior| {
        prior.model == snapshot.model
            && prior.clusters.len() <= 10
            && prior.clusters.iter().all(|tracked| {
                let center = &tracked.cluster.center;
                Some(center.len()) == width
                    && center.iter().all(|value| value.is_finite())
                    && center.iter().any(|value| *value != 0.0)
            })
    });
    if let Some(prior) = compatible_prior {
        for cluster in &prior.clusters {
            existing_clusters.push(NewsLensReclusterExistingCluster {
                stable_id: cluster.cluster.plan_id.clone(),
                center: cluster.cluster.center.clone(),
                member_story_ids: cluster.cluster.story_ids.clone(),
            });
        }
    } else {
        for lens in &snapshot.lenses {
            let Some(center) = lens.centroid.as_ref().filter(|vector| {
                lens.centroid_model.as_deref() == Some(model.as_str())
                    && Some(vector.len()) == width
                    && vector.iter().all(|value| value.is_finite())
                    && vector.iter().any(|value| *value != 0.0)
            }) else {
                continue;
            };
            existing_clusters.push(NewsLensReclusterExistingCluster {
                stable_id: lens.key.clone(),
                center: center.clone(),
                member_story_ids: Vec::new(),
            });
            if existing_clusters.len() == 10 {
                break;
            }
        }
    }
    NewsLensReclusteringInput {
        now: snapshot.cutoff.timestamp(),
        stories: snapshot
            .stories
            .iter()
            .map(|s| NewsLensReclusterStory {
                story_id: s.source.id.to_string(),
                event_id: s.event_id.to_string(),
                available_at: s.available_at.timestamp(),
                vector: s.vector.clone(),
            })
            .collect(),
        existing_clusters,
        config: NewsLensReclusteringConfig {
            expected_dimensions: width,
            ..Default::default()
        },
    }
}

pub(super) fn current_publication_matches(
    snapshot: &NewsCategorySnapshot,
    candidate: &CandidateState,
    names: &BriefingLensNamingBatch,
) -> bool {
    if candidate.clusters.iter().any(|cluster| !cluster.published)
        || snapshot.lenses.len() != candidate.clusters.len()
        || names.categories.len() != candidate.clusters.len()
    {
        return false;
    }
    let centroid_model = format!("openrouter:{}", snapshot.model);
    candidate.clusters.iter().all(|cluster| {
        let Some(lens) = snapshot
            .lenses
            .iter()
            .find(|lens| lens.key == cluster.cluster.plan_id)
        else {
            return false;
        };
        let Some(name) = names
            .categories
            .iter()
            .find(|name| name.stable_id == cluster.cluster.plan_id)
        else {
            return false;
        };
        lens.centroid.as_ref() == Some(&cluster.cluster.center)
            && lens.centroid_model.as_deref() == Some(centroid_model.as_str())
            && lens.title == name.title
            && lens.deck == name.deck
            && lens.routing_rule.as_deref() == Some(name.routing_rule.as_str())
    })
}

pub(super) fn track(
    snapshot: &NewsCategorySnapshot,
    prior: Option<&CandidateState>,
    mut plan: NewsLensReclusteringPlan,
    local_date: NaiveDate,
    run_id: i64,
) -> CandidateState {
    let consecutive = prior.is_some_and(|p| p.local_date.succ_opt() == Some(local_date));
    let mut clusters = Vec::new();
    for (index, mut cluster) in plan.clusters.drain(..).enumerate() {
        let old_plan_id = cluster.plan_id.clone();
        let key = match &cluster.identity {
            NewsLensClusterIdentity::Existing { stable_id } => stable_id.clone(),
            NewsLensClusterIdentity::New => format!("news-nightly-{run_id}-{index}"),
        };
        let published = snapshot.lenses.iter().any(|l| l.key == key);
        let supported_nights = prior
            .and_then(|p| {
                p.clusters
                    .iter()
                    .find(|candidate| candidate.cluster.plan_id == key)
            })
            .map_or(1, |c| {
                if consecutive {
                    c.supported_nights.saturating_add(1)
                } else if prior.is_some_and(|p| p.local_date == local_date) {
                    c.supported_nights
                } else {
                    1
                }
            });
        cluster.plan_id.clone_from(&key);
        for edge in &mut plan.lineage {
            if edge.new_plan_id.as_deref() == Some(old_plan_id.as_str()) {
                edge.new_plan_id = Some(key.clone());
            }
        }
        clusters.push(TrackedCluster {
            cluster,
            supported_nights,
            published,
        });
    }
    CandidateState {
        schema_version: 2,
        local_date,
        model: snapshot.model.clone(),
        clusters,
        mixed_story_ids: plan.mixed_story_ids,
        lineage: plan.lineage,
        diagnostics: plan.diagnostics,
    }
}

pub(super) fn naming_request(
    snapshot: &NewsCategorySnapshot,
    candidate: &CandidateState,
) -> anyhow::Result<BriefingLensNamingBatchRequest> {
    let stories: HashMap<_, _> = snapshot
        .stories
        .iter()
        .map(|s| (s.source.id.to_string(), s))
        .collect();
    let mut categories = Vec::new();
    for tracked in &candidate.clusters {
        if !tracked.published && tracked.supported_nights < 2 {
            continue;
        }
        let cluster = &tracked.cluster;
        let previous = snapshot
            .lenses
            .iter()
            .find(|lens| lens.key == cluster.plan_id);
        categories.push(BriefingLensNamingCategory {
            stable_id: cluster.plan_id.clone(),
            current_title: previous.map_or_else(String::new, |l| bounded(&l.title, 80)),
            current_deck: previous.map_or_else(String::new, |l| bounded(&l.deck, 400)),
            current_routing_rule: previous
                .and_then(|l| l.routing_rule.as_deref())
                .map_or_else(String::new, |s| bounded(s, 400)),
            story_count: cluster.story_ids.len(),
            stories: cluster
                .representative_story_ids
                .iter()
                .filter_map(|id| stories.get(id))
                .map(|s| BriefingLensNamingStory {
                    title: bounded(&s.source.title, 160),
                    summary: bounded(
                        &format!(
                            "{} {}",
                            s.source.summary.as_deref().unwrap_or(""),
                            s.source.key_points.join(" ")
                        ),
                        600,
                    ),
                    source_name: s.source.source_name.as_ref().map(|s| bounded(s, 80)),
                    published_at: s.source.published_at.map(|d| d.to_rfc3339()),
                })
                .collect(),
        });
    }
    let mut request = BriefingLensNamingBatchRequest { categories };
    // Preserve all 25 diverse examples even for multibyte text by shortening
    // excerpts uniformly instead of silently dropping most stories.
    for limit in [300, 150, 75, 0] {
        if serde_json::to_vec_pretty(&request)?.len() <= BRIEFING_LENS_NAMING_MAX_INPUT_BYTES {
            return Ok(request);
        }
        for category in &mut request.categories {
            for story in &mut category.stories {
                story.summary = bounded(&story.summary, limit);
                if limit == 0 {
                    story.title = bounded(&story.title, 60);
                    story.source_name = None;
                }
            }
        }
    }
    anyhow::ensure!(
        serde_json::to_vec_pretty(&request)?.len() <= BRIEFING_LENS_NAMING_MAX_INPUT_BYTES,
        "naming input exceeds byte budget"
    );
    Ok(request)
}

pub(super) fn publication(
    candidate: &CandidateState,
    names: &BriefingLensNamingBatch,
) -> anyhow::Result<Vec<NewsCategoryPublicationLens>> {
    names
        .categories
        .iter()
        .map(|name| {
            let cluster = candidate
                .clusters
                .iter()
                .find(|candidate| candidate.cluster.plan_id == name.stable_id)
                .ok_or_else(|| anyhow::anyhow!("naming result references unknown cluster"))?;
            anyhow::ensure!(
                cluster.published || cluster.supported_nights >= 2,
                "new category lacks persistence"
            );
            Ok(NewsCategoryPublicationLens {
                key: cluster.cluster.plan_id.clone(),
                title: name.title.clone(),
                deck: name.deck.clone(),
                routing_rule: name.routing_rule.clone(),
                centroid: cluster.cluster.center.clone(),
                story_ids: cluster
                    .cluster
                    .story_ids
                    .iter()
                    .map(|id| id.parse::<i64>())
                    .collect::<Result<Vec<_>, _>>()?,
            })
        })
        .collect()
}

fn bounded(text: &str, limit: usize) -> String {
    text.chars().take(limit).collect()
}
