use std::collections::{BTreeMap, BTreeSet};
use std::path::Path;

use chrono::{DateTime, Utc};
use futures_util::StreamExt;
use reqwest::{RequestBuilder, StatusCode, Url};
use serde_json::Value;

use crate::content_misc::{
    ContentMiscGatewayError, DiscussionCommentHit, DiscussionLinkHit, DiscussionRefreshResult,
    DiscussionThreadHit,
};

const HN_ALGOLIA_ITEM_URL: &str = "https://hn.algolia.com/api/v1/items/";
const HN_FIREBASE_ITEM_URL: &str = "https://hacker-news.firebaseio.com/v0/item/";
const MAX_PROVIDER_RESPONSE_BYTES: usize = 16 * 1024 * 1024;
const COMMENT_CAP: usize = 1_000;

pub(super) async fn refresh_discussion(
    client: &reqwest::Client,
    platform: Option<&str>,
    discussion_url: Option<&str>,
    external_id: Option<&str>,
) -> Result<DiscussionRefreshResult, ContentMiscGatewayError> {
    let platform = infer_platform(platform, discussion_url)
        .ok_or(ContentMiscGatewayError::UnsupportedDiscussionPlatform)?;
    match platform.as_str() {
        "hackernews" => refresh_hacker_news(client, discussion_url, external_id).await,
        "reddit" => refresh_reddit(client, discussion_url, external_id).await,
        _ => Err(ContentMiscGatewayError::UnsupportedDiscussionPlatform),
    }
}

async fn refresh_hacker_news(
    client: &reqwest::Client,
    discussion_url: Option<&str>,
    external_id: Option<&str>,
) -> Result<DiscussionRefreshResult, ContentMiscGatewayError> {
    let item_id = external_id
        .and_then(positive_numeric_string)
        .or_else(|| discussion_url.and_then(hacker_news_id))
        .ok_or(ContentMiscGatewayError::DiscussionIdentityMissing)?;
    let firebase_url = format!("{HN_FIREBASE_ITEM_URL}{item_id}.json");
    let firebase = match fetch_json(client.get(firebase_url)).await {
        Ok(value) => value,
        Err(error) if error.status == Some(StatusCode::NOT_FOUND) => {
            return Err(unavailable("gone", "Hacker News discussion is gone"));
        }
        Err(error) => return Err(error.into_gateway()),
    };
    if firebase.is_null()
        || firebase.get("dead").and_then(Value::as_bool) == Some(true)
        || firebase.get("deleted").and_then(Value::as_bool) == Some(true)
    {
        return Err(unavailable("gone", "Hacker News discussion is gone"));
    }
    require_object_payload(&firebase, "Hacker News Firebase")?;
    let algolia_url = format!("{HN_ALGOLIA_ITEM_URL}{item_id}");
    let algolia = match fetch_json(client.get(algolia_url)).await {
        Ok(value) => value,
        Err(error) if error.status == Some(StatusCode::NOT_FOUND) => {
            return Err(unavailable("gone", "Hacker News discussion is gone"));
        }
        Err(error) => return Err(error.into_gateway()),
    };
    require_object_payload(&algolia, "Hacker News Algolia")?;
    let source_url = discussion_url
        .and_then(normalize_http_url)
        .unwrap_or_else(|| format!("https://news.ycombinator.com/item?id={item_id}"));
    let mut comments = Vec::new();
    let mut total_seen = 0usize;
    let mut cap_reached = false;
    if let Some(children) = algolia.get("children").and_then(Value::as_array) {
        flatten_hacker_news(
            children,
            None,
            0,
            &mut comments,
            &mut total_seen,
            &mut cap_reached,
        );
    }
    let links = links_from_comments(&comments);
    let declared_comment_count = nonnegative_i64(firebase.get("descendants"));
    let thread = DiscussionThreadHit {
        title: clean_value(firebase.get("title")).or_else(|| clean_value(algolia.get("title"))),
        author: clean_value(firebase.get("by")).or_else(|| clean_value(algolia.get("author"))),
        score: nonnegative_i64(firebase.get("score"))
            .or_else(|| nonnegative_i64(algolia.get("points"))),
        comment_count: declared_comment_count,
        created_at: unix_to_rfc3339(firebase.get("time"))
            .or_else(|| clean_value(algolia.get("created_at"))),
        subreddit: None,
    };
    Ok(build_result(
        "hackernews",
        item_id,
        source_url,
        "algolia",
        thread,
        comments,
        links,
        total_seen,
        cap_reached,
    ))
}

async fn refresh_reddit(
    client: &reqwest::Client,
    discussion_url: Option<&str>,
    external_id: Option<&str>,
) -> Result<DiscussionRefreshResult, ContentMiscGatewayError> {
    let source_url = discussion_url
        .and_then(normalize_reddit_url)
        .ok_or(ContentMiscGatewayError::DiscussionIdentityMissing)?;
    let item_id = external_id
        .and_then(nonempty_owned)
        .or_else(|| reddit_submission_id(&source_url))
        .ok_or(ContentMiscGatewayError::DiscussionIdentityMissing)?;
    let mut endpoint =
        Url::parse(&source_url).map_err(|error| ContentMiscGatewayError::DiscussionFetch {
            message: format!("invalid Reddit discussion URL: {error}"),
            retryable: false,
        })?;
    let base_path = endpoint.path().trim_end_matches('/').to_owned();
    if !Path::new(&base_path)
        .extension()
        .is_some_and(|extension| extension.eq_ignore_ascii_case("json"))
    {
        endpoint.set_path(&format!("{base_path}.json"));
    }
    endpoint.set_fragment(None);
    endpoint
        .query_pairs_mut()
        .append_pair("raw_json", "1")
        .append_pair("limit", "500");
    let payload = fetch_json(
        client
            .get(endpoint)
            .header(reqwest::header::USER_AGENT, "Newsly/1.0 discussion refresh"),
    )
    .await
    .map_err(FetchFailure::into_gateway)?;
    let listings = payload
        .as_array()
        .ok_or_else(|| ContentMiscGatewayError::DiscussionFetch {
            message: "Reddit discussion endpoint returned a non-array payload".to_owned(),
            retryable: true,
        })?;
    let post = listings
        .first()
        .and_then(|listing| listing.pointer("/data/children/0/data"));
    let mut comments = Vec::new();
    let mut total_seen = 0usize;
    let mut cap_reached = false;
    if let Some(children) = listings
        .get(1)
        .and_then(|listing| listing.pointer("/data/children"))
        .and_then(Value::as_array)
    {
        flatten_reddit(
            children,
            None,
            0,
            &source_url,
            &mut comments,
            &mut total_seen,
            &mut cap_reached,
        );
    }
    let links = links_from_comments(&comments);
    let thread = DiscussionThreadHit {
        title: post.and_then(|value| clean_value(value.get("title"))),
        author: post.and_then(|value| clean_value(value.get("author"))),
        score: post.and_then(|value| nonnegative_i64(value.get("score"))),
        comment_count: post.and_then(|value| nonnegative_i64(value.get("num_comments"))),
        created_at: post.and_then(|value| unix_to_rfc3339(value.get("created_utc"))),
        subreddit: post.and_then(|value| clean_value(value.get("subreddit"))),
    };
    Ok(build_result(
        "reddit",
        item_id,
        source_url,
        "reddit",
        thread,
        comments,
        links,
        total_seen,
        cap_reached,
    ))
}

#[allow(clippy::too_many_arguments)]
fn build_result(
    platform: &str,
    external_id: String,
    source_url: String,
    provider: &str,
    thread: DiscussionThreadHit,
    comments: Vec<DiscussionCommentHit>,
    links: Vec<DiscussionLinkHit>,
    total_seen: usize,
    cap_reached: bool,
) -> DiscussionRefreshResult {
    let mut stats = BTreeMap::new();
    stats.insert("provider".to_owned(), Value::String(provider.to_owned()));
    stats.insert(
        "declared_comment_count".to_owned(),
        thread.comment_count.map_or(Value::Null, Value::from),
    );
    stats.insert(
        "fetched_count".to_owned(),
        Value::from(u64::try_from(comments.len()).unwrap_or(u64::MAX)),
    );
    stats.insert(
        "total_seen".to_owned(),
        Value::from(u64::try_from(total_seen).unwrap_or(u64::MAX)),
    );
    stats.insert(
        "stored_comment_cap".to_owned(),
        Value::from(u64::try_from(COMMENT_CAP).unwrap_or(u64::MAX)),
    );
    stats.insert("cap_reached".to_owned(), Value::Bool(cap_reached));
    DiscussionRefreshResult {
        platform: platform.to_owned(),
        external_id,
        source_url,
        provider: provider.to_owned(),
        thread,
        comments,
        links,
        total_seen,
        comment_cap: COMMENT_CAP,
        cap_reached,
        stats,
    }
}

fn flatten_hacker_news(
    values: &[Value],
    parent_id: Option<&str>,
    depth: i64,
    output: &mut Vec<DiscussionCommentHit>,
    total_seen: &mut usize,
    cap_reached: &mut bool,
) {
    for value in values {
        if output.len() >= COMMENT_CAP {
            *cap_reached = true;
            return;
        }
        let Some(object) = value.as_object() else {
            continue;
        };
        *total_seen = (*total_seen).saturating_add(1);
        let comment_id = object.get("id").and_then(value_string);
        let text = object
            .get("text")
            .and_then(Value::as_str)
            .map(strip_html)
            .unwrap_or_default();
        let next_parent = if let (Some(comment_id), false) = (comment_id.as_ref(), text.is_empty())
        {
            output.push(DiscussionCommentHit {
                comment_id: comment_id.clone(),
                parent_id: parent_id.map(str::to_owned),
                author: clean_value(object.get("author")),
                compact_text: compact_text(&text),
                text,
                depth,
                created_at: clean_value(object.get("created_at"))
                    .or_else(|| unix_to_rfc3339(object.get("created_at_i"))),
                source_url: Some(format!("https://news.ycombinator.com/item?id={comment_id}")),
            });
            Some(comment_id.as_str())
        } else {
            parent_id
        };
        if let Some(children) = object.get("children").and_then(Value::as_array) {
            flatten_hacker_news(
                children,
                next_parent,
                depth.saturating_add(1),
                output,
                total_seen,
                cap_reached,
            );
            if *cap_reached {
                return;
            }
        }
    }
}

#[allow(clippy::too_many_arguments)]
fn flatten_reddit(
    values: &[Value],
    parent_id: Option<&str>,
    depth: i64,
    source_url: &str,
    output: &mut Vec<DiscussionCommentHit>,
    total_seen: &mut usize,
    cap_reached: &mut bool,
) {
    for value in values {
        if output.len() >= COMMENT_CAP {
            *cap_reached = true;
            return;
        }
        if value.get("kind").and_then(Value::as_str) != Some("t1") {
            continue;
        }
        let Some(data) = value.get("data").and_then(Value::as_object) else {
            continue;
        };
        *total_seen = (*total_seen).saturating_add(1);
        let comment_id = data.get("id").and_then(value_string);
        let text = data
            .get("body")
            .and_then(Value::as_str)
            .map(clean_text)
            .unwrap_or_default();
        let usable = !matches!(text.as_str(), "" | "[deleted]" | "[removed]");
        let next_parent = if let (Some(comment_id), true) = (comment_id.as_ref(), usable) {
            output.push(DiscussionCommentHit {
                comment_id: comment_id.clone(),
                parent_id: parent_id.map(str::to_owned),
                author: clean_value(data.get("author")),
                compact_text: compact_text(&text),
                text,
                depth,
                created_at: unix_to_rfc3339(data.get("created_utc")),
                source_url: Some(format!(
                    "{}/{}",
                    source_url.trim_end_matches('/'),
                    comment_id
                )),
            });
            Some(comment_id.as_str())
        } else {
            parent_id
        };
        if let Some(children) = data
            .get("replies")
            .filter(|value| value.is_object())
            .and_then(|value| value.pointer("/data/children"))
            .and_then(Value::as_array)
        {
            flatten_reddit(
                children,
                next_parent,
                depth.saturating_add(1),
                source_url,
                output,
                total_seen,
                cap_reached,
            );
            if *cap_reached {
                return;
            }
        }
    }
}

fn links_from_comments(comments: &[DiscussionCommentHit]) -> Vec<DiscussionLinkHit> {
    let mut seen = BTreeSet::new();
    let mut output = Vec::new();
    for comment in comments {
        for token in comment.text.split_whitespace() {
            let candidate = token.trim_matches(|character: char| {
                matches!(
                    character,
                    '"' | '\'' | '<' | '>' | '(' | ')' | '[' | ']' | '{' | '}' | ',' | ';'
                )
            });
            let candidate = candidate.trim_end_matches(['.', ':', '!', '?']);
            let Some(url) = normalize_http_url(candidate) else {
                continue;
            };
            if !candidate.starts_with("http://")
                && !candidate.starts_with("https://")
                && !candidate.starts_with("//")
            {
                continue;
            }
            if seen.insert(url.clone()) {
                output.push(DiscussionLinkHit {
                    url,
                    comment_id: Some(comment.comment_id.clone()),
                    title: None,
                });
            }
        }
    }
    output
}

async fn fetch_json(request: RequestBuilder) -> Result<Value, FetchFailure> {
    let response = request
        .send()
        .await
        .map_err(|error| FetchFailure::transport(&error))?;
    let status = response.status();
    if !status.is_success() {
        return Err(FetchFailure::status(status));
    }
    if response
        .content_length()
        .is_some_and(|length| length > MAX_PROVIDER_RESPONSE_BYTES as u64)
    {
        return Err(FetchFailure::too_large());
    }
    let mut body = Vec::new();
    let mut stream = response.bytes_stream();
    while let Some(chunk) = stream.next().await {
        let chunk = chunk.map_err(|error| FetchFailure::transport(&error))?;
        if body.len().saturating_add(chunk.len()) > MAX_PROVIDER_RESPONSE_BYTES {
            return Err(FetchFailure::too_large());
        }
        body.extend_from_slice(&chunk);
    }
    serde_json::from_slice(&body).map_err(|error| FetchFailure {
        message: format!("discussion endpoint returned invalid JSON: {error}"),
        retryable: true,
        status: None,
    })
}

#[derive(Debug)]
struct FetchFailure {
    message: String,
    retryable: bool,
    status: Option<StatusCode>,
}

impl FetchFailure {
    fn transport(error: &reqwest::Error) -> Self {
        let status = error.status();
        Self {
            message: status.map_or_else(
                || format!("discussion provider request failed: {error}"),
                |status| format!("HTTP {status} while fetching discussion"),
            ),
            retryable: status.is_none_or(retryable_status),
            status,
        }
    }

    fn status(status: StatusCode) -> Self {
        Self {
            message: format!("HTTP {status} while fetching discussion"),
            retryable: retryable_status(status),
            status: Some(status),
        }
    }

    fn too_large() -> Self {
        Self {
            message: "discussion provider response exceeded the 16 MiB bound".to_owned(),
            retryable: false,
            status: None,
        }
    }

    fn into_gateway(self) -> ContentMiscGatewayError {
        ContentMiscGatewayError::DiscussionFetch {
            message: self.message,
            retryable: self.retryable,
        }
    }
}

fn unavailable(status: &str, message: &str) -> ContentMiscGatewayError {
    ContentMiscGatewayError::DiscussionUnavailable {
        status: status.to_owned(),
        message: message.to_owned(),
    }
}

fn require_object_payload(value: &Value, provider: &str) -> Result<(), ContentMiscGatewayError> {
    if value.is_object() {
        return Ok(());
    }
    Err(ContentMiscGatewayError::DiscussionFetch {
        message: format!("{provider} returned a non-object payload"),
        retryable: true,
    })
}

fn infer_platform(platform: Option<&str>, discussion_url: Option<&str>) -> Option<String> {
    if let Some(value) = platform.and_then(nonempty_owned) {
        let normalized = value.to_ascii_lowercase().replace([' ', '-'], "_");
        return match normalized.as_str() {
            "hn" | "hacker_news" | "hackernews" => Some("hackernews".to_owned()),
            "reddit" => Some("reddit".to_owned()),
            _ => None,
        };
    }
    let host = discussion_url
        .and_then(|value| Url::parse(value).ok())
        .and_then(|url| url.host_str().map(str::to_ascii_lowercase))?;
    if domain_matches(&host, "news.ycombinator.com") {
        Some("hackernews".to_owned())
    } else if domain_matches(&host, "reddit.com") || domain_matches(&host, "redd.it") {
        Some("reddit".to_owned())
    } else {
        None
    }
}

fn normalize_http_url(value: &str) -> Option<String> {
    let value = value.trim();
    if value.is_empty() {
        return None;
    }
    let candidate = if value.starts_with("//") {
        format!("https:{value}")
    } else if value.contains("://") {
        value.to_owned()
    } else {
        format!("https://{value}")
    };
    let mut url = Url::parse(&candidate).ok()?;
    if !matches!(url.scheme(), "http" | "https") || url.host_str().is_none() {
        return None;
    }
    url.set_scheme("https").ok()?;
    Some(url.to_string())
}

fn normalize_reddit_url(value: &str) -> Option<String> {
    let mut url = Url::parse(value).ok()?;
    let host = url.host_str()?.to_ascii_lowercase();
    if !domain_matches(&host, "reddit.com") && !domain_matches(&host, "redd.it") {
        return None;
    }
    if matches!(
        host.as_str(),
        "reddit.com" | "old.reddit.com" | "www.reddit.com"
    ) {
        url.set_host(Some("www.reddit.com")).ok()?;
    }
    url.set_scheme("https").ok()?;
    Some(url.to_string())
}

fn hacker_news_id(value: &str) -> Option<String> {
    let url = Url::parse(value).ok()?;
    url.query_pairs()
        .find(|(name, _)| name == "id")
        .and_then(|(_, value)| positive_numeric_string(&value))
        .or_else(|| {
            let value = url.path_segments()?.next_back()?.strip_suffix(".json")?;
            positive_numeric_string(value)
        })
}

fn reddit_submission_id(value: &str) -> Option<String> {
    let url = Url::parse(value).ok()?;
    let parts = url.path_segments()?.collect::<Vec<_>>();
    parts
        .windows(2)
        .find(|parts| parts[0].eq_ignore_ascii_case("comments"))
        .and_then(|parts| nonempty_owned(parts[1]))
        .map(|value| value.to_ascii_lowercase())
}

fn positive_numeric_string(value: &str) -> Option<String> {
    let value = value.trim();
    (!value.is_empty() && value.chars().all(|character| character.is_ascii_digit()))
        .then(|| value.to_owned())
}

fn nonempty_owned(value: &str) -> Option<String> {
    let value = value.trim();
    (!value.is_empty()).then(|| value.to_owned())
}

fn clean_value(value: Option<&Value>) -> Option<String> {
    value.and_then(value_string).map(|value| clean_text(&value))
}

fn value_string(value: &Value) -> Option<String> {
    value
        .as_str()
        .map(str::to_owned)
        .or_else(|| value.as_i64().map(|value| value.to_string()))
        .filter(|value| !value.trim().is_empty())
}

fn nonnegative_i64(value: Option<&Value>) -> Option<i64> {
    let value = value?;
    value
        .as_i64()
        .or_else(|| value.as_u64().and_then(|value| i64::try_from(value).ok()))
        .or_else(|| value.as_str()?.trim().replace(',', "").parse::<i64>().ok())
        .filter(|value| *value >= 0)
}

fn unix_to_rfc3339(value: Option<&Value>) -> Option<String> {
    let value = value?;
    let timestamp = value
        .as_i64()
        .or_else(|| value.as_u64().and_then(|value| i64::try_from(value).ok()))
        .or_else(|| value.as_f64().and_then(truncated_i64))
        .or_else(|| {
            let value = value.as_str()?.trim();
            value
                .parse::<i64>()
                .ok()
                .or_else(|| value.parse::<f64>().ok().and_then(truncated_i64))
        })?;
    DateTime::<Utc>::from_timestamp(timestamp, 0).map(|value| value.to_rfc3339())
}

fn truncated_i64(value: f64) -> Option<i64> {
    value
        .is_finite()
        .then(|| value.trunc().to_string().parse::<i64>().ok())
        .flatten()
}

fn compact_text(value: &str) -> String {
    let cleaned = clean_text(value);
    if cleaned.chars().count() <= 400 {
        return cleaned;
    }
    let mut output = cleaned.chars().take(397).collect::<String>();
    while output.chars().last().is_some_and(char::is_whitespace) {
        output.pop();
    }
    output.push_str("...");
    output
}

fn clean_text(value: &str) -> String {
    value.split_whitespace().collect::<Vec<_>>().join(" ")
}

fn strip_html(value: &str) -> String {
    let mut plain = String::with_capacity(value.len());
    let mut remaining = value;
    while let Some(tag_start) = remaining.find('<') {
        plain.push_str(&remaining[..tag_start]);
        let tag = &remaining[tag_start + 1..];
        let Some(tag_end) = tag.find('>') else {
            plain.push_str(&remaining[tag_start..]);
            remaining = "";
            break;
        };
        let tag_body = &tag[..tag_end];
        if let Some(href) = anchor_href(tag_body) {
            plain.push(' ');
            plain.push_str(href);
        }
        plain.push(' ');
        remaining = &tag[tag_end + 1..];
    }
    plain.push_str(remaining);
    clean_text(
        &plain
            .replace("&gt;", ">")
            .replace("&lt;", "<")
            .replace("&amp;", "&")
            .replace("&quot;", "\"")
            .replace("&#x27;", "'")
            .replace("&#39;", "'"),
    )
}

fn anchor_href(tag: &str) -> Option<&str> {
    let tag = tag.trim_start();
    let first = tag.as_bytes().first().copied()?;
    if !matches!(first, b'a' | b'A')
        || tag
            .as_bytes()
            .get(1)
            .is_some_and(|character| !character.is_ascii_whitespace())
    {
        return None;
    }
    let lower = tag.to_ascii_lowercase();
    let mut search_from = 1;
    while let Some(relative) = lower[search_from..].find("href") {
        let start = search_from + relative;
        if start > 0
            && !lower
                .as_bytes()
                .get(start - 1)
                .is_some_and(u8::is_ascii_whitespace)
        {
            search_from = start + 4;
            continue;
        }
        let mut cursor = start + 4;
        while lower
            .as_bytes()
            .get(cursor)
            .is_some_and(u8::is_ascii_whitespace)
        {
            cursor += 1;
        }
        if lower.as_bytes().get(cursor) != Some(&b'=') {
            search_from = start + 4;
            continue;
        }
        cursor += 1;
        while lower
            .as_bytes()
            .get(cursor)
            .is_some_and(u8::is_ascii_whitespace)
        {
            cursor += 1;
        }
        let delimiter = *tag.as_bytes().get(cursor)?;
        if matches!(delimiter, b'\'' | b'"') {
            let value_start = cursor + 1;
            let value_end = tag[value_start..].find(char::from(delimiter))? + value_start;
            return nonempty_borrowed(&tag[value_start..value_end]);
        }
        let value_end = tag[cursor..]
            .find(char::is_whitespace)
            .map_or(tag.len(), |offset| cursor + offset);
        return nonempty_borrowed(&tag[cursor..value_end]);
    }
    None
}

fn nonempty_borrowed(value: &str) -> Option<&str> {
    let value = value.trim();
    (!value.is_empty()).then_some(value)
}

fn domain_matches(host: &str, domain: &str) -> bool {
    host == domain || host.ends_with(&format!(".{domain}"))
}

fn retryable_status(status: StatusCode) -> bool {
    status == StatusCode::TOO_MANY_REQUESTS || status.is_server_error()
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::*;

    #[test]
    fn normalizes_hacker_news_tree_and_preserves_parentage() {
        let values = vec![json!({
            "id": 1,
            "author": "alice",
            "text": "<p>Hello &amp; welcome</p>",
            "children": [{"id": 2, "author": "bob", "text": "reply"}],
        })];
        let mut comments = Vec::new();
        let mut total_seen = 0;
        let mut cap_reached = false;
        flatten_hacker_news(
            &values,
            None,
            0,
            &mut comments,
            &mut total_seen,
            &mut cap_reached,
        );
        assert_eq!(comments.len(), 2);
        assert_eq!(comments[0].text, "Hello & welcome");
        assert_eq!(comments[1].parent_id.as_deref(), Some("1"));
        assert_eq!(total_seen, 2);
        assert!(!cap_reached);
    }

    #[test]
    fn canonicalizes_platform_and_ids() {
        assert_eq!(
            infer_platform(Some("hacker-news"), None).as_deref(),
            Some("hackernews")
        );
        assert_eq!(
            hacker_news_id("https://news.ycombinator.com/item?id=123").as_deref(),
            Some("123")
        );
        assert_eq!(
            reddit_submission_id("https://reddit.com/r/rust/comments/AbC123/title").as_deref(),
            Some("abc123")
        );
    }

    #[test]
    fn extracts_and_deduplicates_absolute_links() {
        let comments = vec![DiscussionCommentHit {
            comment_id: "1".to_owned(),
            parent_id: None,
            author: None,
            text: "See https://example.com/a. Again https://example.com/a".to_owned(),
            compact_text: String::new(),
            depth: 0,
            created_at: None,
            source_url: None,
        }];
        let links = links_from_comments(&comments);
        assert_eq!(links.len(), 1);
        assert_eq!(links[0].url, "https://example.com/a");
    }

    #[test]
    fn hacker_news_html_preserves_anchor_targets_for_link_extraction() {
        let text = strip_html(
            r#"<p>Read <a class="story" href="https://example.com/research">the paper</a>.</p>"#,
        );
        assert!(text.contains("https://example.com/research"));
        let links = links_from_comments(&[DiscussionCommentHit {
            comment_id: "1".to_owned(),
            parent_id: None,
            author: None,
            compact_text: compact_text(&text),
            text,
            depth: 0,
            created_at: None,
            source_url: None,
        }]);
        assert_eq!(links[0].url, "https://example.com/research");
    }

    #[test]
    fn malformed_hacker_news_provider_shapes_are_retryable() {
        let error = require_object_payload(&json!([]), "Hacker News Algolia")
            .expect_err("arrays must not be interpreted as empty discussions");
        assert!(error.discussion_retryable());
    }
}
