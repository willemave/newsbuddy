use std::collections::BTreeSet;
use std::sync::LazyLock;
use std::time::Instant;

use newsly_db::{BriefingRefreshSource, news_lens_embeddings};
use newsly_providers::{BriefingCompositionGateway, BriefingEmbeddingBatch};
use regex::Regex;
use sqlx::PgPool;
use tracing::Instrument;

const MATCH_STOPWORDS: [&str; 20] = [
    "about", "after", "against", "along", "also", "amid", "been", "between", "from", "have",
    "into", "more", "news", "over", "that", "their", "them", "they", "this", "with",
];
static MATCH_TOKEN_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"[a-z0-9]{3,}").expect("Briefing match-token regex must compile"));

#[derive(Debug, Clone, PartialEq)]
pub(super) struct PlannedBriefingWindow {
    pub sources: Vec<BriefingRefreshSource>,
    pub event_groups: Vec<Vec<String>>,
}

#[derive(Debug, Clone, PartialEq)]
pub(super) struct PlannedBriefingWindows {
    pub windows: Vec<PlannedBriefingWindow>,
    pub embedding_batches: Vec<BriefingEmbeddingBatch>,
}

pub(super) async fn plan_windows(
    pool: &PgPool,
    gateway: &BriefingCompositionGateway,
    sources: &[BriefingRefreshSource],
    tier: &str,
    news_window_max: usize,
    event_similarity: f64,
    embedding_batch_size: usize,
) -> PlannedBriefingWindows {
    let cached = if tier == "news" && sources.len() > 1 {
        match cached_news_vectors(pool, sources, gateway.embedding_model()).await {
            Ok(vectors) => vectors,
            Err(error) => {
                tracing::warn!(source_count = sources.len(), error = %error,
                    "Briefing event embedding cache unavailable; requesting missing vectors");
                vec![None; sources.len()]
            }
        }
    } else {
        vec![None; sources.len()]
    };
    plan_windows_with_embeddings(
        sources,
        tier,
        news_window_max,
        event_similarity,
        embedding_batch_size,
        cached,
        |texts| async move { gateway.embed(&texts).await },
    )
    .await
}

// The cache owns model, exact-input, encoder, and vector validation. No connection
// remains checked out during external requests.
async fn cached_news_vectors(
    pool: &PgPool,
    sources: &[BriefingRefreshSource],
    model: &str,
) -> Result<Vec<Option<Vec<f64>>>, sqlx::Error> {
    let started = Instant::now();
    let work = async {
        let mut connection = pool.acquire().await?;
        let mut vectors = Vec::with_capacity(sources.len());
        for source in sources {
            vectors.push(if source.kind == "news" {
                news_lens_embeddings::load(
                    &mut connection,
                    source.id,
                    model,
                    &source.embedding_text(),
                )
                .await?
            } else {
                None
            });
        }
        Ok::<_, sqlx::Error>(vectors)
    };
    let result = work
        .instrument(tracing::info_span!(
            "briefing_event_cache_load",
            source_count = sources.len(),
            model
        ))
        .await;
    tracing::info!(
        stage = "briefing_event_cache_load",
        source_count = sources.len(),
        cache_hits = result
            .as_ref()
            .map_or(0, |vectors| vectors.iter().filter(|v| v.is_some()).count()),
        elapsed_ms = started.elapsed().as_millis(),
        succeeded = result.is_ok(),
        "Briefing event planning timing"
    );
    result
}

async fn plan_windows_with_embeddings<F, Fut, E>(
    sources: &[BriefingRefreshSource],
    tier: &str,
    news_window_max: usize,
    event_similarity: f64,
    embedding_batch_size: usize,
    mut cached: Vec<Option<Vec<f64>>>,
    embed: F,
) -> PlannedBriefingWindows
where
    F: Fn(Vec<String>) -> Fut,
    Fut: std::future::Future<Output = Result<BriefingEmbeddingBatch, E>>,
    E: std::fmt::Display,
{
    if sources.is_empty() {
        return PlannedBriefingWindows {
            windows: Vec::new(),
            embedding_batches: Vec::new(),
        };
    }
    if tier != "news" {
        return PlannedBriefingWindows {
            windows: sources
                .iter()
                .cloned()
                .map(|source| PlannedBriefingWindow {
                    event_groups: vec![vec![source.source_key.clone()]],
                    sources: vec![source],
                })
                .collect(),
            embedding_batches: Vec::new(),
        };
    }
    if cached.len() != sources.len() {
        cached = vec![None; sources.len()];
    }
    let mut embedding_batches = Vec::new();
    if sources.len() > 1 {
        let missing: Vec<_> = cached
            .iter()
            .enumerate()
            .filter_map(|(i, v)| v.is_none().then_some(i))
            .collect();
        for indices in missing.chunks(embedding_batch_size.clamp(1, 128)) {
            let texts = indices
                .iter()
                .map(|index| sources[*index].embedding_text())
                .collect();
            let started = Instant::now();
            let result = embed(texts)
                .instrument(tracing::info_span!(
                    "briefing_event_embedding_request",
                    source_count = indices.len()
                ))
                .await;
            tracing::info!(
                stage = "briefing_event_embedding_request",
                source_count = indices.len(),
                elapsed_ms = started.elapsed().as_millis(),
                succeeded = result.is_ok(),
                "Briefing event planning timing"
            );
            match result {
                Ok(batch) => {
                    let valid_shape = batch.vectors.len() == indices.len();
                    if valid_shape {
                        for (index, vector) in indices.iter().zip(&batch.vectors) {
                            cached[*index] = Some(vector.clone());
                        }
                    }
                    // Only fresh provider calls carry usage, including an invalid result.
                    embedding_batches.push(batch);
                    if !valid_shape {
                        cached.clear();
                        break;
                    }
                }
                Err(error) => {
                    tracing::warn!(source_count=sources.len(), error=%error,
                        "Briefing event embeddings failed; preserving source arrival order");
                    cached.clear();
                    break;
                }
            }
        }
    }
    let vectors: Vec<_> = cached.into_iter().flatten().collect();
    let events = if vectors.len() == sources.len() && dimensions_match(&vectors) {
        group_news_sources(sources, &vectors, event_similarity)
    } else {
        (0..sources.len()).map(|index| vec![index]).collect()
    };
    let event_windows = balance_event_windows(&events, news_window_max.max(1));
    let windows = event_windows
        .into_iter()
        .map(|events| {
            let event_groups = events
                .iter()
                .map(|event| {
                    event
                        .iter()
                        .map(|index| sources[*index].source_key.clone())
                        .collect::<Vec<_>>()
                })
                .collect::<Vec<_>>();
            let sources = events
                .into_iter()
                .flatten()
                .map(|index| sources[index].clone())
                .collect();
            PlannedBriefingWindow {
                sources,
                event_groups,
            }
        })
        .collect();
    PlannedBriefingWindows {
        windows,
        embedding_batches,
    }
}

fn group_news_sources(
    sources: &[BriefingRefreshSource],
    vectors: &[Vec<f64>],
    threshold: f64,
) -> Vec<Vec<usize>> {
    let token_sets = sources
        .iter()
        .map(|source| match_tokens(&source.title))
        .collect::<Vec<_>>();
    let mut events: Vec<Vec<usize>> = Vec::new();
    let mut centroids: Vec<Vec<f64>> = Vec::new();
    let mut event_tokens: Vec<BTreeSet<String>> = Vec::new();
    for (index, vector) in vectors.iter().enumerate() {
        let best = centroids
            .iter()
            .enumerate()
            .map(|(event_index, centroid)| (event_index, dot(vector, centroid)))
            .max_by(|left, right| left.1.total_cmp(&right.1));
        if let Some((event_index, score)) = best
            && score >= threshold
            && !token_sets[index].is_disjoint(&event_tokens[event_index])
        {
            events[event_index].push(index);
            centroids[event_index] = normalized_mean(&events[event_index], vectors);
            event_tokens[event_index].extend(token_sets[index].iter().cloned());
            continue;
        }
        events.push(vec![index]);
        centroids.push(vector.clone());
        event_tokens.push(token_sets[index].clone());
    }
    events
}

fn balance_event_windows(events: &[Vec<usize>], max_events: usize) -> Vec<Vec<Vec<usize>>> {
    if events.is_empty() {
        return Vec::new();
    }
    let window_count = events.len().div_ceil(max_events.max(1));
    let base_size = events.len() / window_count;
    let larger_windows = events.len() % window_count;
    let mut windows = Vec::with_capacity(window_count);
    let mut cursor = 0_usize;
    for window_index in 0..window_count {
        let size = base_size + usize::from(window_index < larger_windows);
        windows.push(events[cursor..cursor + size].to_vec());
        cursor += size;
    }
    windows
}

fn match_tokens(text: &str) -> BTreeSet<String> {
    MATCH_TOKEN_RE
        .find_iter(&text.to_ascii_lowercase())
        .filter_map(|matched| {
            let token = normalize_match_token(matched.as_str());
            (!token.is_empty() && !MATCH_STOPWORDS.contains(&token.as_str())).then_some(token)
        })
        .collect()
}

fn normalize_match_token(token: &str) -> String {
    let mut normalized = token.to_owned();
    if normalized.ends_with("ing") && normalized.len() > 6 {
        normalized.truncate(normalized.len() - 3);
    } else if (normalized.ends_with("ed") && normalized.len() > 5)
        || (normalized.ends_with("es") && normalized.len() > 5)
    {
        normalized.truncate(normalized.len() - 2);
    } else if normalized.ends_with('s') && normalized.len() > 4 {
        normalized.truncate(normalized.len() - 1);
    }
    normalized
}

fn dimensions_match(vectors: &[Vec<f64>]) -> bool {
    let Some(first) = vectors.first() else {
        return true;
    };
    !first.is_empty()
        && vectors.iter().all(|vector| {
            vector.len() == first.len() && vector.iter().all(|value| value.is_finite())
        })
}

fn dot(left: &[f64], right: &[f64]) -> f64 {
    left.iter()
        .zip(right)
        .map(|(left, right)| left * right)
        .sum()
}

fn normalized_mean(indexes: &[usize], vectors: &[Vec<f64>]) -> Vec<f64> {
    let dimensions = vectors.first().map_or(0, Vec::len);
    let mut centroid = vec![0.0; dimensions];
    for index in indexes {
        for (target, value) in centroid.iter_mut().zip(&vectors[*index]) {
            *target += value;
        }
    }
    let member_count = u32::try_from(indexes.len()).unwrap_or(u32::MAX);
    let scale = 1.0 / f64::from(member_count);
    for value in &mut centroid {
        *value *= scale;
    }
    let norm = centroid
        .iter()
        .map(|value| value * value)
        .sum::<f64>()
        .sqrt();
    if norm > 1e-12 {
        for value in &mut centroid {
            *value /= norm;
        }
    }
    centroid
}

#[cfg(test)]
mod tests;
