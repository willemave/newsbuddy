use super::*;
use newsly_agent_runtime::{
    AgentLimits, BoxToolFuture, NewslyTranscript, ToolDefinition, ToolOutput, ToolPolicy,
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

/// `look` returns one image; `read` returns none.
#[derive(Debug)]
struct ImageTools;
impl ToolExecutor for ImageTools {
    fn execute(&self, call: NewslyToolCall, _: Arc<dyn AgentEventSink>) -> BoxToolFuture<'_> {
        Box::pin(async move {
            let images = if call.name == "look" {
                vec![ToolImage {
                    media_type: ImageMediaType::Jpeg,
                    data_base64: "AAAA".to_owned(),
                }]
            } else {
                Vec::new()
            };
            Ok(ToolOutput {
                content: serde_json::json!({"tool": call.name}),
                is_error: false,
                images,
            })
        })
    }
}

fn call(index: u32, name: &str) -> AssistantContent {
    AssistantContent::ToolCall(RigToolCall {
        id: ToolCallId::new(format!("call_{index}")).expect("id"),
        provider: None,
        function: ToolFunction::new(name.to_owned(), serde_json::json!({})),
        signature: None,
        additional_params: None,
    })
}

fn request() -> AgentRequest {
    AgentRequest {
        feature: "learning_deck_generation".into(),
        model_spec: "openrouter:anthropic/claude-sonnet-5.5".into(),
        system_prompt: "Deck".into(),
        user_prompt: "Build".into(),
        transcript: NewslyTranscript::default(),
        response_contract: ResponseContract::Text,
        tools: ["look", "read"]
            .into_iter()
            .map(|name| ToolDefinition {
                name: name.into(),
                description: name.into(),
                input_schema: schemars::json_schema!({"type":"object"}),
            })
            .collect(),
        tool_policy: ToolPolicy {
            allowed: ["look".to_owned(), "read".to_owned()].into(),
            require_tool: false,
            allow_parallel_calls: true,
        },
        limits: AgentLimits {
            request_limit: None,
            tool_call_limit: 10,
            output_token_limit: Some(1024),
            deadline: Duration::from_secs(60),
        },
        provider_parameters: Map::new(),
    }
}

#[tokio::test]
async fn tool_images_follow_every_tool_result_of_the_batch_and_replay_identically() {
    let batch = CompletionResponse::new(
        vec![call(1, "look"), call(2, "read")],
        Usage::default(),
        "openrouter",
    );
    let done = CompletionResponse::new(
        vec![AssistantContent::text("Done")],
        Usage::default(),
        "openrouter",
    );
    let requests = Arc::new(Mutex::new(Vec::new()));
    let model = RigModel::Fake {
        responses: Mutex::new(vec![batch, done].into()),
        requests: requests.clone(),
    };
    let engine = RigAgentEngine::new(ProviderCredentials {
        openai: None,
        anthropic: None,
        google: None,
        openrouter: None,
    });
    let outcome = engine
        .run_inner(
            request(),
            Arc::new(ImageTools),
            Arc::new(Events),
            None,
            Some(model),
        )
        .await
        .expect("run completes");
    let sent = requests.lock().unwrap();
    let history = &sent[1].chat_history;

    // tool result (look), tool result (read), then the image in its own user message.
    let tail: Vec<_> = history.iter().rev().take(3).rev().collect();
    let kinds: Vec<&str> = tail
        .iter()
        .map(|message| match message {
            Message::User { content } => match (content.first(), content.get(1)) {
                (Some(UserContent::ToolResult(_)), _) => "tool_result",
                (Some(UserContent::Text(text)), Some(UserContent::Image(_)))
                    if text.text.contains("look (1)") =>
                {
                    "labelled_images"
                }
                _ => "other",
            },
            _ => "other",
        })
        .collect();
    assert_eq!(kinds, ["tool_result", "tool_result", "labelled_images"]);

    let wire: Vec<Value> = tail
        .iter()
        .flat_map(|message| {
            openrouter::messages_from_rig_message((*message).clone()).expect("openrouter wire")
        })
        .map(|message| serde_json::to_value(message).expect("json"))
        .collect();
    let roles: Vec<&str> = wire.iter().map(|m| m["role"].as_str().unwrap()).collect();
    assert_eq!(roles, ["tool", "tool", "user"]);
    assert_eq!(
        wire[2]["content"][1]["image_url"]["url"],
        "data:image/jpeg;base64,AAAA"
    );

    // The returned transcript replays to the same provider history and is never storable.
    let replayed = rig_history_from_transcript(&outcome.transcript).expect("replay");
    let replayed_tail: Vec<_> = replayed
        .iter()
        .rev()
        .skip(1)
        .take(3)
        .rev()
        .cloned()
        .collect();
    assert_eq!(replayed_tail, tail.into_iter().cloned().collect::<Vec<_>>());
    assert!(matches!(
        outcome.transcript.validate_durable(),
        Err(newsly_agent_runtime::TranscriptError::InlineImage(_))
    ));
}

/// Live check of the deck route: `OpenRouter` Claude with signed reasoning sees a tool image and
/// replays the image-bearing transcript. Run with
/// `cargo test -p newsly-providers --lib live_openrouter_tool_image -- --ignored` and
/// `OPENROUTER_API_KEY` set.
#[tokio::test]
#[ignore = "calls OpenRouter; costs money"]
async fn live_openrouter_tool_image_round_trips_with_reasoning() {
    // 96x64 JPEG: green left half, orange right half.
    const SWATCH: &str = "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAUDBAQEAwUEBAQFBQUGBwwIBwcHBw8LCwkMEQ8SEhEPERETFhwXExQaFRERGCEYGh0dHx8fExciJCIeJBweHx7/2wBDAQUFBQcGBw4ICA4eFBEUHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh7/wAARCABAAGADASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwDzyiiitT8sCiiigAooooA9Yooor8MP64CiiigAooooA8nooor9zP5HCiiigAooooA9Yooor8MP64CiiigAooooA8nooor9zP5HCiiigAooooA9Yooor8MP64CiiigAooooA8nooor9zP5HCiiigAooooA9Yooor8MP64CiiigAooooA//Z";
    #[derive(Debug)]
    struct Swatch;
    impl ToolExecutor for Swatch {
        fn execute(&self, _: NewslyToolCall, _: Arc<dyn AgentEventSink>) -> BoxToolFuture<'_> {
            Box::pin(async {
                Ok(ToolOutput {
                    content: serde_json::json!({"status": "rendered", "sheets": [[1]]}),
                    is_error: false,
                    images: vec![ToolImage {
                        media_type: ImageMediaType::Jpeg,
                        data_base64: SWATCH.to_owned(),
                    }],
                })
            })
        }
    }
    let engine = RigAgentEngine::new(ProviderCredentials::from_env());
    let mut first = request();
    first.limits.deadline = Duration::from_secs(240);
    first.limits.output_token_limit = Some(16_000);
    first.user_prompt =
        "Call look exactly once, then name the two colors you see from left to right.".into();
    first.provider_parameters.insert(
        "reasoning".into(),
        serde_json::json!({"enabled": true, "effort": "high"}),
    );
    let outcome = engine
        .run(first.clone(), Arc::new(Swatch), Arc::new(Events))
        .await
        .expect("first turn");
    assert!(outcome.tool_call_count >= 1);
    assert!(
        outcome.output_text.to_lowercase().contains("green"),
        "{}",
        outcome.output_text
    );

    let mut second = first;
    second.transcript = outcome.transcript;
    second.user_prompt = "Without calling tools: which color was on the right?".into();
    let replay = engine
        .run(second, Arc::new(Swatch), Arc::new(Events))
        .await
        .expect("replay with image history");
    assert!(
        replay.output_text.to_lowercase().contains("orange"),
        "{}",
        replay.output_text
    );
}
