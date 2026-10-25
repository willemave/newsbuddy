use super::*;

#[test]
fn assistant_context_normalizes_before_durable_staging() {
    let mut payload: AssistantTurnRequest = serde_json::from_value(serde_json::json!({
        "message":"Explain", "screen_context":{
            "screen_type":" ","screen_title":" Reader ","selected_topic":" ",
            "query":" q ","note":" note ","assistant_action":" explain ",
            "visible_content_ids":[0,-1,2,2],"visible_news_item_ids":[-3,4]
        }
    }))
    .unwrap();
    normalize_and_validate_assistant(&mut payload, "request").unwrap();
    assert_eq!(payload.screen_context.screen_type, "unknown");
    assert_eq!(
        payload.screen_context.screen_title.as_deref(),
        Some("Reader")
    );
    assert_eq!(payload.screen_context.selected_topic, None);
    assert_eq!(payload.screen_context.visible_content_ids, vec![2, 2]);
    assert_eq!(payload.screen_context.visible_news_item_ids, vec![4]);
    assert_eq!(
        payload.screen_context.assistant_action.as_deref(),
        Some("explain")
    );
}

#[tokio::test]
async fn retired_research_inputs_decode_but_reject_creation_and_updates() {
    use axum::response::IntoResponse;

    for fields in [
        serde_json::json!({"llm_provider": "deep_research"}),
        serde_json::json!({"llm_model_hint": "deep_research:o4-mini"}),
        serde_json::json!({"llm_provider": "openai", "llm_model_hint": "openai:o4-mini-deep-research-2025-06-26"}),
        serde_json::json!({"llm_model_hint": "o3-deep-research"}),
        serde_json::json!({"llm_model_hint": " OPENAI:O4-MINI-DEEP-RESEARCH "}),
        serde_json::json!({"llm_model_hint": " DEEP_RESEARCH:o4-mini "}),
    ] {
        let create: CreateChatSessionRequest = serde_json::from_value(fields.clone()).unwrap();
        let update: UpdateChatSessionRequest = serde_json::from_value(fields).unwrap();
        for result in [
            validate_create_request(&create, "request"),
            validate_update_request(&update, "request"),
        ] {
            let response = result.unwrap_err().into_response();
            assert_eq!(response.status(), StatusCode::BAD_REQUEST);
            let body = axum::body::to_bytes(response.into_body(), 4096)
                .await
                .unwrap();
            let error: newsly_contracts::ErrorEnvelope = serde_json::from_slice(&body).unwrap();
            assert_eq!(error.code, "deep_research_retired");
            assert!(!error.retryable);
            assert!(error.message.contains("regular chat"));
        }
    }
}

#[tokio::test]
async fn historical_research_models_reject_session_writes_with_stable_error() {
    use axum::response::IntoResponse;

    for (provider, session_type, model) in [
        (
            "openai",
            Some("knowledge_chat"),
            "openai:o4-mini-deep-research",
        ),
        ("openai", Some("article"), " OPENAI:O4-MINI-DEEP-RESEARCH "),
        (" Deep_Research ", None, "o4-mini"),
        ("openai", Some(" DEEP_RESEARCH "), "o4-mini"),
        ("openai", None, " DEEP_RESEARCH:o4-mini "),
    ] {
        let response = reject_retired_session(provider, session_type, model, "request")
            .unwrap_err()
            .into_response();
        assert_eq!(response.status(), StatusCode::BAD_REQUEST);
        let body = axum::body::to_bytes(response.into_body(), 4096)
            .await
            .unwrap();
        let error: newsly_contracts::ErrorEnvelope = serde_json::from_slice(&body).unwrap();
        assert_eq!(error.code, "deep_research_retired");
        assert!(!error.retryable);
    }
    reject_retired_session(
        "openai",
        Some("knowledge_chat"),
        "openai:gpt-6.1-sol",
        "request",
    )
    .unwrap();
}

#[test]
fn openai_chat_defaults_to_sol_and_preserves_explicit_selection() {
    assert_eq!(
        resolve_model(None, None),
        ("openai", "openai:gpt-6.1-sol".to_owned())
    );
    assert_eq!(
        resolve_model(Some(LlmProvider::Openai), Some("openai:gpt-5.6-terra")),
        ("openai", "openai:gpt-5.6-terra".to_owned())
    );
    assert_eq!(
        resolve_model(Some(LlmProvider::Anthropic), None),
        ("anthropic", "anthropic:claude-opus-4-6".to_owned())
    );
}
