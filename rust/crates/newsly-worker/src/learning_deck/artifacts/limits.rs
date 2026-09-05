use super::{LearningDeckArtifactError, bounded_usize};

#[derive(Debug, Clone)]
pub(in crate::learning_deck) struct LearningDeckArtifactLimits {
    pub index_html_bytes: usize,
    pub source_notes_bytes: usize,
    pub asset_count: usize,
    pub asset_bytes: usize,
}

impl LearningDeckArtifactLimits {
    pub(in crate::learning_deck) fn from_env() -> Result<Self, LearningDeckArtifactError> {
        Ok(Self {
            index_html_bytes: bounded_usize(
                "LEARNING_DECK_MAX_INDEX_HTML_BYTES",
                2_000_000,
                10_000,
                10_000_000,
            )?,
            source_notes_bytes: bounded_usize(
                "LEARNING_DECK_MAX_SOURCE_NOTES_BYTES",
                1_000_000,
                1_000,
                5_000_000,
            )?,
            asset_count: bounded_usize("LEARNING_DECK_MAX_ASSET_COUNT", 40, 0, 200)?,
            asset_bytes: bounded_usize(
                "LEARNING_DECK_MAX_ASSET_BYTES",
                5_000_000,
                1_000,
                20_000_000,
            )?,
        })
    }
}
