use newsly_agent_runtime::AgentRuntimeError;
use rig_core::completion::{AssistantContent, CompletionResponse, Usage};
use rig_core::message::{Reasoning, ReasoningContent};
use serde_json::json;

use super::{
    assistant_content, enforce_request_limit, newsly_assistant_parts, provider_reported_cost_usd,
    response_reports_usage,
};

#[test]
fn finite_request_limit_stops_the_rig_loop_at_the_bound() {
    assert!(enforce_request_limit(1, Some(2)).is_ok());
    assert!(matches!(
        enforce_request_limit(2, Some(2)),
        Err(AgentRuntimeError::RequestLimitExceeded)
    ));
}

#[test]
fn absent_request_limit_never_stops_the_rig_loop_by_count() {
    assert!(enforce_request_limit(u32::MAX, None).is_ok());
}

#[test]
fn usage_is_observed_only_for_complete_provider_counters() {
    let valid = CompletionResponse::new(Vec::new(), Usage::default(), "openai")
        .with_raw(json!({"usage": {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12}}));
    let empty = CompletionResponse::new(Vec::new(), Usage::default(), "openai")
        .with_raw(json!({"usage": {}}));
    let malformed = CompletionResponse::new(Vec::new(), Usage::default(), "anthropic")
        .with_raw(json!({"usage": {"input_tokens": "10", "output_tokens": 2}}));
    let google = CompletionResponse::new(Vec::new(), Usage::default(), "gemini")
        .with_raw(json!({"usageMetadata": {"promptTokenCount": 10, "totalTokenCount": 10}}));

    assert!(response_reports_usage(&valid));
    assert!(!response_reports_usage(&empty));
    assert!(!response_reports_usage(&malformed));
    assert!(response_reports_usage(&google));
}

#[test]
fn openrouter_account_charge_is_captured_only_when_finite_and_nonnegative() {
    let charged = CompletionResponse::new(Vec::new(), Usage::default(), "openrouter")
        .with_raw(json!({"usage": {"cost": 0.0042}}));
    let negative = CompletionResponse::new(Vec::new(), Usage::default(), "openrouter")
        .with_raw(json!({"usage": {"cost": -0.0042}}));
    let other_provider = CompletionResponse::new(Vec::new(), Usage::default(), "openai")
        .with_raw(json!({"usage": {"cost": 0.0042}}));

    let cost = provider_reported_cost_usd(&charged).expect("valid account charge");
    assert!((cost - 0.0042).abs() < f64::EPSILON);
    assert_eq!(provider_reported_cost_usd(&negative), None);
    assert_eq!(provider_reported_cost_usd(&other_provider), None);
}

#[test]
fn reasoning_item_identity_and_order_survive_newsly_transcript_round_trip() {
    let original = vec![AssistantContent::Reasoning(Reasoning {
        id: Some("rs_reasoning_item".to_owned()),
        content: vec![
            ReasoningContent::Summary("short summary".to_owned()),
            ReasoningContent::Encrypted("encrypted payload".to_owned()),
        ],
    })];

    let durable = newsly_assistant_parts(&original);
    let replayed = assistant_content(&durable).expect("durable reasoning should replay");

    assert_eq!(replayed, original);
}
