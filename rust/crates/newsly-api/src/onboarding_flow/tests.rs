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
        discovery_run_id: None,
        selected_suggestion_ids: vec![9],
        selected_aggregators: Vec::new(),
        twitter_username: None,
    };
    assert!(validate_completion_request(&request, "request-1").is_err());
}
