use newsly_contracts::{AudioEpisodeStatus, BriefingNarrationResponse, BriefingNarrationScope};
use newsly_db::AudioEpisodeProjection;
use newsly_domain::{BriefingNarrationMetadata, NarrationScope};
use serde::Deserialize;
use serde_json::Value;

use super::clean_text;
use crate::audio_episode_presentation::present_audio_episode;
use crate::error::ApiError;
use crate::write_support::internal_error;

pub(super) fn present_narration(
    mut episodes: Vec<AudioEpisodeProjection>,
    request_id: &str,
) -> Result<BriefingNarrationResponse, ApiError> {
    episodes.sort_by_key(|episode| (episode.chapter_index.unwrap_or(0), episode.id));
    let first = episodes
        .first()
        .ok_or_else(|| internal_error("Briefing narration has no chapters", request_id))?;
    let snapshot = first
        .source_snapshot
        .as_object()
        .ok_or_else(|| internal_error("Briefing narration metadata is incomplete", request_id))?;
    let group_id = first
        .episode_group_id
        .as_deref()
        .and_then(clean_text)
        .ok_or_else(|| internal_error("Briefing narration group is missing", request_id))?;
    let lens_key = snapshot
        .get("lens_key")
        .and_then(Value::as_str)
        .and_then(clean_text)
        .ok_or_else(|| internal_error("Briefing narration lens is missing", request_id))?;
    let lens_title = snapshot
        .get("lens_title")
        .and_then(Value::as_str)
        .and_then(clean_text)
        .unwrap_or_else(|| "Briefing".to_owned());
    let metadata = BriefingNarrationMetadata::deserialize(snapshot)
        .map_err(|error| internal_error(error, request_id))?;
    metadata
        .style()
        .map_err(|error| internal_error(error, request_id))?;
    let scope = metadata.scope.map(|scope| match scope {
        NarrationScope::Lens => BriefingNarrationScope::Lens,
        NarrationScope::ArticleTier => BriefingNarrationScope::ArticleTier,
        NarrationScope::PodcastTier => BriefingNarrationScope::PodcastTier,
        NarrationScope::NewsProgram => BriefingNarrationScope::NewsProgram,
    });
    let first_status = AudioEpisodeStatus::try_from(first.status.as_str())
        .map_err(|error| internal_error(error, request_id))?;
    let statuses = episodes
        .iter()
        .map(|episode| {
            AudioEpisodeStatus::try_from(episode.status.as_str())
                .map_err(|error| internal_error(error, request_id))
        })
        .collect::<Result<Vec<_>, _>>()?;
    let playable = first_status == AudioEpisodeStatus::Completed;
    let status = if statuses
        .iter()
        .all(|status| *status == AudioEpisodeStatus::Completed)
    {
        AudioEpisodeStatus::Completed
    } else if first_status == AudioEpisodeStatus::Failed {
        AudioEpisodeStatus::Failed
    } else if playable || statuses.contains(&AudioEpisodeStatus::Processing) {
        AudioEpisodeStatus::Processing
    } else {
        AudioEpisodeStatus::Pending
    };
    let duration_seconds = episodes
        .iter()
        .map(|episode| episode.duration_seconds.unwrap_or(0).max(0))
        .fold(0_i32, i32::saturating_add);
    let chapters = episodes
        .into_iter()
        .map(|episode| present_audio_episode(episode, request_id))
        .collect::<Result<Vec<_>, _>>()?;
    Ok(BriefingNarrationResponse {
        episode_group_id: group_id,
        lens_key,
        scope,
        title: if scope == Some(BriefingNarrationScope::Lens) {
            lens_title
        } else {
            format!("{lens_title} briefing")
        },
        status,
        playable,
        duration_seconds,
        chapters,
    })
}
