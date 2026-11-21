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

fn content_source(id: i64) -> BriefingCompositionSource {
    BriefingCompositionSource {
        source_key: format!("content:{id}"),
        kind: "content".to_owned(),
        ..source(id)
    }
}

#[test]
fn citation_corpus_validates_brackets_but_rejects_bare_and_code_uris() {
    for label in ["A [study]", r"A \[study\]", "你好 (Update)!"] {
        let markdown = format!("[{label}](newsly://briefing/news/1) explains the result.");
        assert!(validate_passage(markdown, "news", &[source(1)]).is_ok());
    }
    for markdown in [
        "newsly://briefing/news/1",
        "`[Title](newsly://briefing/news/1)`",
        "[Ten](newsly://briefing/news/10)",
    ] {
        assert!(validate_passage(markdown.to_owned(), "news", &[source(1)]).is_err());
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
    let markdown =
        "[First source](newsly://briefing/news/1) meets [second source](newsly://briefing/news/2).";
    assert!(validate_passage(markdown.to_owned(), "news", &[source(1), source(2)]).is_ok());
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
fn card_prompt_requests_short_source_summaries_without_figures_or_quotes() {
    // Compare on collapsed whitespace so rewrapping the prompt never breaks the test.
    let card = COMPOSITION_SYSTEM_PROMPT[COMPOSITION_SYSTEM_PROMPT
        .find("For `audio` and `longform`")
        .unwrap()..]
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ");
    for required in [
        "already shows the work's title, its publication or show name, its length, and its image",
        "never restate the title, publication, show, author byline, or length",
        "at most six sentences, 60-100 words, never padding to reach the target",
        "central argument, finding, or story",
        "link the source once on a descriptive phrase",
        "roughly four to ten words, never the bare title",
        "natural noun phrase that reads as part of its sentence",
        "single strongest concrete evidence, example, number, counterpoint, or practical implication",
        "Keep most sentences under 25 words",
        "`briefing_context` when present",
        "no lists, no em dashes",
        "no markdown emphasis such as asterisks or underscores, no quoted titles",
        "\"this episode explores\", \"the author discusses\", or \"in this article\"",
        "attribute claims to the speaker or guest by name",
    ] {
        assert!(card.contains(required), "{required}");
    }
    for removed in [
        "pullquote",
        "figure",
        "quotation",
        "100-200 words",
        "substantive treatment",
    ] {
        assert!(!card.contains(removed), "{removed}");
    }
    assert!(!COMPOSITION_SYSTEM_PROMPT.contains("Suggest editorial pullquotes"));
    assert!(!COMPOSITION_SYSTEM_PROMPT.contains("Add a figure"));
}

#[test]
fn news_layout_rejects_duplicate_source_link() {
    let markdown = "[First](newsly://briefing/news/1), then [again](newsly://briefing/news/1).";
    assert!(validate_passage(markdown.to_owned(), "news", &[source(1)]).is_err());
}

#[test]
fn news_layout_counts_complete_source_uris() {
    let markdown = "[One](newsly://briefing/news/1) and [ten](newsly://briefing/news/10).";
    assert!(validate_passage(markdown.to_owned(), "news", &[source(1), source(10)]).is_ok());
}

#[test]
fn layout_rejects_unknown_source_links() {
    let markdown = "[Known](newsly://briefing/news/1) meets [invented](newsly://briefing/news/2).";
    for tier in ["news", "longform"] {
        let error = validate_passage(markdown.to_owned(), tier, &[source(1)]).unwrap_err();
        assert_eq!(error.code(), "unknown_source_link");
    }
}

const CARD_LEAD: &str =
    "[A study of remote work output](newsly://briefing/content/1) finds teams ship more.";

fn card(extra_sentences: usize, words_per_sentence: usize) -> String {
    let filler = vec!["word"; words_per_sentence.saturating_sub(1)].join(" ");
    let evidence = format!(" Evidence {filler}.");
    format!("{CARD_LEAD}{}", evidence.repeat(extra_sentences))
}

#[test]
fn card_requests_compose_exactly_one_source() {
    let request = |tier: &str, count: i64| BriefingCompositionRequest {
        lens_title: "Reading".to_owned(),
        tier: tier.to_owned(),
        sources: (1..=count).map(content_source).collect(),
    };
    for tier in ["audio", "longform"] {
        assert!(check_composition_request(&request(tier, 1)).is_ok());
        let error = check_composition_request(&request(tier, 2)).unwrap_err();
        assert_eq!(error.reason_code(), "card_source_count");
        assert!(!error.retryable());
        assert_eq!(
            check_composition_request(&request(tier, 0))
                .unwrap_err()
                .reason_code(),
            "empty_sources"
        );
    }
    assert!(check_composition_request(&request("news", 3)).is_ok());
}

#[test]
fn card_passages_link_the_source_at_least_once() {
    for tier in ["audio", "longform"] {
        assert!(validate_passage(card(2, 8), tier, &[content_source(1)]).is_ok());
        let twice = format!("{CARD_LEAD} The [same study](newsly://briefing/content/1) agrees.");
        assert!(validate_passage(twice, tier, &[content_source(1)]).is_ok());
        let error = validate_passage(
            "Teams ship more without a citation.".to_owned(),
            tier,
            &[content_source(1)],
        )
        .unwrap_err();
        assert_eq!(error.code(), "source_link_missing");
        assert_eq!(
            error.to_string(),
            "source content:1 must be linked at least once"
        );
    }
}

#[test]
fn card_passages_allow_six_sentences_and_reject_seven() {
    assert!(validate_passage(card(5, 8), "longform", &[content_source(1)]).is_ok());
    let error = validate_passage(card(6, 8), "longform", &[content_source(1)]).unwrap_err();
    assert_eq!(error.code(), "card_sentence_count");
    assert_eq!(
        error.to_string(),
        "card summary has 7 sentences; rewrite it in at most six sentences"
    );
}

#[test]
fn card_sentence_count_matches_publication_splitting() {
    // Terminators inside the link, decimals, and raw paragraph breaks do not add sentences.
    let markdown = "[Version 2.0 of the study asks why?](newsly://briefing/content/1) Output rose 3.5%.\n\nTeams agree. One. Two. Three. Four";
    assert!(validate_passage(markdown.to_owned(), "audio", &[content_source(1)]).is_ok());
    let seven = format!("{markdown}. Five.");
    let error = validate_passage(seven, "audio", &[content_source(1)]).unwrap_err();
    assert_eq!(error, BriefingLayoutRejection::CardSentenceCount(7));
}

#[test]
fn card_word_cap_counts_visible_words_and_has_its_own_code() {
    // Six sentences of 25 visible words: the lead link's target URI is not counted.
    let at_cap = card(5, 25);
    let words = crate::briefing_links::briefing_plain_text(&at_cap)
        .split_whitespace()
        .count();
    assert_eq!(words, 135);
    assert!(validate_passage(at_cap, "audio", &[content_source(1)]).is_ok());
    let over = card(5, 30);
    let error = validate_passage(over, "audio", &[content_source(1)]).unwrap_err();
    assert_eq!(error.code(), "card_word_count");
    assert_eq!(
        error.to_string(),
        "card summary has 160 words; rewrite it in at most 150 words, targeting 60-100 words"
    );
}

#[test]
fn card_passages_do_not_validate_paragraph_counts() {
    let markdown = format!("{CARD_LEAD}\n\nA second paragraph.\n\nA third paragraph.");
    assert!(validate_passage(markdown, "longform", &[content_source(1)]).is_ok());
}
