use newsly_agent_runtime::AgentRuntimeError;
use newsly_contracts::{
    AssistantFeedOption, AssistantPublishedItem, AssistantSourceActivity, FeedFormat, FeedType,
    SourceActivityBasis,
};
use newsly_domain::AggregatorKey;
use sha1::{Digest, Sha1};

pub(super) fn assistant_feed_option(source: newsly_providers::ResolvedFeed) -> AssistantFeedOption {
    use newsly_providers::{SourceKind, ValidatedFeedFormat};
    let feed_type =
        crate::share_actions::workflows::validated_feed_type(&source.feed, Some(source.kind));
    let feed_url = source.feed.effective_url.clone();
    let site_url = source.site_url;
    let feed_format = match source.feed.format {
        ValidatedFeedFormat::Rss => FeedFormat::Rss,
        ValidatedFeedFormat::Atom => FeedFormat::Atom,
    };
    let title = bounded_text(&source.title, 300)
        .or_else(|| feed_host_label(&site_url))
        .unwrap_or_else(|| feed_url.clone());
    let activity = if source.kind == SourceKind::Podcast {
        source.feed.podcast_publishing_activity
    } else {
        source.feed.publishing_activity
    };
    AssistantFeedOption {
        id: option_id(&feed_url),
        title,
        site_url: site_url.clone(),
        feed_url,
        feed_type,
        feed_format,
        description: None,
        rationale: Some(
            "Resolved and validated from publisher or podcast directory evidence".into(),
        ),
        evidence_url: Some(site_url),
        is_subscribed: false,
        publishing_activity: Some(feed_activity(activity)),
    }
}

fn feed_activity(activity: newsly_providers::FeedPublishingActivity) -> AssistantSourceActivity {
    AssistantSourceActivity {
        basis: SourceActivityBasis::FeedSnapshot,
        checked_at: activity.checked_at,
        window_start: activity.window_start,
        year_window_start: activity.year_window_start,
        window_end: activity.window_end,
        latest_item: activity.latest_item.map(|item| AssistantPublishedItem {
            title: item.title,
            url: item.url,
            published_at: Some(item.published_at),
            collected_at: None,
        }),
        observed_count: activity.observed_count,
        observed_year_count: activity.observed_year_count,
        undated_count: activity.undated_count,
    }
}

pub(super) fn collected_activity(
    activity: newsly_db::aggregator_corpus::AggregatorCollectedActivity,
    checked_at: chrono::DateTime<chrono::Utc>,
) -> AssistantSourceActivity {
    AssistantSourceActivity {
        basis: SourceActivityBasis::NewslyCorpus,
        checked_at,
        window_start: checked_at - chrono::Duration::days(90),
        year_window_start: checked_at - chrono::Duration::days(365),
        window_end: checked_at,
        latest_item: activity
            .latest_collected_at
            .map(|date| AssistantPublishedItem {
                title: activity.latest_title.as_deref().and_then(|title| {
                    let title = title
                        .chars()
                        .filter(|c| !c.is_control())
                        .collect::<String>();
                    bounded_text(&title, 300)
                }),
                url: activity
                    .latest_url
                    .filter(|url| url.chars().count() <= 2_048)
                    .and_then(|url| valid_url(&url).ok()),
                published_at: None,
                collected_at: Some(date.and_utc()),
            }),
        observed_count: activity.observed_count,
        observed_year_count: activity.observed_year_count,
        undated_count: 0,
    }
}

/// A reviewable subscription card for a built-in Newsly aggregator.
pub(super) fn aggregator_feed_option(key: AggregatorKey) -> AssistantFeedOption {
    let feed_url = format!("aggregator://{}", key.as_str());
    let rationale = match key.topics() {
        [] => None,
        topics => Some(format!(
            "Built-in Newsly aggregator. Follows all of {}; ask to narrow it.",
            topics.join(", ")
        )),
    };
    AssistantFeedOption {
        id: option_id(&feed_url),
        title: key.display_name().to_owned(),
        site_url: key.site_url().to_owned(),
        feed_url,
        feed_type: FeedType::Aggregator,
        feed_format: FeedFormat::Rss,
        description: Some(key.description().to_owned()),
        rationale,
        evidence_url: Some(key.site_url().to_owned()),
        is_subscribed: false,
        publishing_activity: None,
    }
}

fn option_id(feed_url: &str) -> String {
    let digest = Sha1::digest(feed_url.as_bytes());
    format!("{digest:x}").chars().take(16).collect()
}

fn bounded_text(value: &str, maximum: usize) -> Option<String> {
    let value = value.trim();
    if value.is_empty() {
        return None;
    }
    let count = value.chars().count();
    if count <= maximum {
        Some(value.to_owned())
    } else if maximum > 3 {
        Some(format!(
            "{}...",
            value
                .chars()
                .take(maximum - 3)
                .collect::<String>()
                .trim_end()
        ))
    } else {
        Some(value.chars().take(maximum).collect())
    }
}

fn feed_host_label(value: &str) -> Option<String> {
    reqwest::Url::parse(value)
        .ok()?
        .host_str()
        .map(|host| host.trim_start_matches("www.").to_owned())
        .filter(|host| !host.is_empty())
}

pub(super) fn bounded_query(value: &str) -> Result<&str, AgentRuntimeError> {
    let value = value.trim();
    if value.is_empty() || value.chars().count() > 2_000 {
        Err(AgentRuntimeError::Tool(
            "query must contain 1-2000 characters".to_owned(),
        ))
    } else {
        Ok(value)
    }
}

pub(super) fn valid_url(value: &str) -> Result<String, AgentRuntimeError> {
    let parsed = newsly_extraction::PublicUrl::parse(value.trim()).map_err(|_| {
        AgentRuntimeError::Tool(
            "URL must be a public HTTP(S) address without credentials".to_owned(),
        )
    })?;
    Ok(parsed.as_str().to_owned())
}

pub(super) fn clean(value: Option<String>) -> Option<String> {
    value.and_then(|value| {
        let value = value.trim();
        (!value.is_empty()).then(|| value.chars().take(500).collect())
    })
}

#[cfg(test)]
mod activity_tests {
    use super::*;

    #[test]
    fn feed_card_activity_preserves_published_evidence() {
        let checked_at = chrono::Utc::now();
        let activity = newsly_providers::FeedPublishingActivity {
            checked_at,
            window_start: checked_at - chrono::Duration::days(90),
            year_window_start: checked_at - chrono::Duration::days(365),
            window_end: checked_at,
            latest_item: Some(newsly_providers::FeedPublishedItem {
                title: Some("Latest episode".to_owned()),
                url: Some("https://example.com/episode".to_owned()),
                published_at: checked_at,
            }),
            observed_count: Some(3),
            observed_year_count: Some(12),
            undated_count: 2,
        };
        let source = newsly_providers::ResolvedFeed {
            title: "Podcast".into(),
            site_url: "https://example.com".into(),
            kind: newsly_providers::SourceKind::Podcast,
            feed: newsly_providers::ValidatedFeed {
                effective_url: "https://example.com/feed".into(),
                format: newsly_providers::ValidatedFeedFormat::Atom,
                has_audio_entries: true,
                publishing_activity: activity.clone(),
                podcast_publishing_activity: activity,
            },
        };
        let card = assistant_feed_option(source);
        assert_eq!(card.feed_format, FeedFormat::Atom);
        assert_eq!(card.feed_type, FeedType::PodcastRss);
        let activity = card.publishing_activity.unwrap();
        assert_eq!(activity.basis, SourceActivityBasis::FeedSnapshot);
        assert_eq!(activity.observed_count, Some(3));
        assert_eq!(activity.observed_year_count, Some(12));
        assert_eq!(activity.undated_count, 2);
        let item = activity.latest_item.unwrap();
        assert_eq!(item.published_at, Some(checked_at));
        assert_eq!(item.collected_at, None);
    }

    #[test]
    fn publication_card_uses_verified_format_and_only_labels_substack_hosts() {
        let now = chrono::Utc::now();
        let activity = newsly_providers::FeedPublishingActivity {
            checked_at: now,
            window_start: now,
            year_window_start: now,
            window_end: now,
            latest_item: None,
            observed_count: None,
            observed_year_count: None,
            undated_count: 0,
        };
        for (url, expected_type) in [
            ("https://publisher.example/feed.atom", FeedType::Atom),
            ("https://writer.substack.com/feed", FeedType::Substack),
            ("https://substack.com.example/feed", FeedType::Atom),
        ] {
            let source = newsly_providers::ResolvedFeed {
                title: "Publication".into(),
                site_url: "https://publisher.example".into(),
                kind: newsly_providers::SourceKind::Publication,
                feed: newsly_providers::ValidatedFeed {
                    effective_url: url.into(),
                    format: newsly_providers::ValidatedFeedFormat::Rss,
                    has_audio_entries: true,
                    publishing_activity: activity.clone(),
                    podcast_publishing_activity: activity.clone(),
                },
            };
            let card = assistant_feed_option(source);
            assert_eq!(card.feed_type, expected_type);
            assert_eq!(card.feed_format, FeedFormat::Rss);
        }
    }

    #[test]
    fn corpus_card_activity_never_calls_collection_a_publication() {
        let now = chrono::Utc::now();
        let activity = collected_activity(
            newsly_db::aggregator_corpus::AggregatorCollectedActivity {
                key: "arxiv".to_owned(),
                observed_count: Some(0),
                observed_year_count: Some(1),
                latest_title: Some("Paper\0title".to_owned()),
                latest_url: Some("javascript:bad".to_owned()),
                latest_collected_at: Some(now.naive_utc()),
            },
            now,
        );
        assert_eq!(activity.basis, SourceActivityBasis::NewslyCorpus);
        assert_eq!(activity.observed_count, Some(0));
        let item = activity.latest_item.unwrap();
        assert_eq!(item.published_at, None);
        assert_eq!(item.collected_at, Some(now));
        assert!(item.url.is_none());
        assert!(!item.title.unwrap().contains('\0'));
    }
}
