use std::collections::BTreeSet;
use std::sync::Arc;
use std::time::Duration;

use newsly_agent_runtime::{
    AgentEventSink, AgentLimits, AgentOutcome, AgentRequest, AgentRuntimeError, NewslyTranscript,
    ResponseContract, ToolPolicy,
};
use rig_core::schemars::{JsonSchema, Schema, schema_for};
use serde::de::DeserializeOwned;
use serde_json::{Map, Value};
use thiserror::Error;

use super::RigAgentEngine;
use crate::agent_support::NoTools;

pub(crate) struct StructuredRunRequest {
    pub(crate) feature: String,
    pub(crate) model_spec: String,
    pub(crate) system_prompt: String,
    pub(crate) user_prompt: String,
    pub(crate) schema_name: Option<String>,
    pub(crate) deadline: Duration,
    pub(crate) request_limit: u32,
    pub(crate) output_token_limit: u64,
    pub(crate) validation_retries: u16,
    pub(crate) provider_parameters: Map<String, Value>,
}

pub(crate) struct RawStructuredRunRequest {
    pub(crate) feature: String,
    pub(crate) model_spec: String,
    pub(crate) system_prompt: String,
    pub(crate) user_prompt: String,
    pub(crate) schema_name: String,
    pub(crate) schema: Schema,
    pub(crate) deadline: Duration,
    pub(crate) request_limit: u32,
    pub(crate) output_token_limit: u64,
    pub(crate) validation_retries: u16,
    pub(crate) provider_parameters: Map<String, Value>,
}

#[derive(Debug, Error)]
pub(crate) enum RunStructuredError {
    #[error(transparent)]
    Runtime(#[from] AgentRuntimeError),
    #[error("structured agent response omitted its validated output")]
    MissingOutput,
    #[error("structured agent response could not be decoded: {0}")]
    Decode(#[from] serde_json::Error),
}

fn no_tool_request(
    feature: String,
    model_spec: String,
    system_prompt: String,
    user_prompt: String,
    response_contract: ResponseContract,
    limits: AgentLimits,
    provider_parameters: Map<String, Value>,
) -> AgentRequest {
    AgentRequest {
        feature,
        model_spec,
        system_prompt,
        user_prompt,
        transcript: NewslyTranscript::default(),
        response_contract,
        tools: Vec::new(),
        tool_policy: ToolPolicy {
            allowed: BTreeSet::new(),
            require_tool: false,
            allow_parallel_calls: false,
        },
        limits,
        provider_parameters,
    }
}

pub(crate) struct TextRunRequest {
    pub(crate) feature: String,
    pub(crate) model_spec: String,
    pub(crate) system_prompt: String,
    pub(crate) user_prompt: String,
    pub(crate) output_token_limit: u64,
    pub(crate) deadline: Duration,
}

impl RigAgentEngine {
    pub(crate) async fn run_structured_raw(
        &self,
        request: RawStructuredRunRequest,
        events: Arc<dyn AgentEventSink>,
    ) -> Result<AgentOutcome, AgentRuntimeError> {
        self.run(
            no_tool_request(
                request.feature,
                request.model_spec,
                request.system_prompt,
                request.user_prompt,
                ResponseContract::JsonSchema {
                    name: request.schema_name,
                    schema: request.schema,
                    strict: true,
                    validation_retries: request.validation_retries,
                },
                AgentLimits {
                    request_limit: Some(request.request_limit),
                    tool_call_limit: 0,
                    output_token_limit: Some(request.output_token_limit),
                    deadline: request.deadline,
                },
                request.provider_parameters,
            ),
            Arc::new(NoTools),
            events,
        )
        .await
    }

    pub(crate) async fn run_structured<T>(
        &self,
        request: StructuredRunRequest,
        events: Arc<dyn AgentEventSink>,
    ) -> Result<(T, AgentOutcome), RunStructuredError>
    where
        T: DeserializeOwned + JsonSchema,
    {
        let schema_name = request
            .schema_name
            .unwrap_or_else(|| request.feature.replace('.', "_"));
        let outcome = self
            .run_structured_raw(
                RawStructuredRunRequest {
                    feature: request.feature,
                    model_spec: request.model_spec,
                    system_prompt: request.system_prompt,
                    user_prompt: request.user_prompt,
                    schema_name,
                    schema: schema_for!(T),
                    deadline: request.deadline,
                    request_limit: request.request_limit,
                    output_token_limit: request.output_token_limit,
                    validation_retries: request.validation_retries,
                    provider_parameters: request.provider_parameters,
                },
                events,
            )
            .await?;
        let value = outcome
            .structured_output
            .clone()
            .ok_or(RunStructuredError::MissingOutput)?;
        Ok((serde_json::from_value(value)?, outcome))
    }

    pub(crate) async fn run_text(
        &self,
        request: TextRunRequest,
        events: Arc<dyn AgentEventSink>,
    ) -> Result<AgentOutcome, AgentRuntimeError> {
        self.run(
            no_tool_request(
                request.feature,
                request.model_spec,
                request.system_prompt,
                request.user_prompt,
                ResponseContract::Text,
                AgentLimits {
                    request_limit: Some(1),
                    tool_call_limit: 0,
                    output_token_limit: Some(request.output_token_limit),
                    deadline: request.deadline,
                },
                Map::new(),
            ),
            Arc::new(NoTools),
            events,
        )
        .await
    }
}
