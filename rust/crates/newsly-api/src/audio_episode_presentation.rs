use crate::{error::ApiError, write_support::internal_error};
use newsly_contracts::{AudioEpisodeKind, AudioEpisodeResponse, AudioEpisodeStatus};
use newsly_db::AudioEpisodeProjection;
use serde_json::Value;

fn clean_text(value: &str) -> Option<String> {
    let value = value.trim();
    (!value.is_empty()).then(|| value.to_owned())
}

fn json_i64_values(value: &Value) -> Vec<i64> {
    value
        .as_array()
        .map(|values| values.iter().filter_map(Value::as_i64).collect())
        .unwrap_or_default()
}

fn source_titles(
    source_content_id: Option<i64>,
    snapshot: Option<&serde_json::Map<String, Value>>,
) -> Vec<String> {
    if let Some(title) = source_content_id
        .and(snapshot)
        .and_then(snapshot_display_title)
    {
        return vec![title];
    }
    snapshot
        .and_then(|value| value.get("items"))
        .and_then(Value::as_array)
        .map(|items| {
            items
                .iter()
                .filter_map(Value::as_object)
                .filter_map(snapshot_display_title)
                .collect()
        })
        .unwrap_or_default()
}

fn snapshot_display_title(snapshot: &serde_json::Map<String, Value>) -> Option<String> {
    newsly_db::content_metadata::source_presentation::content_display_title(
        snapshot.get("title").and_then(Value::as_str),
        snapshot,
    )
}

pub(crate) fn present_audio_episode(
    episode: AudioEpisodeProjection,
    request_id: &str,
) -> Result<AudioEpisodeResponse, ApiError> {
    let kind = AudioEpisodeKind::try_from(episode.kind.as_str())
        .map_err(|error| internal_error(error, request_id))?;
    let status = AudioEpisodeStatus::try_from(episode.status.as_str())
        .map_err(|error| internal_error(error, request_id))?;
    let snapshot = episode.source_snapshot.as_object();
    let source_item_ids = json_i64_values(&episode.source_item_ids);
    let mut source_content_ids = if let Some(id) = episode.source_content_id {
        vec![id]
    } else {
        snapshot
            .and_then(|snapshot| snapshot.get("content_ids"))
            .map(json_i64_values)
            .unwrap_or_default()
    };
    if source_content_ids.is_empty() {
        source_content_ids = snapshot
            .and_then(|value| value.get("items"))
            .and_then(Value::as_array)
            .map(|items| {
                items
                    .iter()
                    .filter_map(|item| item.get("content_id").and_then(Value::as_i64))
                    .collect()
            })
            .unwrap_or_default();
    }
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
        source_titles: source_titles(episode.source_content_id, snapshot),
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
            .then(|| "Couldn't prepare audio. Please try again.".to_owned()),
        created_at: episode.created_at,
        updated_at: episode.updated_at,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn episode(source_content_id: Option<i64>, source_snapshot: Value) -> AudioEpisodeProjection {
        AudioEpisodeProjection {
            id: 1,
            kind: "custom_narration".to_owned(),
            status: "completed".to_owned(),
            title: "Narration".to_owned(),
            source_content_id,
            source_item_ids: json!([]),
            source_snapshot,
            script_text: None,
            audio_storage_path: Some("audio.mp3".to_owned()),
            duration_seconds: Some(10),
            error_message: None,
            episode_group_id: None,
            chapter_index: None,
            created_at: chrono::Utc::now(),
            updated_at: None,
        }
    }

    #[test]
    fn source_projection_combines_single_and_multi_item_contracts() {
        let single =
            present_audio_episode(episode(Some(9), json!({"title":"  One  "})), "test").unwrap();
        assert_eq!(single.source_content_ids, vec![9]);
        assert_eq!(single.source_titles, vec!["One"]);
        assert_eq!(single.source_count, 1);
        let multiple = present_audio_episode(
            episode(
                None,
                json!({"items":[
            {"content_id":7,"title":" First ","source_name":" Publication "},
            {"content_id":8,"title":" Second "}
        ],"read_on_play":{"content_ids":[7,8]}}),
            ),
            "test",
        )
        .unwrap();
        assert_eq!(multiple.source_content_ids, vec![7, 8]);
        assert_eq!(multiple.source_titles, vec!["First", "Second"]);
        assert_eq!(multiple.source_count, 2);
        assert_eq!(multiple.subtitle.as_deref(), Some("Publication"));
        assert_eq!(multiple.read_on_play_content_ids, vec![7, 8]);
    }

    #[test]
    fn source_headlines_use_saved_summary_without_rewriting_snapshot() {
        let snapshot =
            json!({"title":"Show #123", "summary":{"title":"<b>Reader</b> &amp; headline"}});
        let single = present_audio_episode(episode(Some(9), snapshot.clone()), "test").unwrap();
        assert_eq!(single.source_titles, vec!["Reader & headline"]);
        assert_eq!(snapshot["title"], "Show #123");
        let multiple = present_audio_episode(
            episode(
                None,
                json!({"items":[
                    {"title":"Raw title", "summary":{"feed_preview":{"title":"Preview headline"}}},
                    {"title":"News title", "summary":"News summary text"},
                    {"title":"Legacy source", "summary":{"overview":null,"key_points":[]}}
                ]}),
            ),
            "test",
        )
        .unwrap();
        assert_eq!(
            multiple.source_titles,
            vec!["Preview headline", "News title", "Legacy source"]
        );
    }

    #[test]
    fn unknown_kind_or_status_is_never_mislabeled_as_a_valid_episode() {
        let mut row = episode(None, json!({}));
        row.kind = "future_kind".to_owned();
        assert!(present_audio_episode(row, "test").is_err());
        let mut row = episode(None, json!({}));
        row.status = "future_status".to_owned();
        assert!(present_audio_episode(row, "test").is_err());
    }
}
