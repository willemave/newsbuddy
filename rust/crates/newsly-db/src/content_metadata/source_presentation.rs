//! Shared source attribution and actual publication dates for Briefing rendering and composition.
use chrono::{DateTime, NaiveDateTime, Utc};
use serde_json::{Map, Value};

pub fn content_publication_date(
    url: &str,
    metadata: &Map<String, Value>,
    created_at: DateTime<Utc>,
    publication_date: Option<DateTime<Utc>>,
) -> Option<DateTime<Utc>> {
    if is_x_status_url(url) {
        return tweet_publication_date(metadata);
    }
    // Extraction historically substituted ingestion time into this SQL column.
    // Preserve an explicitly extracted date, but do not present that fallback.
    clean_string(metadata.get("publication_date"))
        .and_then(|raw| DateTime::parse_from_rfc3339(&raw).ok())
        .map(|date| date.with_timezone(&Utc))
        .or_else(|| publication_date.filter(|date| *date != created_at))
}

fn is_x_status_url(raw: &str) -> bool {
    let Ok(url) = url::Url::parse(raw) else {
        return false;
    };
    let is_x = url.host_str().is_some_and(|host| {
        matches!(
            host,
            "x.com" | "www.x.com" | "twitter.com" | "www.twitter.com" | "mobile.twitter.com"
        )
    });
    is_x && url.path_segments().is_some_and(|segments| {
        let segments = segments.collect::<Vec<_>>();
        segments.windows(2).any(|pair| {
            pair[0] == "status"
                && !pair[1].is_empty()
                && pair[1].bytes().all(|value| value.is_ascii_digit())
        })
    })
}

// Content ingestion and Briefing admission are distinct from source publication.
fn tweet_publication_date(metadata: &Map<String, Value>) -> Option<DateTime<Utc>> {
    let raw = tweet_metadata_text(metadata, "tweet_created_at", "created_at")?;
    DateTime::parse_from_rfc3339(&raw)
        .map(|date| date.with_timezone(&Utc))
        // Historical X imports stored UTC without an explicit offset.
        .or_else(|_| NaiveDateTime::parse_from_str(&raw, "%Y-%m-%dT%H:%M:%S%.f").map(|date| date.and_utc()))
        .ok()
}

fn tweet_metadata_text(
    metadata: &Map<String, Value>,
    legacy: &str,
    snapshot: &str,
) -> Option<String> {
    clean_string(metadata.get(legacy)).or_else(|| {
        clean_string(
            metadata
                .get("tweet_snapshot")
                .and_then(|tweet| tweet.get(snapshot)),
        )
    })
}

pub fn content_publisher(
    url: &str,
    source_name: Option<&str>,
    metadata: &Map<String, Value>,
) -> Option<String> {
    if !is_x_status_url(url) {
        return source_name
            .and_then(clean_text)
            .or_else(|| clean_string(metadata.get("source")));
    }
    let author = tweet_metadata_text(metadata, "tweet_author", "author_name");
    let handle = tweet_metadata_text(metadata, "tweet_author_username", "author_username")
        .and_then(|handle| clean_text(handle.trim_start_matches('@')))
        .map(|handle| format!("@{handle}"));
    Some(match (author, handle) {
        (Some(author), Some(handle)) => format!("{author} ({handle})"),
        (Some(author), None) => author,
        (None, Some(handle)) => handle,
        (None, None) => "X".to_owned(),
    })
}

fn clean_text(raw: &str) -> Option<String> {
    let text = raw.trim();
    (!text.is_empty()).then(|| text.to_owned())
}

fn clean_string(value: Option<&Value>) -> Option<String> {
    value.and_then(Value::as_str).and_then(clean_text)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn canonical_x_status_uses_original_date_and_author() {
        let created = DateTime::parse_from_rfc3339("2026-03-27T12:00:00Z")
            .unwrap()
            .with_timezone(&Utc);
        let original = DateTime::parse_from_rfc3339("2023-04-01T12:00:00Z")
            .unwrap()
            .with_timezone(&Utc);
        let metadata = json!({"tweet_author":"Writer", "tweet_author_username":"writer", "tweet_created_at":"2023-04-01T12:00:00Z"});
        let map = metadata.as_object().unwrap();
        assert_eq!(
            content_publication_date(
                "https://x.com/writer/status/123",
                map,
                created,
                Some(created)
            ),
            Some(original)
        );
        assert_eq!(
            content_publisher(
                "https://x.com/writer/status/123",
                Some("self submission"),
                map
            )
            .as_deref(),
            Some("Writer (@writer)")
        );
        assert_eq!(
            content_publication_date(
                "https://publisher.example/article",
                map,
                created,
                Some(created)
            ),
            None
        );
        assert_eq!(
            content_publisher("https://publisher.example/article", Some("Publisher"), map)
                .as_deref(),
            Some("Publisher")
        );
    }

    #[test]
    fn unknown_dates_and_non_status_x_urls_do_not_adopt_tweet_metadata() {
        let created = DateTime::parse_from_rfc3339("2026-03-27T12:00:00Z")
            .unwrap()
            .with_timezone(&Utc);
        let map = Map::new();
        assert_eq!(
            content_publication_date(
                "https://x.com/writer/status/123",
                &map,
                created,
                Some(created)
            ),
            None
        );
        for url in [
            "https://x.com/writer",
            "https://x.com.evil.example/writer/status/123",
        ] {
            assert_eq!(
                content_publisher(url, Some("Publisher"), &map).as_deref(),
                Some("Publisher")
            );
        }
    }
}
