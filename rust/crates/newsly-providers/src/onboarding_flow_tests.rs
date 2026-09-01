use super::{
    AUDIO_PLAN_SYSTEM_PROMPT, ONBOARDING_MODEL, OnboardingGateway, OnboardingLaneTarget,
    onboarding_provider_parameters,
};

#[test]
fn onboarding_uses_luna_priority_with_low_reasoning() {
    assert_eq!(ONBOARDING_MODEL, "openai:gpt-5.6-luna");

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
}

#[tokio::test]
#[ignore = "requires OPENAI_API_KEY and performs one live structured-output request"]
async fn live_luna_priority_audio_plan_satisfies_the_product_contract() {
    let gateway = OnboardingGateway::from_env().expect("onboarding gateway must build");
    let (plan, used_fallback, error) = gateway
        .build_audio_plan_with_metadata(
            "I follow AI engineering, startup strategy, and product leadership.",
            Some("en-US"),
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
