//! Shared interpretation of persisted Briefing narration metadata.
//!
//! Older snapshots omit scope and contain preauthored prose. Scoped snapshots
//! describe adaptations; their wire fields remain unchanged for existing episodes.
use serde::{Deserialize, Serialize};
use thiserror::Error;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum NarrationScope {
    Lens,
    ArticleTier,
    PodcastTier,
    NewsProgram,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum NarrationTier {
    #[serde(rename = "longform")]
    Article,
    #[serde(rename = "audio")]
    Podcast,
    #[serde(rename = "news")]
    News,
}

impl TryFrom<&str> for NarrationTier {
    type Error = InvalidNarrationMetadata;
    fn try_from(value: &str) -> Result<Self, Self::Error> {
        match value {
            "longform" => Ok(Self::Article),
            "audio" => Ok(Self::Podcast),
            "news" => Ok(Self::News),
            _ => Err(InvalidNarrationMetadata),
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum NewsNarrationWindow {
    Lens,
    AllLenses,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BriefingNarrationStyle {
    Preauthored,
    Document,
    News(NewsNarrationWindow),
}

#[derive(Debug, Default, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct BriefingNarrationMetadata {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub scope: Option<NarrationScope>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub lens_tier: Option<NarrationTier>,
}

impl BriefingNarrationMetadata {
    /// Resolves the script policy of a stored narration edition.
    ///
    /// # Errors
    /// Returns an error when an adapted lens does not specify a supported tier.
    pub fn style(self) -> Result<BriefingNarrationStyle, InvalidNarrationMetadata> {
        match self.scope {
            None => Ok(BriefingNarrationStyle::Preauthored),
            Some(NarrationScope::ArticleTier | NarrationScope::PodcastTier) => {
                Ok(BriefingNarrationStyle::Document)
            }
            Some(NarrationScope::NewsProgram) => {
                Ok(BriefingNarrationStyle::News(NewsNarrationWindow::AllLenses))
            }
            Some(NarrationScope::Lens) => match self.lens_tier {
                Some(NarrationTier::Article | NarrationTier::Podcast) => {
                    Ok(BriefingNarrationStyle::Document)
                }
                Some(NarrationTier::News) => {
                    Ok(BriefingNarrationStyle::News(NewsNarrationWindow::Lens))
                }
                None => Err(InvalidNarrationMetadata),
            },
        }
    }
}

#[derive(Debug, Error)]
#[error("Briefing narration tier is missing or unsupported")]
pub struct InvalidNarrationMetadata;

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn narration_metadata_has_one_explicit_style_for_each_supported_scope() {
        assert_eq!(
            BriefingNarrationMetadata::default().style().unwrap(),
            BriefingNarrationStyle::Preauthored
        );
        for (scope, lens_tier, expected) in [
            (
                NarrationScope::Lens,
                Some(NarrationTier::News),
                BriefingNarrationStyle::News(NewsNarrationWindow::Lens),
            ),
            (
                NarrationScope::Lens,
                Some(NarrationTier::Article),
                BriefingNarrationStyle::Document,
            ),
            (
                NarrationScope::Lens,
                Some(NarrationTier::Podcast),
                BriefingNarrationStyle::Document,
            ),
            (
                NarrationScope::ArticleTier,
                None,
                BriefingNarrationStyle::Document,
            ),
            (
                NarrationScope::PodcastTier,
                None,
                BriefingNarrationStyle::Document,
            ),
            (
                NarrationScope::NewsProgram,
                None,
                BriefingNarrationStyle::News(NewsNarrationWindow::AllLenses),
            ),
        ] {
            assert_eq!(
                BriefingNarrationMetadata {
                    scope: Some(scope),
                    lens_tier
                }
                .style()
                .unwrap(),
                expected
            );
        }
        assert!(
            BriefingNarrationMetadata {
                scope: Some(NarrationScope::Lens),
                lens_tier: None
            }
            .style()
            .is_err()
        );
        assert!(NarrationTier::try_from("unsupported").is_err());
    }
}
