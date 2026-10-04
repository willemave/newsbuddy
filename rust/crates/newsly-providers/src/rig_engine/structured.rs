use std::sync::Arc;
use std::time::Duration;

use newsly_agent_runtime::{
    AgentEventSink, AgentLimits, AgentOutcome, AgentRequest, AgentRuntimeError, NewslyTranscript,
    ResponseContract, ToolPolicy,
};
use rig_core::schemars::{JsonSchema, schema_for};
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

impl RigAgentEngine {
    pub(crate) async fn run_structured<T>(
        &self,
        request: StructuredRunRequest,
        events: Arc<dyn AgentEventSink>,
    ) -> Result<(T, AgentOutcome), RunStructuredError>
    where
        T: DeserializeOwned + JsonSchema,
    {
        let feature = request.feature;
        let outcome = self
            .run(
                AgentRequest {
                    feature: feature.clone(),
                    model_spec: request.model_spec,
                    system_prompt: request.system_prompt,
                    user_prompt: request.user_prompt,
                    transcript: NewslyTranscript::default(),
                    response_contract: ResponseContract::JsonSchema {
                        name: feature.replace('.', "_"),
                        schema: schema_for!(T),
                        strict: true,
                        validation_retries: request.validation_retries,
                    },
                    tools: Vec::new(),
                    tool_policy: ToolPolicy {
                        allowed: std::collections::BTreeSet::new(),
                        require_tool: false,
                        allow_parallel_calls: false,
                    },
                    limits: AgentLimits {
                        request_limit: Some(request.request_limit),
                        tool_call_limit: 0,
                        output_token_limit: Some(request.output_token_limit),
                        deadline: request.deadline,
                    },
                    provider_parameters: request.provider_parameters,
                },
                Arc::new(NoTools),
                events,
            )
            .await?;
        let value = outcome
            .structured_output
            .clone()
            .ok_or(RunStructuredError::MissingOutput)?;
        Ok((serde_json::from_value(value)?, outcome))
    }
}
