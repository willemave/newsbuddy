use std::collections::{HashMap, HashSet};
use std::fmt::Write as _;

use serde_json::{Map, Value, json};
use sha2::{Digest, Sha256};

use super::{
    BRIEFING_NARRATION_KIND, BriefingSegmentProjection, dedupe_source_keys, parse_source_ids,
};
use crate::briefing_refresh::BriefingRefreshSource;

#[derive(Debug, Clone)]
pub(super) struct NarrationPlan {
    pub(super) index: i32,
    pub(super) segment_ids: Vec<i64>,
    pub(super) source_keys: Vec<String>,
    pub(super) narration_text: String,
    pub(super) duration_seconds: i32,
}

pub(super) fn narration_chapter_plans(
    segments: &[BriefingSegmentProjection],
    target_seconds: i32,
) -> Vec<NarrationPlan> {
    let speakable = segments
        .iter()
        .filter(|segment| !segment.narration_text.trim().is_empty())
        .collect::<Vec<_>>();
    let mut groups = Vec::<Vec<&BriefingSegmentProjection>>::new();
    let mut current = Vec::new();
    let mut current_duration = 0_i32;
    for segment in speakable {
        let duration = estimate_duration_seconds(&segment.narration_text).max(1);
        let combined = current_duration.saturating_add(duration);
        if !current.is_empty()
            && (target_seconds - current_duration).abs() < (target_seconds - combined).abs()
        {
            groups.push(current);
            current = vec![segment];
            current_duration = duration;
        } else {
            current.push(segment);
            current_duration = combined;
        }
    }
    if !current.is_empty() {
        groups.push(current);
    }
    groups
        .into_iter()
        .enumerate()
        .map(|(index, group)| plan_from_segments(i32::try_from(index).unwrap_or(i32::MAX), &group))
        .collect()
}

pub(super) fn document_narration_plans(
    segments: &[BriefingSegmentProjection],
) -> Vec<NarrationPlan> {
    let mut seen_source_keys = HashSet::new();
    let mut plans = Vec::new();
    for segment in segments {
        for source_key in &segment.source_keys {
            if !seen_source_keys.insert(source_key) {
                continue;
            }
            let narration_text = segment.narration_text.trim().to_owned();
            plans.push(NarrationPlan {
                index: i32::try_from(plans.len()).unwrap_or(i32::MAX),
                segment_ids: vec![segment.id],
                source_keys: vec![source_key.clone()],
                duration_seconds: estimate_duration_seconds(&narration_text).max(1),
                narration_text,
            });
        }
    }
    plans
}

pub(super) fn legacy_narration_plan(segments: &[BriefingSegmentProjection]) -> Vec<NarrationPlan> {
    let speakable = segments
        .iter()
        .filter(|segment| !segment.narration_text.trim().is_empty())
        .collect::<Vec<_>>();
    if speakable.is_empty() {
        Vec::new()
    } else {
        vec![plan_from_segments(0, &speakable)]
    }
}

fn plan_from_segments(index: i32, segments: &[&BriefingSegmentProjection]) -> NarrationPlan {
    let narration_text = segments
        .iter()
        .map(|segment| segment.narration_text.trim())
        .filter(|text| !text.is_empty())
        .collect::<Vec<_>>()
        .join("\n\n");
    NarrationPlan {
        index,
        segment_ids: segments.iter().map(|segment| segment.id).collect(),
        source_keys: dedupe_source_keys(segments.iter().flat_map(|segment| &segment.source_keys)),
        duration_seconds: estimate_duration_seconds(&narration_text).max(1),
        narration_text,
    }
}

#[allow(clippy::too_many_arguments)]
pub(super) fn source_snapshot(
    program_key: &str,
    program_title: &str,
    scope: Option<&str>,
    episode_group_id: Option<&str>,
    chapter_count: usize,
    plan: &NarrationPlan,
    sources: &HashMap<String, BriefingRefreshSource>,
    chaptered: bool,
) -> Value {
    let (content_ids, news_item_ids) = parse_source_ids(&plan.source_keys);
    let items = plan
        .source_keys
        .iter()
        .filter_map(|key| sources.get(key))
        .map(narration_source_value)
        .collect::<Vec<_>>();
    let mut snapshot = Map::from_iter([
        (
            "kind".to_owned(),
            Value::String(BRIEFING_NARRATION_KIND.to_owned()),
        ),
        ("lens_key".to_owned(), Value::String(program_key.to_owned())),
        (
            "lens_title".to_owned(),
            Value::String(program_title.to_owned()),
        ),
        ("source_count".to_owned(), json!(plan.source_keys.len())),
        ("segment_ids".to_owned(), json!(plan.segment_ids)),
        ("source_keys".to_owned(), json!(plan.source_keys)),
        ("items".to_owned(), Value::Array(items)),
        (
            "read_on_play".to_owned(),
            json!({"content_ids": content_ids, "news_item_ids": news_item_ids}),
        ),
    ]);
    if let Some(scope) = scope {
        snapshot.insert("scope".to_owned(), Value::String(scope.to_owned()));
    } else {
        snapshot.insert(
            "script_text".to_owned(),
            Value::String(plan.narration_text.clone()),
        );
    }
    if chaptered {
        snapshot.insert(
            "episode_group_id".to_owned(),
            Value::String(episode_group_id.unwrap_or_default().to_owned()),
        );
        snapshot.insert("chapter_index".to_owned(), json!(plan.index));
        snapshot.insert("chapter_count".to_owned(), json!(chapter_count));
    }
    Value::Object(snapshot)
}

fn narration_source_value(source: &BriefingRefreshSource) -> Value {
    json!({
        "source_key": source.source_key,
        "kind": source.kind,
        "title": source.title,
        "source_name": source.source_name,
        "summary": source.summary,
        "key_points": source.key_points,
        "briefing_context": source.briefing_context,
        "url": source.url,
        "image_url": source.image_url,
        "thumbnail_url": source.thumbnail_url,
        "published_at": source.published_at,
    })
}

pub(super) fn episode_group_id(
    program_key: &str,
    program_title: &str,
    scope: Option<&str>,
    prompt_version: i32,
    plans: &[NarrationPlan],
    sources: &HashMap<String, BriefingRefreshSource>,
) -> String {
    stable_hash(&json!({
        "prompt_version": prompt_version,
        "kind": BRIEFING_NARRATION_KIND,
        "program_key": program_key,
        "program_title": program_title,
        "scope": scope,
        "chapters": plans.iter().map(|plan| json!({
            "chapter_index": plan.index,
            "segment_ids": plan.segment_ids,
            "source_keys": plan.source_keys,
            "sources": plan.source_keys.iter().filter_map(|key| sources.get(key)).map(narration_source_value).collect::<Vec<_>>(),
        })).collect::<Vec<_>>(),
    }))
}

fn estimate_duration_seconds(text: &str) -> i32 {
    let words = text.split_whitespace().count();
    if words == 0 {
        return 0;
    }
    let seconds = words.saturating_mul(60).div_ceil(145);
    i32::try_from(seconds).unwrap_or(i32::MAX)
}

pub(super) fn stable_hash(value: &Value) -> String {
    let encoded = serde_json::to_vec(value).expect("JSON values always serialize");
    let digest = Sha256::digest(encoded);
    let mut encoded_digest = String::with_capacity(digest.len() * 2);
    for byte in digest {
        write!(&mut encoded_digest, "{byte:02x}").expect("writing to a String cannot fail");
    }
    encoded_digest
}

#[cfg(test)]
mod tests {
    use chrono::{TimeZone, Utc};
    use serde_json::json;

    use super::{BriefingSegmentProjection, document_narration_plans};

    #[test]
    fn document_narration_keeps_one_source_per_chapter_in_order() {
        let segment = |id, source_key: &str| BriefingSegmentProjection {
            id,
            created_at: Utc.timestamp_opt(id, 0).single().expect("timestamp"),
            status: "active".to_owned(),
            narration_text: format!("Narration for {source_key}"),
            blocks: json!([]),
            source_keys: vec![source_key.to_owned()],
        };
        let segments = vec![segment(1, "content:10"), segment(2, "content:20")];

        let plans = document_narration_plans(&segments);

        assert_eq!(plans.len(), 2);
        assert_eq!(plans[0].index, 0);
        assert_eq!(plans[0].segment_ids, vec![1]);
        assert_eq!(plans[0].source_keys, vec!["content:10"]);
        assert_eq!(plans[1].index, 1);
        assert_eq!(plans[1].segment_ids, vec![2]);
        assert_eq!(plans[1].source_keys, vec!["content:20"]);
    }

    #[test]
    fn document_narration_skips_segments_without_owned_sources() {
        let segments = vec![BriefingSegmentProjection {
            id: 1,
            created_at: Utc.timestamp_opt(1, 0).single().expect("timestamp"),
            status: "active".to_owned(),
            narration_text: "Orphaned text".to_owned(),
            blocks: json!([]),
            source_keys: Vec::new(),
        }];

        assert!(document_narration_plans(&segments).is_empty());
    }

    #[test]
    fn document_narration_splits_malformed_multi_source_segments() {
        let segments = vec![BriefingSegmentProjection {
            id: 1,
            created_at: Utc.timestamp_opt(1, 0).single().expect("timestamp"),
            status: "active".to_owned(),
            narration_text: "Shared legacy narration".to_owned(),
            blocks: json!([]),
            source_keys: vec!["content:10".to_owned(), "content:20".to_owned()],
        }];

        let plans = document_narration_plans(&segments);

        assert_eq!(plans.len(), 2);
        assert_eq!(plans[0].source_keys, vec!["content:10"]);
        assert_eq!(plans[1].source_keys, vec!["content:20"]);
        assert!(plans.iter().all(|plan| plan.source_keys.len() == 1));
    }

    #[test]
    fn document_narration_keeps_only_the_first_chapter_for_each_source() {
        let segment = |id, source_key: &str| BriefingSegmentProjection {
            id,
            created_at: Utc.timestamp_opt(id, 0).single().expect("timestamp"),
            status: "active".to_owned(),
            narration_text: format!("Narration {id}"),
            blocks: json!([]),
            source_keys: vec![source_key.to_owned()],
        };

        let plans = document_narration_plans(&[
            segment(1, "content:10"),
            segment(2, "content:10"),
            segment(3, "content:20"),
        ]);

        assert_eq!(plans.len(), 2);
        assert_eq!(plans[0].segment_ids, vec![1]);
        assert_eq!(plans[0].source_keys, vec!["content:10"]);
        assert_eq!(plans[1].index, 1);
        assert_eq!(plans[1].source_keys, vec!["content:20"]);
    }
}
