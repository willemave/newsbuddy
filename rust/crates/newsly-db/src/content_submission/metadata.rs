use serde_json::{Map, Value};

use super::SELF_SUBMISSION_SOURCE;

const PROCESSING_FIELDS: &[&str] = &[
    "subscribe_to_feed",
    "feed_subscription",
    "detected_feed",
    "all_detected_feeds",
    "share_and_chat_user_ids",
    "share_and_chat_requests",
    "submitted_by_user_id",
    "submitted_via",
    "platform_hint",
    "content_to_summarize",
    "processing_errors",
    "canonical_content_id",
    "tweet_enrichment",
    "tweet_only",
];

pub(super) fn build_new_metadata(
    user_id: i64,
    submission_channel: &str,
    platform: Option<&str>,
    subscribe_to_feed: bool,
    share_and_chat: bool,
    chat_initial_message: Option<&str>,
) -> Map<String, Value> {
    let mut metadata = Map::from_iter([("source".to_owned(), Value::from(SELF_SUBMISSION_SOURCE))]);
    set_processing_field(&mut metadata, "submitted_by_user_id", Value::from(user_id));
    set_processing_field(
        &mut metadata,
        "submitted_via",
        Value::from(submission_channel),
    );
    if subscribe_to_feed {
        set_processing_field(&mut metadata, "subscribe_to_feed", Value::Bool(true));
    }
    if let Some(platform) = platform {
        set_processing_field(&mut metadata, "platform_hint", Value::from(platform));
    }
    if share_and_chat {
        metadata = append_share_and_chat_request(metadata, user_id, chat_initial_message);
    }
    metadata
}

pub(super) fn metadata_object(value: &Value) -> Map<String, Value> {
    value.as_object().cloned().unwrap_or_default()
}

fn normalize_metadata_shape(mut metadata: Map<String, Value>) -> Map<String, Value> {
    let mut domain = metadata
        .get("domain")
        .and_then(Value::as_object)
        .cloned()
        .unwrap_or_default();
    let mut processing = metadata
        .get("processing")
        .and_then(Value::as_object)
        .cloned()
        .unwrap_or_default();
    for (key, value) in &metadata {
        if matches!(key.as_str(), "domain" | "processing") {
            continue;
        }
        if PROCESSING_FIELDS.contains(&key.as_str()) {
            processing
                .entry(key.clone())
                .or_insert_with(|| value.clone());
        } else {
            domain.entry(key.clone()).or_insert_with(|| value.clone());
        }
    }
    metadata.insert("domain".to_owned(), Value::Object(domain));
    metadata.insert("processing".to_owned(), Value::Object(processing));
    metadata
}

pub(super) fn runtime_metadata(metadata: &Map<String, Value>) -> Map<String, Value> {
    let normalized = normalize_metadata_shape(metadata.clone());
    let mut runtime = normalized
        .get("domain")
        .and_then(Value::as_object)
        .cloned()
        .unwrap_or_default();
    if let Some(processing) = normalized.get("processing").and_then(Value::as_object) {
        runtime.extend(processing.clone());
    }
    runtime
}

pub(super) fn set_processing_field(metadata: &mut Map<String, Value>, key: &str, value: Value) {
    let mut normalized = normalize_metadata_shape(std::mem::take(metadata));
    let mut processing = normalized
        .get("processing")
        .and_then(Value::as_object)
        .cloned()
        .unwrap_or_default();
    processing.insert(key.to_owned(), value.clone());
    normalized.insert("processing".to_owned(), Value::Object(processing));
    normalized.insert(key.to_owned(), value);
    *metadata = normalized;
}

pub(super) fn processing_flag(metadata: &Map<String, Value>, key: &str) -> Option<Value> {
    metadata
        .get("processing")
        .and_then(Value::as_object)
        .and_then(|processing| processing.get(key))
        .cloned()
        .or_else(|| runtime_metadata(metadata).remove(key))
}

pub(super) fn submission_user_id(metadata: &Map<String, Value>) -> Option<i64> {
    processing_flag(metadata, "submitted_by_user_id")
        .as_ref()
        .and_then(coerce_i64_like_python)
}

pub(super) fn append_share_and_chat_request(
    metadata: Map<String, Value>,
    user_id: i64,
    initial_message: Option<&str>,
) -> Map<String, Value> {
    let mut normalized = normalize_metadata_shape(metadata);
    let mut user_ids = extract_share_and_chat_user_ids(&normalized);
    if !user_ids.contains(&user_id) {
        user_ids.push(user_id);
    }
    let mut request = Map::from_iter([("user_id".to_owned(), Value::from(user_id))]);
    if let Some(message) = clean_text(initial_message) {
        request.insert("initial_message".to_owned(), Value::from(message));
    }
    let mut requests = extract_share_and_chat_requests(&normalized)
        .into_iter()
        .filter(|existing| existing.get("user_id").and_then(Value::as_i64) != Some(user_id))
        .map(Value::Object)
        .collect::<Vec<_>>();
    requests.push(Value::Object(request));
    set_processing_field(
        &mut normalized,
        "share_and_chat_user_ids",
        Value::Array(user_ids.into_iter().map(Value::from).collect()),
    );
    set_processing_field(
        &mut normalized,
        "share_and_chat_requests",
        Value::Array(requests),
    );
    normalized
}

fn extract_share_and_chat_user_ids(metadata: &Map<String, Value>) -> Vec<i64> {
    let runtime = runtime_metadata(metadata);
    let raw = runtime.get("share_and_chat_user_ids");
    let values = raw.and_then(Value::as_array).map_or_else(
        || raw.into_iter().collect::<Vec<_>>(),
        |values| values.iter().collect(),
    );
    let mut user_ids = Vec::new();
    for value in values {
        if let Some(user_id) = coerce_positive_i64(value)
            && !user_ids.contains(&user_id)
        {
            user_ids.push(user_id);
        }
    }
    user_ids
}

fn extract_share_and_chat_requests(metadata: &Map<String, Value>) -> Vec<Map<String, Value>> {
    let runtime = runtime_metadata(metadata);
    let mut requests = Vec::new();
    if let Some(raw_requests) = runtime
        .get("share_and_chat_requests")
        .and_then(Value::as_array)
    {
        for raw in raw_requests {
            let Some(raw) = raw.as_object() else {
                continue;
            };
            let Some(user_id) = raw.get("user_id").and_then(coerce_positive_i64) else {
                continue;
            };
            let mut request = Map::from_iter([("user_id".to_owned(), Value::from(user_id))]);
            if let Some(message) = clean_text(raw.get("initial_message").and_then(Value::as_str)) {
                request.insert("initial_message".to_owned(), Value::from(message));
            }
            requests.push(request);
        }
    }
    let mut existing = requests
        .iter()
        .filter_map(|request| request.get("user_id").and_then(Value::as_i64))
        .collect::<Vec<_>>();
    for user_id in extract_share_and_chat_user_ids(metadata) {
        if !existing.contains(&user_id) {
            requests.push(Map::from_iter([(
                "user_id".to_owned(),
                Value::from(user_id),
            )]));
            existing.push(user_id);
        }
    }
    requests
}

fn clean_text(value: Option<&str>) -> Option<&str> {
    value.map(str::trim).filter(|value| !value.is_empty())
}

fn coerce_positive_i64(value: &Value) -> Option<i64> {
    let parsed = coerce_i64_like_python(value)?;
    (parsed > 0).then_some(parsed)
}

fn coerce_i64_like_python(value: &Value) -> Option<i64> {
    match value {
        Value::Bool(value) => Some(i64::from(*value)),
        Value::Number(value) => value
            .as_i64()
            .or_else(|| value.as_u64().and_then(|value| i64::try_from(value).ok()))
            .or_else(|| {
                value
                    .as_f64()
                    .and_then(|value| value.trunc().to_string().parse().ok())
            }),
        Value::String(value) => value.trim().parse().ok(),
        Value::Null | Value::Array(_) | Value::Object(_) => None,
    }
}

pub(super) fn json_truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(value) => *value,
        Value::Number(value) => value.as_f64().is_some_and(|value| value != 0.0),
        Value::String(value) => !value.is_empty(),
        Value::Array(value) => !value.is_empty(),
        Value::Object(value) => !value.is_empty(),
    }
}

pub(super) fn summary_is_readable(runtime: &Map<String, Value>, content_type: &str) -> bool {
    let Some(summary) = runtime.get("summary") else {
        return false;
    };
    if content_type == "podcast" {
        return json_truthy(summary);
    }
    let Some(summary) = summary.as_object() else {
        return json_truthy(summary);
    };
    if runtime.get("summary_kind").and_then(Value::as_str) == Some("longform_artifact")
        && summary.get("artifact").is_some_and(Value::is_object)
    {
        return summary.get("feed_preview").is_some_and(Value::is_object)
            || runtime.get("feed_preview").is_some_and(Value::is_object)
            || summary.get("one_line").is_some_and(json_truthy);
    }
    [
        "one_line",
        "overview",
        "summary",
        "hook",
        "takeaway",
        "editorial_narrative",
        "bullet_points",
        "key_points",
        "points",
        "insights",
        "artifact",
    ]
    .iter()
    .any(|key| summary.get(*key).is_some_and(json_truthy))
}

#[cfg(test)]
mod tests {
    use serde_json::{Value, json};

    use super::{append_share_and_chat_request, build_new_metadata, runtime_metadata};

    #[test]
    fn new_metadata_dual_writes_processing_state() {
        let metadata = build_new_metadata(7, "share_sheet", Some("youtube"), true, false, None);

        assert_eq!(metadata["source"], "self submission");
        assert_eq!(metadata["domain"]["source"], "self submission");
        assert_eq!(metadata["submitted_by_user_id"], 7);
        assert_eq!(metadata["processing"]["submitted_by_user_id"], 7);
        assert_eq!(metadata["subscribe_to_feed"], true);
        assert_eq!(metadata["processing"]["platform_hint"], "youtube");
    }

    #[test]
    fn share_chat_request_replaces_one_users_message_and_preserves_legacy_users() {
        let metadata = json!({
            "share_and_chat_user_ids": [2, "7"],
            "share_and_chat_requests": [
                {"user_id": 2, "initial_message": "old"},
                {"user_id": 9}
            ]
        })
        .as_object()
        .cloned()
        .unwrap();
        let updated = append_share_and_chat_request(metadata, 2, Some("  new question  "));
        let runtime = runtime_metadata(&updated);

        assert_eq!(runtime["share_and_chat_user_ids"], json!([2, 7]));
        assert_eq!(
            runtime["share_and_chat_requests"],
            json!([
                {"user_id": 9},
                {"user_id": 7},
                {"user_id": 2, "initial_message": "new question"}
            ])
        );
        assert!(matches!(updated["processing"], Value::Object(_)));
    }
}
