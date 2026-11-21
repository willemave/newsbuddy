use std::collections::BTreeSet;
use std::sync::LazyLock;

use newsly_db::BriefingRefreshSource;
use newsly_providers::briefing_links::{briefing_links, briefing_plain_text, briefing_sentences};
use regex::Regex;
use serde_json::{Value, json};
use thiserror::Error;

static BOLD_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"\*\*([^*]+)\*\*").expect("Briefing bold regex must compile"));
static INSIGHT_MARKER_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"\{\{/?insight(?::[^{}]*)?\}\}")
        .expect("Briefing insight-marker regex must compile")
});
static EM_DASH_RANGE_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"([0-9])\s*—\s*([0-9])").expect("Briefing numeric-range regex must compile")
});
static EM_DASH_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"\s*—\s*").expect("Briefing em-dash regex must compile"));

#[derive(Debug, Clone, PartialEq)]
pub(super) struct NormalizedBriefingLayout {
    pub blocks: Value,
    pub markdown_raw: String,
    pub narration_text: String,
}

/// Publishes one validated passage as a single passage block.
pub(super) fn normalize_passage(
    markdown: &str,
    sources: &[BriefingRefreshSource],
    tier: &str,
) -> Result<NormalizedBriefingLayout, BriefingNormalizationError> {
    let source_keys = sources
        .iter()
        .map(|source| source.source_key.clone())
        .collect::<BTreeSet<_>>();
    let markdown = repair_text(markdown);
    let mut covered = BTreeSet::new();
    let paragraphs = paragraphs_from_markdown(&markdown, &source_keys, &mut covered);
    if paragraphs.is_empty() {
        return Err(BriefingNormalizationError::NoPassage);
    }
    let missing = source_keys
        .difference(&covered)
        .cloned()
        .collect::<Vec<_>>();
    if !missing.is_empty() {
        return Err(BriefingNormalizationError::MissingCoverage(missing));
    }
    let block = json!({
        "type": "passage",
        "weight": if tier == "news" { "brief" } else { "feature" },
        "paragraphs": paragraphs,
        "source_key": null,
    });
    Ok(NormalizedBriefingLayout {
        blocks: json!([block]),
        narration_text: briefing_plain_text(&markdown),
        markdown_raw: markdown,
    })
}

fn paragraphs_from_markdown(
    markdown: &str,
    source_keys: &BTreeSet<String>,
    covered: &mut BTreeSet<String>,
) -> Vec<Value> {
    // `repair_text` has already collapsed whitespace, so paragraphs come only
    // from grouping sentences in threes.
    sentence_groups(markdown.trim())
        .into_iter()
        .filter(|paragraph| !paragraph.is_empty())
        .filter_map(|paragraph| {
            let runs = runs_from_markdown(&paragraph, source_keys, covered);
            (!runs.is_empty()).then(|| json!({"runs": runs}))
        })
        .collect()
}

fn sentence_groups(paragraph: &str) -> Vec<String> {
    let sentences = briefing_sentences(paragraph);
    if sentences.len() <= 3 {
        return vec![paragraph.to_owned()];
    }
    sentences.chunks(3).map(|chunk| chunk.join(" ")).collect()
}

fn runs_from_markdown(
    markdown: &str,
    source_keys: &BTreeSet<String>,
    covered: &mut BTreeSet<String>,
) -> Vec<Value> {
    let mut runs = Vec::new();
    let mut cursor = 0_usize;
    for link in briefing_links(markdown) {
        append_text_runs(&mut runs, &markdown[cursor..link.range.start]);
        let source_key = link.source_key;
        let text = link.label.as_str();
        if source_keys.contains(source_key.as_str()) {
            covered.insert(source_key.clone());
            append_run(
                &mut runs,
                "source_link",
                text,
                Some(source_key.as_str()),
                false,
            );
        } else {
            append_text_runs(&mut runs, text);
        }
        cursor = link.range.end;
    }
    append_text_runs(&mut runs, &markdown[cursor..]);
    runs
}

fn append_text_runs(runs: &mut Vec<Value>, text: &str) {
    let mut cursor = 0_usize;
    for captures in BOLD_RE.captures_iter(text) {
        let whole = captures.get(0).expect("bold capture");
        append_run(runs, "text", &text[cursor..whole.start()], None, false);
        append_run(
            runs,
            "text",
            captures.get(1).expect("bold text").as_str(),
            None,
            true,
        );
        cursor = whole.end();
    }
    append_run(runs, "text", &text[cursor..], None, false);
}

fn append_run(runs: &mut Vec<Value>, kind: &str, text: &str, source_key: Option<&str>, bold: bool) {
    if text.is_empty() {
        return;
    }
    if let Some(previous) = runs.last_mut().and_then(Value::as_object_mut)
        && previous.get("kind").and_then(Value::as_str) == Some(kind)
        && previous.get("source_key").and_then(Value::as_str) == source_key
        && previous.get("bold").and_then(Value::as_bool) == Some(bold)
        && let Some(Value::String(previous_text)) = previous.get_mut("text")
    {
        previous_text.push_str(text);
        return;
    }
    runs.push(json!({
        "kind": kind,
        "text": text,
        "source_key": source_key,
        "insight_id": null,
        "bold": bold,
    }));
}

fn repair_text(value: &str) -> String {
    let value = INSIGHT_MARKER_RE.replace_all(value, "");
    let value = EM_DASH_RANGE_RE.replace_all(&value, "$1-$2");
    EM_DASH_RE
        .replace_all(&value, ", ")
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ")
}

#[derive(Debug, Error)]
pub(super) enum BriefingNormalizationError {
    #[error("Briefing layout has no usable passage")]
    NoPassage,
    #[error("Briefing layout does not cover sources: {0:?}")]
    MissingCoverage(Vec<String>),
}

#[cfg(test)]
mod tests {
    use super::*;

    fn source(id: i64) -> BriefingRefreshSource {
        BriefingRefreshSource {
            source_key: format!("content:{id}"),
            kind: "content".to_owned(),
            id,
            title: format!("Source {id}"),
            original_title: format!("Source {id}"),
            source_name: None,
            summary: None,
            key_points: Vec::new(),
            url: None,
            image_url: Some(format!("/images/{id}.png")),
            thumbnail_url: None,
            published_at: None,
            briefing_context: None,
        }
    }

    #[test]
    fn citation_corpus_survives_coverage_runs_and_narration() {
        for label in [
            "A study [Update]",
            r"A study \[Update\]",
            "**你好** (update)!",
        ] {
            let markdown = format!("[{label}](newsly://briefing/content/1) explains the result.");
            let normalized = normalize_passage(&markdown, &[source(1)], "longform").unwrap();
            assert!(!normalized.narration_text.contains("newsly://"));
            assert!(normalized.blocks.to_string().contains("source_link"));
        }
    }

    #[test]
    fn normalizes_source_links_bold_text_and_narration() {
        let normalized = normalize_passage(
            "**Lead** [Source 1](newsly://briefing/content/1).",
            &[source(1)],
            "news",
        )
        .expect("layout");
        let runs = normalized.blocks[0]["paragraphs"][0]["runs"]
            .as_array()
            .expect("runs");
        assert_eq!(runs[0]["bold"], true);
        let source_link = runs
            .iter()
            .find(|run| run["kind"] == "source_link")
            .expect("source-link run");
        assert_eq!(source_link["source_key"], "content:1");
        assert_eq!(normalized.narration_text, "Lead Source 1.");
    }

    #[test]
    fn card_passage_publishes_one_passage_block_of_at_most_two_paragraphs() {
        let markdown = "[A study of remote work](newsly://briefing/content/1) finds output rose — \
                        sharply.\n\nTeams shipped more. Reviews were faster. Bugs fell. \
                        Managers disagreed. Costs stayed flat.";
        let normalized = normalize_passage(markdown, &[source(1)], "audio").unwrap();
        let blocks = normalized.blocks.as_array().unwrap();
        assert_eq!(blocks.len(), 1);
        let block = blocks[0].as_object().unwrap();
        assert_eq!(
            block.keys().map(String::as_str).collect::<Vec<_>>(),
            ["type", "weight", "paragraphs", "source_key"]
        );
        assert_eq!(block["type"], "passage");
        assert_eq!(block["weight"], "feature");
        // Raw breaks collapse; six sentences re-split into two three-sentence paragraphs.
        assert_eq!(block["paragraphs"].as_array().unwrap().len(), 2);
        assert!(!normalized.markdown_raw.contains('—'));
        assert!(!normalized.markdown_raw.contains('\n'));
        assert_eq!(
            normalized.narration_text,
            "A study of remote work finds output rose, sharply. Teams shipped more. Reviews were \
             faster. Bugs fell. Managers disagreed. Costs stayed flat."
        );
    }

    #[test]
    fn passage_must_cover_every_source_and_contain_runs() {
        let error = normalize_passage(
            "[Only one](newsly://briefing/content/1) is cited.",
            &[source(1), source(2)],
            "news",
        )
        .unwrap_err();
        assert!(matches!(
            error,
            BriefingNormalizationError::MissingCoverage(ref missing) if missing == &["content:2"]
        ));
        assert!(matches!(
            normalize_passage("  ", &[source(1)], "news").unwrap_err(),
            BriefingNormalizationError::NoPassage
        ));
    }
}
