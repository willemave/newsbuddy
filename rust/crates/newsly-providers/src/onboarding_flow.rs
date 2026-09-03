use std::collections::{BTreeSet, HashSet};
use std::env;
use std::sync::Arc;
use std::time::Duration;

use futures_util::{StreamExt, stream};
use newsly_agent_runtime::{
    AgentEngine, AgentLimits, AgentRequest, AgentRuntimeError, NewslyTranscript, ResponseContract,
    ToolPolicy,
};
use reqwest::Url;
use rig_core::schemars::{JsonSchema, schema_for};
use secrecy::{ExposeSecret, SecretString};
use serde::{Deserialize, Serialize, de::DeserializeOwned};
use thiserror::Error;

use crate::{OpenRouterPrivacyPolicy, ProviderCredentials, RigAgentEngine};

#[path = "onboarding_agent_support.rs"]
mod agent_support;
#[path = "onboarding_model.rs"]
mod model_config;
use agent_support::{NoEvents, NoTools};
use model_config::{AUDIO_PLAN_SYSTEM_PROMPT, ONBOARDING_MODEL, onboarding_provider_parameters};
const DEFAULT_EXA_API_BASE: &str = "https://api.exa.ai";
const EXA_MAX_CONCURRENCY: usize = 8;
const PROFILE_TIMEOUT: Duration = Duration::from_secs(8);
const VOICE_TIMEOUT: Duration = Duration::from_secs(6);
const AUDIO_PLAN_TIMEOUT: Duration = Duration::from_secs(8);
const FAST_DISCOVER_TIMEOUT: Duration = Duration::from_secs(12);
const DISCOVERY_PROMPT_MAX_RESULTS: usize = 200;
const DISCOVERY_SNIPPET_CHARS: usize = 280;
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

const PROFILE_SYSTEM_PROMPT: &str = "You are building a short onboarding profile for a user. Use the provided interests and web snippets to infer a concise profile summary and 3-6 topical interests. Do not invent interests that contradict the user-provided topics. Return structured output only.";
const VOICE_SYSTEM_PROMPT: &str = "You extract onboarding fields from a transcript. Return a first name if explicitly stated and a concise list of interest topics. Do not guess missing information. Return structured output only.";
const FAST_DISCOVER_SYSTEM_PROMPT: &str = "You are selecting high-quality sources for a new user. Use only the profile summary, topics, and search snippets to suggest Substack/Atom feeds, podcast RSS feeds, and relevant subreddits. Every suggestion must be grounded in web_results; do not use static defaults, curated backups, or general prior knowledge as source candidates. Podcast suggestions must come from web_results only. If web_results contain no suitable sources for a category, return zero suggestions for that category. Every suggestion must include a concise, specific rationale sentence. Prefer sources with clear RSS URLs when possible. For feed-like sources, always provide a best-effort feed_url when available. If uncertain, include candidate_feed_url and set is_likely_feed plus feed_confidence (0-1). For reddit entries, include subreddit. Return structured output only.";

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct OnboardingProfile {
    pub profile_summary: String,
    pub inferred_topics: Vec<String>,
    pub candidate_sources: Vec<String>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct OnboardingVoiceFields {
    pub first_name: Option<String>,
    pub interest_topics: Vec<String>,
    pub confidence: Option<f64>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum OnboardingLaneTarget {
    Feeds,
    Podcasts,
    Reddit,
}

impl OnboardingLaneTarget {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Feeds => "feeds",
            Self::Podcasts => "podcasts",
            Self::Reddit => "reddit",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct OnboardingAudioLane {
    pub name: String,
    pub goal: String,
    pub target: OnboardingLaneTarget,
    #[serde(default)]
    pub queries: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct OnboardingAudioPlan {
    pub topic_summary: String,
    #[serde(default)]
    pub inferred_topics: Vec<String>,
    #[serde(default)]
    pub lanes: Vec<OnboardingAudioLane>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct OnboardingSuggestionSeed {
    pub title: Option<String>,
    pub site_url: Option<String>,
    pub feed_url: Option<String>,
    pub candidate_feed_url: Option<String>,
    pub is_likely_feed: Option<bool>,
    #[schemars(range(min = 0.0, max = 1.0))]
    pub feed_confidence: Option<f64>,
    pub subreddit: Option<String>,
    pub rationale: Option<String>,
    pub score: Option<f64>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema, Default)]
pub struct OnboardingDiscoverySeeds {
    #[serde(default)]
    pub substacks: Vec<OnboardingSuggestionSeed>,
    #[serde(default)]
    pub podcasts: Vec<OnboardingSuggestionSeed>,
    #[serde(default)]
    pub subreddits: Vec<OnboardingSuggestionSeed>,
}

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
struct ProfileOutput {
    profile_summary: String,
    #[serde(default)]
    inferred_topics: Vec<String>,
    #[serde(default)]
    candidate_sources: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
struct VoiceOutput {
    first_name: Option<String>,
    #[serde(default)]
    interest_topics: Vec<String>,
    #[schemars(range(min = 0.0, max = 1.0))]
    confidence: Option<f64>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct WebResult {
    title: String,
    url: String,
    snippet: Option<String>,
    published_date: Option<String>,
    query: String,
}

#[derive(Debug, Default)]
struct SearchManyOutcome {
    results: Vec<WebResult>,
    attempted: usize,
    succeeded: usize,
}

impl SearchManyOutcome {
    const fn all_attempts_failed(&self) -> bool {
        self.attempted > 0 && self.succeeded == 0
    }
}

#[derive(Debug, Clone)]
pub struct OnboardingGateway {
    client: reqwest::Client,
    exa_api_key: Option<SecretString>,
    exa_search_url: Url,
    engine: RigAgentEngine,
}

impl OnboardingGateway {
    /// Builds the provider boundary without requiring optional keys at process startup. Missing
    /// Exa credentials produce deterministic empty-search fallbacks; missing
    /// model credentials remain typed request-time failures for operations that require the LLM.
    ///
    /// # Errors
    ///
    /// Returns an error when the HTTP client, provider engine, or configured Exa URL cannot be
    /// constructed.
    pub fn from_env() -> Result<Self, OnboardingGatewayError> {
        let client = reqwest::Client::builder()
            .timeout(Duration::from_secs(
                env_u64("HTTP_TIMEOUT_SECONDS", 60).max(1),
            ))
            .build()?;
        let exa_search_url = exa_search_url()?;
        let policy = OpenRouterPrivacyPolicy::default();
        let engine = RigAgentEngine::new(
            ProviderCredentials {
                openai: secret_env("OPENAI_API_KEY"),
                anthropic: secret_env("ANTHROPIC_API_KEY"),
                google: secret_env("GOOGLE_API_KEY").or_else(|| secret_env("GEMINI_API_KEY")),
                openrouter: secret_env("OPENROUTER_API_KEY"),
            },
            policy,
        )?;
        Ok(Self {
            client,
            exa_api_key: secret_env("EXA_API_KEY"),
            exa_search_url,
            engine,
        })
    }

    /// Builds a grounded onboarding profile from the user's declared interests.
    ///
    /// # Errors
    ///
    /// Returns an error when the provider request fails or its structured response is invalid.
    pub async fn build_profile(
        &self,
        first_name: &str,
        interest_topics: &[String],
    ) -> Result<OnboardingProfile, OnboardingGatewayError> {
        let queries = profile_queries(interest_topics, first_name);
        let results = self
            .search_many(queries, 3, false, PROFILE_TIMEOUT)
            .await
            .results;
        if results.is_empty() {
            return Ok(OnboardingProfile {
                profile_summary: profile_fallback_summary(first_name, interest_topics),
                inferred_topics: merge_topics([interest_topics.iter().map(String::as_str)], 8),
                candidate_sources: Vec::new(),
            });
        }

        let web_results = results
            .iter()
            .take(10)
            .enumerate()
            .map(|(index, item)| {
                format!(
                    "{}. {}\nurl: {}\nsummary: {}",
                    index + 1,
                    item.title,
                    item.url,
                    item.snippet.as_deref().unwrap_or_default()
                )
            })
            .collect::<Vec<_>>()
            .join("\n");
        let prompt = format!(
            "first_name: {first_name}\ninterest_topics: {}\n\nweb_results:\n{web_results}",
            interest_topics.join(", ")
        );
        let output: ProfileOutput = self
            .structured(
                "onboarding.profile",
                PROFILE_SYSTEM_PROMPT,
                prompt,
                PROFILE_TIMEOUT,
            )
            .await?;
        let inferred_topics = merge_topics(
            [
                output.inferred_topics.iter().map(String::as_str),
                interest_topics.iter().map(String::as_str),
            ],
            8,
        );
        Ok(OnboardingProfile {
            profile_summary: output.profile_summary,
            inferred_topics,
            candidate_sources: output.candidate_sources,
        })
    }

    /// Extracts onboarding fields from a voice transcript.
    ///
    /// # Errors
    ///
    /// Returns an error when the provider request fails or its structured response is invalid.
    pub async fn parse_voice(
        &self,
        transcript: &str,
        locale: Option<&str>,
    ) -> Result<OnboardingVoiceFields, OnboardingGatewayError> {
        let prompt = format!(
            "Extract the user's first name (if stated) and the topics of news they want to read. Return concise topic phrases (2-5 words) and avoid guessing. locale: {}\ntranscript: {transcript}",
            locale
                .filter(|value| !value.is_empty())
                .unwrap_or("unknown")
        );
        let output: VoiceOutput = self
            .structured(
                "onboarding.voice_parse",
                VOICE_SYSTEM_PROMPT,
                prompt,
                VOICE_TIMEOUT,
            )
            .await?;
        Ok(OnboardingVoiceFields {
            first_name: clean(output.first_name),
            interest_topics: merge_topics([output.interest_topics.iter().map(String::as_str)], 8),
            confidence: output
                .confidence
                .filter(|value| (0.0..=1.0).contains(value)),
        })
    }

    /// Audio planning is deliberately fail-soft. The durable discovery run is still useful when
    /// `OpenRouter` is unavailable, so provider/schema failures fall back to deterministic lanes.
    pub async fn build_audio_plan(
        &self,
        transcript: &str,
        locale: Option<&str>,
    ) -> OnboardingAudioPlan {
        self.build_audio_plan_with_metadata(transcript, locale)
            .await
            .0
    }

    /// Builds an audio discovery plan and reports whether the deterministic fallback was used.
    /// The admin preview exposes this metadata while product callers only need the plan itself.
    pub async fn build_audio_plan_with_metadata(
        &self,
        transcript: &str,
        locale: Option<&str>,
    ) -> (OnboardingAudioPlan, bool, Option<String>) {
        let prompt = format!(
            "locale: {}\ntranscript: {transcript}",
            locale
                .filter(|value| !value.is_empty())
                .unwrap_or("unknown")
        );
        match self
            .structured::<OnboardingAudioPlan>(
                "onboarding.audio_plan",
                AUDIO_PLAN_SYSTEM_PROMPT,
                prompt,
                AUDIO_PLAN_TIMEOUT,
            )
            .await
        {
            Ok(plan) => (normalize_audio_plan(plan, transcript), false, None),
            Err(error) => {
                tracing::error!(error = %error, "onboarding audio plan failed; using deterministic lanes");
                (
                    fallback_audio_plan(transcript),
                    true,
                    Some(error.to_string()),
                )
            }
        }
    }

    /// Discovers candidate sources for a completed onboarding profile.
    ///
    /// # Errors
    ///
    /// Returns an error when the grounded structured discovery request fails.
    pub async fn fast_discover(
        &self,
        profile_summary: &str,
        inferred_topics: &[String],
    ) -> Result<OnboardingDiscoverySeeds, OnboardingGatewayError> {
        let queries = discovery_queries(profile_summary, inferred_topics, 6);
        let outcome = self
            .search_many(queries, 12, false, FAST_DISCOVER_TIMEOUT)
            .await;
        if outcome.all_attempts_failed() {
            return Err(OnboardingGatewayError::SearchUnavailable);
        }
        let mut results = outcome.results;
        dedupe_web_results(&mut results);
        results.truncate(DISCOVERY_PROMPT_MAX_RESULTS);
        self.discovery_from_results(
            "onboarding.fast_discover",
            profile_summary,
            inferred_topics,
            &results,
        )
        .await
    }

    /// Executes one search-only request per persisted audio-discovery lane concurrently, then
    /// balances prompt evidence across lanes before the structured model call. A lane whose search
    /// request fails is retried by the durable task instead of being published as an empty success.
    ///
    /// # Errors
    ///
    /// Returns an error when the grounded structured discovery request fails.
    pub async fn discover_from_lanes(
        &self,
        profile_summary: &str,
        inferred_topics: &[String],
        lanes: &[OnboardingAudioLane],
    ) -> Result<OnboardingDiscoverySeeds, OnboardingGatewayError> {
        let gateway = self.clone();
        let groups = stream::iter(lanes.to_vec())
            .map(move |lane| {
                let gateway = gateway.clone();
                async move {
                    let query = lane_search_query(&lane);
                    let outcome = gateway
                        .search_many(
                            vec![query],
                            20,
                            lane.target == OnboardingLaneTarget::Reddit,
                            FAST_DISCOVER_TIMEOUT,
                        )
                        .await;
                    if outcome.all_attempts_failed() {
                        return Err(OnboardingGatewayError::SearchUnavailable);
                    }
                    let mut results = outcome.results;
                    dedupe_web_results(&mut results);
                    Ok(results)
                }
            })
            .buffered(lanes.len().max(1))
            .collect::<Vec<Result<Vec<_>, OnboardingGatewayError>>>()
            .await;
        let groups = groups.into_iter().collect::<Result<Vec<_>, _>>()?;
        let results = balanced_web_results(groups, DISCOVERY_PROMPT_MAX_RESULTS);
        self.discovery_from_results(
            "onboarding.audio_discover",
            profile_summary,
            inferred_topics,
            &results,
        )
        .await
    }

    async fn discovery_from_results(
        &self,
        feature: &str,
        profile_summary: &str,
        inferred_topics: &[String],
        results: &[WebResult],
    ) -> Result<OnboardingDiscoverySeeds, OnboardingGatewayError> {
        if results.is_empty() {
            return Ok(OnboardingDiscoverySeeds::default());
        }
        let web_results = results
            .iter()
            .enumerate()
            .map(|(index, item)| {
                let snippet = item
                    .snippet
                    .as_deref()
                    .unwrap_or_default()
                    .trim()
                    .replace('\n', " ")
                    .chars()
                    .take(DISCOVERY_SNIPPET_CHARS)
                    .collect::<String>();
                format!(
                    "{}. {} | query: {}\nurl: {}\nsummary: {snippet}",
                    index + 1,
                    item.title,
                    item.query,
                    item.url
                )
            })
            .collect::<Vec<_>>()
            .join("\n");
        let prompt = format!(
            "profile_summary: {profile_summary}\ntopics: {}\n\nweb_results:\n{web_results}",
            inferred_topics.join(", ")
        );
        self.structured(
            feature,
            FAST_DISCOVER_SYSTEM_PROMPT,
            prompt,
            FAST_DISCOVER_TIMEOUT,
        )
        .await
    }

    async fn structured<T>(
        &self,
        feature: &str,
        system_prompt: &str,
        user_prompt: String,
        deadline: Duration,
    ) -> Result<T, OnboardingGatewayError>
    where
        T: DeserializeOwned + JsonSchema,
    {
        let outcome = self
            .engine
            .run(
                AgentRequest {
                    feature: feature.to_owned(),
                    model_spec: ONBOARDING_MODEL.to_owned(),
                    system_prompt: system_prompt.to_owned(),
                    user_prompt,
                    transcript: NewslyTranscript::default(),
                    response_contract: ResponseContract::JsonSchema {
                        name: feature.replace('.', "_"),
                        schema: schema_for!(T),
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
                        output_token_limit: Some(1_500),
                        deadline,
                    },
                    provider_parameters: onboarding_provider_parameters(),
                },
                Arc::new(NoTools),
                Arc::new(NoEvents),
            )
            .await?;
        let value = outcome
            .structured_output
            .ok_or(OnboardingGatewayError::MissingStructuredOutput)?;
        serde_json::from_value(value)
            .map_err(|error| OnboardingGatewayError::InvalidStructuredOutput(error.to_string()))
    }

    async fn search_many(
        &self,
        queries: Vec<String>,
        num_results: usize,
        include_social: bool,
        timeout: Duration,
    ) -> SearchManyOutcome {
        let Some(api_key) = self.exa_api_key.clone() else {
            tracing::warn!("Exa is not configured; onboarding search returned no results");
            return SearchManyOutcome::default();
        };
        let client = self.client.clone();
        let endpoint = self.exa_search_url.clone();
        let mut grouped = stream::iter(queries.into_iter().enumerate().map(|(index, query)| {
            let client = client.clone();
            let endpoint = endpoint.clone();
            let api_key = api_key.clone();
            async move {
                let result = search_exa(
                    &client,
                    endpoint,
                    &api_key,
                    &query,
                    num_results,
                    include_social,
                    timeout,
                )
                .await;
                (index, query, result)
            }
        }))
        .buffer_unordered(EXA_MAX_CONCURRENCY)
        .collect::<Vec<_>>()
        .await;
        grouped.sort_by_key(|(index, _, _)| *index);
        let mut outcome = SearchManyOutcome {
            attempted: grouped.len(),
            ..SearchManyOutcome::default()
        };
        for (_, query, result) in grouped {
            match result {
                Ok(results) => {
                    outcome.succeeded += 1;
                    outcome
                        .results
                        .extend(results.into_iter().map(|result| WebResult {
                            title: clean(result.title).unwrap_or_else(|| "Untitled".to_owned()),
                            url: result.url,
                            snippet: clean(result.summary).or_else(|| clean(result.text)),
                            published_date: clean(result.published_date),
                            query: query.clone(),
                        }));
                }
                Err(error) => {
                    tracing::error!(error = %error, query, "Exa onboarding search failed");
                }
            }
        }
        outcome
    }
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct ExaSearchRequest<'a> {
    query: &'a str,
    num_results: usize,
    #[serde(skip_serializing_if = "Option::is_none")]
    exclude_domains: Option<Vec<&'static str>>,
}

#[derive(Debug, Deserialize)]
struct ExaSearchResponse {
    #[serde(default)]
    results: Vec<ExaSearchRow>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct ExaSearchRow {
    title: Option<String>,
    url: String,
    summary: Option<String>,
    text: Option<String>,
    published_date: Option<String>,
}

async fn search_exa(
    client: &reqwest::Client,
    endpoint: Url,
    api_key: &SecretString,
    query: &str,
    num_results: usize,
    include_social: bool,
    timeout: Duration,
) -> Result<Vec<ExaSearchRow>, reqwest::Error> {
    let payload = ExaSearchRequest {
        query,
        num_results,
        exclude_domains: (!include_social).then(|| EXCLUDED_DOMAINS.to_vec()),
    };
    let response = client
        .post(endpoint)
        .timeout(timeout)
        .header("x-api-key", api_key.expose_secret())
        .json(&payload)
        .send()
        .await?
        .error_for_status()?
        .json::<ExaSearchResponse>()
        .await?;
    Ok(response
        .results
        .into_iter()
        .filter(|result| !result.url.trim().is_empty())
        .collect())
}

fn profile_queries(topics: &[String], first_name: &str) -> Vec<String> {
    let topics = merge_topics([topics.iter().map(String::as_str)], 8);
    let mut queries = Vec::new();
    for topic in topics {
        queries.push(format!("{topic} newsletter"));
        queries.push(format!("{topic} podcast"));
        queries.push(format!("{topic} substack"));
        if queries.len() >= 4 {
            break;
        }
    }
    if queries.is_empty() {
        queries.push(format!("{first_name} newsletter"));
    }
    queries.truncate(4);
    queries
}

fn discovery_queries(summary: &str, topics: &[String], max_queries: usize) -> Vec<String> {
    let topics = topics
        .iter()
        .map(|topic| topic.trim())
        .filter(|topic| !topic.is_empty())
        .take(4)
        .collect::<Vec<_>>();
    let mut queries = Vec::new();
    if !summary.is_empty() {
        queries.push(format!("{summary} newsletter"));
    }
    for topic in topics {
        queries.push(format!("{topic} substack"));
        queries.push(format!("{topic} podcast rss"));
        queries.push(format!("{topic} best newsletters"));
        if queries.len() >= max_queries {
            break;
        }
    }
    queries.truncate(max_queries);
    queries
}

fn lane_search_query(lane: &OnboardingAudioLane) -> String {
    let query = format!(
        "{} Source requirements: {}",
        lane.goal.trim(),
        lane.queries.join("; ")
    );
    query.chars().take(1_000).collect()
}

fn profile_fallback_summary(first_name: &str, topics: &[String]) -> String {
    let topics = merge_topics([topics.iter().map(String::as_str)], 3);
    if topics.is_empty() {
        first_name.to_owned()
    } else {
        format!("{first_name} interested in {}", topics.join(", "))
    }
}

fn normalize_audio_plan(plan: OnboardingAudioPlan, transcript: &str) -> OnboardingAudioPlan {
    let topic_summary =
        clean(Some(plan.topic_summary)).unwrap_or_else(|| fallback_topic_summary(transcript));
    let inferred_topics = merge_topics([plan.inferred_topics.iter().map(String::as_str)], 6);
    let mut lanes = Vec::new();
    let mut names = HashSet::new();
    for lane in plan.lanes {
        let Some(name) = clean(Some(lane.name)) else {
            continue;
        };
        if !names.insert(name.to_lowercase()) {
            continue;
        }
        let goal = lane.goal.trim().to_owned();
        let queries = refine_queries(
            lane.target,
            lane.queries,
            &goal,
            &inferred_topics,
            &topic_summary,
        );
        if queries.len() < 2 {
            continue;
        }
        lanes.push(OnboardingAudioLane {
            name,
            goal,
            target: lane.target,
            queries,
        });
        if lanes.len() >= 5 {
            break;
        }
    }
    if lanes.is_empty() {
        return fallback_audio_plan(transcript);
    }
    if !lanes
        .iter()
        .any(|lane| lane.target == OnboardingLaneTarget::Reddit)
    {
        let reddit = fallback_reddit_lane(transcript, &inferred_topics, &topic_summary);
        if lanes.len() >= 5 {
            lanes.pop();
        }
        lanes.push(reddit);
    }
    append_core_lanes(&mut lanes, transcript, &inferred_topics, &topic_summary);
    lanes.truncate(5);
    OnboardingAudioPlan {
        topic_summary,
        inferred_topics,
        lanes,
    }
}

fn fallback_audio_plan(transcript: &str) -> OnboardingAudioPlan {
    let topic_summary = fallback_topic_summary(transcript);
    let inferred_topics = merge_topics([[topic_summary.as_str()]], 3);
    let mut lanes = Vec::new();
    append_core_lanes(&mut lanes, transcript, &inferred_topics, &topic_summary);
    OnboardingAudioPlan {
        topic_summary,
        inferred_topics,
        lanes,
    }
}

fn append_core_lanes(
    lanes: &mut Vec<OnboardingAudioLane>,
    transcript: &str,
    topics: &[String],
    topic_summary: &str,
) {
    let seed = topics
        .first()
        .cloned()
        .unwrap_or_else(|| fallback_topic_summary(transcript));
    if lanes.len() < 3
        && !lanes
            .iter()
            .any(|lane| lane.target == OnboardingLaneTarget::Feeds)
    {
        let goal = "Find newsletters and RSS feeds aligned with the user's interests.";
        lanes.push(OnboardingAudioLane {
            name: "Newsletters & Feeds".to_owned(),
            goal: goal.to_owned(),
            target: OnboardingLaneTarget::Feeds,
            queries: refine_queries(
                OnboardingLaneTarget::Feeds,
                vec![
                    format!("{seed} newsletter"),
                    format!("{seed} RSS feed"),
                    format!("best {seed} Substack"),
                ],
                goal,
                topics,
                topic_summary,
            ),
        });
    }
    if lanes.len() < 3
        && !lanes
            .iter()
            .any(|lane| lane.target == OnboardingLaneTarget::Podcasts)
    {
        let goal = "Find podcast feeds covering the user's interests.";
        lanes.push(OnboardingAudioLane {
            name: "Podcasts".to_owned(),
            goal: goal.to_owned(),
            target: OnboardingLaneTarget::Podcasts,
            queries: refine_queries(
                OnboardingLaneTarget::Podcasts,
                vec![
                    format!("{seed} podcast"),
                    format!("{seed} podcast RSS"),
                    format!("best {seed} podcasts"),
                ],
                goal,
                topics,
                topic_summary,
            ),
        });
    }
    if !lanes
        .iter()
        .any(|lane| lane.target == OnboardingLaneTarget::Reddit)
    {
        lanes.push(fallback_reddit_lane(transcript, topics, topic_summary));
    }
}

fn fallback_reddit_lane(
    transcript: &str,
    topics: &[String],
    topic_summary: &str,
) -> OnboardingAudioLane {
    let seed = topics
        .first()
        .cloned()
        .unwrap_or_else(|| fallback_topic_summary(transcript));
    let goal = "Find active subreddits for the user's interests.";
    OnboardingAudioLane {
        name: "Reddit".to_owned(),
        goal: goal.to_owned(),
        target: OnboardingLaneTarget::Reddit,
        queries: refine_queries(
            OnboardingLaneTarget::Reddit,
            vec![
                format!("{seed} subreddit"),
                format!("best subreddits for {seed}"),
                format!("{seed} reddit community"),
            ],
            goal,
            topics,
            topic_summary,
        ),
    }
}

fn refine_queries(
    target: OnboardingLaneTarget,
    queries: Vec<String>,
    lane_goal: &str,
    inferred_topics: &[String],
    topic_summary: &str,
) -> Vec<String> {
    let mut keyword_pool = merge_topics([inferred_topics.iter().map(String::as_str)], 6);
    if keyword_pool.is_empty() {
        keyword_pool = merge_topics([[lane_goal, topic_summary]], 4);
    }
    let mut cleaned = clean_queries(queries);
    if cleaned.is_empty() {
        cleaned.push(
            keyword_pool
                .first()
                .cloned()
                .unwrap_or_else(|| lane_goal.to_owned()),
        );
    }
    let patterns = query_patterns(target);
    let mut refined = cleaned
        .iter()
        .take(4)
        .enumerate()
        .map(|(index, query)| {
            let focus = query_focus(query, &keyword_pool, index);
            let query = patterns[index % patterns.len()].replace("{focus}", &focus);
            enforce_query_words(&query, target)
        })
        .collect::<Vec<_>>();
    while refined.len() < 3 {
        let index = refined.len();
        let seed = keyword_pool
            .get(index % keyword_pool.len().max(1))
            .map_or(lane_goal, String::as_str);
        let focus = query_focus(seed, &keyword_pool, index);
        let query = patterns[index % patterns.len()].replace("{focus}", &focus);
        refined.push(enforce_query_words(&query, target));
    }
    clean_queries(refined).into_iter().take(4).collect()
}

fn clean_queries(queries: Vec<String>) -> Vec<String> {
    let mut seen = HashSet::new();
    queries
        .into_iter()
        .filter_map(|query| {
            let query = query.trim().trim_end_matches('.').to_owned();
            (!query.is_empty() && seen.insert(query.to_lowercase())).then_some(query)
        })
        .take(4)
        .collect()
}

fn query_patterns(target: OnboardingLaneTarget) -> &'static [&'static str] {
    match target {
        OnboardingLaneTarget::Podcasts => &[
            "best {focus} podcast episodes",
            "top {focus} podcast rss feeds",
            "weekly {focus} interview podcasts",
            "{focus} long-form educational podcasts",
        ],
        OnboardingLaneTarget::Reddit => &[
            "best subreddits for {focus}",
            "active reddit communities about {focus}",
            "top reddit threads on {focus}",
            "{focus} subreddit recommendations and discussions",
        ],
        OnboardingLaneTarget::Feeds => &[
            "best {focus} newsletters and rss feeds",
            "top {focus} substack and atom feeds",
            "weekly {focus} analysis newsletter feeds",
            "credible {focus} editorial rss sources",
        ],
    }
}

fn query_focus(query: &str, keywords: &[String], index: usize) -> String {
    let mut tokens = query
        .trim()
        .trim_matches(['.', ',', ';', ':', '!', '?'])
        .split_whitespace()
        .map(str::to_owned)
        .collect::<Vec<_>>();
    while tokens.first().is_some_and(|token| {
        matches!(
            token.to_lowercase().as_str(),
            "best" | "top" | "popular" | "weekly" | "find" | "search" | "discover" | "identify"
        )
    }) {
        tokens.remove(0);
    }
    let mut seen = HashSet::new();
    tokens.retain(|token| seen.insert(token.to_lowercase()));
    if tokens.len() < 2
        && let Some(keyword) = keywords.get(index % keywords.len().max(1))
    {
        for token in keyword.split_whitespace() {
            if tokens.len() >= 4 {
                break;
            }
            if !tokens
                .iter()
                .any(|existing| existing.eq_ignore_ascii_case(token))
            {
                tokens.push(token.to_owned());
            }
        }
    }
    if tokens.is_empty() {
        "current developments".to_owned()
    } else {
        tokens.into_iter().take(4).collect::<Vec<_>>().join(" ")
    }
}

fn enforce_query_words(query: &str, target: OnboardingLaneTarget) -> String {
    let mut seen = HashSet::new();
    let mut tokens = query
        .split_whitespace()
        .map(|token| token.trim_matches(['.', ',', ';', ':', '!', '?']))
        .filter(|token| !token.is_empty())
        .filter(|token| seen.insert(token.to_lowercase()))
        .map(str::to_owned)
        .take(10)
        .collect::<Vec<_>>();
    let fillers: &[&str] = match target {
        OnboardingLaneTarget::Feeds => &["newsletter", "rss", "feeds"],
        OnboardingLaneTarget::Podcasts => &["podcast", "episodes"],
        OnboardingLaneTarget::Reddit => &["reddit", "communities"],
    };
    let mut index = 0;
    while tokens.len() < 5 {
        tokens.push(fillers[index % fillers.len()].to_owned());
        index += 1;
    }
    tokens.join(" ")
}

fn fallback_topic_summary(transcript: &str) -> String {
    let cleaned = transcript.trim().trim_end_matches('.');
    if cleaned.is_empty() {
        "general news interests".to_owned()
    } else {
        cleaned
            .split_whitespace()
            .take(10)
            .collect::<Vec<_>>()
            .join(" ")
    }
}

fn merge_topics<'a, I, J>(groups: I, limit: usize) -> Vec<String>
where
    I: IntoIterator<Item = J>,
    J: IntoIterator<Item = &'a str>,
{
    let mut merged = Vec::new();
    let mut seen = HashSet::new();
    for group in groups {
        for topic in group {
            let topic = topic.trim().trim_matches(['.', ',', ';', ':']);
            if topic.is_empty() || !seen.insert(topic.to_lowercase()) {
                continue;
            }
            merged.push(topic.to_owned());
            if merged.len() >= limit {
                return merged;
            }
        }
    }
    merged
}

fn dedupe_web_results(results: &mut Vec<WebResult>) {
    let mut seen = HashSet::new();
    results.retain(|result| seen.insert(result.url.trim().to_lowercase()));
}

fn balanced_web_results(groups: Vec<Vec<WebResult>>, limit: usize) -> Vec<WebResult> {
    let mut groups = groups.into_iter().map(Vec::into_iter).collect::<Vec<_>>();
    let mut selected = Vec::new();
    let mut seen = HashSet::new();
    loop {
        let mut advanced = false;
        for group in &mut groups {
            for result in group.by_ref() {
                if !seen.insert(result.url.trim().to_lowercase()) {
                    continue;
                }
                selected.push(result);
                advanced = true;
                break;
            }
            if selected.len() >= limit {
                return selected;
            }
        }
        if !advanced {
            return selected;
        }
    }
}

fn clean(value: Option<String>) -> Option<String> {
    value.and_then(|value| {
        let value = value.trim();
        (!value.is_empty()).then(|| value.to_owned())
    })
}

fn exa_search_url() -> Result<Url, OnboardingGatewayError> {
    let raw = env::var("EXA_API_BASE_URL").unwrap_or_else(|_| DEFAULT_EXA_API_BASE.to_owned());
    let mut base =
        Url::parse(&raw).map_err(|error| OnboardingGatewayError::Url(error.to_string()))?;
    if base.path().trim_end_matches('/').ends_with("/search") {
        return Ok(base);
    }
    if !base.path().ends_with('/') {
        base.set_path(&format!("{}/", base.path().trim_end_matches('/')));
    }
    base.join("search")
        .map_err(|error| OnboardingGatewayError::Url(error.to_string()))
}

fn secret_env(name: &str) -> Option<SecretString> {
    env::var(name)
        .ok()
        .map(|value| value.trim().to_owned())
        .filter(|value| !value.is_empty())
        .map(SecretString::from)
}

fn env_u64(name: &str, default: u64) -> u64 {
    env::var(name)
        .ok()
        .and_then(|value| value.parse().ok())
        .unwrap_or(default)
}

#[derive(Debug, Error)]
pub enum OnboardingGatewayError {
    #[error("onboarding HTTP client failed")]
    Http(#[from] reqwest::Error),
    #[error("onboarding provider URL is invalid: {0}")]
    Url(String),
    #[error("onboarding search provider was unavailable for an entire discovery lane")]
    SearchUnavailable,
    #[error("onboarding agent configuration failed")]
    AgentConfiguration(#[from] crate::RigAgentEngineError),
    #[error("onboarding agent request failed")]
    Agent(#[from] AgentRuntimeError),
    #[error("onboarding model omitted its structured output")]
    MissingStructuredOutput,
    #[error("onboarding model returned invalid structured output: {0}")]
    InvalidStructuredOutput(String),
}

#[cfg(test)]
#[path = "onboarding_flow_tests.rs"]
mod tests;
