use super::*;
use crate::{BriefingCompositionGatewayError, BriefingFigurePlacement, BriefingSuggestedQuote};
use newsly_agent_runtime::AgentRuntimeError;
fn source(id: i64) -> BriefingCompositionSource {
    serde_json::from_value(serde_json::json!({
        "source_key":format!("news:{id}"), "kind":"news", "id":id,
        "title":format!("Source {id}"), "key_points":[]
    }))
    .unwrap()
}

fn decode(markdown: &str) -> Result<BriefingCompositionLayout, BriefingLayoutRejection> {
    decode_composition_layout(
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
            decode_composition_layout(Some(invalid), "news", &[source(1)])
                .unwrap_err()
                .code(),
            "decode_failed"
        );
    }
    for tier in ["audio", "longform"] {
        assert_eq!(
            composition_schema(tier),
            schemars::schema_for!(BriefingCompositionLayout)
        );
        assert_eq!(
            briefing_composition_response_contract(tier),
            "briefing_composer_layout_v1"
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
    let layout = decode(good).unwrap();
    assert!(layout.suggested_quotes.is_empty());
    assert_eq!(
        layout.blocks,
        vec![BriefingCompositionBlock::Passage {
            markdown: good.to_owned(),
            weight: BriefingPassageWeight::Brief
        }]
    );
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
        decode_composition_layout(None, "news", &[source(1)])
            .unwrap_err()
            .code(),
        "missing_structured_output"
    );
    assert!(decode_composition_layout(Some(serde_json::json!({"markdown":"[One](newsly://briefing/news/1) and [ten](newsly://briefing/news/10)."})),"news", &[source(1), source(10)]).is_ok());
}

#[test]
fn rich_prompts_and_schema_stay_unchanged_and_feedback_is_preserved() {
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
            original
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
fn real_rich_validators_emit_bounded_codes_and_original_feedback() {
    let passage = || BriefingCompositionBlock::Passage {
        markdown: "[A source phrase](newsly://briefing/news/1) explains the result.".to_owned(),
        weight: BriefingPassageWeight::Feature,
    };
    let cases = [
        (
            BriefingCompositionLayout {
                suggested_quotes: vec![],
                blocks: vec![],
            },
            "audio",
            "empty_layout",
            "layout has no blocks",
        ),
        (
            BriefingCompositionLayout {
                suggested_quotes: vec![BriefingSuggestedQuote {
                    id: String::new(),
                    text: "private quote".to_owned(),
                }],
                blocks: vec![passage()],
            },
            "audio",
            "invalid_suggested_quotes",
            "suggested quotes must have unique bounded IDs and text",
        ),
        (
            BriefingCompositionLayout {
                suggested_quotes: vec![],
                blocks: vec![
                    passage(),
                    BriefingCompositionBlock::Figure {
                        source_key: "news:1".to_owned(),
                        caption: "Caption".to_owned(),
                        placement: BriefingFigurePlacement::Full,
                        alignment: None,
                    },
                ],
            },
            "audio",
            "invalid_figure",
            "figure does not reference an imaged source",
        ),
        (
            BriefingCompositionLayout {
                suggested_quotes: vec![],
                blocks: vec![
                    passage(),
                    BriefingCompositionBlock::Pullquote {
                        suggestion_id: "private suggestion".to_owned(),
                    },
                ],
            },
            "audio",
            "invalid_pullquote",
            "pullquote references an unknown or reused suggestion",
        ),
        (
            BriefingCompositionLayout {
                suggested_quotes: vec![],
                blocks: vec![
                    passage(),
                    BriefingCompositionBlock::Passage {
                        markdown: "Another substantive fact.".to_owned(),
                        weight: BriefingPassageWeight::Brief,
                    },
                ],
            },
            "news",
            "news_passage_count",
            "news layout must contain exactly one passage",
        ),
        (
            BriefingCompositionLayout {
                suggested_quotes: vec![],
                blocks: vec![BriefingCompositionBlock::Passage {
                    markdown: "A substantive fact without citation.".to_owned(),
                    weight: BriefingPassageWeight::Brief,
                }],
            },
            "audio",
            "source_link_missing",
            "source news:1 must be linked at least once",
        ),
    ];
    for (layout, tier, code, feedback) in cases {
        let error = layout.validate(tier, &[source(1)]).unwrap_err();
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
    let missing = decode_composition_layout(Some(serde_json::json!({"markdown":"[A shared-event report](newsly://briefing/news/1) explains the result."})), "news", &request.sources).unwrap_err();
    assert_eq!(missing.code(), "source_link_missing");
    assert!(decode_composition_layout(Some(serde_json::json!({"markdown":"[One report of the event](newsly://briefing/news/1) and [another report of the event](newsly://briefing/news/10) explain the result."})), "news", &request.sources).is_ok());
}
