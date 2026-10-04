use std::sync::Arc;

use newsly_agent_runtime::{
    AgentEventSink, AgentRuntimeError, BoxToolFuture, ToolCall, ToolExecutor,
};

#[derive(Debug)]
pub(crate) struct NoTools;

impl ToolExecutor for NoTools {
    fn execute(&self, call: ToolCall, _events: Arc<dyn AgentEventSink>) -> BoxToolFuture<'_> {
        Box::pin(async move {
            Err(AgentRuntimeError::Tool(format!(
                "this agent does not expose tool {}",
                call.name
            )))
        })
    }
}
