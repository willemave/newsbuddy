use super::{
    DiscussionIdentity, NewsItemSource, Value, clean_string, clean_value, hacker_news_id,
    infer_platform, nested_clean, nonnegative_i32, normalize_http_url, normalize_reddit_url,
    reddit_submission_id,
};

pub(super) fn resolve_identity(news_item: &NewsItemSource) -> Option<DiscussionIdentity> {
    let metadata = news_item.raw_metadata.as_object();
    let mut platform = clean_string(news_item.platform.as_deref())
        .or_else(|| metadata.and_then(|value| clean_value(value.get("platform"))))
        .unwrap_or_default()
        .to_ascii_lowercase();
    if platform == "hn" || platform == "hacker_news" {
        "hackernews".clone_into(&mut platform);
    }
    let mut discussion_url = news_item
        .discussion_url
        .as_deref()
        .and_then(normalize_http_url)
        .or_else(|| {
            news_item
                .canonical_item_url
                .as_deref()
                .and_then(normalize_http_url)
        });
    if platform == "reddit" {
        discussion_url = discussion_url.and_then(|value| normalize_reddit_url(&value));
    }
    if !matches!(platform.as_str(), "hackernews" | "reddit") {
        platform = infer_platform(discussion_url.as_deref())?;
        if platform == "reddit" {
            discussion_url = discussion_url.and_then(|value| normalize_reddit_url(&value));
        }
    }
    let discussion_url = discussion_url?;
    let aggregator = metadata
        .and_then(|value| value.get("aggregator"))
        .and_then(Value::as_object);
    let external_id = clean_string(news_item.source_external_id.as_deref())
        .or_else(|| aggregator.and_then(|value| clean_value(value.get("external_id"))))
        .or_else(|| metadata.and_then(|value| clean_value(value.get("source_external_id"))))
        .or_else(|| match platform.as_str() {
            "hackernews" => hacker_news_id(&discussion_url),
            "reddit" => reddit_submission_id(&discussion_url),
            _ => None,
        })?;
    let title = metadata
        .and_then(|value| nested_clean(value, &["summary", "title"]))
        .or_else(|| metadata.and_then(|value| nested_clean(value, &["article", "title"])))
        .or_else(|| aggregator.and_then(|value| clean_value(value.get("title"))));
    let author = aggregator.and_then(|value| clean_value(value.get("author")));
    let score = metadata
        .and_then(|value| nonnegative_i32(value.get("score")))
        .or_else(|| aggregator.and_then(|value| nonnegative_i32(value.get("score"))));
    let comment_count = metadata
        .and_then(|value| nonnegative_i32(value.get("comment_count")))
        .or_else(|| metadata.and_then(|value| nonnegative_i32(value.get("comments_count"))))
        .or_else(|| {
            aggregator
                .and_then(|value| value.get("metadata"))
                .and_then(Value::as_object)
                .and_then(|value| {
                    nonnegative_i32(value.get("comments_count"))
                        .or_else(|| nonnegative_i32(value.get("comment_count")))
                })
        });
    Some(DiscussionIdentity {
        platform,
        external_id,
        discussion_url,
        title,
        author,
        score,
        comment_count,
    })
}
