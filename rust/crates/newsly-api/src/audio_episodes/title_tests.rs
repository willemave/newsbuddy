use super::*;

fn content(title: Option<&str>, metadata: Value) -> newsly_db::ContentDetailProjection {
    newsly_db::ContentDetailProjection {
        id: 42,
        content_type: "podcast".to_owned(),
        url: "https://example.com/episode".to_owned(),
        source_url: None,
        title: title.map(str::to_owned),
        source: None,
        platform: None,
        status: "completed".to_owned(),
        error_message: None,
        retry_count: 0,
        content_metadata: metadata,
        created_at: chrono::Utc::now(),
        updated_at: None,
        processed_at: None,
        checked_out_by: None,
        checked_out_at: None,
        publication_date: None,
        is_read: false,
        is_saved_to_knowledge: false,
        body_available: true,
        body_format: None,
    }
}

#[test]
fn reader_headlines_do_not_change_existing_content_snapshot_bytes() {
    for (stored_title, metadata, expected_title) in [
        (
            Some("  <b>Guest</b>  &amp; chips  "),
            json!({}),
            "<b>Guest</b>  &amp; chips",
        ),
        (Some("Unknown"), json!({}), "Unknown"),
        (
            Some("  "),
            json!({"title":"  Raw  metadata &amp; title  ", "domain":{"title":"Different domain title"}}),
            "Raw  metadata &amp; title",
        ),
        (
            None,
            json!({"title":" ","episode_title":"  Episode  123  "}),
            "Episode  123",
        ),
        (None, json!({}), "Content 42"),
    ] {
        let mut metadata = metadata;
        metadata["summary"] = json!({"title":"Clean reader headline"});
        let content = content(stored_title, metadata);
        let snapshot = content_source_payload(&content, " source body ", 100);
        let expected = json!({
            "content_id":42,
            "content_type":"podcast",
            "title":expected_title,
            "source":null,
            "platform":null,
            "url":"https://example.com/episode",
            "publication_date":null,
            "summary":{"title":"Clean reader headline"},
            "source_text":"source body",
            "source_text_excerpt_strategy":"full",
            "source_text_truncated":false,
            "source_text_chars":11,
            "source_text_included_chars":11,
        });
        assert_eq!(
            serde_json::to_vec(&snapshot).unwrap(),
            serde_json::to_vec(&expected).unwrap()
        );
        assert_eq!(content_display_title(&content), "Clean reader headline");
        let custom_snapshot = json!({"items":[snapshot]});
        assert_eq!(
            custom_narration_title(&custom_snapshot, Some(&content_display_title(&content))),
            "Narration: Clean reader headline"
        );
    }
}

#[test]
fn custom_narration_uses_reader_headline_even_when_snapshot_has_no_legacy_summary() {
    let content = content(
        Some("Original show title #123"),
        json!({"domain":{"summary":{"title":"Legacy reader headline"}}}),
    );
    let snapshot = content_source_payload(&content, "source body", 100);
    assert_eq!(snapshot["title"], "Original show title #123");
    assert_eq!(
        snapshot["summary"],
        json!({"overview":null,"key_points":[]})
    );
    let custom_snapshot = json!({"items":[snapshot, {"title":"Another source"}]});
    assert_eq!(
        custom_narration_title(&custom_snapshot, Some(&content_display_title(&content))),
        "Narration: Legacy reader headline + 1 more"
    );
    assert_eq!(
        custom_narration_title(&json!({"items":[{"title":"Fast Read"}]}), None),
        "Narration: Fast Read"
    );
}
