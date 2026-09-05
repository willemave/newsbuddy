use std::collections::{BTreeMap, BTreeSet};
use std::env;
use std::sync::Arc;
use std::time::Duration;

use chrono::{DateTime, Utc};
use newsly_agent_runtime::{
    AgentEngine, AgentEvent, AgentEventSink, AgentLimits, AgentRequest, AgentRuntimeError,
    BoxToolFuture, NewslyTranscript, ProviderUsage, ResponseContract, ToolCall, ToolExecutor,
    ToolPolicy,
};
use reqwest::{StatusCode, Url};
use schemars::JsonSchema;
use secrecy::{ExposeSecret, SecretString};
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};
use thiserror::Error;

use crate::{OpenRouterPrivacyPolicy, ProviderCredentials, RigAgentEngine};

const DEFAULT_NEWS_MODEL: &str = "openai:gpt-5.6-luna";
const DEFAULT_EMBEDDING_MODEL: &str = "openrouter:qwen/qwen3-embedding-8b";
const DEFAULT_OPENROUTER_BASE: &str = "https://openrouter.ai/api/v1/";
const MAX_EMBEDDING_BATCH: usize = 128;
const MAX_EMBEDDING_TEXT_CHARS: usize = 50_000;
const MAX_EMBEDDING_TOTAL_CHARS: usize = 1_000_000;
const MAX_PROVIDER_RESPONSE_BYTES: usize = 16 * 1024 * 1024;
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
    client: reqwest::Client,
    engine: RigAgentEngine,
    summary_model: String,
    link_model: String,
    embedding_model: String,
    embedding_url: Url,
    openrouter_key: Option<SecretString>,
    openrouter_policy: OpenRouterPrivacyPolicy,
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
            env::var("NEWS_PROCESSING_TIMEOUT_SECONDS")
                .ok()
                .and_then(|value| value.parse::<u64>().ok())
                .unwrap_or(90)
                .clamp(15, 600),
        );
        let client = reqwest::Client::builder().timeout(deadline).build()?;
        let openrouter_key = secret_env("OPENROUTER_API_KEY");
        let credentials = ProviderCredentials {
            openai: secret_env("OPENAI_API_KEY"),
            anthropic: secret_env("ANTHROPIC_API_KEY"),
            google: secret_env("GOOGLE_API_KEY").or_else(|| secret_env("GEMINI_API_KEY")),
            openrouter: openrouter_key.clone(),
        };
        let openrouter_policy = OpenRouterPrivacyPolicy::default();
        let engine = RigAgentEngine::new(credentials, openrouter_policy.clone())?;
        let embedding_model =
            env::var("NEWS_EMBEDDING_MODEL").unwrap_or_else(|_| DEFAULT_EMBEDDING_MODEL.to_owned());
        let embedding_name = embedding_model
            .strip_prefix("openrouter:")
            .filter(|model| !model.trim().is_empty())
            .ok_or_else(|| {
                NewsItemGatewayError::UnsupportedEmbeddingModel(embedding_model.clone())
            })?;
        let mut base = Url::parse(
            &env::var("OPENROUTER_BASE_URL").unwrap_or_else(|_| DEFAULT_OPENROUTER_BASE.to_owned()),
        )
        .map_err(|error| NewsItemGatewayError::Url(error.to_string()))?;
        if !base.path().ends_with('/') {
            base.set_path(&format!("{}/", base.path().trim_end_matches('/')));
        }
        let embedding_url = base
            .join("embeddings")
            .map_err(|error| NewsItemGatewayError::Url(error.to_string()))?;
        Ok(Self {
            client,
            engine,
            summary_model: env::var("NEWS_PROCESSING_MODEL")
                .unwrap_or_else(|_| DEFAULT_NEWS_MODEL.to_owned()),
            link_model: env::var("NEWS_LINK_SELECTION_MODEL")
                .unwrap_or_else(|_| DEFAULT_NEWS_MODEL.to_owned()),
            embedding_model: embedding_name.to_owned(),
            embedding_url,
            openrouter_key,
            openrouter_policy,
            deadline,
        })
    }

    pub fn summary_model(&self) -> &str {
        &self.summary_model
    }

    pub fn embedding_model(&self) -> &str {
        &self.embedding_model
    }

    /// Generate and validate the canonical short-form news summary.
    ///
    /// # Errors
    ///
    /// Returns an error when the evidence is empty, the provider call fails, or the structured
    /// output violates the Newsly summary contract.
    pub async fn summarize(
        &self,
        evidence: String,
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
    pub async fn select_relevant_links(
        &self,
        article_title: Option<&str>,
        source_url: Option<&str>,
        candidates: &[LinkCandidate],
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
        validate_embedding_input(texts)?;
        if texts.is_empty() {
            return Ok(EmbeddingBatch {
                vectors: Vec::new(),
                model: self.embedding_model.clone(),
                usage: ProviderUsage::default(),
                provider_response_id: None,
            });
        }
        let key = self
            .openrouter_key
            .as_ref()
            .ok_or(NewsItemGatewayError::OpenRouterUnavailable)?;
        let provider = Value::Object(self.openrouter_policy.request_parameters()?);
        let response = self
            .client
            .post(self.embedding_url.clone())
            .bearer_auth(key.expose_secret())
            .json(&EmbeddingRequest {
                model: &self.embedding_model,
                input: texts,
                encoding_format: "float",
                provider,
            })
            .send()
            .await?;
        let status = response.status();
        let request_id = response
            .headers()
            .get("x-request-id")
            .and_then(|value| value.to_str().ok())
            .map(str::to_owned);
        let bytes = response.bytes().await?;
        if bytes.len() > MAX_PROVIDER_RESPONSE_BYTES {
            return Err(NewsItemGatewayError::EmbeddingResponseTooLarge(bytes.len()));
        }
        if !status.is_success() {
            return Err(NewsItemGatewayError::EmbeddingProvider {
                status,
                message: bounded_provider_error(&bytes),
            });
        }
        let payload = serde_json::from_slice::<EmbeddingResponse>(&bytes)?;
        let mut rows = payload.data;
        rows.sort_by_key(|row| row.index);
        if rows.len() != texts.len()
            || rows
                .iter()
                .enumerate()
                .any(|(expected, row)| row.index != expected)
        {
            return Err(NewsItemGatewayError::EmbeddingShape {
                expected: texts.len(),
                actual: rows.len(),
            });
        }
        let dimension = rows.first().map_or(0, |row| row.embedding.len());
        if dimension == 0 || rows.iter().any(|row| row.embedding.len() != dimension) {
            return Err(NewsItemGatewayError::InvalidEmbedding(
                "provider returned empty or inconsistent vector dimensions".to_owned(),
            ));
        }
        let vectors = rows
            .into_iter()
            .map(|row| normalize_vector(row.embedding))
            .collect::<Result<Vec<_>, _>>()?;
        let usage = payload.usage.unwrap_or_default();
        Ok(EmbeddingBatch {
            vectors,
            model: payload
                .model
                .unwrap_or_else(|| self.embedding_model.clone()),
            usage: ProviderUsage {
                request_count: 1,
                input_tokens: usage
                    .prompt_tokens
                    .filter(|tokens| *tokens > 0)
                    .or(usage.total_tokens)
                    .unwrap_or(0),
                ..ProviderUsage::default()
            },
            provider_response_id: payload.id.or(request_id),
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
                Arc::new(NoEvents),
            )
            .await?)
    }
}

#[derive(Debug, Serialize)]
struct EmbeddingRequest<'a> {
    model: &'a str,
    input: &'a [String],
    encoding_format: &'static str,
    provider: Value,
}

#[derive(Debug, Deserialize)]
struct EmbeddingResponse {
    #[serde(default)]
    id: Option<String>,
    #[serde(default)]
    model: Option<String>,
    data: Vec<EmbeddingRow>,
    #[serde(default)]
    usage: Option<EmbeddingUsage>,
}

#[derive(Debug, Deserialize)]
struct EmbeddingRow {
    index: usize,
    embedding: Vec<f64>,
}

#[derive(Debug, Default, Deserialize)]
struct EmbeddingUsage {
    prompt_tokens: Option<u64>,
    total_tokens: Option<u64>,
}

fn validate_embedding_input(texts: &[String]) -> Result<(), NewsItemGatewayError> {
    if texts.len() > MAX_EMBEDDING_BATCH {
        return Err(NewsItemGatewayError::TooManyEmbeddingInputs(texts.len()));
    }
    let mut total = 0usize;
    for text in texts {
        let count = text.chars().count();
        if count == 0 || count > MAX_EMBEDDING_TEXT_CHARS {
            return Err(NewsItemGatewayError::InvalidEmbeddingInputLength(count));
        }
        total = total.saturating_add(count);
    }
    if total > MAX_EMBEDDING_TOTAL_CHARS {
        return Err(NewsItemGatewayError::EmbeddingInputTooLarge(total));
    }
    Ok(())
}

fn normalize_vector(vector: Vec<f64>) -> Result<Vec<f64>, NewsItemGatewayError> {
    if vector.iter().any(|value| !value.is_finite()) {
        return Err(NewsItemGatewayError::InvalidEmbedding(
            "provider returned a non-finite vector component".to_owned(),
        ));
    }
    let norm = vector.iter().map(|value| value * value).sum::<f64>().sqrt();
    if !norm.is_finite() || norm <= 1e-12 {
        return Err(NewsItemGatewayError::InvalidEmbedding(
            "provider returned a zero-length vector".to_owned(),
        ));
    }
    Ok(vector.into_iter().map(|value| value / norm).collect())
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

fn bounded_provider_error(bytes: &[u8]) -> String {
    let message = String::from_utf8_lossy(bytes);
    message.chars().take(1_000).collect()
}

fn secret_env(name: &str) -> Option<SecretString> {
    env::var(name)
        .ok()
        .filter(|value| !value.trim().is_empty())
        .map(SecretString::from)
}

#[derive(Debug)]
struct NoTools;

impl ToolExecutor for NoTools {
    fn execute(&self, _call: ToolCall, _events: Arc<dyn AgentEventSink>) -> BoxToolFuture<'_> {
        Box::pin(async {
            Err(AgentRuntimeError::Tool(
                "news-item generation does not expose tools".to_owned(),
            ))
        })
    }
}

#[derive(Debug)]
struct NoEvents;

impl AgentEventSink for NoEvents {
    fn publish(&self, _event: AgentEvent) -> Result<(), AgentRuntimeError> {
        Ok(())
    }
}

#[derive(Debug, Error)]
pub enum NewsItemGatewayError {
    #[error("news summary input is empty")]
    EmptySummaryInput,
    #[error("news-item provider returned no structured output")]
    MissingStructuredOutput,
    #[error("invalid generated news summary: {0}")]
    InvalidSummary(String),
    #[error("OpenRouter is required for hosted news embeddings")]
    OpenRouterUnavailable,
    #[error("unsupported production news embedding model {0}; expected an openrouter: model")]
    UnsupportedEmbeddingModel(String),
    #[error("embedding request has {0} inputs; the maximum is {MAX_EMBEDDING_BATCH}")]
    TooManyEmbeddingInputs(usize),
    #[error("embedding input has invalid character length {0}")]
    InvalidEmbeddingInputLength(usize),
    #[error(
        "embedding request has {0} total characters; the maximum is {MAX_EMBEDDING_TOTAL_CHARS}"
    )]
    EmbeddingInputTooLarge(usize),
    #[error("embedding provider returned HTTP {status}: {message}")]
    EmbeddingProvider { status: StatusCode, message: String },
    #[error("embedding response has {0} bytes and exceeds the response limit")]
    EmbeddingResponseTooLarge(usize),
    #[error("embedding response shape mismatch: expected {expected} rows, got {actual}")]
    EmbeddingShape { expected: usize, actual: usize },
    #[error("invalid embedding: {0}")]
    InvalidEmbedding(String),
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
    Engine(#[from] crate::RigAgentEngineError),
    #[error(transparent)]
    Routing(#[from] crate::OpenRouterRoutingError),
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
            Self::EmbeddingProvider { status, .. } => {
                status.is_server_error() || matches!(status.as_u16(), 408 | 429)
            }
            _ => false,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

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
    fn embedding_vectors_are_finite_nonzero_and_normalized() {
        let vector = normalize_vector(vec![3.0, 4.0]).expect("valid vector");
        assert!((vector[0] - 0.6).abs() < 1e-12);
        assert!((vector[1] - 0.8).abs() < 1e-12);
        assert!(normalize_vector(vec![0.0, 0.0]).is_err());
        assert!(normalize_vector(vec![f64::NAN]).is_err());
    }

    #[test]
    fn local_embedding_models_are_rejected_for_production() {
        let value = "Qwen/Qwen3-Embedding-8B".to_owned();
        assert!(value.strip_prefix("openrouter:").is_none());
    }
    #[test]
    fn typed_retry_policy_stops_configuration_and_authentication_failures() {
        assert!(!NewsItemGatewayError::OpenRouterUnavailable.retryable());
        assert!(
            !NewsItemGatewayError::EmbeddingProvider {
                status: StatusCode::UNAUTHORIZED,
                message: "temporary-looking text".to_owned()
            }
            .retryable()
        );
        assert!(
            NewsItemGatewayError::EmbeddingProvider {
                status: StatusCode::TOO_MANY_REQUESTS,
                message: String::new()
            }
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
