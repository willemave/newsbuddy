use super::{
    RequestedSource, ScrapeConfigSnapshot, ScrapeRequest, SourcePlan, SourcePlanKind, feed_target,
    reddit_target,
};

pub(super) fn build_source_plans(
    request: &ScrapeRequest,
    prepared: &[ScrapeConfigSnapshot],
) -> Vec<SourcePlan> {
    let mut plans = Vec::new();
    for source in &request.sources {
        match *source {
            RequestedSource::Aggregator(key) => {
                let required_config_ids = Vec::new();
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
    plans
}
