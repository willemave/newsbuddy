use std::collections::BTreeSet;
use std::env;
use std::sync::Arc;
use std::time::Duration;

use newsly_agent_runtime::{
    AgentEngine, AgentEvent, AgentEventSink, AgentLimits, AgentOutcome, AgentRequest,
    AgentRuntimeError, BoxToolFuture, NewslyTranscript, ProviderUsage, ResponseContract, ToolCall,
    ToolExecutor, ToolPolicy,
};
use reqwest::{StatusCode, Url};
use schemars::JsonSchema;
use secrecy::{ExposeSecret, SecretString};
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};
use thiserror::Error;

use crate::{OpenRouterPrivacyPolicy, ProviderCredentials, RigAgentEngine};

const DEFAULT_BRIEFING_MODEL: &str = "openai:gpt-5.6-luna";
const DEFAULT_EMBEDDING_MODEL: &str = "openrouter:qwen/qwen3-embedding-8b";
const DEFAULT_OPENROUTER_BASE: &str = "https://openrouter.ai/api/v1/";
const MAX_EMBEDDING_BATCH: usize = 128;
const MAX_EMBEDDING_TEXT_CHARS: usize = 50_000;
const MAX_EMBEDDING_TOTAL_CHARS: usize = 1_000_000;
const MAX_PROVIDER_RESPONSE_BYTES: usize = 16 * 1024 * 1024;

const COMPOSITION_SYSTEM_PROMPT: &str = r"Compose one source-grounded personal-news Briefing window.

Return JSON matching the supplied schema. Passage markdown must cite sources using only these URI
forms: `[exact title](newsly://briefing/content/123)` and
`[exact title](newsly://briefing/news/456)`. Never invent a source, URL, title, publication, show,
fact, quotation, or attribution. Never use em dashes or generic summary-speak. Begin with the
strongest fact or idea rather than naming the lens or counting sources.

For `news`, return exactly one passage: one concise, information-dense paragraph of at most three
sentences, with no figures or pullquotes, linking every source exactly once. Synthesize related
sources into a unified account instead of giving each source its own sentence, and omit details
that do not materially improve the reader's understanding. Use the fewest sentences needed for a
clear account. For `audio` and `longform`, treat every source as a full work rather than a headline.
Give each source its own substantive treatment of 3-5 sentences, roughly 100-200 words, covering
its thesis, key points, concrete evidence or counterpoints, and why it matters to the reader. Use
the supplied `briefing_context` when present, including specific facts and attributable quotations
when the context supports them. Identify the exact title near the beginning and include the
supplied publication or show name when available. Suggest editorial pullquotes rather than
pretending they are source quotations. Add a figure for each deep source that has an image,
normally inset, with alternating alignment and at most one full figure. Cover every supplied source
at least once.";

const LENS_NAMING_SYSTEM_PROMPT: &str = r"Name one semantic cluster of unread Fast Reads for Newsly.

Return JSON matching the supplied schema. The key must be a stable URL-safe key beginning with
`news-`. The title must be a specific reader-facing title under 40 characters. The deck must be one
sentence explaining what connects the sources. Describe the shared theme across most sources, not
the most vivid single story. If the sources are only loosely related, choose a broad but honest
title. Avoid vague labels such as Updates, Briefs, News, or Misc unless the sources are genuinely
mixed. Do not name a lens after one source unless nearly every source is about that topic.";

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct BriefingCompositionSource {
    pub source_key: String,
    pub kind: String,
    pub id: i64,
    pub title: String,
    pub source_name: Option<String>,
    pub summary: Option<String>,
    pub key_points: Vec<String>,
    pub url: Option<String>,
    pub image_url: Option<String>,
    pub thumbnail_url: Option<String>,
    pub published_at: Option<String>,
    pub briefing_context: Option<String>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum BriefingPassageWeight {
    Feature,
    Brief,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum BriefingFigurePlacement {
    Full,
    Inset,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum BriefingFigureAlignment {
    Left,
    Right,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct BriefingSuggestedQuote {
    pub id: String,
    pub text: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(tag = "type", rename_all = "snake_case", deny_unknown_fields)]
pub enum BriefingCompositionBlock {
    Passage {
        markdown: String,
        weight: BriefingPassageWeight,
    },
    Figure {
        source_key: String,
        caption: String,
        placement: BriefingFigurePlacement,
        alignment: Option<BriefingFigureAlignment>,
    },
    Pullquote {
        suggestion_id: String,
    },
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct BriefingCompositionLayout {
    #[serde(default)]
    pub suggested_quotes: Vec<BriefingSuggestedQuote>,
    pub blocks: Vec<BriefingCompositionBlock>,
}

impl BriefingCompositionLayout {
    fn validate(self, tier: &str, sources: &[BriefingCompositionSource]) -> Result<Self, String> {
        if self.blocks.is_empty() {
            return Err("layout has no blocks".to_owned());
        }
        let suggestion_ids = self
            .suggested_quotes
            .iter()
            .map(|suggestion| suggestion.id.trim())
            .collect::<BTreeSet<_>>();
        if suggestion_ids.len() != self.suggested_quotes.len()
            || suggestion_ids
                .iter()
                .any(|id| id.is_empty() || id.len() > 80)
            || self.suggested_quotes.iter().any(|suggestion| {
                suggestion.text.trim().is_empty() || suggestion.text.chars().count() > 360
            })
        {
            return Err("suggested quotes must have unique bounded IDs and text".to_owned());
        }
        let mut selected_quotes = BTreeSet::new();
        let mut passages = Vec::new();
        for block in &self.blocks {
            match block {
                BriefingCompositionBlock::Passage { markdown, .. } => {
                    if markdown.trim().is_empty() {
                        return Err("passage markdown is empty".to_owned());
                    }
                    passages.push(markdown.as_str());
                }
                BriefingCompositionBlock::Figure {
                    source_key,
                    caption,
                    ..
                } => {
                    if caption.trim().is_empty()
                        || !sources.iter().any(|source| {
                            source.source_key == *source_key
                                && (source.image_url.is_some() || source.thumbnail_url.is_some())
                        })
                    {
                        return Err("figure does not reference an imaged source".to_owned());
                    }
                }
                BriefingCompositionBlock::Pullquote { suggestion_id } => {
                    if !suggestion_ids.contains(suggestion_id.trim())
                        || !selected_quotes.insert(suggestion_id.trim())
                    {
                        return Err(
                            "pullquote references an unknown or reused suggestion".to_owned()
                        );
                    }
                }
            }
        }
        let joined = passages.join("\n\n");
        let allowed_links = sources
            .iter()
            .map(|source| source_link(&source.kind, source.id))
            .collect::<BTreeSet<_>>();
        let source_links = source_links_in_markdown(&joined);
        if source_links
            .iter()
            .any(|link| !allowed_links.contains(link))
        {
            return Err("layout contains an unknown Briefing source link".to_owned());
        }
        for source in sources {
            let link = source_link(&source.kind, source.id);
            let count = source_links
                .iter()
                .filter(|found| found.as_str() == link)
                .count();
            if count == 0 || (tier == "news" && count != 1) {
                return Err(format!(
                    "source {} must be linked {}",
                    source.source_key,
                    if tier == "news" {
                        "exactly once"
                    } else {
                        "at least once"
                    }
                ));
            }
        }
        if tier == "news" {
            if self.blocks.len() != 1 || passages.len() != 1 {
                return Err("news layout must contain exactly one passage".to_owned());
            }
            if passages[0]
                .split("\n\n")
                .filter(|value| !value.trim().is_empty())
                .count()
                != 1
            {
                return Err("news layout must contain exactly one paragraph".to_owned());
            }
        }
        Ok(self)
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BriefingCompositionRequest {
    pub lens_title: String,
    pub tier: String,
    pub sources: Vec<BriefingCompositionSource>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct BriefingLensName {
    pub key: String,
    pub title: String,
    pub deck: String,
}

impl BriefingLensName {
    fn validate(self) -> Result<Self, String> {
        let key = self.key.trim();
        let title = self.title.split_whitespace().collect::<Vec<_>>().join(" ");
        let deck = self.deck.split_whitespace().collect::<Vec<_>>().join(" ");
        let valid_key = key.starts_with("news-")
            && (2..=64).contains(&key.len())
            && key
                .bytes()
                .all(|byte| byte.is_ascii_lowercase() || byte.is_ascii_digit() || byte == b'-');
        if !valid_key
            || !(2..=40).contains(&title.chars().count())
            || !(8..=180).contains(&deck.chars().count())
        {
            return Err("lens name has an invalid key, title, or deck".to_owned());
        }
        Ok(Self {
            key: key.to_owned(),
            title,
            deck,
        })
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct GeneratedBriefingLayout {
    pub layout: BriefingCompositionLayout,
    pub model: String,
    pub usage: ProviderUsage,
    pub provider_response_id: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct GeneratedBriefingLensName {
    pub name: BriefingLensName,
    pub model: String,
    pub usage: ProviderUsage,
    pub provider_response_id: Option<String>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct BriefingEmbeddingBatch {
    pub vectors: Vec<Vec<f64>>,
    pub model: String,
    pub usage: ProviderUsage,
    pub provider_response_id: Option<String>,
}

struct StructuredRunRequest<'a> {
    feature: &'a str,
    system_prompt: &'a str,
    user_prompt: String,
    schema_name: &'a str,
    schema: schemars::Schema,
    output_tokens: u64,
    validation_retries: u16,
}

#[derive(Debug, Clone)]
pub struct BriefingCompositionGateway {
    engine: RigAgentEngine,
    client: reqwest::Client,
    model_spec: String,
    embedding_model: String,
    embedding_url: Url,
    openrouter_key: Option<SecretString>,
    openrouter_policy: OpenRouterPrivacyPolicy,
    deadline: Duration,
}

impl BriefingCompositionGateway {
    /// Creates a Briefing composition gateway from the process environment.
    ///
    /// # Errors
    ///
    /// Returns an error when provider configuration, URLs, or HTTP/model clients are invalid.
    pub fn from_env() -> Result<Self, BriefingCompositionGatewayError> {
        let deadline = Duration::from_secs(
            env::var("BRIEFING_LLM_TIMEOUT_SECONDS")
                .ok()
                .and_then(|value| value.parse::<u64>().ok())
                .unwrap_or(300)
                .clamp(15, 600),
        );
        let embedding_timeout = Duration::from_secs(
            env::var("BRIEFING_CATEGORY_EMBEDDING_TIMEOUT_SECONDS")
                .ok()
                .and_then(|value| value.parse::<u64>().ok())
                .unwrap_or(30)
                .clamp(1, 120),
        );
        let client = reqwest::Client::builder()
            .timeout(embedding_timeout)
            .build()?;
        let openrouter_key = secret_env("OPENROUTER_API_KEY");
        let credentials = ProviderCredentials {
            openai: secret_env("OPENAI_API_KEY"),
            anthropic: secret_env("ANTHROPIC_API_KEY"),
            google: secret_env("GOOGLE_API_KEY").or_else(|| secret_env("GEMINI_API_KEY")),
            openrouter: openrouter_key.clone(),
        };
        let openrouter_policy = OpenRouterPrivacyPolicy::default();
        let engine = RigAgentEngine::new(credentials, openrouter_policy.clone())?;
        let embedding_spec = env::var("BRIEFING_CATEGORY_EMBEDDING_MODEL")
            .unwrap_or_else(|_| DEFAULT_EMBEDDING_MODEL.to_owned());
        let embedding_model = embedding_spec
            .strip_prefix("openrouter:")
            .filter(|model| !model.trim().is_empty())
            .ok_or_else(|| {
                BriefingCompositionGatewayError::UnsupportedEmbeddingModel(embedding_spec.clone())
            })?
            .to_owned();
        let mut base = Url::parse(
            &env::var("OPENROUTER_BASE_URL").unwrap_or_else(|_| DEFAULT_OPENROUTER_BASE.to_owned()),
        )
        .map_err(|error| BriefingCompositionGatewayError::Url(error.to_string()))?;
        if !base.path().ends_with('/') {
            base.set_path(&format!("{}/", base.path().trim_end_matches('/')));
        }
        let embedding_url = base
            .join("embeddings")
            .map_err(|error| BriefingCompositionGatewayError::Url(error.to_string()))?;
        Ok(Self {
            engine,
            client,
            model_spec: env::var("BRIEFING_MODEL")
                .unwrap_or_else(|_| DEFAULT_BRIEFING_MODEL.to_owned()),
            embedding_model,
            embedding_url,
            openrouter_key,
            openrouter_policy,
            deadline,
        })
    }

    pub fn model_spec(&self) -> &str {
        &self.model_spec
    }

    pub fn embedding_model(&self) -> &str {
        &self.embedding_model
    }

    /// Produces and validates a structured Briefing layout for the requested tier and sources.
    ///
    /// # Errors
    ///
    /// Returns an error for invalid inputs or when provider execution, decoding, or layout
    /// validation fails.
    pub async fn compose(
        &self,
        request: &BriefingCompositionRequest,
    ) -> Result<GeneratedBriefingLayout, BriefingCompositionGatewayError> {
        if request.sources.is_empty() {
            return Err(BriefingCompositionGatewayError::EmptySources);
        }
        if !matches!(request.tier.as_str(), "audio" | "longform" | "news") {
            return Err(BriefingCompositionGatewayError::UnsupportedTier(
                request.tier.clone(),
            ));
        }
        let user_prompt = format!(
            "Lens: {}\nTier: {}\n\nSources:\n{}\n\nCompose one complete Briefing window.",
            request.lens_title,
            request.tier,
            serde_json::to_string_pretty(&request.sources)?,
        );
        let outcome = self
            .run_structured(StructuredRunRequest {
                feature: "briefing_compose",
                system_prompt: COMPOSITION_SYSTEM_PROMPT,
                user_prompt,
                schema_name: "briefing_composer_layout_v1",
                schema: schemars::schema_for!(BriefingCompositionLayout),
                output_tokens: 3_200,
                validation_retries: 2,
            })
            .await?;
        let value = outcome
            .structured_output
            .ok_or(BriefingCompositionGatewayError::MissingStructuredOutput)?;
        let layout = serde_json::from_value::<BriefingCompositionLayout>(value)?
            .validate(&request.tier, &request.sources)
            .map_err(BriefingCompositionGatewayError::InvalidLayout)?;
        Ok(GeneratedBriefingLayout {
            layout,
            model: outcome.model_name,
            usage: outcome.usage,
            provider_response_id: outcome.provider_response_id,
        })
    }

    /// Generates and validates a concise lens name for a nonempty source collection.
    ///
    /// # Errors
    ///
    /// Returns an error for empty input or when provider execution, decoding, or name validation
    /// fails.
    pub async fn name_lens(
        &self,
        sources: &[BriefingCompositionSource],
    ) -> Result<GeneratedBriefingLensName, BriefingCompositionGatewayError> {
        if sources.is_empty() {
            return Err(BriefingCompositionGatewayError::EmptyLensSources);
        }
        let user_prompt = serde_json::to_string_pretty(sources)?;
        let outcome = self
            .run_structured(StructuredRunRequest {
                feature: "briefing_lens_naming",
                system_prompt: LENS_NAMING_SYSTEM_PROMPT,
                user_prompt,
                schema_name: "briefing_lens_name_v1",
                schema: schemars::schema_for!(BriefingLensName),
                output_tokens: 320,
                validation_retries: 1,
            })
            .await?;
        let value = outcome
            .structured_output
            .ok_or(BriefingCompositionGatewayError::MissingStructuredOutput)?;
        let name = serde_json::from_value::<BriefingLensName>(value)?
            .validate()
            .map_err(BriefingCompositionGatewayError::InvalidLensName)?;
        Ok(GeneratedBriefingLensName {
            name,
            model: outcome.model_name,
            usage: outcome.usage,
            provider_response_id: outcome.provider_response_id,
        })
    }

    /// Embeds and normalizes the supplied texts through the configured `OpenRouter` model.
    ///
    /// # Errors
    ///
    /// Returns an error when input validation, provider transport, response decoding, or vector
    /// normalization fails.
    pub async fn embed(
        &self,
        texts: &[String],
    ) -> Result<BriefingEmbeddingBatch, BriefingCompositionGatewayError> {
        validate_embedding_input(texts)?;
        if texts.is_empty() {
            return Ok(BriefingEmbeddingBatch {
                vectors: Vec::new(),
                model: self.embedding_model.clone(),
                usage: ProviderUsage::default(),
                provider_response_id: None,
            });
        }
        let key = self
            .openrouter_key
            .as_ref()
            .ok_or(BriefingCompositionGatewayError::OpenRouterUnavailable)?;
        let mut routing = self.openrouter_policy.request_parameters()?;
        let provider = routing
            .remove("provider")
            .unwrap_or_else(|| Value::Object(Map::new()));
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
            return Err(BriefingCompositionGatewayError::ResponseTooLarge(
                bytes.len(),
            ));
        }
        if !status.is_success() {
            return Err(BriefingCompositionGatewayError::EmbeddingProvider {
                status,
                message: String::from_utf8_lossy(&bytes)
                    .chars()
                    .take(1_000)
                    .collect(),
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
            return Err(BriefingCompositionGatewayError::EmbeddingShape {
                expected: texts.len(),
                actual: rows.len(),
            });
        }
        let vectors = rows
            .into_iter()
            .map(|row| normalize_vector(row.embedding))
            .collect::<Result<Vec<_>, _>>()?;
        let usage = payload.usage.unwrap_or_default();
        Ok(BriefingEmbeddingBatch {
            vectors,
            model: payload
                .model
                .unwrap_or_else(|| self.embedding_model.clone()),
            usage: ProviderUsage {
                request_count: 1,
                input_tokens: usage.prompt_tokens.or(usage.total_tokens).unwrap_or(0),
                ..ProviderUsage::default()
            },
            provider_response_id: payload.id.or(request_id),
        })
    }

    async fn run_structured(
        &self,
        request: StructuredRunRequest<'_>,
    ) -> Result<AgentOutcome, BriefingCompositionGatewayError> {
        let StructuredRunRequest {
            feature,
            system_prompt,
            user_prompt,
            schema_name,
            schema,
            output_tokens,
            validation_retries,
        } = request;
        Ok(self
            .engine
            .run(
                AgentRequest {
                    feature: feature.to_owned(),
                    model_spec: self.model_spec.clone(),
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

fn source_link(kind: &str, id: i64) -> String {
    format!("newsly://briefing/{kind}/{id}")
}

fn source_links_in_markdown(markdown: &str) -> Vec<String> {
    let prefix = "newsly://briefing/";
    let mut remaining = markdown;
    let mut links = Vec::new();
    while let Some(start) = remaining.find(prefix) {
        let candidate = &remaining[start..];
        let end = candidate
            .find(|character: char| {
                character.is_whitespace() || matches!(character, ')' | ']' | '}' | '>' | '"' | '\'')
            })
            .unwrap_or(candidate.len());
        links.push(candidate[..end].to_owned());
        remaining = &candidate[end..];
    }
    links
}

fn validate_embedding_input(texts: &[String]) -> Result<(), BriefingCompositionGatewayError> {
    if texts.len() > MAX_EMBEDDING_BATCH {
        return Err(BriefingCompositionGatewayError::TooManyEmbeddingInputs(
            texts.len(),
        ));
    }
    let mut total = 0_usize;
    for text in texts {
        let count = text.chars().count();
        if count == 0 || count > MAX_EMBEDDING_TEXT_CHARS {
            return Err(BriefingCompositionGatewayError::InvalidEmbeddingInputLength(count));
        }
        total = total.saturating_add(count);
    }
    if total > MAX_EMBEDDING_TOTAL_CHARS {
        return Err(BriefingCompositionGatewayError::EmbeddingInputTooLarge(
            total,
        ));
    }
    Ok(())
}

fn normalize_vector(vector: Vec<f64>) -> Result<Vec<f64>, BriefingCompositionGatewayError> {
    if vector.is_empty() || vector.iter().any(|value| !value.is_finite()) {
        return Err(BriefingCompositionGatewayError::InvalidEmbedding(
            "provider returned an empty or non-finite vector".to_owned(),
        ));
    }
    let norm = vector.iter().map(|value| value * value).sum::<f64>().sqrt();
    if !norm.is_finite() || norm <= 1e-12 {
        return Err(BriefingCompositionGatewayError::InvalidEmbedding(
            "provider returned a zero-length vector".to_owned(),
        ));
    }
    Ok(vector.into_iter().map(|value| value / norm).collect())
}

fn secret_env(name: &str) -> Option<SecretString> {
    env::var(name)
        .ok()
        .filter(|value| !value.trim().is_empty())
        .map(SecretString::from)
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
    id: Option<String>,
    model: Option<String>,
    data: Vec<EmbeddingRow>,
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

#[derive(Debug)]
struct NoTools;

impl ToolExecutor for NoTools {
    fn execute(&self, call: ToolCall, _events: Arc<dyn AgentEventSink>) -> BoxToolFuture<'_> {
        Box::pin(async move {
            Err(AgentRuntimeError::Tool(format!(
                "Briefing composition does not expose tool {}",
                call.name
            )))
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
pub enum BriefingCompositionGatewayError {
    #[error("Briefing composition requires at least one source")]
    EmptySources,
    #[error("Briefing lens naming requires at least one source")]
    EmptyLensSources,
    #[error("unsupported Briefing tier {0:?}")]
    UnsupportedTier(String),
    #[error("Briefing provider returned no structured output")]
    MissingStructuredOutput,
    #[error("invalid Briefing layout: {0}")]
    InvalidLayout(String),
    #[error("invalid Briefing lens name: {0}")]
    InvalidLensName(String),
    #[error("OpenRouter is required for Briefing event embeddings")]
    OpenRouterUnavailable,
    #[error("unsupported Briefing embedding model {0}; expected an openrouter: model")]
    UnsupportedEmbeddingModel(String),
    #[error("embedding request has {0} inputs; the maximum is {MAX_EMBEDDING_BATCH}")]
    TooManyEmbeddingInputs(usize),
    #[error("embedding input has invalid character length {0}")]
    InvalidEmbeddingInputLength(usize),
    #[error("embedding request has {0} total characters; limit is {MAX_EMBEDDING_TOTAL_CHARS}")]
    EmbeddingInputTooLarge(usize),
    #[error("Briefing embedding provider returned HTTP {status}: {message}")]
    EmbeddingProvider { status: StatusCode, message: String },
    #[error("Briefing provider response has {0} bytes and exceeds the response limit")]
    ResponseTooLarge(usize),
    #[error("embedding response shape mismatch: expected {expected} rows, got {actual}")]
    EmbeddingShape { expected: usize, actual: usize },
    #[error("invalid Briefing embedding: {0}")]
    InvalidEmbedding(String),
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

#[cfg(test)]
mod tests {
    use super::*;

    fn source(id: i64) -> BriefingCompositionSource {
        BriefingCompositionSource {
            source_key: format!("news:{id}"),
            kind: "news".to_owned(),
            id,
            title: format!("Source {id}"),
            source_name: None,
            summary: Some("Summary".to_owned()),
            key_points: vec!["Point".to_owned()],
            url: None,
            image_url: None,
            thumbnail_url: None,
            published_at: None,
            briefing_context: None,
        }
    }

    #[test]
    fn lens_name_requires_a_bounded_slug_and_copy() {
        let name = BriefingLensName {
            key: "news-public-infrastructure".to_owned(),
            title: "Public Infrastructure".to_owned(),
            deck: "Fast reads about the systems that make public life work.".to_owned(),
        };
        assert!(name.validate().is_ok());
        let invalid = BriefingLensName {
            key: "Public Infrastructure".to_owned(),
            title: "Public Infrastructure".to_owned(),
            deck: "Too short".to_owned(),
        };
        assert!(invalid.validate().is_err());
    }

    #[test]
    fn news_layout_requires_one_link_per_source() {
        let layout = BriefingCompositionLayout {
            suggested_quotes: Vec::new(),
            blocks: vec![BriefingCompositionBlock::Passage {
                markdown: "[First source](newsly://briefing/news/1) meets [second source](newsly://briefing/news/2).".to_owned(),
                weight: BriefingPassageWeight::Brief,
            }],
        };
        assert!(layout.validate("news", &[source(1), source(2)]).is_ok());
    }

    #[test]
    fn news_prompt_requests_concise_synthesis() {
        assert!(COMPOSITION_SYSTEM_PROMPT.contains("one concise, information-dense paragraph"));
        assert!(COMPOSITION_SYSTEM_PROMPT.contains("Use the fewest sentences needed"));
        assert!(
            COMPOSITION_SYSTEM_PROMPT.contains("instead of giving each source its own sentence")
        );
    }

    #[test]
    fn deep_prompt_requests_full_source_treatment() {
        assert!(COMPOSITION_SYSTEM_PROMPT.contains("treat every source as a full work"));
        assert!(COMPOSITION_SYSTEM_PROMPT.contains("3-5 sentences, roughly 100-200 words"));
        assert!(COMPOSITION_SYSTEM_PROMPT.contains("concrete evidence or counterpoints"));
        assert!(COMPOSITION_SYSTEM_PROMPT.contains("supplied `briefing_context`"));
    }

    #[test]
    fn news_layout_rejects_duplicate_source_link() {
        let layout = BriefingCompositionLayout {
            suggested_quotes: Vec::new(),
            blocks: vec![BriefingCompositionBlock::Passage {
                markdown:
                    "[First](newsly://briefing/news/1), then [again](newsly://briefing/news/1)."
                        .to_owned(),
                weight: BriefingPassageWeight::Brief,
            }],
        };
        assert!(layout.validate("news", &[source(1)]).is_err());
    }

    #[test]
    fn news_layout_counts_complete_source_uris() {
        let layout = BriefingCompositionLayout {
            suggested_quotes: Vec::new(),
            blocks: vec![BriefingCompositionBlock::Passage {
                markdown: "[One](newsly://briefing/news/1) and [ten](newsly://briefing/news/10)."
                    .to_owned(),
                weight: BriefingPassageWeight::Brief,
            }],
        };
        assert!(layout.validate("news", &[source(1), source(10)]).is_ok());
    }

    #[test]
    fn layout_rejects_unknown_source_links() {
        let layout = BriefingCompositionLayout {
            suggested_quotes: Vec::new(),
            blocks: vec![BriefingCompositionBlock::Passage {
                markdown:
                    "[Known](newsly://briefing/news/1) meets [invented](newsly://briefing/news/2)."
                        .to_owned(),
                weight: BriefingPassageWeight::Brief,
            }],
        };
        assert!(layout.validate("news", &[source(1)]).is_err());
    }

    #[test]
    fn embedding_vectors_are_normalized() {
        let vector = normalize_vector(vec![3.0, 4.0]).expect("valid vector");
        assert!((vector[0] - 0.6).abs() < 1e-12);
        assert!((vector[1] - 0.8).abs() < 1e-12);
    }
}
