use super::*;
use crate::BriefingCompositionGatewayError;
use newsly_agent_runtime::AgentRuntimeError;
fn source(id: i64) -> BriefingCompositionSource {
    serde_json::from_value(serde_json::json!({
        "source_key":format!("news:{id}"), "kind":"news", "id":id,
        "title":format!("Source {id}"), "key_points":[]
    }))
    .unwrap()
}

fn decode(markdown: &str) -> Result<String, BriefingLayoutRejection> {
    decode_composition_passage(
        Some(serde_json::json!({"markdown": markdown})),
        "news",
        &[source(1)],
    )
}

#[test]
fn compact_contract_eliminates_unused_layout_choices() {
    let schema = serde_json::to_value(composition_schema("news")).unwrap();
    assert_eq!(schema["additionalProperties"], false);
    assert_eq!(schema["required"], serde_json::json!(["markdown"]));
    assert_eq!(schema["properties"].as_object().unwrap().len(), 1);
    let validator = jsonschema::validator_for(&schema).unwrap();
    assert!(validator.is_valid(&serde_json::json!({"markdown": "A passage"})));
    for invalid in [
        serde_json::json!({"markdown": 1}),
        serde_json::json!({"markdown":"A passage", "blocks": []}),
        serde_json::json!({}),
    ] {
        assert!(!validator.is_valid(&invalid));
        assert_eq!(
            decode_composition_passage(Some(invalid), "news", &[source(1)])
                .unwrap_err()
                .code(),
            "decode_failed"
        );
    }
    for tier in ["audio", "longform"] {
        let schema = serde_json::to_value(composition_schema(tier)).unwrap();
        assert_eq!(schema["additionalProperties"], false);
        assert_eq!(schema["required"], serde_json::json!(["markdown"]));
        assert_eq!(schema["properties"].as_object().unwrap().len(), 1);
        let validator = jsonschema::validator_for(&schema).unwrap();
        assert!(validator.is_valid(&serde_json::json!({"markdown": "A card"})));
        for invalid in [
            serde_json::json!({"markdown": "A card", "suggested_quotes": []}),
            serde_json::json!({"blocks": [{"type": "figure"}]}),
            serde_json::json!({}),
        ] {
            assert!(!validator.is_valid(&invalid));
            assert_eq!(
                decode_composition_passage(Some(invalid), tier, &[source(1)])
                    .unwrap_err()
                    .code(),
                "decode_failed"
            );
        }
        assert_eq!(
            briefing_composition_response_contract(tier),
            "briefing_card_markdown_v1"
        );
    }
    assert_ne!(
        briefing_composition_response_contract("news"),
        briefing_composition_response_contract("audio")
    );
}

#[test]
fn compact_output_preserves_canonical_layout_and_every_content_validator() {
    let good = "[A substantive source phrase](newsly://briefing/news/1) explains the result.";
    assert_eq!(decode(good).unwrap(), good);
    let cases = [
        ("", "empty_passage", "passage markdown is empty"),
        (
            "No citation",
            "source_link_missing",
            "source news:1 must be linked exactly once",
        ),
        (
            "[Unknown](newsly://briefing/news/10)",
            "unknown_source_link",
            "layout contains an unknown Briefing source link",
        ),
        (
            "[A](newsly://briefing/news/1) and [A](newsly://briefing/news/1)",
            "source_link_duplicated",
            "source news:1 must be linked exactly once",
        ),
        (
            "[A](newsly://briefing/news/1)\n\nAnother paragraph.",
            "news_paragraph_count",
            "news layout must contain exactly one paragraph",
        ),
    ];
    for (markdown, code, detail) in cases {
        let error = decode(markdown).unwrap_err();
        assert_eq!(error.code(), code);
        assert_eq!(error.to_string(), detail);
    }
    assert_eq!(
        decode_composition_passage(None, "news", &[source(1)])
            .unwrap_err()
            .code(),
        "missing_structured_output"
    );
    assert!(decode_composition_passage(Some(serde_json::json!({"markdown":"[One](newsly://briefing/news/1) and [ten](newsly://briefing/news/10)."})),"news", &[source(1), source(10)]).is_ok());
}

#[test]
fn prompts_carry_the_citation_manifest_and_preserve_feedback() {
    for tier in ["audio", "longform", "news"] {
        let request = BriefingCompositionRequest {
            lens_title: "Reading".to_owned(),
            tier: tier.to_owned(),
            sources: vec![source(1)],
        };
        let original = format!(
            "Lens: Reading\nTier: {tier}\n\nSources:\n{}\n\nCompose one complete Briefing window.",
            serde_json::to_string_pretty(&request.sources).unwrap()
        );
        let expected = if tier == "news" {
            format!(
                "{original}\nReturn `markdown` containing the single passage paragraph.\nRequired citation manifest (use each URI exactly once in markdown):\n- news:1 => newsly://briefing/news/1\nCite every source separately, including sources about the same event. Do not replace multiple source citations with one representative link. Before returning, check every URI above appears exactly once."
            )
        } else {
            format!(
                "{original}\nReturn `markdown` containing the card summary for this source.\nRequired citation manifest (link each URI at least once in markdown):\n- news:1 => newsly://briefing/news/1"
            )
        };
        assert_eq!(composition_user_prompt(&request, None).unwrap(), expected);
        assert_eq!(
            composition_user_prompt(&request, Some("source news:1 must be linked exactly once"))
                .unwrap(),
            format!(
                "{expected}\nCorrect this validation failure in the complete replacement layout:\nsource news:1 must be linked exactly once"
            )
        );
    }
}

#[test]
fn provider_reason_codes_are_typed_and_do_not_change_retry_policy() {
    for (source, code, retryable) in [
        (
            AgentRuntimeError::Validation("private output".to_owned()),
            "structured_output_invalid",
            false,
        ),
        (
            AgentRuntimeError::InvalidRequest("private request".to_owned()),
            "invalid_request",
            false,
        ),
        (
            AgentRuntimeError::RequestLimitExceeded,
            "request_limit_exceeded",
            false,
        ),
        (
            AgentRuntimeError::DeadlineExceeded,
            "provider_deadline_exceeded",
            true,
        ),
        (
            AgentRuntimeError::Provider("private body".to_owned()),
            "provider_execution_failed",
            true,
        ),
    ] {
        let error = BriefingCompositionGatewayError::ObservedAgent {
            source,
            usage: None,
        };
        assert_eq!(error.reason_code(), code);
        assert_eq!(error.retryable(), retryable);
    }
}

#[test]
fn card_validators_emit_bounded_codes_and_original_feedback() {
    let cases = [
        (
            serde_json::json!({"markdown": "  "}),
            "empty_passage",
            "passage markdown is empty".to_owned(),
        ),
        (
            serde_json::json!({"markdown": "A substantive fact without citation."}),
            "source_link_missing",
            "source news:1 must be linked at least once".to_owned(),
        ),
        (
            serde_json::json!({"markdown": format!(
                "[A source phrase](newsly://briefing/news/1) explains the result.{}",
                " Private detail.".repeat(6)
            )}),
            "card_sentence_count",
            "card summary has 7 sentences; rewrite it in at most six sentences".to_owned(),
        ),
    ];
    for (value, code, feedback) in cases {
        let error = decode_composition_passage(Some(value), "audio", &[source(1)]).unwrap_err();
        assert_eq!(error.code(), code);
        assert_eq!(error.to_string(), feedback);
        assert!(!error.code().contains("private"));
    }
}

#[test]
fn news_citation_manifest_keeps_same_event_sources_and_uses_canonical_uris() {
    let mut first = source(1);
    first.title = "Shared event".to_owned();
    let mut second = source(10);
    second.title = first.title.clone();
    // Labels identify input rows; citation targets still come from canonical kind/id.
    second.source_key = "second-report".to_owned();
    let request = BriefingCompositionRequest {
        lens_title: "Briefs".to_owned(),
        tier: "news".to_owned(),
        sources: vec![first, second],
    };
    let prompt = composition_user_prompt(&request, None).unwrap();
    assert!(prompt.contains(
        "- news:1 => newsly://briefing/news/1\n- second-report => newsly://briefing/news/10"
    ));
    assert_eq!(prompt.matches("newsly://briefing/news/1\n").count(), 1);
    assert_eq!(prompt.matches("newsly://briefing/news/10\n").count(), 1);
    assert!(prompt.contains("including sources about the same event"));
    assert!(
        prompt.contains("Do not replace multiple source citations with one representative link")
    );
    let missing = decode_composition_passage(Some(serde_json::json!({"markdown":"[A shared-event report](newsly://briefing/news/1) explains the result."})), "news", &request.sources).unwrap_err();
    assert_eq!(missing.code(), "source_link_missing");
    assert!(decode_composition_passage(Some(serde_json::json!({"markdown":"[One report of the event](newsly://briefing/news/1) and [another report of the event](newsly://briefing/news/10) explain the result."})), "news", &request.sources).is_ok());
}
