use super::*;

fn source(id: i64) -> BriefingCompositionSource {
    BriefingCompositionSource {
        source_key: format!("news:{id}"),
        kind: "news".to_owned(),
        id,
        title: format!("Source {id}"),
        source_name: None,
        summary: Some("Summary".to_owned()),
        key_points: vec!["Point".to_owned()],
        url: None,
        image_url: None,
        thumbnail_url: None,
        published_at: None,
        briefing_context: None,
    }
}

#[test]
fn citation_corpus_validates_brackets_but_rejects_bare_and_code_uris() {
    for label in ["A [study]", r"A \[study\]", "你好 (Update)!"] {
        let layout = BriefingCompositionLayout {
            suggested_quotes: vec![],
            blocks: vec![BriefingCompositionBlock::Passage {
                markdown: format!("[{label}](newsly://briefing/news/1) explains the result."),
                weight: BriefingPassageWeight::Brief,
            }],
        };
        assert!(layout.validate("news", &[source(1)]).is_ok());
    }
    for markdown in [
        "newsly://briefing/news/1",
        "`[Title](newsly://briefing/news/1)`",
        "[Ten](newsly://briefing/news/10)",
    ] {
        let layout = BriefingCompositionLayout {
            suggested_quotes: vec![],
            blocks: vec![BriefingCompositionBlock::Passage {
                markdown: markdown.to_owned(),
                weight: BriefingPassageWeight::Brief,
            }],
        };
        assert!(layout.validate("news", &[source(1)]).is_err());
    }
}

#[test]
fn lens_name_requires_a_bounded_slug_and_copy() {
    let name = BriefingLensName {
        key: "news-public-infrastructure".to_owned(),
        title: "Public Infrastructure".to_owned(),
        deck: "Fast reads about the systems that make public life work.".to_owned(),
        member_source_keys: vec!["news:1".to_owned()],
        excluded_source_keys: Vec::new(),
    };
    assert!(name.validate().is_ok());
    let invalid = BriefingLensName {
        key: "Public Infrastructure".to_owned(),
        title: "Public Infrastructure".to_owned(),
        deck: "Too short".to_owned(),
        member_source_keys: vec!["news:1".to_owned()],
        excluded_source_keys: Vec::new(),
    };
    assert!(invalid.validate().is_err());
}

fn lens_membership(members: &[&str], excluded: &[&str]) -> BriefingLensName {
    BriefingLensName {
        key: "news-shared-theme".to_owned(),
        title: "Shared theme".to_owned(),
        deck: "These sources share a specific common theme.".to_owned(),
        member_source_keys: members.iter().map(|key| (*key).to_owned()).collect(),
        excluded_source_keys: excluded.iter().map(|key| (*key).to_owned()).collect(),
    }
}

#[test]
fn lens_membership_accepts_exact_partitions_including_empty_and_mixed_judgments() {
    let sources = [source(1), source(2), source(3), source(4)];
    for name in [
        lens_membership(&["news:1", "news:2", "news:3"], &["news:4"]),
        lens_membership(&["news:1", "news:2"], &["news:3", "news:4"]),
        lens_membership(&[], &["news:1", "news:2", "news:3", "news:4"]),
        lens_membership(&["news:1", "news:2", "news:3", "news:4"], &[]),
    ] {
        assert!(name.validate_membership(&sources).is_ok());
    }
}

#[test]
fn lens_membership_rejects_unknown_missing_duplicate_and_overlapping_output() {
    let sources = [source(1), source(2), source(3)];
    for name in [
        lens_membership(&["news:1", "news:2"], &["news:99"]),
        lens_membership(&["news:99"], &["news:1", "news:2", "news:3"]),
        lens_membership(&["news:1"], &["news:2"]),
        lens_membership(&["news:1", "news:1"], &["news:2", "news:3"]),
        lens_membership(&["news:1"], &["news:2", "news:3", "news:3"]),
        lens_membership(&["news:1", "news:2"], &["news:2", "news:3"]),
    ] {
        assert!(name.validate_membership(&sources).is_err(), "{name:?}");
    }
}

#[test]
fn lens_membership_rejects_duplicate_input_and_keys_outside_the_shown_subset() {
    let name = lens_membership(&["news:1"], &["news:2"]);
    assert!(
        name.validate_membership(&[source(1), source(1), source(2)])
            .is_err()
    );
    assert!(name.validate_membership(&[source(1), source(3)]).is_err());
    let valid_subset = lens_membership(&["news:2"], &["news:4"]);
    assert!(
        valid_subset
            .validate_membership(&[source(2), source(4)])
            .is_ok()
    );
}

#[test]
fn lens_membership_fields_are_required_by_decoding_and_schema() {
    let legacy = serde_json::json!({
        "key": "news-shared-theme", "title": "Shared theme",
        "deck": "These sources share a specific common theme.",
    });
    assert!(serde_json::from_value::<BriefingLensName>(legacy).is_err());
    let schema = serde_json::to_value(schemars::schema_for!(BriefingLensName)).unwrap();
    let required = schema["required"].as_array().unwrap();
    assert!(required.iter().any(|field| field == "member_source_keys"));
    assert!(required.iter().any(|field| field == "excluded_source_keys"));
}

#[test]
fn lens_naming_prompt_requires_a_specific_theme_and_explicit_outlier_exclusion() {
    assert!(LENS_NAMING_SYSTEM_PROMPT.contains("minimum_sources"));
    assert!(LENS_NAMING_SYSTEM_PROMPT.contains("must describe every member"));
    assert!(LENS_NAMING_SYSTEM_PROMPT.contains("never widen the theme"));
    assert!(LENS_NAMING_SYSTEM_PROMPT.contains("return no members"));
    assert!(!LENS_NAMING_SYSTEM_PROMPT.contains("broad but honest"));
    assert!(!LENS_NAMING_SYSTEM_PROMPT.contains("across most sources"));
}

#[test]
fn news_layout_requires_one_link_per_source() {
    let layout = BriefingCompositionLayout {
        suggested_quotes: Vec::new(),
        blocks: vec![BriefingCompositionBlock::Passage {
            markdown: "[First source](newsly://briefing/news/1) meets [second source](newsly://briefing/news/2).".to_owned(),
            weight: BriefingPassageWeight::Brief,
        }],
    };
    assert!(layout.validate("news", &[source(1), source(2)]).is_ok());
}

#[test]
fn news_prompt_requests_newspaper_briefs() {
    assert!(COMPOSITION_SYSTEM_PROMPT.contains("write like a newspaper brief"));
    assert!(COMPOSITION_SYSTEM_PROMPT.contains("never exceed 40 words"));
    assert!(COMPOSITION_SYSTEM_PROMPT.contains("target 45-65 words"));
    assert!(COMPOSITION_SYSTEM_PROMPT.contains("never exceed 75"));
    assert!(COMPOSITION_SYSTEM_PROMPT.contains("Select at most two useful"));
    assert!(COMPOSITION_SYSTEM_PROMPT.contains("never substitute a feature or"));
    assert!(COMPOSITION_SYSTEM_PROMPT.contains("complete, informative clause"));
    assert!(COMPOSITION_SYSTEM_PROMPT.contains("Never add detail merely"));
    assert!(COMPOSITION_SYSTEM_PROMPT.contains("do not put a finite verb inside"));
    assert!(COMPOSITION_SYSTEM_PROMPT.contains("duplicated verb or restatement"));
    assert!(COMPOSITION_SYSTEM_PROMPT.contains("Place links toward the beginning"));
    assert!(COMPOSITION_SYSTEM_PROMPT.contains("instead of repeating it"));
    assert!(!COMPOSITION_SYSTEM_PROMPT.contains("unified account"));
}

#[test]
fn deep_prompt_requests_full_source_treatment() {
    assert!(COMPOSITION_SYSTEM_PROMPT.contains("treat every source as a full work"));
    assert!(COMPOSITION_SYSTEM_PROMPT.contains("3-5 sentences, roughly 100-200 words"));
    assert!(COMPOSITION_SYSTEM_PROMPT.contains("concrete evidence or counterpoints"));
    assert!(COMPOSITION_SYSTEM_PROMPT.contains("supplied `briefing_context`"));
    assert!(
        COMPOSITION_SYSTEM_PROMPT.contains("never restate the title, publication, or show name")
    );
    assert!(!COMPOSITION_SYSTEM_PROMPT.contains("Identify the exact title"));
}

#[test]
fn news_layout_rejects_duplicate_source_link() {
    let layout = BriefingCompositionLayout {
        suggested_quotes: Vec::new(),
        blocks: vec![BriefingCompositionBlock::Passage {
            markdown: "[First](newsly://briefing/news/1), then [again](newsly://briefing/news/1)."
                .to_owned(),
            weight: BriefingPassageWeight::Brief,
        }],
    };
    assert!(layout.validate("news", &[source(1)]).is_err());
}

#[test]
fn news_layout_counts_complete_source_uris() {
    let layout = BriefingCompositionLayout {
        suggested_quotes: Vec::new(),
        blocks: vec![BriefingCompositionBlock::Passage {
            markdown: "[One](newsly://briefing/news/1) and [ten](newsly://briefing/news/10)."
                .to_owned(),
            weight: BriefingPassageWeight::Brief,
        }],
    };
    assert!(layout.validate("news", &[source(1), source(10)]).is_ok());
}

#[test]
fn layout_rejects_unknown_source_links() {
    let layout = BriefingCompositionLayout {
        suggested_quotes: Vec::new(),
        blocks: vec![BriefingCompositionBlock::Passage {
            markdown:
                "[Known](newsly://briefing/news/1) meets [invented](newsly://briefing/news/2)."
                    .to_owned(),
            weight: BriefingPassageWeight::Brief,
        }],
    };
    assert!(layout.validate("news", &[source(1)]).is_err());
}
