use super::{
    AUDIO_PLAN_SYSTEM_PROMPT, ONBOARDING_MODEL, OnboardingGateway, OnboardingLaneTarget,
    onboarding_provider_parameters,
};
use newsly_agent_runtime::{AgentEvent, AgentEventSink, AgentRuntimeError};
use std::sync::Arc;

#[derive(Debug)]
struct TestEvents;

impl AgentEventSink for TestEvents {
    fn publish(&self, _event: AgentEvent) -> Result<(), AgentRuntimeError> {
        Ok(())
    }
}

#[test]
fn onboarding_uses_luna_priority_with_low_reasoning() {
    assert_eq!(ONBOARDING_MODEL, "openai:gpt-6-luna");

    let parameters = onboarding_provider_parameters();
    assert_eq!(parameters["reasoning"]["effort"], "low");
    assert_eq!(parameters["service_tier"], "priority");
    assert_eq!(parameters["store"], false);
}

#[test]
fn audio_plan_prompt_requires_durable_and_meaningfully_diverse_sources() {
    assert!(AUDIO_PLAN_SYSTEM_PROMPT.contains("do not add unsupported niches"));
    assert!(AUDIO_PLAN_SYSTEM_PROMPT.contains("durable, recurring sources"));
    assert!(AUDIO_PLAN_SYSTEM_PROMPT.contains("shows, series, or feeds"));
    assert!(AUDIO_PLAN_SYSTEM_PROMPT.contains("site:reddit.com/r/<community>"));
    assert!(AUDIO_PLAN_SYSTEM_PROMPT.contains("source archetypes and viewpoints"));
    assert!(AUDIO_PLAN_SYSTEM_PROMPT.contains("format differences alone do not count"));
    assert!(AUDIO_PLAN_SYSTEM_PROMPT.contains("names no specific subjects"));
}

#[tokio::test]
#[ignore = "requires OPENAI_API_KEY and performs one live structured-output request"]
async fn live_luna_priority_audio_plan_satisfies_the_product_contract() {
    let gateway = OnboardingGateway::from_env().expect("onboarding gateway must build");
    let (plan, used_fallback, error) = gateway
        .build_audio_plan_with_metadata(
            "I follow AI engineering, startup strategy, and product leadership.",
            Some("en-US"),
            Arc::new(TestEvents),
        )
        .await;

    assert!(!used_fallback, "live Luna request fell back: {error:?}");
    assert!((3..=5).contains(&plan.lanes.len()));
    assert!(
        plan.lanes
            .iter()
            .any(|lane| lane.target == OnboardingLaneTarget::Reddit)
    );
    assert!(
        plan.lanes
            .iter()
            .all(|lane| (2..=4).contains(&lane.queries.len()))
    );
}

#[test]
fn planned_queries_preserve_source_archetypes_language_and_named_communities() {
    let queries = vec![
        "independent climate technology practitioner newsletters in English".to_owned(),
        "academic climate research institutes recurring analysis RSS".to_owned(),
        "institutional climate research journals RSS analysis".to_owned(),
    ];
    assert_eq!(
        super::refine_queries(
            OnboardingLaneTarget::Feeds,
            queries.clone(),
            "Climate",
            &[],
            "Climate"
        ),
        queries
    );
}

#[test]
fn missing_queries_use_show_level_fallback_without_rewriting_existing_query() {
    let queries = super::refine_queries(
        OnboardingLaneTarget::Podcasts,
        vec!["long founder interviews English podcast shows".to_owned()],
        "Founder interviews",
        &["Business".to_owned()],
        "Business",
    );
    assert_eq!(queries[0], "long founder interviews English podcast shows");
    assert_eq!(queries.len(), 3);
    assert!(queries.iter().all(|query| !query.contains("episodes")));
}

#[test]
fn missing_format_cue_is_appended_without_rewriting_planned_interest() {
    let query = "long founder interviews in Norwegian".to_owned();
    assert_eq!(
        super::ensure_query_target(query, OnboardingLaneTarget::Podcasts),
        "long founder interviews in Norwegian podcast shows"
    );
    let reddit = "site:reddit.com/r/F1Technical race engineering discussions".to_owned();
    assert_eq!(
        super::ensure_query_target(reddit.clone(), OnboardingLaneTarget::Reddit),
        reddit
    );
}

#[test]
fn incidental_format_substrings_do_not_hide_missing_source_type() {
    for query in ["anatomy research", "feedback control systems"] {
        assert_eq!(
            super::ensure_query_target(query.to_owned(), OnboardingLaneTarget::Feeds),
            format!("{query} RSS feeds")
        );
    }
}
