use std::time::Duration;

use newsly_agent_runtime::AgentLimits;

pub(super) fn learning_deck_agent_limits(tool_call_limit: u32, deadline: Duration) -> AgentLimits {
    AgentLimits {
        request_limit: None,
        tool_call_limit,
        output_token_limit: None,
        deadline,
    }
}
