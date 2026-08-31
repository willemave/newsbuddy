use std::collections::{BTreeSet, HashSet};
use std::sync::LazyLock;

use newsly_domain::RelationExactKey;
use newsly_providers::{
    LinkCandidate, NewsClassification, NewsSummary, RelevantLink, RelevantLinkCategory,
};
use regex::Regex;
use serde_json::{Map, Value};
use url::{Url, form_urlencoded};

use super::model::{NewsSnapshot, RelationCandidate, RelevantLinkInput};

const MAX_NEWS_ARTICLE_BODY_CHARS: usize = 48_000;
const BODY_TRUNCATION_MARKER: &str = "\n\n[... middle of article body omitted ...]\n\n";
const MAX_DISCUSSION_SNIPPETS: usize = 5;
const MAX_LINK_CANDIDATES: usize = 30;
const MAX_LINK_CONTEXT_CHARS: usize = 240;

static MARKDOWN_LINK: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(
        r#"(?m)(?P<image>!)?\[(?P<title>[^\]\n]{1,300})\]\((?P<url>[^)\s]+)(?:\s+\"[^\"]*\")?\)"#,
    )
    .expect("markdown-link regex is valid")
});
static PLAIN_URL: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r#"https?://[^\s<>\]\"')]+"#).expect("plain-URL regex is valid"));
static MATCH_TOKEN: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"[a-z0-9]{3,}").expect("match-token regex is valid"));

const MATCH_STOPWORDS: [&str; 20] = [
    "about", "after", "against", "along", "also", "amid", "been", "between", "from", "have",
    "into", "more", "news", "over", "that", "their", "them", "they", "this", "with",
];
const PLACEHOLDER_TITLES: [&str; 6] = ["na", "n/a", "none", "unknown", "untitled", "void"];
const ASSET_EXTENSIONS: [&str; 17] = [
    ".7z", ".avi", ".css", ".gif", ".ico", ".jpeg", ".jpg", ".js", ".mov", ".mp3", ".mp4", ".png",
    ".svg", ".webm", ".webp", ".zip", ".pdf.png",
];
const JUNK_SEGMENTS: [&str; 21] = [
    "about",
    "account",
    "advertise",
    "author",
    "category",
    "contact",
    "feed",
    "feeds",
    "login",
    "logout",
    "newsletter",
    "privacy",
    "rss",
    "signin",
    "sign-in",
    "signup",
    "sign-up",
    "subscribe",
    "tag",
    "tags",
    "terms",
];
const TRACKING_QUERY_NAMES: [&str; 11] = [
    "fbclid", "gclid", "igshid", "mc_cid", "mc_eid", "mkt_tok", "ref", "ref_src", "source", "spm",
    "yclid",
];

pub(super) fn existing_summary(snapshot: &NewsSnapshot) -> Option<NewsSummary> {
    let metadata = snapshot.raw_metadata.as_object()?;
    let version_one = metadata.get("summary_version").and_then(Value::as_i64) == Some(1);
    let short_news = metadata.get("summary_kind").and_then(Value::as_str) == Some("short_news");
    if !version_one || (!short_news && !metadata.get("summary").is_some_and(Value::is_object)) {
        return None;
    }
    let summary_value = metadata.get("summary").and_then(Value::as_object);
    let key_points = if snapshot.summary_key_points.is_empty() {
        summary_value
            .and_then(|summary| summary.get("key_points"))
            .map(normalize_key_points)
            .unwrap_or_default()
    } else {
        snapshot.summary_key_points.clone()
    };
    let summary_text = snapshot
        .summary_text
        .as_deref()
        .and_then(clean_string)
        .or_else(|| {
            summary_value
                .and_then(|summary| summary.get("summary"))
                .and_then(Value::as_str)
                .and_then(clean_string)
        });
    if key_points.is_empty() && summary_text.is_none() {
        return None;
    }
    let title = summary_title(metadata)
        .or_else(|| article_title(metadata))
        .or_else(|| {
            summary_value
                .and_then(|summary| summary.get("title"))
                .and_then(Value::as_str)
                .and_then(clean_title)
        })
        .or_else(|| summary_text.as_deref().and_then(summary_title_fallback))?;
    let classification = summary_value
        .and_then(|summary| summary.get("classification"))
        .and_then(Value::as_str)
        .map_or(NewsClassification::ToRead, |classification| {
            if classification == "skip" {
                NewsClassification::Skip
            } else {
                NewsClassification::ToRead
            }
        });
    let summarization_date = summary_value
        .and_then(|summary| summary.get("summarization_date"))
        .and_then(Value::as_str)
        .and_then(|value| chrono::DateTime::parse_from_rfc3339(value).ok())
        .map_or_else(chrono::Utc::now, |value| value.with_timezone(&chrono::Utc));
    Some(NewsSummary {
        title,
        article_url: summary_value
            .and_then(|summary| summary.get("article_url"))
            .and_then(Value::as_str)
            .and_then(normalize_http_url)
            .or_else(|| snapshot.article_url.clone()),
        key_points,
        summary: summary_text.or_else(|| snapshot.summary_key_points.first().cloned())?,
        classification,
        summarization_date,
    })
}

pub(super) fn build_summary_prompt(snapshot: &NewsSnapshot, article_body: Option<&str>) -> String {
    let mut lines =
        vec!["Create a compact short-form news summary grounded only in this evidence.".to_owned()];
    push_field(&mut lines, "Source label", snapshot.source_label.as_deref());
    push_field(&mut lines, "Platform", snapshot.platform.as_deref());
    push_field(
        &mut lines,
        "Article title",
        snapshot
            .raw_metadata
            .as_object()
            .and_then(article_title)
            .as_deref(),
    );
    push_field(
        &mut lines,
        "Article domain",
        snapshot.article_domain.as_deref(),
    );
    push_field(&mut lines, "Article URL", snapshot.article_url.as_deref());
    let metadata = snapshot.raw_metadata.as_object();
    if let Some(aggregator) = metadata
        .and_then(|metadata| metadata.get("aggregator"))
        .and_then(Value::as_object)
    {
        push_field(
            &mut lines,
            "Aggregator title",
            aggregator.get("title").and_then(Value::as_str),
        );
        push_field(
            &mut lines,
            "Aggregator author",
            aggregator.get("author").and_then(Value::as_str),
        );
    }
    if let Some(body) = article_body.and_then(clean_body) {
        lines.extend([String::new(), "Article body:".to_owned(), bound_body(body)]);
    }
    if let Some(excerpt) = metadata
        .and_then(|metadata| metadata.get("excerpt"))
        .and_then(Value::as_str)
        .and_then(clean_string)
    {
        lines.extend([String::new(), "Excerpt:".to_owned(), excerpt]);
    }
    let snippets = discussion_snippets(metadata);
    if !snippets.is_empty() {
        lines.extend([String::new(), "Discussion snippets:".to_owned()]);
        lines.extend(snippets.into_iter().map(|snippet| format!("- {snippet}")));
    }
    lines.join("\n")
}

pub(super) fn resolved_summary(mut summary: NewsSummary, snapshot: &NewsSnapshot) -> NewsSummary {
    let fallback = snapshot
        .raw_metadata
        .as_object()
        .and_then(article_title)
        .or_else(|| snapshot.raw_metadata.as_object().and_then(summary_title))
        .or_else(|| summary_title_fallback(&summary.summary));
    if clean_title(&summary.title).is_none() {
        summary.title = fallback.unwrap_or_else(|| format!("News item {}", snapshot.id));
    } else {
        summary.title = clean_title(&summary.title).expect("checked title");
    }
    summary.key_points = summary
        .key_points
        .into_iter()
        .filter_map(|point| clean_string(&point))
        .take(5)
        .collect();
    summary.summary = clean_string(&summary.summary)
        .or_else(|| summary.key_points.first().cloned())
        .or_else(|| snapshot.summary_text.clone())
        .unwrap_or_else(|| summary.title.clone());
    if let Some(url) = summary.article_url.as_deref().and_then(normalize_http_url) {
        summary.article_url = Some(url);
    }
    summary
}

pub(super) fn exact_relation_key(snapshot: &NewsSnapshot) -> Option<RelationExactKey> {
    for (kind, candidate) in [
        (
            "story",
            snapshot
                .canonical_story_url
                .as_deref()
                .or(snapshot.article_url.as_deref()),
        ),
        (
            "item",
            snapshot
                .canonical_item_url
                .as_deref()
                .or(snapshot.discussion_url.as_deref()),
        ),
    ] {
        if let Some(value) = candidate.and_then(normalize_http_url) {
            return Some(RelationExactKey {
                kind: kind.to_owned(),
                value,
            });
        }
    }
    match (
        snapshot.platform.as_deref().and_then(clean_string),
        snapshot
            .source_external_id
            .as_deref()
            .and_then(clean_string),
    ) {
        (Some(platform), Some(external_id)) => Some(RelationExactKey {
            kind: "external".to_owned(),
            value: format!("{platform}:{external_id}"),
        }),
        _ => None,
    }
}

pub(super) fn relation_search_query(title: Option<&str>) -> Option<String> {
    let tokens = title
        .into_iter()
        .flat_map(|title| {
            let lower = title.to_ascii_lowercase();
            MATCH_TOKEN
                .find_iter(&lower)
                .map(|matched| normalize_match_token(matched.as_str()))
                .collect::<Vec<_>>()
        })
        .filter(|token| !token.is_empty() && !MATCH_STOPWORDS.contains(&token.as_str()))
        .collect::<BTreeSet<_>>();
    (!tokens.is_empty()).then(|| tokens.into_iter().collect::<Vec<_>>().join(" | "))
}

pub(super) fn prefilter_relation_candidates(
    item: &newsly_domain::NewsRelationDocument,
    candidates: Vec<RelationCandidate>,
) -> Vec<RelationCandidate> {
    if let Some(exact) = item.exact_relation_key.as_ref() {
        let exact_candidates = candidates
            .iter()
            .filter(|candidate| candidate.document.exact_relation_key.as_ref() == Some(exact))
            .cloned()
            .collect::<Vec<_>>();
        if !exact_candidates.is_empty() {
            return exact_candidates;
        }
    }
    let item_tokens = match_tokens(item.primary_title.as_deref().unwrap_or_default());
    if item_tokens.is_empty() {
        return Vec::new();
    }
    let item_domain = item.article_domain.as_deref().map(str::to_ascii_lowercase);
    let item_source = item.source_label.as_deref().map(str::to_ascii_lowercase);
    let mut ranked = candidates
        .into_iter()
        .enumerate()
        .filter_map(|(index, candidate)| {
            let tokens = candidate
                .document
                .related_titles
                .iter()
                .chain(candidate.document.primary_title.iter())
                .flat_map(|title| match_tokens(title))
                .collect::<HashSet<_>>();
            let overlap = item_tokens.intersection(&tokens).count();
            (overlap > 0).then(|| {
                let domain_match = item_domain.as_ref().is_some_and(|domain| {
                    candidate
                        .document
                        .article_domain
                        .as_deref()
                        .is_some_and(|value| value.eq_ignore_ascii_case(domain))
                });
                let source_match = item_source.as_ref().is_some_and(|source| {
                    candidate
                        .document
                        .source_label
                        .as_deref()
                        .is_some_and(|value| value.eq_ignore_ascii_case(source))
                });
                (overlap, domain_match, source_match, index, candidate)
            })
        })
        .collect::<Vec<_>>();
    ranked.sort_by(|left, right| {
        right
            .0
            .cmp(&left.0)
            .then_with(|| right.1.cmp(&left.1))
            .then_with(|| right.2.cmp(&left.2))
            .then_with(|| left.3.cmp(&right.3))
    });
    ranked.into_iter().take(12).map(|entry| entry.4).collect()
}

pub(super) fn metadata_tweet_body(snapshot: &NewsSnapshot) -> Option<(String, Option<String>)> {
    let metadata = snapshot.raw_metadata.as_object()?;
    let source_url = snapshot
        .article_url
        .as_deref()
        .or(snapshot.canonical_story_url.as_deref())
        .and_then(normalize_http_url);
    let expected_id = source_url.as_deref().and_then(tweet_id);
    if let Some(tweet) = metadata.get("tweet_snapshot").and_then(Value::as_object) {
        let snapshot_id = tweet
            .get("id")
            .and_then(Value::as_str)
            .or(expected_id.as_deref())?;
        if expected_id
            .as_deref()
            .is_some_and(|expected| expected != snapshot_id)
        {
            return None;
        }
        let body = rich_tweet_text(tweet)?;
        return Some((body, source_url));
    }
    let metadata_id = metadata
        .get("tweet_id")
        .and_then(Value::as_str)
        .or(expected_id.as_deref())?;
    if expected_id
        .as_deref()
        .is_some_and(|expected| expected != metadata_id)
    {
        return None;
    }
    let body = [
        "tweet_article_text",
        "tweet_note_tweet_text",
        "tweet_text",
        "tweet_article_title",
    ]
    .into_iter()
    .find_map(|key| {
        metadata
            .get(key)
            .and_then(Value::as_str)
            .and_then(clean_string)
    })?;
    Some((body, source_url))
}

pub(super) fn choose_article_url(snapshot: &NewsSnapshot) -> Option<String> {
    let discussion_url = snapshot
        .discussion_url
        .as_deref()
        .and_then(normalize_http_url);
    for candidate in [
        snapshot.article_url.as_deref(),
        snapshot.canonical_story_url.as_deref(),
    ] {
        let Some(url) = candidate.and_then(normalize_http_url) else {
            continue;
        };
        if discussion_url.as_deref() == Some(url.as_str()) {
            continue;
        }
        let host = Url::parse(&url)
            .ok()
            .and_then(|url| url.host_str().map(str::to_ascii_lowercase));
        if host.as_deref().is_some_and(|host| {
            aggregator_native_domains(snapshot.platform.as_deref()).contains(&host)
        }) {
            continue;
        }
        return Some(url);
    }
    None
}

pub(super) fn relevant_link_input(
    snapshot: &NewsSnapshot,
    article_body: &str,
) -> Option<RelevantLinkInput> {
    let metadata = snapshot.raw_metadata.as_object();
    if metadata
        .and_then(|metadata| metadata.get("article_relevant_links"))
        .is_some_and(Value::is_array)
    {
        return None;
    }
    let source_url = snapshot
        .article_url
        .clone()
        .or_else(|| snapshot.canonical_story_url.clone());
    let candidates = extract_link_candidates(article_body, source_url.as_deref());
    (!candidates.is_empty()).then(|| RelevantLinkInput {
        title: metadata.and_then(article_title),
        source_url,
        candidates,
    })
}

pub(super) fn relevant_links_json(links: &[RelevantLink]) -> Value {
    Value::Array(
        links
            .iter()
            .map(|link| {
                serde_json::json!({
                    "url": link.url,
                    "title": link.title,
                    "reason": link.reason,
                    "category": link_category(link.category),
                    "confidence": link.confidence,
                    "source": "article",
                })
            })
            .collect(),
    )
}

pub(super) fn summary_json(summary: &NewsSummary) -> Value {
    let mut payload = Map::from_iter([
        ("title".to_owned(), Value::String(summary.title.clone())),
        (
            "key_points".to_owned(),
            serde_json::to_value(&summary.key_points).unwrap_or_else(|_| Value::Array(Vec::new())),
        ),
        ("summary".to_owned(), Value::String(summary.summary.clone())),
        (
            "classification".to_owned(),
            Value::String(
                match summary.classification {
                    NewsClassification::ToRead => "to_read",
                    NewsClassification::Skip => "skip",
                }
                .to_owned(),
            ),
        ),
        (
            "summarization_date".to_owned(),
            Value::String(summary.summarization_date.to_rfc3339()),
        ),
    ]);
    if let Some(article_url) = &summary.article_url {
        payload.insert("article_url".to_owned(), Value::String(article_url.clone()));
    }
    Value::Object(payload)
}

pub(super) fn article_title(metadata: &Map<String, Value>) -> Option<String> {
    section_title(metadata, "article")
}

pub(super) fn summary_title(metadata: &Map<String, Value>) -> Option<String> {
    section_title(metadata, "summary")
}

pub(super) fn normalize_key_points(value: &Value) -> Vec<String> {
    value
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(|value| match value {
            Value::String(value) => clean_string(value),
            Value::Object(value) => value
                .get("text")
                .and_then(Value::as_str)
                .and_then(clean_string),
            _ => None,
        })
        .collect()
}

pub(super) fn clean_string(value: &str) -> Option<String> {
    let value = value.split_whitespace().collect::<Vec<_>>().join(" ");
    (!value.is_empty()).then_some(value)
}

pub(super) fn normalize_http_url(value: &str) -> Option<String> {
    let mut url = Url::parse(value.trim()).ok()?;
    if !matches!(url.scheme(), "http" | "https") || url.host_str().is_none() {
        return None;
    }
    url.set_scheme("https").ok()?;
    url.set_fragment(None);
    let host = url.host_str()?.to_ascii_lowercase();
    url.set_host(Some(&host)).ok()?;
    Some(url.to_string())
}

fn section_title(metadata: &Map<String, Value>, section: &str) -> Option<String> {
    metadata
        .get(section)
        .and_then(Value::as_object)
        .and_then(|section| section.get("title"))
        .and_then(Value::as_str)
        .and_then(clean_title)
}

fn clean_title(value: &str) -> Option<String> {
    let title = clean_string(value)?;
    (!PLACEHOLDER_TITLES.contains(&title.to_ascii_lowercase().as_str())).then_some(title)
}

fn summary_title_fallback(value: &str) -> Option<String> {
    let value = clean_string(value)?;
    Some(if value.chars().count() <= 120 {
        value
    } else {
        format!(
            "{}…",
            value.chars().take(120).collect::<String>().trim_end()
        )
    })
}

fn push_field(lines: &mut Vec<String>, label: &str, value: Option<&str>) {
    if let Some(value) = value.and_then(clean_string) {
        lines.push(format!("{label}: {value}"));
    }
}

fn clean_body(value: &str) -> Option<&str> {
    let value = value.trim();
    (!value.is_empty()).then_some(value)
}

fn bound_body(body: &str) -> String {
    if body.chars().count() <= MAX_NEWS_ARTICLE_BODY_CHARS {
        return body.to_owned();
    }
    let available = MAX_NEWS_ARTICLE_BODY_CHARS - BODY_TRUNCATION_MARKER.chars().count();
    let head_count = available * 3 / 4;
    let tail_count = available - head_count;
    let head = body.chars().take(head_count).collect::<String>();
    let tail = body
        .chars()
        .rev()
        .take(tail_count)
        .collect::<String>()
        .chars()
        .rev()
        .collect::<String>();
    format!(
        "{}{BODY_TRUNCATION_MARKER}{}",
        head.trim_end(),
        tail.trim_start()
    )
}

fn discussion_snippets(metadata: Option<&Map<String, Value>>) -> Vec<String> {
    let Some(discussion) = metadata
        .and_then(|metadata| metadata.get("discussion_payload"))
        .and_then(Value::as_object)
    else {
        return Vec::new();
    };
    if let Some(comments) = discussion.get("compact_comments").and_then(Value::as_array) {
        return comments
            .iter()
            .filter_map(Value::as_str)
            .filter_map(clean_string)
            .take(MAX_DISCUSSION_SNIPPETS)
            .collect();
    }
    discussion
        .get("comments")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(Value::as_object)
        .filter_map(|comment| {
            comment
                .get("compact_text")
                .or_else(|| comment.get("text"))
                .and_then(Value::as_str)
                .and_then(clean_string)
        })
        .take(MAX_DISCUSSION_SNIPPETS)
        .collect()
}

fn normalize_match_token(token: &str) -> String {
    let mut token = token.to_ascii_lowercase();
    if token.ends_with("ing") && token.len() > 6 {
        token.truncate(token.len() - 3);
    } else if (token.ends_with("ed") || token.ends_with("es")) && token.len() > 5 {
        token.truncate(token.len() - 2);
    } else if token.ends_with('s') && token.len() > 4 {
        token.truncate(token.len() - 1);
    }
    token
}

fn match_tokens(value: &str) -> HashSet<String> {
    MATCH_TOKEN
        .find_iter(&value.to_ascii_lowercase())
        .map(|matched| normalize_match_token(matched.as_str()))
        .filter(|token| !token.is_empty() && !MATCH_STOPWORDS.contains(&token.as_str()))
        .collect()
}

fn tweet_id(url: &str) -> Option<String> {
    static TWEET_ID: LazyLock<Regex> = LazyLock::new(|| {
        Regex::new(r"(?i)(?:twitter\.com|x\.com)/(?:i/)?(?:status|[^/]+/status)/(\d+)")
            .expect("tweet-id regex is valid")
    });
    TWEET_ID
        .captures(url)
        .and_then(|captures| captures.get(1))
        .map(|matched| matched.as_str().to_owned())
}

fn rich_tweet_text(tweet: &Map<String, Value>) -> Option<String> {
    let title = tweet
        .get("article_title")
        .and_then(Value::as_str)
        .and_then(clean_string);
    if let Some(text) = tweet
        .get("article_text")
        .and_then(Value::as_str)
        .and_then(clean_string)
    {
        return Some(match title {
            Some(title) if title != text && !text.starts_with(&title) => {
                format!("{title}\n\n{text}")
            }
            _ => text,
        });
    }
    ["note_tweet_text", "text"].into_iter().find_map(|key| {
        tweet
            .get(key)
            .and_then(Value::as_str)
            .and_then(clean_string)
    })
}

fn aggregator_native_domains(platform: Option<&str>) -> &'static [&'static str] {
    match platform.unwrap_or_default().to_ascii_lowercase().as_str() {
        "brutalist" => &["brutalist.report", "www.brutalist.report"],
        "hackernews" => &["news.ycombinator.com"],
        "mediagazer" => &["mediagazer.com", "www.mediagazer.com"],
        "memeorandum" => &["memeorandum.com", "www.memeorandum.com"],
        "reddit" => &["reddit.com", "www.reddit.com", "old.reddit.com", "redd.it"],
        "techmeme" => &["techmeme.com", "www.techmeme.com"],
        "twitter" | "x" => &["x.com", "www.x.com", "twitter.com", "www.twitter.com"],
        _ => &[],
    }
}

fn extract_link_candidates(text: &str, source_url: Option<&str>) -> Vec<LinkCandidate> {
    let mut seen = HashSet::new();
    let mut output = Vec::new();
    for captures in MARKDOWN_LINK.captures_iter(text) {
        if captures.name("image").is_some() {
            continue;
        }
        let Some(url_match) = captures.name("url") else {
            continue;
        };
        add_link_candidate(
            text,
            source_url,
            url_match.as_str(),
            captures.name("title").map(|value| value.as_str()),
            url_match.start(),
            url_match.end(),
            &mut seen,
            &mut output,
        );
        if output.len() >= MAX_LINK_CANDIDATES {
            return output;
        }
    }
    for matched in PLAIN_URL.find_iter(text) {
        add_link_candidate(
            text,
            source_url,
            matched.as_str(),
            None,
            matched.start(),
            matched.end(),
            &mut seen,
            &mut output,
        );
        if output.len() >= MAX_LINK_CANDIDATES {
            break;
        }
    }
    output
}

#[allow(clippy::too_many_arguments)]
fn add_link_candidate(
    text: &str,
    source_url: Option<&str>,
    raw_url: &str,
    title: Option<&str>,
    start: usize,
    end: usize,
    seen: &mut HashSet<String>,
    output: &mut Vec<LinkCandidate>,
) {
    let Some(url) = normalize_candidate_url(raw_url, source_url) else {
        return;
    };
    if !seen.insert(url.clone()) {
        return;
    }
    let context_start = text.floor_char_boundary(start.saturating_sub(120));
    let context_end = text.ceil_char_boundary((end + 120).min(text.len()));
    output.push(LinkCandidate {
        url,
        title: title.and_then(clean_string),
        context: clean_string(&text[context_start..context_end])
            .map(|value| value.chars().take(MAX_LINK_CONTEXT_CHARS).collect()),
    });
}

fn normalize_candidate_url(raw_url: &str, source_url: Option<&str>) -> Option<String> {
    let cleaned = raw_url
        .trim()
        .trim_matches(['<', '>'])
        .trim_end_matches(['.', ',', ';', ':']);
    let mut url = source_url
        .and_then(|source| Url::parse(source).ok())
        .and_then(|source| source.join(cleaned).ok())
        .or_else(|| Url::parse(cleaned).ok())?;
    if !matches!(url.scheme(), "http" | "https") {
        return None;
    }
    let host = url
        .host_str()?
        .trim_start_matches("www.")
        .to_ascii_lowercase();
    let source_host = source_url
        .and_then(|source| Url::parse(source).ok())
        .and_then(|source| source.host_str().map(str::to_owned))
        .map(|host| host.trim_start_matches("www.").to_ascii_lowercase());
    if source_host
        .as_deref()
        .is_some_and(|source| same_site(&host, source))
    {
        return None;
    }
    let lower_path = url.path().to_ascii_lowercase();
    if ASSET_EXTENSIONS
        .iter()
        .any(|extension| lower_path.ends_with(extension))
        || lower_path
            .trim_matches('/')
            .split('/')
            .any(|segment| JUNK_SEGMENTS.contains(&segment))
    {
        return None;
    }
    let query = url
        .query_pairs()
        .filter(|(key, _)| {
            let key = key.to_ascii_lowercase();
            !key.starts_with("utm_") && !TRACKING_QUERY_NAMES.contains(&key.as_str())
        })
        .fold(
            form_urlencoded::Serializer::new(String::new()),
            |mut serializer, (key, value)| {
                serializer.append_pair(&key, &value);
                serializer
            },
        )
        .finish();
    url.set_query((!query.is_empty()).then_some(&query));
    url.set_fragment(None);
    url.set_scheme("https").ok()?;
    Some(url.to_string())
}

fn same_site(left: &str, right: &str) -> bool {
    left == right || left.ends_with(&format!(".{right}")) || right.ends_with(&format!(".{left}"))
}

const fn link_category(category: RelevantLinkCategory) -> &'static str {
    match category {
        RelevantLinkCategory::PrimarySource => "primary_source",
        RelevantLinkCategory::Research => "research",
        RelevantLinkCategory::Documentation => "documentation",
        RelevantLinkCategory::Tool => "tool",
        RelevantLinkCategory::Dataset => "dataset",
        RelevantLinkCategory::CompanyProduct => "company_product",
        RelevantLinkCategory::RelatedContext => "related_context",
        RelevantLinkCategory::Other => "other",
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn article_body_bound_preserves_three_quarters_head_and_tail() {
        let body = "a".repeat(60_000) + &"z".repeat(10_000);
        let bounded = bound_body(&body);
        assert_eq!(bounded.chars().count(), MAX_NEWS_ARTICLE_BODY_CHARS);
        assert!(bounded.starts_with('a'));
        assert!(bounded.ends_with('z'));
        assert!(bounded.contains("middle of article body omitted"));
    }

    #[test]
    fn link_extraction_filters_same_site_assets_and_tracking() {
        let body = "[source](https://evidence.org/paper?utm_source=x) [self](https://news.example/more) [image](https://cdn.example/a.png)";
        let candidates = extract_link_candidates(body, Some("https://news.example/story"));
        assert_eq!(candidates.len(), 1);
        assert_eq!(candidates[0].url, "https://evidence.org/paper");
    }

    #[test]
    fn relation_query_matches_python_stemming_and_or_shape() {
        assert_eq!(
            relation_search_query(Some("Models launched with agents")),
            Some("agent | launch | model".to_owned())
        );
    }
}
