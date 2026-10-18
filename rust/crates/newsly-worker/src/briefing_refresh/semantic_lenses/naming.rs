//! Bounded naming attempts and complete usage retention on planning failure.
use super::{
    BriefingCompositionGateway, BriefingCompositionGatewayError, BriefingLensAssignmentUsage,
    Candidate, Cluster, LENS_NAMING_ATTEMPTS, LENS_NAMING_PARALLELISM, SemanticLensPlanningError,
    composition_source, nonempty, split_model_spec,
};
use futures_util::future::join_all;
use newsly_agent_runtime::ProviderUsage;
use newsly_providers::GeneratedBriefingLensName;

pub(super) fn naming_usage(
    name: &GeneratedBriefingLensName,
    model_spec: &str,
) -> BriefingLensAssignmentUsage {
    let (provider, fallback_model) = split_model_spec(model_spec);
    BriefingLensAssignmentUsage {
        provider: provider.to_owned(),
        model: nonempty(&name.model).unwrap_or(fallback_model).to_owned(),
        provider_response_id: name.provider_response_id.clone(),
        usage: name.usage.clone(),
        feature: "briefing_lens_naming".to_owned(),
        operation: "briefing.name_lens".to_owned(),
    }
}

fn rejected_usage(usage: &ProviderUsage, model_spec: &str) -> BriefingLensAssignmentUsage {
    let (provider, model) = split_model_spec(model_spec);
    BriefingLensAssignmentUsage {
        provider: provider.to_owned(),
        model: model.to_owned(),
        provider_response_id: None,
        usage: usage.clone(),
        feature: "briefing_lens_naming".to_owned(),
        operation: "briefing.name_lens.observed".to_owned(),
    }
}

#[derive(Debug)]
pub(super) struct NamedClusters {
    pub names: Vec<Option<GeneratedBriefingLensName>>,
    pub failed_usage: Vec<BriefingLensAssignmentUsage>,
}

// Preserve successful siblings and every billable response; failed clusters stay pending.
pub(super) async fn name_clusters<'a, F, Fut>(
    clusters: &'a [Cluster],
    available_slots: usize,
    source_limit: usize,
    name: F,
) -> Result<NamedClusters, SemanticLensPlanningError>
where
    F: Fn(&'a [Candidate]) -> Fut,
    Fut: std::future::Future<Output = Result<GeneratedBriefingLensName, SemanticLensPlanningError>>,
{
    let mut names = Vec::new();
    let mut failed_usage = Vec::new();
    let mut failure = None;
    for batch in clusters[..clusters.len().min(available_slots)].chunks(LENS_NAMING_PARALLELISM) {
        let results = join_all(batch.iter().map(|cluster| {
            name(&cluster.candidates[..cluster.candidates.len().min(source_limit)])
        }))
        .await;
        let mut batch_failed = false;
        for result in results {
            match result {
                Ok(name) => names.push(Some(name)),
                Err(error) => {
                    let (cause, usage) = error.into_observed();
                    failed_usage.extend(usage);
                    names.push(None);
                    batch_failed = true;
                    if failure.is_none() {
                        failure = Some(cause);
                    }
                }
            }
        }
        if batch_failed {
            // Avoid starting further batches after failures, without discarding
            // the already completed useful work in this task.
            break;
        }
    }
    if names.iter().all(Option::is_none)
        && let Some(error) = failure
    {
        return Err(error.with_usage(failed_usage));
    }
    Ok(NamedClusters {
        names,
        failed_usage,
    })
}

pub(super) async fn name_cluster(
    gateway: &BriefingCompositionGateway,
    candidates: &[Candidate],
    minimum_members: usize,
    lease: &crate::LeaseHealth,
) -> Result<GeneratedBriefingLensName, SemanticLensPlanningError> {
    let sources = candidates
        .iter()
        .map(|candidate| composition_source(&candidate.pending))
        .collect::<Vec<_>>();
    name_with_retries(
        sources.len(),
        gateway.naming_model_spec(),
        || !lease.ownership_lost(),
        || gateway.name_lens(&sources, minimum_members),
    )
    .await
}

pub(super) async fn name_with_retries<F, Fut>(
    source_count: usize,
    model_spec: &str,
    can_dispatch: impl Fn() -> bool,
    mut attempt_name: F,
) -> Result<GeneratedBriefingLensName, SemanticLensPlanningError>
where
    F: FnMut() -> Fut,
    Fut: std::future::Future<
            Output = Result<GeneratedBriefingLensName, BriefingCompositionGatewayError>,
        >,
{
    let mut failed_usage: Vec<BriefingLensAssignmentUsage> = Vec::new();
    let mut last_error = None;
    for attempt in 1..=LENS_NAMING_ATTEMPTS {
        if !can_dispatch() {
            return Err(SemanticLensPlanningError::OwnershipLost.with_usage(failed_usage));
        }
        match attempt_name().await {
            Ok(mut name) => {
                for observed in &failed_usage {
                    name.usage.add_assign(&observed.usage);
                }
                return Ok(name);
            }
            Err(error) => {
                if let Some(usage) = error.observed_usage() {
                    failed_usage.push(rejected_usage(usage, model_spec));
                }
                tracing::warn!(source_count, attempt, max_attempts=LENS_NAMING_ATTEMPTS, error=%error, "Briefing semantic lens naming failed");
                last_error = Some(error);
            }
        }
    }
    Err(
        SemanticLensPlanningError::Provider(last_error.expect("at least one naming attempt"))
            .with_usage(failed_usage),
    )
}
