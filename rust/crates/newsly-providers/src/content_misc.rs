use std::collections::{BTreeMap, BTreeSet};
use std::env;
use std::fmt::Write as _;
use std::sync::Arc;
use std::time::Duration;

use chrono::{DateTime, Utc};
use newsly_agent_runtime::{
    AgentEngine, AgentEvent, AgentEventSink, AgentLimits, AgentRequest, AgentRuntimeError,
    BoxToolFuture, NewslyTranscript, ProviderUsage, ResponseContract, ToolCall, ToolExecutor,
    ToolPolicy,
};
use reqwest::Url;
use rig_core::schemars::{JsonSchema, schema_for};
use secrecy::{ExposeSecret, SecretString};
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};
use thiserror::Error;

use crate::{OpenRouterPrivacyPolicy, ProviderCredentials, RigAgentEngine};

const DEFAULT_ITUNES_SEARCH_URL: &str = "https://itunes.apple.com/search";
const DEFAULT_ELEVENLABS_API_BASE: &str = "https://api.elevenlabs.io";
const DEFAULT_TWEET_MODEL: &str = "openai:gpt-5.6-luna";
const DEFAULT_ANTHROPIC_TWEET_MODEL: &str = "anthropic:claude-sonnet-4-5";
const DEFAULT_DISCUSSION_MODEL: &str = "openai:gpt-5.6-luna";

#[derive(Debug, Clone, PartialEq)]
pub struct PodcastEpisodeHit {
    pub title: String,
    pub episode_url: String,
    pub podcast_title: Option<String>,
    pub source: Option<String>,
    pub snippet: Option<String>,
    pub feed_url: Option<String>,
    pub published_at: Option<String>,
    pub provider: String,
    pub score: Option<f64>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FeedDiscoveryHit {
    pub id: String,
    pub title: String,
    pub site_url: String,
    pub feed_url: String,
    pub feed_type: String,
    pub feed_format: String,
    pub description: Option<String>,
    pub evidence_url: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DiscussionCommentHit {
    pub comment_id: String,
    pub parent_id: Option<String>,
    pub author: Option<String>,
    pub text: String,
    pub compact_text: String,
    pub depth: i64,
    pub created_at: Option<String>,
    pub source_url: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DiscussionLinkHit {
    pub url: String,
    pub comment_id: Option<String>,
    pub title: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DiscussionThreadHit {
    pub title: Option<String>,
    pub author: Option<String>,
    pub score: Option<i64>,
    pub comment_count: Option<i64>,
    pub created_at: Option<String>,
    pub subreddit: Option<String>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct DiscussionRefreshResult {
    pub platform: String,
    pub external_id: String,
    pub source_url: String,
    pub provider: String,
    pub thread: DiscussionThreadHit,
    pub comments: Vec<DiscussionCommentHit>,
    pub links: Vec<DiscussionLinkHit>,
    pub total_seen: usize,
    pub comment_cap: usize,
    pub cap_reached: bool,
    pub stats: BTreeMap<String, Value>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct DiscussionSummaryTopic {
    pub title: String,
    pub summary: String,
    pub stance: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct DiscussionSummaryLink {
    pub url: String,
    pub title: Option<String>,
    pub reason: Option<String>,
    pub source_comment_id: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct DiscussionSummaryComment {
    pub comment_id: Option<String>,
    pub author: Option<String>,
    pub text: String,
    pub reason: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct DiscussionSummaryArtifact {
    pub overview: String,
    pub topics: Vec<DiscussionSummaryTopic>,
    #[serde(default)]
    pub notable_links: Vec<DiscussionSummaryLink>,
    #[serde(default)]
    pub representative_comments: Vec<DiscussionSummaryComment>,
    #[serde(default)]
    pub external_discussion_url: Option<String>,
    #[serde(default = "Utc::now")]
    pub generated_at: DateTime<Utc>,
}

impl DiscussionSummaryArtifact {
    fn normalize_and_validate(
        mut self,
        external_discussion_url: Option<&str>,
    ) -> Result<Self, ContentMiscGatewayError> {
        self.overview = clean_text(&self.overview);
        if !(20..=900).contains(&self.overview.chars().count()) {
            return Err(ContentMiscGatewayError::InvalidDiscussionSummary(
                "overview must contain 20 to 900 characters".to_owned(),
            ));
        }
        if self.topics.is_empty() {
            self.topics.push(DiscussionSummaryTopic {
                title: "General discussion".to_owned(),
                summary: self.overview.clone(),
                stance: None,
            });
        }
        if self.topics.len() > 8 {
            self.topics.truncate(8);
        }
        for topic in &mut self.topics {
            topic.title = clean_text(&topic.title);
            topic.summary = clean_text(&topic.summary);
            topic.stance = clean_optional_bounded(topic.stance.take(), 120);
            if !(2..=90).contains(&topic.title.chars().count())
                || !(10..=500).contains(&topic.summary.chars().count())
            {
                return Err(ContentMiscGatewayError::InvalidDiscussionSummary(
                    "topic title or summary is outside its supported length".to_owned(),
                ));
            }
        }
        self.notable_links
            .retain(|link| link.url.chars().count() <= 2_048 && is_http_url(&link.url));
        for link in &mut self.notable_links {
            link.url = link.url.trim().to_owned();
            link.title = clean_optional_bounded(link.title.take(), 240);
            link.reason = clean_optional_bounded(link.reason.take(), 300);
            link.source_comment_id = clean_optional_bounded(link.source_comment_id.take(), 120);
        }
        self.notable_links.truncate(10);
        for comment in &mut self.representative_comments {
            comment.comment_id = clean_optional_bounded(comment.comment_id.take(), 120);
            comment.author = clean_optional_bounded(comment.author.take(), 120);
            comment.text = clean_text(&comment.text);
            comment.reason = clean_optional_bounded(comment.reason.take(), 220);
        }
        self.representative_comments
            .retain(|comment| !comment.text.is_empty() && comment.text.chars().count() <= 500);
        self.representative_comments.truncate(6);
        self.external_discussion_url = external_discussion_url
            .filter(|value| is_http_url(value))
            .map(str::to_owned);
        self.generated_at = Utc::now();
        Ok(self)
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct GeneratedDiscussionSummary {
    pub summary: DiscussionSummaryArtifact,
    pub summary_json: Value,
    pub provider: String,
    pub model: String,
    pub usage: ProviderUsage,
    pub provider_response_id: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct GeneratedTweetSuggestion {
    pub id: u8,
    pub text: String,
    pub style_label: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
struct GeneratedTweetDocument {
    suggestions: Vec<GeneratedTweetSuggestion>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct GeneratedTweetSuggestions {
    pub model: String,
    pub suggestions: Vec<GeneratedTweetSuggestion>,
}

#[derive(Debug, Clone)]
pub struct ContentMiscGateway {
    client: reqwest::Client,
    itunes_search_url: Url,
    agent_engine: RigAgentEngine,
    tweet_model: String,
    anthropic_tweet_model: String,
    discussion_model: String,
    elevenlabs_api_base: Url,
    elevenlabs_api_key: Option<SecretString>,
    elevenlabs_voice_id: Option<String>,
    elevenlabs_model: String,
    elevenlabs_output_format: String,
    elevenlabs_speed: f32,
}

impl ContentMiscGateway {
    /// Creates the miscellaneous content-provider gateway from environment configuration.
    ///
    /// # Errors
    ///
    /// Returns an error when provider URLs, credentials, or HTTP/model clients are invalid.
    pub fn from_env() -> Result<Self, ContentMiscGatewayError> {
        let timeout = Duration::from_secs(env_u64("HTTP_TIMEOUT_SECONDS", 60).max(1));
        let client = reqwest::Client::builder().timeout(timeout).build()?;
        let itunes_search_url = Url::parse(
            &env::var("APPLE_ITUNES_SEARCH_URL")
                .unwrap_or_else(|_| DEFAULT_ITUNES_SEARCH_URL.to_owned()),
        )
        .map_err(|error| ContentMiscGatewayError::Url(error.to_string()))?;
        let elevenlabs_api_base = Url::parse(
            &env::var("ELEVENLABS_API_BASE_URL")
                .unwrap_or_else(|_| DEFAULT_ELEVENLABS_API_BASE.to_owned()),
        )
        .map_err(|error| ContentMiscGatewayError::Url(error.to_string()))?;
        let agent_engine = RigAgentEngine::new(
            ProviderCredentials {
                openai: secret_env("OPENAI_API_KEY"),
                anthropic: secret_env("ANTHROPIC_API_KEY"),
                google: secret_env("GOOGLE_API_KEY").or_else(|| secret_env("GEMINI_API_KEY")),
                openrouter: secret_env("OPENROUTER_API_KEY"),
            },
            OpenRouterPrivacyPolicy::default(),
        )?;
        Ok(Self {
            client,
            itunes_search_url,
            agent_engine,
            tweet_model: env::var("TWEET_SUGGESTION_MODEL")
                .unwrap_or_else(|_| DEFAULT_TWEET_MODEL.to_owned()),
            anthropic_tweet_model: env::var("TWEET_SUGGESTION_ANTHROPIC_MODEL")
                .unwrap_or_else(|_| DEFAULT_ANTHROPIC_TWEET_MODEL.to_owned()),
            discussion_model: env::var("DISCUSSION_SUMMARY_MODEL")
                .unwrap_or_else(|_| DEFAULT_DISCUSSION_MODEL.to_owned()),
            elevenlabs_api_base,
            elevenlabs_api_key: secret_env("ELEVENLABS_API_KEY"),
            elevenlabs_voice_id: clean_env("ELEVENLABS_TTS_VOICE_ID"),
            elevenlabs_model: env::var("ELEVENLABS_NARRATION_TTS_MODEL")
                .unwrap_or_else(|_| "eleven_flash_v2_5".to_owned()),
            elevenlabs_output_format: env::var("ELEVENLABS_NARRATION_TTS_OUTPUT_FORMAT")
                .unwrap_or_else(|_| "mp3_44100_128".to_owned()),
            elevenlabs_speed: env::var("ELEVENLABS_NARRATION_TTS_SPEED")
                .ok()
                .and_then(|value| value.parse::<f32>().ok())
                .filter(|value| (0.5..=2.0).contains(value))
                .unwrap_or(1.0),
        })
    }

    /// Searches the configured podcast catalog for episodes matching a query.
    ///
    /// # Errors
    ///
    /// Returns an error when the provider request or response cannot be processed.
    pub async fn search_podcast_episodes(
        &self,
        query: &str,
        limit: usize,
    ) -> Result<Vec<PodcastEpisodeHit>, ContentMiscGatewayError> {
        let response = self
            .client
            .get(self.itunes_search_url.clone())
            .query(&[
                ("term", query),
                ("media", "podcast"),
                ("entity", "podcastEpisode"),
                ("country", "US"),
                ("limit", &limit.clamp(1, 25).to_string()),
            ])
            .send()
            .await?
            .error_for_status()?
            .json::<ItunesSearchResponse>()
            .await?;
        let mut hits = Vec::new();
        for result in response.results {
            let Some(episode_url) = clean(result.episode_url.or(result.track_view_url)) else {
                continue;
            };
            if !is_http_url(&episode_url) {
                continue;
            }
            let title = clean(result.track_name)
                .or_else(|| clean(result.collection_name.clone()))
                .unwrap_or_else(|| episode_url.clone());
            hits.push(PodcastEpisodeHit {
                title,
                episode_url,
                podcast_title: clean(result.collection_name),
                source: clean(result.artist_name),
                snippet: clean(result.description),
                feed_url: clean(result.feed_url).filter(|value| is_http_url(value)),
                published_at: clean(result.release_date),
                provider: "apple_itunes".to_owned(),
                score: None,
            });
        }
        dedupe_podcast_hits(&mut hits);
        hits.truncate(limit.clamp(1, 25));
        Ok(hits)
    }

    /// Discovers candidate feeds for a natural-language query.
    ///
    /// # Errors
    ///
    /// Returns an error when discovery transport, parsing, or normalization fails.
    pub async fn discover_feeds(
        &self,
        query: &str,
        limit: usize,
    ) -> Result<Vec<FeedDiscoveryHit>, ContentMiscGatewayError> {
        let episodes = self
            .search_podcast_episodes(query, limit.saturating_mul(2).clamp(1, 25))
            .await?;
        let mut feeds = Vec::new();
        for episode in episodes {
            let Some(feed_url) = episode.feed_url else {
                continue;
            };
            let site_url = Url::parse(&episode.episode_url).ok().map_or_else(
                || episode.episode_url.clone(),
                |mut url| {
                    url.set_path("");
                    url.set_query(None);
                    url.set_fragment(None);
                    url.to_string()
                },
            );
            let id = stable_feed_id(&feed_url);
            feeds.push(FeedDiscoveryHit {
                id,
                title: episode
                    .podcast_title
                    .clone()
                    .unwrap_or_else(|| episode.title.clone()),
                site_url,
                feed_url: feed_url.clone(),
                feed_type: "podcast_rss".to_owned(),
                feed_format: "rss".to_owned(),
                description: episode.snippet,
                evidence_url: Some(episode.episode_url),
            });
        }
        feeds.sort_by(|left, right| left.feed_url.cmp(&right.feed_url));
        feeds.dedup_by(|left, right| left.feed_url == right.feed_url);
        feeds.truncate(limit);
        Ok(feeds)
    }

    /// Generates structured tweet suggestions for the supplied content and guidance.
    ///
    /// # Errors
    ///
    /// Returns an error when provider routing, model execution, decoding, or validation fails.
    pub async fn generate_tweet_suggestions(
        &self,
        content_context: &str,
        guidance: Option<&str>,
        creativity: u8,
        length: &str,
        provider: Option<&str>,
    ) -> Result<GeneratedTweetSuggestions, ContentMiscGatewayError> {
        let model_spec = if provider == Some("anthropic") {
            self.anthropic_tweet_model.clone()
        } else {
            self.tweet_model.clone()
        };
        let schema = schema_for!(GeneratedTweetDocument);
        let prompt = format!(
            "Create exactly three distinct tweet suggestions about the content below. Length preference: {length}. Creativity from 1 to 10: {creativity}. Optional user guidance: {}. Include the source URL from the content in each tweet. Return only the required JSON object.\n\n{content_context}",
            guidance.unwrap_or("none")
        );
        let outcome = self
            .agent_engine
            .run(
                AgentRequest {
                    feature: "tweet_suggestions".to_owned(),
                    model_spec,
                    system_prompt: concat!(
                        "You write accurate, useful social posts grounded only in supplied Newsly content. ",
                        "Do not invent claims. Exactly three suggestions are required. Keep style labels short."
                    )
                    .to_owned(),
                    user_prompt: prompt,
                    transcript: NewslyTranscript::default(),
                    response_contract: ResponseContract::JsonSchema {
                        name: "tweet_suggestions".to_owned(),
                        schema,
                        strict: true,
                        validation_retries: 1,
                    },
                    tools: Vec::new(),
                    tool_policy: ToolPolicy {
                        allowed: BTreeSet::new(),
                        require_tool: false,
                        allow_parallel_calls: false,
                    },
                    limits: AgentLimits {
                        request_limit: Some(2),
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
        let mut document: GeneratedTweetDocument = serde_json::from_str(&outcome.output_text)
            .map_err(|error| ContentMiscGatewayError::InvalidTweetOutput(error.to_string()))?;
        document.suggestions.sort_by_key(|suggestion| suggestion.id);
        validate_tweets(&document.suggestions)?;
        Ok(GeneratedTweetSuggestions {
            model: outcome.model_name,
            suggestions: document.suggestions,
        })
    }

    /// Refreshes discussion content from its configured external platform.
    ///
    /// # Errors
    ///
    /// Returns an error when discussion identity is invalid or the external fetch fails.
    pub async fn refresh_discussion(
        &self,
        platform: Option<&str>,
        discussion_url: Option<&str>,
        external_id: Option<&str>,
    ) -> Result<DiscussionRefreshResult, ContentMiscGatewayError> {
        crate::discussion_fetch::refresh_discussion(
            &self.client,
            platform,
            discussion_url,
            external_id,
        )
        .await
    }

    /// Generates and validates a discussion summary from the supplied prompt.
    ///
    /// # Errors
    ///
    /// Returns an error when model execution, response decoding, or validation fails.
    pub async fn summarize_discussion(
        &self,
        prompt: &str,
        merge: bool,
        external_discussion_url: Option<&str>,
    ) -> Result<GeneratedDiscussionSummary, ContentMiscGatewayError> {
        if prompt.trim().is_empty() {
            return Err(ContentMiscGatewayError::InvalidDiscussionSummary(
                "discussion summary input cannot be empty".to_owned(),
            ));
        }
        let system_prompt = if merge {
            concat!(
                "You are an expert community-discussion analyst. Update an existing Hacker News ",
                "or Reddit discussion summary from new or changed comments. Return a complete ",
                "structured summary, not a patch. Ground new claims only in the supplied prior ",
                "summary and changed comments. Preserve still-useful links, named entities, ",
                "numbers, and technical terms. If changes are low-signal, keep the prior synthesis."
            )
        } else {
            concat!(
                "You are an expert community-discussion analyst. Read a Hacker News or Reddit ",
                "thread and produce a concise structured summary. Prioritize expert corrections, ",
                "dissent, practical experience, surprising details, and useful commenter links. ",
                "Ground everything only in the supplied comments and metadata. Preserve named ",
                "entities, numbers, and technical terms."
            )
        };
        let outcome = self
            .agent_engine
            .run(
                AgentRequest {
                    feature: "news_discussions".to_owned(),
                    model_spec: self.discussion_model.clone(),
                    system_prompt: system_prompt.to_owned(),
                    user_prompt: prompt.to_owned(),
                    transcript: NewslyTranscript::default(),
                    response_contract: ResponseContract::JsonSchema {
                        name: "discussion_summary".to_owned(),
                        schema: schema_for!(DiscussionSummaryArtifact),
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
                        output_token_limit: Some(3_000),
                        deadline: Duration::from_secs(120),
                    },
                    provider_parameters: Map::new(),
                },
                Arc::new(NoTools),
                Arc::new(NoEvents),
            )
            .await
            .map_err(|error| ContentMiscGatewayError::DiscussionGeneration(error.to_string()))?;
        let value = match outcome.structured_output {
            Some(value) => value,
            None => serde_json::from_str(&outcome.output_text).map_err(|error| {
                ContentMiscGatewayError::InvalidDiscussionSummary(error.to_string())
            })?,
        };
        let summary = serde_json::from_value::<DiscussionSummaryArtifact>(value)
            .map_err(|error| ContentMiscGatewayError::InvalidDiscussionSummary(error.to_string()))?
            .normalize_and_validate(external_discussion_url)?;
        let summary_json = serde_json::to_value(&summary)?;
        Ok(GeneratedDiscussionSummary {
            summary,
            summary_json,
            provider: model_provider_name(&self.discussion_model).to_owned(),
            model: outcome.model_name,
            usage: outcome.usage,
            provider_response_id: outcome.provider_response_id,
        })
    }

    /// Synthesizes a narration as an MP3 payload.
    ///
    /// # Errors
    ///
    /// Returns an error when narration input is invalid or the synthesis provider fails.
    pub async fn synthesize_narration_mp3(
        &self,
        narration_text: &str,
    ) -> Result<Vec<u8>, ContentMiscGatewayError> {
        let api_key = self
            .elevenlabs_api_key
            .as_ref()
            .ok_or(ContentMiscGatewayError::NarrationUnavailable)?;
        let voice_id = self
            .elevenlabs_voice_id
            .as_deref()
            .ok_or(ContentMiscGatewayError::NarrationUnavailable)?;
        let mut endpoint = self.elevenlabs_api_base.clone();
        {
            let mut path = endpoint
                .path_segments_mut()
                .map_err(|()| ContentMiscGatewayError::NarrationUnavailable)?;
            path.pop_if_empty();
            path.extend(["v1", "text-to-speech", voice_id]);
        }
        endpoint
            .query_pairs_mut()
            .append_pair("output_format", &self.elevenlabs_output_format);
        let response = self
            .client
            .post(endpoint)
            .header("xi-api-key", api_key.expose_secret())
            .header(reqwest::header::ACCEPT, "audio/mpeg")
            .json(&ElevenLabsRequest {
                text: narration_text,
                model_id: &self.elevenlabs_model,
                voice_settings: ElevenLabsVoiceSettings {
                    speed: self.elevenlabs_speed,
                },
            })
            .send()
            .await?
            .error_for_status()?
            .bytes()
            .await?
            .to_vec();
        if response.is_empty() {
            return Err(ContentMiscGatewayError::EmptyNarrationAudio);
        }
        Ok(response)
    }
}

#[derive(Debug, Deserialize)]
struct ItunesSearchResponse {
    #[serde(default)]
    results: Vec<ItunesSearchResult>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct ItunesSearchResult {
    track_name: Option<String>,
    collection_name: Option<String>,
    artist_name: Option<String>,
    episode_url: Option<String>,
    track_view_url: Option<String>,
    feed_url: Option<String>,
    description: Option<String>,
    release_date: Option<String>,
}

#[derive(Debug, Serialize)]
struct ElevenLabsRequest<'a> {
    text: &'a str,
    model_id: &'a str,
    voice_settings: ElevenLabsVoiceSettings,
}

#[derive(Debug, Serialize)]
struct ElevenLabsVoiceSettings {
    speed: f32,
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
                "tweet suggestions do not expose tool {}",
                call.name
            )))
        })
    }
}

fn validate_tweets(
    suggestions: &[GeneratedTweetSuggestion],
) -> Result<(), ContentMiscGatewayError> {
    if suggestions.len() != 3 {
        return Err(ContentMiscGatewayError::InvalidTweetOutput(
            "exactly three suggestions are required".to_owned(),
        ));
    }
    for (index, suggestion) in suggestions.iter().enumerate() {
        let expected = u8::try_from(index + 1).unwrap_or(u8::MAX);
        if suggestion.id != expected || suggestion.text.trim().is_empty() {
            return Err(ContentMiscGatewayError::InvalidTweetOutput(
                "suggestion ids must be 1, 2, 3 and text must be non-empty".to_owned(),
            ));
        }
    }
    Ok(())
}

fn dedupe_podcast_hits(hits: &mut Vec<PodcastEpisodeHit>) {
    hits.sort_by(|left, right| left.episode_url.cmp(&right.episode_url));
    hits.dedup_by(|left, right| left.episode_url == right.episode_url);
}

fn stable_feed_id(feed_url: &str) -> String {
    use sha2::{Digest, Sha256};
    let digest = Sha256::digest(feed_url.as_bytes());
    let mut encoded = String::with_capacity(digest.len() * 2);
    for byte in digest {
        write!(&mut encoded, "{byte:02x}").expect("writing to a String cannot fail");
    }
    format!("feed-{}", &encoded[..20])
}

fn is_http_url(value: &str) -> bool {
    Url::parse(value)
        .is_ok_and(|url| matches!(url.scheme(), "http" | "https") && url.host().is_some())
}

fn clean(value: Option<String>) -> Option<String> {
    value.and_then(|value| {
        let trimmed = value.trim();
        (!trimmed.is_empty()).then(|| trimmed.to_owned())
    })
}

fn clean_text(value: &str) -> String {
    value.split_whitespace().collect::<Vec<_>>().join(" ")
}

fn clean_optional_bounded(value: Option<String>, max_chars: usize) -> Option<String> {
    value.and_then(|value| {
        let cleaned = clean_text(&value);
        (!cleaned.is_empty()).then(|| cleaned.chars().take(max_chars).collect())
    })
}

fn model_provider_name(model_spec: &str) -> &'static str {
    let value = model_spec.trim();
    match value.split_once(':').map(|(provider, _)| provider) {
        Some("openai") => "openai",
        Some("anthropic") => "anthropic",
        Some("google" | "google-gla") => "google",
        Some("openrouter") => "openrouter",
        _ if value.starts_with("claude-") => "anthropic",
        _ if value.starts_with("gemini-") => "google",
        _ => "openai",
    }
}

fn clean_env(name: &str) -> Option<String> {
    env::var(name)
        .ok()
        .map(|value| value.trim().to_owned())
        .filter(|value| !value.is_empty())
}

fn secret_env(name: &str) -> Option<SecretString> {
    clean_env(name).map(SecretString::from)
}

fn env_u64(name: &str, default: u64) -> u64 {
    env::var(name)
        .ok()
        .and_then(|value| value.parse::<u64>().ok())
        .unwrap_or(default)
}

#[derive(Debug, Error)]
pub enum ContentMiscGatewayError {
    #[error("content provider HTTP request failed")]
    Http(#[from] reqwest::Error),
    #[error("content provider URL is invalid")]
    Url(String),
    #[error("tweet provider configuration failed")]
    TweetConfiguration(#[from] crate::RigAgentEngineError),
    #[error("tweet generation failed")]
    TweetGeneration(#[from] AgentRuntimeError),
    #[error("tweet output serialization failed")]
    Json(#[from] serde_json::Error),
    #[error("tweet output is invalid: {0}")]
    InvalidTweetOutput(String),
    #[error("discussion platform is not supported")]
    UnsupportedDiscussionPlatform,
    #[error("discussion external identity is missing")]
    DiscussionIdentityMissing,
    #[error("discussion fetch failed: {message}")]
    DiscussionFetch { message: String, retryable: bool },
    #[error("discussion is unavailable ({status}): {message}")]
    DiscussionUnavailable { status: String, message: String },
    #[error("discussion summary generation failed: {0}")]
    DiscussionGeneration(String),
    #[error("discussion summary output is invalid: {0}")]
    InvalidDiscussionSummary(String),
    #[error("narration TTS is not configured")]
    NarrationUnavailable,
    #[error("narration provider returned empty audio")]
    EmptyNarrationAudio,
}

impl ContentMiscGatewayError {
    /// Returns a terminal discussion state that should leave refresh rotation.
    pub fn discussion_terminal_status(&self) -> Option<&str> {
        match self {
            Self::DiscussionUnavailable { status, .. } => Some(status),
            _ => None,
        }
    }

    /// Classifies provider failures for the durable queue retry contract.
    pub fn discussion_retryable(&self) -> bool {
        match self {
            Self::DiscussionFetch { retryable, .. } => *retryable,
            Self::DiscussionGeneration(_) | Self::InvalidDiscussionSummary(_) | Self::Json(_) => {
                true
            }
            Self::Http(error) => error.status().is_none_or(|status| {
                status == reqwest::StatusCode::TOO_MANY_REQUESTS || status.is_server_error()
            }),
            Self::UnsupportedDiscussionPlatform
            | Self::DiscussionIdentityMissing
            | Self::DiscussionUnavailable { .. }
            | Self::Url(_)
            | Self::TweetConfiguration(_)
            | Self::TweetGeneration(_)
            | Self::InvalidTweetOutput(_)
            | Self::NarrationUnavailable
            | Self::EmptyNarrationAudio => false,
        }
    }
}
