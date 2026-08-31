use serde_json::{Map, Value};
use sha2::{Digest, Sha256};

const VIDEO_TRANSCRIPT_MARKER: &str = "[Embedded video transcript]";

pub(super) fn build_summarization_payload(
    content_type: &str,
    metadata: &Value,
    source_text: Option<&str>,
) -> String {
    let view = runtime_metadata_view(metadata);
    let empty = Map::new();
    let root = metadata.as_object().unwrap_or(&empty);
    match content_type {
        "article" => append_video_transcript(
            first_text(
                source_text,
                [&view, root],
                &["content", "content_to_summarize"],
            ),
            &view,
        ),
        "news" => {
            let article = append_video_transcript(
                first_text(
                    source_text,
                    [&view, root],
                    &["content", "content_to_summarize"],
                ),
                &view,
            );
            let context = build_news_context(&view);
            if !article.trim().is_empty() && !context.is_empty() {
                format!("Context:\n{context}\n\nArticle Content:\n{article}")
            } else {
                article
            }
        }
        "podcast" => first_text(
            source_text,
            [&view, root],
            &["transcript", "content_to_summarize"],
        ),
        _ => String::new(),
    }
}

pub(super) fn input_fingerprint(content_type: &str, payload: &str) -> String {
    let normalized = payload.split_whitespace().collect::<Vec<_>>().join(" ");
    let digest = Sha256::digest(format!("{content_type}\n{normalized}").as_bytes());
    hex_encode(&digest)
}

pub(super) fn summary_matches(metadata: &Value, fingerprint: &str) -> bool {
    let view = runtime_metadata_view(metadata);
    view.get("summary").is_some_and(Value::is_object)
        && view
            .get("summarization_input_fingerprint")
            .and_then(Value::as_str)
            == Some(fingerprint)
}

pub(super) fn runtime_metadata_view(metadata: &Value) -> Map<String, Value> {
    let Some(root) = metadata.as_object() else {
        return Map::new();
    };
    let mut domain = root
        .get("domain")
        .and_then(Value::as_object)
        .cloned()
        .unwrap_or_default();
    for (key, value) in root {
        if !matches!(key.as_str(), "domain" | "processing") {
            domain.entry(key.clone()).or_insert_with(|| value.clone());
        }
    }
    if let Some(processing) = root.get("processing").and_then(Value::as_object) {
        for (key, value) in processing {
            domain.insert(key.clone(), value.clone());
        }
    }
    domain
}

fn first_text(source_text: Option<&str>, maps: [&Map<String, Value>; 2], keys: &[&str]) -> String {
    if let Some(text) = source_text.map(str::trim).filter(|value| !value.is_empty()) {
        return text.to_owned();
    }
    for map in maps {
        for key in keys {
            if let Some(text) = map
                .get(*key)
                .and_then(Value::as_str)
                .map(str::trim)
                .filter(|value| !value.is_empty())
            {
                return text.to_owned();
            }
        }
    }
    String::new()
}

fn append_video_transcript(mut text: String, metadata: &Map<String, Value>) -> String {
    let Some(transcript) = metadata
        .get("video_transcript")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())
    else {
        return text;
    };
    if !text.trim().is_empty() {
        text.push_str("\n\n");
    }
    text.push_str(VIDEO_TRANSCRIPT_MARKER);
    text.push('\n');
    text.push_str(transcript);
    text
}

fn build_news_context(metadata: &Map<String, Value>) -> String {
    let article = metadata
        .get("article")
        .and_then(Value::as_object)
        .cloned()
        .unwrap_or_default();
    let aggregator = metadata
        .get("aggregator")
        .and_then(Value::as_object)
        .cloned()
        .unwrap_or_default();
    let mut lines = Vec::new();
    if let Some(title) = clean_text(article.get("title")) {
        lines.push(format!("Article Title: {title}"));
    }
    if let Some(url) = clean_text(article.get("url")) {
        lines.push(format!("Article URL: {url}"));
    }
    if !aggregator.is_empty() {
        let name =
            clean_text(aggregator.get("name")).or_else(|| clean_text(metadata.get("platform")));
        let author = clean_text(aggregator.get("author"));
        if let Some(title) = clean_text(aggregator.get("title")) {
            lines.push(format!("Aggregator Headline: {title}"));
        }
        let context = [name, author.map(|value| format!("by {value}"))]
            .into_iter()
            .flatten()
            .collect::<Vec<_>>()
            .join(", ");
        if !context.is_empty() {
            lines.push(format!("Aggregator Context: {context}"));
        }
        if let Some(url) =
            clean_text(metadata.get("discussion_url")).or_else(|| clean_text(aggregator.get("url")))
        {
            lines.push(format!("Discussion URL: {url}"));
        }
        if let Some(extra) = aggregator.get("metadata").and_then(Value::as_object) {
            let signals = ["score", "comments_count", "likes", "retweets", "replies"]
                .into_iter()
                .filter_map(|key| extra.get(key).map(|value| format!("{key}={value}")))
                .collect::<Vec<_>>();
            if !signals.is_empty() {
                lines.push(format!("Signals: {}", signals.join(", ")));
            }
        }
    }
    let excerpt = clean_text(metadata.get("excerpt")).or_else(|| {
        metadata
            .get("summary")
            .and_then(Value::as_object)
            .and_then(|summary| {
                ["overview", "summary", "hook", "takeaway"]
                    .into_iter()
                    .find_map(|key| clean_text(summary.get(key)))
            })
    });
    if let Some(excerpt) = excerpt {
        lines.push(format!("Aggregator Summary: {excerpt}"));
    }
    lines.join("\n")
}

fn clean_text(value: Option<&Value>) -> Option<String> {
    value
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(str::to_owned)
}

fn hex_encode(bytes: &[u8]) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut encoded = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        encoded.push(char::from(HEX[usize::from(byte >> 4)]));
        encoded.push(char::from(HEX[usize::from(byte & 0x0f)]));
    }
    encoded
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::*;

    #[test]
    fn fingerprint_is_stable_across_whitespace() {
        assert_eq!(
            input_fingerprint("article", "one\n two"),
            input_fingerprint("article", " one   two ")
        );
    }

    #[test]
    fn nested_processing_metadata_wins() {
        let value = json!({
            "content": "old",
            "domain": {"content": "domain"},
            "processing": {"content": "new"},
        });
        assert_eq!(build_summarization_payload("article", &value, None), "new");
    }
}
