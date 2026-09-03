use std::sync::Arc;

use newsly_agent_runtime::{
    AgentEvent, AgentEventSink, AgentRuntimeError, BoxToolFuture, ToolCall, ToolExecutor,
};

#[derive(Debug)]
pub(super) struct NoEvents;

impl AgentEventSink for NoEvents {
    fn publish(&self, _event: AgentEvent) -> Result<(), AgentRuntimeError> {
        Ok(())
    }
}

#[derive(Debug)]
pub(super) struct NoTools;

impl ToolExecutor for NoTools {
    fn execute(&self, call: ToolCall, _events: Arc<dyn AgentEventSink>) -> BoxToolFuture<'_> {
        Box::pin(async move {
            Err(AgentRuntimeError::Tool(format!(
                "onboarding does not expose tool {}",
                call.name
            )))
        })
    }
}
