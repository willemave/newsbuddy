use std::fmt::Write as _;

use schemars::JsonSchema;
use serde::Deserialize;

use super::{BriefingCompositionRequest, BriefingCompositionSource, BriefingLayoutRejection};

#[derive(Debug, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub(super) struct BriefingNewsComposition {
    /// The complete single-paragraph news passage, with every supplied source linked exactly once.
    markdown: String,
}

#[derive(Debug, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub(super) struct BriefingCardComposition {
    /// The card summary of the single supplied work, at most six sentences, linking the source.
    markdown: String,
}

pub(super) fn decode_composition_passage(
    value: Option<serde_json::Value>,
    tier: &str,
    sources: &[BriefingCompositionSource],
) -> Result<String, BriefingLayoutRejection> {
    let value = value.ok_or(BriefingLayoutRejection::MissingStructuredOutput)?;
    let markdown = if tier == "news" {
        serde_json::from_value::<BriefingNewsComposition>(value).map(|passage| passage.markdown)
    } else {
        serde_json::from_value::<BriefingCardComposition>(value).map(|passage| passage.markdown)
    }
    .map_err(|error| BriefingLayoutRejection::Decode(error.to_string()))?;
    super::validate_passage(markdown, tier, sources)
}

pub(super) fn composition_schema(tier: &str) -> schemars::Schema {
    if tier == "news" {
        schemars::schema_for!(BriefingNewsComposition)
    } else {
        schemars::schema_for!(BriefingCardComposition)
    }
}

/// Identifies the ephemeral provider response contract, not the persisted blocks.
pub fn briefing_composition_response_contract(tier: &str) -> &'static str {
    if tier == "news" {
        "briefing_news_markdown_v1"
    } else {
        "briefing_card_markdown_v1"
    }
}

pub(super) fn composition_user_prompt(
    request: &BriefingCompositionRequest,
    feedback: Option<&str>,
) -> Result<String, serde_json::Error> {
    let mut prompt = format!(
        "Lens: {}\nTier: {}\n\nSources:\n{}\n\nCompose one complete Briefing window.",
        request.lens_title,
        request.tier,
        serde_json::to_string_pretty(&request.sources)?,
    );
    let (shape, citation_rule) = if request.tier == "news" {
        (
            "the single passage paragraph",
            "use each URI exactly once in markdown",
        )
    } else {
        (
            "the card summary for this source",
            "link each URI at least once in markdown",
        )
    };
    let _ = write!(
        prompt,
        "\nReturn `markdown` containing {shape}.\nRequired citation manifest ({citation_rule}):"
    );
    for source in &request.sources {
        let _ = write!(
            prompt,
            "\n- {} => {}",
            source.source_key,
            super::source_link(&source.kind, source.id)
        );
    }
    if request.tier == "news" {
        prompt.push_str("\nCite every source separately, including sources about the same event. Do not replace multiple source citations with one representative link. Before returning, check every URI above appears exactly once.");
    }
    if let Some(feedback) = feedback {
        prompt.push_str("\nCorrect this validation failure in the complete replacement layout:\n");
        prompt.push_str(feedback);
    }
    Ok(prompt)
}

#[cfg(test)]
mod tests;
