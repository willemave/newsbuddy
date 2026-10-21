//! Canonical runtime view and readability rules for persisted content metadata.

pub mod source_presentation;

use serde_json::{Map, Value};

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
    "extraction_error",
    "extraction_error_code",
    "canonical_content_id",
    "tweet_enrichment",
    "tweet_only",
    "deck_source_only",
];

pub const RAW_BODY_KEYS: [&str; 6] = [
    "content",
    "transcript",
    "content_to_summarize",
    "file_path",
    "transcript_path",
    "full_text",
];

/// Updates one field and its SQL-compatible root mirror without reshaping other fields.
///
/// # Panics
///
/// Debug builds reject the reserved `domain` and `processing` namespace keys.
pub fn set_field(metadata: &mut Map<String, Value>, key: &str, value: Value) {
    debug_assert!(!matches!(key, "domain" | "processing"));
    let (target, opposite) = if PROCESSING_FIELDS.contains(&key) {
        ("processing", "domain")
    } else {
        ("domain", "processing")
    };
    metadata.insert(key.to_owned(), value.clone());
    let namespace = metadata
        .entry(target.to_owned())
        .or_insert_with(|| Value::Object(Map::new()));
    if !namespace.is_object() {
        *namespace = Value::Object(Map::new());
    }
    if let Value::Object(namespace) = namespace {
        namespace.insert(key.to_owned(), value);
    }
    if let Some(namespace) = metadata.get_mut(opposite).and_then(Value::as_object_mut) {
        namespace.remove(key);
    }
}

pub fn set_fields(metadata: &mut Map<String, Value>, fields: &Map<String, Value>) {
    for (key, value) in fields {
        set_field(metadata, key, value.clone());
    }
}

/// Initializes a newly inserted row while retaining its root SQL mirror.
pub fn initialize_metadata(mut metadata: Map<String, Value>) -> Map<String, Value> {
    for (key, value) in metadata.clone() {
        if !matches!(key.as_str(), "domain" | "processing") {
            set_field(&mut metadata, &key, value);
        }
    }
    metadata
}

pub fn remove_field(metadata: &mut Map<String, Value>, key: &str) {
    metadata.remove(key);
    for namespace in ["domain", "processing"] {
        if let Some(namespace) = metadata.get_mut(namespace).and_then(Value::as_object_mut) {
            namespace.remove(key);
        }
    }
}

pub fn runtime_value<'a>(metadata: &'a Map<String, Value>, key: &str) -> Option<&'a Value> {
    let processing = metadata
        .get("processing")
        .and_then(Value::as_object)
        .and_then(|value| value.get(key));
    let domain = metadata
        .get("domain")
        .and_then(Value::as_object)
        .and_then(|value| value.get(key));
    processing.or_else(|| {
        if PROCESSING_FIELDS.contains(&key) {
            metadata.get(key).or(domain)
        } else {
            domain.or_else(|| metadata.get(key))
        }
    })
}

/// Retire after production tweet snapshot divergence is inventoried and backfilled.
pub fn legacy_root_first_value<'a>(
    metadata: &'a Map<String, Value>,
    key: &str,
) -> Option<&'a Value> {
    metadata.get(key).or_else(|| runtime_value(metadata, key))
}

/// A model or sandbox receives content data without any other submitter's identity or prompt.
pub fn source_metadata(metadata: &Value) -> Value {
    let mut metadata = metadata
        .as_object()
        .map(runtime_metadata)
        .unwrap_or_default();
    for key in [
        "submitted_by_user_id",
        "share_and_chat_user_ids",
        "share_and_chat_requests",
    ] {
        metadata.remove(key);
    }
    Value::Object(metadata)
}

pub fn runtime_metadata(metadata: &Map<String, Value>) -> Map<String, Value> {
    let mut runtime = metadata
        .get("domain")
        .and_then(Value::as_object)
        .cloned()
        .unwrap_or_default();
    for (key, value) in metadata {
        if matches!(key.as_str(), "domain" | "processing") {
            continue;
        }
        if PROCESSING_FIELDS.contains(&key.as_str()) {
            runtime.insert(key.clone(), value.clone());
        } else {
            runtime.entry(key.clone()).or_insert_with(|| value.clone());
        }
    }
    if let Some(processing) = metadata.get("processing").and_then(Value::as_object) {
        runtime.extend(processing.clone());
    }
    runtime
}

pub fn json_truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(value) => *value,
        Value::Number(value) => value.as_f64().is_some_and(|value| value != 0.0),
        Value::String(value) => !value.is_empty(),
        Value::Array(value) => !value.is_empty(),
        Value::Object(value) => !value.is_empty(),
    }
}

pub fn summary_is_readable(runtime: &Map<String, Value>, content_type: &str) -> bool {
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
    use super::{runtime_metadata, summary_is_readable};
    use serde_json::json;

    #[test]
    fn writes_route_processing_keys_without_reshaping_other_fields() {
        for key in super::PROCESSING_FIELDS {
            let mut metadata = json!({"domain": {"untouched":1}, "unrelated":2})
                .as_object()
                .unwrap()
                .clone();
            metadata
                .get_mut("domain")
                .unwrap()
                .as_object_mut()
                .unwrap()
                .insert((*key).to_owned(), json!("old"));
            super::set_field(&mut metadata, key, json!("new"));
            assert_eq!(metadata[*key], "new");
            assert_eq!(metadata["processing"][*key], "new");
            assert!(metadata["domain"].get(*key).is_none());
            assert_eq!(metadata["domain"]["untouched"], 1);
            assert!(metadata["domain"].get("unrelated").is_none());
        }
    }

    #[test]
    fn writes_repair_only_the_target_namespace_and_removal_clears_all_copies() {
        let mut metadata = json!({"domain":false,"processing":{"summary":"stale","keep":true}})
            .as_object()
            .unwrap()
            .clone();
        super::set_field(&mut metadata, "summary", json!({"title":"new"}));
        assert_eq!(metadata["domain"]["summary"], metadata["summary"]);
        assert!(metadata["processing"].get("summary").is_none());
        assert_eq!(metadata["processing"]["keep"], true);
        super::remove_field(&mut metadata, "summary");
        assert!(!metadata.contains_key("summary"));
        assert!(metadata["domain"].get("summary").is_none());
        assert!(metadata["processing"].get("summary").is_none());
    }

    #[test]
    fn canonical_lookup_matches_runtime_map_and_gated_worker_keeps_the_fresh_root() {
        let metadata = json!({"submitted_via":"flat","tweet_snapshot":"fresh", "domain": {
            "submitted_via":"misplaced", "tweet_snapshot":"old"
        }, "processing":{"summary":"processing"}})
        .as_object()
        .unwrap()
        .clone();
        for key in ["submitted_via", "tweet_snapshot", "summary"] {
            assert_eq!(
                super::runtime_value(&metadata, key),
                super::runtime_metadata(&metadata).get(key)
            );
        }
        assert_eq!(
            super::legacy_root_first_value(&metadata, "tweet_snapshot"),
            Some(&json!("fresh"))
        );
    }

    #[test]
    fn source_metadata_never_contains_submitter_identities_or_chat_prompts() {
        let metadata = super::source_metadata(&json!({"domain":{
            "submitted_by_user_id":1,"summary":{"title":"source"}
        },"processing":{"share_and_chat_user_ids":[1,2],"share_and_chat_requests":[{"user_id":2,"initial_message":"private"}],"detected_feed":{"url":"feed"}}}));
        for key in [
            "submitted_by_user_id",
            "share_and_chat_user_ids",
            "share_and_chat_requests",
        ] {
            assert!(metadata.get(key).is_none());
        }
        assert_eq!(metadata["summary"]["title"], "source");
        assert_eq!(metadata["detected_feed"]["url"], "feed");
    }

    #[test]
    fn normalized_namespaces_preserve_submission_precedence() {
        let value = json!({
            "summary": {"one_line": "flat"},
            "domain": {"summary": {"one_line": "domain"}, "submitted_via": "domain"},
            "processing": {"summary": {"one_line": "processing"}},
            "submitted_via": "flat"
        });
        let runtime = runtime_metadata(value.as_object().unwrap());
        assert_eq!(runtime["summary"]["one_line"], "processing");
        assert_eq!(runtime["submitted_via"], "flat");
        assert!(summary_is_readable(&runtime, "article"));
    }

    #[test]
    fn longform_artifact_requires_a_readable_preview() {
        let value = json!({"domain": {
            "summary_kind": "longform_artifact",
            "summary": {"artifact": {"blocks": []}}
        }});
        let mut runtime = runtime_metadata(value.as_object().unwrap());
        assert!(!summary_is_readable(&runtime, "article"));
        runtime.insert("feed_preview".to_owned(), json!({}));
        assert!(summary_is_readable(&runtime, "article"));
        assert!(summary_is_readable(&runtime, "podcast"));
    }
}
