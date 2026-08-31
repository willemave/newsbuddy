use std::collections::BTreeMap;

use chrono::Utc;
use newsly_providers::XTweet;
use newsly_queue::{OwnedWorkPlan, TaskResult};
use secrecy::ExposeSecret;
use serde_json::{Map, Value};
use url::Url;

use crate::{HandlerExecution, LeaseHealth};

use super::super::model::{
    ContentFinalizationPlan, ContentMutation, ContentSnapshot, UsageWrite, XUsageWrite,
};
use super::ContentWorkerServices;
use super::analysis::normalized_optional;
use super::support::{
    extraction_failure, is_youtube_single_video, normalized_host, podcast_share_platform,
    request_id, runtime_bool, runtime_string, runtime_value, storage_failure, terminal_failure,
    with_finalizer,
};

#[derive(Debug)]
pub(super) struct TweetTargetResolution {
    pub(super) selected_article_url: Option<String>,
    pub(super) resolution_source: &'static str,
    pub(super) resolution_tweet_id: String,
    thread_text: String,
    linked_tweet_ids: Vec<String>,
    thread_lookup_status: &'static str,
    included_tweets: BTreeMap<String, XTweet>,
    usage: Vec<UsageWrite>,
}

#[allow(clippy::too_many_lines)]
pub(super) async fn execute_tweet_analysis(
    services: &ContentWorkerServices,
    plan: &OwnedWorkPlan,
    snapshot: &ContentSnapshot,
    content_id: i64,
    mut lease: LeaseHealth,
) -> HandlerExecution {
    let Some(tweet_id) = extract_tweet_id(&snapshot.url) else {
        return terminal_failure(
            services,
            plan,
            content_id,
            "analyze_url",
            "Tweet URL does not contain a numeric status id",
            "invalid_tweet_url",
            Vec::new(),
        );
    };
    let canonical_url = format!("https://x.com/i/status/{tweet_id}");
    let (tweet, included, lookup_source, mut usage) = if let Some(tweet) =
        hydrate_tweet(&snapshot.content_metadata, &tweet_id)
    {
        (
            tweet,
            hydrate_included_tweets(&snapshot.content_metadata),
            "content_metadata",
            Vec::new(),
        )
    } else {
        let Some(token) = services.x_app_bearer_token.as_ref() else {
            return terminal_failure(
                services,
                plan,
                content_id,
                "analyze_url",
                "X app-authenticated tweet lookup is unavailable. Configure X_APP_BEARER_TOKEN (or TWITTER_AUTH_TOKEN).",
                "x_app_auth_unavailable",
                Vec::new(),
            );
        };
        let lookup = services
            .x_lookup
            .fetch_tweet(token.expose_secret(), &tweet_id);
        tokio::pin!(lookup);
        let result = tokio::select! {
            result = &mut lookup => result,
            () = lease.wait_for_ownership_loss() => {
                return HandlerExecution::from_result(TaskResult::fail(
                    Some("lease ownership was lost during X post lookup".to_owned()),
                    true,
                ));
            }
        };
        match result {
            Ok((tweet, included)) => (
                tweet,
                included,
                "x_api",
                vec![UsageWrite::X(XUsageWrite {
                    request_id: format!("{}:x:post", request_id(plan)),
                    operation: "posts.read",
                    resource_count: 1,
                })],
            ),
            Err(error) => {
                let retryable = matches!(
                    error,
                    newsly_providers::XSyncGatewayError::Transport(_)
                        | newsly_providers::XSyncGatewayError::Provider {
                            status: 429 | 500..=599,
                            ..
                        }
                );
                return extraction_failure(
                    services,
                    plan,
                    content_id,
                    "analyze_url",
                    format!("X tweet lookup failed: {error}"),
                    "x_tweet_lookup".to_owned(),
                    retryable,
                    Vec::new(),
                );
            }
        }
    };
    let Some(token) = services.x_app_bearer_token.as_ref() else {
        let resolution = resolve_tweet_without_lookups(&tweet, included);
        return finalize_tweet_resolution(
            services,
            plan,
            snapshot,
            content_id,
            tweet,
            lookup_source,
            canonical_url,
            resolution,
            usage,
        )
        .await;
    };
    let mut resolution = resolve_tweet_target(
        services,
        &tweet,
        included,
        token.expose_secret(),
        &request_id(plan),
    )
    .await;
    usage.append(&mut resolution.usage);
    if lease.ownership_lost() {
        return HandlerExecution::from_result(TaskResult::fail(
            Some("lease ownership was lost during X thread resolution".to_owned()),
            true,
        ));
    }
    finalize_tweet_resolution(
        services,
        plan,
        snapshot,
        content_id,
        tweet,
        lookup_source,
        canonical_url,
        resolution,
        usage,
    )
    .await
}

#[allow(clippy::too_many_arguments, clippy::too_many_lines)]
async fn finalize_tweet_resolution(
    services: &ContentWorkerServices,
    plan: &OwnedWorkPlan,
    snapshot: &ContentSnapshot,
    content_id: i64,
    tweet: XTweet,
    lookup_source: &str,
    canonical_url: String,
    resolution: TweetTargetResolution,
    usage: Vec<UsageWrite>,
) -> HandlerExecution {
    let processing_text = tweet_processing_text(&tweet);
    let target_url = resolution
        .selected_article_url
        .clone()
        .unwrap_or_else(|| canonical_url.clone());
    let (content_type, platform) =
        tweet_target_type(&target_url, resolution.selected_article_url.is_some());
    let mut metadata_updates = Map::new();
    let values = [
        ("platform", Value::String("twitter".to_owned())),
        ("discussion_url", Value::String(canonical_url.clone())),
        ("tweet_id", Value::String(tweet.id.clone())),
        ("tweet_url", Value::String(canonical_url)),
        ("tweet_text", Value::String(tweet.text.clone())),
        (
            "tweet_thread_text",
            Value::String(resolution.thread_text.clone()),
        ),
        (
            "tweet_processing_text",
            Value::String(processing_text.clone()),
        ),
        ("tweet_external_urls", strings_json(&tweet.external_urls)),
        (
            "tweet_linked_tweet_ids",
            strings_json(&resolution.linked_tweet_ids),
        ),
        (
            "tweet_referenced_tweet_types",
            strings_json(&tweet.referenced_tweet_types),
        ),
        ("has_video", Value::Bool(tweet.has_video)),
        (
            "tweet_resolution_source",
            Value::String(resolution.resolution_source.to_owned()),
        ),
        (
            "tweet_resolution_tweet_id",
            Value::String(resolution.resolution_tweet_id),
        ),
        (
            "tweet_thread_lookup_status",
            Value::String(resolution.thread_lookup_status.to_owned()),
        ),
        (
            "tweet_lookup_source",
            Value::String(lookup_source.to_owned()),
        ),
        (
            "tweet_snapshot",
            serde_json::to_value(&tweet).unwrap_or(Value::Null),
        ),
        (
            "tweet_snapshot_included",
            serde_json::to_value(&resolution.included_tweets).unwrap_or(Value::Object(Map::new())),
        ),
    ];
    metadata_updates.extend(
        values
            .into_iter()
            .map(|(key, value)| (key.to_owned(), value)),
    );
    insert_optional_string(
        &mut metadata_updates,
        "tweet_author",
        tweet.author_name.as_deref(),
    );
    insert_optional_string(
        &mut metadata_updates,
        "tweet_author_id",
        tweet.author_id.as_deref(),
    );
    insert_optional_string(
        &mut metadata_updates,
        "tweet_author_username",
        tweet.author_username.as_deref(),
    );
    insert_optional_string(
        &mut metadata_updates,
        "tweet_created_at",
        tweet.created_at.as_deref(),
    );
    insert_optional_string(
        &mut metadata_updates,
        "tweet_conversation_id",
        tweet.conversation_id.as_deref(),
    );
    insert_optional_i64(&mut metadata_updates, "tweet_like_count", tweet.like_count);
    insert_optional_i64(
        &mut metadata_updates,
        "tweet_retweet_count",
        tweet.retweet_count,
    );
    insert_optional_i64(
        &mut metadata_updates,
        "tweet_reply_count",
        tweet.reply_count,
    );
    insert_optional_i64(
        &mut metadata_updates,
        "video_duration_ms",
        tweet.video_duration_ms,
    );
    insert_optional_string(
        &mut metadata_updates,
        "tweet_article_title",
        tweet.article_title.as_deref(),
    );
    insert_optional_string(
        &mut metadata_updates,
        "tweet_article_text",
        tweet.article_text.as_deref(),
    );
    insert_optional_string(
        &mut metadata_updates,
        "tweet_note_tweet_text",
        tweet.note_tweet_text.as_deref(),
    );
    if resolution.selected_article_url.is_none()
        && tweet.article_text.is_none()
        && tweet.note_tweet_text.is_none()
    {
        metadata_updates.insert("tweet_only".to_owned(), Value::Bool(true));
    }

    let body_text = resolution
        .selected_article_url
        .is_none()
        .then_some(resolution.thread_text)
        .filter(|value| !value.trim().is_empty());
    let (body, body_char_count) = if let Some(text) = body_text {
        match services.body_store.stage_source(content_id, &text).await {
            Ok(body) => {
                let count = text.chars().count();
                (Some(body), count)
            }
            Err(error) => {
                return storage_failure(services, plan, content_id, "analyze_url", &error, usage);
            }
        }
    } else {
        (None, 0)
    };
    let finalization = ContentFinalizationPlan {
        task_id: plan.task_id,
        content_id,
        mutation: ContentMutation::AnalyzeTweet {
            target_url,
            content_type,
            platform,
            title: tweet
                .article_title
                .clone()
                .or_else(|| snapshot.title.clone()),
            metadata_updates,
            body,
            body_char_count,
            scrub_instruction: plan.payload.contains_key("instruction"),
        },
        usage,
    };
    with_finalizer(services, TaskResult::ok(), finalization)
}

fn resolve_tweet_without_lookups(
    tweet: &XTweet,
    included: BTreeMap<String, XTweet>,
) -> TweetTargetResolution {
    resolve_from_known_tweets(tweet, included).unwrap_or_else(|| TweetTargetResolution {
        selected_article_url: None,
        resolution_source: "tweet_only",
        resolution_tweet_id: tweet.id.clone(),
        thread_text: tweet_processing_text(tweet),
        linked_tweet_ids: tweet.linked_tweet_ids.clone(),
        thread_lookup_status: "not_attempted",
        included_tweets: BTreeMap::new(),
        usage: Vec::new(),
    })
}

#[allow(clippy::too_many_lines)]
async fn resolve_tweet_target(
    services: &ContentWorkerServices,
    root: &XTweet,
    mut included: BTreeMap<String, XTweet>,
    access_token: &str,
    request_id: &str,
) -> TweetTargetResolution {
    if let Some(resolution) = resolve_from_known_tweets(root, included.clone()) {
        return resolution;
    }
    let mut usage = Vec::new();
    let missing_ids = root
        .linked_tweet_ids
        .iter()
        .filter(|id| !included.contains_key(*id))
        .cloned()
        .collect::<Vec<_>>();
    if !missing_ids.is_empty() {
        match services
            .x_lookup
            .fetch_tweets(access_token, &missing_ids)
            .await
        {
            Ok(page) => {
                usage.push(UsageWrite::X(XUsageWrite {
                    request_id: format!("{request_id}:x:linked"),
                    operation: "posts.read",
                    resource_count: page.tweets.len(),
                }));
                included.extend(page.included_tweets);
                included.extend(
                    page.tweets
                        .into_iter()
                        .map(|tweet| (tweet.id.clone(), tweet)),
                );
                if let Some(mut resolution) = resolve_from_known_tweets(root, included.clone()) {
                    resolution.usage = usage;
                    return resolution;
                }
            }
            Err(error) => {
                tracing::warn!(tweet_id = %root.id, error = %error, "linked X post lookup failed");
            }
        }
    }
    if !should_attempt_thread_lookup(root) {
        return TweetTargetResolution {
            selected_article_url: None,
            resolution_source: "tweet_only",
            resolution_tweet_id: root.id.clone(),
            thread_text: tweet_processing_text(root),
            linked_tweet_ids: root.linked_tweet_ids.clone(),
            thread_lookup_status: "not_attempted",
            included_tweets: included,
            usage,
        };
    }

    let mut collected = vec![root.clone()];
    if recent_search_eligible(root)
        && let (Some(conversation_id), Some(username)) = (
            root.conversation_id.as_deref(),
            root.author_username.as_deref(),
        )
    {
        let query = format!("conversation_id:{conversation_id} from:{username}");
        match services
            .x_lookup
            .search_recent(access_token, &query, 100)
            .await
        {
            Ok(page) => {
                usage.push(UsageWrite::X(XUsageWrite {
                    request_id: format!("{request_id}:x:thread-search"),
                    operation: "posts.search_recent",
                    resource_count: page.tweets.len(),
                }));
                collected.extend(page.tweets);
                if let Some((url, tweet_id, thread)) = first_thread_target(root, &collected) {
                    return TweetTargetResolution {
                        selected_article_url: Some(url),
                        resolution_source: "thread_reply",
                        resolution_tweet_id: tweet_id,
                        thread_text: thread_text(&thread),
                        linked_tweet_ids: root.linked_tweet_ids.clone(),
                        thread_lookup_status: "found",
                        included_tweets: included,
                        usage,
                    };
                }
            }
            Err(error) => {
                tracing::warn!(tweet_id = %root.id, error = %error, "recent X thread search failed");
            }
        }
    }
    let Some(author_id) = root.author_id.as_deref() else {
        return unresolved_thread(root, included, &collected, usage, "unavailable");
    };
    let mut token = None;
    let mut scanned = collected.iter().filter(|tweet| tweet.id != root.id).count();
    for page_index in 0..10 {
        let remaining = 1_000usize.saturating_sub(scanned);
        if remaining < 5 {
            break;
        }
        let page_size = u8::try_from(remaining.min(100)).unwrap_or(100);
        match services
            .x_lookup
            .fetch_user_tweets(access_token, author_id, token.as_deref(), page_size)
            .await
        {
            Ok(page) => {
                scanned = scanned.saturating_add(page.tweets.len());
                usage.push(UsageWrite::X(XUsageWrite {
                    request_id: format!("{request_id}:x:user-page-{page_index}"),
                    operation: "users.posts.read",
                    resource_count: page.tweets.len(),
                }));
                collected.extend(page.tweets);
                if let Some((url, tweet_id, thread)) = first_thread_target(root, &collected) {
                    return TweetTargetResolution {
                        selected_article_url: Some(url),
                        resolution_source: "thread_reply",
                        resolution_tweet_id: tweet_id,
                        thread_text: thread_text(&thread),
                        linked_tweet_ids: root.linked_tweet_ids.clone(),
                        thread_lookup_status: "found",
                        included_tweets: included,
                        usage,
                    };
                }
                token = page.next_token;
                if token.is_none() {
                    return unresolved_thread(root, included, &collected, usage, "not_found");
                }
                if scanned >= 1_000 {
                    break;
                }
            }
            Err(error) => {
                tracing::warn!(tweet_id = %root.id, error = %error, "X user thread lookup failed");
                return unresolved_thread(root, included, &collected, usage, "unavailable");
            }
        }
    }
    unresolved_thread(root, included, &collected, usage, "capped")
}

pub(super) fn resolve_from_known_tweets(
    root: &XTweet,
    included: BTreeMap<String, XTweet>,
) -> Option<TweetTargetResolution> {
    if let Some(url) = root
        .external_urls
        .iter()
        .find_map(|url| normalized_external_url(url))
    {
        return Some(known_tweet_resolution(
            root,
            included,
            url,
            "root_tweet",
            &root.id,
        ));
    }
    if root.article_text.is_some() || root.note_tweet_text.is_some() {
        return Some(TweetTargetResolution {
            selected_article_url: None,
            resolution_source: "root_tweet",
            resolution_tweet_id: root.id.clone(),
            thread_text: tweet_processing_text(root),
            linked_tweet_ids: root.linked_tweet_ids.clone(),
            thread_lookup_status: "not_needed",
            included_tweets: included,
            usage: Vec::new(),
        });
    }
    for id in &root.linked_tweet_ids {
        if let Some(linked) = included.get(id)
            && let Some(url) = linked
                .external_urls
                .iter()
                .find_map(|url| normalized_external_url(url))
        {
            let resolution_tweet_id = id.clone();
            return Some(known_tweet_resolution(
                root,
                included,
                url,
                "linked_tweet",
                &resolution_tweet_id,
            ));
        }
    }
    None
}

fn known_tweet_resolution(
    root: &XTweet,
    included: BTreeMap<String, XTweet>,
    url: String,
    source: &'static str,
    resolution_tweet_id: &str,
) -> TweetTargetResolution {
    TweetTargetResolution {
        selected_article_url: Some(url),
        resolution_source: source,
        resolution_tweet_id: resolution_tweet_id.to_owned(),
        thread_text: tweet_processing_text(root),
        linked_tweet_ids: root.linked_tweet_ids.clone(),
        thread_lookup_status: "not_needed",
        included_tweets: included,
        usage: Vec::new(),
    }
}

fn unresolved_thread(
    root: &XTweet,
    included: BTreeMap<String, XTweet>,
    tweets: &[XTweet],
    usage: Vec<UsageWrite>,
    status: &'static str,
) -> TweetTargetResolution {
    let thread = same_author_thread(root, tweets);
    TweetTargetResolution {
        selected_article_url: None,
        resolution_source: "tweet_only",
        resolution_tweet_id: root.id.clone(),
        thread_text: thread_text(&thread),
        linked_tweet_ids: root.linked_tweet_ids.clone(),
        thread_lookup_status: status,
        included_tweets: included,
        usage,
    }
}

fn first_thread_target(root: &XTweet, tweets: &[XTweet]) -> Option<(String, String, Vec<XTweet>)> {
    let thread = same_author_thread(root, tweets);
    for tweet in &thread {
        if let Some(url) = tweet
            .external_urls
            .iter()
            .find_map(|url| normalized_external_url(url))
        {
            return Some((url, tweet.id.clone(), thread));
        }
    }
    None
}

fn same_author_thread(root: &XTweet, tweets: &[XTweet]) -> Vec<XTweet> {
    let mut by_id = BTreeMap::new();
    by_id.insert(root.id.clone(), root.clone());
    for tweet in tweets {
        if let (Some(root_author_id), Some(author_id)) =
            (root.author_id.as_deref(), tweet.author_id.as_deref())
            && author_id != root_author_id
        {
            continue;
        }
        if tweet.conversation_id != root.conversation_id {
            continue;
        }
        by_id.insert(tweet.id.clone(), tweet.clone());
    }
    let mut values = by_id.into_values().collect::<Vec<_>>();
    values.sort_by(|left, right| {
        parsed_timestamp(left.created_at.as_deref())
            .cmp(&parsed_timestamp(right.created_at.as_deref()))
            .then_with(|| left.id.cmp(&right.id))
    });
    values
}

fn parsed_timestamp(value: Option<&str>) -> Option<chrono::DateTime<Utc>> {
    value
        .and_then(|value| chrono::DateTime::parse_from_rfc3339(value).ok())
        .map(|value| value.with_timezone(&Utc))
}

fn recent_search_eligible(tweet: &XTweet) -> bool {
    tweet.author_username.is_some()
        && parsed_timestamp(tweet.created_at.as_deref())
            .is_some_and(|created| created >= Utc::now() - chrono::Duration::days(7))
}

fn should_attempt_thread_lookup(tweet: &XTweet) -> bool {
    if tweet.author_id.is_none()
        || tweet.conversation_id.is_none()
        || tweet.article_text.is_some()
        || tweet.note_tweet_text.is_some()
        || !tweet.external_urls.is_empty()
    {
        return false;
    }
    let lowered = tweet.text.to_ascii_lowercase();
    ["1/", "thread", "🧵", "part 1", "1 of"]
        .iter()
        .any(|marker| lowered.contains(marker))
        || tweet.reply_count.unwrap_or_default() >= 3
}

fn tweet_processing_text(tweet: &XTweet) -> String {
    if let Some(article) = normalized_optional(tweet.article_text.as_deref()) {
        if let Some(title) = normalized_optional(tweet.article_title.as_deref())
            && article != title
            && !article.starts_with(&title)
        {
            return format!("{title}\n\n{article}");
        }
        return article;
    }
    normalized_optional(tweet.note_tweet_text.as_deref())
        .unwrap_or_else(|| tweet.text.trim().to_owned())
}

fn thread_text(tweets: &[XTweet]) -> String {
    tweets
        .iter()
        .map(tweet_processing_text)
        .filter(|text| !text.is_empty())
        .collect::<Vec<_>>()
        .join("\n\n")
}

fn hydrate_tweet(metadata: &Value, expected_id: &str) -> Option<XTweet> {
    if let Some(value) = runtime_value(metadata, "tweet_snapshot")
        && let Ok(tweet) = serde_json::from_value::<XTweet>(value.clone())
        && tweet.id == expected_id
    {
        return Some(tweet);
    }
    let id = runtime_string(metadata, "tweet_id").unwrap_or_else(|| expected_id.to_owned());
    if id != expected_id {
        return None;
    }
    let text = runtime_string(metadata, "tweet_text")
        .or_else(|| runtime_string(metadata, "tweet_note_tweet_text"))
        .or_else(|| runtime_string(metadata, "tweet_article_title"))
        .or_else(|| runtime_string(metadata, "tweet_article_text"))?;
    Some(XTweet {
        id,
        text,
        author_id: runtime_string(metadata, "tweet_author_id"),
        author_username: runtime_string(metadata, "tweet_author_username"),
        author_name: runtime_string(metadata, "tweet_author"),
        created_at: runtime_string(metadata, "tweet_created_at"),
        like_count: runtime_i64(metadata, "tweet_like_count"),
        retweet_count: runtime_i64(metadata, "tweet_retweet_count"),
        reply_count: runtime_i64(metadata, "tweet_reply_count"),
        conversation_id: runtime_string(metadata, "tweet_conversation_id"),
        in_reply_to_user_id: None,
        referenced_tweet_types: runtime_strings(metadata, "tweet_referenced_tweet_types"),
        article_title: runtime_string(metadata, "tweet_article_title"),
        article_text: runtime_string(metadata, "tweet_article_text"),
        note_tweet_text: runtime_string(metadata, "tweet_note_tweet_text"),
        external_urls: runtime_strings(metadata, "tweet_external_urls"),
        linked_tweet_ids: runtime_strings(metadata, "tweet_linked_tweet_ids"),
        has_video: runtime_bool(metadata, "has_video"),
        video_duration_ms: runtime_i64(metadata, "video_duration_ms"),
    })
}

fn hydrate_included_tweets(metadata: &Value) -> BTreeMap<String, XTweet> {
    runtime_value(metadata, "tweet_snapshot_included")
        .and_then(Value::as_object)
        .into_iter()
        .flat_map(|entries| entries.iter())
        .filter_map(|(id, value)| {
            serde_json::from_value::<XTweet>(value.clone())
                .ok()
                .filter(|tweet| tweet.id == *id)
                .map(|tweet| (id.clone(), tweet))
        })
        .collect()
}

fn runtime_i64(metadata: &Value, key: &str) -> Option<i64> {
    runtime_value(metadata, key).and_then(Value::as_i64)
}

fn runtime_strings(metadata: &Value, key: &str) -> Vec<String> {
    runtime_value(metadata, key)
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(str::to_owned)
        .collect()
}

pub(super) fn extract_tweet_id(value: &str) -> Option<String> {
    let url = Url::parse(value).ok()?;
    let host = normalized_host(&url);
    if !matches!(host.as_str(), "x.com" | "twitter.com") {
        return None;
    }
    let segments = url.path_segments()?.collect::<Vec<_>>();
    segments.windows(2).find_map(|parts| {
        (parts[0] == "status"
            && !parts[1].is_empty()
            && parts[1].bytes().all(|byte| byte.is_ascii_digit()))
        .then(|| parts[1].to_owned())
    })
}

pub(super) fn normalized_external_url(value: &str) -> Option<String> {
    let mut url = Url::parse(value.trim()).ok()?;
    if !matches!(url.scheme(), "http" | "https") || url.host().is_none() {
        return None;
    }
    if url.scheme() == "http" && url.set_scheme("https").is_err() {
        return None;
    }
    url.set_fragment(None);
    Some(url.to_string())
}

fn tweet_target_type(url: &str, has_external_target: bool) -> (String, String) {
    if !has_external_target {
        return ("article".to_owned(), "twitter".to_owned());
    }
    let Some(parsed) = Url::parse(url).ok() else {
        return ("article".to_owned(), "twitter".to_owned());
    };
    let host = normalized_host(&parsed);
    if is_youtube_single_video(&parsed, &host) {
        return ("podcast".to_owned(), "youtube".to_owned());
    }
    if host == "podcasts.apple.com" || host == "music.apple.com" {
        return ("podcast".to_owned(), "apple_podcasts".to_owned());
    }
    if let Some(platform) = podcast_share_platform(&host) {
        return ("podcast".to_owned(), platform.to_owned());
    }
    ("article".to_owned(), "twitter".to_owned())
}

fn strings_json(values: &[String]) -> Value {
    Value::Array(values.iter().cloned().map(Value::String).collect())
}

fn insert_optional_string(map: &mut Map<String, Value>, key: &str, value: Option<&str>) {
    if let Some(value) = normalized_optional(value) {
        map.insert(key.to_owned(), Value::String(value));
    }
}

fn insert_optional_i64(map: &mut Map<String, Value>, key: &str, value: Option<i64>) {
    if let Some(value) = value {
        map.insert(key.to_owned(), Value::from(value));
    }
}
