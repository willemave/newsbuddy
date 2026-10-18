use super::*;
use newsly_agent_runtime::{AgentRuntimeError, ProviderUsage};
use std::cell::Cell;

fn observed(tokens: u64) -> ProviderUsage {
    ProviderUsage {
        request_count: 1,
        input_tokens: tokens,
        ..Default::default()
    }
}

fn successful(id: i64, tokens: u64) -> newsly_providers::GeneratedBriefingLensName {
    let mut name = generated_name(id);
    name.usage = observed(tokens);
    name.provider_response_id = Some(format!("response-{id}"));
    name
}

fn observed_error(tokens: u64) -> BriefingCompositionGatewayError {
    BriefingCompositionGatewayError::ObservedNaming {
        reason: "invalid membership".into(),
        usage: observed(tokens),
    }
}

#[tokio::test]
async fn a_recovered_naming_retry_retains_observed_tokens_once() {
    for has_observation in [true, false] {
        let calls = Cell::new(0);
        let name = naming::name_with_retries(
            4,
            "openai:test",
            || true,
            || {
                let attempt = calls.get();
                calls.set(attempt + 1);
                async move {
                    if attempt == 0 {
                        Err(BriefingCompositionGatewayError::ObservedAgent {
                            source: AgentRuntimeError::DeadlineExceeded,
                            usage: has_observation.then(|| observed(10)),
                        })
                    } else {
                        Ok(successful(1, 20))
                    }
                }
            },
        )
        .await
        .unwrap();
        assert_eq!(calls.get(), 2);
        assert_eq!(
            name.usage.request_count,
            if has_observation { 2 } else { 1 }
        );
        assert_eq!(
            name.usage.input_tokens,
            if has_observation { 30 } else { 20 }
        );
        assert_eq!(name.provider_response_id.as_deref(), Some("response-1"));
    }
}

#[tokio::test]
async fn all_failed_attempts_keep_only_actual_observations() {
    for first_observed in [true, false] {
        let calls = Cell::new(0);
        let error = naming::name_with_retries(
            4,
            "openai:test",
            || true,
            || {
                let attempt = calls.get();
                calls.set(attempt + 1);
                async move {
                    if attempt == 0 && !first_observed {
                        Err(BriefingCompositionGatewayError::MissingStructuredOutput)
                    } else {
                        Err(observed_error(if attempt == 0 { 10 } else { 20 }))
                    }
                }
            },
        )
        .await
        .unwrap_err();
        assert_eq!(calls.get(), 2);
        assert_eq!(
            error.observed_usage().len(),
            if first_observed { 2 } else { 1 }
        );
        assert_eq!(
            error
                .observed_usage()
                .iter()
                .map(|row| row.usage.input_tokens)
                .sum::<u64>(),
            if first_observed { 30 } else { 20 }
        );
        assert!(
            error
                .observed_usage()
                .iter()
                .all(|row| row.provider == "openai"
                    && row.model == "test"
                    && row.provider_response_id.is_none())
        );
    }
}

#[tokio::test]
async fn failed_parallel_batch_keeps_prior_successes_and_every_finished_sibling_once() {
    let clusters = (1..=9).map(singleton_cluster).collect::<Vec<_>>();
    let calls = Cell::new(0);
    let result = name_clusters(&clusters, 9, 4, |candidates| {
        calls.set(calls.get() + 1);
        let id = candidates[0].pending.source_id;
        async move {
            if [6, 8].contains(&id) {
                let tokens = if id == 6 { 10 } else { 20 };
                let mut usage = naming_usage(&successful(id, tokens), "openai:test");
                usage.provider_response_id = None;
                Err(SemanticLensPlanningError::Provider(observed_error(tokens))
                    .with_usage(vec![usage]))
            } else {
                Ok(successful(id, u64::try_from(id).unwrap()))
            }
        }
    })
    .await
    .unwrap();
    assert_eq!(calls.get(), 8, "no calls after a failed batch");
    assert_eq!(result.names.iter().flatten().count(), 6);
    assert!(result.names[5].is_none());
    assert!(result.names[7].is_none());
    let mut rows = result.failed_usage;
    rows.extend(
        result
            .names
            .iter()
            .flatten()
            .map(|name| naming_usage(name, "openai:test")),
    );
    assert_eq!(rows.len(), 8);
    assert_eq!(
        rows.iter().map(|row| row.usage.request_count).sum::<u64>(),
        8
    );
    assert_eq!(
        rows.iter().map(|row| row.usage.input_tokens).sum::<u64>(),
        52
    );
    assert_eq!(
        rows.iter()
            .filter(|row| row.provider_response_id.is_some())
            .count(),
        6
    );
}

#[test]
fn attaching_prior_embedding_usage_preserves_distinct_models_without_nested_errors() {
    let naming = naming_usage(&successful(1, 10), "openai:test");
    let embedding = BriefingLensAssignmentUsage {
        provider: "openrouter".into(),
        model: "qwen/embedding".into(),
        provider_response_id: Some("embedding-response".into()),
        usage: observed(25),
        feature: "briefing_category_assignment".into(),
        operation: "briefing.embed_categories".into(),
    };
    let error = SemanticLensPlanningError::Provider(observed_error(10))
        .with_usage(vec![naming])
        .with_usage(vec![embedding]);
    assert_eq!(error.observed_usage().len(), 2);
    assert_eq!(error.observed_usage()[0].provider, "openrouter");
    assert_eq!(error.observed_usage()[1].provider, "openai");
    assert!(matches!(
        error.cause(),
        SemanticLensPlanningError::Provider(_)
    ));
}

#[tokio::test]
async fn ownership_loss_stops_initial_dispatch_and_retries_but_retains_usage() {
    for initially_owned in [false, true] {
        let owned = Cell::new(initially_owned);
        let calls = Cell::new(0);
        let error = naming::name_with_retries(
            4,
            "openai:test",
            || owned.get(),
            || {
                calls.set(calls.get() + 1);
                owned.set(false);
                async { Err(observed_error(17)) }
            },
        )
        .await
        .unwrap_err();
        assert!(matches!(
            error.cause(),
            SemanticLensPlanningError::OwnershipLost
        ));
        assert_eq!(calls.get(), usize::from(initially_owned));
        assert_eq!(error.observed_usage().len(), usize::from(initially_owned));
        if initially_owned {
            assert_eq!(error.observed_usage()[0].usage.input_tokens, 17);
        }
    }
}

#[tokio::test]
async fn ownership_loss_drains_finished_siblings_and_prevents_later_batches() {
    let clusters = (1..=9).map(singleton_cluster).collect::<Vec<_>>();
    let owned = Cell::new(true);
    let calls = Cell::new(0);
    let first_finished = tokio::sync::Notify::new();
    let result = name_clusters(&clusters, 9, 4, |candidates| {
        let id = candidates[0].pending.source_id;
        let owned = &owned;
        let calls = &calls;
        let first_finished = &first_finished;
        async move {
            naming::name_with_retries(
                1,
                "openai:test",
                || owned.get(),
                || {
                    calls.set(calls.get() + 1);
                    async move {
                        if id == 1 {
                            first_finished.notify_one();
                            Ok(successful(id, 11))
                        } else {
                            first_finished.notified().await;
                            owned.set(false);
                            Err(observed_error(19))
                        }
                    }
                },
            )
            .await
        }
    })
    .await
    .unwrap();
    assert_eq!(calls.get(), 2);
    assert_eq!(result.names.iter().flatten().count(), 1);
    assert_eq!(result.names[0].as_ref().unwrap().usage.input_tokens, 11);
    assert_eq!(result.failed_usage.len(), 1);
    assert_eq!(result.failed_usage[0].usage.input_tokens, 19);
}
