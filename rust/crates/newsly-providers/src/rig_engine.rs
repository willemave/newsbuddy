use std::sync::Arc;

use chrono::Utc;
use newsly_agent_runtime::{
    AgentEvent, AgentEventSink, AgentModelUsageObservation, AgentOutcome, AgentRequest,
    AgentRuntimeError, AssistantPart, ChatBudget, MessagePart, MessageRole, NewslyMessage,
    ProviderUsage, ReasoningContentKind, RequestPart, ResponseContract, ToolCall as NewslyToolCall,
    ToolExecutor, TranscriptFinishReason,
};
use reqwest::Client;
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
use uuid::Uuid;

use crate::{
    ModelProvider, ModelSpec, ProviderCredentials, openrouter::openrouter_request_parameters,
};

mod chat_budget;
use chat_budget::{CHAT_FINAL_ANSWER_INSTRUCTION, ChatBudgetState, normalize_final_response};
mod openai_response;
mod structured;
pub(crate) use structured::{RunStructuredError, StructuredRunRequest};

#[derive(Debug, Clone)]
pub struct RigAgentEngine {
    credentials: ProviderCredentials,
    http: Client,
}

impl RigAgentEngine {
    /// Creates a Rig-backed agent engine with a shared HTTP connection pool.
    #[must_use]
    pub fn new(credentials: ProviderCredentials) -> Self {
        Self::with_http_client(credentials, Client::new())
    }

    /// Creates a Rig-backed agent engine using the supplied shared HTTP connection pool.
    #[must_use]
    pub fn with_http_client(credentials: ProviderCredentials, http: Client) -> Self {
        Self { credentials, http }
    }

    #[allow(clippy::too_many_lines)]
    async fn run_inner(
        &self,
        request: AgentRequest,
        tools: Arc<dyn ToolExecutor>,
        events: Arc<dyn AgentEventSink>,
        budget: Option<ChatBudget>,
        model_override: Option<RigModel>,
    ) -> Result<AgentOutcome, AgentRuntimeError> {
        request.limits.validate()?;
        let mut chat_budget = budget
            .map(|budget| ChatBudgetState::new(budget, &request))
            .transpose()?;
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
        let model = match model_override {
            Some(model) => model,
            None => self.model(&model_spec)?,
        };
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
        let run_id = Uuid::new_v4();

        loop {
            enforce_request_limit(request_count, request.limits.request_limit)?;
            let final_answer = match &chat_budget {
                Some(budget) => budget.final_answer(
                    &request,
                    request_count,
                    tool_call_count,
                    &history,
                    &rig_tools,
                )?,
                None => false,
            };
            if final_answer {
                history.push(Message::user(CHAT_FINAL_ANSWER_INSTRUCTION));
            }
            let mut completion_request = CompletionRequest {
                model: None,
                preamble: None,
                chat_history: history.clone(),
                documents: Vec::new(),
                // Keep definitions for provider validation of historical tool blocks.
                // ToolChoice::None disables new calls in the final answer.
                tools: rig_tools.clone(),
                temperature: None,
                max_tokens: request.limits.output_token_limit,
                tool_choice: (!rig_tools.is_empty()).then_some(if final_answer {
                    ToolChoice::None
                } else if request.tool_policy.require_tool && tool_call_count == 0 {
                    ToolChoice::Required
                } else {
                    ToolChoice::Auto
                }),
                additional_params: Self::provider_parameters(
                    &model_spec,
                    &request.provider_parameters,
                    request.tool_policy.allow_parallel_calls,
                ),
                output_schema: output_schema.clone(),
                record_telemetry_content: false,
            };
            completion_request
                .validate_message_content()
                .map_err(|error| AgentRuntimeError::InvalidRequest(error.to_string()))?;

            let estimated_tokens = chat_budget
                .as_ref()
                .map(|budget| budget.prepare_dispatch(&mut completion_request, final_answer))
                .transpose()?;
            request_count = request_count.saturating_add(1);
            events.publish(AgentEvent::ModelRequestStarted {
                sequence: request_count,
            })?;

            let mut response = if final_answer {
                let budget = chat_budget
                    .as_ref()
                    .expect("final answer requires chat budget");
                tokio::time::timeout(
                    budget.final_request_timeout(),
                    model.complete(completion_request),
                )
                .await
                .map_err(|_| AgentRuntimeError::DeadlineExceeded)??
            } else {
                model.complete(completion_request).await?
            };
            if let Some(budget) = &mut chat_budget {
                budget.observe(&response, estimated_tokens.unwrap_or_default());
            }
            let response_usage = provider_usage(&response);
            usage.add_assign(&response_usage);
            events.publish(AgentEvent::Usage {
                observation: Box::new(AgentModelUsageObservation {
                    run_id,
                    sequence: request_count,
                    feature: request.feature.clone(),
                    provider: response.provider.clone(),
                    model: response
                        .model
                        .clone()
                        .unwrap_or_else(|| model_spec.model.clone()),
                    endpoint: provider_endpoint(model_spec.provider, &model_spec.model),
                    response_id: response.response_id.clone(),
                    provider_request_id: response.provider_request_id.clone(),
                    requested_service_tier: request
                        .provider_parameters
                        .get("service_tier")
                        .and_then(Value::as_str)
                        .map(str::to_owned),
                    processing_tier: response
                        .raw
                        .get("service_tier")
                        .and_then(Value::as_str)
                        .map(str::to_owned),
                    usage_is_observed: response_reports_usage(&response),
                    provider_reported_cost_usd: provider_reported_cost_usd(&response),
                    usage: response_usage.clone(),
                }),
            })?;
            if response.response_id.is_some() {
                last_response_id.clone_from(&response.response_id);
            }

            let provider_finish_reason = response.finish_reason();
            let finish_reason = if final_answer && normalize_final_response(&mut response)? {
                TranscriptFinishReason::Stop
            } else {
                transcript_finish_reason(provider_finish_reason.as_ref())
            };
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
                // Reject an invalid batch before any tool can cause side effects.
                for call in &calls {
                    if !request.tool_policy.allowed.contains(&call.function.name) {
                        return Err(AgentRuntimeError::InvalidRequest(format!(
                            "model requested disallowed tool {}",
                            call.function.name
                        )));
                    }
                }
                let remaining = request
                    .limits
                    .tool_call_limit
                    .saturating_sub(tool_call_count);
                if calls.len() > remaining as usize {
                    return Err(AgentRuntimeError::ToolCallLimitExceeded);
                }
                for call in calls {
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

            if final_answer && text.trim().is_empty() {
                return Err(AgentRuntimeError::ChatBudgetExhausted(
                    "final response contained no answer".to_owned(),
                ));
            }
            if !final_answer && request.tool_policy.require_tool && tool_call_count == 0 {
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
            ModelProvider::OpenAi => openai::Client::builder()
                .api_key(key)
                .http_client(self.http.clone())
                .build()
                .map(|client| RigModel::OpenAi {
                    client,
                    model: spec.model.clone(),
                })
                .map_err(|error| AgentRuntimeError::Provider(error.to_string())),
            ModelProvider::Anthropic => anthropic::Client::builder()
                .api_key(key)
                .http_client(self.http.clone())
                .build()
                .map(|client| RigModel::Anthropic(client.completion_model(spec.model.clone())))
                .map_err(|error| AgentRuntimeError::Provider(error.to_string())),
            ModelProvider::Google => gemini::Client::builder()
                .api_key(key)
                .http_client(self.http.clone())
                .build()
                .map(|client| RigModel::Google(client.completion_model(spec.model.clone())))
                .map_err(|error| AgentRuntimeError::Provider(error.to_string())),
            ModelProvider::OpenRouter => openrouter::Client::builder()
                .api_key(key)
                .http_client(self.http.clone())
                .build()
                .map(|client| RigModel::OpenRouter(client.completion_model(spec.model.clone())))
                .map_err(|error| AgentRuntimeError::Provider(error.to_string())),
        }
    }

    fn provider_parameters(
        model: &ModelSpec,
        requested: &Map<String, Value>,
        allow_parallel_calls: bool,
    ) -> Option<Value> {
        let mut parameters = requested.clone();
        if matches!(
            model.provider,
            ModelProvider::OpenAi | ModelProvider::OpenRouter
        ) {
            parameters.insert(
                "parallel_tool_calls".to_owned(),
                Value::Bool(allow_parallel_calls),
            );
        }
        if model.provider == ModelProvider::OpenRouter {
            for (key, value) in openrouter_request_parameters() {
                if key == "reasoning" {
                    parameters.entry(key).or_insert(value);
                } else {
                    parameters.insert(key, value);
                }
            }
            if model.model.starts_with("anthropic/") {
                // Claude endpoints reject this OpenAI parameter when require_parameters is
                // enabled. The tool policy still rejects multiple calls before execution.
                parameters.remove("parallel_tool_calls");
            }
        }
        (!parameters.is_empty()).then_some(Value::Object(parameters))
    }
}

impl RigAgentEngine {
    /// Runs one bounded agent request through the configured provider.
    ///
    /// # Errors
    ///
    /// Returns a normalized runtime error when validation, provider execution, tools, events, or
    /// the request deadline fails.
    pub async fn run(
        &self,
        request: AgentRequest,
        tools: Arc<dyn ToolExecutor>,
        events: Arc<dyn AgentEventSink>,
    ) -> Result<AgentOutcome, AgentRuntimeError> {
        let deadline = request.limits.deadline;
        tokio::time::timeout(deadline, self.run_inner(request, tools, events, None, None))
            .await
            .map_err(|_| AgentRuntimeError::DeadlineExceeded)?
    }

    /// Runs chat with a reserved, tool-free final response inside the original absolute limits.
    ///
    /// # Errors
    /// Returns ordinary runtime errors, or `ChatBudgetExhausted` when no safe dispatch remains.
    pub async fn run_chat(
        &self,
        request: AgentRequest,
        tools: Arc<dyn ToolExecutor>,
        events: Arc<dyn AgentEventSink>,
        budget: ChatBudget,
    ) -> Result<AgentOutcome, AgentRuntimeError> {
        let deadline = request.limits.deadline;
        tokio::time::timeout(
            deadline,
            self.run_inner(request, tools, events, Some(budget), None),
        )
        .await
        .map_err(|_| AgentRuntimeError::DeadlineExceeded)?
    }
}

enum RigModel {
    #[cfg(test)]
    Fake {
        responses: std::sync::Mutex<std::collections::VecDeque<CompletionResponse>>,
        requests: Arc<std::sync::Mutex<Vec<CompletionRequest>>>,
    },
    OpenAi {
        client: openai::Client,
        model: String,
    },
    Anthropic(anthropic::completion::CompletionModel),
    Google(gemini::completion::CompletionModel),
    OpenRouter(openrouter::CompletionModel),
}

fn provider_endpoint(provider: ModelProvider, model: &str) -> String {
    match provider {
        ModelProvider::OpenAi => "https://api.openai.com/v1/responses".to_owned(),
        ModelProvider::Anthropic => "https://api.anthropic.com/v1/messages".to_owned(),
        ModelProvider::Google => format!(
            "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        ),
        ModelProvider::OpenRouter => "https://openrouter.ai/api/v1/chat/completions".to_owned(),
    }
}

fn response_reports_usage(response: &CompletionResponse) -> bool {
    match response.provider.as_str() {
        "anthropic" => response
            .raw
            .get("usage")
            .is_some_and(|usage| has_u64_fields(usage, &["input_tokens", "output_tokens"])),
        "google" | "gemini" => response
            .raw
            .get("usageMetadata")
            .or_else(|| response.raw.get("usage_metadata"))
            .is_some_and(|usage| has_u64_fields(usage, &["promptTokenCount", "totalTokenCount"])),
        "openrouter" => response
            .raw
            .get("usage")
            .is_some_and(|usage| has_u64_fields(usage, &["prompt_tokens", "total_tokens"])),
        _ => response.raw.get("usage").is_some_and(|usage| {
            has_u64_fields(usage, &["input_tokens", "output_tokens", "total_tokens"])
        }),
    }
}

fn has_u64_fields(value: &Value, fields: &[&str]) -> bool {
    fields
        .iter()
        .all(|field| value.get(field).and_then(Value::as_u64).is_some())
}

fn provider_reported_cost_usd(response: &CompletionResponse) -> Option<f64> {
    if response.provider != "openrouter" {
        return None;
    }
    response
        .raw
        .get("usage")?
        .get("cost")?
        .as_f64()
        .filter(|cost| cost.is_finite() && *cost >= 0.0)
}

impl RigModel {
    async fn complete(
        &self,
        request: CompletionRequest,
    ) -> Result<CompletionResponse, AgentRuntimeError> {
        let response = match self {
            #[cfg(test)]
            Self::Fake {
                responses,
                requests,
            } => {
                requests.lock().expect("request lock").push(request);
                return responses
                    .lock()
                    .expect("response lock")
                    .pop_front()
                    .ok_or_else(|| AgentRuntimeError::Provider("fake exhausted".to_owned()));
            }
            Self::OpenAi { client, model } => {
                return openai_response::complete(client, model, request).await;
            }
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
            let text = request_text(&message.parts)?;
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

fn request_text(parts: &[MessagePart]) -> Result<String, AgentRuntimeError> {
    parts
        .iter()
        .map(|part| match part {
            MessagePart::Request(RequestPart::Text { text }) => Ok(text.clone()),
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
    // Rig reports Anthropic cache reads/writes separately from input_tokens,
    // while OpenAI includes them. The Newsly ledger always stores inclusive input.
    let input_tokens = if response.provider == "anthropic" {
        response
            .usage
            .input_tokens
            .saturating_add(response.usage.cached_input_tokens)
            .saturating_add(response.usage.cache_creation_input_tokens)
    } else {
        response.usage.input_tokens
    };
    ProviderUsage {
        request_count: 1,
        input_tokens,
        output_tokens: response.usage.output_tokens,
        cached_input_tokens: response.usage.cached_input_tokens,
        cache_write_tokens: provider_cache_write_tokens(response),
        reasoning_tokens: response.usage.reasoning_tokens,
        input_audio_tokens: 0,
        output_audio_tokens: 0,
    }
}

fn provider_cache_write_tokens(response: &CompletionResponse) -> u64 {
    if response.provider == "openai" {
        return response
            .raw
            .get("usage")
            .and_then(|usage| usage.get("input_tokens_details"))
            .and_then(|details| details.get("cache_write_tokens"))
            .and_then(Value::as_u64)
            .unwrap_or(response.usage.cache_creation_input_tokens);
    }
    response.usage.cache_creation_input_tokens
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

#[cfg(test)]
mod tests;

#[cfg(test)]
mod chat_budget_tests;
