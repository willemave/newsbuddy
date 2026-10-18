use super::{completion_tasks, validate_completion_request};
use newsly_contracts::OnboardingCompleteRequest;
use newsly_db::OnboardingCompletionProjection;
use newsly_queue::TaskType;

#[test]
fn completion_never_requeues_onboarding_discovery() {
    let persisted = OnboardingCompletionProjection {
        configured_source_count: 1,
        feed_config_ids: vec![10],
        first_edition_run_id: 20,
        sources_to_scrape: vec!["Reddit".to_owned()],
        generate_image_content_ids: vec![30],
        inbox_count: 100,
        tutorial_complete: false,
        has_feed_discovery_task: false,
    };
    let (requests, _, _) = completion_tasks(7, &persisted);
    assert!(
        requests
            .iter()
            .all(|request| request.task_type != TaskType::OnboardingDiscover)
    );
}

#[test]
fn runless_completion_cannot_name_discovered_suggestions() {
    let request = OnboardingCompleteRequest {
        news_filter_seed: None,
        discovery_run_id: None,
        selected_suggestion_ids: vec![9],
        selected_aggregators: Vec::new(),
        twitter_username: None,
    };
    assert!(validate_completion_request(&request, "request-1").is_err());
}

#[test]
fn onboarding_aggregators_keep_only_catalog_keys_and_their_topics() {
    use newsly_contracts::OnboardingSelectedAggregator;

    let selected = |key: &str, topics: &[&str]| OnboardingSelectedAggregator {
        key: key.to_owned(),
        title: None,
        topics: topics.iter().map(|topic| (*topic).to_owned()).collect(),
    };
    let normalized = super::normalize_aggregators(vec![
        selected(" ArXiv ", &["cs.CL", "physics.optics", "CS.AI", "cs.cl"]),
        selected("hfpapers", &["cs.CL"]),
        selected("unknown", &[]),
    ]);
    let summary = normalized
        .iter()
        .map(|aggregator| (aggregator.key.as_str(), aggregator.topics.clone()))
        .collect::<Vec<_>>();
    assert_eq!(
        summary,
        vec![
            ("arxiv", vec!["cs.AI".to_owned(), "cs.CL".to_owned()]),
            ("hfpapers", Vec::new()),
        ]
    );
}

#[test]
fn completion_seed_requires_personalized_run_and_preserves_disabled_drafts() {
    let mut request = newsly_contracts::OnboardingCompleteRequest {
        news_filter_seed: Some(newsly_contracts::OnboardingNewsFilterSeed {
            text: "AI, never funding".to_owned(),
            enabled: false,
        }),
        ..Default::default()
    };
    assert!(super::validate_completion_request(&request, "seed-test").is_err());
    request.discovery_run_id = Some(1);
    assert!(super::validate_completion_request(&request, "seed-test").is_ok());
    request.news_filter_seed.as_mut().unwrap().text = "界".repeat(3000);
    request.news_filter_seed.as_mut().unwrap().enabled = true;
    // Onboarding retains the whole draft disabled instead of blocking completion.
    assert!(super::validate_completion_request(&request, "seed-test").is_ok());
    request.news_filter_seed.as_mut().unwrap().text = "a".repeat(8001);
    assert!(super::validate_completion_request(&request, "seed-test").is_err());
}
