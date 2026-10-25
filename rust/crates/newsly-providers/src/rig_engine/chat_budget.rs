use super::{provider_usage, response_reports_usage};
use newsly_agent_runtime::{AgentRequest, AgentRuntimeError, ChatBudget, ResponseContract};
use rig_core::completion::{
    AssistantContent, CompletionRequest, CompletionResponse, Message,
    ToolDefinition as RigToolDefinition,
};
use rig_core::message::{ToolResultContent, UserContent};

pub(super) const CHAT_FINAL_ANSWER_INSTRUCTION: &str = "The execution budget is nearly exhausted. Do not call tools. Give the user a concise final answer using only confirmed results already available. State any incomplete work plainly. Tool results marked budget_omitted are partial; do not imply full source coverage or infer omitted facts. Do not include internal reasoning or tool chatter.";

pub(super) struct ChatBudgetState {
    policy: ChatBudget,
    pub(super) deadline: tokio::time::Instant,
    charged_tokens: u64,
}

impl ChatBudgetState {
    pub(super) fn new(
        policy: ChatBudget,
        request: &AgentRequest,
    ) -> Result<Self, AgentRuntimeError> {
        if policy.cumulative_token_limit == 0
            || policy.final_answer_reserve.is_zero()
            || request.limits.request_limit.is_none()
            || !matches!(request.limits.output_token_limit, Some(1..))
            || !matches!(request.response_contract, ResponseContract::Text)
        {
            return Err(AgentRuntimeError::InvalidRequest(
                "chat requires positive token, output, reserve and finite request bounds, and text output".to_owned(),
            ));
        }
        Ok(Self {
            policy,
            deadline: tokio::time::Instant::now() + request.limits.deadline,
            charged_tokens: 0,
        })
    }

    pub(super) fn final_answer(
        &self,
        request: &AgentRequest,
        requests: u32,
        tool_calls: u32,
        history: &[Message],
        tools: &[RigToolDefinition],
    ) -> Result<bool, AgentRuntimeError> {
        if tokio::time::Instant::now() >= self.deadline {
            return Err(AgentRuntimeError::DeadlineExceeded);
        }
        // Estimate serialized input at three UTF-8 bytes per token, with framing and full
        // output capacity reserved. This is a dispatch allowance, not an exact billing cap:
        // provider tokenization can differ and actual usage arrives after a response.
        let projected = serialized_token_estimate(&(history, tools))
            .saturating_add(request.limits.output_token_limit.unwrap_or_default())
            .saturating_add((CHAT_FINAL_ANSWER_INSTRUCTION.len() as u64).div_ceil(3))
            .saturating_add(1024);
        Ok(
            requests.saturating_add(1) >= request.limits.request_limit.unwrap_or_default()
                || tool_calls >= request.limits.tool_call_limit
                || self
                    .deadline
                    .saturating_duration_since(tokio::time::Instant::now())
                    <= self.policy.final_answer_reserve
                || self
                    .charged_tokens
                    .saturating_add(projected.saturating_mul(2))
                    >= self.policy.cumulative_token_limit,
        )
    }

    pub(super) fn prepare_dispatch(
        &self,
        request: &mut CompletionRequest,
        final_answer: bool,
    ) -> Result<u64, AgentRuntimeError> {
        let mut estimate = estimate_request_tokens(request);
        let remaining = self
            .policy
            .cumulative_token_limit
            .saturating_sub(self.charged_tokens);
        if final_answer && estimate > remaining {
            // Keep the canonical transcript intact. Only the final provider request receives
            // partial source previews, and each pass starts from the original source content.
            let originals = serialized_tool_results(&request.chat_history)?;
            for preview_bytes in [4096, 512, 0] {
                compact_tool_results(&mut request.chat_history, &originals, preview_bytes);
                estimate = estimate_request_tokens(request);
                if estimate <= remaining {
                    break;
                }
            }
        }
        self.check_dispatch(estimate)?;
        Ok(estimate)
    }

    pub(super) fn check_dispatch(&self, estimated_tokens: u64) -> Result<(), AgentRuntimeError> {
        if tokio::time::Instant::now() >= self.deadline {
            return Err(AgentRuntimeError::DeadlineExceeded);
        }
        if self.charged_tokens.saturating_add(estimated_tokens) > self.policy.cumulative_token_limit
        {
            return Err(AgentRuntimeError::ChatBudgetExhausted(
                "cumulative token allowance".to_owned(),
            ));
        }
        Ok(())
    }

    pub(super) fn final_request_timeout(&self) -> std::time::Duration {
        self.policy.final_answer_reserve.min(
            self.deadline
                .saturating_duration_since(tokio::time::Instant::now()),
        )
    }

    pub(super) fn observe(&mut self, response: &CompletionResponse, estimate: u64) {
        let usage = provider_usage(response);
        let tokens = if response_reports_usage(response) {
            usage.input_tokens.saturating_add(usage.output_tokens)
        } else {
            estimate
        };
        self.charged_tokens = self.charged_tokens.saturating_add(tokens);
    }
}

fn serialized_token_estimate(value: &impl serde::Serialize) -> u64 {
    serde_json::to_vec(value).map_or(u64::MAX, |bytes| (bytes.len() as u64).div_ceil(3))
}

pub(super) fn estimate_request_tokens(request: &CompletionRequest) -> u64 {
    serialized_token_estimate(&(
        &request.chat_history,
        &request.tools,
        &request.additional_params,
    ))
    .saturating_add(request.max_tokens.unwrap_or_default())
    .saturating_add(1024)
}

/// A provider may ignore `tool_choice=none`. Preserve usable prose, but never persist or
/// execute its unpaired calls. The host note explicitly marks the unexecuted work.
pub(super) fn normalize_final_response(
    response: &mut CompletionResponse,
) -> Result<bool, AgentRuntimeError> {
    if !response
        .choice
        .iter()
        .any(|part| matches!(part, AssistantContent::ToolCall(_)))
    {
        return Ok(false);
    }
    if !response
        .choice
        .iter()
        .any(|part| matches!(part, AssistantContent::Text(text) if !text.text.trim().is_empty()))
    {
        return Err(AgentRuntimeError::ChatBudgetExhausted(
            "final response requested tools without an answer".to_owned(),
        ));
    }
    response
        .choice
        .retain(|part| matches!(part, AssistantContent::Text(_)));
    response.choice.push(AssistantContent::text(
        "\n\nThe additional actions requested in this response were not run.",
    ));
    Ok(true)
}

type SerializedToolResult = (usize, usize, String);

fn serialized_tool_results(
    history: &[Message],
) -> Result<Vec<SerializedToolResult>, AgentRuntimeError> {
    let mut originals = Vec::new();
    for (message_index, message) in history.iter().enumerate() {
        if let Message::User { content } = message {
            for (content_index, part) in content.iter().enumerate() {
                if let UserContent::ToolResult(result) = part {
                    let serialized = serde_json::to_string(&result.content)
                        .map_err(|error| AgentRuntimeError::InvalidRequest(error.to_string()))?;
                    originals.push((message_index, content_index, serialized));
                }
            }
        }
    }
    Ok(originals)
}

fn compact_tool_results(
    history: &mut [Message],
    originals: &[SerializedToolResult],
    preview_bytes: usize,
) {
    for (message_index, content_index, original) in originals {
        let mut end = original.len().min(preview_bytes);
        while !original.is_char_boundary(end) {
            end -= 1;
        }
        let replacement = ToolResultContent::json(serde_json::json!({
            "budget_omitted": true,
            "original_bytes": original.len(),
            "preview": &original[..end],
        }));
        // Never enlarge a small result or erase it just to add the omission marker.
        if serde_json::to_vec(&vec![&replacement]).map_or(usize::MAX, |bytes| bytes.len())
            >= original.len()
        {
            continue;
        }
        if let Message::User { content } = &mut history[*message_index]
            && let UserContent::ToolResult(result) = &mut content[*content_index]
        {
            result.content = vec![replacement];
        }
    }
}
