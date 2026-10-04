use chrono::{DateTime, Utc};
use newsly_db::ChatListCursor;
use serde::{Deserialize, Serialize};

use crate::pagination_cursor::{self, InvalidCursor};

#[derive(Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct CursorPayload {
    last_id: i64,
    last_created_at: String,
    filters_hash: String,
}

#[derive(Serialize)]
struct CursorFilters {
    route: &'static str,
    content_id: Option<i64>,
    news_item_id: Option<i64>,
}

pub(super) fn decode(
    cursor: &str,
    content_id: Option<i64>,
    news_item_id: Option<i64>,
) -> Result<ChatListCursor, InvalidCursor> {
    let payload: CursorPayload = pagination_cursor::decode(cursor)?;
    if payload.last_id <= 0 || payload.filters_hash != filters_hash(content_id, news_item_id) {
        return Err(InvalidCursor);
    }
    Ok(ChatListCursor {
        last_id: payload.last_id,
        last_activity_at: pagination_cursor::decode_timestamp(&payload.last_created_at)?.and_utc(),
    })
}

pub(super) fn encode(
    last_id: i64,
    last_activity_at: DateTime<Utc>,
    content_id: Option<i64>,
    news_item_id: Option<i64>,
) -> Result<String, InvalidCursor> {
    if last_id <= 0 {
        return Err(InvalidCursor);
    }
    pagination_cursor::encode(&CursorPayload {
        last_id,
        last_created_at: pagination_cursor::encode_timestamp(last_activity_at.naive_utc()),
        filters_hash: filters_hash(content_id, news_item_id),
    })
}

fn filters_hash(content_id: Option<i64>, news_item_id: Option<i64>) -> String {
    let bytes = serde_json::to_vec(&CursorFilters {
        route: "chat_sessions",
        content_id,
        news_item_id,
    })
    .expect("chat cursor filters are serializable");
    newsly_domain::sha256_hex(&bytes)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn round_trips_chat_cursor_and_binds_filters() {
        let timestamp = chrono::DateTime::parse_from_rfc3339("2026-08-30T12:34:56.123456Z")
            .unwrap()
            .to_utc();
        let encoded = encode(42, timestamp, None, Some(9)).expect("cursor encodes");
        let decoded = decode(&encoded, None, Some(9)).expect("cursor decodes");
        assert_eq!(decoded.last_id, 42);
        assert_eq!(decoded.last_activity_at, timestamp);
        assert!(decode(&encoded, Some(9), None).is_err());
    }

    #[test]
    fn rejects_nonpositive_id() {
        let cursor = pagination_cursor::encode(&CursorPayload {
            last_id: 0,
            last_created_at: "2026-08-30T12:34:56.123456Z".to_owned(),
            filters_hash: filters_hash(None, None),
        })
        .expect("fixture encodes");
        assert!(decode(&cursor, None, None).is_err());
    }
}
