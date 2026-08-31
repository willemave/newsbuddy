use std::collections::BTreeSet;
use std::env;
use std::sync::Arc;
use std::time::Duration;

use newsly_agent_runtime::{
    AgentEngine, AgentEvent, AgentEventSink, AgentLimits, AgentRequest, AgentRuntimeError,
    BoxToolFuture, NewslyTranscript, ProviderUsage, ResponseContract, ToolCall, ToolExecutor,
    ToolPolicy,
};
use schemars::JsonSchema;
use secrecy::SecretString;
use serde::{Deserialize, Serialize};
use serde_json::Map;
use thiserror::Error;

use crate::{ModelSpec, OpenRouterPrivacyPolicy, ProviderCredentials, RigAgentEngine};

const DEFAULT_CONTENT_ANALYSIS_MODEL: &str = "openai:gpt-5.6-terra";
const MAX_ANALYSIS_TEXT_CHARS: usize = 8_000;
const MAX_INSTRUCTION_CHARS: usize = 2_000;
const MAX_INSTRUCTION_LINKS: usize = 50;
const PAGE_CONTENT_TRUNCATION_MARKER: &str = "\n\n[... PAGE CONTENT TRUNCATED ...]\n\n";

const SYSTEM_PROMPT: &str = r"You classify submitted web pages and optionally select links that satisfy a user instruction.

Classification priority:
1. A page with more than 3,000 words remains an article even when it embeds a podcast.
2. A short page centered on a podcast platform is a podcast.
3. A page centered on YouTube or Vimeo is a video.
4. Otherwise classify it as an article.

Only use a direct audio or video file as media_url. Never use a Spotify, Apple Podcasts, Overcast, YouTube, or Vimeo page URL as media_url. When an instruction is present, return a concise result and only HTTP(S) links found in or directly supported by the supplied page text. Do not invent URLs. Return structured output only.";

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum AnalyzedContentType {
    Article,
    Podcast,
    Video,
}

impl AnalyzedContentType {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Article => "article",
            Self::Podcast => "podcast",
            Self::Video => "video",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct ContentAnalysisResult {
    pub content_type: AnalyzedContentType,
    pub original_url: String,
    pub media_url: Option<String>,
    pub media_format: Option<String>,
    pub title: Option<String>,
    pub description: Option<String>,
    pub duration_seconds: Option<i64>,
    pub platform: Option<String>,
    pub confidence: f64,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct InstructionLink {
    pub url: String,
    pub title: Option<String>,
    pub context: Option<String>,
    pub content_type: Option<String>,
    pub platform: Option<String>,
    pub source: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct InstructionResult {
    pub text: Option<String>,
    #[serde(default)]
    pub links: Vec<InstructionLink>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
struct ContentAnalysisDocument {
    analysis: ContentAnalysisResult,
    instruction: Option<InstructionResult>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct GeneratedContentAnalysis {
    pub analysis: ContentAnalysisResult,
    pub instruction: Option<InstructionResult>,
    pub model: String,
    pub usage: ProviderUsage,
    pub provider_response_id: Option<String>,
}

#[derive(Debug, Clone)]
pub struct ContentAnalysisGateway {
    engine: RigAgentEngine,
    model_spec: String,
    deadline: Duration,
}

impl ContentAnalysisGateway {
    /// Builds the model boundary without requiring optional credentials at process startup.
    /// Missing credentials become a typed request-time error only when structured analysis runs.
    ///
    /// # Errors
    ///
    /// Returns an error when the selected model or provider policy is invalid.
    pub fn from_env() -> Result<Self, ContentAnalysisGatewayError> {
        let model_spec = env::var("CONTENT_ANALYSIS_MODEL")
            .unwrap_or_else(|_| DEFAULT_CONTENT_ANALYSIS_MODEL.to_owned());
        ModelSpec::parse(&model_spec)?;
        let engine = RigAgentEngine::new(
            ProviderCredentials {
                openai: secret_env("OPENAI_API_KEY"),
                anthropic: secret_env("ANTHROPIC_API_KEY"),
                google: secret_env("GOOGLE_API_KEY").or_else(|| secret_env("GEMINI_API_KEY")),
                openrouter: secret_env("OPENROUTER_API_KEY"),
            },
            OpenRouterPrivacyPolicy::default(),
        )?;
        let timeout_seconds = env::var("CONTENT_ANALYSIS_TIMEOUT_SECONDS")
            .ok()
            .and_then(|value| value.parse::<u64>().ok())
            .unwrap_or(90)
            .clamp(5, 180);
        Ok(Self {
            engine,
            model_spec,
            deadline: Duration::from_secs(timeout_seconds),
        })
    }

    /// Classifies one already-extracted page and handles its optional instruction.
    ///
    /// # Errors
    ///
    /// Returns a typed provider, schema, or semantic validation error. No database state is
    /// touched by this operation.
    pub async fn analyze(
        &self,
        url: &str,
        page_text: &str,
        instruction: Option<&str>,
    ) -> Result<GeneratedContentAnalysis, ContentAnalysisGatewayError> {
        let input_url = reqwest::Url::parse(url)
            .map_err(|_| ContentAnalysisGatewayError::InvalidUrl(url.to_owned()))?;
        if !matches!(input_url.scheme(), "http" | "https") || input_url.host().is_none() {
            return Err(ContentAnalysisGatewayError::InvalidUrl(url.to_owned()));
        }
        let instruction = instruction
            .map(str::trim)
            .filter(|value| !value.is_empty())
            .map(|value| truncate_chars(value, MAX_INSTRUCTION_CHARS));
        let page_text = clip_middle(page_text, MAX_ANALYSIS_TEXT_CHARS);
        let word_count = page_text.split_whitespace().count();
        let prompt = format!(
            "URL: {url}\nWORD COUNT: {word_count}\nINSTRUCTION: {}\n\nPAGE CONTENT:\n{page_text}",
            instruction.as_deref().unwrap_or("None")
        );
        let outcome = self
            .engine
            .run(
                AgentRequest {
                    feature: "content_analysis".to_owned(),
                    model_spec: self.model_spec.clone(),
                    system_prompt: SYSTEM_PROMPT.to_owned(),
                    user_prompt: prompt,
                    transcript: NewslyTranscript::default(),
                    response_contract: ResponseContract::JsonSchema {
                        name: "content_analysis_v1".to_owned(),
                        schema: schemars::schema_for!(ContentAnalysisDocument),
                        strict: true,
                        validation_retries: 2,
                    },
                    tools: Vec::new(),
                    tool_policy: ToolPolicy {
                        allowed: BTreeSet::new(),
                        require_tool: false,
                        allow_parallel_calls: false,
                    },
                    limits: AgentLimits {
                        request_limit: Some(3),
                        tool_call_limit: 0,
                        output_token_limit: Some(2_500),
                        deadline: self.deadline,
                    },
                    provider_parameters: Map::new(),
                },
                Arc::new(NoTools),
                Arc::new(NoEvents),
            )
            .await?;
        let document = serde_json::from_value::<ContentAnalysisDocument>(
            outcome
                .structured_output
                .ok_or(ContentAnalysisGatewayError::MissingStructuredOutput)?,
        )?;
        validate_document(&document, input_url.as_str(), instruction.is_some())?;
        Ok(GeneratedContentAnalysis {
            analysis: document.analysis,
            instruction: document.instruction,
            model: outcome.model_name,
            usage: outcome.usage,
            provider_response_id: outcome.provider_response_id,
        })
    }
}

fn validate_document(
    document: &ContentAnalysisDocument,
    input_url: &str,
    instruction_present: bool,
) -> Result<(), ContentAnalysisGatewayError> {
    let returned_url = reqwest::Url::parse(&document.analysis.original_url)
        .map_err(|_| ContentAnalysisGatewayError::InvalidOutput("invalid original_url"))?;
    let input = reqwest::Url::parse(input_url)
        .map_err(|_| ContentAnalysisGatewayError::InvalidOutput("invalid input URL"))?;
    if returned_url != input {
        return Err(ContentAnalysisGatewayError::InvalidOutput(
            "original_url does not match the requested URL",
        ));
    }
    if !document.analysis.confidence.is_finite()
        || !(0.0..=1.0).contains(&document.analysis.confidence)
    {
        return Err(ContentAnalysisGatewayError::InvalidOutput(
            "confidence is outside 0 through 1",
        ));
    }
    if document
        .analysis
        .duration_seconds
        .is_some_and(|value| value < 0)
    {
        return Err(ContentAnalysisGatewayError::InvalidOutput(
            "duration_seconds is negative",
        ));
    }
    if let Some(media_url) = &document.analysis.media_url {
        require_http_url(media_url, "media_url")?;
    }
    let links = document
        .instruction
        .as_ref()
        .map_or(&[][..], |instruction| instruction.links.as_slice());
    if links.len() > MAX_INSTRUCTION_LINKS {
        return Err(ContentAnalysisGatewayError::InvalidOutput(
            "instruction returned too many links",
        ));
    }
    if !instruction_present && !links.is_empty() {
        return Err(ContentAnalysisGatewayError::InvalidOutput(
            "instruction links were returned without an instruction",
        ));
    }
    for link in links {
        require_http_url(&link.url, "instruction link")?;
        if link.url.chars().count() > 2_048
            || link
                .title
                .as_ref()
                .is_some_and(|value| value.chars().count() > 500)
            || link
                .context
                .as_ref()
                .is_some_and(|value| value.chars().count() > 1_000)
        {
            return Err(ContentAnalysisGatewayError::InvalidOutput(
                "instruction link exceeds its size bound",
            ));
        }
    }
    Ok(())
}

fn require_http_url(value: &str, field: &'static str) -> Result<(), ContentAnalysisGatewayError> {
    let parsed = reqwest::Url::parse(value)
        .map_err(|_| ContentAnalysisGatewayError::InvalidOutput(field))?;
    if !matches!(parsed.scheme(), "http" | "https") || parsed.host().is_none() {
        return Err(ContentAnalysisGatewayError::InvalidOutput(field));
    }
    Ok(())
}

fn clip_middle(value: &str, max_chars: usize) -> String {
    if value.chars().count() <= max_chars {
        return value.to_owned();
    }
    let remaining = max_chars.saturating_sub(PAGE_CONTENT_TRUNCATION_MARKER.chars().count());
    let head = remaining / 2;
    let tail = remaining.saturating_sub(head);
    let beginning = value.chars().take(head).collect::<String>();
    let ending = value
        .chars()
        .rev()
        .take(tail)
        .collect::<String>()
        .chars()
        .rev()
        .collect::<String>();
    format!(
        "{}{PAGE_CONTENT_TRUNCATION_MARKER}{}",
        beginning.trim_end(),
        ending.trim_start()
    )
}

fn truncate_chars(value: &str, max_chars: usize) -> String {
    value.chars().take(max_chars).collect()
}

fn secret_env(name: &str) -> Option<SecretString> {
    env::var(name)
        .ok()
        .map(|value| value.trim().to_owned())
        .filter(|value| !value.is_empty())
        .map(SecretString::from)
}

#[derive(Debug)]
struct NoTools;

impl ToolExecutor for NoTools {
    fn execute(&self, call: ToolCall, _events: Arc<dyn AgentEventSink>) -> BoxToolFuture<'_> {
        Box::pin(async move {
            Err(AgentRuntimeError::Tool(format!(
                "content analysis does not expose tool {}",
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
pub enum ContentAnalysisGatewayError {
    #[error("content analysis URL is invalid: {0}")]
    InvalidUrl(String),
    #[error("content analysis model omitted its structured output")]
    MissingStructuredOutput,
    #[error("content analysis model returned invalid output: {0}")]
    InvalidOutput(&'static str),
    #[error(transparent)]
    Agent(#[from] AgentRuntimeError),
    #[error(transparent)]
    Engine(#[from] crate::RigAgentEngineError),
    #[error(transparent)]
    ModelSpec(#[from] crate::ModelSpecError),
    #[error(transparent)]
    Json(#[from] serde_json::Error),
}

#[cfg(test)]
mod tests {
    use super::{ContentAnalysisDocument, validate_document};

    #[test]
    fn rejects_links_without_an_instruction() {
        let document = serde_json::from_value::<ContentAnalysisDocument>(serde_json::json!({
            "analysis": {
                "content_type": "article",
                "original_url": "https://example.com/source",
                "media_url": null,
                "media_format": null,
                "title": "Source",
                "description": null,
                "duration_seconds": null,
                "platform": null,
                "confidence": 0.9
            },
            "instruction": {
                "text": null,
                "links": [{
                    "url": "https://example.com/child",
                    "title": null,
                    "context": null,
                    "content_type": null,
                    "platform": null,
                    "source": null
                }]
            }
        }))
        .expect("fixture is valid JSON");
        assert!(validate_document(&document, "https://example.com/source", false).is_err());
    }

    #[test]
    fn accepts_bounded_instruction_links() {
        let document = serde_json::from_value::<ContentAnalysisDocument>(serde_json::json!({
            "analysis": {
                "content_type": "article",
                "original_url": "https://example.com/source",
                "media_url": null,
                "media_format": null,
                "title": "Source",
                "description": null,
                "duration_seconds": null,
                "platform": null,
                "confidence": 0.9
            },
            "instruction": {
                "text": "One relevant source",
                "links": [{
                    "url": "https://example.com/child",
                    "title": "Child",
                    "context": "Relevant",
                    "content_type": "article",
                    "platform": null,
                    "source": "source page"
                }]
            }
        }))
        .expect("fixture is valid JSON");
        assert!(validate_document(&document, "https://example.com/source", true).is_ok());
    }
}
