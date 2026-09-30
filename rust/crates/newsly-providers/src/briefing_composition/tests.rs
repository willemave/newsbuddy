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
    };
    assert!(name.validate().is_ok());
    let invalid = BriefingLensName {
        key: "Public Infrastructure".to_owned(),
        title: "Public Infrastructure".to_owned(),
        deck: "Too short".to_owned(),
    };
    assert!(invalid.validate().is_err());
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

#[test]
fn embedding_vectors_are_normalized() {
    let vector = normalize_vector(vec![3.0, 4.0]).expect("valid vector");
    assert!((vector[0] - 0.6).abs() < 1e-12);
    assert!((vector[1] - 0.8).abs() < 1e-12);
}
