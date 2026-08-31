//! Pure news-clustering policy shared by production workers and offline evals.
//!
//! Embedding providers intentionally live outside this module. Callers first ask
//! for the canonical texts, build an embedding bundle in any language, and then
//! pass that immutable bundle back to this policy. This keeps Python useful for
//! model experiments without making Python the owner of production decisions.

use std::cmp::Ordering;
use std::collections::{HashMap, HashSet};
use std::fmt::Write as _;
use std::sync::LazyLock;

use chrono::{DateTime, Utc};
use regex::Regex;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use thiserror::Error;

const TITLE_WEIGHT: f64 = 0.55;
const CONTENT_WEIGHT: f64 = 0.35;
const PROVENANCE_WEIGHT: f64 = 0.10;
const SEMANTIC_PREFILTER_MAX_CANDIDATES: usize = 12;
const CLUSTER_RELATED_TITLE_LIMIT: usize = 6;

static MATCH_TOKEN_PATTERN: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"[a-z0-9]{3,}").expect("match-token regex is valid"));
static VERSION_DETAIL_PATTERN: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"\b\d+(?:\.\d+)+\b").expect("version-detail regex is valid"));
static MODEL_DETAIL_PATTERN: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"\b(?:[a-z]{1,6}\d{1,4}[a-z]{0,2}|\d{1,4}[a-z]{1,3})\b")
        .expect("model-detail regex is valid")
});
static DIGIT_SUFFIX_PATTERN: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"[a-z]+$").expect("digit-suffix regex is valid"));

const MATCH_STOPWORDS: [&str; 20] = [
    "about", "after", "against", "along", "also", "amid", "been", "between", "from", "have",
    "into", "more", "news", "over", "that", "their", "them", "they", "this", "with",
];
const NON_DISTINCTIVE_DIGIT_SUFFIXES: [&str; 12] = [
    "k", "m", "b", "bn", "t", "tn", "st", "nd", "rd", "th", "s", "x",
];

/// One immutable document presented to the relation policy.
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct NewsRelationDocument {
    pub id: i64,
    #[serde(default)]
    pub primary_title: Option<String>,
    #[serde(default)]
    pub related_titles: Vec<String>,
    #[serde(default)]
    pub summary_key_points: Vec<String>,
    #[serde(default)]
    pub summary_text: Option<String>,
    #[serde(default)]
    pub article_domain: Option<String>,
    #[serde(default)]
    pub source_label: Option<String>,
    #[serde(default)]
    pub platform: Option<String>,
    #[serde(default)]
    pub exact_relation_key: Option<RelationExactKey>,
    #[serde(default)]
    pub ingested_at: Option<DateTime<Utc>>,
}

/// A definitive, already-normalized story/item/provider identity.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct RelationExactKey {
    pub kind: String,
    pub value: String,
}

/// Thresholds are policy inputs so eval sweeps do not mutate process state.
#[derive(Clone, Copy, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct RelationThresholds {
    pub primary: f64,
    pub secondary: f64,
}

impl RelationThresholds {
    fn validate(self) -> Result<Self, RelationMatchError> {
        if !self.primary.is_finite()
            || !self.secondary.is_finite()
            || !(-1.0..=1.0).contains(&self.primary)
            || !(-1.0..=1.0).contains(&self.secondary)
            || self.primary < self.secondary
        {
            return Err(RelationMatchError::InvalidThresholds {
                primary: self.primary,
                secondary: self.secondary,
            });
        }
        Ok(self)
    }
}

/// One canonical string that an offline embedding pipeline must encode.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct RelationEmbeddingText {
    pub id: String,
    pub text_sha256: String,
    pub text: String,
}

/// One vector from a language-neutral embedding bundle.
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct EmbeddingVector {
    pub id: String,
    pub text_sha256: String,
    pub vector: Vec<f64>,
}

/// Validated, normalized vectors addressed by the SHA-256 of canonical text.
#[derive(Clone, Debug)]
pub struct EmbeddingVectorStore {
    dimensions: usize,
    vectors: HashMap<String, Vec<f64>>,
}

impl EmbeddingVectorStore {
    /// Validates dimensions, IDs, hashes, finiteness, and zero norms. Vectors
    /// are always L2-normalized here, even when an eval provider already did it.
    ///
    /// # Errors
    ///
    /// Returns [`InvalidEmbeddingBundle`] for any malformed or duplicated item.
    pub fn new(
        dimensions: usize,
        items: impl IntoIterator<Item = EmbeddingVector>,
    ) -> Result<Self, InvalidEmbeddingBundle> {
        if dimensions == 0 {
            return Err(InvalidEmbeddingBundle::ZeroDimensions);
        }
        let mut vectors = HashMap::new();
        let mut ids = HashSet::new();
        for item in items {
            if item.id.trim().is_empty() || !is_sha256(&item.text_sha256) {
                return Err(InvalidEmbeddingBundle::InvalidIdentity { id: item.id });
            }
            if !ids.insert(item.id.clone()) {
                return Err(InvalidEmbeddingBundle::DuplicateIdentity { id: item.id });
            }
            if item.vector.len() != dimensions {
                return Err(InvalidEmbeddingBundle::DimensionMismatch {
                    id: item.id,
                    expected: dimensions,
                    actual: item.vector.len(),
                });
            }
            if item.vector.iter().any(|component| !component.is_finite()) {
                return Err(InvalidEmbeddingBundle::NonFiniteVector { id: item.id });
            }
            let squared_norm = item
                .vector
                .iter()
                .map(|component| component.powi(2))
                .sum::<f64>();
            if squared_norm <= f64::EPSILON {
                return Err(InvalidEmbeddingBundle::ZeroNormVector { id: item.id });
            }
            let norm = squared_norm.sqrt();
            let normalized = item
                .vector
                .into_iter()
                .map(|component| component / norm)
                .collect::<Vec<_>>();
            if vectors
                .insert(item.text_sha256.clone(), normalized)
                .is_some()
            {
                return Err(InvalidEmbeddingBundle::DuplicateIdentity {
                    id: item.text_sha256,
                });
            }
        }
        Ok(Self {
            dimensions,
            vectors,
        })
    }

    pub const fn dimensions(&self) -> usize {
        self.dimensions
    }

    fn cosine(&self, left: &str, right: &str) -> Result<f64, RelationMatchError> {
        let left_key = relation_embedding_key(left);
        let right_key = relation_embedding_key(right);
        let left_vector = self
            .vectors
            .get(&left_key)
            .ok_or(RelationMatchError::MissingEmbedding { key: left_key })?;
        let right_vector = self
            .vectors
            .get(&right_key)
            .ok_or(RelationMatchError::MissingEmbedding { key: right_key })?;
        debug_assert_eq!(left_vector.len(), self.dimensions);
        debug_assert_eq!(right_vector.len(), self.dimensions);
        Ok(left_vector
            .iter()
            .zip(right_vector)
            .map(|(left, right)| left * right)
            .sum::<f64>()
            .clamp(-1.0, 1.0))
    }
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum RelationMatchPath {
    Exact,
    NoCandidates,
    PrefilterEmpty,
    Embedding,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum RelationDecisionOutcome {
    PrimaryAccepted,
    SecondaryAccepted,
    SecondaryGuardRejected,
    SecondaryDetailVeto,
    BelowSecondary,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct RelationDecision {
    pub candidate_id: i64,
    pub candidate_title: Option<String>,
    pub title_score: Option<f64>,
    pub content_score: Option<f64>,
    pub provenance_score: Option<f64>,
    pub combined_score: f64,
    pub outcome: RelationDecisionOutcome,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct RelationMatchResult {
    pub item_id: i64,
    pub item_title: Option<String>,
    pub candidate_count: usize,
    pub path: RelationMatchPath,
    pub decisions: Vec<RelationDecision>,
    pub accepted_ids: Vec<i64>,
}

#[derive(Debug, Error)]
pub enum InvalidEmbeddingBundle {
    #[error("embedding dimensions must be greater than zero")]
    ZeroDimensions,
    #[error("embedding item {id} must have a nonempty id and lowercase text SHA-256")]
    InvalidIdentity { id: String },
    #[error("embedding item {id} has {actual} dimensions; expected {expected}")]
    DimensionMismatch {
        id: String,
        expected: usize,
        actual: usize,
    },
    #[error("embedding item {id} contains a non-finite component")]
    NonFiniteVector { id: String },
    #[error("embedding item {id} has zero norm")]
    ZeroNormVector { id: String },
    #[error("embedding item {id} is duplicated")]
    DuplicateIdentity { id: String },
}

#[derive(Debug, Error)]
pub enum RelationMatchError {
    #[error("invalid thresholds: primary={primary}, secondary={secondary}")]
    InvalidThresholds { primary: f64, secondary: f64 },
    #[error("candidate id {id} is duplicated")]
    DuplicateCandidate { id: i64 },
    #[error("embedding bundle is missing canonical text {key}")]
    MissingEmbedding { key: String },
}

/// The stable ID used by both the Python bundle builder and Rust policy.
pub fn relation_embedding_key(text: &str) -> String {
    Sha256::digest(text.as_bytes())
        .iter()
        .fold(String::with_capacity(64), |mut output, byte| {
            write!(output, "{byte:02x}").expect("writing into a String cannot fail");
            output
        })
}

/// Return every canonical text required to compare `item` with `candidates`.
/// Identical text is emitted once and ordered by first use.
pub fn prepare_relation_embedding_texts(
    item: &NewsRelationDocument,
    candidates: &[NewsRelationDocument],
) -> Vec<RelationEmbeddingText> {
    let mut seen = HashSet::new();
    let mut texts = Vec::new();
    for text in document_embedding_texts(item, false).into_iter().chain(
        candidates
            .iter()
            .flat_map(|candidate| document_embedding_texts(candidate, true)),
    ) {
        if !seen.insert(text.clone()) {
            continue;
        }
        let key = relation_embedding_key(&text);
        texts.push(RelationEmbeddingText {
            id: key.clone(),
            text_sha256: key,
            text,
        });
    }
    texts
}

/// Whether a new item may bridge two already-existing representative clusters.
/// A shared exact key always wins; otherwise conflicting version/model details
/// keep the clusters separate even when both matched the bridge item.
pub fn can_bridge_relation_clusters(
    left: &NewsRelationDocument,
    right: &NewsRelationDocument,
) -> bool {
    if left.exact_relation_key.is_some() && left.exact_relation_key == right.exact_relation_key {
        return true;
    }
    !distinctive_details_conflict(
        &candidate_title_variants(left),
        &candidate_title_variants(right),
    )
}

/// Rebuild the representative fields that affect relation decisions after a
/// cluster merge. The representative identity/source remain stable while the
/// richest evidence owns title, summary, definitive URL key, and domain.
pub fn aggregate_relation_representative(
    representative: &NewsRelationDocument,
    members: &[NewsRelationDocument],
) -> NewsRelationDocument {
    let evidence = members
        .iter()
        .max_by(|left, right| {
            matching_text_len(left)
                .cmp(&matching_text_len(right))
                .then_with(|| compare_optional_time(left.ingested_at, right.ingested_at))
                .then_with(|| left.id.cmp(&right.id))
        })
        .unwrap_or(representative);
    let mut aggregate = representative.clone();
    aggregate.primary_title = evidence
        .primary_title
        .clone()
        .or_else(|| representative.primary_title.clone());
    aggregate.summary_text = evidence
        .summary_text
        .clone()
        .or_else(|| representative.summary_text.clone());
    aggregate.summary_key_points = if evidence.summary_key_points.is_empty() {
        representative.summary_key_points.clone()
    } else {
        evidence
            .summary_key_points
            .iter()
            .take(5)
            .cloned()
            .collect()
    };
    aggregate.article_domain = evidence
        .article_domain
        .clone()
        .or_else(|| representative.article_domain.clone());
    aggregate.exact_relation_key = evidence
        .exact_relation_key
        .clone()
        .or_else(|| representative.exact_relation_key.clone());

    let mut seen = HashSet::new();
    aggregate.related_titles = aggregate
        .primary_title
        .iter()
        .chain(members.iter().flat_map(|member| {
            member
                .primary_title
                .iter()
                .chain(member.related_titles.iter())
        }))
        .filter_map(|title| clean_optional(Some(title)))
        .filter(|title| seen.insert(title.to_lowercase()))
        .collect();
    aggregate
}

/// Find matching representatives with the exact same policy used by the Rust
/// worker. Repository lookback/visibility filtering happens before this call.
///
/// # Errors
///
/// Returns [`RelationMatchError`] for invalid thresholds, duplicate candidate
/// IDs, or a missing canonical embedding.
pub fn related_representatives(
    item: &NewsRelationDocument,
    candidates: &[NewsRelationDocument],
    thresholds: RelationThresholds,
    embeddings: &EmbeddingVectorStore,
) -> Result<RelationMatchResult, RelationMatchError> {
    let thresholds = thresholds.validate()?;
    validate_candidate_ids(candidates)?;

    let exact = item
        .exact_relation_key
        .as_ref()
        .map_or_else(Vec::new, |key| {
            candidates
                .iter()
                .filter(|candidate| candidate.exact_relation_key.as_ref() == Some(key))
                .map(|candidate| candidate.id)
                .collect::<Vec<_>>()
        });
    if !exact.is_empty() {
        return Ok(RelationMatchResult {
            item_id: item.id,
            item_title: clean_optional(item.primary_title.as_deref()),
            candidate_count: exact.len(),
            path: RelationMatchPath::Exact,
            decisions: Vec::new(),
            accepted_ids: exact,
        });
    }

    if candidates.is_empty() {
        return Ok(empty_result(item, 0, RelationMatchPath::NoCandidates));
    }

    let item_tokens = match_tokens(item.primary_title.as_deref().unwrap_or_default());
    let mut prefiltered = semantic_prefilter(item, candidates, &item_tokens);
    if prefiltered.is_empty() {
        return Ok(empty_result(
            item,
            candidates.len(),
            RelationMatchPath::PrefilterEmpty,
        ));
    }

    let item_views = ItemViews {
        title: title_matching_text(item),
        content: content_matching_text(item),
        provenance: provenance_matching_text(item),
        title_variants: clean_optional(item.primary_title.as_deref())
            .into_iter()
            .collect(),
        tokens: item_tokens,
    };
    let mut decisions = Vec::with_capacity(prefiltered.len());
    let mut accepted = Vec::new();

    for candidate in prefiltered.drain(..) {
        let decision = score_candidate(item, &item_views, &candidate, thresholds, embeddings)?;
        if matches!(
            decision.outcome,
            RelationDecisionOutcome::PrimaryAccepted | RelationDecisionOutcome::SecondaryAccepted
        ) {
            accepted.push((
                decision.combined_score,
                candidate.document.ingested_at,
                candidate.document.id,
            ));
        }
        decisions.push(decision);
    }

    accepted.sort_by(|left, right| {
        right
            .0
            .total_cmp(&left.0)
            .then_with(|| compare_optional_time(left.1, right.1))
            .then_with(|| left.2.cmp(&right.2))
    });

    Ok(RelationMatchResult {
        item_id: item.id,
        item_title: clean_optional(item.primary_title.as_deref()),
        candidate_count: candidates.len(),
        path: RelationMatchPath::Embedding,
        decisions,
        accepted_ids: accepted.into_iter().map(|entry| entry.2).collect(),
    })
}

#[derive(Debug)]
struct ItemViews {
    title: Option<String>,
    content: Option<String>,
    provenance: Option<String>,
    title_variants: Vec<String>,
    tokens: HashSet<String>,
}

fn score_candidate(
    item: &NewsRelationDocument,
    item_views: &ItemViews,
    candidate: &PrefilteredCandidate<'_>,
    thresholds: RelationThresholds,
    embeddings: &EmbeddingVectorStore,
) -> Result<RelationDecision, RelationMatchError> {
    let candidate_titles = candidate_title_variants(candidate.document);
    let title_score = match item_views.title.as_deref() {
        Some(left) => candidate_titles
            .iter()
            .map(|title| embeddings.cosine(left, &format!("Title: {title}")))
            .collect::<Result<Vec<_>, _>>()?
            .into_iter()
            .max_by(f64::total_cmp),
        None => None,
    };
    let content_score = view_score(
        item_views.content.as_deref(),
        content_matching_text(candidate.document).as_deref(),
        embeddings,
    )?;
    let provenance_score = view_score(
        item_views.provenance.as_deref(),
        provenance_matching_text(candidate.document).as_deref(),
        embeddings,
    )?;
    let combined_score = combine_scores(title_score, content_score, provenance_score);
    let outcome = relation_outcome(
        item,
        item_views,
        candidate,
        &candidate_titles,
        combined_score,
        thresholds,
    );
    Ok(RelationDecision {
        candidate_id: candidate.document.id,
        candidate_title: clean_optional(candidate.document.primary_title.as_deref()),
        title_score,
        content_score,
        provenance_score,
        combined_score,
        outcome,
    })
}

fn relation_outcome(
    item: &NewsRelationDocument,
    item_views: &ItemViews,
    candidate: &PrefilteredCandidate<'_>,
    candidate_titles: &[String],
    combined_score: f64,
    thresholds: RelationThresholds,
) -> RelationDecisionOutcome {
    if combined_score >= thresholds.primary {
        return RelationDecisionOutcome::PrimaryAccepted;
    }
    if combined_score < thresholds.secondary {
        return RelationDecisionOutcome::BelowSecondary;
    }
    if !relaxed_lexical_guard(
        item,
        candidate.document,
        &item_views.tokens,
        &candidate.tokens,
    ) {
        return RelationDecisionOutcome::SecondaryGuardRejected;
    }
    if distinctive_details_conflict(&item_views.title_variants, candidate_titles) {
        return RelationDecisionOutcome::SecondaryDetailVeto;
    }
    RelationDecisionOutcome::SecondaryAccepted
}

#[derive(Debug)]
struct PrefilteredCandidate<'a> {
    document: &'a NewsRelationDocument,
    tokens: HashSet<String>,
    overlap: usize,
    domain_match: bool,
    source_match: bool,
    original_index: usize,
}

fn semantic_prefilter<'a>(
    item: &NewsRelationDocument,
    candidates: &'a [NewsRelationDocument],
    item_tokens: &HashSet<String>,
) -> Vec<PrefilteredCandidate<'a>> {
    if item_tokens.is_empty() {
        return Vec::new();
    }
    let item_domain = normalized_optional(item.article_domain.as_deref());
    let item_source = normalized_optional(item.source_label.as_deref());
    let mut ranked = candidates
        .iter()
        .enumerate()
        .filter_map(|(original_index, document)| {
            let tokens = candidate_title_variants(document)
                .iter()
                .flat_map(|title| match_tokens(title))
                .collect::<HashSet<_>>();
            let overlap = item_tokens.intersection(&tokens).count();
            (overlap >= 1).then_some(PrefilteredCandidate {
                document,
                tokens,
                overlap,
                domain_match: item_domain.is_some()
                    && item_domain == normalized_optional(document.article_domain.as_deref()),
                source_match: item_source.is_some()
                    && item_source == normalized_optional(document.source_label.as_deref()),
                original_index,
            })
        })
        .collect::<Vec<_>>();
    ranked.sort_by(|left, right| {
        right
            .overlap
            .cmp(&left.overlap)
            .then_with(|| right.domain_match.cmp(&left.domain_match))
            .then_with(|| right.source_match.cmp(&left.source_match))
            .then_with(|| left.original_index.cmp(&right.original_index))
    });
    ranked.truncate(SEMANTIC_PREFILTER_MAX_CANDIDATES);
    ranked
}

fn validate_candidate_ids(candidates: &[NewsRelationDocument]) -> Result<(), RelationMatchError> {
    let mut ids = HashSet::new();
    for candidate in candidates {
        if !ids.insert(candidate.id) {
            return Err(RelationMatchError::DuplicateCandidate { id: candidate.id });
        }
    }
    Ok(())
}

fn empty_result(
    item: &NewsRelationDocument,
    candidate_count: usize,
    path: RelationMatchPath,
) -> RelationMatchResult {
    RelationMatchResult {
        item_id: item.id,
        item_title: clean_optional(item.primary_title.as_deref()),
        candidate_count,
        path,
        decisions: Vec::new(),
        accepted_ids: Vec::new(),
    }
}

fn document_embedding_texts(
    document: &NewsRelationDocument,
    include_candidate_title_variants: bool,
) -> Vec<String> {
    let mut texts = Vec::new();
    if include_candidate_title_variants {
        texts.extend(
            candidate_title_variants(document)
                .into_iter()
                .map(|title| format!("Title: {title}")),
        );
    } else if let Some(text) = title_matching_text(document) {
        texts.push(text);
    }
    if let Some(text) = content_matching_text(document) {
        texts.push(text);
    }
    if let Some(text) = provenance_matching_text(document) {
        texts.push(text);
    }
    texts
}

fn matching_text_len(document: &NewsRelationDocument) -> usize {
    [
        title_matching_text(document),
        provenance_matching_text(document),
        content_matching_text(document),
    ]
    .into_iter()
    .flatten()
    .map(|part| part.len())
    .sum()
}

fn title_matching_text(document: &NewsRelationDocument) -> Option<String> {
    clean_optional(document.primary_title.as_deref()).map(|title| format!("Title: {title}"))
}

fn candidate_title_variants(document: &NewsRelationDocument) -> Vec<String> {
    let mut seen = HashSet::new();
    let mut titles = Vec::new();
    for title in document
        .related_titles
        .iter()
        .filter_map(|title| clean_optional(Some(title)))
        .take(CLUSTER_RELATED_TITLE_LIMIT)
        .chain(clean_optional(document.primary_title.as_deref()))
    {
        if seen.insert(title.to_lowercase()) {
            titles.push(title);
        }
    }
    titles
}

fn content_matching_text(document: &NewsRelationDocument) -> Option<String> {
    let key_points = document
        .summary_key_points
        .iter()
        .take(5)
        .filter_map(|point| clean_optional(Some(point)))
        .collect::<Vec<_>>();
    let mut sections = Vec::new();
    if !key_points.is_empty() {
        sections.push(format!(
            "Key points:\n{}",
            key_points
                .iter()
                .map(|point| format!("- {point}"))
                .collect::<Vec<_>>()
                .join("\n")
        ));
    }
    if let Some(summary) = clean_optional(document.summary_text.as_deref()) {
        sections.push(format!("Summary: {summary}"));
    }
    (!sections.is_empty()).then(|| sections.join("\n"))
}

fn provenance_matching_text(document: &NewsRelationDocument) -> Option<String> {
    let parts = [
        ("Domain", document.article_domain.as_deref()),
        ("Source surface", document.source_label.as_deref()),
        ("Platform", document.platform.as_deref()),
    ]
    .into_iter()
    .filter_map(|(label, value)| clean_optional(value).map(|value| format!("{label}: {value}")))
    .collect::<Vec<_>>();
    (!parts.is_empty()).then(|| parts.join("\n"))
}

fn view_score(
    left: Option<&str>,
    right: Option<&str>,
    embeddings: &EmbeddingVectorStore,
) -> Result<Option<f64>, RelationMatchError> {
    match (left, right) {
        (Some(left), Some(right)) => embeddings.cosine(left, right).map(Some),
        _ => Ok(None),
    }
}

fn combine_scores(title: Option<f64>, content: Option<f64>, provenance: Option<f64>) -> f64 {
    let values = [
        (TITLE_WEIGHT, title),
        (CONTENT_WEIGHT, content),
        (PROVENANCE_WEIGHT, provenance),
    ];
    let (weighted_sum, active_weight) =
        values
            .into_iter()
            .fold((0.0, 0.0), |(sum, total), (weight, value)| match value {
                Some(value) => (sum + weight * value, total + weight),
                None => (sum, total),
            });
    if active_weight == 0.0 {
        -1.0
    } else {
        weighted_sum / active_weight
    }
}

fn relaxed_lexical_guard(
    left: &NewsRelationDocument,
    right: &NewsRelationDocument,
    left_tokens: &HashSet<String>,
    right_tokens: &HashSet<String>,
) -> bool {
    let same_domain = normalized_optional(left.article_domain.as_deref())
        .is_some_and(|value| Some(value) == normalized_optional(right.article_domain.as_deref()));
    let same_source = normalized_optional(left.source_label.as_deref())
        .is_some_and(|value| Some(value) == normalized_optional(right.source_label.as_deref()));
    same_domain || same_source || !left_tokens.is_disjoint(right_tokens)
}

fn match_tokens(text: &str) -> HashSet<String> {
    MATCH_TOKEN_PATTERN
        .find_iter(&text.to_lowercase())
        .filter_map(|matched| {
            let normalized = normalize_match_token(matched.as_str());
            (!normalized.is_empty() && !MATCH_STOPWORDS.contains(&normalized.as_str()))
                .then_some(normalized)
        })
        .collect()
}

fn normalize_match_token(token: &str) -> String {
    let mut normalized = token.to_lowercase();
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

fn distinctive_details_conflict(left_titles: &[String], right_titles: &[String]) -> bool {
    let (left_versions, left_models) = distinctive_detail_tokens(left_titles);
    let (right_versions, right_models) = distinctive_detail_tokens(right_titles);
    (!left_versions.is_empty()
        && !right_versions.is_empty()
        && left_versions.is_disjoint(&right_versions))
        || (!left_models.is_empty()
            && !right_models.is_empty()
            && left_models.is_disjoint(&right_models))
}

fn distinctive_detail_tokens(titles: &[String]) -> (HashSet<String>, HashSet<String>) {
    let mut versions = HashSet::new();
    let mut models = HashSet::new();
    for title in titles {
        let lowered = title.to_lowercase();
        versions.extend(
            VERSION_DETAIL_PATTERN
                .find_iter(&lowered)
                .map(|matched| matched.as_str().to_owned()),
        );
        for matched in MODEL_DETAIL_PATTERN.find_iter(&lowered) {
            let token = matched.as_str();
            let suffix = DIGIT_SUFFIX_PATTERN.find(token).map(|value| value.as_str());
            let numeric_prefix = suffix.map_or("", |suffix| &token[..token.len() - suffix.len()]);
            if suffix.is_some_and(|suffix| {
                !numeric_prefix.is_empty()
                    && numeric_prefix
                        .chars()
                        .all(|character| character.is_ascii_digit())
                    && NON_DISTINCTIVE_DIGIT_SUFFIXES.contains(&suffix)
            }) {
                continue;
            }
            models.insert(token.to_owned());
        }
    }
    (versions, models)
}

fn clean_optional(value: Option<&str>) -> Option<String> {
    value
        .map(str::split_whitespace)
        .map(|parts| parts.collect::<Vec<_>>().join(" "))
        .filter(|value| !value.is_empty())
}

fn normalized_optional(value: Option<&str>) -> Option<String> {
    clean_optional(value).map(|value| value.to_lowercase())
}

fn compare_optional_time(left: Option<DateTime<Utc>>, right: Option<DateTime<Utc>>) -> Ordering {
    match (left, right) {
        (Some(left), Some(right)) => left.cmp(&right),
        (None, Some(_)) => Ordering::Less,
        (Some(_), None) => Ordering::Greater,
        (None, None) => Ordering::Equal,
    }
}

fn is_sha256(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}
