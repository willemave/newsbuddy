use std::collections::BTreeSet;
use std::future::Future;
use std::pin::Pin;
use std::sync::Arc;
use std::time::Duration;

use schemars::Schema;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use thiserror::Error;

use crate::{NewslyTranscript, ProviderUsage};

pub type BoxAgentFuture<'a> =
    Pin<Box<dyn Future<Output = Result<AgentOutcome, AgentRuntimeError>> + Send + 'a>>;
pub type BoxToolFuture<'a> =
    Pin<Box<dyn Future<Output = Result<ToolOutput, AgentRuntimeError>> + Send + 'a>>;

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct AgentRequest {
    pub feature: String,
    pub model_spec: String,
    pub system_prompt: String,
    pub user_prompt: String,
    pub transcript: NewslyTranscript,
    pub response_contract: ResponseContract,
    pub tools: Vec<ToolDefinition>,
    pub tool_policy: ToolPolicy,
    pub limits: AgentLimits,
    #[serde(default)]
    pub provider_parameters: serde_json::Map<String, Value>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "mode", rename_all = "snake_case")]
pub enum ResponseContract {
    Text,
    JsonSchema {
        name: String,
        schema: Schema,
        strict: bool,
        validation_retries: u16,
    },
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ToolDefinition {
    pub name: String,
    pub description: String,
    pub input_schema: Schema,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ToolPolicy {
    pub allowed: BTreeSet<String>,
    pub require_tool: bool,
    pub allow_parallel_calls: bool,
}

impl ToolPolicy {
    /// Verify that every allowed tool is present in the supplied definition set.
    ///
    /// # Errors
    ///
    /// Returns [`AgentRuntimeError::InvalidRequest`] when the policy names an undefined tool.
    pub fn validate(&self, tools: &[ToolDefinition]) -> Result<(), AgentRuntimeError> {
        let defined = tools
            .iter()
            .map(|tool| tool.name.as_str())
            .collect::<BTreeSet<_>>();
        let unknown = self
            .allowed
            .iter()
            .filter(|name| !defined.contains(name.as_str()))
            .cloned()
            .collect::<Vec<_>>();
        if unknown.is_empty() {
            Ok(())
        } else {
            Err(AgentRuntimeError::InvalidRequest(format!(
                "tool policy references undefined tools: {}",
                unknown.join(", ")
            )))
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct AgentLimits {
    pub request_limit: Option<u32>,
    pub tool_call_limit: u32,
    pub output_token_limit: Option<u64>,
    #[serde(with = "duration_millis")]
    pub deadline: Duration,
}

impl AgentLimits {
    /// Verify the optional request and required deadline bounds used by the runtime.
    ///
    /// # Errors
    ///
    /// Returns [`AgentRuntimeError::InvalidRequest`] when a configured bound is zero.
    pub fn validate(&self) -> Result<(), AgentRuntimeError> {
        if self.request_limit == Some(0) {
            return Err(AgentRuntimeError::InvalidRequest(
                "request_limit must be greater than zero when configured".to_owned(),
            ));
        }
        if self.deadline.is_zero() {
            return Err(AgentRuntimeError::InvalidRequest(
                "deadline must be greater than zero".to_owned(),
            ));
        }
        Ok(())
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct AgentOutcome {
    pub output_text: String,
    pub structured_output: Option<Value>,
    pub transcript: NewslyTranscript,
    pub usage: ProviderUsage,
    pub provider_response_id: Option<String>,
    pub model_name: String,
    pub request_count: u32,
    pub tool_call_count: u32,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum AgentEvent {
    ModelRequestStarted { sequence: u32 },
    TextDelta { text: String },
    ToolCallStarted { id: String, name: String },
    ToolProgress { id: String, text: String },
    ToolCallFinished { id: String, is_error: bool },
    Usage { usage: ProviderUsage },
    Completed,
}

pub trait AgentEventSink: std::fmt::Debug + Send + Sync {
    /// Publish one normalized agent event.
    ///
    /// # Errors
    ///
    /// Returns an event-sink error when the durable progress boundary rejects the event.
    fn publish(&self, event: AgentEvent) -> Result<(), AgentRuntimeError>;
}

pub trait AgentEngine: std::fmt::Debug + Send + Sync {
    fn run(
        &self,
        request: AgentRequest,
        tools: Arc<dyn ToolExecutor>,
        events: Arc<dyn AgentEventSink>,
    ) -> BoxAgentFuture<'_>;
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ToolCall {
    pub id: String,
    pub name: String,
    pub arguments: Value,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ToolOutput {
    pub content: Value,
    pub is_error: bool,
}

pub trait ToolExecutor: std::fmt::Debug + Send + Sync {
    fn execute(&self, call: ToolCall, events: Arc<dyn AgentEventSink>) -> BoxToolFuture<'_>;
}

#[derive(Debug, Error)]
pub enum AgentRuntimeError {
    #[error("invalid agent request: {0}")]
    InvalidRequest(String),
    #[error("agent deadline exceeded")]
    DeadlineExceeded,
    #[error("agent request limit exceeded")]
    RequestLimitExceeded,
    #[error("agent tool-call limit exceeded")]
    ToolCallLimitExceeded,
    #[error("provider execution failed: {0}")]
    Provider(String),
    #[error("tool execution failed: {0}")]
    Tool(String),
    #[error("structured response validation failed: {0}")]
    Validation(String),
    #[error("event publication failed: {0}")]
    EventSink(String),
}

mod duration_millis {
    use std::time::Duration;

    use serde::{Deserialize, Deserializer, Serializer};

    pub(super) fn serialize<S>(value: &Duration, serializer: S) -> Result<S::Ok, S::Error>
    where
        S: Serializer,
    {
        serializer.serialize_u64(value.as_millis().try_into().unwrap_or(u64::MAX))
    }

    pub(super) fn deserialize<'de, D>(deserializer: D) -> Result<Duration, D::Error>
    where
        D: Deserializer<'de>,
    {
        Ok(Duration::from_millis(u64::deserialize(deserializer)?))
    }
}

#[cfg(test)]
mod tests {
    use std::time::Duration;

    use super::{AgentLimits, AgentRuntimeError};

    fn limits(request_limit: Option<u32>) -> AgentLimits {
        AgentLimits {
            request_limit,
            tool_call_limit: 1,
            output_token_limit: None,
            deadline: Duration::from_secs(1),
        }
    }

    #[test]
    fn finite_zero_request_limit_is_invalid() {
        assert!(matches!(
            limits(Some(0)).validate(),
            Err(AgentRuntimeError::InvalidRequest(message))
                if message == "request_limit must be greater than zero when configured"
        ));
    }

    #[test]
    fn absent_request_limit_is_valid() {
        limits(None)
            .validate()
            .expect("a deadline may be the sole model-request bound");
    }
}
