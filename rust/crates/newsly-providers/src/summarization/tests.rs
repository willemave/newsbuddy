use super::*;

fn source(content_type: &str, url: &str, platform: Option<&str>) -> SummarizationSource {
    SummarizationSource {
        content_id: 7,
        content_type: content_type.to_owned(),
        title: Some("A useful source".to_owned()),
        url: url.to_owned(),
        source_name: None,
        platform: platform.map(str::to_owned),
        publication_date: None,
        metadata: Value::Object(Map::new()),
        text: "source text".to_owned(),
    }
}

#[test]
fn prompt_rejects_secondary_text_and_preserves_speaker_ownership() {
    let source = source("podcast", "https://example.com/episode", Some("podcast"));
    let hint = resolve_artifact_source_hint(&source);
    let (system, _) = build_prompts(&source, &hint);
    assert!(system.contains("reader comments"));
    assert!(system.contains("Preserve who said each material claim"));
}

#[test]
fn only_podcast_requests_ask_for_a_scene_alongside_the_reader_summary() {
    for (content_type, podcast) in [("podcast", true), ("article", false)] {
        let source = source(content_type, "https://example.com/source", None);
        let hint = resolve_artifact_source_hint(&source);
        let (system, _) = build_prompts(&source, &hint);
        let (_, schema) = summarization_schema(podcast);
        let schema = Value::from(schema);
        assert_eq!(
            system.contains("observable action with its visible consequence"),
            podcast
        );
        assert_eq!(
            schema["properties"].get("podcast_artwork_scene").is_some(),
            podcast
        );
        if podcast {
            assert_eq!(
                schema["properties"]["podcast_artwork_scene"]["type"],
                "string"
            );
            assert!(schema["properties"].get("summary").is_some());
        }
    }
}

#[test]
fn prompt_and_schema_guide_subject_specific_titles() {
    let source = source("podcast", "https://example.com/episode", Some("podcast"));
    let hint = resolve_artifact_source_hint(&source);
    let (system, _) = build_prompts(&source, &hint);
    let schema = serde_json::to_value(schemars::schema_for!(LongformArtifactEnvelope)).unwrap();
    let title_description = schema["properties"]["title"]["description"]
        .as_str()
        .unwrap();
    for policy in [
        "subject-specific reader headline",
        "named entities",
        "qualifiers",
        "interview guests",
        "parentheses",
        "publisher/show branding",
        "newsletter prefixes",
        "episode numbers",
        "durations",
        "guest credentials",
        "promotional clutter",
        "6-12 words when appropriate",
        "technical entities",
    ] {
        assert!(system.contains(policy), "prompt missing {policy}");
        assert!(
            title_description.contains(policy),
            "schema missing {policy}"
        );
    }
    assert!(system.contains("same headline for title and feed_preview.title"));
    let preview_schema = serde_json::to_value(schemars::schema_for!(FeedPreview)).unwrap();
    assert!(
        preview_schema["properties"]["title"]["description"]
            .as_str()
            .unwrap()
            .contains("same faithful, subject-specific reader headline as the envelope title")
    );
}

#[test]
fn new_artifact_normalization_uses_the_canonical_title_without_truncation() {
    let source = source("news", "https://example.com/article", None);
    let hint = resolve_artifact_source_hint(&source);
    let canonical_title = "How the European Space Agency uses synthetic aperture radar (SAR) to track Antarctic ice movement";
    let original_preview_title = "Daily Newsletter #123: Publisher branding (45 minutes)";
    let value = serde_json::json!({
        "title": canonical_title,
        "one_line": "Satellite radar tracks changes in Antarctic ice movement.",
        "ask": "track",
        "artifact": {
            "type": "briefing",
            "payload": {
                "quotes": [
                    {"text": "Satellite radar tracks changes in Antarctic ice movement.", "attribution": null},
                    {"text": "Repeated observations reveal how the ice changes over time.", "attribution": null}
                ],
                "key_points": [],
                "takeaway": "Repeated radar observations track ice movement.",
                "extras": {"timeline": [], "key_actors": [], "what_to_watch": "Future radar observations."}
            }
        },
        "generated_at": "2026-10-26T00:00:00Z",
        "source_context": {"url": source.url, "source_name": null, "publication_date": null, "platform": null},
        "selection_trace": {"source_hint": hint.source_hint, "candidates": ["briefing"], "selected": "briefing", "reason": "The source reports changes in ice movement.", "confidence": 0.9},
        "feed_preview": {"title": original_preview_title, "one_line": "Satellite radar tracks changes in Antarctic ice movement.", "preview_bullets": [], "reason_to_read": "Understand Antarctic ice observations.", "artifact_type": "briefing"}
    });
    let (artifact, _) = decode_summarization_output(value.clone(), false).unwrap();
    // Reading existing artifacts preserves their titles; only generation normalizes them.
    assert_eq!(artifact.feed_preview.title, original_preview_title);

    let normalized = artifact.normalize_and_validate(&source, &hint).unwrap();
    assert_eq!(normalized.title, canonical_title);
    assert_eq!(normalized.feed_preview.title, canonical_title);

    let scene = "An engineer lifts a shield beside a compact radar instrument.";
    let (podcast_summary, decoded_scene) = decode_summarization_output(
        serde_json::json!({"summary": value, "podcast_artwork_scene": scene}),
        true,
    )
    .unwrap();
    assert_eq!(decoded_scene.as_deref(), Some(scene));
    let podcast_summary = serde_json::to_value(&podcast_summary).unwrap();
    assert!(podcast_summary.get("podcast_artwork_scene").is_none());
    let (_, empty_scene) = decode_summarization_output(
        serde_json::json!({"summary": podcast_summary, "podcast_artwork_scene": "  "}),
        true,
    )
    .unwrap();
    assert_eq!(empty_scene, None);
}

#[test]
fn source_routing_preserves_the_wire_contract() {
    assert_eq!(
        resolve_artifact_source_hint(&source("news", "https://example.com", None)).candidates,
        vec![ArtifactType::Briefing]
    );
    assert_eq!(
        resolve_artifact_source_hint(&source(
            "article",
            "https://github.com/newsly/backend",
            None,
        ))
        .source_hint,
        "github:repo"
    );
    assert_eq!(
        resolve_artifact_source_hint(&source("podcast", "https://example.com", None)).candidates,
        vec![
            ArtifactType::Playbook,
            ArtifactType::Portrait,
            ArtifactType::MentalModel,
        ]
    );
}

#[test]
fn payload_clipping_preserves_head_and_tail() {
    let payload = "a".repeat(100) + &"z".repeat(100);
    let clipped = clip_payload(&payload, 80);
    assert_eq!(clipped.chars().count(), 80);
    assert!(clipped.starts_with('a'));
    assert!(clipped.ends_with('z'));
    assert!(clipped.contains("CONTENT TRUNCATED"));
}
