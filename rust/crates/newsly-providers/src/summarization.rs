use std::collections::BTreeSet;
use std::env;
use std::path::Path;
use std::sync::Arc;
use std::time::Duration;

use chrono::{DateTime, Utc};
use newsly_agent_runtime::{
    AgentEngine, AgentEvent, AgentEventSink, AgentLimits, AgentRequest, AgentRuntimeError,
    BoxToolFuture, NewslyTranscript, ProviderUsage, ResponseContract, ToolCall, ToolExecutor,
    ToolPolicy,
};
use schemars::JsonSchema;
use secrecy::SecretString;
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};
use thiserror::Error;

use crate::{OpenRouterPrivacyPolicy, ProviderCredentials, RigAgentEngine};

const DEFAULT_SUMMARIZATION_MODEL: &str = "openai:gpt-5.6-terra";
const MAX_SUMMARIZATION_PAYLOAD_CHARS: usize = 220_000;
const CONTENT_TRUNCATION_MARKER: &str = "\n\n[... CONTENT TRUNCATED ...]\n\n";

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SummarizationSource {
    pub content_id: i64,
    pub content_type: String,
    pub title: Option<String>,
    pub url: String,
    pub source_name: Option<String>,
    pub platform: Option<String>,
    pub publication_date: Option<String>,
    pub metadata: Value,
    pub text: String,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum ArtifactType {
    Argument,
    MentalModel,
    Playbook,
    Portrait,
    Briefing,
    Walkthrough,
    Findings,
}

impl ArtifactType {
    const fn ask(self) -> ArtifactAsk {
        match self {
            Self::Argument => ArtifactAsk::Judge,
            Self::MentalModel => ArtifactAsk::Learn,
            Self::Playbook => ArtifactAsk::Copy,
            Self::Portrait => ArtifactAsk::Absorb,
            Self::Briefing => ArtifactAsk::Track,
            Self::Walkthrough => ArtifactAsk::Try,
            Self::Findings => ArtifactAsk::Update,
        }
    }

    const fn as_str(self) -> &'static str {
        match self {
            Self::Argument => "argument",
            Self::MentalModel => "mental_model",
            Self::Playbook => "playbook",
            Self::Portrait => "portrait",
            Self::Briefing => "briefing",
            Self::Walkthrough => "walkthrough",
            Self::Findings => "findings",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum ArtifactAsk {
    Judge,
    Learn,
    Copy,
    Absorb,
    Track,
    Try,
    Update,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct ArtifactQuote {
    pub text: String,
    pub attribution: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct ArtifactKeyPoint {
    pub heading: String,
    pub content: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema, Default)]
#[serde(deny_unknown_fields)]
pub struct SharedArtifactExtras {
    #[serde(default)]
    pub evidence: Vec<String>,
    #[serde(default)]
    pub mental_model: Vec<String>,
    #[serde(default)]
    pub counter_arguments: Vec<String>,
    #[serde(default)]
    pub supporting_arguments: Vec<String>,
}

macro_rules! artifact_extras {
    ($name:ident { $($field:ident),+ $(,)? }) => {
        #[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
        #[serde(deny_unknown_fields)]
        pub struct $name {
            $(pub $field: String,)+
            #[serde(flatten)]
            pub shared: SharedArtifactExtras,
        }
    };
}

artifact_extras!(ArgumentExtras {
    thesis,
    counterpoint
});
artifact_extras!(MentalModelExtras {
    what_it_explains,
    when_to_use_it,
});
artifact_extras!(PlaybookExtras { situation, outcome });
artifact_extras!(PortraitExtras {
    background,
    current_focus,
});
artifact_extras!(FindingsExtras {
    question,
    method,
    limits,
});

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct BriefingTimelineItem {
    pub when: String,
    pub what: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct BriefingKeyActor {
    pub name: String,
    pub stake: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct BriefingExtras {
    pub timeline: Vec<BriefingTimelineItem>,
    pub key_actors: Vec<BriefingKeyActor>,
    pub what_to_watch: String,
    #[serde(flatten)]
    pub shared: SharedArtifactExtras,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct WalkthroughExtras {
    pub what_youll_make: String,
    pub prereqs: Vec<String>,
    pub time_or_cost: String,
    #[serde(flatten)]
    pub shared: SharedArtifactExtras,
}

macro_rules! artifact_payload {
    ($name:ident, $extras:ty) => {
        #[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
        #[serde(deny_unknown_fields)]
        pub struct $name {
            #[serde(default, skip_serializing_if = "Option::is_none")]
            pub overview: Option<String>,
            pub quotes: Vec<ArtifactQuote>,
            pub key_points: Vec<ArtifactKeyPoint>,
            pub takeaway: String,
            pub extras: $extras,
        }
    };
}

artifact_payload!(ArgumentPayload, ArgumentExtras);
artifact_payload!(MentalModelPayload, MentalModelExtras);
artifact_payload!(PlaybookPayload, PlaybookExtras);
artifact_payload!(PortraitPayload, PortraitExtras);
artifact_payload!(BriefingPayload, BriefingExtras);
artifact_payload!(WalkthroughPayload, WalkthroughExtras);
artifact_payload!(FindingsPayload, FindingsExtras);

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(tag = "type", rename_all = "snake_case", deny_unknown_fields)]
pub enum LongformArtifactBody {
    Argument { payload: ArgumentPayload },
    MentalModel { payload: MentalModelPayload },
    Playbook { payload: PlaybookPayload },
    Portrait { payload: PortraitPayload },
    Briefing { payload: BriefingPayload },
    Walkthrough { payload: WalkthroughPayload },
    Findings { payload: FindingsPayload },
}

impl LongformArtifactBody {
    const fn artifact_type(&self) -> ArtifactType {
        match self {
            Self::Argument { .. } => ArtifactType::Argument,
            Self::MentalModel { .. } => ArtifactType::MentalModel,
            Self::Playbook { .. } => ArtifactType::Playbook,
            Self::Portrait { .. } => ArtifactType::Portrait,
            Self::Briefing { .. } => ArtifactType::Briefing,
            Self::Walkthrough { .. } => ArtifactType::Walkthrough,
            Self::Findings { .. } => ArtifactType::Findings,
        }
    }

    fn quotes_mut(&mut self) -> &mut Vec<ArtifactQuote> {
        match self {
            Self::Argument { payload } => &mut payload.quotes,
            Self::MentalModel { payload } => &mut payload.quotes,
            Self::Playbook { payload } => &mut payload.quotes,
            Self::Portrait { payload } => &mut payload.quotes,
            Self::Briefing { payload } => &mut payload.quotes,
            Self::Walkthrough { payload } => &mut payload.quotes,
            Self::Findings { payload } => &mut payload.quotes,
        }
    }

    fn overview_mut(&mut self) -> &mut Option<String> {
        match self {
            Self::Argument { payload } => &mut payload.overview,
            Self::MentalModel { payload } => &mut payload.overview,
            Self::Playbook { payload } => &mut payload.overview,
            Self::Portrait { payload } => &mut payload.overview,
            Self::Briefing { payload } => &mut payload.overview,
            Self::Walkthrough { payload } => &mut payload.overview,
            Self::Findings { payload } => &mut payload.overview,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct SourceContext {
    pub url: String,
    pub source_name: Option<String>,
    pub publication_date: Option<String>,
    pub platform: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct SelectionTrace {
    pub source_hint: String,
    pub candidates: Vec<ArtifactType>,
    pub selected: ArtifactType,
    pub reason: String,
    pub confidence: f64,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct FeedPreview {
    pub title: String,
    pub one_line: String,
    pub preview_bullets: Vec<String>,
    pub reason_to_read: String,
    pub artifact_type: ArtifactType,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct LongformArtifactEnvelope {
    pub title: String,
    pub one_line: String,
    pub ask: ArtifactAsk,
    pub artifact: LongformArtifactBody,
    pub generated_at: DateTime<Utc>,
    pub source_context: SourceContext,
    pub selection_trace: SelectionTrace,
    pub feed_preview: FeedPreview,
}

impl LongformArtifactEnvelope {
    fn normalize_and_validate(
        mut self,
        source: &SummarizationSource,
        hint: &ArtifactSourceHint,
    ) -> Result<Self, SummarizationGatewayError> {
        let selected = self.artifact.artifact_type();
        if !hint.candidates.contains(&selected) {
            return Err(SummarizationGatewayError::InvalidArtifact(format!(
                "selected artifact type {} is not allowed for {}",
                selected.as_str(),
                hint.source_hint
            )));
        }
        if !(0.0..=1.0).contains(&self.selection_trace.confidence) {
            return Err(SummarizationGatewayError::InvalidArtifact(
                "selection confidence must be between zero and one".to_owned(),
            ));
        }
        if self.title.trim().len() < 5
            || self.one_line.trim().len() < 20
            || self.selection_trace.reason.trim().len() < 10
        {
            return Err(SummarizationGatewayError::InvalidArtifact(
                "artifact title, one-line preview, or selection reason is too short".to_owned(),
            ));
        }

        let quotes = self.artifact.quotes_mut();
        quotes.retain(|quote| quote.text.trim().chars().count() >= 20);
        if quotes.len() < 2 || quotes.len() > 5 {
            return Err(SummarizationGatewayError::InvalidArtifact(
                "artifact must contain two to five grounded quotes".to_owned(),
            ));
        }
        *self.artifact.overview_mut() = None;

        // Provenance and discriminator fields are server-owned. The model chooses only the
        // artifact body; normalizing these fields prevents model output from spoofing source data.
        self.ask = selected.ask();
        self.generated_at = Utc::now();
        self.source_context = SourceContext {
            url: source.url.clone(),
            source_name: source.source_name.clone(),
            publication_date: source.publication_date.clone(),
            platform: source.platform.clone(),
        };
        self.selection_trace
            .source_hint
            .clone_from(&hint.source_hint);
        self.selection_trace.candidates.clone_from(&hint.candidates);
        self.selection_trace.selected = selected;
        self.feed_preview.artifact_type = selected;
        Ok(self)
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct GeneratedLongformSummary {
    pub summary: LongformArtifactEnvelope,
    pub summary_json: Value,
    pub model: String,
    pub usage: ProviderUsage,
    pub provider_response_id: Option<String>,
}

#[derive(Debug, Clone)]
pub struct SummarizationGateway {
    engine: RigAgentEngine,
    model_spec: String,
    deadline: Duration,
}

impl SummarizationGateway {
    /// Creates the production summarization gateway from environment configuration.
    ///
    /// # Errors
    ///
    /// Returns an error when model configuration or provider initialization is invalid.
    pub fn from_env() -> Result<Self, SummarizationGatewayError> {
        let model_spec = env::var("SUMMARIZATION_MODEL")
            .unwrap_or_else(|_| DEFAULT_SUMMARIZATION_MODEL.to_owned());
        let deadline = Duration::from_secs(
            env::var("SUMMARIZATION_TIMEOUT_SECONDS")
                .ok()
                .and_then(|value| value.parse::<u64>().ok())
                .unwrap_or(180)
                .clamp(15, 900),
        );
        Self::from_env_for_model(model_spec, deadline)
    }

    /// Builds an isolated evaluator for an explicit provider/model without exposing Rig types.
    ///
    /// This is used by the authenticated admin comparison surface. Production summarization
    /// continues to use [`Self::from_env`], while both paths share the same credential and
    /// `OpenRouter` privacy policy.
    ///
    /// # Errors
    ///
    /// Returns an error when the model specification or provider initialization is invalid.
    pub fn from_env_for_model(
        model_spec: impl Into<String>,
        deadline: Duration,
    ) -> Result<Self, SummarizationGatewayError> {
        let credentials = ProviderCredentials {
            openai: secret_env("OPENAI_API_KEY"),
            anthropic: secret_env("ANTHROPIC_API_KEY"),
            google: secret_env("GOOGLE_API_KEY").or_else(|| secret_env("GEMINI_API_KEY")),
            openrouter: secret_env("OPENROUTER_API_KEY"),
        };
        let engine = RigAgentEngine::new(credentials, OpenRouterPrivacyPolicy::default())?;
        let model_spec = crate::ModelSpec::parse(&model_spec.into())?.canonical();
        Ok(Self {
            engine,
            model_spec,
            deadline,
        })
    }

    pub fn model_spec(&self) -> &str {
        &self.model_spec
    }

    /// Generates and validates a long-form artifact for one immutable source snapshot.
    ///
    /// # Errors
    ///
    /// Returns an error for empty input or when model execution, decoding, or artifact validation
    /// fails.
    pub async fn summarize(
        &self,
        source: &SummarizationSource,
    ) -> Result<GeneratedLongformSummary, SummarizationGatewayError> {
        if source.text.trim().is_empty() {
            return Err(SummarizationGatewayError::EmptyInput);
        }
        let hint = resolve_artifact_source_hint(source);
        let (system_prompt, user_prompt) = build_prompts(source, &hint);
        let outcome = self
            .engine
            .run(
                AgentRequest {
                    feature: "summarization".to_owned(),
                    model_spec: self.model_spec.clone(),
                    system_prompt,
                    user_prompt,
                    transcript: NewslyTranscript::default(),
                    response_contract: ResponseContract::JsonSchema {
                        name: "longform_artifact_v1".to_owned(),
                        schema: schemars::schema_for!(LongformArtifactEnvelope),
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
                        output_token_limit: Some(6_000),
                        deadline: self.deadline,
                    },
                    provider_parameters: Map::new(),
                },
                Arc::new(NoTools),
                Arc::new(NoEvents),
            )
            .await?;
        let value = outcome
            .structured_output
            .ok_or(SummarizationGatewayError::MissingStructuredOutput)?;
        let summary = serde_json::from_value::<LongformArtifactEnvelope>(value)?
            .normalize_and_validate(source, &hint)?;
        let summary_json = serde_json::to_value(&summary)?;
        Ok(GeneratedLongformSummary {
            summary,
            summary_json,
            model: outcome.model_name,
            usage: outcome.usage,
            provider_response_id: outcome.provider_response_id,
        })
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct ArtifactSourceHint {
    source_hint: String,
    candidates: Vec<ArtifactType>,
}

struct ArtifactSourceContext {
    content_type: String,
    platform: String,
    host: String,
    metadata_content_type: String,
    is_pdf: bool,
}

fn resolve_artifact_source_hint(source: &SummarizationSource) -> ArtifactSourceHint {
    let context = artifact_source_context(source);
    if context.content_type == "news" {
        return hint("news:briefing", &[ArtifactType::Briefing]);
    }
    if context.metadata_content_type == "pdf"
        || context.is_pdf
        || matches!(
            context.host.as_str(),
            "arxiv.org"
                | "huggingface.co"
                | "openreview.net"
                | "paperswithcode.com"
                | "pmc.ncbi.nlm.nih.gov"
                | "nature.com"
        )
    {
        return hint(
            "research:paper",
            &[
                ArtifactType::Findings,
                ArtifactType::MentalModel,
                ArtifactType::Briefing,
            ],
        );
    }
    if context.platform == "github"
        || matches!(
            context.host.as_str(),
            "github.com" | "www.github.com" | "gist.github.com" | "raw.githubusercontent.com"
        )
    {
        return hint(
            "github:repo",
            &[
                ArtifactType::Walkthrough,
                ArtifactType::Playbook,
                ArtifactType::MentalModel,
            ],
        );
    }
    if matches!(
        context.host.as_str(),
        "news.ycombinator.com" | "techmeme.com" | "www.techmeme.com"
    ) || matches!(context.platform.as_str(), "hackernews" | "techmeme")
    {
        return hint("news:event", &[ArtifactType::Briefing]);
    }
    if matches!(context.platform.as_str(), "twitter" | "x")
        || matches!(
            context.host.as_str(),
            "twitter.com" | "www.twitter.com" | "x.com" | "www.x.com"
        )
    {
        return hint(
            "social:announcement",
            &[ArtifactType::Briefing, ArtifactType::Argument],
        );
    }
    if context.content_type == "podcast" {
        return hint(
            "podcast:conversation",
            &[
                ArtifactType::Playbook,
                ArtifactType::Portrait,
                ArtifactType::MentalModel,
            ],
        );
    }
    if context.platform == "substack" || context.host.ends_with(".substack.com") {
        return hint(
            "substack:analysis",
            &[
                ArtifactType::Argument,
                ArtifactType::MentalModel,
                ArtifactType::Findings,
            ],
        );
    }
    hint(
        "article:general",
        &[
            ArtifactType::Argument,
            ArtifactType::MentalModel,
            ArtifactType::Briefing,
            ArtifactType::Playbook,
        ],
    )
}

fn artifact_source_context(source: &SummarizationSource) -> ArtifactSourceContext {
    let platform = source
        .platform
        .as_deref()
        .or_else(|| source.metadata.get("platform").and_then(Value::as_str))
        .unwrap_or_default()
        .trim()
        .to_ascii_lowercase();
    let parsed_url = reqwest::Url::parse(source.url.trim()).ok();
    let host = parsed_url
        .as_ref()
        .and_then(|value| value.host_str().map(str::to_owned))
        .unwrap_or_default();
    let is_pdf = parsed_url
        .as_ref()
        .and_then(|url| Path::new(url.path()).extension())
        .is_some_and(|extension| extension.eq_ignore_ascii_case("pdf"));
    ArtifactSourceContext {
        content_type: source.content_type.trim().to_ascii_lowercase(),
        platform,
        host,
        metadata_content_type: source
            .metadata
            .get("content_type")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .trim()
            .to_ascii_lowercase(),
        is_pdf,
    }
}

fn hint(source_hint: &str, candidates: &[ArtifactType]) -> ArtifactSourceHint {
    ArtifactSourceHint {
        source_hint: source_hint.to_owned(),
        candidates: candidates.to_vec(),
    }
}

fn build_prompts(source: &SummarizationSource, hint: &ArtifactSourceHint) -> (String, String) {
    let candidate_guidance = hint
        .candidates
        .iter()
        .map(|candidate| format!("- {}: {}", candidate.as_str(), guidance(*candidate)))
        .collect::<Vec<_>>()
        .join("\n");
    let extras_guidance = hint
        .candidates
        .iter()
        .map(|candidate| format!("- {}: {}", candidate.as_str(), extras_hint(*candidate)))
        .collect::<Vec<_>>()
        .join("\n");
    let candidates_json = hint
        .candidates
        .iter()
        .map(|candidate| format!("\"{}\"", candidate.as_str()))
        .collect::<Vec<_>>()
        .join(", ");
    let system_prompt = format!(
        r"You are Newsly's long-form artifact generator.

Produce one typed artifact from the source content, not a generic summary. Choose exactly one
artifact type from the candidate list, then generate the matching artifact in the same JSON
response. The choice belongs in selection_trace.

Candidate artifact types:
{candidate_guidance}

Every payload has quotes (2-5 direct source quotes), type-specific extras, key_points (4-8 headed
items with substantive content), and a one-sentence takeaway. Do not create an overview or
narrative lede. Shared extras are evidence, mental_model, counter_arguments, and
supporting_arguments; use an empty array when the source does not support a field.

Allowed extras shapes:
{extras_guidance}

Return only JSON matching the supplied schema. The selected type must be one of
[{candidates_json}]. ask must match its type. Preserve names, numbers, dates, and technical terms.
Never invent quotes. Use null when attribution is unavailable. selection_trace.source_hint is
{source_hint}. No markdown outside JSON.",
        source_hint = hint.source_hint,
    );
    let clipped = clip_payload(&source.text, MAX_SUMMARIZATION_PAYLOAD_CHARS);
    let user_prompt = format!(
        "Source metadata:\nTitle: {}\nURL: {}\nSource: {}\nPlatform: {}\nPublication date: {}\nSource hint: {}\nCandidates: [{}]\n\nSource content:\n\n{}",
        source.title.as_deref().unwrap_or("unknown"),
        source.url,
        source.source_name.as_deref().unwrap_or("unknown"),
        source.platform.as_deref().unwrap_or("unknown"),
        source.publication_date.as_deref().unwrap_or("unknown"),
        hint.source_hint,
        candidates_json,
        clipped,
    );
    (system_prompt, user_prompt)
}

fn guidance(artifact_type: ArtifactType) -> &'static str {
    match artifact_type {
        ArtifactType::Argument => {
            "Essays and analysis making a claim; key points follow the author's reasons."
        }
        ArtifactType::MentalModel => {
            "Explainers and frameworks; key points are parts or stages of the model."
        }
        ArtifactType::Playbook => {
            "Tactical operator stories; key points are phases in chronological order."
        }
        ArtifactType::Portrait => {
            "Profiles and interviews; key points are themes in the person's worldview."
        }
        ArtifactType::Briefing => {
            "News events and updates; key points are the major beats of what happened."
        }
        ArtifactType::Walkthrough => {
            "Tutorials and build guides; key points are steps in execution order."
        }
        ArtifactType::Findings => {
            "Research and reports; key points are findings in order of significance."
        }
    }
}

fn extras_hint(artifact_type: ArtifactType) -> &'static str {
    match artifact_type {
        ArtifactType::Argument => "thesis, counterpoint, and the four shared arrays",
        ArtifactType::MentalModel => "what_it_explains, when_to_use_it, and the four shared arrays",
        ArtifactType::Playbook => "situation, outcome, and the four shared arrays",
        ArtifactType::Portrait => "background, current_focus, and the four shared arrays",
        ArtifactType::Briefing => "timeline, key_actors, what_to_watch, and the four shared arrays",
        ArtifactType::Walkthrough => {
            "what_youll_make, prereqs, time_or_cost, and the four shared arrays"
        }
        ArtifactType::Findings => "question, method, limits, and the four shared arrays",
    }
}

fn clip_payload(payload: &str, max_chars: usize) -> String {
    if payload.chars().count() <= max_chars {
        return payload.to_owned();
    }
    let remaining = max_chars.saturating_sub(CONTENT_TRUNCATION_MARKER.chars().count());
    let head_count = remaining / 2;
    let tail_count = remaining.saturating_sub(head_count);
    let head = payload.chars().take(head_count).collect::<String>();
    let tail = payload
        .chars()
        .rev()
        .take(tail_count)
        .collect::<String>()
        .chars()
        .rev()
        .collect::<String>();
    format!(
        "{}{CONTENT_TRUNCATION_MARKER}{}",
        head.trim_end(),
        tail.trim_start()
    )
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
                "summarization does not expose tools".to_owned(),
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
pub enum SummarizationGatewayError {
    #[error("summarization input is empty")]
    EmptyInput,
    #[error("summarization returned no structured output")]
    MissingStructuredOutput,
    #[error("invalid long-form artifact: {0}")]
    InvalidArtifact(String),
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
    use super::*;

    fn source(content_type: &str, url: &str, platform: Option<&str>) -> SummarizationSource {
        SummarizationSource {
            content_id: 7,
            content_type: content_type.to_owned(),
            title: Some("A useful source".to_owned()),
            url: url.to_owned(),
            source_name: None,
            platform: platform.map(str::to_owned),
            publication_date: None,
            metadata: Value::Object(Map::new()),
            text: "source text".to_owned(),
        }
    }

    #[test]
    fn source_routing_preserves_the_wire_contract() {
        assert_eq!(
            resolve_artifact_source_hint(&source("news", "https://example.com", None)).candidates,
            vec![ArtifactType::Briefing]
        );
        assert_eq!(
            resolve_artifact_source_hint(&source(
                "article",
                "https://github.com/newsly/backend",
                None,
            ))
            .source_hint,
            "github:repo"
        );
        assert_eq!(
            resolve_artifact_source_hint(&source("podcast", "https://example.com", None))
                .candidates,
            vec![
                ArtifactType::Playbook,
                ArtifactType::Portrait,
                ArtifactType::MentalModel,
            ]
        );
    }

    #[test]
    fn payload_clipping_preserves_head_and_tail() {
        let payload = "a".repeat(100) + &"z".repeat(100);
        let clipped = clip_payload(&payload, 80);
        assert_eq!(clipped.chars().count(), 80);
        assert!(clipped.starts_with('a'));
        assert!(clipped.ends_with('z'));
        assert!(clipped.contains("CONTENT TRUNCATED"));
    }
}
