//! Shared value normalization for persisted Learning Deck compatibility data.

use super::{BTreeSet, DateTime, Map, NaiveDateTime, SecondsFormat, Utc, Value, json};

pub(super) fn content_display_title(
    content_id: i64,
    title: Option<&str>,
    metadata: &Value,
) -> String {
    nested_clean_text(metadata, &["source_metadata", "paper_title"])
        .or_else(|| nested_clean_text(metadata, &["source_metadata", "title"]))
        .or_else(|| nested_clean_text(metadata, &["source_metadata", "primary_source", "title"]))
        .or_else(|| nested_clean_text(metadata, &["extracted_title"]))
        .or_else(|| nested_clean_text(metadata, &["summary", "title"]))
        .or_else(|| clean_title(title))
        .unwrap_or_else(|| format!("Content {content_id}"))
}

pub(super) fn resolve_display_title(
    deck_metadata: &Map<String, Value>,
    content_metadata: &Map<String, Value>,
    content_title: Option<&str>,
    source_title: Option<&str>,
    stored_title: &str,
) -> String {
    let deck_value = Value::Object(deck_metadata.clone());
    let content_value = Value::Object(content_metadata.clone());
    nested_clean_text(&deck_value, &["paper_title"])
        .or_else(|| nested_clean_text(&deck_value, &["title"]))
        .or_else(|| nested_clean_text(&deck_value, &["primary_source", "title"]))
        .or_else(|| nested_clean_text(&content_value, &["source_metadata", "paper_title"]))
        .or_else(|| nested_clean_text(&content_value, &["source_metadata", "title"]))
        .or_else(|| {
            nested_clean_text(
                &content_value,
                &["source_metadata", "primary_source", "title"],
            )
        })
        .or_else(|| nested_clean_text(&content_value, &["extracted_title"]))
        .or_else(|| nested_clean_text(&content_value, &["summary", "title"]))
        .or_else(|| clean_title(content_title))
        .or_else(|| clean_title(source_title))
        .or_else(|| clean_title(Some(stored_title)))
        .unwrap_or_else(|| "Learning Deck".to_owned())
}

pub(super) fn clean_title(value: Option<&str>) -> Option<String> {
    let value = clean_optional(value)?;
    let lowered = value.to_ascii_lowercase();
    if matches!(
        lowered.as_str(),
        "untitled" | "unknown" | "no title" | "n/a" | "none" | "null"
    ) || value.starts_with("http://")
        || value.starts_with("https://")
    {
        return None;
    }
    let compact = value.split_whitespace().collect::<Vec<_>>().join(" ");
    (!compact.is_empty()).then(|| compact.chars().take(500).collect())
}

pub(super) fn nested_clean_text(value: &Value, path: &[&str]) -> Option<String> {
    let mut current = value;
    for key in path {
        current = current.as_object()?.get(*key)?;
    }
    clean_title(current.as_str())
}

pub(super) fn processing_value<'a>(metadata: &'a Value, key: &str) -> Option<&'a Value> {
    metadata
        .as_object()
        .and_then(|value| value.get("processing"))
        .and_then(Value::as_object)
        .and_then(|value| value.get(key))
        .or_else(|| metadata.as_object().and_then(|value| value.get(key)))
}

pub(super) fn coerce_i64(value: &Value) -> Option<i64> {
    value
        .as_i64()
        .or_else(|| value.as_str().and_then(|value| value.parse().ok()))
}

pub(super) fn extract_x_status_id(value: &str) -> Option<String> {
    let path = value.split(['?', '#']).next()?;
    let mut parts = path.split('/').filter(|part| !part.is_empty());
    while let Some(part) = parts.next() {
        if part == "status" {
            let id = parts.next()?;
            if !id.is_empty() && id.bytes().all(|byte| byte.is_ascii_digit()) {
                return Some(id.to_owned());
            }
        }
    }
    None
}

pub(super) fn status_history_entry(
    status: &str,
    workflow_state: &str,
    note: &str,
    now: DateTime<Utc>,
) -> Value {
    json!({
        "status": status,
        "workflow_state": workflow_state,
        "created_at": legacy_naive_iso(now),
        "note": note,
    })
}

pub(super) fn legacy_naive_iso(now: DateTime<Utc>) -> String {
    now.naive_utc()
        .and_utc()
        .to_rfc3339_opts(SecondsFormat::Micros, true)
        .trim_end_matches('Z')
        .to_owned()
}

pub(super) fn parse_utc(value: &str) -> Option<DateTime<Utc>> {
    DateTime::parse_from_rfc3339(value)
        .map(|value| value.with_timezone(&Utc))
        .ok()
        .or_else(|| {
            NaiveDateTime::parse_from_str(value, "%Y-%m-%dT%H:%M:%S%.f")
                .or_else(|_| NaiveDateTime::parse_from_str(value, "%Y-%m-%d %H:%M:%S%.f"))
                .ok()
                .map(|value| value.and_utc())
        })
}

pub(super) fn json_clean_text(value: &Value, key: &str) -> Option<String> {
    clean_optional(Some(value.as_object()?.get(key)?.as_str()?)).map(str::to_owned)
}

pub(super) fn clean_optional(value: Option<&str>) -> Option<&str> {
    value.map(str::trim).filter(|value| !value.is_empty())
}

pub(super) fn json_object(value: Value) -> Map<String, Value> {
    match value {
        Value::Object(object) => object,
        _ => Map::new(),
    }
}

pub(super) fn string_values(value: &Value) -> BTreeSet<String> {
    value
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(str::to_owned)
        .collect()
}

pub(super) fn collect_string_values(value: &Value, destination: &mut BTreeSet<String>) {
    destination.extend(string_values(value));
}
