//! Summary decisions against the user's frozen category definitions.

use futures_util::{StreamExt, stream::FuturesUnordered};
use newsly_agent_runtime::{AgentModelUsageObservation, ProviderUsage};
use newsly_db::PreparedBriefingRefreshSeed;
use newsly_providers::{
    NEWS_CATEGORY_RESPONSE_MODEL, NewsCategory, NewsCategoryDecision, NewsCategoryGateway,
    NewsCategoryGatewayError, NewsCategoryInput,
};

use crate::{LeaseHealth, agent_usage::AgentUsageRecorder};

const PARALLELISM: usize = 4;
const PHASE_BUDGET: std::time::Duration = std::time::Duration::from_secs(20);
const MIN_PROBABILITY: f64 = 0.80;

#[derive(Debug, Clone, PartialEq, Eq)]
pub(super) enum CategoryRoute {
    Existing(String),
    Discover,
    General,
}

pub(super) async fn route_pending(
    gateway: &NewsCategoryGateway,
    seed: &PreparedBriefingRefreshSeed,
    lease: &LeaseHealth,
    recorder: &AgentUsageRecorder,
) -> Vec<CategoryRoute> {
    let categories: Vec<_> = seed
        .lens_assignment
        .active_lenses
        .iter()
        .map(|lens| NewsCategory {
            key: lens.key.clone(),
            title: lens.title.clone(),
            deck: lens.deck.clone(),
            routing_rule: lens.routing_rule.clone(),
        })
        .collect();
    if categories.is_empty() {
        return vec![CategoryRoute::Discover; seed.lens_assignment.pending_sources.len()];
    }
    let inputs: Vec<_> = seed
        .lens_assignment
        .pending_sources
        .iter()
        .map(|pending| {
            let source = &pending.source;
            NewsCategoryInput {
                title: source.title.clone(),
                summary: source.summary.clone().unwrap_or_default(),
                key_points: source.key_points.clone(),
                categories: categories.clone(),
            }
        })
        .collect();
    let deadline = tokio::time::Instant::now() + PHASE_BUDGET;
    let routes = route_bounded(&inputs, || lease.ownership_lost() || tokio::time::Instant::now() >= deadline, |input| async move {
        let result = gateway.decide(input).await;
        if let Some(observation) = observation(&result)
            && let Err(error) = recorder.record(observation) {
            tracing::error!(task_id=seed.task_id, error=%error, "category usage recording failed");
        }
        if let Err(error) = &result {
            tracing::warn!(task_id=seed.task_id, user_id=seed.user_id, code=error.code(),
                "category decision unavailable; retaining general news");
        }
        decision_route(result, &input.categories)
    }).await;
    tracing::info!(
        task_id = seed.task_id,
        user_id = seed.user_id,
        eligible_sources = inputs.len(),
        routed_sources = routes.len(),
        dispatch_budget_exhausted = tokio::time::Instant::now() >= deadline,
        "Briefing category routing pass completed"
    );
    routes
}

// Dispatch a contiguous source prefix, restoring input order after completion-driven refill.
// Cancellation stops dispatch and drains owned calls so their billing observations survive.
// Each production call retains the gateway's five-second transport deadline.
async fn route_bounded<'a, F, Fut>(
    inputs: &'a [NewsCategoryInput],
    cancelled: impl Fn() -> bool,
    decide: F,
) -> Vec<CategoryRoute>
where
    F: Fn(&'a NewsCategoryInput) -> Fut,
    Fut: std::future::Future<Output = CategoryRoute>,
{
    let mut inflight = FuturesUnordered::new();
    let mut routes = Vec::with_capacity(inputs.len());
    let mut next = 0;
    loop {
        while inflight.len() < PARALLELISM && next < inputs.len() && !cancelled() {
            let index = next;
            let decision = decide(&inputs[index]);
            inflight.push(async move { (index, decision.await) });
            routes.push(None);
            next += 1;
        }
        let Some((index, route)) = inflight.next().await else {
            break;
        };
        routes[index] = Some(route);
    }
    // Every dispatched source is drained; undispatched sources remain a pending suffix.
    routes.into_iter().map(Option::unwrap).collect()
}

fn decision_route(
    result: Result<NewsCategoryDecision, NewsCategoryGatewayError>,
    categories: &[NewsCategory],
) -> CategoryRoute {
    let Ok(decision) = result else {
        return CategoryRoute::General;
    };
    if !decision.selected_probability.is_finite() || decision.selected_probability < MIN_PROBABILITY
    {
        return CategoryRoute::General;
    }
    match decision.selected_key {
        Some(key) if categories.iter().any(|category| category.key == key) => {
            CategoryRoute::Existing(key)
        }
        Some(_) => CategoryRoute::General,
        None => CategoryRoute::Discover,
    }
}

fn observation(
    result: &Result<NewsCategoryDecision, NewsCategoryGatewayError>,
) -> Option<AgentModelUsageObservation> {
    let (usage, cost, response_id) = match result {
        Ok(decision) => (
            Some(decision.usage.clone()),
            Some(decision.cost_usd),
            Some(decision.generation_id.clone()),
        ),
        Err(NewsCategoryGatewayError::Unavailable | NewsCategoryGatewayError::InvalidInput(_)) => {
            return None;
        }
        Err(error) => (
            error.observed_usage().cloned(),
            error.observed_cost_usd(),
            error.generation_id().map(str::to_owned),
        ),
    };
    Some(AgentModelUsageObservation {
        run_id: uuid::Uuid::new_v4(),
        sequence: 1,
        feature: "briefing_category_assignment".to_owned(),
        provider: "openrouter".to_owned(),
        model: NEWS_CATEGORY_RESPONSE_MODEL.to_owned(),
        endpoint: "decisions".to_owned(),
        response_id,
        provider_request_id: None,
        requested_service_tier: None,
        processing_tier: None,
        usage_is_observed: usage.is_some(),
        provider_reported_cost_usd: cost,
        usage: usage.unwrap_or_else(|| ProviderUsage {
            request_count: 1,
            ..ProviderUsage::default()
        }),
    })
}

#[cfg(test)]
mod tests;
