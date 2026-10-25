use super::*;
use newsly_agent_runtime::{
    AgentLimits, BoxToolFuture, ChatBudget, NewslyTranscript, ToolDefinition, ToolOutput,
    ToolPolicy,
};
use rig_core::completion::Usage;
use std::{sync::Mutex, time::Duration};

#[derive(Debug)]
struct Events;
impl AgentEventSink for Events {
    fn publish(&self, _: AgentEvent) -> Result<(), AgentRuntimeError> {
        Ok(())
    }
}
#[derive(Debug)]
struct Tools;
impl ToolExecutor for Tools {
    fn execute(&self, call: NewslyToolCall, _: Arc<dyn AgentEventSink>) -> BoxToolFuture<'_> {
        Box::pin(async move {
            let content = call.arguments.get("result_bytes").and_then(Value::as_u64)
                .map_or_else(|| serde_json::json!({"confirmed":true}), |size| serde_json::json!({"source_evidence": "e".repeat(usize::try_from(size).expect("fixture size"))}));
            Ok(ToolOutput {
                content,
                is_error: false,
            })
        })
    }
}
fn request(limit: u32) -> AgentRequest {
    AgentRequest {
        feature: "chat".into(),
        model_spec: "openai:test".into(),
        system_prompt: "Assistant".into(),
        user_prompt: "Work".into(),
        transcript: NewslyTranscript::default(),
        response_contract: ResponseContract::Text,
        tools: vec![ToolDefinition {
            name: "read".into(),
            description: "read".into(),
            input_schema: schemars::json_schema!({"type":"object"}),
        }],
        tool_policy: ToolPolicy {
            allowed: ["read".to_owned()].into(),
            require_tool: true,
            allow_parallel_calls: false,
        },
        limits: AgentLimits {
            request_limit: Some(limit),
            tool_call_limit: 100,
            output_token_limit: Some(1024),
            deadline: Duration::from_secs(300),
        },
        provider_parameters: Map::new(),
    }
}
fn budget(tokens: u64) -> ChatBudget {
    ChatBudget {
        cumulative_token_limit: tokens,
        final_answer_reserve: Duration::from_secs(30),
    }
}

#[tokio::test]
async fn openrouter_claude_keeps_reasoning_and_serial_tool_policy_through_the_loop() {
    let mut request = request(3);
    request.model_spec = "openrouter:anthropic/claude-sonnet-5.5".into();
    request.tool_policy.require_tool = false;
    request
        .provider_parameters
        .insert("reasoning".into(), serde_json::json!({"enabled": true}));
    let (outcome, requests) = run(request.clone(), vec![tool(1), answer()], None).await;
    assert_eq!(outcome.unwrap().tool_call_count, 1);
    assert_eq!(requests.len(), 2);
    for sent in &requests {
        let parameters = sent.additional_params.as_ref().unwrap();
        assert_eq!(
            parameters["reasoning"],
            serde_json::json!({"enabled": true})
        );
        assert!(parameters.get("parallel_tool_calls").is_none());
    }

    let parallel = CompletionResponse::new(
        vec![tool(1).choice[0].clone(), tool(2).choice[0].clone()],
        Usage::default(),
        "openrouter",
    );
    let (outcome, _) = run(request, vec![parallel], None).await;
    assert!(
        matches!(outcome, Err(AgentRuntimeError::InvalidRequest(message))
        if message.contains("parallel tool calls"))
    );
}

fn tool(index: u32) -> CompletionResponse {
    CompletionResponse::new(
        vec![AssistantContent::ToolCall(RigToolCall {
            id: ToolCallId::new(format!("call_{index}")).expect("id"),
            provider: None,
            function: ToolFunction::new("read".to_owned(), serde_json::json!({})),
            signature: None,
            additional_params: None,
        })],
        Usage::default(),
        "openai",
    )
}
fn answer() -> CompletionResponse {
    CompletionResponse::new(
        vec![AssistantContent::text("Confirmed")],
        Usage::default(),
        "openai",
    )
}
async fn run(
    request: AgentRequest,
    responses: Vec<CompletionResponse>,
    budget: Option<ChatBudget>,
) -> (
    Result<AgentOutcome, AgentRuntimeError>,
    Vec<CompletionRequest>,
) {
    let requests = Arc::new(Mutex::new(Vec::new()));
    let model = RigModel::Fake {
        responses: Mutex::new(responses.into()),
        requests: requests.clone(),
    };
    let engine = RigAgentEngine::new(ProviderCredentials {
        openai: None,
        anthropic: None,
        google: None,
        openrouter: None,
    });
    let result = engine
        .run_inner(
            request,
            Arc::new(Tools),
            Arc::new(Events),
            budget,
            Some(model),
        )
        .await;
    let captured = std::mem::take(&mut *requests.lock().expect("requests"));
    (result, captured)
}
#[tokio::test]
async fn more_than_eight_productive_requests_complete() {
    let mut responses = (0..10).map(tool).collect::<Vec<_>>();
    responses.push(answer());
    let (outcome, requests) = run(request(32), responses, Some(budget(256_000))).await;
    let outcome = outcome.expect("productive chat completes");
    assert_eq!(outcome.request_count, 11);
    assert_eq!(outcome.tool_call_count, 10);
    assert_eq!(requests.len(), 11);
}
#[tokio::test]
async fn last_request_is_tool_free_and_cannot_execute_unoffered_tools() {
    let (outcome, requests) = run(request(2), vec![tool(1), tool(2)], Some(budget(256_000))).await;
    assert!(matches!(
        outcome,
        Err(AgentRuntimeError::ChatBudgetExhausted(_))
    ));
    assert_eq!(requests.len(), 2);
    assert!(!requests[0].tools.is_empty());
    assert!(!requests[1].tools.is_empty());
    assert_eq!(requests[1].tool_choice, Some(ToolChoice::None));
}
#[tokio::test]
async fn time_reserve_allows_final_without_required_tool() {
    let mut request = request(32);
    request.limits.deadline = Duration::from_secs(5);
    let (outcome, requests) = run(request, vec![answer()], Some(budget(256_000))).await;
    assert!(outcome.is_ok());
    assert_eq!(requests[0].tool_choice, Some(ToolChoice::None));
}
#[tokio::test]
async fn insufficient_tokens_prevent_even_final_dispatch() {
    let (outcome, requests) = run(request(32), vec![answer()], Some(budget(1))).await;
    assert!(matches!(
        outcome,
        Err(AgentRuntimeError::ChatBudgetExhausted(_))
    ));
    assert!(requests.is_empty());
}
#[tokio::test]
async fn ordinary_finite_limit_is_unchanged() {
    let (outcome, requests) = run(request(2), vec![tool(1), tool(2)], None).await;
    assert!(matches!(
        outcome,
        Err(AgentRuntimeError::RequestLimitExceeded)
    ));
    assert_eq!(requests.len(), 2);
    assert!(requests.iter().all(|request| !request.tools.is_empty()));
}
#[tokio::test]
async fn expired_deadline_cannot_dispatch() {
    let request = request(32);
    let mut state = ChatBudgetState::new(budget(256_000), &request).expect("state");
    state.deadline = tokio::time::Instant::now();
    assert!(matches!(
        state.check_dispatch(1),
        Err(AgentRuntimeError::DeadlineExceeded)
    ));
}

#[tokio::test]
async fn observed_cumulative_usage_reserves_a_tool_free_answer() {
    let mut first = tool(1);
    first.usage.input_tokens = 252_000;
    first.raw = serde_json::json!({"usage":{"input_tokens":252_000,"output_tokens":0,"total_tokens":252_000}});
    let (outcome, requests) = run(request(32), vec![first, answer()], Some(budget(256_000))).await;
    assert!(outcome.is_ok());
    assert_eq!(requests.len(), 2);
    assert!(!requests[1].tools.is_empty());
    assert_eq!(requests[1].tool_choice, Some(ToolChoice::None));
}

#[tokio::test]
async fn observed_overshoot_is_accounted_and_blocks_another_dispatch() {
    let mut first = tool(1);
    first.usage.input_tokens = 300_000;
    first.raw = serde_json::json!({"usage":{"input_tokens":300_000,"output_tokens":0,"total_tokens":300_000}});
    let (outcome, requests) = run(request(32), vec![first, answer()], Some(budget(256_000))).await;
    assert!(matches!(
        outcome,
        Err(AgentRuntimeError::ChatBudgetExhausted(_))
    ));
    assert_eq!(requests.len(), 1);
}

#[tokio::test]
async fn request_ceiling_reserves_one_successful_final_answer() {
    let (outcome, requests) = run(request(2), vec![tool(1), answer()], Some(budget(256_000))).await;
    let outcome = outcome.expect("final response");
    assert_eq!(outcome.request_count, 2);
    assert_eq!(outcome.tool_call_count, 1);
    assert_eq!(requests.len(), 2);
    assert_eq!(requests[1].tool_choice, Some(ToolChoice::None));
    assert_eq!(requests[1].tools[0].name, "read");
    assert!(requests[1].chat_history.iter().any(|message| matches!(message, Message::Assistant { content, .. } if content.iter().any(|part| matches!(part, AssistantContent::ToolCall(_))))));
}

#[tokio::test]
async fn final_timeout_stays_inside_absolute_deadline_and_reserve() {
    let mut request = request(32);
    request.limits.deadline = Duration::from_secs(5);
    let state = ChatBudgetState::new(budget(256_000), &request).expect("state");
    assert!(state.final_request_timeout() <= Duration::from_secs(5));
    request.limits.deadline = Duration::from_secs(300);
    let state = ChatBudgetState::new(budget(256_000), &request).expect("state");
    assert!(state.final_request_timeout() <= Duration::from_secs(30));
}

#[tokio::test]
async fn long_article_and_full_history_allow_productive_tools_before_final_answer() {
    let mut request = request(3);
    request.limits.output_token_limit = Some(8_000);
    request.system_prompt = format!(
        "Article context: {}",
        "Read this article. "
            .repeat(5_000)
            .chars()
            .take(80_000)
            .collect::<String>()
    );
    request.transcript.messages.push(NewslyMessage {
        id: None,
        role: MessageRole::User,
        parts: vec![MessagePart::Request(RequestPart::Text {
            text: "Prior reading context. "
                .repeat(3_000)
                .chars()
                .take(64_000)
                .collect(),
        })],
        created_at: Utc::now(),
        run_id: None,
        provider: None,
        model: None,
        finish_reason: None,
        usage: ProviderUsage::default(),
        metadata: Map::new(),
    });
    request.tools[0].description = "Read source material. ".repeat(760);
    let (outcome, requests) = run(
        request,
        vec![tool(1), tool(2), answer()],
        Some(budget(256_000)),
    )
    .await;
    let outcome = outcome.expect("long context still permits useful work and a final answer");
    assert_eq!(outcome.output_text, "Confirmed");
    assert_eq!(outcome.tool_call_count, 2);
    assert_eq!(requests.len(), 3);
    assert_eq!(requests[0].tool_choice, Some(ToolChoice::Required));
    assert_eq!(requests[1].tool_choice, Some(ToolChoice::Auto));
    assert_eq!(requests[2].tool_choice, Some(ToolChoice::None));
    assert_eq!(
        outcome
            .transcript
            .messages
            .iter()
            .filter(|message| message.role == MessageRole::Tool)
            .count(),
        2
    );
}

#[tokio::test]
async fn final_text_survives_unexecuted_calls_without_orphaning_history() {
    let mut final_response = tool(2);
    final_response
        .choice
        .push(AssistantContent::Reasoning(Reasoning {
            id: Some("internal".to_owned()),
            content: vec![ReasoningContent::Summary(
                "Internal planning must stay hidden".to_owned(),
            )],
        }));
    final_response
        .choice
        .insert(0, AssistantContent::text("Here is the confirmed result."));
    let (outcome, requests) = run(
        request(2),
        vec![tool(1), final_response],
        Some(budget(256_000)),
    )
    .await;
    let outcome = outcome.expect("usable final prose");
    assert_eq!(requests.len(), 2);
    assert_eq!(outcome.tool_call_count, 1);
    assert!(!outcome.output_text.contains("Internal planning"));
    assert!(
        outcome
            .output_text
            .starts_with("Here is the confirmed result.")
    );
    assert!(
        outcome
            .output_text
            .ends_with("The additional actions requested in this response were not run.")
    );
    let final_message = outcome.transcript.messages.last().expect("final message");
    assert_eq!(
        final_message.finish_reason,
        Some(TranscriptFinishReason::Stop)
    );
    assert!(
        final_message
            .parts
            .iter()
            .all(|part| matches!(part, MessagePart::Assistant(AssistantPart::Text { .. })))
    );
    outcome.transcript.validate().expect("canonical transcript");
    let replayed = rig_history_from_transcript(&outcome.transcript).expect("history replays");
    assert_eq!(replayed.iter().filter(|message| matches!(message, Message::Assistant { content, .. } if content.iter().any(|part| matches!(part, AssistantContent::ToolCall(_))))).count(), 1);
}

#[tokio::test]
async fn retired_tool_names_replay_without_becoming_executable() {
    let mut request = request(2);
    for (index, name) in ["find_feed_options", "start_deep_research"]
        .into_iter()
        .enumerate()
    {
        let response = CompletionResponse::new(
            vec![AssistantContent::ToolCall(RigToolCall {
                id: ToolCallId::new(format!("legacy_{index}")).expect("id"),
                provider: None,
                function: ToolFunction::new(name.to_owned(), serde_json::json!({})),
                signature: None,
                additional_params: None,
            })],
            Usage::default(),
            "openai",
        );
        request.transcript.messages.push(NewslyMessage {
            id: None,
            role: MessageRole::Assistant,
            parts: newsly_assistant_parts(&response.choice),
            created_at: Utc::now(),
            run_id: None,
            provider: None,
            model: None,
            finish_reason: Some(TranscriptFinishReason::ToolCall),
            usage: ProviderUsage::default(),
            metadata: Map::new(),
        });
        request.transcript.messages.push(NewslyMessage {
            id: None,
            role: MessageRole::Tool,
            parts: vec![MessagePart::Request(RequestPart::ToolResult {
                tool_call_id: format!("legacy_{index}"),
                provider_call_id: None,
                provider_item_id: None,
                tool_name: name.to_owned(),
                content: serde_json::json!({"historical":true}),
                is_error: false,
            })],
            created_at: Utc::now(),
            run_id: None,
            provider: None,
            model: None,
            finish_reason: None,
            usage: ProviderUsage::default(),
            metadata: Map::new(),
        });
    }
    request.tool_policy.require_tool = false;
    let (outcome, requests) = run(request, vec![answer()], Some(budget(256_000))).await;
    let outcome = outcome.expect("retired calls replay");
    assert_eq!(outcome.tool_call_count, 0);
    assert_eq!(requests[0].tools.len(), 1);
    assert_eq!(requests[0].tools[0].name, "read");
    assert_eq!(requests[0].chat_history.iter().filter(|message| matches!(message, Message::Assistant { content, .. } if content.iter().any(|part| matches!(part, AssistantContent::ToolCall(_))))).count(), 2);
    outcome
        .transcript
        .validate()
        .expect("canonical historical transcript");
}

#[tokio::test]
async fn oversized_read_results_leave_a_bounded_partial_final_answer() {
    for (size, observed, preview_limit) in
        [(100_000_u64, 236_000_u64, 4096), (500_000, 246_000, 512)]
    {
        let mut request = request(32);
        request.limits.output_token_limit = Some(8_000);
        let mut response = tool(1);
        if let AssistantContent::ToolCall(call) = &mut response.choice[0] {
            call.function.arguments = serde_json::json!({"result_bytes": size});
            call.provider = ProviderCallId::new("provider_call".to_owned())
                .map(|id| id.with_item_id("provider_item".to_owned()));
        }
        response.usage.input_tokens = observed;
        response.raw = serde_json::json!({"usage":{"input_tokens":observed,"output_tokens":0,"total_tokens":observed}});
        let mut final_response = answer();
        final_response.choice = vec![AssistantContent::text(
            "Partial source evidence is available; remaining source content was omitted.",
        )];
        let (outcome, requests) = run(
            request,
            vec![response, final_response],
            Some(budget(256_000)),
        )
        .await;
        let outcome = outcome.expect("large read result permits partial final answer");
        assert_eq!(requests.len(), 2);
        assert_eq!(outcome.tool_call_count, 1);
        assert_eq!(requests[0].tool_choice, Some(ToolChoice::Required));
        assert_eq!(requests[1].tool_choice, Some(ToolChoice::None));
        assert!(super::chat_budget::estimate_request_tokens(&requests[1]) <= 256_000 - observed);
        let result = requests[1]
            .chat_history
            .iter()
            .find_map(|message| match message {
                Message::User { content } => content.iter().find_map(|part| match part {
                    UserContent::ToolResult(result) => Some(result),
                    _ => None,
                }),
                _ => None,
            })
            .expect("paired read result");
        assert_eq!(result.call.as_str(), "call_1");
        assert_eq!(result.name, "read");
        let provider = result.provider.as_ref().expect("provider identity");
        assert_eq!(provider.call_id, "provider_call");
        assert_eq!(provider.item_id.as_deref(), Some("provider_item"));
        let partial = result.content[0].as_json().expect("partial envelope");
        assert_eq!(partial["budget_omitted"], true);
        assert!(
            !partial["preview"]
                .as_str()
                .expect("source preview")
                .is_empty()
        );
        assert!(partial["preview"].as_str().expect("source preview").len() <= preview_limit);
        let original = outcome
            .transcript
            .messages
            .iter()
            .find_map(|message| {
                message.parts.iter().find_map(|part| match part {
                    MessagePart::Request(RequestPart::ToolResult { content, .. }) => Some(content),
                    _ => None,
                })
            })
            .expect("original result remains canonical");
        assert_eq!(
            original["source_evidence"]
                .as_str()
                .expect("full evidence")
                .len() as u64,
            size
        );
        outcome
            .transcript
            .validate()
            .expect("canonical full transcript");
        assert!(
            requests[1]
                .chat_history
                .iter()
                .any(|message| matches!(message, Message::System { .. }))
        );
        assert!(outcome.output_text.contains("Partial source evidence"));
    }
}
