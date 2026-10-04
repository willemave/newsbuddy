use std::collections::BTreeSet;
use std::env;
use std::sync::Arc;
use std::time::Duration;

use newsly_agent_runtime::{
    AgentEventSink, AgentLimits, AgentRequest, AgentRuntimeError, NewslyTranscript, ProviderUsage,
    ResponseContract, ToolPolicy,
};
use reqwest::Url;
use secrecy::SecretString;
use serde_json::Map;
use thiserror::Error;

use crate::exa::{ExaSearchOptions, ExaSearchUsage, search_exa};
use crate::provider_env::{ProviderEnvError, u64_from_env};
use crate::{ProviderCredentials, RigAgentEngine};

use crate::agent_support::NoTools;

const DEFAULT_EXA_API_BASE: &str = "https://api.exa.ai";
const DEFAULT_BRIEFING_MODEL: &str = "openai:gpt-6-luna";

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BriefingWebSearchResult {
    pub title: String,
    pub url: String,
    pub snippet: Option<String>,
    pub published_date: Option<String>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct BriefingWebSearchOutcome {
    pub results: Vec<BriefingWebSearchResult>,
    pub usage: ExaSearchUsage,
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
        let timeout_seconds = u64_from_env(
            "HTTP_TIMEOUT_SECONDS",
            60,
            "an unsigned integer number of seconds",
        )?
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
        let credentials = ProviderCredentials::from_env();
        let engine = RigAgentEngine::new(credentials);
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
    ) -> Result<BriefingWebSearchOutcome, BriefingDigGatewayError> {
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
    ) -> Result<BriefingWebSearchOutcome, BriefingDigGatewayError> {
        let key = self
            .exa_api_key
            .as_ref()
            .ok_or(BriefingDigGatewayError::ExaUnavailable)?;
        let payload = search_exa(
            &self.client,
            self.exa_search_url.clone(),
            key,
            query,
            ExaSearchOptions {
                num_results: limit.clamp(1, 25),
                include_social: false,
                text_max_characters: 1_500,
                livecrawl: Some("fallback"),
                summary_query: Some("Key points and main takeaways"),
                timeout: None,
            },
        )
        .await?;
        let summary_count = payload
            .results
            .iter()
            .filter(|result| nonempty(result.summary.clone()).is_some())
            .count();
        let text_count = payload
            .results
            .iter()
            .filter(|result| nonempty(result.text.clone()).is_some())
            .count();
        let results = payload
            .results
            .into_iter()
            .filter(|result| !result.url.trim().is_empty())
            .map(|result| BriefingWebSearchResult {
                title: nonempty(result.title).unwrap_or_else(|| result.url.clone()),
                url: result.url,
                snippet: nonempty(result.summary).or_else(|| clean_snippet(result.text)),
                published_date: nonempty(result.published_date),
            })
            .collect::<Vec<_>>();
        Ok(BriefingWebSearchOutcome {
            usage: ExaSearchUsage {
                request_count: 1,
                result_count: u64::try_from(results.len()).unwrap_or(u64::MAX),
                summary_count: u64::try_from(summary_count).unwrap_or(u64::MAX),
                text_count: u64::try_from(text_count).unwrap_or(u64::MAX),
                estimated_cost_usd: payload.estimated_cost_usd,
                known_estimated_cost_usd: payload.estimated_cost_usd.unwrap_or_default(),
                missing_cost_estimate_count: u64::from(payload.estimated_cost_usd.is_none()),
            },
            results,
        })
    }

    /// Produces a bounded text summary from the provided system and user prompts.
    ///
    /// # Errors
    ///
    /// Returns an error when model execution fails.
    /// Produces a bounded summary while publishing per-response model observations.
    ///
    /// # Errors
    ///
    /// Returns provider and validation errors from summary generation.
    pub async fn summarize_with_events(
        &self,
        system_prompt: String,
        user_prompt: String,
        events: Arc<dyn AgentEventSink>,
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
                events,
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
    #[error(transparent)]
    Config(#[from] ProviderEnvError),
    #[error("Exa is not configured")]
    ExaUnavailable,
    #[error("Briefing model returned an empty summary")]
    EmptySummary,
    #[error("Briefing Dig HTTP client failed")]
    Http(#[from] reqwest::Error),
    #[error("Briefing Dig URL is invalid")]
    Url(String),
    #[error("Briefing Dig agent request failed")]
    Agent(#[from] AgentRuntimeError),
}
