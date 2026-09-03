use std::collections::BTreeSet;
use std::env;
use std::sync::Arc;
use std::time::Duration;

use newsly_agent_runtime::{
    AgentEngine, AgentLimits, AgentOutcome, AgentRequest, AgentRuntimeError, MessagePart,
    MessageRole, NewslyMessage, NewslyTranscript, RequestPart, ResponseContract, ToolPolicy,
};
use newsly_db::{ChatTaskSnapshot, ChatTurnKind};
use newsly_e2b::{ControlPlaneConfig, DirectE2bProvider, E2bError, FileLimits};
use newsly_providers::{
    FeedValidationError, FeedValidator, IntegrationTokenCipher, ModelProvider, ModelSpec,
    OnboardingGateway, OnboardingGatewayError, OpenRouterPrivacyPolicy, ProviderCredentials,
    RigAgentEngine, RigAgentEngineError,
};
use newsly_queue::QueueKernel;
use reqwest::Url;
use secrecy::SecretString;
use serde_json::{Map, Value};
use sqlx::PgPool;
use thiserror::Error;
use tokio::time::Instant;
use tokio_util::sync::CancellationToken;

use crate::content_body_store::{ContentBodyStore, ContentBodyStoreError};
use crate::share_actions::{ShareActionAgentConfig, ShareActionAgentConfigError};
use crate::task_sandbox::{TaskSandboxConfig, TaskSandboxError, TaskSandboxOwner};
use crate::task_tools::ExaSearchClient;

use super::events::ChatEvents;
use super::prompts::{ChatPromptError, system_prompt};
use super::routing::{article_tools, route_assistant_turn};
use super::tools::{ChatToolDependencies, ChatToolExecutor};

const DEFAULT_EXA_API_BASE: &str = "https://api.exa.ai/search";
const CONTEXT_WINDOW_TOKENS: usize = 64_000;
const TOOL_SCHEMA_RESERVE_TOKENS: usize = 4_000;
const CHAT_HISTORY_MAX_TOKENS: usize = 16_000;
const TOKEN_CHARS_PER_TOKEN: usize = 4;

#[derive(Debug, Clone)]
pub struct ChatAgentConfig {
    pub sandbox_timeout: Duration,
    pub request_limit: u32,
    pub tool_call_limit: u32,
    pub max_output_chars: usize,
    pub history_message_limit: i64,
    pub output_token_limit: u64,
    pub deep_research_poll_interval: Duration,
    pub deep_research_max_polls: u32,
}

impl ChatAgentConfig {
    pub fn from_env() -> Result<(Self, ShareActionAgentConfig), ChatAgentBuildError> {
        let shared = ShareActionAgentConfig::from_env()?;
        let history_message_limit = bounded_i64("CHAT_HISTORY_MESSAGE_LIMIT", 200, 2, 200)?;
        let output_token_limit = bounded_u64("CHAT_OUTPUT_TOKEN_LIMIT", 8_000, 256, 100_000)?;
        let deep_research_poll_seconds =
            bounded_u64("DEEP_RESEARCH_POLL_INTERVAL_SECONDS", 2, 1, 60)?;
        let deep_research_max_polls = bounded_u64("DEEP_RESEARCH_MAX_POLLS", 300, 1, 3_600)?;
        Ok((
            Self {
                sandbox_timeout: shared.sandbox_timeout,
                request_limit: shared.request_limit,
                tool_call_limit: shared.tool_call_limit,
                max_output_chars: shared.max_output_chars,
                history_message_limit,
                output_token_limit,
                deep_research_poll_interval: Duration::from_secs(deep_research_poll_seconds),
                deep_research_max_polls: u32::try_from(deep_research_max_polls)
                    .map_err(|_| ChatAgentBuildError::Invalid("DEEP_RESEARCH_MAX_POLLS"))?,
            },
            shared,
        ))
    }
}

#[derive(Debug, Clone)]
pub struct ChatAgentRuntime {
    pool: PgPool,
    queue: QueueKernel,
    provider: Arc<DirectE2bProvider>,
    lifecycle: TaskSandboxOwner,
    exa: ExaSearchClient,
    onboarding: OnboardingGateway,
    feed_validator: FeedValidator,
    body_store: ContentBodyStore,
    credentials: ProviderCredentials,
    cipher: Option<IntegrationTokenCipher>,
    openrouter_policy: OpenRouterPrivacyPolicy,
    config: ChatAgentConfig,
}

impl ChatAgentRuntime {
    pub fn from_env(pool: PgPool, queue: QueueKernel) -> Result<Self, ChatAgentBuildError> {
        let (config, shared) = ChatAgentConfig::from_env()?;
        let e2b_key = secret_env("LLM_TASK_SANDBOX_E2B_API_KEY")
            .or_else(|| secret_env("E2B_API_KEY"))
            .ok_or(ChatAgentBuildError::MissingE2bKey)?;
        let provider = Arc::new(DirectE2bProvider::new(
            ControlPlaneConfig::production(e2b_key.clone())?,
            FileLimits {
                upload_bytes: 1_000_000,
                download_bytes: 1_000_000,
            },
        )?);
        let lifecycle = TaskSandboxOwner::new(
            pool.clone(),
            Arc::clone(&provider),
            TaskSandboxConfig {
                template_id: shared.template_id.clone(),
                template_revision: shared.template_revision,
                sandbox_timeout: shared.sandbox_timeout,
            },
        )?;
        let endpoint = env::var("EXA_API_BASE_URL")
            .ok()
            .map(|value| value.trim_end_matches('/').to_owned())
            .map_or_else(
                || Url::parse(DEFAULT_EXA_API_BASE),
                |value| Url::parse(&format!("{value}/search")),
            )
            .map_err(|_| ChatAgentBuildError::Invalid("EXA_API_BASE_URL"))?;
        let exa =
            ExaSearchClient::new(secret_env("EXA_API_KEY"), endpoint, Duration::from_secs(60))?;
        let onboarding = OnboardingGateway::from_env()?;
        let feed_validator = FeedValidator::new();
        let credentials = ProviderCredentials {
            openai: secret_env("OPENAI_API_KEY"),
            anthropic: secret_env("ANTHROPIC_API_KEY"),
            google: secret_env("GOOGLE_API_KEY").or_else(|| secret_env("GEMINI_API_KEY")),
            openrouter: secret_env("OPENROUTER_API_KEY"),
        };
        let cipher = secret_env("X_TOKEN_ENCRYPTION_KEY")
            .as_ref()
            .map(IntegrationTokenCipher::new)
            .transpose()?;
        Ok(Self {
            pool,
            queue,
            provider,
            lifecycle,
            exa,
            onboarding,
            feed_validator,
            body_store: ContentBodyStore::from_env()?,
            credentials,
            cipher,
            openrouter_policy: OpenRouterPrivacyPolicy::default(),
            config,
        })
    }

    pub const fn config(&self) -> &ChatAgentConfig {
        &self.config
    }

    pub(super) async fn content_body(
        &self,
        snapshot: &ChatTaskSnapshot,
    ) -> Result<Option<String>, ChatAgentError> {
        match snapshot
            .content
            .as_ref()
            .and_then(|content| content.body_storage_key.as_deref())
        {
            Some(key) => self.body_store.get_text(key).await.map_err(Into::into),
            None => Ok(None),
        }
    }

    pub(super) async fn run(
        &self,
        snapshot: &ChatTaskSnapshot,
        cancellation: CancellationToken,
    ) -> Result<ChatAgentRun, ChatAgentError> {
        if snapshot.context.kind == ChatTurnKind::DeepResearch {
            return Err(ChatAgentError::UnsupportedKind);
        }
        let content_body = self.content_body(snapshot).await?;
        let (instruction, allowed_tools) = match snapshot.context.kind {
            ChatTurnKind::Assistant => {
                let context = snapshot
                    .context
                    .screen_context
                    .as_ref()
                    .ok_or(ChatAgentError::MissingScreenContext)?;
                let route = route_assistant_turn(&snapshot.context.user_prompt, context)?;
                (route.instruction, route.allowed_tools)
            }
            ChatTurnKind::Article | ChatTurnKind::Council => (None, article_tools()),
            ChatTurnKind::DeepResearch => unreachable!(),
        };
        let engine = RigAgentEngine::new(
            self.credentials_for(snapshot)?,
            self.openrouter_policy.clone(),
        )?;
        let deadline = Instant::now()
            .checked_add(self.config.sandbox_timeout)
            .ok_or(ChatAgentError::Deadline)?;
        let tools = Arc::new(ChatToolExecutor::new(
            ChatToolDependencies {
                pool: self.pool.clone(),
                queue: self.queue.clone(),
                provider: Arc::clone(&self.provider),
                lifecycle: self.lifecycle.clone(),
                exa: self.exa.clone(),
                onboarding: self.onboarding.clone(),
                feed_validator: self.feed_validator.clone(),
                body_store: self.body_store.clone(),
                max_output_chars: self.config.max_output_chars,
                deadline,
                cancellation: cancellation.child_token(),
            },
            snapshot.clone(),
        ));
        let events = Arc::new(ChatEvents::new(
            self.pool.clone(),
            snapshot.message_id,
            snapshot.stream_generation,
        ));
        let definitions = ChatToolExecutor::definitions();
        validate_allowed_tools(&allowed_tools, &definitions)?;
        let system_prompt = system_prompt(snapshot, content_body.as_deref(), instruction)?;
        let history_budget = available_history_tokens(
            &system_prompt,
            &snapshot.context.user_prompt,
            self.config.output_token_limit,
        );
        let history = trim_history_to_token_budget(&snapshot.history, history_budget)?;
        let history_message_count = history.messages.len();
        let request = AgentRequest {
            feature: match snapshot.context.kind {
                ChatTurnKind::Assistant => "assistant_chat".to_owned(),
                _ => "article_chat".to_owned(),
            },
            model_spec: ModelSpec::parse(&snapshot.context.session.model)?.canonical(),
            system_prompt,
            user_prompt: snapshot.context.user_prompt.clone(),
            transcript: history,
            response_contract: ResponseContract::Text,
            tools: definitions,
            tool_policy: ToolPolicy {
                allowed: allowed_tools,
                require_tool: false,
                allow_parallel_calls: false,
            },
            limits: AgentLimits {
                request_limit: Some(self.config.request_limit),
                tool_call_limit: self.config.tool_call_limit,
                output_token_limit: Some(self.config.output_token_limit),
                deadline: self.config.sandbox_timeout,
            },
            provider_parameters: Map::new(),
        };

        let execution = engine.run(request, tools.clone(), events.clone());
        tokio::pin!(execution);
        let result = tokio::select! {
            result = &mut execution => result.map_err(ChatAgentError::Agent),
            () = cancellation.cancelled() => {
                // Let the cancelled tool future unwind through its sandbox cleanup guards.
                let _ = execution.await;
                Err(ChatAgentError::Cancelled)
            }
        };
        let cleanup = tools.close().await;
        events.finish().await;
        if let Err(error) = cleanup {
            tracing::error!(error = %error, "chat sandbox cleanup remains pending");
        }
        let outcome = result?;
        if outcome.output_text.trim().is_empty() {
            return Err(ChatAgentError::EmptyOutput);
        }
        let turn_transcript = turn_transcript(snapshot, history_message_count, &outcome)?;
        let model_provider = ModelSpec::parse(&outcome.model_name)?
            .provider
            .as_str()
            .to_owned();
        Ok(ChatAgentRun {
            render_metadata: tools.render_metadata(),
            tool_names: events.tool_names(),
            turn_transcript,
            model_provider,
            outcome,
        })
    }

    fn credentials_for(
        &self,
        snapshot: &ChatTaskSnapshot,
    ) -> Result<ProviderCredentials, ChatAgentError> {
        let mut credentials = self.credentials.clone();
        let Some(encrypted) = snapshot.encrypted_provider_key.as_deref() else {
            return Ok(credentials);
        };
        let cipher = self
            .cipher
            .as_ref()
            .ok_or(ChatAgentError::MissingTokenCipher)?;
        let key = SecretString::from(cipher.decrypt(encrypted)?);
        match ModelSpec::parse(&snapshot.context.session.model)?.provider {
            ModelProvider::OpenAi => credentials.openai = Some(key),
            ModelProvider::Anthropic => credentials.anthropic = Some(key),
            ModelProvider::Google => credentials.google = Some(key),
            ModelProvider::OpenRouter => credentials.openrouter = Some(key),
        }
        Ok(credentials)
    }
}

#[derive(Debug)]
pub(super) struct ChatAgentRun {
    pub outcome: AgentOutcome,
    pub turn_transcript: NewslyTranscript,
    pub tool_names: Vec<String>,
    pub render_metadata: Option<Value>,
    pub model_provider: String,
}

fn turn_transcript(
    snapshot: &ChatTaskSnapshot,
    history_message_count: usize,
    outcome: &AgentOutcome,
) -> Result<NewslyTranscript, ChatAgentError> {
    if outcome.transcript.messages.len() <= history_message_count {
        return Err(ChatAgentError::InvalidTranscript);
    }
    let transcript = NewslyTranscript {
        stream_generation: u64::try_from(snapshot.stream_generation)
            .map_err(|_| ChatAgentError::InvalidTranscript)?,
        messages: outcome.transcript.messages[history_message_count..].to_vec(),
        ..NewslyTranscript::default()
    };
    transcript
        .validate()
        .map_err(|_| ChatAgentError::InvalidTranscript)?;
    Ok(transcript)
}

fn available_history_tokens(
    system_prompt: &str,
    user_prompt: &str,
    output_token_limit: u64,
) -> usize {
    let output_reserve = usize::try_from(output_token_limit).unwrap_or(usize::MAX);
    CONTEXT_WINDOW_TOKENS
        .saturating_sub(output_reserve)
        .saturating_sub(TOOL_SCHEMA_RESERVE_TOKENS)
        .saturating_sub(estimate_tokens(system_prompt))
        .saturating_sub(estimate_tokens(user_prompt))
        .min(CHAT_HISTORY_MAX_TOKENS)
}

fn trim_history_to_token_budget(
    transcript: &NewslyTranscript,
    max_tokens: usize,
) -> Result<NewslyTranscript, ChatAgentError> {
    if max_tokens == 0 || transcript.messages.is_empty() {
        return Ok(NewslyTranscript::default());
    }

    let mut turns = Vec::<Vec<NewslyMessage>>::new();
    let mut current = Vec::new();
    for message in &transcript.messages {
        let starts_user_turn = message.role == MessageRole::User
            && message
                .parts
                .iter()
                .any(|part| matches!(part, MessagePart::Request(RequestPart::Text { .. })));
        if starts_user_turn && !current.is_empty() {
            turns.push(std::mem::take(&mut current));
        }
        current.push(message.clone());
    }
    if !current.is_empty() {
        turns.push(current);
    }

    let mut newest_turns = Vec::new();
    let mut used_tokens = 0_usize;
    for turn in turns.into_iter().rev() {
        let serialized =
            serde_json::to_string(&turn).map_err(|_| ChatAgentError::InvalidTranscript)?;
        let turn_tokens = estimate_tokens(&serialized);
        if used_tokens.saturating_add(turn_tokens) > max_tokens {
            break;
        }
        used_tokens = used_tokens.saturating_add(turn_tokens);
        newest_turns.push(turn);
    }
    let history = NewslyTranscript {
        messages: newest_turns.into_iter().rev().flatten().collect::<Vec<_>>(),
        ..NewslyTranscript::default()
    };
    history
        .validate()
        .map_err(|_| ChatAgentError::InvalidTranscript)?;
    Ok(history)
}

fn estimate_tokens(value: &str) -> usize {
    value.chars().count().div_ceil(TOKEN_CHARS_PER_TOKEN).max(1)
}

fn validate_allowed_tools(
    allowed: &BTreeSet<String>,
    definitions: &[newsly_agent_runtime::ToolDefinition],
) -> Result<(), ChatAgentError> {
    let defined = definitions
        .iter()
        .map(|definition| definition.name.as_str())
        .collect::<BTreeSet<_>>();
    if allowed.iter().all(|name| defined.contains(name.as_str())) {
        Ok(())
    } else {
        Err(ChatAgentError::InvalidToolRoute)
    }
}

fn bounded_u64(
    name: &'static str,
    default: u64,
    minimum: u64,
    maximum: u64,
) -> Result<u64, ChatAgentBuildError> {
    let value = env::var(name)
        .ok()
        .map_or(Ok(default), |value| value.parse::<u64>())
        .map_err(|_| ChatAgentBuildError::Invalid(name))?;
    if !(minimum..=maximum).contains(&value) {
        return Err(ChatAgentBuildError::Invalid(name));
    }
    Ok(value)
}

fn bounded_i64(
    name: &'static str,
    default: i64,
    minimum: i64,
    maximum: i64,
) -> Result<i64, ChatAgentBuildError> {
    let value = env::var(name)
        .ok()
        .map_or(Ok(default), |value| value.parse::<i64>())
        .map_err(|_| ChatAgentBuildError::Invalid(name))?;
    if !(minimum..=maximum).contains(&value) {
        return Err(ChatAgentBuildError::Invalid(name));
    }
    Ok(value)
}

fn secret_env(name: &str) -> Option<SecretString> {
    env::var(name)
        .ok()
        .map(|value| value.trim().to_owned())
        .filter(|value| !value.is_empty())
        .map(SecretString::from)
}

trait ModelProviderName {
    fn as_str(&self) -> &'static str;
}

impl ModelProviderName for ModelProvider {
    fn as_str(&self) -> &'static str {
        match self {
            Self::OpenAi => "openai",
            Self::Anthropic => "anthropic",
            Self::Google => "google",
            Self::OpenRouter => "openrouter",
        }
    }
}

#[derive(Debug, Error)]
pub enum ChatAgentBuildError {
    #[error("LLM_TASK_SANDBOX_E2B_API_KEY or E2B_API_KEY is required")]
    MissingE2bKey,
    #[error("invalid chat worker configuration in {0}")]
    Invalid(&'static str),
    #[error(transparent)]
    SharedConfig(#[from] ShareActionAgentConfigError),
    #[error(transparent)]
    E2b(#[from] E2bError),
    #[error(transparent)]
    Http(#[from] reqwest::Error),
    #[error(transparent)]
    Lifecycle(#[from] TaskSandboxError),
    #[error(transparent)]
    Onboarding(#[from] OnboardingGatewayError),
    #[error(transparent)]
    FeedValidation(#[from] FeedValidationError),
    #[error("chat body storage configuration failed: {0}")]
    Storage(String),
    #[error(transparent)]
    Cipher(#[from] newsly_providers::IntegrationTokenCipherError),
}

impl From<ContentBodyStoreError> for ChatAgentBuildError {
    fn from(error: ContentBodyStoreError) -> Self {
        Self::Storage(error.to_string())
    }
}

#[derive(Debug, Error)]
pub(super) enum ChatAgentError {
    #[error("chat agent deadline expired")]
    Deadline,
    #[error("chat agent was cancelled after losing its queue lease")]
    Cancelled,
    #[error("deep research does not run through Rig")]
    UnsupportedKind,
    #[error("assistant turn is missing its immutable screen context")]
    MissingScreenContext,
    #[error("a user-managed provider key exists but X_TOKEN_ENCRYPTION_KEY is unavailable")]
    MissingTokenCipher,
    #[error("chat agent returned empty output")]
    EmptyOutput,
    #[error("chat agent returned an invalid current-turn transcript")]
    InvalidTranscript,
    #[error("assistant routing selected an undefined tool")]
    InvalidToolRoute,
    #[error(transparent)]
    Prompt(#[from] ChatPromptError),
    #[error(transparent)]
    Storage(#[from] ContentBodyStoreError),
    #[error(transparent)]
    Model(#[from] newsly_providers::ModelSpecError),
    #[error(transparent)]
    Rig(#[from] RigAgentEngineError),
    #[error(transparent)]
    Cipher(#[from] newsly_providers::IntegrationTokenCipherError),
    #[error(transparent)]
    Agent(#[from] AgentRuntimeError),
}
