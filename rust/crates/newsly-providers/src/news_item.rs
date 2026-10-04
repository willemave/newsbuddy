use std::collections::{BTreeMap, BTreeSet};
use std::env;
use std::sync::Arc;
use std::time::Duration;

use chrono::{DateTime, Utc};
use newsly_agent_runtime::{
    AgentEventSink, AgentLimits, AgentRequest, AgentRuntimeError, NewslyTranscript, ProviderUsage,
    ResponseContract, ToolPolicy,
};
use reqwest::Url;
use schemars::JsonSchema;
use secrecy::SecretString;
use serde::{Deserialize, Serialize};
use serde_json::Map;
use thiserror::Error;

use crate::openrouter_embeddings::{OpenRouterEmbeddingClient, OpenRouterEmbeddingError};
use crate::{ProviderCredentials, RigAgentEngine};

use crate::agent_support::NoTools;
use crate::provider_env::{ProviderEnvError, u64_from_env};

const DEFAULT_NEWS_MODEL: &str = "openai:gpt-6-luna";
const DEFAULT_EMBEDDING_MODEL: &str = "openrouter:qwen/qwen3-embedding-8b";
const DEFAULT_OPENROUTER_BASE: &str = "https://openrouter.ai/api/v1/";
const MAX_LINK_CANDIDATES: usize = 30;
const MAX_SELECTED_LINKS: usize = 6;

const NEWS_SYSTEM_PROMPT: &str = r"Create a compact short-form news summary grounded only in the supplied evidence.

Return a factual headline of 5-95 characters, two to four concrete key points of at most 220
characters each, and a two-to-three sentence overview of at most 500 characters. Preserve names,
numbers, dates, and uncertainty. Do not add facts that are absent from the evidence. Classify the
item as `to_read` when it contains substantive reader value and `skip` only when it is duplicative,
empty, promotional, or otherwise low-signal. Return only JSON matching the supplied schema.";

const LINKS_SYSTEM_PROMPT: &str = r"Select useful outbound links from an article.

Choose only from the supplied candidates. Prefer primary sources, papers, datasets,
documentation, tools, source repositories, company or product pages, and important related
context. Exclude navigation, homepages, share links, login or subscription pages, ads, generic
social links, and weak citations. Prefer fewer high-signal links. Never invent or alter a URL.
Return only JSON matching the supplied schema.";

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum NewsClassification {
    ToRead,
    Skip,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
struct NewsSummaryOutput {
    #[schemars(length(min = 5, max = 95))]
    title: String,
    #[schemars(length(max = 2_048))]
    article_url: Option<String>,
    #[schemars(length(min = 2, max = 4), transform = key_point_constraints)]
    key_points: Vec<String>,
    #[schemars(length(min = 1, max = 500))]
    summary: String,
    classification: NewsClassification,
}

fn key_point_constraints(schema: &mut schemars::Schema) {
    schema.insert(
        "items".to_owned(),
        serde_json::json!({"type": "string", "minLength": 1, "maxLength": 220}),
    );
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct NewsSummary {
    pub title: String,
    pub article_url: Option<String>,
    pub key_points: Vec<String>,
    pub summary: String,
    pub classification: NewsClassification,
    pub summarization_date: DateTime<Utc>,
}

impl NewsSummaryOutput {
    fn normalize(self) -> Result<NewsSummary, NewsItemGatewayError> {
        let title = compact(&self.title);
        if !(5..=95).contains(&title.chars().count()) {
            return Err(NewsItemGatewayError::InvalidSummary(
                "title must contain 5-95 characters".to_owned(),
            ));
        }
        let summary = compact(&self.summary);
        if summary.is_empty() || summary.chars().count() > 500 {
            return Err(NewsItemGatewayError::InvalidSummary(
                "summary must contain 1-500 characters".to_owned(),
            ));
        }
        if !(2..=4).contains(&self.key_points.len()) {
            return Err(NewsItemGatewayError::InvalidSummary(
                "summary must contain two to four key points".to_owned(),
            ));
        }
        let key_points = self
            .key_points
            .into_iter()
            .map(|point| compact(&point))
            .collect::<Vec<_>>();
        if key_points
            .iter()
            .any(|point| point.is_empty() || point.chars().count() > 220)
        {
            return Err(NewsItemGatewayError::InvalidSummary(
                "each key point must contain 1-220 characters".to_owned(),
            ));
        }
        let article_url = self
            .article_url
            .as_deref()
            .map(normalize_http_url)
            .transpose()?;
        Ok(NewsSummary {
            title,
            article_url,
            key_points,
            summary,
            classification: self.classification,
            summarization_date: Utc::now(),
        })
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct GeneratedNewsSummary {
    pub summary: NewsSummary,
    pub model: String,
    pub usage: ProviderUsage,
    pub provider_response_id: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct LinkCandidate {
    pub url: String,
    pub title: Option<String>,
    pub context: Option<String>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum RelevantLinkCategory {
    PrimarySource,
    Research,
    Documentation,
    Tool,
    Dataset,
    CompanyProduct,
    RelatedContext,
    Other,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct RelevantLink {
    #[schemars(length(min = 1, max = 2_048))]
    pub url: String,
    #[schemars(length(max = 300))]
    pub title: Option<String>,
    #[schemars(length(min = 5, max = 300))]
    pub reason: String,
    pub category: RelevantLinkCategory,
    #[schemars(range(min = 0.0, max = 1.0))]
    pub confidence: f64,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
struct RelevantLinksOutput {
    #[schemars(length(max = 6))]
    links: Vec<RelevantLink>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct SelectedRelevantLinks {
    pub links: Vec<RelevantLink>,
    pub model: String,
    pub usage: ProviderUsage,
    pub provider_response_id: Option<String>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct EmbeddingBatch {
    pub vectors: Vec<Vec<f64>>,
    pub model: String,
    pub usage: ProviderUsage,
    pub provider_response_id: Option<String>,
}

#[derive(Debug, Clone)]
pub struct NewsItemGateway {
    engine: RigAgentEngine,
    summary_model: String,
    link_model: String,
    embeddings: OpenRouterEmbeddingClient,
    deadline: Duration,
}

impl NewsItemGateway {
    /// Build the production gateway from provider credentials and model routing in the environment.
    ///
    /// # Errors
    ///
    /// Returns an error for invalid provider configuration, an unsupported embedding route, or an
    /// invalid HTTP client/base URL.
    pub fn from_env() -> Result<Self, NewsItemGatewayError> {
        let deadline = Duration::from_secs(
            u64_from_env(
                "NEWS_PROCESSING_TIMEOUT_SECONDS",
                90,
                "an unsigned integer number of seconds",
            )?
            .clamp(15, 600),
        );
        let client = reqwest::Client::builder().timeout(deadline).build()?;
        let openrouter_key = secret_env("OPENROUTER_API_KEY");
        let credentials = ProviderCredentials::from_env();
        let engine = RigAgentEngine::new(credentials);
        let embedding_model =
            env::var("NEWS_EMBEDDING_MODEL").unwrap_or_else(|_| DEFAULT_EMBEDDING_MODEL.to_owned());
        let embeddings = OpenRouterEmbeddingClient::new(
            client,
            &embedding_model,
            &env::var("OPENROUTER_BASE_URL").unwrap_or_else(|_| DEFAULT_OPENROUTER_BASE.to_owned()),
            openrouter_key,
        )?;
        Ok(Self {
            engine,
            summary_model: env::var("NEWS_PROCESSING_MODEL")
                .unwrap_or_else(|_| DEFAULT_NEWS_MODEL.to_owned()),
            link_model: env::var("NEWS_LINK_SELECTION_MODEL")
                .unwrap_or_else(|_| DEFAULT_NEWS_MODEL.to_owned()),
            embeddings,
            deadline,
        })
    }

    pub fn summary_model(&self) -> &str {
        &self.summary_model
    }

    pub fn embedding_model(&self) -> &str {
        self.embeddings.model()
    }

    /// Generate and validate the canonical short-form news summary.
    ///
    /// # Errors
    ///
    /// Returns an error when the evidence is empty, the provider call fails, or the structured
    /// output violates the Newsly summary contract.
    /// Runs summary generation while publishing per-response model observations.
    ///
    /// # Errors
    ///
    /// Returns input, provider, and validation errors from summary generation.
    pub async fn summarize_with_events(
        &self,
        evidence: String,
        events: Arc<dyn AgentEventSink>,
    ) -> Result<GeneratedNewsSummary, NewsItemGatewayError> {
        if evidence.trim().is_empty() {
            return Err(NewsItemGatewayError::EmptySummaryInput);
        }
        let outcome = self
            .run_structured(
                "news_processing",
                &self.summary_model,
                NEWS_SYSTEM_PROMPT,
                evidence,
                "generated_news_summary_v1",
                schemars::schema_for!(NewsSummaryOutput),
                1_200,
                2,
                events,
            )
            .await?;
        let value = outcome
            .structured_output
            .ok_or(NewsItemGatewayError::MissingStructuredOutput)?;
        let summary = serde_json::from_value::<NewsSummaryOutput>(value)?.normalize()?;
        Ok(GeneratedNewsSummary {
            summary,
            model: outcome.model_name,
            usage: outcome.usage,
            provider_response_id: outcome.provider_response_id,
        })
    }

    /// Select only high-signal links from the supplied, already-bounded candidate set.
    ///
    /// # Errors
    ///
    /// Returns an error for invalid candidates, provider failures, or an invented/malformed link
    /// in the structured response.
    /// Runs relevant-link selection while publishing per-response model observations.
    ///
    /// # Errors
    ///
    /// Returns candidate, provider, and validation errors from link selection.
    pub async fn select_relevant_links_with_events(
        &self,
        article_title: Option<&str>,
        source_url: Option<&str>,
        candidates: &[LinkCandidate],
        events: Arc<dyn AgentEventSink>,
    ) -> Result<SelectedRelevantLinks, NewsItemGatewayError> {
        if candidates.is_empty() {
            return Ok(SelectedRelevantLinks {
                links: Vec::new(),
                model: self.link_model.clone(),
                usage: ProviderUsage::default(),
                provider_response_id: None,
            });
        }
        if candidates.len() > MAX_LINK_CANDIDATES {
            return Err(NewsItemGatewayError::TooManyLinkCandidates(
                candidates.len(),
            ));
        }
        let candidates = normalize_candidates(candidates, source_url)?;
        if candidates.is_empty() {
            return Ok(SelectedRelevantLinks {
                links: Vec::new(),
                model: self.link_model.clone(),
                usage: ProviderUsage::default(),
                provider_response_id: None,
            });
        }
        let prompt = format!(
            "Article title: {}\nArticle URL: {}\n\nCandidate outbound links:\n{}\n\nSelect up to {MAX_SELECTED_LINKS} links. Use concise titles and reasons.",
            article_title
                .map(compact)
                .filter(|value| !value.is_empty())
                .as_deref()
                .unwrap_or("Untitled"),
            source_url.unwrap_or("unknown"),
            serde_json::to_string_pretty(&candidates)?,
        );
        let outcome = self
            .run_structured(
                "news_relevant_links",
                &self.link_model,
                LINKS_SYSTEM_PROMPT,
                prompt,
                "interesting_external_links_v1",
                schemars::schema_for!(RelevantLinksOutput),
                1_200,
                1,
                events,
            )
            .await?;
        let value = outcome
            .structured_output
            .ok_or(NewsItemGatewayError::MissingStructuredOutput)?;
        let output = serde_json::from_value::<RelevantLinksOutput>(value)?;
        let links = validate_link_selection(output.links, &candidates)?;
        Ok(SelectedRelevantLinks {
            links,
            model: outcome.model_name,
            usage: outcome.usage,
            provider_response_id: outcome.provider_response_id,
        })
    }

    /// Encode canonical relation-policy texts through the hosted `OpenRouter` embedding endpoint.
    ///
    /// # Errors
    ///
    /// Returns an error for invalid or oversized input, unavailable credentials, transport or
    /// provider failures, and malformed embedding shapes or vectors.
    pub async fn embed(&self, texts: &[String]) -> Result<EmbeddingBatch, NewsItemGatewayError> {
        let batch = self.embeddings.embed(texts).await?;
        Ok(EmbeddingBatch {
            vectors: batch.vectors,
            model: batch.model,
            usage: batch.usage,
            provider_response_id: batch.provider_response_id,
        })
    }

    #[allow(clippy::too_many_arguments)]
    async fn run_structured(
        &self,
        feature: &str,
        model: &str,
        system_prompt: &str,
        user_prompt: String,
        schema_name: &str,
        schema: schemars::Schema,
        output_tokens: u64,
        validation_retries: u16,
        events: Arc<dyn AgentEventSink>,
    ) -> Result<newsly_agent_runtime::AgentOutcome, NewsItemGatewayError> {
        Ok(self
            .engine
            .run(
                AgentRequest {
                    feature: feature.to_owned(),
                    model_spec: model.to_owned(),
                    system_prompt: system_prompt.to_owned(),
                    user_prompt,
                    transcript: NewslyTranscript::default(),
                    response_contract: ResponseContract::JsonSchema {
                        name: schema_name.to_owned(),
                        schema,
                        strict: true,
                        validation_retries,
                    },
                    tools: Vec::new(),
                    tool_policy: ToolPolicy {
                        allowed: BTreeSet::new(),
                        require_tool: false,
                        allow_parallel_calls: false,
                    },
                    limits: AgentLimits {
                        request_limit: Some(u32::from(validation_retries).saturating_add(1)),
                        tool_call_limit: 0,
                        output_token_limit: Some(output_tokens),
                        deadline: self.deadline,
                    },
                    provider_parameters: Map::new(),
                },
                Arc::new(NoTools),
                events,
            )
            .await?)
    }
}

fn normalize_candidates(
    candidates: &[LinkCandidate],
    source_url: Option<&str>,
) -> Result<Vec<LinkCandidate>, NewsItemGatewayError> {
    let source_host = source_url.and_then(http_host);
    let mut seen = BTreeSet::new();
    let mut output = Vec::new();
    for candidate in candidates {
        let url = normalize_http_url(&candidate.url)?;
        let host = http_host(&url).ok_or_else(|| NewsItemGatewayError::InvalidUrl(url.clone()))?;
        if source_host
            .as_ref()
            .is_some_and(|source| same_site(&host, source))
            || !seen.insert(url.clone())
        {
            continue;
        }
        output.push(LinkCandidate {
            url,
            title: candidate
                .title
                .as_deref()
                .map(compact)
                .filter(|value| !value.is_empty()),
            context: candidate
                .context
                .as_deref()
                .map(compact)
                .filter(|value| !value.is_empty())
                .map(|value| value.chars().take(240).collect()),
        });
    }
    Ok(output)
}

fn validate_link_selection(
    links: Vec<RelevantLink>,
    candidates: &[LinkCandidate],
) -> Result<Vec<RelevantLink>, NewsItemGatewayError> {
    if links.len() > MAX_SELECTED_LINKS {
        return Err(NewsItemGatewayError::TooManySelectedLinks(links.len()));
    }
    let candidates = candidates
        .iter()
        .map(|candidate| (candidate.url.as_str(), candidate))
        .collect::<BTreeMap<_, _>>();
    let mut seen = BTreeSet::new();
    let mut selected = Vec::new();
    for mut link in links {
        let url = normalize_http_url(&link.url)?;
        let Some(candidate) = candidates.get(url.as_str()) else {
            return Err(NewsItemGatewayError::InventedRelevantLink(url));
        };
        if !seen.insert(url.clone()) {
            continue;
        }
        link.url.clone_from(&url);
        link.title = link
            .title
            .as_deref()
            .map(compact)
            .filter(|value| !value.is_empty())
            .or_else(|| candidate.title.clone())
            .or_else(|| http_host(&link.url));
        link.reason = compact(&link.reason);
        if !(5..=300).contains(&link.reason.chars().count())
            || !(0.0..=1.0).contains(&link.confidence)
        {
            return Err(NewsItemGatewayError::InvalidRelevantLink(url));
        }
        selected.push(link);
    }
    Ok(selected)
}

fn normalize_http_url(value: &str) -> Result<String, NewsItemGatewayError> {
    let mut url =
        Url::parse(value.trim()).map_err(|_| NewsItemGatewayError::InvalidUrl(value.to_owned()))?;
    if !matches!(url.scheme(), "http" | "https") || url.host_str().is_none() {
        return Err(NewsItemGatewayError::InvalidUrl(value.to_owned()));
    }
    url.set_scheme("https")
        .map_err(|()| NewsItemGatewayError::InvalidUrl(value.to_owned()))?;
    url.set_fragment(None);
    Ok(url.to_string())
}

fn http_host(value: &str) -> Option<String> {
    Url::parse(value)
        .ok()?
        .host_str()
        .map(|host| host.trim_start_matches("www.").to_ascii_lowercase())
}

fn same_site(left: &str, right: &str) -> bool {
    left == right || left.ends_with(&format!(".{right}")) || right.ends_with(&format!(".{left}"))
}

fn compact(value: &str) -> String {
    value.split_whitespace().collect::<Vec<_>>().join(" ")
}

fn secret_env(name: &str) -> Option<SecretString> {
    env::var(name)
        .ok()
        .filter(|value| !value.trim().is_empty())
        .map(SecretString::from)
}

#[derive(Debug, Error)]
pub enum NewsItemGatewayError {
    #[error(transparent)]
    Config(#[from] ProviderEnvError),
    #[error("news summary input is empty")]
    EmptySummaryInput,
    #[error("news-item provider returned no structured output")]
    MissingStructuredOutput,
    #[error("invalid generated news summary: {0}")]
    InvalidSummary(String),
    #[error(transparent)]
    Embedding(#[from] OpenRouterEmbeddingError),
    #[error("link selection has {0} candidates; the maximum is {MAX_LINK_CANDIDATES}")]
    TooManyLinkCandidates(usize),
    #[error("link selection returned {0} links; the maximum is {MAX_SELECTED_LINKS}")]
    TooManySelectedLinks(usize),
    #[error("link selector invented URL {0}")]
    InventedRelevantLink(String),
    #[error("invalid relevant link {0}")]
    InvalidRelevantLink(String),
    #[error("invalid HTTP URL {0}")]
    InvalidUrl(String),
    #[error(transparent)]
    Agent(#[from] AgentRuntimeError),
    #[error(transparent)]
    Http(#[from] reqwest::Error),
    #[error("invalid provider URL: {0}")]
    Url(String),
    #[error(transparent)]
    Json(#[from] serde_json::Error),
}

impl NewsItemGatewayError {
    #[must_use]
    pub fn retryable(&self) -> bool {
        match self {
            Self::Agent(AgentRuntimeError::DeadlineExceeded | AgentRuntimeError::Provider(_)) => {
                true
            }
            Self::Http(error) => crate::public_http::retryable_http_error(error),
            Self::Embedding(error) => error.retryable(),
            _ => false,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use reqwest::StatusCode;
    use serde_json::Value;

    #[test]
    fn generated_summary_enforces_persisted_wire_constraints() {
        let summary = NewsSummaryOutput {
            title: " A factual headline about Newsly ".to_owned(),
            article_url: Some("http://example.com/story#part".to_owned()),
            key_points: vec![" First point. ".to_owned(), "Second point.".to_owned()],
            summary: " A compact factual overview. ".to_owned(),
            classification: NewsClassification::ToRead,
        }
        .normalize()
        .expect("valid summary");
        assert_eq!(summary.title, "A factual headline about Newsly");
        assert_eq!(
            summary.article_url.as_deref(),
            Some("https://example.com/story")
        );
        assert_eq!(summary.key_points, ["First point.", "Second point."]);
    }

    #[test]
    fn key_point_schema_enforces_character_limits_on_each_item() {
        let schema = Value::from(schemars::schema_for!(NewsSummaryOutput));
        let validator = jsonschema::validator_for(&schema).unwrap();
        for (length, accepted) in [(0, false), (1, true), (220, true), (221, false)] {
            let value = serde_json::json!({
                "title": "A factual headline",
                "article_url": null,
                "key_points": ["界".repeat(length), "Another point"],
                "summary": "A factual overview.",
                "classification": "to_read"
            });
            assert_eq!(validator.is_valid(&value), accepted, "length {length}");
            assert_eq!(
                serde_json::from_value::<NewsSummaryOutput>(value)
                    .unwrap()
                    .normalize()
                    .is_ok(),
                accepted
            );
        }
    }

    #[test]
    fn relevant_links_must_be_selected_from_candidates() {
        let candidates = vec![LinkCandidate {
            url: "https://example.org/paper".to_owned(),
            title: Some("Paper".to_owned()),
            context: None,
        }];
        let error = validate_link_selection(
            vec![RelevantLink {
                url: "https://invented.example/result".to_owned(),
                title: None,
                reason: "Useful primary evidence".to_owned(),
                category: RelevantLinkCategory::PrimarySource,
                confidence: 0.9,
            }],
            &candidates,
        )
        .expect_err("invented URL must fail closed");
        assert!(matches!(
            error,
            NewsItemGatewayError::InventedRelevantLink(_)
        ));
    }

    #[test]
    fn local_embedding_models_are_rejected_for_production() {
        let value = "Qwen/Qwen3-Embedding-8B".to_owned();
        assert!(value.strip_prefix("openrouter:").is_none());
    }
    #[test]
    fn typed_retry_policy_stops_configuration_and_authentication_failures() {
        assert!(
            !NewsItemGatewayError::Embedding(OpenRouterEmbeddingError::Unavailable).retryable()
        );
        assert!(
            !NewsItemGatewayError::Embedding(OpenRouterEmbeddingError::Provider {
                status: StatusCode::UNAUTHORIZED,
                message: "temporary-looking text".to_owned()
            })
            .retryable()
        );
        assert!(
            NewsItemGatewayError::Embedding(OpenRouterEmbeddingError::Provider {
                status: StatusCode::TOO_MANY_REQUESTS,
                message: String::new()
            })
            .retryable()
        );
        assert!(
            !crate::SummarizationGatewayError::InvalidArtifact(
                "timeout text in invalid output".to_owned()
            )
            .retryable()
        );
        assert!(
            crate::SummarizationGatewayError::Agent(AgentRuntimeError::DeadlineExceeded)
                .retryable()
        );
    }
}
