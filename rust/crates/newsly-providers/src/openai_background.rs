use std::collections::HashMap;
use std::time::Duration;

use async_openai::Client;
use async_openai::config::OpenAIConfig;
use async_openai::error::OpenAIError;
use async_openai::types::responses::{
    CodeInterpreterContainerAuto, CodeInterpreterTool, CodeInterpreterToolContainer,
    CreateResponseArgs, Reasoning, ReasoningSummary, Response, Status, Tool, WebSearchTool,
};
use secrecy::{ExposeSecret, SecretString};
use thiserror::Error;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct OpenAiBackgroundRequest {
    pub model: String,
    pub input: String,
    pub tools: BackgroundBuiltInTools,
    pub metadata: HashMap<String, String>,
    pub safety_identifier: Option<String>,
}

impl OpenAiBackgroundRequest {
    fn validate(&self) -> Result<(), OpenAiGatewayError> {
        if self.model.trim().is_empty() || self.input.trim().is_empty() {
            return Err(OpenAiGatewayError::InvalidRequest(
                "model and input must not be empty".to_owned(),
            ));
        }
        if self.metadata.len() > 16 {
            return Err(OpenAiGatewayError::InvalidRequest(
                "OpenAI response metadata may contain at most 16 entries".to_owned(),
            ));
        }
        if self
            .metadata
            .iter()
            .any(|(key, value)| key.len() > 64 || value.len() > 512)
        {
            return Err(OpenAiGatewayError::InvalidRequest(
                "OpenAI response metadata exceeds key or value limits".to_owned(),
            ));
        }
        Ok(())
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct BackgroundBuiltInTools {
    pub web_search: bool,
    pub code_interpreter: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct OpenAiBackgroundResult {
    pub response_id: String,
    pub status: BackgroundResponseStatus,
    pub model: String,
    pub output_text: Option<String>,
    pub usage: Option<BackgroundUsage>,
    pub error: Option<BackgroundProviderError>,
    pub incomplete_reason: Option<String>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BackgroundResponseStatus {
    Queued,
    InProgress,
    Completed,
    Failed,
    Cancelled,
    Incomplete,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BackgroundUsage {
    pub input_tokens: u64,
    pub output_tokens: u64,
    pub cached_input_tokens: u64,
    pub reasoning_tokens: u64,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BackgroundProviderError {
    pub code: String,
    pub message: String,
}

#[derive(Debug, Clone)]
pub struct OpenAiBackgroundGateway {
    client: Client<OpenAIConfig>,
    request_timeout: Duration,
}

impl OpenAiBackgroundGateway {
    /// Creates an `OpenAI` Responses gateway with a bounded request deadline.
    ///
    /// # Errors
    ///
    /// Returns an error when the API key is empty or the timeout is zero.
    pub fn new(
        api_key: &SecretString,
        api_base: Option<&str>,
        request_timeout: Duration,
    ) -> Result<Self, OpenAiGatewayError> {
        if api_key.expose_secret().trim().is_empty() {
            return Err(OpenAiGatewayError::InvalidRequest(
                "OpenAI API key must not be empty".to_owned(),
            ));
        }
        if request_timeout.is_zero() {
            return Err(OpenAiGatewayError::InvalidRequest(
                "OpenAI request timeout must be greater than zero".to_owned(),
            ));
        }
        let mut config = OpenAIConfig::new().with_api_key(api_key.expose_secret());
        if let Some(api_base) = api_base {
            config = config.with_api_base(api_base);
        }
        Ok(Self {
            client: Client::with_config(config),
            request_timeout,
        })
    }

    /// Starts a stored background response.
    ///
    /// # Errors
    ///
    /// Returns a typed provider error for invalid input, timeout, transport, or API failure.
    pub async fn start(
        &self,
        request: OpenAiBackgroundRequest,
    ) -> Result<OpenAiBackgroundResult, OpenAiGatewayError> {
        request.validate()?;
        let mut builder = CreateResponseArgs::default();
        builder
            .model(request.model)
            .input(request.input)
            .background(true)
            .store(true)
            .parallel_tool_calls(true);
        if !request.metadata.is_empty() {
            builder.metadata(request.metadata);
        }
        if let Some(safety_identifier) = request.safety_identifier {
            builder.safety_identifier(safety_identifier);
        }
        builder.reasoning(Reasoning {
            effort: None,
            summary: Some(ReasoningSummary::Detailed),
        });
        let tools = openai_tools(request.tools);
        if !tools.is_empty() {
            builder.tools(tools);
        }
        let request = builder
            .build()
            .map_err(|error| OpenAiGatewayError::InvalidRequest(error.to_string()))?;
        let response = tokio::time::timeout(
            self.request_timeout,
            self.client.responses().create(request),
        )
        .await
        .map_err(|_| OpenAiGatewayError::Timeout)??;
        map_response(response)
    }

    /// Retrieves a stored background response by provider ID.
    ///
    /// # Errors
    ///
    /// Returns a typed provider error for an invalid ID, timeout, transport, or API failure.
    pub async fn retrieve(
        &self,
        response_id: &str,
    ) -> Result<OpenAiBackgroundResult, OpenAiGatewayError> {
        validate_response_id(response_id)?;
        let response = tokio::time::timeout(
            self.request_timeout,
            self.client.responses().retrieve(response_id),
        )
        .await
        .map_err(|_| OpenAiGatewayError::Timeout)??;
        map_response(response)
    }

    /// Cancels a stored background response by provider ID.
    ///
    /// # Errors
    ///
    /// Returns a typed provider error for an invalid ID, timeout, transport, or API failure.
    pub async fn cancel(
        &self,
        response_id: &str,
    ) -> Result<OpenAiBackgroundResult, OpenAiGatewayError> {
        validate_response_id(response_id)?;
        let response = tokio::time::timeout(
            self.request_timeout,
            self.client.responses().cancel(response_id),
        )
        .await
        .map_err(|_| OpenAiGatewayError::Timeout)??;
        map_response(response)
    }
}

fn openai_tools(tools: BackgroundBuiltInTools) -> Vec<Tool> {
    let mut result = Vec::with_capacity(2);
    if tools.web_search {
        result.push(Tool::WebSearchPreview(WebSearchTool::default()));
    }
    if tools.code_interpreter {
        result.push(Tool::CodeInterpreter(CodeInterpreterTool {
            container: CodeInterpreterToolContainer::Auto(CodeInterpreterContainerAuto::default()),
        }));
    }
    result
}

fn map_response(response: Response) -> Result<OpenAiBackgroundResult, OpenAiGatewayError> {
    if response.id.trim().is_empty() {
        return Err(OpenAiGatewayError::MalformedResponse(
            "OpenAI response is missing its durable id".to_owned(),
        ));
    }
    let output_text = response.output_text();
    let usage = response.usage.as_ref().map(|usage| BackgroundUsage {
        input_tokens: u64::from(usage.input_tokens),
        output_tokens: u64::from(usage.output_tokens),
        cached_input_tokens: u64::from(usage.input_tokens_details.cached_tokens),
        reasoning_tokens: u64::from(usage.output_tokens_details.reasoning_tokens),
    });
    Ok(OpenAiBackgroundResult {
        response_id: response.id,
        status: response.status.into(),
        model: response.model,
        output_text,
        usage,
        error: response.error.map(|error| BackgroundProviderError {
            code: error.code,
            message: error.message,
        }),
        incomplete_reason: response.incomplete_details.map(|details| details.reason),
    })
}

impl From<Status> for BackgroundResponseStatus {
    fn from(value: Status) -> Self {
        match value {
            Status::Completed => Self::Completed,
            Status::Failed => Self::Failed,
            Status::InProgress => Self::InProgress,
            Status::Cancelled => Self::Cancelled,
            Status::Queued => Self::Queued,
            Status::Incomplete => Self::Incomplete,
        }
    }
}

fn validate_response_id(response_id: &str) -> Result<(), OpenAiGatewayError> {
    if response_id.starts_with("resp_") && response_id.len() > "resp_".len() {
        Ok(())
    } else {
        Err(OpenAiGatewayError::InvalidRequest(
            "OpenAI response id must start with resp_".to_owned(),
        ))
    }
}

impl From<OpenAIError> for OpenAiGatewayError {
    fn from(error: OpenAIError) -> Self {
        match error {
            OpenAIError::ApiError(response) => {
                let status = response.status_code.as_u16();
                let retryable = status == 408 || status == 409 || status == 429 || status >= 500;
                Self::Api {
                    status,
                    code: response.api_error.code,
                    message: response.api_error.message,
                    retryable,
                }
            }
            OpenAIError::Reqwest(error) => Self::Transport {
                message: error.to_string(),
                retryable: error.is_connect() || error.is_timeout(),
            },
            other => Self::Sdk {
                message: other.to_string(),
                retryable: matches!(other, OpenAIError::StreamError(_)),
            },
        }
    }
}

#[derive(Debug, Error)]
pub enum OpenAiGatewayError {
    #[error("invalid OpenAI background request: {0}")]
    InvalidRequest(String),
    #[error("OpenAI background request timed out")]
    Timeout,
    #[error("OpenAI API failed with HTTP {status}: {message}")]
    Api {
        status: u16,
        code: Option<String>,
        message: String,
        retryable: bool,
    },
    #[error("OpenAI transport failed: {message}")]
    Transport { message: String, retryable: bool },
    #[error("OpenAI SDK failed: {message}")]
    Sdk { message: String, retryable: bool },
    #[error("malformed OpenAI background response: {0}")]
    MalformedResponse(String),
}

impl OpenAiGatewayError {
    pub fn retryable(&self) -> bool {
        match self {
            Self::Timeout => true,
            Self::Api { retryable, .. }
            | Self::Transport { retryable, .. }
            | Self::Sdk { retryable, .. } => *retryable,
            Self::InvalidRequest(_) | Self::MalformedResponse(_) => false,
        }
    }
}
