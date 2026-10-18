use std::fmt::Write as _;

use schemars::JsonSchema;
use serde::Deserialize;

use super::{
    BriefingCompositionBlock, BriefingCompositionLayout, BriefingCompositionRequest,
    BriefingCompositionSource, BriefingLayoutRejection, BriefingPassageWeight,
};

#[derive(Debug, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub(super) struct BriefingNewsComposition {
    /// The complete single-paragraph news passage, with every supplied source linked exactly once.
    markdown: String,
}

pub(super) fn decode_composition_layout(
    value: Option<serde_json::Value>,
    tier: &str,
    sources: &[BriefingCompositionSource],
) -> Result<BriefingCompositionLayout, BriefingLayoutRejection> {
    let value = value.ok_or(BriefingLayoutRejection::MissingStructuredOutput)?;
    let layout = if tier == "news" {
        let passage = serde_json::from_value::<BriefingNewsComposition>(value)
            .map_err(|error| BriefingLayoutRejection::Decode(error.to_string()))?;
        BriefingCompositionLayout {
            suggested_quotes: Vec::new(),
            blocks: vec![BriefingCompositionBlock::Passage {
                markdown: passage.markdown,
                weight: BriefingPassageWeight::Brief,
            }],
        }
    } else {
        serde_json::from_value::<BriefingCompositionLayout>(value)
            .map_err(|error| BriefingLayoutRejection::Decode(error.to_string()))?
    };
    layout.validate(tier, sources)
}

pub(super) fn composition_schema(tier: &str) -> schemars::Schema {
    if tier == "news" {
        schemars::schema_for!(BriefingNewsComposition)
    } else {
        schemars::schema_for!(BriefingCompositionLayout)
    }
}

/// Identifies the ephemeral provider response contract, not the persisted layout.
pub fn briefing_composition_response_contract(tier: &str) -> &'static str {
    if tier == "news" {
        "briefing_news_markdown_v1"
    } else {
        "briefing_composer_layout_v1"
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
    if request.tier == "news" {
        prompt.push_str("\nReturn `markdown` containing the single passage paragraph.");
        prompt.push_str("\nRequired citation manifest (use each URI exactly once in markdown):");
        for source in &request.sources {
            let _ = write!(
                prompt,
                "\n- {} => {}",
                source.source_key,
                super::source_link(&source.kind, source.id)
            );
        }
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
