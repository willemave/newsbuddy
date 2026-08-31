use std::collections::BTreeSet;
use std::env;
use std::sync::Arc;
use std::time::Duration;

use newsly_agent_runtime::{
    AgentEngine, AgentEvent, AgentEventSink, AgentLimits, AgentRequest, AgentRuntimeError,
    BoxToolFuture, NewslyTranscript, ProviderUsage, ResponseContract, ToolCall, ToolExecutor,
    ToolPolicy,
};
use reqwest::Url;
use secrecy::{ExposeSecret, SecretString};
use serde::{Deserialize, Serialize};
use serde_json::Map;
use thiserror::Error;

use crate::{OpenRouterPrivacyPolicy, ProviderCredentials, RigAgentEngine};

const DEFAULT_EXA_API_BASE: &str = "https://api.exa.ai";
const DEFAULT_BRIEFING_MODEL: &str = "openai:gpt-5.6-luna";
const EXCLUDED_DOMAINS: [&str; 8] = [
    "facebook.com",
    "linkedin.com",
    "twitter.com",
    "x.com",
    "instagram.com",
    "tiktok.com",
    "pinterest.com",
    "reddit.com",
];

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BriefingWebSearchResult {
    pub title: String,
    pub url: String,
    pub snippet: Option<String>,
    pub published_date: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BriefingDigSummary {
    pub text: String,
    pub model: String,
    pub usage: ProviderUsage,
}

#[derive(Debug, Clone)]
pub struct BriefingDigGateway {
    client: reqwest::Client,
    exa_api_key: Option<SecretString>,
    exa_search_url: Url,
    model_spec: String,
    engine: RigAgentEngine,
}

impl BriefingDigGateway {
    /// Builds the native provider boundary from the same environment variables as the legacy
    /// runtime. Missing provider keys remain a request-time typed unavailable error so the rest of
    /// the Rust API can boot without optional Dig Deeper integrations.
    ///
    /// # Errors
    ///
    /// Returns an error when configured URLs or HTTP/model clients cannot be constructed.
    pub fn from_env() -> Result<Self, BriefingDigGatewayError> {
        let timeout_seconds = env::var("HTTP_TIMEOUT_SECONDS")
            .ok()
            .and_then(|value| value.parse::<u64>().ok())
            .unwrap_or(60)
            .max(1);
        let client = reqwest::Client::builder()
            .timeout(Duration::from_secs(timeout_seconds))
            .build()?;
        let mut exa_base = Url::parse(
            &env::var("EXA_API_BASE_URL").unwrap_or_else(|_| DEFAULT_EXA_API_BASE.to_owned()),
        )
        .map_err(|error| BriefingDigGatewayError::Url(error.to_string()))?;
        if !exa_base.path().ends_with('/') {
            exa_base.set_path(&format!("{}/", exa_base.path().trim_end_matches('/')));
        }
        let exa_search_url = exa_base
            .join("search")
            .map_err(|error| BriefingDigGatewayError::Url(error.to_string()))?;
        let credentials = ProviderCredentials {
            openai: secret_env("OPENAI_API_KEY"),
            anthropic: secret_env("ANTHROPIC_API_KEY"),
            google: secret_env("GOOGLE_API_KEY").or_else(|| secret_env("GEMINI_API_KEY")),
            openrouter: secret_env("OPENROUTER_API_KEY"),
        };
        let engine = RigAgentEngine::new(credentials, OpenRouterPrivacyPolicy::default())?;
        Ok(Self {
            client,
            exa_api_key: secret_env("EXA_API_KEY"),
            exa_search_url,
            model_spec: env::var("BRIEFING_MODEL")
                .unwrap_or_else(|_| DEFAULT_BRIEFING_MODEL.to_owned()),
            engine,
        })
    }

    pub fn model_spec(&self) -> &str {
        &self.model_spec
    }

    /// Searches for up to five web results relevant to the supplied query.
    ///
    /// # Errors
    ///
    /// Returns an error when Exa is unavailable or its request or response cannot be processed.
    pub async fn search(
        &self,
        query: &str,
    ) -> Result<Vec<BriefingWebSearchResult>, BriefingDigGatewayError> {
        self.search_limit(query, 5).await
    }

    /// Searches Exa with the caller's bounded result limit. This is shared by Briefing Dig and
    /// the machine-facing agent search endpoint so Newsly has one provider contract.
    ///
    /// # Errors
    ///
    /// Returns an error when Exa is unavailable or its request or response cannot be processed.
    pub async fn search_limit(
        &self,
        query: &str,
        limit: usize,
    ) -> Result<Vec<BriefingWebSearchResult>, BriefingDigGatewayError> {
        let key = self
            .exa_api_key
            .as_ref()
            .ok_or(BriefingDigGatewayError::ExaUnavailable)?;
        let response = self
            .client
            .post(self.exa_search_url.clone())
            .header("x-api-key", key.expose_secret())
            .json(&ExaSearchRequest {
                query,
                num_results: u8::try_from(limit.clamp(1, 25)).unwrap_or(25),
                exclude_domains: EXCLUDED_DOMAINS.to_vec(),
                contents: ExaContentsRequest {
                    livecrawl: "fallback",
                    summary: ExaSummaryRequest {
                        query: "Key points and main takeaways",
                    },
                    text: ExaTextRequest {
                        max_characters: 1_500,
                    },
                },
            })
            .send()
            .await?
            .error_for_status()?;
        let payload = response.json::<ExaSearchResponse>().await?;
        Ok(payload
            .results
            .into_iter()
            .filter(|result| !result.url.trim().is_empty())
            .map(|result| BriefingWebSearchResult {
                title: nonempty(result.title).unwrap_or_else(|| result.url.clone()),
                url: result.url,
                snippet: nonempty(result.summary).or_else(|| clean_snippet(result.text)),
                published_date: nonempty(result.published_date),
            })
            .collect())
    }

    /// Produces a bounded text summary from the provided system and user prompts.
    ///
    /// # Errors
    ///
    /// Returns an error when model execution fails.
    pub async fn summarize(
        &self,
        system_prompt: String,
        user_prompt: String,
    ) -> Result<BriefingDigSummary, BriefingDigGatewayError> {
        let outcome = self
            .engine
            .run(
                AgentRequest {
                    feature: "briefing_dig".to_owned(),
                    model_spec: self.model_spec.clone(),
                    system_prompt,
                    user_prompt,
                    transcript: NewslyTranscript::default(),
                    response_contract: ResponseContract::Text,
                    tools: Vec::new(),
                    tool_policy: ToolPolicy {
                        allowed: BTreeSet::new(),
                        require_tool: false,
                        allow_parallel_calls: false,
                    },
                    limits: AgentLimits {
                        request_limit: Some(1),
                        tool_call_limit: 0,
                        output_token_limit: Some(1_200),
                        deadline: Duration::from_secs(60),
                    },
                    provider_parameters: Map::new(),
                },
                Arc::new(NoTools),
                Arc::new(NoEvents),
            )
            .await?;
        let text = outcome.output_text.trim().to_owned();
        if text.is_empty() {
            return Err(BriefingDigGatewayError::EmptySummary);
        }
        Ok(BriefingDigSummary {
            text,
            model: outcome.model_name,
            usage: outcome.usage,
        })
    }
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct ExaSearchRequest<'a> {
    query: &'a str,
    num_results: u8,
    exclude_domains: Vec<&'static str>,
    contents: ExaContentsRequest<'a>,
}

#[derive(Debug, Serialize)]
struct ExaContentsRequest<'a> {
    livecrawl: &'a str,
    summary: ExaSummaryRequest<'a>,
    text: ExaTextRequest,
}

#[derive(Debug, Serialize)]
struct ExaSummaryRequest<'a> {
    query: &'a str,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct ExaTextRequest {
    max_characters: usize,
}

#[derive(Debug, Deserialize)]
struct ExaSearchResponse {
    #[serde(default)]
    results: Vec<ExaSearchRow>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct ExaSearchRow {
    #[serde(default)]
    title: String,
    #[serde(default)]
    url: String,
    summary: Option<String>,
    text: Option<String>,
    published_date: Option<String>,
}

#[derive(Debug)]
struct NoEvents;

impl AgentEventSink for NoEvents {
    fn publish(&self, _event: AgentEvent) -> Result<(), AgentRuntimeError> {
        Ok(())
    }
}

#[derive(Debug)]
struct NoTools;

impl ToolExecutor for NoTools {
    fn execute(&self, call: ToolCall, _events: Arc<dyn AgentEventSink>) -> BoxToolFuture<'_> {
        Box::pin(async move {
            Err(AgentRuntimeError::Tool(format!(
                "Briefing Dig does not expose tool {}",
                call.name
            )))
        })
    }
}

fn secret_env(name: &str) -> Option<SecretString> {
    env::var(name)
        .ok()
        .filter(|value| !value.trim().is_empty())
        .map(SecretString::from)
}

fn nonempty(value: impl Into<Option<String>>) -> Option<String> {
    value.into().and_then(|value| {
        let trimmed = value.trim();
        (!trimmed.is_empty()).then(|| trimmed.to_owned())
    })
}

fn clean_snippet(value: Option<String>) -> Option<String> {
    let text = nonempty(value)?;
    let cleaned = text
        .lines()
        .map(str::trim)
        .filter(|line| !line.is_empty())
        .collect::<Vec<_>>()
        .join(" ");
    nonempty(Some(cleaned.chars().take(1_500).collect()))
}

#[derive(Debug, Error)]
pub enum BriefingDigGatewayError {
    #[error("Exa is not configured")]
    ExaUnavailable,
    #[error("Briefing model returned an empty summary")]
    EmptySummary,
    #[error("Briefing Dig HTTP client failed")]
    Http(#[from] reqwest::Error),
    #[error("Briefing Dig URL is invalid")]
    Url(String),
    #[error("Briefing Dig agent configuration failed")]
    AgentConfiguration(#[from] crate::RigAgentEngineError),
    #[error("Briefing Dig agent request failed")]
    Agent(#[from] AgentRuntimeError),
}
