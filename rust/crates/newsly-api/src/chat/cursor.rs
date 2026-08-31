use std::collections::BTreeMap;

use base64::Engine as _;
use base64::engine::general_purpose::{URL_SAFE, URL_SAFE_NO_PAD};
use chrono::{DateTime, NaiveDateTime, Utc};
use newsly_db::ChatListCursor;
use serde::Deserialize;
use serde_json::Value;
use sha2::{Digest, Sha256};

use crate::encoding::hex_encode;

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct CursorPayload {
    last_id: i64,
    last_created_at: String,
    #[serde(default)]
    filters_hash: Option<String>,
    #[serde(default, rename = "last_rank")]
    _last_rank: Option<f64>,
}

pub(super) fn decode(
    cursor: &str,
    content_id: Option<i64>,
    news_item_id: Option<i64>,
) -> Result<ChatListCursor, &'static str> {
    let decoded = URL_SAFE
        .decode(cursor)
        .or_else(|_| URL_SAFE_NO_PAD.decode(cursor))
        .map_err(|_| "Invalid pagination cursor")?;
    let payload: CursorPayload =
        serde_json::from_slice(&decoded).map_err(|_| "Invalid pagination cursor")?;
    if payload
        .filters_hash
        .as_deref()
        .is_some_and(|value| !value.is_empty() && value != filters_hash(content_id, news_item_id))
    {
        return Err("Invalid pagination cursor for filters");
    }
    let last_activity_at =
        parse_datetime(&payload.last_created_at).ok_or("Invalid pagination cursor")?;
    Ok(ChatListCursor {
        last_id: payload.last_id,
        last_activity_at,
    })
}

pub(super) fn encode(
    last_id: i64,
    last_activity_at: DateTime<Utc>,
    content_id: Option<i64>,
    news_item_id: Option<i64>,
) -> String {
    let mut payload = BTreeMap::new();
    payload.insert(
        "filters_hash",
        Value::String(filters_hash(content_id, news_item_id)),
    );
    payload.insert(
        "last_created_at",
        Value::String(
            last_activity_at
                .naive_utc()
                .format("%Y-%m-%dT%H:%M:%S%.f")
                .to_string(),
        ),
    );
    payload.insert("last_id", Value::from(last_id));
    URL_SAFE.encode(serde_json::to_vec(&payload).expect("chat cursor serialization is infallible"))
}

fn filters_hash(content_id: Option<i64>, news_item_id: Option<i64>) -> String {
    // Python's json.dumps uses a space after `:`. Preserve that byte representation so cursors
    // remain valid in both runtimes during the ownership transition.
    let normalized = match (content_id, news_item_id) {
        (Some(content_id), None) => format!(r#"{{"content_id": {content_id}}}"#),
        (None, Some(news_item_id)) => format!(r#"{{"news_item_id": {news_item_id}}}"#),
        _ => "{}".to_owned(),
    };
    hex_encode(&Sha256::digest(normalized.as_bytes()))
}

fn parse_datetime(value: &str) -> Option<DateTime<Utc>> {
    DateTime::parse_from_rfc3339(value)
        .map(|value| value.with_timezone(&Utc))
        .ok()
        .or_else(|| {
            [
                "%Y-%m-%dT%H:%M:%S%.f",
                "%Y-%m-%d %H:%M:%S%.f",
                "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%d %H:%M:%S",
            ]
            .into_iter()
            .find_map(|format| NaiveDateTime::parse_from_str(value, format).ok())
            .map(|value| value.and_utc())
        })
}

#[cfg(test)]
mod tests {
    use super::{decode, encode, filters_hash};

    #[test]
    fn hash_matches_python_json_dumps_spacing() {
        assert_eq!(
            filters_hash(None, None),
            "44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a"
        );
        assert_eq!(
            filters_hash(Some(7), None),
            "1facc88a5e6e813316efbdfa16157d4551b72d7d28d74197701b8bce838cf34f"
        );
    }

    #[test]
    fn round_trips_chat_cursor() {
        let timestamp = chrono::DateTime::parse_from_rfc3339("2026-08-30T12:34:56.123456Z")
            .unwrap()
            .to_utc();
        let encoded = encode(42, timestamp, None, Some(9));
        let decoded = decode(&encoded, None, Some(9)).unwrap();
        assert_eq!(decoded.last_id, 42);
        assert_eq!(decoded.last_activity_at, timestamp);
    }
}
