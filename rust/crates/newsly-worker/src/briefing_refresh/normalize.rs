use std::collections::{BTreeMap, BTreeSet};
use std::sync::LazyLock;

use newsly_db::BriefingRefreshSource;
use newsly_providers::{
    BriefingCompositionBlock, BriefingCompositionLayout, BriefingFigureAlignment,
    BriefingFigurePlacement, BriefingPassageWeight,
};
use regex::Regex;
use serde_json::{Value, json};
use thiserror::Error;

static SOURCE_LINK_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"\[([^\]]+)\]\(((?:newsly|news)://briefing/(content|news)/(\d+))\)")
        .expect("Briefing source-link regex must compile")
});
static BOLD_LINK_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"\*\*\s*(\[[^\]]+\]\([^)]+\))\s*\*\*")
        .expect("Briefing bold-link regex must compile")
});
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
    pub warnings: Vec<String>,
}

#[derive(Debug, Clone)]
enum ResolvedBlock {
    Passage {
        markdown: String,
        weight: BriefingPassageWeight,
    },
    Figure {
        source_key: String,
        caption: String,
        placement: BriefingFigurePlacement,
        alignment: Option<BriefingFigureAlignment>,
    },
    Pullquote {
        text: String,
    },
}

pub(super) fn normalize_layout(
    layout: &BriefingCompositionLayout,
    sources: &[BriefingRefreshSource],
    tier: &str,
    figure_budget: usize,
) -> Result<NormalizedBriefingLayout, BriefingNormalizationError> {
    let source_by_key = sources
        .iter()
        .map(|source| (source.source_key.as_str(), source))
        .collect::<BTreeMap<_, _>>();
    let suggestions = layout
        .suggested_quotes
        .iter()
        .map(|suggestion| (suggestion.id.as_str(), suggestion.text.as_str()))
        .collect::<BTreeMap<_, _>>();
    let mut resolved = Vec::with_capacity(layout.blocks.len());
    let mut figures_used = 0_usize;
    let mut warnings = Vec::new();
    for block in &layout.blocks {
        match block {
            BriefingCompositionBlock::Passage { markdown, weight } => {
                let markdown = repair_text(markdown);
                if !markdown.is_empty() {
                    resolved.push(ResolvedBlock::Passage {
                        markdown,
                        weight: *weight,
                    });
                }
            }
            BriefingCompositionBlock::Figure {
                source_key,
                caption,
                placement,
                alignment,
            } => {
                if figures_used >= figure_budget {
                    warnings.push("figure_budget_exceeded_dropped".to_owned());
                } else if source_by_key.contains_key(source_key.as_str()) {
                    resolved.push(ResolvedBlock::Figure {
                        source_key: source_key.clone(),
                        caption: repair_text(caption),
                        placement: *placement,
                        alignment: *alignment,
                    });
                    figures_used += 1;
                }
            }
            BriefingCompositionBlock::Pullquote { suggestion_id } => {
                let text = suggestions.get(suggestion_id.as_str()).ok_or_else(|| {
                    BriefingNormalizationError::UnknownSuggestion(suggestion_id.clone())
                })?;
                resolved.push(ResolvedBlock::Pullquote {
                    text: repair_text(text).chars().take(360).collect(),
                });
            }
        }
    }

    if tier != "news" {
        let backfilled = backfill_figures(&mut resolved, sources, figure_budget);
        if backfilled > 0 {
            warnings.push(format!("figure_backfill:{backfilled}"));
        }
    }
    canonicalize_figure_placement(&mut resolved, &mut warnings);

    let source_keys = source_by_key
        .keys()
        .map(|key| (*key).to_owned())
        .collect::<BTreeSet<_>>();
    let mut normalized_blocks = Vec::with_capacity(resolved.len());
    let mut markdown_parts = Vec::new();
    let mut narration_parts = Vec::new();
    let mut covered = BTreeSet::new();
    for block in resolved {
        match block {
            ResolvedBlock::Passage { markdown, weight } => {
                let paragraphs = paragraphs_from_markdown(&markdown, &source_keys, &mut covered);
                if paragraphs.is_empty() {
                    warnings.push("passage_without_runs_dropped".to_owned());
                    continue;
                }
                markdown_parts.push(markdown.clone());
                let narration = markdown_to_narration(&markdown);
                if !narration.is_empty() {
                    narration_parts.push(narration);
                }
                normalized_blocks.push(json!({
                    "type": "passage",
                    "weight": match weight {
                        BriefingPassageWeight::Feature => "feature",
                        BriefingPassageWeight::Brief => "brief",
                    },
                    "paragraphs": paragraphs,
                    "source_key": null,
                    "image_url": null,
                    "thumbnail_url": null,
                    "caption": null,
                    "placement": null,
                    "alignment": null,
                    "text": null,
                }));
            }
            ResolvedBlock::Figure {
                source_key,
                caption,
                placement,
                alignment,
            } => {
                let source = source_by_key
                    .get(source_key.as_str())
                    .ok_or_else(|| BriefingNormalizationError::UnknownSource(source_key.clone()))?;
                normalized_blocks.push(json!({
                    "type": "figure",
                    "weight": null,
                    "paragraphs": null,
                    "source_key": source_key,
                    "image_url": source.image_url,
                    "thumbnail_url": source.thumbnail_url,
                    "caption": (!caption.is_empty()).then_some(caption),
                    "placement": match placement {
                        BriefingFigurePlacement::Full => "full",
                        BriefingFigurePlacement::Inset => "inset",
                    },
                    "alignment": alignment.map(|value| match value {
                        BriefingFigureAlignment::Left => "left",
                        BriefingFigureAlignment::Right => "right",
                    }),
                    "text": null,
                }));
            }
            ResolvedBlock::Pullquote { text } => {
                if text.is_empty() {
                    warnings.push("empty_pullquote_dropped".to_owned());
                    continue;
                }
                normalized_blocks.push(json!({
                    "type": "pullquote",
                    "weight": null,
                    "paragraphs": null,
                    "source_key": null,
                    "image_url": null,
                    "thumbnail_url": null,
                    "caption": null,
                    "placement": null,
                    "alignment": null,
                    "text": text,
                }));
            }
        }
    }
    if normalized_blocks.is_empty() || markdown_parts.is_empty() {
        return Err(BriefingNormalizationError::NoPassage);
    }
    let missing = source_keys
        .difference(&covered)
        .cloned()
        .collect::<Vec<_>>();
    if !missing.is_empty() {
        return Err(BriefingNormalizationError::MissingCoverage(missing));
    }
    Ok(NormalizedBriefingLayout {
        blocks: Value::Array(normalized_blocks),
        markdown_raw: markdown_parts.join("\n\n"),
        narration_text: narration_parts.join("\n\n"),
        warnings,
    })
}

fn backfill_figures(
    blocks: &mut Vec<ResolvedBlock>,
    sources: &[BriefingRefreshSource],
    figure_budget: usize,
) -> usize {
    let mut figured = blocks
        .iter()
        .filter_map(|block| match block {
            ResolvedBlock::Figure { source_key, .. } => Some(source_key.clone()),
            _ => None,
        })
        .collect::<BTreeSet<_>>();
    let existing = figured.len();
    let mut added = 0_usize;
    for source in sources {
        if existing + added >= figure_budget
            || figured.contains(&source.source_key)
            || (source.image_url.is_none() && source.thumbnail_url.is_none())
        {
            continue;
        }
        let insert_at = figure_insert_index(blocks, &source.source_key);
        blocks.insert(
            insert_at,
            ResolvedBlock::Figure {
                source_key: source.source_key.clone(),
                caption: source.title.clone(),
                placement: BriefingFigurePlacement::Inset,
                alignment: Some(if (existing + added).is_multiple_of(2) {
                    BriefingFigureAlignment::Right
                } else {
                    BriefingFigureAlignment::Left
                }),
            },
        );
        figured.insert(source.source_key.clone());
        added += 1;
    }
    added
}

fn figure_insert_index(blocks: &[ResolvedBlock], source_key: &str) -> usize {
    for (index, block) in blocks.iter().enumerate() {
        let ResolvedBlock::Passage { markdown, .. } = block else {
            continue;
        };
        if !source_keys_in_markdown(markdown).contains(source_key) {
            continue;
        }
        let mut insert_at = index + 1;
        while matches!(blocks.get(insert_at), Some(ResolvedBlock::Figure { .. })) {
            insert_at += 1;
        }
        return insert_at;
    }
    blocks.len()
}

fn canonicalize_figure_placement(blocks: &mut [ResolvedBlock], warnings: &mut Vec<String>) {
    let mut full_seen = false;
    let mut previous_inset = None;
    for block in blocks {
        let ResolvedBlock::Figure {
            placement,
            alignment,
            ..
        } = block
        else {
            continue;
        };
        if *placement == BriefingFigurePlacement::Full {
            if full_seen {
                *placement = BriefingFigurePlacement::Inset;
                warnings.push("extra_full_figure_downgraded".to_owned());
            } else {
                full_seen = true;
                *alignment = None;
                continue;
            }
        }
        let fallback = match previous_inset {
            Some(BriefingFigureAlignment::Left) | None => BriefingFigureAlignment::Right,
            Some(BriefingFigureAlignment::Right) => BriefingFigureAlignment::Left,
        };
        let selected = alignment.unwrap_or(fallback);
        if Some(selected) == previous_inset {
            *alignment = Some(fallback);
            warnings.push("consecutive_inset_alignment_alternated".to_owned());
        } else {
            *alignment = Some(selected);
        }
        previous_inset = *alignment;
    }
}

fn paragraphs_from_markdown(
    markdown: &str,
    source_keys: &BTreeSet<String>,
    covered: &mut BTreeSet<String>,
) -> Vec<Value> {
    markdown
        .split("\n\n")
        .map(str::trim)
        .filter(|paragraph| !paragraph.is_empty())
        .flat_map(sentence_groups)
        .filter_map(|paragraph| {
            let runs = runs_from_markdown(&paragraph, source_keys, covered);
            (!runs.is_empty()).then(|| json!({"runs": runs}))
        })
        .collect()
}

fn sentence_groups(paragraph: &str) -> Vec<String> {
    let spans = SOURCE_LINK_RE
        .find_iter(paragraph)
        .map(|found| (found.start(), found.end()))
        .collect::<Vec<_>>();
    let mut sentences = Vec::new();
    let mut start = 0_usize;
    for (index, character) in paragraph.char_indices() {
        if !matches!(character, '.' | '!' | '?')
            || spans
                .iter()
                .any(|(link_start, link_end)| *link_start <= index && index < *link_end)
        {
            continue;
        }
        let after = index + character.len_utf8();
        if paragraph[after..]
            .chars()
            .next()
            .is_none_or(char::is_whitespace)
        {
            let sentence = paragraph[start..after].trim();
            if !sentence.is_empty() {
                sentences.push(sentence.to_owned());
            }
            start = after;
        }
    }
    let tail = paragraph[start..].trim();
    if !tail.is_empty() {
        sentences.push(tail.to_owned());
    }
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
    let unwrapped = BOLD_LINK_RE.replace_all(markdown, "$1");
    let mut runs = Vec::new();
    let mut cursor = 0_usize;
    for captures in SOURCE_LINK_RE.captures_iter(&unwrapped) {
        let whole = captures.get(0).expect("source-link capture");
        append_text_runs(&mut runs, &unwrapped[cursor..whole.start()]);
        let kind = captures.get(3).expect("source kind").as_str();
        let id = captures.get(4).expect("source id").as_str();
        let source_key = format!("{kind}:{id}");
        let text = captures.get(1).expect("source title").as_str();
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
        cursor = whole.end();
    }
    append_text_runs(&mut runs, &unwrapped[cursor..]);
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

fn markdown_to_narration(markdown: &str) -> String {
    let without_links = SOURCE_LINK_RE.replace_all(markdown, "$1");
    let without_insights = INSIGHT_MARKER_RE.replace_all(&without_links, "");
    without_insights
        .replace("**", "")
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ")
}

fn source_keys_in_markdown(markdown: &str) -> BTreeSet<String> {
    SOURCE_LINK_RE
        .captures_iter(markdown)
        .map(|captures| {
            format!(
                "{}:{}",
                captures.get(3).expect("source kind").as_str(),
                captures.get(4).expect("source id").as_str()
            )
        })
        .collect()
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
    #[error("Briefing layout references unknown suggestion {0:?}")]
    UnknownSuggestion(String),
    #[error("Briefing layout references unknown source {0:?}")]
    UnknownSource(String),
    #[error("Briefing layout has no usable passage")]
    NoPassage,
    #[error("Briefing layout does not cover sources: {0:?}")]
    MissingCoverage(Vec<String>),
}

#[cfg(test)]
mod tests {
    use newsly_providers::{BriefingCompositionBlock, BriefingCompositionLayout};

    use super::*;

    fn source(id: i64, image: bool) -> BriefingRefreshSource {
        BriefingRefreshSource {
            source_key: format!("content:{id}"),
            kind: "content".to_owned(),
            id,
            title: format!("Source {id}"),
            source_name: None,
            summary: None,
            key_points: Vec::new(),
            url: None,
            image_url: image.then(|| format!("/images/{id}.png")),
            thumbnail_url: None,
            published_at: None,
            briefing_context: None,
        }
    }

    #[test]
    fn normalizes_source_links_bold_text_and_narration() {
        let layout = BriefingCompositionLayout {
            suggested_quotes: Vec::new(),
            blocks: vec![BriefingCompositionBlock::Passage {
                markdown: "**Lead** [Source 1](newsly://briefing/content/1).".to_owned(),
                weight: BriefingPassageWeight::Feature,
            }],
        };
        let normalized = normalize_layout(&layout, &[source(1, false)], "news", 0).expect("layout");
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
    fn deep_layout_backfills_figures_after_citing_passages() {
        let layout = BriefingCompositionLayout {
            suggested_quotes: Vec::new(),
            blocks: vec![BriefingCompositionBlock::Passage {
                markdown: "[Source 1](newsly://briefing/content/1) and [Source 2](newsly://briefing/content/2).".to_owned(),
                weight: BriefingPassageWeight::Brief,
            }],
        };
        let normalized =
            normalize_layout(&layout, &[source(1, true), source(2, true)], "longform", 12)
                .expect("layout");
        let blocks = normalized.blocks.as_array().expect("blocks");
        assert_eq!(blocks.len(), 3);
        assert_eq!(blocks[1]["type"], "figure");
        assert_eq!(blocks[1]["alignment"], "right");
        assert_eq!(blocks[2]["alignment"], "left");
        assert_eq!(normalized.warnings, ["figure_backfill:2"]);
    }

    #[test]
    fn configured_figure_budget_bounds_generated_and_backfilled_figures() {
        let layout = BriefingCompositionLayout {
            suggested_quotes: Vec::new(),
            blocks: vec![
                BriefingCompositionBlock::Passage {
                    markdown: "[Source 1](newsly://briefing/content/1) and [Source 2](newsly://briefing/content/2).".to_owned(),
                    weight: BriefingPassageWeight::Brief,
                },
                BriefingCompositionBlock::Figure {
                    source_key: "content:1".to_owned(),
                    caption: "First".to_owned(),
                    placement: BriefingFigurePlacement::Inset,
                    alignment: Some(BriefingFigureAlignment::Right),
                },
                BriefingCompositionBlock::Figure {
                    source_key: "content:2".to_owned(),
                    caption: "Second".to_owned(),
                    placement: BriefingFigurePlacement::Inset,
                    alignment: Some(BriefingFigureAlignment::Left),
                },
            ],
        };
        let normalized =
            normalize_layout(&layout, &[source(1, true), source(2, true)], "longform", 1)
                .expect("layout");
        let figure_count = normalized
            .blocks
            .as_array()
            .expect("blocks")
            .iter()
            .filter(|block| block["type"] == "figure")
            .count();
        assert_eq!(figure_count, 1);
        assert!(
            normalized
                .warnings
                .contains(&"figure_budget_exceeded_dropped".to_owned())
        );
    }
}
