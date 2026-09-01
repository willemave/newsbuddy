use newsly_contracts::{
    AudioEpisodeKind, AudioEpisodeResponse, AudioEpisodeStatus, BriefingNarrationResponse,
    BriefingNarrationScope,
};
use newsly_db::AudioEpisodeProjection;
use serde_json::Value;

use super::{clean_text, json_i64_values};
use crate::error::ApiError;
use crate::write_support::internal_error;

pub(super) fn present_audio_episode(
    episode: AudioEpisodeProjection,
    request_id: &str,
) -> Result<AudioEpisodeResponse, ApiError> {
    let kind = AudioEpisodeKind::try_from(episode.kind.as_str())
        .map_err(|error| internal_error(error, request_id))?;
    let status = AudioEpisodeStatus::try_from(episode.status.as_str())
        .map_err(|error| internal_error(error, request_id))?;
    let snapshot = episode.source_snapshot.as_object();
    let source_item_ids = json_i64_values(&episode.source_item_ids);
    let source_content_ids = if let Some(id) = episode.source_content_id {
        vec![id]
    } else {
        snapshot
            .and_then(|snapshot| snapshot.get("content_ids"))
            .map(json_i64_values)
            .unwrap_or_default()
    };
    let source_count = snapshot
        .and_then(|snapshot| snapshot.get("source_count"))
        .and_then(Value::as_u64)
        .and_then(|value| usize::try_from(value).ok())
        .unwrap_or({
            if source_content_ids.is_empty() {
                source_item_ids.len()
            } else {
                source_content_ids.len()
            }
        });
    let read_policy = snapshot
        .and_then(|snapshot| snapshot.get("read_on_play"))
        .and_then(Value::as_object);
    Ok(AudioEpisodeResponse {
        id: episode.id,
        kind,
        status,
        title: episode.title,
        source_content_id: episode.source_content_id,
        source_item_ids,
        source_content_ids,
        source_count,
        source_titles: snapshot
            .and_then(|snapshot| snapshot.get("items"))
            .and_then(Value::as_array)
            .map(|items| {
                items
                    .iter()
                    .filter_map(|item| item.get("title").and_then(Value::as_str))
                    .filter_map(clean_text)
                    .collect()
            })
            .unwrap_or_default(),
        subtitle: snapshot
            .and_then(|snapshot| snapshot.get("items"))
            .and_then(Value::as_array)
            .and_then(|items| items.first())
            .and_then(|item| item.get("source_name"))
            .and_then(Value::as_str)
            .and_then(clean_text),
        artwork_url: snapshot
            .and_then(|snapshot| snapshot.get("items"))
            .and_then(Value::as_array)
            .and_then(|items| items.first())
            .and_then(|item| {
                item.get("image_url")
                    .and_then(Value::as_str)
                    .or_else(|| item.get("thumbnail_url").and_then(Value::as_str))
            })
            .and_then(clean_text),
        read_on_play_content_ids: read_policy
            .and_then(|policy| policy.get("content_ids"))
            .map(json_i64_values)
            .unwrap_or_default(),
        read_on_play_news_item_ids: read_policy
            .and_then(|policy| policy.get("news_item_ids"))
            .map(json_i64_values)
            .unwrap_or_default(),
        duration_seconds: episode.duration_seconds,
        audio_url: (status == AudioEpisodeStatus::Completed
            && episode.audio_storage_path.is_some())
        .then(|| format!("/api/content/audio-episodes/{}/audio", episode.id)),
        stream_url: Some(format!("/api/content/audio-episodes/{}/stream", episode.id)),
        script_text: episode.script_text,
        error_message: (status == AudioEpisodeStatus::Failed)
            .then(|| newsly_db::public_audio_episode_error_message().to_owned()),
        created_at: episode.created_at,
        updated_at: episode.updated_at,
    })
}

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
    let scope = snapshot
        .get("scope")
        .and_then(Value::as_str)
        .map(|value| match value {
            "article_tier" => Ok(BriefingNarrationScope::ArticleTier),
            "podcast_tier" => Ok(BriefingNarrationScope::PodcastTier),
            "news_program" => Ok(BriefingNarrationScope::NewsProgram),
            other => Err(internal_error(
                format!("unsupported Briefing narration scope {other:?}"),
                request_id,
            )),
        })
        .transpose()?;
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
        title: format!("{lens_title} briefing"),
        status,
        playable,
        duration_seconds,
        chapters,
    })
}
