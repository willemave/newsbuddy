mod naming;
pub use naming::{
    BRIEFING_LENS_NAMING_MAX_CATEGORIES, BRIEFING_LENS_NAMING_MAX_INPUT_BYTES,
    BRIEFING_LENS_NAMING_MAX_OUTPUT_TOKENS, BRIEFING_LENS_NAMING_MAX_STORIES_PER_CATEGORY,
    BriefingLensNamingBatch, BriefingLensNamingBatchRequest, BriefingLensNamingCategory,
    BriefingLensNamingResult, BriefingLensNamingStory, GeneratedBriefingLensNamingBatch,
};

use std::collections::BTreeSet;
use std::env;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use newsly_agent_runtime::{
    AgentEvent, AgentEventSink, AgentLimits, AgentOutcome, AgentRequest, AgentRuntimeError,
    NewslyTranscript, ProviderUsage, ResponseContract, ToolPolicy,
};
use schemars::JsonSchema;
use secrecy::SecretString;
use serde::{Deserialize, Serialize};
use serde_json::Map;
use thiserror::Error;

use crate::openrouter_embeddings::{OpenRouterEmbeddingClient, OpenRouterEmbeddingError};
use crate::{ProviderCredentials, RigAgentEngine};

use crate::agent_support::NoTools;
use crate::provider_env::{ProviderEnvError, u64_from_env};

const DEFAULT_BRIEFING_MODEL: &str = "openai:gpt-6-luna";
use newsly_domain::DEFAULT_LENS_EMBEDDING_MODEL as DEFAULT_EMBEDDING_MODEL;
const DEFAULT_OPENROUTER_BASE: &str = "https://openrouter.ai/api/v1/";

const COMPOSITION_SYSTEM_PROMPT: &str = r"Compose one source-grounded personal-news Briefing window.

Return JSON matching the supplied schema. Passage markdown must cite sources using only these URI
forms: `[source title or descriptive phrase](newsly://briefing/content/123)` and
`[source title or descriptive phrase](newsly://briefing/news/456)`. Never invent a source, URL, title, publication, show,
fact, quotation, or attribution. Never use em dashes or generic summary-speak. Begin with the
strongest fact or idea rather than naming the lens or counting sources.

For `news`, write like a newspaper brief: concise, factual, and information dense. For one distinct
event, never exceed 40 words for the entire passage, including linked text. Select at most two useful
supporting facts or qualifications beyond the linked headline; never substitute a feature or
capability list. For a roundup of multiple distinct events, target 45-65 words and never exceed 75.
Never add detail merely to reach a target.
Give each distinct event a complete, informative clause; preserve concrete context or a material
qualification only when it improves understanding. Cut generic background, feature inventories,
and thematic transitions before material facts. Return exactly one passage: one compact paragraph
of at most four sentences, with no figures or
pullquotes, linking every source exactly once.
Place links toward the beginning of the sentence that covers each source. Make each source link
span a substantial phrase: the title plus its surrounding descriptive words, roughly four to ten
words, never a bare two-word name. Let the linked phrase carry the fact instead of repeating it in
surrounding prose. Use a descriptive noun phrase for each link; do not put a finite verb inside the
link or follow it with a duplicated verb or restatement. Begin directly with the strongest fact or
story and vary sentence openings.
Use a compact, informational register and simple connective prose rather than lists. Connect
stories only when the connection adds useful information; omit abstract thematic introductions
and conclusions. For `audio` and `longform`, treat every source as a full work rather than a headline.
Give each source its own substantive treatment of 3-5 sentences, roughly 100-200 words, covering
its thesis, key points, concrete evidence or counterpoints, and why it matters to the reader. Use
the supplied `briefing_context` when present, including specific facts and attributable quotations
when the context supports them. The app shows each deep source's title and publication or show
name as a headline above its passage, so never restate the title, publication, or show name in
the prose. Open directly with the work's central argument or finding, and link the source on a
descriptive phrase about that claim or subject rather than its title. Suggest editorial pullquotes rather than
pretending they are source quotations. Add a figure for each deep source that has an image,
normally inset, with alternating alignment and at most one full figure. Cover every supplied source
at least once.";

const LENS_NAMING_SYSTEM_PROMPT: &str = r"Name one semantic cluster of unread Fast Reads for Newsly.

Return JSON matching the supplied schema. The key must begin with `news-`, contain only lowercase
ASCII letters, digits, and hyphens, and be at most 64 characters. The title must be a specific
reader-facing title of 2-40 characters. The deck must be one sentence of 8-180 characters explaining
what connects the sources. Describe the shared theme across most sources, not
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
        if !valid_key {
            return Err("lens key must use news- followed by lowercase letters, digits, or hyphens, at most 64 characters".to_owned());
        }
        if !(2..=40).contains(&title.chars().count()) {
            return Err("lens title must contain 2-40 characters".to_owned());
        }
        if !(8..=180).contains(&deck.chars().count()) {
            return Err("lens deck must contain 8-180 characters".to_owned());
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
    pub layout: Result<BriefingCompositionLayout, String>,
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
    model_spec: String,
    naming_model_spec: String,
    embeddings: OpenRouterEmbeddingClient,
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
            u64_from_env(
                "BRIEFING_LLM_TIMEOUT_SECONDS",
                300,
                "an unsigned integer number of seconds",
            )?
            .clamp(15, 600),
        );
        let embedding_timeout = Duration::from_secs(
            u64_from_env(
                "BRIEFING_CATEGORY_EMBEDDING_TIMEOUT_SECONDS",
                30,
                "an unsigned integer number of seconds",
            )?
            .clamp(1, 120),
        );
        let client = reqwest::Client::builder()
            .timeout(embedding_timeout)
            .build()?;
        let openrouter_key = secret_env("OPENROUTER_API_KEY");
        let credentials = ProviderCredentials::from_env();
        let engine = RigAgentEngine::new(credentials);
        let embedding_spec = env::var("BRIEFING_CATEGORY_EMBEDDING_MODEL")
            .unwrap_or_else(|_| DEFAULT_EMBEDDING_MODEL.to_owned());
        let embeddings = OpenRouterEmbeddingClient::new(
            client,
            &embedding_spec,
            &env::var("OPENROUTER_BASE_URL").unwrap_or_else(|_| DEFAULT_OPENROUTER_BASE.to_owned()),
            openrouter_key,
        )?;
        Ok(Self {
            engine,
            model_spec: env::var("BRIEFING_MODEL")
                .unwrap_or_else(|_| DEFAULT_BRIEFING_MODEL.to_owned()),
            naming_model_spec: env::var("BRIEFING_CATEGORY_NAMING_MODEL")
                .unwrap_or_else(|_| DEFAULT_BRIEFING_MODEL.to_owned()),
            embeddings,
            deadline,
        })
    }

    pub fn model_spec(&self) -> &str {
        &self.model_spec
    }

    pub fn naming_model_spec(&self) -> &str {
        &self.naming_model_spec
    }

    pub fn embedding_model(&self) -> &str {
        self.embeddings.model()
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
        feedback: Option<&str>,
    ) -> Result<GeneratedBriefingLayout, BriefingCompositionGatewayError> {
        if request.sources.is_empty() {
            return Err(BriefingCompositionGatewayError::EmptySources);
        }
        if !matches!(request.tier.as_str(), "audio" | "longform" | "news") {
            return Err(BriefingCompositionGatewayError::UnsupportedTier(
                request.tier.clone(),
            ));
        }
        let mut user_prompt = format!(
            "Lens: {}\nTier: {}\n\nSources:\n{}\n\nCompose one complete Briefing window.",
            request.lens_title,
            request.tier,
            serde_json::to_string_pretty(&request.sources)?,
        );
        if let Some(feedback) = feedback {
            user_prompt.push_str(
                "\nCorrect this validation failure in the complete replacement layout:\n",
            );
            user_prompt.push_str(feedback);
        }
        let outcome = self
            .run_structured(StructuredRunRequest {
                feature: "briefing_compose",
                system_prompt: COMPOSITION_SYSTEM_PROMPT,
                user_prompt,
                schema_name: "briefing_composer_layout_v1",
                schema: schemars::schema_for!(BriefingCompositionLayout),
                output_tokens: 3_200,
                validation_retries: 0,
            })
            .await?;
        let layout = outcome
            .structured_output
            .ok_or_else(|| "provider returned no structured output".to_owned())
            .and_then(|value| {
                serde_json::from_value::<BriefingCompositionLayout>(value)
                    .map_err(|error| error.to_string())
            })
            .and_then(|layout| layout.validate(&request.tier, &request.sources));
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
            .run_structured_with_model(
                StructuredRunRequest {
                    feature: "briefing_lens_naming",
                    system_prompt: LENS_NAMING_SYSTEM_PROMPT,
                    user_prompt,
                    schema_name: "briefing_lens_name_v1",
                    schema: schemars::schema_for!(BriefingLensName),
                    output_tokens: 320,
                    validation_retries: 1,
                },
                &self.naming_model_spec,
                None,
            )
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
        let batch = self.embeddings.embed(texts).await?;
        Ok(BriefingEmbeddingBatch {
            vectors: batch.vectors,
            model: batch.model,
            usage: batch.usage,
            provider_response_id: batch.provider_response_id,
        })
    }

    async fn run_structured(
        &self,
        request: StructuredRunRequest<'_>,
    ) -> Result<AgentOutcome, BriefingCompositionGatewayError> {
        self.run_structured_with_model(request, &self.model_spec, None)
            .await
    }

    async fn run_structured_with_model(
        &self,
        request: StructuredRunRequest<'_>,
        model_spec: &str,
        forwarded_events: Option<Arc<dyn AgentEventSink>>,
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
        let events = Arc::new(ObservedUsage::new(forwarded_events));
        self.engine
            .run(
                AgentRequest {
                    feature: feature.to_owned(),
                    model_spec: model_spec.to_owned(),
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
                events.clone(),
            )
            .await
            .map_err(|source| BriefingCompositionGatewayError::ObservedAgent {
                source,
                usage: events.usage.lock().ok().and_then(|usage| usage.clone()),
            })
    }
}

fn source_link(kind: &str, id: i64) -> String {
    format!("newsly://briefing/{kind}/{id}")
}

fn source_links_in_markdown(markdown: &str) -> Vec<String> {
    crate::briefing_links::briefing_links(markdown)
        .into_iter()
        .map(|link| link.uri)
        .collect()
}

fn secret_env(name: &str) -> Option<SecretString> {
    env::var(name)
        .ok()
        .filter(|value| !value.trim().is_empty())
        .map(SecretString::from)
}

#[derive(Debug)]
struct ObservedUsage {
    usage: Mutex<Option<ProviderUsage>>,
    forwarded: Option<Arc<dyn AgentEventSink>>,
}

impl ObservedUsage {
    fn new(forwarded: Option<Arc<dyn AgentEventSink>>) -> Self {
        Self {
            usage: Mutex::new(None),
            forwarded,
        }
    }
}

impl AgentEventSink for ObservedUsage {
    fn publish(&self, event: AgentEvent) -> Result<(), AgentRuntimeError> {
        if let AgentEvent::Usage { observation } = &event {
            let mut observed = self
                .usage
                .lock()
                .map_err(|_| AgentRuntimeError::Tool("usage observation lock failed".to_owned()))?;
            observed
                .get_or_insert_with(ProviderUsage::default)
                .add_assign(&observation.usage);
        }
        if let Some(forwarded) = &self.forwarded {
            forwarded.publish(event)?;
        }
        Ok(())
    }
}

#[derive(Debug, Error)]
pub enum BriefingCompositionGatewayError {
    #[error(transparent)]
    Config(#[from] ProviderEnvError),
    #[error("{source}")]
    ObservedAgent {
        source: AgentRuntimeError,
        usage: Option<ProviderUsage>,
    },
    #[error("invalid observed Briefing naming output: {reason}")]
    ObservedNaming {
        reason: String,
        usage: ProviderUsage,
    },
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
    #[error("invalid Briefing lens naming batch: {0}")]
    InvalidLensNamingBatch(String),
    #[error(transparent)]
    Embedding(#[from] OpenRouterEmbeddingError),
    #[error(transparent)]
    Agent(#[from] AgentRuntimeError),
    #[error(transparent)]
    Http(#[from] reqwest::Error),
    #[error("invalid provider URL: {0}")]
    Url(String),
    #[error(transparent)]
    Json(#[from] serde_json::Error),
}

impl BriefingCompositionGatewayError {
    pub fn observed_usage(&self) -> Option<&ProviderUsage> {
        match self {
            Self::ObservedAgent { usage, .. } => usage.as_ref(),
            Self::ObservedNaming { usage, .. } => Some(usage),
            _ => None,
        }
    }
    pub fn retryable(&self) -> bool {
        match self {
            Self::ObservedAgent {
                source: AgentRuntimeError::DeadlineExceeded | AgentRuntimeError::Provider(_),
                ..
            }
            | Self::Agent(AgentRuntimeError::DeadlineExceeded | AgentRuntimeError::Provider(_)) => {
                true
            }
            Self::Http(error) => error
                .status()
                .is_none_or(|status| status.as_u16() == 429 || status.is_server_error()),
            Self::Embedding(error) => error.retryable(),
            _ => false,
        }
    }
}

#[cfg(test)]
mod tests;
