//! Versioned bridge between offline Python experiments and production Rust algorithms.

#![forbid(unsafe_code)]

use std::collections::{BTreeMap, HashMap, HashSet};

use newsly_domain::{
    EmbeddingVector, EmbeddingVectorStore, NewsRelationDocument, RelationEmbeddingText,
    RelationMatchResult, RelationThresholds, aggregate_relation_representative,
    can_bridge_relation_clusters, prepare_relation_embedding_texts, related_representatives,
};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use thiserror::Error;

pub const EVAL_PROTOCOL_VERSION: u16 = 1;

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct RelationEvalCase {
    pub case_id: String,
    pub label: String,
    pub groups: Vec<Vec<NewsRelationDocument>>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct PrepareRelationsRequest {
    pub version: u16,
    pub cases: Vec<RelationEvalCase>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct PrepareRelationsResponse {
    pub version: u16,
    pub texts: Vec<RelationEmbeddingText>,
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum EmbeddingNormalization {
    None,
    L2,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct EmbeddingBundle {
    pub version: u16,
    pub model: String,
    pub dimensions: usize,
    pub normalization: EmbeddingNormalization,
    pub items: Vec<EmbeddingVector>,
    pub timings_ms: BTreeMap<String, f64>,
    #[serde(default)]
    pub provider_metadata: Option<Value>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ThresholdSweep {
    pub label: String,
    pub primary: f64,
    pub secondary: f64,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ScoreRelationsRequest {
    pub version: u16,
    pub cases: Vec<RelationEvalCase>,
    pub embedding_bundle: EmbeddingBundle,
    pub thresholds: Vec<ThresholdSweep>,
    #[serde(default)]
    pub include_traces: bool,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ScoreRelationsResponse {
    pub version: u16,
    pub model: String,
    pub runs: Vec<RelationEvalRun>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct RelationEvalRun {
    pub threshold: ThresholdSweep,
    pub summary: RelationEvalSummary,
    pub results: Vec<RelationCaseResult>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct RelationEvalSummary {
    pub case_count: usize,
    pub passed_count: usize,
    pub failed_count: usize,
    pub macro_precision: f64,
    pub macro_recall: f64,
    pub macro_f1: f64,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct RelationCaseResult {
    pub case_id: String,
    pub label: String,
    pub expected_member_count: usize,
    pub gold_cluster_count: usize,
    pub predicted_cluster_count: usize,
    pub precision: f64,
    pub recall: f64,
    pub f1: f64,
    pub passed: bool,
    pub groups: Vec<PredictedGroup>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub traces: Option<Vec<RelationMatchResult>>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct PredictedGroup {
    pub representative_id: i64,
    pub member_count: usize,
    pub titles: Vec<Option<String>>,
}

#[derive(Debug, Error)]
pub enum EvalDriverError {
    #[error("unsupported eval protocol version {actual}; expected {expected}")]
    UnsupportedVersion { actual: u16, expected: u16 },
    #[error("eval case id must not be empty")]
    EmptyCaseId,
    #[error("eval case {case_id} has no documents")]
    EmptyCase { case_id: String },
    #[error("document id {id} occurs more than once in eval case {case_id}")]
    DuplicateDocument { case_id: String, id: i64 },
    #[error("threshold sweep label must not be empty")]
    EmptyThresholdLabel,
    #[error("at least one threshold sweep is required")]
    NoThresholds,
    #[error("embedding model must not be empty")]
    EmptyEmbeddingModel,
    #[error("embedding timing {name} must be finite and nonnegative")]
    InvalidTiming { name: String },
    #[error("invalid embedding bundle: {0}")]
    InvalidEmbeddingBundle(#[from] newsly_domain::InvalidEmbeddingBundle),
    #[error("relation matcher failed for case {case_id}: {source}")]
    RelationMatcher {
        case_id: String,
        #[source]
        source: newsly_domain::RelationMatchError,
    },
    #[error("relation matcher accepted unknown representative {id} in case {case_id}")]
    UnknownRepresentative { case_id: String, id: i64 },
}

/// Build the complete, deduplicated canonical text set for Python to encode.
///
/// # Errors
///
/// Returns [`EvalDriverError`] when the protocol or a case is malformed.
pub fn prepare_relations(
    request: &PrepareRelationsRequest,
) -> Result<PrepareRelationsResponse, EvalDriverError> {
    validate_version(request.version)?;
    validate_cases(&request.cases)?;
    let mut seen = HashSet::new();
    let mut texts = Vec::new();
    for case in &request.cases {
        let documents = case.groups.iter().flatten().cloned().collect::<Vec<_>>();
        if let Some(first) = documents.first() {
            append_unique_texts(
                &mut texts,
                &mut seen,
                prepare_relation_embedding_texts(first, &documents),
            );
        }
        // Representative enrichment deliberately keeps its original source and
        // platform while the richest member contributes domain/content. Emit
        // every possible hybrid provenance string before model inference.
        for representative in &documents {
            for evidence in &documents {
                let mut hybrid = evidence.clone();
                hybrid.source_label.clone_from(&representative.source_label);
                hybrid.platform.clone_from(&representative.platform);
                append_unique_texts(
                    &mut texts,
                    &mut seen,
                    prepare_relation_embedding_texts(&hybrid, &[]),
                );
            }
        }
    }
    Ok(PrepareRelationsResponse {
        version: EVAL_PROTOCOL_VERSION,
        texts,
    })
}

/// Score all cases and threshold sweeps through the production relation policy.
///
/// # Errors
///
/// Returns [`EvalDriverError`] when the request, embedding bundle, or matcher
/// inputs are invalid.
pub fn score_relations(
    request: ScoreRelationsRequest,
) -> Result<ScoreRelationsResponse, EvalDriverError> {
    validate_version(request.version)?;
    validate_version(request.embedding_bundle.version)?;
    validate_cases(&request.cases)?;
    validate_bundle_metadata(&request.embedding_bundle)?;
    if request.thresholds.is_empty() {
        return Err(EvalDriverError::NoThresholds);
    }
    for threshold in &request.thresholds {
        if threshold.label.trim().is_empty() {
            return Err(EvalDriverError::EmptyThresholdLabel);
        }
    }
    let model = request.embedding_bundle.model.clone();
    let store = EmbeddingVectorStore::new(
        request.embedding_bundle.dimensions,
        request.embedding_bundle.items,
    )?;
    let mut runs = Vec::with_capacity(request.thresholds.len());
    for threshold in request.thresholds {
        let mut results = Vec::with_capacity(request.cases.len());
        for case in &request.cases {
            results.push(evaluate_case(
                case,
                RelationThresholds {
                    primary: threshold.primary,
                    secondary: threshold.secondary,
                },
                &store,
                request.include_traces,
            )?);
        }
        let summary = summarize(&results);
        runs.push(RelationEvalRun {
            threshold,
            summary,
            results,
        });
    }
    Ok(ScoreRelationsResponse {
        version: EVAL_PROTOCOL_VERSION,
        model,
        runs,
    })
}

#[derive(Debug)]
struct RelationCluster {
    representative_id: i64,
    members: Vec<NewsRelationDocument>,
}

impl RelationCluster {
    fn representative(&self) -> NewsRelationDocument {
        let representative = self
            .members
            .iter()
            .find(|member| member.id == self.representative_id)
            .expect("cluster always contains its representative");
        aggregate_relation_representative(representative, &self.members)
    }
}

fn evaluate_case(
    case: &RelationEvalCase,
    thresholds: RelationThresholds,
    embeddings: &EmbeddingVectorStore,
    include_traces: bool,
) -> Result<RelationCaseResult, EvalDriverError> {
    let mut clusters: Vec<RelationCluster> = Vec::new();
    let mut gold_labels = HashMap::new();
    let mut traces = Vec::new();
    for (group_index, group) in case.groups.iter().enumerate() {
        for document in group {
            gold_labels.insert(document.id, group_index);
        }
    }
    let mut documents = case.groups.iter().flatten().cloned().collect::<Vec<_>>();
    documents.sort_by(|left, right| {
        left.ingested_at
            .cmp(&right.ingested_at)
            .then_with(|| left.id.cmp(&right.id))
    });
    for document in documents {
        reconcile_document(
            case,
            document,
            thresholds,
            embeddings,
            &mut clusters,
            &mut traces,
        )?;
    }

    let predicted_labels = clusters
        .iter()
        .flat_map(|cluster| {
            cluster
                .members
                .iter()
                .map(|member| (member.id, cluster.representative_id))
        })
        .collect::<HashMap<_, _>>();
    let gold_pairs = pairwise_sets(&gold_labels);
    let predicted_pairs = pairwise_sets(&predicted_labels);
    let true_positive = gold_pairs.intersection(&predicted_pairs).count();
    let precision = if predicted_pairs.is_empty() {
        1.0
    } else {
        ratio(true_positive, predicted_pairs.len())
    };
    let recall = if gold_pairs.is_empty() {
        1.0
    } else {
        ratio(true_positive, gold_pairs.len())
    };
    let f1 = if precision + recall == 0.0 {
        0.0
    } else {
        2.0 * precision * recall / (precision + recall)
    };
    let mut groups = clusters
        .iter()
        .map(|cluster| PredictedGroup {
            representative_id: cluster.representative_id,
            member_count: cluster.members.len(),
            titles: cluster
                .members
                .iter()
                .map(|member| member.primary_title.clone())
                .collect(),
        })
        .collect::<Vec<_>>();
    groups.sort_by(|left, right| {
        right
            .member_count
            .cmp(&left.member_count)
            .then_with(|| left.representative_id.cmp(&right.representative_id))
    });
    Ok(RelationCaseResult {
        case_id: case.case_id.clone(),
        label: case.label.clone(),
        expected_member_count: gold_labels.len(),
        gold_cluster_count: case.groups.len(),
        predicted_cluster_count: clusters.len(),
        precision,
        recall,
        f1,
        passed: gold_pairs == predicted_pairs,
        groups,
        traces: include_traces.then_some(traces),
    })
}

fn reconcile_document(
    case: &RelationEvalCase,
    document: NewsRelationDocument,
    thresholds: RelationThresholds,
    embeddings: &EmbeddingVectorStore,
    clusters: &mut Vec<RelationCluster>,
    traces: &mut Vec<RelationMatchResult>,
) -> Result<(), EvalDriverError> {
    let representatives = clusters
        .iter()
        .map(RelationCluster::representative)
        .collect::<Vec<_>>();
    let matched = related_representatives(&document, &representatives, thresholds, embeddings)
        .map_err(|source| EvalDriverError::RelationMatcher {
            case_id: case.case_id.clone(),
            source,
        })?;
    let accepted_ids = matched.accepted_ids.clone();
    traces.push(matched);
    let Some(target_id) = accepted_ids.first().copied() else {
        clusters.push(RelationCluster {
            representative_id: document.id,
            members: vec![document],
        });
        return Ok(());
    };
    let target_index = cluster_index(clusters, target_id).ok_or_else(|| {
        EvalDriverError::UnknownRepresentative {
            case_id: case.case_id.clone(),
            id: target_id,
        }
    })?;
    let target_before_merge = clusters[target_index].representative();
    let mut merge_indexes = Vec::new();
    for other_id in accepted_ids.into_iter().skip(1) {
        let other_index = cluster_index(clusters, other_id).ok_or_else(|| {
            EvalDriverError::UnknownRepresentative {
                case_id: case.case_id.clone(),
                id: other_id,
            }
        })?;
        if can_bridge_relation_clusters(
            &target_before_merge,
            &clusters[other_index].representative(),
        ) {
            merge_indexes.push(other_index);
        }
    }
    merge_indexes.sort_unstable();
    merge_indexes.dedup();
    for index in merge_indexes.into_iter().rev() {
        let merged = clusters.remove(index);
        let adjusted_target = cluster_index(clusters, target_id).expect("target cluster remains");
        clusters[adjusted_target].members.extend(merged.members);
    }
    let target_index = cluster_index(clusters, target_id).expect("target cluster remains");
    clusters[target_index].members.push(document);
    Ok(())
}

fn cluster_index(clusters: &[RelationCluster], representative_id: i64) -> Option<usize> {
    clusters
        .iter()
        .position(|cluster| cluster.representative_id == representative_id)
}

fn pairwise_sets<T>(labels: &HashMap<i64, T>) -> HashSet<(i64, i64)>
where
    T: Eq,
{
    let mut ids = labels.keys().copied().collect::<Vec<_>>();
    ids.sort_unstable();
    let mut pairs = HashSet::new();
    for (offset, left) in ids.iter().enumerate() {
        for right in ids.iter().skip(offset + 1) {
            if labels.get(left) == labels.get(right) {
                pairs.insert((*left, *right));
            }
        }
    }
    pairs
}

fn summarize(results: &[RelationCaseResult]) -> RelationEvalSummary {
    let case_count = results.len();
    let passed_count = results.iter().filter(|result| result.passed).count();
    RelationEvalSummary {
        case_count,
        passed_count,
        failed_count: case_count - passed_count,
        macro_precision: average(results, |result| result.precision),
        macro_recall: average(results, |result| result.recall),
        macro_f1: average(results, |result| result.f1),
    }
}

fn average(results: &[RelationCaseResult], value: impl Fn(&RelationCaseResult) -> f64) -> f64 {
    if results.is_empty() {
        0.0
    } else {
        results.iter().map(value).sum::<f64>() / count_as_f64(results.len())
    }
}

fn ratio(numerator: usize, denominator: usize) -> f64 {
    count_as_f64(numerator) / count_as_f64(denominator)
}

fn count_as_f64(value: usize) -> f64 {
    f64::from(u32::try_from(value).expect("an in-memory eval collection fits in u32"))
}

fn append_unique_texts(
    output: &mut Vec<RelationEmbeddingText>,
    seen: &mut HashSet<String>,
    inputs: Vec<RelationEmbeddingText>,
) {
    for input in inputs {
        if seen.insert(input.text_sha256.clone()) {
            output.push(input);
        }
    }
}

fn validate_version(version: u16) -> Result<(), EvalDriverError> {
    if version != EVAL_PROTOCOL_VERSION {
        return Err(EvalDriverError::UnsupportedVersion {
            actual: version,
            expected: EVAL_PROTOCOL_VERSION,
        });
    }
    Ok(())
}

fn validate_cases(cases: &[RelationEvalCase]) -> Result<(), EvalDriverError> {
    for case in cases {
        if case.case_id.trim().is_empty() {
            return Err(EvalDriverError::EmptyCaseId);
        }
        let mut ids = HashSet::new();
        let mut count = 0;
        for document in case.groups.iter().flatten() {
            count += 1;
            if !ids.insert(document.id) {
                return Err(EvalDriverError::DuplicateDocument {
                    case_id: case.case_id.clone(),
                    id: document.id,
                });
            }
        }
        if count == 0 {
            return Err(EvalDriverError::EmptyCase {
                case_id: case.case_id.clone(),
            });
        }
    }
    Ok(())
}

fn validate_bundle_metadata(bundle: &EmbeddingBundle) -> Result<(), EvalDriverError> {
    if bundle.model.trim().is_empty() {
        return Err(EvalDriverError::EmptyEmbeddingModel);
    }
    for (name, value) in &bundle.timings_ms {
        if !value.is_finite() || *value < 0.0 {
            return Err(EvalDriverError::InvalidTiming { name: name.clone() });
        }
    }
    Ok(())
}
