use std::sync::Arc;

use chrono::Utc;
use newsly_agent_runtime::{
    AgentEngine, AgentEvent, AgentEventSink, AgentOutcome, AgentRequest, AgentRuntimeError,
    AssistantPart, BoxAgentFuture, MessagePart, MessageRole, NewslyMessage, ProviderUsage,
    ReasoningContentKind, RequestPart, ResponseContract, ToolCall as NewslyToolCall, ToolExecutor,
    TranscriptFinishReason,
};
use rig_core::client::CompletionClient;
use rig_core::completion::{
    AssistantContent, CompletionModel, CompletionRequest, CompletionResponse, FinishReason,
    Message, ToolDefinition as RigToolDefinition,
};
use rig_core::message::{
    ProviderCallId, Reasoning, ReasoningContent, ToolCall as RigToolCall, ToolCallId, ToolChoice,
    ToolFunction, ToolResultContent, UserContent,
};
use rig_core::providers::{anthropic, gemini, openai, openrouter};
use secrecy::ExposeSecret;
use serde_json::{Map, Value};
use thiserror::Error;
use uuid::Uuid;

use crate::{
    ModelProvider, ModelSpec, OpenRouterPrivacyPolicy, OpenRouterRoutingError, ProviderCredentials,
};

#[derive(Debug, Clone)]
pub struct RigAgentEngine {
    credentials: ProviderCredentials,
    openrouter_policy: OpenRouterPrivacyPolicy,
}

impl RigAgentEngine {
    /// Creates a Rig-backed agent engine with validated `OpenRouter` routing policy.
    ///
    /// # Errors
    ///
    /// Returns an error when the routing policy is internally inconsistent.
    pub fn new(
        credentials: ProviderCredentials,
        openrouter_policy: OpenRouterPrivacyPolicy,
    ) -> Result<Self, RigAgentEngineError> {
        openrouter_policy.validate()?;
        Ok(Self {
            credentials,
            openrouter_policy,
        })
    }

    #[allow(clippy::too_many_lines)]
    async fn run_inner(
        &self,
        request: AgentRequest,
        tools: Arc<dyn ToolExecutor>,
        events: Arc<dyn AgentEventSink>,
    ) -> Result<AgentOutcome, AgentRuntimeError> {
        request.limits.validate()?;
        request.tool_policy.validate(&request.tools)?;
        request.transcript.validate().map_err(|error| {
            AgentRuntimeError::InvalidRequest(format!("invalid Newsly transcript: {error}"))
        })?;
        if request.tool_policy.require_tool && request.tool_policy.allowed.is_empty() {
            return Err(AgentRuntimeError::InvalidRequest(
                "a required tool policy must allow at least one tool".to_owned(),
            ));
        }

        let model_spec = ModelSpec::parse(&request.model_spec)
            .map_err(|error| AgentRuntimeError::InvalidRequest(error.to_string()))?;
        let model = self.model(&model_spec)?;
        let model_name = model_spec.canonical();
        let mut history = rig_history_from_transcript(&request.transcript)?;
        history.retain(|message| !matches!(message, Message::System { .. }));
        if !request.system_prompt.is_empty() {
            history.insert(0, Message::system(request.system_prompt.clone()));
        }
        history.push(Message::user(request.user_prompt.clone()));

        let mut transcript = request.transcript.clone();
        transcript.messages.push(NewslyMessage {
            id: Some(Uuid::new_v4()),
            role: MessageRole::User,
            parts: vec![MessagePart::Request(RequestPart::Text {
                text: request.user_prompt.clone(),
            })],
            created_at: Utc::now(),
            run_id: None,
            provider: None,
            model: None,
            finish_reason: None,
            usage: ProviderUsage::default(),
            metadata: Map::new(),
        });

        let rig_tools = request
            .tools
            .iter()
            .filter(|tool| request.tool_policy.allowed.contains(&tool.name))
            .map(|tool| RigToolDefinition {
                name: tool.name.clone(),
                description: tool.description.clone(),
                parameters: Value::from(tool.input_schema.clone()),
            })
            .collect::<Vec<_>>();
        let output_schema = match &request.response_contract {
            ResponseContract::Text => None,
            ResponseContract::JsonSchema { schema, .. } => Some(schema.clone()),
        };

        let mut usage = ProviderUsage::default();
        let mut request_count = 0_u32;
        let mut tool_call_count = 0_u32;
        let mut validation_failures = 0_u16;
        let mut last_response_id = None;

        loop {
            enforce_request_limit(request_count, request.limits.request_limit)?;
            request_count = request_count.saturating_add(1);
            events.publish(AgentEvent::ModelRequestStarted {
                sequence: request_count,
            })?;

            let completion_request = CompletionRequest {
                model: None,
                preamble: None,
                chat_history: history.clone(),
                documents: Vec::new(),
                tools: rig_tools.clone(),
                temperature: None,
                max_tokens: request.limits.output_token_limit,
                tool_choice: (!rig_tools.is_empty()).then_some(
                    if request.tool_policy.require_tool && tool_call_count == 0 {
                        ToolChoice::Required
                    } else {
                        ToolChoice::Auto
                    },
                ),
                additional_params: self.provider_parameters(
                    model_spec.provider,
                    &request.provider_parameters,
                    request.tool_policy.allow_parallel_calls,
                )?,
                output_schema: output_schema.clone(),
                record_telemetry_content: false,
            };
            completion_request
                .validate_message_content()
                .map_err(|error| AgentRuntimeError::InvalidRequest(error.to_string()))?;

            let response = model.complete(completion_request).await?;
            let response_usage = provider_usage(&response);
            usage.add_assign(&response_usage);
            events.publish(AgentEvent::Usage {
                usage: response_usage.clone(),
            })?;
            if response.response_id.is_some() {
                last_response_id.clone_from(&response.response_id);
            }

            let provider_finish_reason = response.finish_reason();
            let finish_reason = transcript_finish_reason(provider_finish_reason.as_ref());
            if response
                .choice
                .iter()
                .any(|content| matches!(content, AssistantContent::Image(_)))
            {
                return Err(AgentRuntimeError::Provider(
                    "agent responses cannot contain image output".to_owned(),
                ));
            }
            let assistant_parts = newsly_assistant_parts(&response.choice);
            let text = response
                .choice
                .iter()
                .filter_map(|content| match content {
                    AssistantContent::Text(text) => Some(text.text.as_str()),
                    _ => None,
                })
                .collect::<String>();
            let mut metadata = Map::new();
            insert_optional(&mut metadata, "message_id", response.message_id.clone());
            insert_optional(
                &mut metadata,
                "provider_request_id",
                response.provider_request_id.clone(),
            );
            insert_optional(&mut metadata, "response_id", response.response_id.clone());
            transcript.messages.push(NewslyMessage {
                id: Some(Uuid::new_v4()),
                role: MessageRole::Assistant,
                parts: assistant_parts,
                created_at: Utc::now(),
                run_id: response.response_id.clone(),
                provider: Some(response.provider.clone()),
                model: response.model.clone().or_else(|| Some(model_name.clone())),
                finish_reason: Some(finish_reason),
                usage: response_usage,
                metadata,
            });

            history.push(Message::Assistant {
                id: response.message_id.clone(),
                content: response.choice.clone(),
            });
            let calls = response
                .choice
                .iter()
                .filter_map(|content| match content {
                    AssistantContent::ToolCall(call) => Some(call.clone()),
                    _ => None,
                })
                .collect::<Vec<_>>();
            if !calls.is_empty() {
                if !text.is_empty() {
                    events.publish(AgentEvent::TextDelta { text: text.clone() })?;
                }
                if !request.tool_policy.allow_parallel_calls && calls.len() > 1 {
                    return Err(AgentRuntimeError::InvalidRequest(
                        "model returned parallel tool calls when the policy forbids them"
                            .to_owned(),
                    ));
                }
                for call in calls {
                    if !request.tool_policy.allowed.contains(&call.function.name) {
                        return Err(AgentRuntimeError::InvalidRequest(format!(
                            "model requested disallowed tool {}",
                            call.function.name
                        )));
                    }
                    if tool_call_count >= request.limits.tool_call_limit {
                        return Err(AgentRuntimeError::ToolCallLimitExceeded);
                    }
                    tool_call_count = tool_call_count.saturating_add(1);
                    let call_id = call.id.as_str().to_owned();
                    events.publish(AgentEvent::ToolCallStarted {
                        id: call_id.clone(),
                        name: call.function.name.clone(),
                    })?;
                    let output = tools
                        .execute(
                            NewslyToolCall {
                                id: call_id.clone(),
                                name: call.function.name.clone(),
                                arguments: call.function.arguments.clone(),
                            },
                            Arc::clone(&events),
                        )
                        .await?;
                    events.publish(AgentEvent::ToolCallFinished {
                        id: call_id.clone(),
                        is_error: output.is_error,
                    })?;

                    let provider_content = if output.is_error {
                        serde_json::json!({"error": output.content})
                    } else {
                        output.content.clone()
                    };
                    history.push(Message::User {
                        content: vec![UserContent::tool_result_for(
                            call.id.clone(),
                            call.provider.clone(),
                            call.function.name.clone(),
                            vec![ToolResultContent::json(provider_content)],
                        )],
                    });
                    transcript.messages.push(NewslyMessage {
                        id: Some(Uuid::new_v4()),
                        role: MessageRole::Tool,
                        parts: vec![MessagePart::Request(RequestPart::ToolResult {
                            tool_call_id: call_id,
                            provider_call_id: call
                                .provider
                                .as_ref()
                                .map(|provider| provider.call_id.clone()),
                            provider_item_id: call
                                .provider
                                .as_ref()
                                .and_then(|provider| provider.item_id.clone()),
                            tool_name: call.function.name,
                            content: output.content,
                            is_error: output.is_error,
                        })],
                        created_at: Utc::now(),
                        run_id: response.response_id.clone(),
                        provider: Some(response.provider.clone()),
                        model: response.model.clone().or_else(|| Some(model_name.clone())),
                        finish_reason: None,
                        usage: ProviderUsage::default(),
                        metadata: Map::new(),
                    });
                }
                continue;
            }

            if request.tool_policy.require_tool && tool_call_count == 0 {
                return Err(AgentRuntimeError::Validation(
                    "the response completed without the required tool call".to_owned(),
                ));
            }
            match validate_output(&request.response_contract, &text) {
                Ok(structured_output) => {
                    if !text.is_empty() {
                        events.publish(AgentEvent::TextDelta { text: text.clone() })?;
                    }
                    events.publish(AgentEvent::Completed)?;
                    return Ok(AgentOutcome {
                        output_text: text,
                        structured_output,
                        transcript,
                        usage,
                        provider_response_id: last_response_id,
                        model_name,
                        request_count,
                        tool_call_count,
                    });
                }
                Err(error) => {
                    let allowed_retries = match &request.response_contract {
                        ResponseContract::Text => 0,
                        ResponseContract::JsonSchema {
                            validation_retries, ..
                        } => *validation_retries,
                    };
                    if validation_failures >= allowed_retries {
                        return Err(AgentRuntimeError::Validation(error));
                    }
                    validation_failures = validation_failures.saturating_add(1);
                    let retry_message = format!(
                        "Your previous response did not satisfy the required JSON Schema: {error}. Return only a corrected JSON value."
                    );
                    history.push(Message::user(retry_message.clone()));
                    transcript.messages.push(NewslyMessage {
                        id: Some(Uuid::new_v4()),
                        role: MessageRole::User,
                        parts: vec![MessagePart::Request(RequestPart::Retry {
                            message: retry_message,
                            tool_call_id: None,
                        })],
                        created_at: Utc::now(),
                        run_id: response.response_id,
                        provider: Some(response.provider),
                        model: response.model.or_else(|| Some(model_name.clone())),
                        finish_reason: None,
                        usage: ProviderUsage::default(),
                        metadata: Map::new(),
                    });
                }
            }
        }
    }

    fn model(&self, spec: &ModelSpec) -> Result<RigModel, AgentRuntimeError> {
        let key = self
            .credentials
            .key_for(spec.provider)
            .ok_or_else(|| {
                AgentRuntimeError::InvalidRequest(format!(
                    "no credential configured for {}",
                    spec.canonical()
                ))
            })?
            .expose_secret()
            .to_owned();
        match spec.provider {
            ModelProvider::OpenAi => openai::Client::new(key)
                .map(|client| RigModel::OpenAi(client.completion_model(spec.model.clone())))
                .map_err(|error| AgentRuntimeError::Provider(error.to_string())),
            ModelProvider::Anthropic => anthropic::Client::new(key)
                .map(|client| RigModel::Anthropic(client.completion_model(spec.model.clone())))
                .map_err(|error| AgentRuntimeError::Provider(error.to_string())),
            ModelProvider::Google => gemini::Client::new(key)
                .map(|client| RigModel::Google(client.completion_model(spec.model.clone())))
                .map_err(|error| AgentRuntimeError::Provider(error.to_string())),
            ModelProvider::OpenRouter => openrouter::Client::new(key)
                .map(|client| RigModel::OpenRouter(client.completion_model(spec.model.clone())))
                .map_err(|error| AgentRuntimeError::Provider(error.to_string())),
        }
    }

    fn provider_parameters(
        &self,
        provider: ModelProvider,
        requested: &Map<String, Value>,
        allow_parallel_calls: bool,
    ) -> Result<Option<Value>, AgentRuntimeError> {
        let mut parameters = requested.clone();
        if matches!(provider, ModelProvider::OpenAi | ModelProvider::OpenRouter) {
            parameters.insert(
                "parallel_tool_calls".to_owned(),
                Value::Bool(allow_parallel_calls),
            );
        }
        if provider == ModelProvider::OpenRouter {
            for (key, value) in self
                .openrouter_policy
                .request_parameters()
                .map_err(|error| AgentRuntimeError::InvalidRequest(error.to_string()))?
            {
                parameters.insert(key, value);
            }
        }
        Ok((!parameters.is_empty()).then_some(Value::Object(parameters)))
    }
}

impl AgentEngine for RigAgentEngine {
    fn run(
        &self,
        request: AgentRequest,
        tools: Arc<dyn ToolExecutor>,
        events: Arc<dyn AgentEventSink>,
    ) -> BoxAgentFuture<'_> {
        Box::pin(async move {
            let deadline = request.limits.deadline;
            tokio::time::timeout(deadline, self.run_inner(request, tools, events))
                .await
                .map_err(|_| AgentRuntimeError::DeadlineExceeded)?
        })
    }
}

enum RigModel {
    OpenAi(openai::responses_api::ResponsesCompletionModel),
    Anthropic(anthropic::completion::CompletionModel),
    Google(gemini::completion::CompletionModel),
    OpenRouter(openrouter::CompletionModel),
}

impl RigModel {
    async fn complete(
        &self,
        request: CompletionRequest,
    ) -> Result<CompletionResponse, AgentRuntimeError> {
        let response = match self {
            Self::OpenAi(model) => model.completion(request).await,
            Self::Anthropic(model) => model.completion(request).await,
            Self::Google(model) => model.completion(request).await,
            Self::OpenRouter(model) => model.completion(request).await,
        };
        response.map_err(|error| AgentRuntimeError::Provider(error.to_string()))
    }
}

fn rig_history_from_transcript(
    transcript: &newsly_agent_runtime::NewslyTranscript,
) -> Result<Vec<Message>, AgentRuntimeError> {
    transcript
        .messages
        .iter()
        .map(rig_message)
        .collect::<Result<Vec<_>, _>>()
}

fn rig_message(message: &NewslyMessage) -> Result<Message, AgentRuntimeError> {
    match message.role {
        MessageRole::System => {
            let text = request_text(&message.parts, true)?;
            Ok(Message::system(text))
        }
        MessageRole::User | MessageRole::Tool => {
            let content = request_content(&message.parts)?;
            Ok(Message::User { content })
        }
        MessageRole::Assistant => {
            let content = assistant_content(&message.parts)?;
            Ok(Message::Assistant {
                id: message
                    .metadata
                    .get("message_id")
                    .and_then(Value::as_str)
                    .map(str::to_owned),
                content,
            })
        }
    }
}

fn request_text(parts: &[MessagePart], system: bool) -> Result<String, AgentRuntimeError> {
    parts
        .iter()
        .map(|part| match part {
            MessagePart::Request(RequestPart::Text { text }) => Ok(text.clone()),
            MessagePart::Request(RequestPart::Retry { message, .. }) if !system => {
                Ok(message.clone())
            }
            _ => Err(invalid_history("message role and part type do not agree")),
        })
        .collect::<Result<Vec<_>, _>>()
        .map(|parts| parts.join("\n"))
}

fn request_content(parts: &[MessagePart]) -> Result<Vec<UserContent>, AgentRuntimeError> {
    parts
        .iter()
        .map(|part| match part {
            MessagePart::Request(RequestPart::Text { text }) => Ok(UserContent::text(text.clone())),
            MessagePart::Request(RequestPart::Retry { message, .. }) => {
                Ok(UserContent::text(message.clone()))
            }
            MessagePart::Request(RequestPart::ToolResult {
                tool_call_id,
                provider_call_id,
                provider_item_id,
                tool_name,
                content,
                is_error,
            }) => {
                let call = nonempty_call_id(tool_call_id)?;
                let provider = provider_call_id
                    .as_ref()
                    .and_then(|id| ProviderCallId::new(id.clone()))
                    .map(|provider| match provider_item_id {
                        Some(item_id) => provider.with_item_id(item_id.clone()),
                        None => provider,
                    });
                let value = if *is_error {
                    serde_json::json!({"error": content})
                } else {
                    content.clone()
                };
                Ok(UserContent::tool_result_for(
                    call,
                    provider,
                    tool_name.clone(),
                    vec![ToolResultContent::json(value)],
                ))
            }
            MessagePart::Assistant(_) => Err(invalid_history(
                "request message contains assistant content",
            )),
        })
        .collect()
}

fn assistant_content(parts: &[MessagePart]) -> Result<Vec<AssistantContent>, AgentRuntimeError> {
    let mut content = Vec::with_capacity(parts.len());
    for part in parts {
        let next = match part {
            MessagePart::Assistant(AssistantPart::Text { text }) => {
                Ok(AssistantContent::text(text.clone()))
            }
            MessagePart::Assistant(AssistantPart::ToolCall {
                tool_call_id,
                provider_call_id,
                provider_item_id,
                tool_name,
                arguments,
                signature,
                additional_params,
            }) => {
                let provider = provider_call_id
                    .as_ref()
                    .and_then(|id| ProviderCallId::new(id.clone()))
                    .map(|provider| match provider_item_id {
                        Some(item_id) => provider.with_item_id(item_id.clone()),
                        None => provider,
                    });
                Ok(AssistantContent::ToolCall(RigToolCall {
                    id: nonempty_call_id(tool_call_id)?,
                    provider,
                    function: ToolFunction::new(tool_name.clone(), arguments.clone()),
                    signature: signature.clone(),
                    additional_params: additional_params.clone(),
                }))
            }
            MessagePart::Assistant(AssistantPart::Reasoning {
                provider_item_id,
                content_kind,
                text,
                signature,
                encrypted_content,
            }) => {
                let reasoning_content = match content_kind {
                    Some(ReasoningContentKind::Text) => ReasoningContent::Text {
                        text: text
                            .clone()
                            .ok_or_else(|| invalid_history("reasoning text is missing"))?,
                        signature: signature.clone(),
                    },
                    Some(ReasoningContentKind::Encrypted) => ReasoningContent::Encrypted(
                        encrypted_content
                            .clone()
                            .ok_or_else(|| invalid_history("encrypted reasoning is missing"))?,
                    ),
                    Some(ReasoningContentKind::Redacted) => ReasoningContent::Redacted {
                        data: encrypted_content
                            .clone()
                            .ok_or_else(|| invalid_history("redacted reasoning is missing"))?,
                    },
                    Some(ReasoningContentKind::Summary) => ReasoningContent::Summary(
                        text.clone()
                            .ok_or_else(|| invalid_history("reasoning summary is missing"))?,
                    ),
                    None => match (text, encrypted_content, signature) {
                        (Some(text), _, _) => ReasoningContent::Text {
                            text: text.clone(),
                            signature: signature.clone(),
                        },
                        (_, Some(encrypted), _) => ReasoningContent::Encrypted(encrypted.clone()),
                        (_, _, Some(redacted)) => ReasoningContent::Redacted {
                            data: redacted.clone(),
                        },
                        _ => return Err(invalid_history("reasoning part has no durable content")),
                    },
                };
                if let Some(AssistantContent::Reasoning(previous)) = content.last_mut()
                    && previous.id == *provider_item_id
                {
                    previous.content.push(reasoning_content);
                    continue;
                }
                Ok(AssistantContent::Reasoning(Reasoning {
                    id: provider_item_id.clone(),
                    content: vec![reasoning_content],
                }))
            }
            MessagePart::Request(_) => Err(invalid_history(
                "assistant message contains request content",
            )),
        }?;
        content.push(next);
    }
    Ok(content)
}

fn newsly_assistant_parts(content: &[AssistantContent]) -> Vec<MessagePart> {
    content
        .iter()
        .flat_map(|part| match part {
            AssistantContent::Text(text) => vec![MessagePart::Assistant(AssistantPart::Text {
                text: text.text.clone(),
            })],
            AssistantContent::ToolCall(call) => {
                vec![MessagePart::Assistant(AssistantPart::ToolCall {
                    tool_call_id: call.id.as_str().to_owned(),
                    provider_call_id: call
                        .provider
                        .as_ref()
                        .map(|provider| provider.call_id.clone()),
                    provider_item_id: call
                        .provider
                        .as_ref()
                        .and_then(|provider| provider.item_id.clone()),
                    tool_name: call.function.name.clone(),
                    arguments: call.function.arguments.clone(),
                    signature: call.signature.clone(),
                    additional_params: call.additional_params.clone(),
                })]
            }
            AssistantContent::Reasoning(reasoning) => reasoning
                .content
                .iter()
                .map(|content| match content {
                    ReasoningContent::Text { text, signature } => {
                        MessagePart::Assistant(AssistantPart::Reasoning {
                            provider_item_id: reasoning.id.clone(),
                            content_kind: Some(ReasoningContentKind::Text),
                            text: Some(text.clone()),
                            signature: signature.clone(),
                            encrypted_content: None,
                        })
                    }
                    ReasoningContent::Encrypted(content) => {
                        MessagePart::Assistant(AssistantPart::Reasoning {
                            provider_item_id: reasoning.id.clone(),
                            content_kind: Some(ReasoningContentKind::Encrypted),
                            text: None,
                            signature: None,
                            encrypted_content: Some(content.clone()),
                        })
                    }
                    ReasoningContent::Redacted { data } => {
                        MessagePart::Assistant(AssistantPart::Reasoning {
                            provider_item_id: reasoning.id.clone(),
                            content_kind: Some(ReasoningContentKind::Redacted),
                            text: None,
                            signature: None,
                            encrypted_content: Some(data.clone()),
                        })
                    }
                    ReasoningContent::Summary(text) => {
                        MessagePart::Assistant(AssistantPart::Reasoning {
                            provider_item_id: reasoning.id.clone(),
                            content_kind: Some(ReasoningContentKind::Summary),
                            text: Some(text.clone()),
                            signature: None,
                            encrypted_content: None,
                        })
                    }
                })
                .collect(),
            AssistantContent::Image(_) => Vec::new(),
        })
        .collect()
}

fn provider_usage(response: &CompletionResponse) -> ProviderUsage {
    ProviderUsage {
        request_count: 1,
        input_tokens: response.usage.input_tokens,
        output_tokens: response.usage.output_tokens,
        cached_input_tokens: response.usage.cached_input_tokens,
        cache_write_tokens: response.usage.cache_creation_input_tokens,
        reasoning_tokens: response.usage.reasoning_tokens,
        input_audio_tokens: 0,
        output_audio_tokens: 0,
    }
}

fn validate_output(contract: &ResponseContract, output: &str) -> Result<Option<Value>, String> {
    let ResponseContract::JsonSchema { schema, .. } = contract else {
        return Ok(None);
    };
    let value: Value = serde_json::from_str(output)
        .map_err(|error| format!("response is not valid JSON: {error}"))?;
    let schema_value = Value::from(schema.clone());
    let validator = jsonschema::validator_for(&schema_value)
        .map_err(|error| format!("response schema is invalid: {error}"))?;
    let errors = validator
        .iter_errors(&value)
        .take(4)
        .map(|error| error.to_string())
        .collect::<Vec<_>>();
    if errors.is_empty() {
        Ok(Some(value))
    } else {
        Err(errors.join("; "))
    }
}

fn transcript_finish_reason(reason: Option<&FinishReason>) -> TranscriptFinishReason {
    match reason {
        Some(FinishReason::Stop) => TranscriptFinishReason::Stop,
        Some(FinishReason::Length) => TranscriptFinishReason::Length,
        Some(FinishReason::ToolCalls) => TranscriptFinishReason::ToolCall,
        Some(FinishReason::ContentFilter) => TranscriptFinishReason::ContentFilter,
        Some(FinishReason::Other(_)) | None => TranscriptFinishReason::Unknown,
    }
}

fn nonempty_call_id(value: &str) -> Result<ToolCallId, AgentRuntimeError> {
    ToolCallId::new(value.to_owned())
        .ok_or_else(|| invalid_history("tool call id must not be empty"))
}

fn invalid_history(message: &str) -> AgentRuntimeError {
    AgentRuntimeError::InvalidRequest(format!("invalid Newsly transcript: {message}"))
}

fn insert_optional(metadata: &mut Map<String, Value>, key: &str, value: Option<String>) {
    if let Some(value) = value {
        metadata.insert(key.to_owned(), Value::String(value));
    }
}

fn enforce_request_limit(
    completed_requests: u32,
    request_limit: Option<u32>,
) -> Result<(), AgentRuntimeError> {
    if request_limit.is_some_and(|limit| completed_requests >= limit) {
        Err(AgentRuntimeError::RequestLimitExceeded)
    } else {
        Ok(())
    }
}

#[derive(Debug, Error)]
pub enum RigAgentEngineError {
    #[error(transparent)]
    OpenRouterRouting(#[from] OpenRouterRoutingError),
}

#[cfg(test)]
mod tests {
    use newsly_agent_runtime::AgentRuntimeError;
    use rig_core::completion::AssistantContent;
    use rig_core::message::{Reasoning, ReasoningContent};

    use super::{assistant_content, enforce_request_limit, newsly_assistant_parts};

    #[test]
    fn finite_request_limit_stops_the_rig_loop_at_the_bound() {
        assert!(enforce_request_limit(1, Some(2)).is_ok());
        assert!(matches!(
            enforce_request_limit(2, Some(2)),
            Err(AgentRuntimeError::RequestLimitExceeded)
        ));
    }

    #[test]
    fn absent_request_limit_never_stops_the_rig_loop_by_count() {
        assert!(enforce_request_limit(u32::MAX, None).is_ok());
    }

    #[test]
    fn reasoning_item_identity_and_order_survive_newsly_transcript_round_trip() {
        let original = vec![AssistantContent::Reasoning(Reasoning {
            id: Some("rs_reasoning_item".to_owned()),
            content: vec![
                ReasoningContent::Summary("short summary".to_owned()),
                ReasoningContent::Encrypted("encrypted payload".to_owned()),
            ],
        })];

        let durable = newsly_assistant_parts(&original);
        let replayed = assistant_content(&durable).expect("durable reasoning should replay");

        assert_eq!(replayed, original);
    }
}
