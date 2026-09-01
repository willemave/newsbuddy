use std::time::Duration;

use chrono::Utc;
use newsly_queue::{OwnedWorkPlan, TaskResult};
use serde_json::{Map, Value};
use url::Url;

use crate::HandlerExecution;

use super::super::model::{
    ContentFinalizationPlan, ContentMutation, ContentSnapshot, FeedCandidate, UsageWrite,
};
use super::super::repository::ContentFinalizer;
use super::super::storage::ContentBodyStoreError;
use super::ContentWorkerServices;

pub(super) fn with_finalizer(
    services: &ContentWorkerServices,
    task_result: TaskResult,
    finalization: ContentFinalizationPlan,
) -> HandlerExecution {
    HandlerExecution::with_finalizer(
        task_result,
        ContentFinalizer::new(services.queue.clone(), finalization),
    )
}

#[allow(clippy::too_many_arguments)]
pub(super) fn extraction_failure(
    services: &ContentWorkerServices,
    plan: &OwnedWorkPlan,
    content_id: i64,
    stage: &'static str,
    reason: String,
    code: String,
    retryable: bool,
    usage: Vec<UsageWrite>,
) -> HandlerExecution {
    let terminal =
        extraction_failure_is_terminal(retryable, plan.retry_count, services.max_retries);
    let task_result = if retryable {
        TaskResult::fail(Some(reason.clone()), true)
    } else {
        // The product row carries the authoritative terminal extraction failure. The delivery
        // task itself completes once that state is durable.
        TaskResult::ok()
    };
    let finalization = ContentFinalizationPlan {
        task_id: plan.task_id,
        content_id,
        mutation: ContentMutation::ExtractionFailure {
            stage,
            reason,
            code,
            terminal,
            scrub_instruction: stage == "analyze_url" && plan.payload.contains_key("instruction"),
        },
        usage,
    };
    with_finalizer(services, task_result, finalization)
}

pub(super) const fn extraction_failure_is_terminal(
    retryable: bool,
    retry_count: i32,
    max_retries: i32,
) -> bool {
    !retryable || retry_count >= max_retries
}

pub(super) fn storage_failure(
    services: &ContentWorkerServices,
    plan: &OwnedWorkPlan,
    content_id: i64,
    stage: &'static str,
    error: &ContentBodyStoreError,
    usage: Vec<UsageWrite>,
) -> HandlerExecution {
    let retryable = matches!(
        error,
        ContentBodyStoreError::CurrentDirectory(_)
            | ContentBodyStoreError::Write(_)
            | ContentBodyStoreError::Read(_)
    );
    extraction_failure(
        services,
        plan,
        content_id,
        stage,
        error.to_string(),
        "content_body_storage".to_owned(),
        retryable,
        usage,
    )
}

pub(super) fn terminal_failure(
    services: &ContentWorkerServices,
    plan: &OwnedWorkPlan,
    content_id: i64,
    stage: &'static str,
    reason: &str,
    code: &str,
    usage: Vec<UsageWrite>,
) -> HandlerExecution {
    extraction_failure(
        services,
        plan,
        content_id,
        stage,
        reason.to_owned(),
        code.to_owned(),
        false,
        usage,
    )
}

pub(super) fn content_id(plan: &OwnedWorkPlan) -> Option<i64> {
    plan.content_id
        .or_else(|| plan.payload.get("content_id").and_then(Value::as_i64))
        .filter(|content_id| *content_id > 0)
}

pub(super) fn request_id(plan: &OwnedWorkPlan) -> String {
    format!(
        "rust-content-task-{}-retry-{}",
        plan.task_id, plan.retry_count
    )
}

pub(super) fn extraction_deadline(timeout: Duration) -> chrono::DateTime<Utc> {
    Utc::now()
        + chrono::Duration::from_std(timeout)
            .expect("validated extraction timeout fits chrono::Duration")
}

pub(super) fn payload_bool(payload: &Map<String, Value>, key: &str) -> bool {
    payload.get(key).and_then(Value::as_bool).unwrap_or(false)
}

pub(super) fn payload_string<'a>(payload: &'a Map<String, Value>, key: &str) -> Option<&'a str> {
    payload
        .get(key)
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())
}

pub(super) fn runtime_value<'a>(metadata: &'a Value, key: &str) -> Option<&'a Value> {
    let object = metadata.as_object()?;
    object
        .get(key)
        .or_else(|| {
            object
                .get("processing")
                .and_then(Value::as_object)
                .and_then(|processing| processing.get(key))
        })
        .or_else(|| {
            object
                .get("domain")
                .and_then(Value::as_object)
                .and_then(|domain| domain.get(key))
        })
}

pub(super) fn runtime_bool(metadata: &Value, key: &str) -> bool {
    runtime_value(metadata, key)
        .and_then(Value::as_bool)
        .unwrap_or(false)
}

pub(super) fn runtime_string(metadata: &Value, key: &str) -> Option<String> {
    runtime_value(metadata, key)
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(str::to_owned)
}

pub(super) fn resolve_article_url(snapshot: &ContentSnapshot) -> String {
    if snapshot.content_type != "news" {
        return normalize_target_url(&snapshot.url);
    }
    if is_http_url(&snapshot.url) {
        return normalize_target_url(&snapshot.url);
    }
    let metadata = &snapshot.content_metadata;
    let article_url = runtime_value(metadata, "article")
        .and_then(Value::as_object)
        .and_then(|article| article.get("url"))
        .and_then(Value::as_str);
    let aggregator_url = runtime_value(metadata, "aggregator")
        .and_then(Value::as_object)
        .and_then(|aggregator| aggregator.get("metadata"))
        .and_then(Value::as_object)
        .and_then(|metadata| metadata.get("hn_linked_url"))
        .and_then(Value::as_str);
    let platform = runtime_string(metadata, "platform")
        .or_else(|| snapshot.platform.clone())
        .unwrap_or_default();
    let candidates = [
        article_url,
        (platform == "hackernews")
            .then_some(aggregator_url)
            .flatten(),
        runtime_value(metadata, "primary_article_url").and_then(Value::as_str),
        runtime_value(metadata, "primary_url").and_then(Value::as_str),
        runtime_value(metadata, "url").and_then(Value::as_str),
    ];
    candidates
        .into_iter()
        .flatten()
        .find(|candidate| is_http_url(candidate))
        .map_or_else(|| snapshot.url.clone(), normalize_target_url)
}

pub(super) fn is_http_url(value: &str) -> bool {
    Url::parse(value)
        .ok()
        .is_some_and(|url| matches!(url.scheme(), "http" | "https") && url.host().is_some())
}

pub(super) fn normalize_target_url(value: &str) -> String {
    let value = value.trim();
    value.strip_prefix("http://").map_or_else(
        || value.to_owned(),
        |remainder| format!("https://{remainder}"),
    )
}

pub(super) fn is_tweet_url(value: &str) -> bool {
    let Ok(url) = Url::parse(value) else {
        return false;
    };
    let host = normalized_host(&url);
    matches!(host.as_str(), "x.com" | "twitter.com")
        && url.path_segments().is_some_and(|segments| {
            segments
                .collect::<Vec<_>>()
                .windows(2)
                .any(|parts| parts[0] == "status")
        })
}

pub(super) struct UrlClassification {
    pub(super) content_type: String,
    pub(super) platform: Option<String>,
    pub(super) metadata_updates: Map<String, Value>,
}

pub(super) fn should_run_structured_analysis(
    snapshot: &ContentSnapshot,
    instruction: Option<&str>,
) -> bool {
    instruction.is_some() || classify_known_url(snapshot).is_none()
}

pub(super) fn classify_known_url(snapshot: &ContentSnapshot) -> Option<UrlClassification> {
    let url = Url::parse(&snapshot.url).ok()?;
    let host = normalized_host(&url);
    let platform_hint = runtime_string(&snapshot.content_metadata, "platform_hint");
    let mut metadata_updates = Map::new();
    let (content_type, platform) = if is_youtube_single_video(&url, &host) {
        metadata_updates.insert("audio_url".to_owned(), Value::String(snapshot.url.clone()));
        metadata_updates.insert("video_url".to_owned(), Value::String(snapshot.url.clone()));
        metadata_updates.insert("youtube_video".to_owned(), Value::Bool(true));
        ("podcast", Some("youtube".to_owned()))
    } else if matches!(host.as_str(), "youtube.com" | "m.youtube.com" | "youtu.be") {
        ("article", Some("youtube".to_owned()))
    } else if matches!(host.as_str(), "podcasts.apple.com" | "music.apple.com") {
        ("podcast", Some("apple_podcasts".to_owned()))
    } else if let Some(platform) = podcast_share_platform(&host) {
        ("article", Some(platform.to_owned()))
    } else {
        return None;
    };
    Some(UrlClassification {
        content_type: content_type.to_owned(),
        platform: platform.or(platform_hint),
        metadata_updates,
    })
}

pub(super) fn normalized_host(url: &Url) -> String {
    url.host_str()
        .unwrap_or_default()
        .trim_start_matches("www.")
        .to_ascii_lowercase()
}

pub(super) fn is_youtube_single_video(url: &Url, host: &str) -> bool {
    let path = url.path().trim_matches('/');
    if host == "youtu.be" {
        return !path.split('/').next().unwrap_or_default().is_empty();
    }
    if !matches!(host, "youtube.com" | "m.youtube.com") {
        return false;
    }
    if url.path().eq_ignore_ascii_case("/watch") {
        return url
            .query_pairs()
            .any(|(key, value)| key == "v" && !value.trim().is_empty());
    }
    ["shorts/", "live/", "embed/", "v/"]
        .iter()
        .any(|prefix| path.to_ascii_lowercase().starts_with(prefix) && path.len() > prefix.len())
}

pub(super) fn podcast_share_platform(host: &str) -> Option<&'static str> {
    match host {
        "open.spotify.com"
        | "spotify.link"
        | "spoti.fi"
        | "on.spotify.com"
        | "open.spotify.link"
        | "podcasters.spotify.com" => Some("spotify"),
        "overcast.fm" => Some("overcast"),
        "pca.st" | "pocketcasts.com" => Some("pocket_casts"),
        "rss.com" => Some("rss"),
        "podcastaddict.com" => Some("podcast_addict"),
        "castbox.fm" => Some("castbox"),
        _ => None,
    }
}

pub(super) fn feed_candidates_from_metadata(metadata: &Value) -> Vec<FeedCandidate> {
    let Some(feed) = runtime_value(metadata, "detected_feed").and_then(Value::as_object) else {
        return Vec::new();
    };
    let Some(url) = feed.get("url").and_then(Value::as_str) else {
        return Vec::new();
    };
    let Some(feed_type) = feed.get("type").and_then(Value::as_str) else {
        return Vec::new();
    };
    if !matches!(feed_type, "substack" | "atom" | "podcast_rss") || !is_http_url(url) {
        return Vec::new();
    }
    vec![FeedCandidate {
        url: url.to_owned(),
        feed_type: feed_type.to_owned(),
        title: feed.get("title").and_then(Value::as_str).map(str::to_owned),
    }]
}
