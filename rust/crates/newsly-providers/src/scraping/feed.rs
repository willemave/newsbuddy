use std::collections::{BTreeMap, BTreeSet};

use reqwest::Url;
use serde_json::json;

use crate::feed_validation::entry_audio_url;

use super::{
    FeedScrapeTarget, ScrapeGatewayError, ScrapeProviderOutcome, ScrapedContentItem, ScrapedItem,
    clean, domain_of, entry_html_url, normalize_http_url,
};

mod podcast;

use podcast::{PodcastEntryMetadata, podcast_entry_metadata};

struct FeedMetadata {
    title: Option<String>,
    author: Option<String>,
    description: Option<String>,
}

pub(super) fn normalize_feed_document(
    target: &FeedScrapeTarget,
    bytes: &[u8],
) -> Result<ScrapeProviderOutcome, ScrapeGatewayError> {
    let feed = feed_rs::parser::parse(bytes)
        .map_err(|error| ScrapeGatewayError::Feed(error.to_string()))?;
    let feed_metadata = FeedMetadata {
        title: feed
            .title
            .as_ref()
            .and_then(|title| clean(Some(title.content.clone())))
            .or_else(|| target.display_name.clone()),
        author: feed
            .authors
            .first()
            .and_then(|person| clean(Some(person.name.clone()))),
        description: feed
            .description
            .as_ref()
            .and_then(|value| clean(Some(value.content.clone()))),
    };
    let repeated_entry_urls = repeated_entry_urls(&feed.entries);
    let podcast_metadata = if target.scraper_type == "podcast_rss" {
        podcast_entry_metadata(bytes)
    } else {
        Vec::new()
    };
    let mut items = Vec::new();
    let mut errors = Vec::new();
    let mut seen = BTreeSet::new();
    let empty_podcast_metadata = PodcastEntryMetadata::default();
    for (entry_index, entry) in feed
        .entries
        .iter()
        .take(target.limit.clamp(1, 100))
        .enumerate()
    {
        let podcast = podcast_metadata
            .get(entry_index)
            .unwrap_or(&empty_podcast_metadata);
        match normalize_feed_entry(target, entry, &feed_metadata, &repeated_entry_urls, podcast) {
            Ok(Some(item)) if seen.insert(item.url.clone()) => {
                items.push(ScrapedItem::Content(Box::new(item)));
            }
            Ok(_) => {}
            Err(error) => errors.push(error),
        }
    }
    Ok(ScrapeProviderOutcome::new(items, errors))
}

fn normalize_feed_entry(
    target: &FeedScrapeTarget,
    entry: &feed_rs::model::Entry,
    feed: &FeedMetadata,
    repeated_entry_urls: &BTreeSet<String>,
    podcast: &PodcastEntryMetadata,
) -> Result<Option<ScrapedContentItem>, String> {
    let title = entry
        .title
        .as_ref()
        .and_then(|value| clean(Some(value.content.clone())));
    if should_skip_entry(target, title.as_deref()) {
        return Ok(None);
    }
    let audio_url = entry_audio_url(entry);
    if target.scraper_type == "podcast_rss" && audio_url.is_none() {
        return Err(format!(
            "{}: podcast entry has no usable audio enclosure",
            entry.id
        ));
    }
    let Some(url) = feed_entry_url(target, entry, audio_url.as_deref(), repeated_entry_urls)
        .and_then(|value| normalize_http_url(&value))
    else {
        return Err(format!("{}: feed entry has no usable URL", entry.id));
    };
    let published_at = entry.published.or(entry.updated);
    let author = entry
        .authors
        .first()
        .and_then(|person| clean(Some(person.name.clone())))
        .or_else(|| {
            entry
                .media
                .iter()
                .flat_map(|media| media.credits.iter())
                .find_map(|credit| clean(Some(credit.entity.clone())))
        })
        .or_else(|| feed.author.clone());
    let body = entry
        .content
        .as_ref()
        .and_then(|content| clean_body(content.body.clone()))
        .or_else(|| {
            entry
                .summary
                .as_ref()
                .and_then(|summary| clean_body(Some(summary.content.clone())))
        });
    let platform = feed_platform(target);
    let content_type = if target.scraper_type == "podcast_rss" {
        "podcast"
    } else {
        "article"
    };
    let source = target
        .display_name
        .clone()
        .or_else(|| feed.title.clone())
        .or_else(|| domain_of(&target.feed_url));
    let feed_name = if target.scraper_type == "podcast_rss" {
        source.clone()
    } else {
        feed.title.clone()
    };
    let tags = entry
        .categories
        .iter()
        .map(|category| category.term.clone())
        .collect::<Vec<_>>();
    let word_count = body
        .as_deref()
        .map_or(0, |value| value.split_whitespace().count());
    let metadata = json!({
        "platform": platform,
        "source": source,
        "source_domain": domain_of(&url),
        "feed_url": target.feed_url,
        "feed_config_id": target.config_id,
        "feed_name": feed_name,
        "feed_title": feed.title,
        "feed_description": feed.description,
        "author": author,
        "publication_date": published_at.map(|value| value.to_rfc3339()),
        "rss_content": body.clone(),
        "description": body,
        "word_count": word_count,
        "tags": tags,
        "audio_url": audio_url,
        "entry_id": entry.id,
        "episode_number": podcast.episode_number,
        "duration": podcast.duration_seconds,
        "duration_seconds": podcast.duration_seconds,
    });
    Ok(Some(ScrapedContentItem {
        url: url.clone(),
        source_url: url,
        title,
        content_type: content_type.to_owned(),
        user_id: target.user_id,
        source,
        platform: platform.to_owned(),
        metadata,
        published_at,
        config_id: target.config_id,
    }))
}

fn feed_entry_url(
    target: &FeedScrapeTarget,
    entry: &feed_rs::model::Entry,
    audio_url: Option<&str>,
    repeated_entry_urls: &BTreeSet<String>,
) -> Option<String> {
    if target.scraper_type != "podcast_rss" {
        return entry_html_url(entry);
    }
    entry_html_url(entry)
        .filter(|value| {
            is_specific_entry_url(value)
                && normalize_http_url(value).is_some_and(|url| !repeated_entry_urls.contains(&url))
        })
        .or_else(|| is_specific_entry_url(&entry.id).then(|| entry.id.clone()))
        .or_else(|| audio_url.map(str::to_owned))
}

fn repeated_entry_urls(entries: &[feed_rs::model::Entry]) -> BTreeSet<String> {
    let mut counts = BTreeMap::<String, usize>::new();
    for url in entries
        .iter()
        .filter_map(entry_html_url)
        .filter_map(|value| normalize_http_url(&value))
    {
        *counts.entry(url).or_default() += 1;
    }
    counts
        .into_iter()
        .filter_map(|(url, count)| (count > 1).then_some(url))
        .collect()
}

fn feed_platform(target: &FeedScrapeTarget) -> &'static str {
    match target.scraper_type.as_str() {
        "podcast_rss" => "podcast",
        "substack" => "substack",
        _ => "atom",
    }
}

fn is_specific_entry_url(value: &str) -> bool {
    let Ok(url) = Url::parse(value.trim()) else {
        return false;
    };
    matches!(url.scheme(), "http" | "https")
        && url.host().is_some()
        && (!matches!(url.path(), "" | "/") || url.query().is_some() || url.fragment().is_some())
}

fn is_substack_audio_title(title: &str) -> bool {
    title
        .split(|character: char| !character.is_alphanumeric())
        .any(|word| matches!(word.to_ascii_lowercase().as_str(), "podcast" | "transcript"))
}

fn should_skip_entry(target: &FeedScrapeTarget, title: Option<&str>) -> bool {
    target.scraper_type == "substack" && title.is_some_and(is_substack_audio_title)
}

fn clean_body(value: Option<String>) -> Option<String> {
    value
        .map(|value| value.trim().to_owned())
        .filter(|value| !value.is_empty())
}
