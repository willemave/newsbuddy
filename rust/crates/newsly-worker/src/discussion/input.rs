use std::collections::BTreeSet;

use chrono::{DateTime, Duration, Utc};
use serde_json::{Map, Value, json};
use sha2::{Digest, Sha256};

use super::model::{
    DiscussionSnapshot, DiscussionSummaryInput, DiscussionSummaryMode, DiscussionSummaryPlan,
    SummaryPromptComment, SummaryPromptLink,
};

const MAX_SUMMARY_COMMENTS: usize = 200;
const MAX_SUMMARY_LINKS: usize = 50;
const MATERIAL_COMMENT_THRESHOLD: usize = 25;
const MINIMUM_SUMMARY_INTERVAL: Duration = Duration::hours(6);
const MAXIMUM_SUMMARY_INTERVAL: Duration = Duration::hours(24);
const MAX_INCREMENTAL_SUMMARY_UPDATES: i32 = 4;

pub(super) fn build_summary_input(
    platform: &str,
    discussion_url: &str,
    title: Option<&str>,
    raw_payload: &Value,
) -> DiscussionSummaryInput {
    let comments = raw_payload
        .get("comments")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .enumerate()
        .filter_map(|(index, comment)| build_summary_comment(comment, index))
        .take(MAX_SUMMARY_COMMENTS)
        .collect::<Vec<_>>();
    let links = raw_payload
        .get("links")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(build_summary_link)
        .take(MAX_SUMMARY_LINKS)
        .collect::<Vec<_>>();
    let prompt = format_full_prompt(platform, discussion_url, title, &comments, &links);
    DiscussionSummaryInput {
        input_sha256: sha256_hex(prompt.as_bytes()),
        comment_count: i32::try_from(comments.len()).unwrap_or(i32::MAX),
        comment_fingerprints: comments
            .iter()
            .map(|comment| (comment.comment_id.clone(), comment.fingerprint.clone()))
            .collect(),
        comments,
        links,
        prompt,
    }
}

pub(super) fn plan_summary(
    snapshot: &DiscussionSnapshot,
    input: &DiscussionSummaryInput,
    previous_raw_sha: Option<&str>,
    current_raw_sha: &str,
    now: DateTime<Utc>,
) -> DiscussionSummaryPlan {
    if input.comment_count == 0 {
        return summary_plan(DiscussionSummaryMode::None, Vec::new());
    }
    if snapshot.summary.is_none() || snapshot.summary_status != "completed" {
        return summary_plan(DiscussionSummaryMode::Full, Vec::new());
    }
    if snapshot.summary_input_sha256.as_deref() == Some(input.input_sha256.as_str()) {
        return summary_plan(DiscussionSummaryMode::None, Vec::new());
    }
    let summary_age = snapshot
        .summary_generated_at
        .map(|generated_at| now.naive_utc() - generated_at);
    if snapshot.summary_seen_input_sha256.as_deref() == Some(input.input_sha256.as_str())
        && summary_age.is_none_or(|age| age < MAXIMUM_SUMMARY_INTERVAL)
    {
        return summary_plan(DiscussionSummaryMode::None, Vec::new());
    }
    if previous_raw_sha == Some(current_raw_sha) && snapshot.summary_input_sha256.is_none() {
        return summary_plan(DiscussionSummaryMode::TrackSummarized, Vec::new());
    }
    let Some(previous_fingerprints) = snapshot.summary_comment_fingerprints.as_ref() else {
        return summary_plan(DiscussionSummaryMode::Full, Vec::new());
    };
    let changed_comments = input
        .comments
        .iter()
        .filter(|comment| {
            previous_fingerprints.get(&comment.comment_id) != Some(&comment.fingerprint)
        })
        .cloned()
        .collect::<Vec<_>>();
    let minimum_elapsed = summary_age.is_none_or(|age| age >= MINIMUM_SUMMARY_INTERVAL);
    let maximum_elapsed = summary_age.is_some_and(|age| age >= MAXIMUM_SUMMARY_INTERVAL);
    let materially_changed = changed_comments.len() > MATERIAL_COMMENT_THRESHOLD;
    let force_stale = !changed_comments.is_empty() && maximum_elapsed;
    if !(force_stale || materially_changed && minimum_elapsed) {
        return summary_plan(DiscussionSummaryMode::TrackSeen, changed_comments);
    }
    if snapshot.summary_incremental_update_count >= MAX_INCREMENTAL_SUMMARY_UPDATES {
        return summary_plan(DiscussionSummaryMode::Full, changed_comments);
    }
    summary_plan(DiscussionSummaryMode::Merge, changed_comments)
}

pub(super) fn build_merge_prompt(
    snapshot: &DiscussionSnapshot,
    input: &DiscussionSummaryInput,
    changed_comments: &[SummaryPromptComment],
) -> String {
    let changed_ids = changed_comments
        .iter()
        .map(|comment| comment.comment_id.as_str())
        .collect::<BTreeSet<_>>();
    let changed_links = input
        .links
        .iter()
        .filter(|link| {
            link.comment_id
                .as_deref()
                .is_some_and(|comment_id| changed_ids.contains(comment_id))
        })
        .collect::<Vec<_>>();
    let existing_summary = summary_for_merge(snapshot.summary.as_ref());
    let mut lines = vec![
        format!("Platform: {}", snapshot.platform),
        format!("Discussion URL: {}", snapshot.discussion_url),
    ];
    if let Some(title) = snapshot.title.as_deref().and_then(clean) {
        lines.push(format!("Thread title: {title}"));
    }
    lines.extend([
        String::new(),
        "Existing summary JSON:".to_owned(),
        serde_json::to_string(&existing_summary).unwrap_or_else(|_| "{}".to_owned()),
        String::new(),
        "New or changed comments:".to_owned(),
    ]);
    lines.extend(changed_comments.iter().map(format_comment_line));
    if !changed_links.is_empty() {
        lines.push(String::new());
        lines.push("New or changed links:".to_owned());
        lines.extend(changed_links.into_iter().map(format_link_line));
    }
    lines.join("\n")
}

fn build_summary_comment(value: &Value, index: usize) -> Option<SummaryPromptComment> {
    let object = value.as_object()?;
    let text = object
        .get("compact_text")
        .and_then(Value::as_str)
        .and_then(clean)
        .or_else(|| object.get("text").and_then(Value::as_str).and_then(clean))?;
    let comment_id = object
        .get("comment_id")
        .and_then(Value::as_str)
        .and_then(clean)
        .map_or_else(
            || format!("comment:{index}:{}", &sha256_hex(text.as_bytes())[..16]),
            str::to_owned,
        );
    let author = object
        .get("author")
        .and_then(Value::as_str)
        .and_then(clean)
        .unwrap_or("unknown")
        .to_owned();
    let depth = nonnegative_i64(object.get("depth")).unwrap_or(0);
    let fingerprint = sha256_json(&json!({
        "author": author,
        "comment_id": comment_id,
        "depth": depth,
        "text": text,
    }));
    Some(SummaryPromptComment {
        comment_id,
        author,
        depth,
        text: text.to_owned(),
        fingerprint,
    })
}

fn build_summary_link(value: &Value) -> Option<SummaryPromptLink> {
    let object = value.as_object()?;
    let url = object.get("url")?.as_str().and_then(clean)?.to_owned();
    Some(SummaryPromptLink {
        url,
        title: optional_clean_string(object.get("title")),
        comment_id: optional_clean_string(object.get("comment_id")),
    })
}

fn format_full_prompt(
    platform: &str,
    discussion_url: &str,
    title: Option<&str>,
    comments: &[SummaryPromptComment],
    links: &[SummaryPromptLink],
) -> String {
    let mut lines = vec![
        format!("Platform: {platform}"),
        format!("Discussion URL: {discussion_url}"),
    ];
    if let Some(title) = title.and_then(clean) {
        lines.push(format!("Thread title: {title}"));
    }
    lines.push(String::new());
    lines.push("Comments:".to_owned());
    lines.extend(comments.iter().map(format_comment_line));
    if !links.is_empty() {
        lines.push(String::new());
        lines.push("Extracted links:".to_owned());
        lines.extend(links.iter().map(format_link_line));
    }
    lines.join("\n")
}

fn format_comment_line(comment: &SummaryPromptComment) -> String {
    format!(
        "- [{}] {} depth={}: {}",
        comment.comment_id, comment.author, comment.depth, comment.text
    )
}

fn format_link_line(link: &SummaryPromptLink) -> String {
    let label = link
        .title
        .as_deref()
        .map_or_else(String::new, |title| format!(" ({title})"));
    let source = link
        .comment_id
        .as_deref()
        .map_or_else(String::new, |id| format!(" from comment {id}"));
    format!("- {}{label}{source}", link.url)
}

fn summary_for_merge(summary: Option<&Value>) -> Value {
    let Some(object) = summary.and_then(Value::as_object) else {
        return Value::Object(Map::new());
    };
    Value::Object(
        [
            "overview",
            "topics",
            "notable_links",
            "representative_comments",
            "external_discussion_url",
        ]
        .into_iter()
        .filter_map(|key| {
            object
                .get(key)
                .cloned()
                .map(|value| (key.to_owned(), value))
        })
        .collect(),
    )
}

fn summary_plan(
    mode: DiscussionSummaryMode,
    changed_comments: Vec<SummaryPromptComment>,
) -> DiscussionSummaryPlan {
    DiscussionSummaryPlan {
        mode,
        changed_comments,
    }
}

fn optional_clean_string(value: Option<&Value>) -> Option<String> {
    value
        .and_then(Value::as_str)
        .and_then(clean)
        .map(str::to_owned)
}

fn nonnegative_i64(value: Option<&Value>) -> Option<i64> {
    value
        .and_then(|value| {
            value
                .as_i64()
                .or_else(|| value.as_str()?.trim().parse::<i64>().ok())
        })
        .filter(|value| *value >= 0)
}

fn clean(value: &str) -> Option<&str> {
    let value = value.trim();
    (!value.is_empty()).then_some(value)
}

fn sha256_json(value: &Value) -> String {
    sha256_hex(serde_json::to_string(value).unwrap_or_default().as_bytes())
}

fn sha256_hex(bytes: &[u8]) -> String {
    let digest = Sha256::digest(bytes);
    let mut output = String::with_capacity(64);
    for byte in digest {
        use std::fmt::Write as _;
        let _ = write!(output, "{byte:02x}");
    }
    output
}

#[cfg(test)]
mod tests {
    use std::collections::BTreeMap;

    use chrono::Duration;

    use super::*;

    fn snapshot() -> DiscussionSnapshot {
        DiscussionSnapshot {
            discussion_id: 4,
            news_item_id: 9,
            owner_user_id: Some(3),
            platform: "hackernews".to_owned(),
            external_id: "42".to_owned(),
            discussion_url: "https://news.ycombinator.com/item?id=42".to_owned(),
            title: Some("A thread".to_owned()),
            author: None,
            score: None,
            comment_count: None,
            raw_comments_sha256: Some("old".to_owned()),
            summary: Some(json!({"overview": "A prior summary that is long enough."})),
            summary_status: "completed".to_owned(),
            summary_input_sha256: Some("prior".to_owned()),
            summary_comment_fingerprints: Some(BTreeMap::new()),
            summary_seen_input_sha256: None,
            summary_incremental_update_count: 0,
            summary_generated_at: Some((Utc::now() - Duration::hours(7)).naive_utc()),
            claim_token: uuid::Uuid::new_v4(),
        }
    }

    #[test]
    fn full_input_is_bounded_and_stable() {
        let raw = json!({
            "comments": (0..240).map(|index| json!({
                "comment_id": index.to_string(),
                "author": "reader",
                "compact_text": format!("comment {index}"),
                "depth": 0,
            })).collect::<Vec<_>>(),
            "links": [{"url": "https://example.com", "comment_id": "1"}],
        });
        let first = build_summary_input(
            "hackernews",
            "https://news.ycombinator.com/item?id=42",
            Some("A thread"),
            &raw,
        );
        let second = build_summary_input(
            "hackernews",
            "https://news.ycombinator.com/item?id=42",
            Some("A thread"),
            &raw,
        );
        assert_eq!(first.comment_count, 200);
        assert_eq!(first.input_sha256, second.input_sha256);
        assert!(first.prompt.contains("Extracted links:"));
    }

    #[test]
    fn small_delta_tracks_seen_until_stale() {
        let mut snapshot = snapshot();
        let raw = json!({"comments": [{"comment_id": "1", "text": "new detail"}]});
        let input = build_summary_input(
            &snapshot.platform,
            &snapshot.discussion_url,
            snapshot.title.as_deref(),
            &raw,
        );
        let plan = plan_summary(&snapshot, &input, Some("old"), "new", Utc::now());
        assert_eq!(plan.mode, DiscussionSummaryMode::TrackSeen);

        snapshot.summary_generated_at = Some((Utc::now() - Duration::hours(25)).naive_utc());
        let stale = plan_summary(&snapshot, &input, Some("old"), "new", Utc::now());
        assert_eq!(stale.mode, DiscussionSummaryMode::Merge);
    }

    #[test]
    fn material_delta_merges_then_forces_full_after_four_updates() {
        let mut snapshot = snapshot();
        let raw = json!({
            "comments": (0..26).map(|index| json!({
                "comment_id": index.to_string(),
                "text": format!("new detail {index}"),
            })).collect::<Vec<_>>(),
        });
        let input = build_summary_input(
            &snapshot.platform,
            &snapshot.discussion_url,
            snapshot.title.as_deref(),
            &raw,
        );
        let merge = plan_summary(&snapshot, &input, Some("old"), "new", Utc::now());
        assert_eq!(merge.mode, DiscussionSummaryMode::Merge);
        snapshot.summary_incremental_update_count = 4;
        let full = plan_summary(&snapshot, &input, Some("old"), "new", Utc::now());
        assert_eq!(full.mode, DiscussionSummaryMode::Full);
    }
}
