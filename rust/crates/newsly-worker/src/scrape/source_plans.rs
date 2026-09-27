use super::{
    AggregatorKey, FeedEntrySelection, FeedScrapeTarget, PreparedScrapeSources, RequestedSource,
    ScrapeConfigSnapshot, ScrapeRequest, SourcePlan, SourcePlanKind, Value, clean_string,
    reddit_target,
};

pub(super) fn build_source_plans(
    request: &ScrapeRequest,
    prepared: &PreparedScrapeSources,
) -> Result<Vec<SourcePlan>, String> {
    let mut plans = Vec::new();
    for source in &request.sources {
        match *source {
            RequestedSource::All => unreachable!("all is expanded while parsing"),
            RequestedSource::Aggregator(key) => {
                let required_config_ids = request
                    .first_edition_run_id
                    .map(|_| {
                        prepared
                            .configs
                            .iter()
                            .filter(|config| {
                                config.scraper_type == "aggregator"
                                    && config
                                        .config
                                        .get("key")
                                        .and_then(Value::as_str)
                                        .is_some_and(|value| {
                                            AggregatorKey::parse(value) == Some(key)
                                        })
                            })
                            .map(|config| config.id)
                            .collect::<Vec<_>>()
                    })
                    .unwrap_or_default();
                if request.first_edition_run_id.is_some() && required_config_ids.is_empty() {
                    return Err(format!(
                        "first-edition run has no active {} source",
                        key.as_str()
                    ));
                }
                plans.push(SourcePlan {
                    source: key.as_str().to_owned(),
                    kind: SourcePlanKind::Aggregator {
                        key,
                        required_config_ids,
                        brutalist_topics: Vec::new(),
                    },
                });
            }
            RequestedSource::Reddit => {
                let targets = prepared
                    .configs
                    .iter()
                    .filter(|config| config.scraper_type == "reddit")
                    .filter_map(reddit_target)
                    .collect::<Vec<_>>();
                plans.push(SourcePlan {
                    source: source.canonical_name(),
                    kind: SourcePlanKind::Reddit(targets),
                });
            }
            RequestedSource::Substack | RequestedSource::Atom | RequestedSource::Podcast => {
                let scraper_type = match source {
                    RequestedSource::Substack => "substack",
                    RequestedSource::Atom => "atom",
                    RequestedSource::Podcast => "podcast_rss",
                    _ => unreachable!(),
                };
                let targets = prepared
                    .configs
                    .iter()
                    .filter(|config| config.scraper_type == scraper_type)
                    .filter_map(feed_target)
                    .collect::<Vec<_>>();
                plans.push(SourcePlan {
                    source: source.canonical_name(),
                    kind: SourcePlanKind::Feed(targets),
                });
            }
            RequestedSource::DiscussionComments => plans.push(SourcePlan {
                source: source.canonical_name(),
                kind: SourcePlanKind::DiscussionComments,
            }),
        }
    }
    Ok(plans)
}

fn feed_target(config: &ScrapeConfigSnapshot) -> Option<FeedScrapeTarget> {
    let feed_url = config
        .feed_url
        .clone()
        .or_else(|| clean_string(config.config.get("feed_url")))
        .or_else(|| clean_string(config.config.get("url")))?;
    Some(FeedScrapeTarget {
        known_urls: std::collections::BTreeSet::new(),
        entry_selection: FeedEntrySelection::StopAtKnown,
        config_id: config.id,
        user_id: config.user_id,
        scraper_type: config.scraper_type.clone(),
        display_name: config
            .display_name
            .clone()
            .or_else(|| clean_string(config.config.get("name"))),
        feed_url,
        limit: config
            .config
            .get("limit")
            .and_then(Value::as_u64)
            .and_then(|value| usize::try_from(value).ok())
            .filter(|value| (1..=100).contains(value))
            .unwrap_or(10),
        fingerprint: config.fingerprint.clone(),
    })
}
