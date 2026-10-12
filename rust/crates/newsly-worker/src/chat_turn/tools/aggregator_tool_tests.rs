use super::*;

#[test]
fn feed_discovery_names_every_catalog_aggregator() {
    let definitions = ChatToolExecutor::definitions();
    let discovery = definitions
        .iter()
        .find(|definition| definition.name == "find_feed_options")
        .unwrap();
    for key in AggregatorKey::ALL {
        assert!(
            discovery.description.contains(key.display_name()),
            "{key:?}"
        );
        assert!(discovery.description.contains(key.as_str()), "{key:?}");
    }
    assert!(
        definitions
            .iter()
            .any(|definition| definition.name == "subscribe_to_aggregator")
    );
}

#[test]
fn aggregator_topics_are_catalog_spelled_and_scoped() {
    let topics = ["CS.cl".to_owned(), " cs.CL ".to_owned(), String::new()];
    assert_eq!(
        canonical_aggregator_topics(AggregatorKey::Arxiv, &topics).unwrap(),
        vec!["cs.CL"]
    );
    assert!(canonical_aggregator_topics(AggregatorKey::Arxiv, &["physics".to_owned()]).is_err());
    assert!(canonical_aggregator_topics(AggregatorKey::HfPapers, &["cs.CL".to_owned()]).is_err());
    assert!(
        canonical_aggregator_topics(AggregatorKey::HfPapers, &[])
            .unwrap()
            .is_empty()
    );
}

#[test]
fn aggregator_cards_subscribe_by_catalog_feed_url() {
    let option = aggregator_feed_option(AggregatorKey::HfPapers);
    assert_eq!(option.feed_type, FeedType::Aggregator);
    assert_eq!(option.feed_url, "aggregator://hfpapers");
    assert_eq!(option.site_url, "https://huggingface.co/papers");
    assert!(option.rationale.is_none());
    assert!(
        aggregator_feed_option(AggregatorKey::Arxiv)
            .rationale
            .unwrap()
            .contains("cs.CL")
    );
}
