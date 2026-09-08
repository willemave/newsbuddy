//! Resolves request compatibility once into a concrete chapter and script plan.
use std::collections::HashMap;

use newsly_domain::{
    BriefingNarrationMetadata, BriefingNarrationStyle, NarrationScope, NarrationTier,
};

use super::{
    NarrationPlan, document_narration_plans, legacy_narration_plan, narration_chapter_plans,
};
use crate::briefing::{
    BriefingLensProjection, BriefingNarrationSelection, BriefingRepositoryError,
    BriefingSegmentProjection,
};
use crate::briefing_refresh::BriefingRefreshSource;

#[derive(Clone, Copy)]
enum ChapterLayout {
    Single,
    Documents,
    Windows,
}

pub(super) struct NarrationProgram<'a> {
    pub(super) key: &'a str,
    pub(super) title: &'a str,
    pub(super) metadata: BriefingNarrationMetadata,
    pub(super) style: BriefingNarrationStyle,
    pub(super) prompt_version: i32,
    layout: ChapterLayout,
}

impl<'a> NarrationProgram<'a> {
    pub(super) fn resolve(
        selection: &BriefingNarrationSelection,
        chaptered: bool,
        lens: &'a BriefingLensProjection,
    ) -> Result<Self, BriefingRepositoryError> {
        let (key, title, metadata, prompt_version) = match selection {
            BriefingNarrationSelection::Lens(_) => (
                lens.key.as_str(),
                lens.title.as_str(),
                BriefingNarrationMetadata::default(),
                if chaptered { 3 } else { 2 },
            ),
            BriefingNarrationSelection::AdaptedLens(_) => (
                lens.key.as_str(),
                lens.title.as_str(),
                BriefingNarrationMetadata {
                    scope: Some(NarrationScope::Lens),
                    lens_tier: Some(NarrationTier::try_from(lens.tier.as_str())?),
                },
                5,
            ),
            BriefingNarrationSelection::ArticleTier => (
                "articles",
                "Articles",
                BriefingNarrationMetadata {
                    scope: Some(NarrationScope::ArticleTier),
                    lens_tier: None,
                },
                4,
            ),
            BriefingNarrationSelection::PodcastTier => (
                "podcasts",
                "Podcasts",
                BriefingNarrationMetadata {
                    scope: Some(NarrationScope::PodcastTier),
                    lens_tier: None,
                },
                4,
            ),
            BriefingNarrationSelection::NewsProgram => (
                "news",
                "News Briefing",
                BriefingNarrationMetadata {
                    scope: Some(NarrationScope::NewsProgram),
                    lens_tier: None,
                },
                4,
            ),
        };
        let style = metadata.style()?;
        let layout = match (style, chaptered) {
            (BriefingNarrationStyle::Preauthored, false) => ChapterLayout::Single,
            (BriefingNarrationStyle::Document, _) => ChapterLayout::Documents,
            _ => ChapterLayout::Windows,
        };
        Ok(Self {
            key,
            title,
            metadata,
            style,
            prompt_version,
            layout,
        })
    }

    pub(super) fn chaptered(&self) -> bool {
        !matches!(self.layout, ChapterLayout::Single)
    }

    pub(super) fn chapters(
        &self,
        mut segments: Vec<BriefingSegmentProjection>,
        sources: &HashMap<String, BriefingRefreshSource>,
    ) -> Vec<NarrationPlan> {
        let active_lens = self.metadata.scope == Some(NarrationScope::Lens);
        if active_lens {
            for segment in &mut segments {
                segment.source_keys.retain(|key| sources.contains_key(key));
            }
            segments.retain(|segment| !segment.source_keys.is_empty());
        }
        let mut plans = match self.layout {
            ChapterLayout::Single => legacy_narration_plan(&segments),
            ChapterLayout::Documents => document_narration_plans(&segments),
            ChapterLayout::Windows => narration_chapter_plans(&segments, 5 * 60),
        };
        // Preserve existing chapter windows and hashes for installed-client requests.
        if !active_lens {
            for plan in &mut plans {
                plan.source_keys.retain(|key| sources.contains_key(key));
            }
            plans.retain(|plan| !plan.source_keys.is_empty());
        }
        plans
    }

    pub(super) fn chapter_title(
        &self,
        plan: &NarrationPlan,
        sources: &HashMap<String, BriefingRefreshSource>,
    ) -> String {
        match self.layout {
            ChapterLayout::Documents => plan
                .source_keys
                .first()
                .and_then(|key| sources.get(key))
                .expect("document plans contain one eligible source")
                .title
                .clone(),
            ChapterLayout::Windows => format!("{} — Chapter {}", self.title, plan.index + 1),
            ChapterLayout::Single => format!("{} briefing", self.title),
        }
    }
}
