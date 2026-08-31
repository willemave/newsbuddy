//! Newsly-owned agent execution interfaces and transcript representation.
//!
//! SDK-specific objects must remain behind this boundary and never become public or persisted
//! Newsly types.

#![forbid(unsafe_code)]

mod engine;
mod transcript;

pub use engine::{
    AgentEngine, AgentEvent, AgentEventSink, AgentLimits, AgentOutcome, AgentRequest,
    AgentRuntimeError, BoxAgentFuture, BoxToolFuture, ResponseContract, ToolCall, ToolDefinition,
    ToolExecutor, ToolOutput, ToolPolicy,
};
pub use transcript::{
    AssistantPart, LegacyHistoryError, MessagePart, MessageRole, NEWSLY_TRANSCRIPT_VERSION,
    NewslyMessage, NewslyTranscript, ProviderUsage, ReasoningContentKind, RequestPart,
    TranscriptError, TranscriptFinishReason,
};
