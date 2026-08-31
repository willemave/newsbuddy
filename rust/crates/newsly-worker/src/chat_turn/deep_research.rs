use std::collections::HashMap;
use std::sync::Arc;

use chrono::Utc;
use newsly_agent_runtime::{
    AssistantPart, MessagePart, MessageRole, NewslyMessage, NewslyTranscript, ProviderUsage,
    RequestPart, TranscriptFinishReason,
};
use newsly_db::{ChatTaskSnapshot, persist_deep_research_response_id};
use newsly_providers::{
    BackgroundBuiltInTools, BackgroundReasoningSummary, BackgroundResponseStatus,
    OpenAiBackgroundRequest, OpenAiBackgroundResponses, OpenAiGatewayError,
};
use serde_json::Map;
use sqlx::PgPool;
use thiserror::Error;
use tokio_util::sync::CancellationToken;
use uuid::Uuid;

use super::agent::ChatAgentRuntime;
use super::prompts::deep_research_context;

const DEEP_RESEARCH_MODEL: &str = "o4-mini-deep-research-2025-06-26";

#[derive(Debug)]
pub(super) struct DeepResearchRun {
    pub output_text: String,
    pub turn_transcript: NewslyTranscript,
    pub usage: ProviderUsage,
    pub provider_response_id: String,
    pub model_name: String,
}

pub(super) async fn run_deep_research(
    agent: &ChatAgentRuntime,
    gateway: Arc<dyn OpenAiBackgroundResponses>,
    pool: &PgPool,
    snapshot: &ChatTaskSnapshot,
    cancellation: CancellationToken,
) -> Result<DeepResearchRun, DeepResearchError> {
    let body = agent.content_body(snapshot).await?;
    let context = deep_research_context(snapshot, body.as_deref());
    let response_id = if let Some(response_id) = snapshot.deep_research_response_id.clone() {
        response_id
    } else {
        if cancellation.is_cancelled() {
            return Err(DeepResearchError::Cancelled);
        }
        let input = context.as_ref().map_or_else(
            || snapshot.context.user_prompt.clone(),
            |context| {
                format!(
                    "Context:\n{context}\n\nResearch Query:\n{}",
                    snapshot.context.user_prompt
                )
            },
        );
        let response = gateway
            .start(OpenAiBackgroundRequest {
                model: DEEP_RESEARCH_MODEL.to_owned(),
                input,
                instructions: None,
                reasoning_summary: Some(BackgroundReasoningSummary::Detailed),
                tools: BackgroundBuiltInTools {
                    web_search: true,
                    code_interpreter: true,
                },
                max_output_tokens: None,
                max_tool_calls: None,
                metadata: HashMap::from_iter([
                    (
                        "queue_task_id".to_owned(),
                        snapshot.queue_task_id.to_string(),
                    ),
                    ("message_id".to_owned(), snapshot.message_id.to_string()),
                    ("user_id".to_owned(), snapshot.user_id.to_string()),
                ]),
                safety_identifier: Some(format!("newsly-user-{}", snapshot.user_id)),
            })
            .await?;
        let mut transaction = pool.begin().await?;
        let persisted = persist_deep_research_response_id(
            &mut transaction,
            snapshot.message_id,
            snapshot.stream_generation,
            &response.response_id,
        )
        .await?;
        transaction.commit().await?;
        match persisted {
            Some(persisted_id) if persisted_id == response.response_id => persisted_id,
            Some(persisted_id) => {
                cancel_orphaned_response(gateway.as_ref(), &response.response_id).await;
                persisted_id
            }
            None => {
                cancel_orphaned_response(gateway.as_ref(), &response.response_id).await;
                return Err(DeepResearchError::OwnershipLost);
            }
        }
    };

    for _ in 0..agent.config().deep_research_max_polls {
        if cancellation.is_cancelled() {
            return Err(DeepResearchError::Cancelled);
        }
        let response = gateway.retrieve(&response_id).await?;
        match response.status {
            BackgroundResponseStatus::Queued | BackgroundResponseStatus::InProgress => {
                tokio::select! {
                    () = cancellation.cancelled() => return Err(DeepResearchError::Cancelled),
                    () = tokio::time::sleep(agent.config().deep_research_poll_interval) => {}
                }
            }
            BackgroundResponseStatus::Completed => {
                let output_text = response
                    .output_text
                    .map(|value| value.trim().to_owned())
                    .filter(|value| !value.is_empty())
                    .ok_or(DeepResearchError::EmptyOutput)?;
                let usage =
                    response
                        .usage
                        .map_or_else(ProviderUsage::default, |usage| ProviderUsage {
                            request_count: 1,
                            input_tokens: usage.input_tokens,
                            output_tokens: usage.output_tokens,
                            cached_input_tokens: usage.cached_input_tokens,
                            reasoning_tokens: usage.reasoning_tokens,
                            ..ProviderUsage::default()
                        });
                let turn_transcript = NewslyTranscript {
                    stream_generation: u64::try_from(snapshot.stream_generation)
                        .map_err(|_| DeepResearchError::InvalidGeneration)?,
                    messages: vec![
                        NewslyMessage {
                            id: Some(Uuid::new_v4()),
                            role: MessageRole::User,
                            parts: vec![MessagePart::Request(RequestPart::Text {
                                text: snapshot.context.user_prompt.clone(),
                            })],
                            created_at: Utc::now(),
                            run_id: None,
                            provider: None,
                            model: None,
                            finish_reason: None,
                            usage: ProviderUsage::default(),
                            metadata: Map::new(),
                        },
                        NewslyMessage {
                            id: Some(Uuid::new_v4()),
                            role: MessageRole::Assistant,
                            parts: vec![MessagePart::Assistant(AssistantPart::Text {
                                text: output_text.clone(),
                            })],
                            created_at: Utc::now(),
                            run_id: Some(response_id.clone()),
                            provider: Some("deep_research".to_owned()),
                            model: Some(response.model.clone()),
                            finish_reason: Some(TranscriptFinishReason::Stop),
                            usage: usage.clone(),
                            metadata: Map::new(),
                        },
                    ],
                    ..NewslyTranscript::default()
                };
                turn_transcript
                    .validate()
                    .map_err(|_| DeepResearchError::InvalidTranscript)?;
                return Ok(DeepResearchRun {
                    output_text,
                    turn_transcript,
                    usage,
                    provider_response_id: response_id,
                    model_name: response.model,
                });
            }
            BackgroundResponseStatus::Failed
            | BackgroundResponseStatus::Cancelled
            | BackgroundResponseStatus::Incomplete => {
                let message = response.error.map_or_else(
                    || {
                        response.incomplete_reason.unwrap_or_else(|| {
                            format!("deep research ended with status {:?}", response.status)
                        })
                    },
                    |error| format!("{}: {}", error.code, error.message),
                );
                return Err(DeepResearchError::Terminal(message));
            }
        }
    }
    Err(DeepResearchError::PollingBudgetExhausted)
}

async fn cancel_orphaned_response(gateway: &dyn OpenAiBackgroundResponses, response_id: &str) {
    if let Err(error) = gateway.cancel(response_id).await {
        tracing::warn!(
            response_id,
            error = %error,
            "could not cancel an unclaimed deep-research response"
        );
    }
}

#[derive(Debug, Error)]
pub(super) enum DeepResearchError {
    #[error("deep research was cancelled after losing its queue lease")]
    Cancelled,
    #[error("a newer chat attempt owns this deep-research message")]
    OwnershipLost,
    #[error("deep research completed without response text")]
    EmptyOutput,
    #[error("deep research polling budget was exhausted")]
    PollingBudgetExhausted,
    #[error("deep research returned an invalid stream generation")]
    InvalidGeneration,
    #[error("deep research returned an invalid transcript")]
    InvalidTranscript,
    #[error("deep research failed: {0}")]
    Terminal(String),
    #[error(transparent)]
    Provider(#[from] OpenAiGatewayError),
    #[error(transparent)]
    Sqlx(#[from] sqlx::Error),
    #[error(transparent)]
    Repository(#[from] newsly_db::ChatTaskRepositoryError),
    #[error(transparent)]
    Agent(#[from] super::agent::ChatAgentError),
}
