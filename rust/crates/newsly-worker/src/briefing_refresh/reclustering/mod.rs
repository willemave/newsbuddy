//! Bounded nightly maintenance; external work is separate from queue finalization.
use super::BriefingRefreshWorkerServices;
use crate::agent_usage::{AgentUsageAttribution, AgentUsageSink};
use crate::{HandlerExecution, HandlerFuture, HandlerInterruptionPolicy, LeaseHealth, TaskHandler};
use chrono::Utc;
use newsly_db::news_category_reclustering::{
    self as repository, NewsCategoryCandidate, NewsCategorySnapshot,
};
use newsly_db::{
    BeginNewsCategoryRunOutcome, NewsCategoryMaintenanceMode, NewsCategoryRunClaimFence,
    NewsCategoryRunContext, NewsCategoryRunStatus, begin_news_category_run,
    reserve_news_category_naming_attempt,
};
use newsly_domain::plan_news_lens_reclustering;
use newsly_providers::{BRIEFING_LENS_NAMING_MAX_OUTPUT_TOKENS, BriefingLensNamingBatch};
use newsly_queue::{OwnedWorkPlan, TaskResult, TaskType};
use serde_json::json;
use std::sync::Arc;

mod finalizer;
#[cfg(test)]
mod finalizer_tests;
mod planning;
#[cfg(test)]
mod tests;
use finalizer::ReclusteringFinalizer;
use planning::CandidateState;

#[derive(Debug)]
pub struct ReclusterNewsLensesHandler {
    services: Arc<BriefingRefreshWorkerServices>,
    global_daily_token_limit: i64,
}
impl ReclusterNewsLensesHandler {
    pub fn new(services: Arc<BriefingRefreshWorkerServices>) -> anyhow::Result<Self> {
        let limit = std::env::var("NEWS_CATEGORY_NAMING_DAILY_TOKEN_LIMIT")
            .unwrap_or_else(|_| "2500000".into())
            .parse::<i64>()?;
        anyhow::ensure!(
            (10_000..=100_000_000).contains(&limit),
            "NEWS_CATEGORY_NAMING_DAILY_TOKEN_LIMIT outside supported bounds"
        );
        Ok(Self {
            services,
            global_daily_token_limit: limit,
        })
    }
}
impl TaskHandler for ReclusterNewsLensesHandler {
    fn task_type(&self) -> TaskType {
        TaskType::ReclusterNewsLenses
    }

    fn interruption_policy(&self) -> HandlerInterruptionPolicy {
        HandlerInterruptionPolicy::DrainBounded
    }

    fn execute(&self, plan: Arc<OwnedWorkPlan>, lease: LeaseHealth) -> HandlerFuture<'_> {
        Box::pin(async move {
            if lease.ownership_lost() {
                return lease_lost();
            }
            // Once bounded execution starts, let any in-flight naming call reach its append-only
            // attempt audit. Exact-lease finalization remains independently fenced, and a lost
            // lease discards the prepared finalizer below.
            let result = execute(self, &plan, &lease).await;
            if lease.ownership_lost() {
                return lease_lost();
            }
            match result {
                Ok(result) => result,
                Err(error) => HandlerExecution::from_result(TaskResult::fail(
                    Some(format!("nightly categories: {error}")),
                    true,
                )),
            }
        })
    }
}

fn lease_lost() -> HandlerExecution {
    HandlerExecution::from_result(TaskResult::fail(
        Some("nightly category lease lost".into()),
        true,
    ))
}

async fn execute(
    handler: &ReclusterNewsLensesHandler,
    plan: &OwnedWorkPlan,
    lease: &LeaseHealth,
) -> anyhow::Result<HandlerExecution> {
    let user = plan
        .payload
        .get("user_id")
        .and_then(serde_json::Value::as_i64)
        .filter(|id| *id > 0);
    anyhow::ensure!(
        user.is_some() && user == plan.owner_user_id,
        "nightly payload owner mismatch"
    );
    let user = user.expect("validated owner");
    let run_id = plan
        .payload
        .get("run_id")
        .and_then(serde_json::Value::as_i64)
        .ok_or_else(|| anyhow::anyhow!("missing run_id"))?;
    let revision = plan
        .payload
        .get("timezone_revision")
        .and_then(serde_json::Value::as_i64)
        .ok_or_else(|| anyhow::anyhow!("missing timezone revision"))?;
    let claim = lease.claim();
    let claim_fence = NewsCategoryRunClaimFence {
        locked_by: claim.locked_by.clone(),
        lease_token: claim.lease_token.get(),
        retry_count: claim.retry_count,
        executor_runtime: claim.executor_runtime.to_string(),
        executor_version: claim.executor_version,
        executor_namespace: claim.executor_namespace.clone(),
    };
    let services = &handler.services;
    let BeginNewsCategoryRunOutcome::Ready(context) = begin_news_category_run(
        &services.pool,
        plan.task_id,
        user,
        run_id,
        revision,
        &claim_fence,
        Utc::now(),
    )
    .await?
    else {
        return Ok(HandlerExecution::from_result(TaskResult::ok()));
    };
    if lease.ownership_lost() {
        return Ok(lease_lost());
    }
    let refresh_busy = busy(&services.pool, user).await?;
    if lease.ownership_lost() {
        return Ok(lease_lost());
    }
    if refresh_busy {
        return Ok(HandlerExecution::from_result(TaskResult::defer(60)));
    }
    let snapshot = {
        let mut tx = services.pool.begin().await?;
        let snapshot = repository::load_snapshot(
            &mut tx,
            user,
            context.run.scheduled_at,
            services.gateway.embedding_model(),
        )
        .await?;
        tx.commit().await?;
        snapshot
    };
    if lease.ownership_lost() {
        return Ok(lease_lost());
    }
    let Some(snapshot) = snapshot else {
        return Ok(completed(
            context,
            None,
            None,
            None,
            NewsCategoryRunStatus::NoOp,
            json!({"reason":"no_eligible_briefing"}),
        ));
    };
    if snapshot.stories.len() < 3 {
        return Ok(completed(
            context,
            Some(snapshot),
            None,
            None,
            NewsCategoryRunStatus::NoOp,
            json!({"reason":"insufficient_cached_stories"}),
        ));
    }
    let stored = repository::load_candidate(&services.pool, user).await?;
    if lease.ownership_lost() {
        return Ok(lease_lost());
    }
    let prior = stored
        .as_ref()
        .and_then(|c| serde_json::from_value::<CandidateState>(c.candidate.clone()).ok())
        .filter(|c| c.schema_version == 2 && c.model == snapshot.model);
    let prior_names = stored
        .as_ref()
        .and_then(|stored| stored.naming_result.as_ref())
        .and_then(|value| serde_json::from_value::<BriefingLensNamingBatch>(value.clone()).ok());
    if stored
        .as_ref()
        .is_some_and(|s| s.input_hash == snapshot.corpus_hash)
        && prior
            .as_ref()
            .zip(prior_names.as_ref())
            .is_some_and(|(candidate, names)| {
                planning::current_publication_matches(&snapshot, candidate, names)
            })
    {
        return Ok(completed(
            context,
            Some(snapshot),
            None,
            None,
            NewsCategoryRunStatus::NoOp,
            json!({"reason":"unchanged_corpus"}),
        ));
    }
    let input = planning::input(&snapshot, prior.as_ref());
    let fitted = tokio::task::spawn_blocking(move || plan_news_lens_reclustering(&input)).await??;
    if lease.ownership_lost() {
        return Ok(lease_lost());
    }
    let mut candidate = planning::track(
        &snapshot,
        prior.as_ref(),
        fitted,
        context.run.local_date,
        context.run.run_id,
    );
    let request = planning::naming_request(&snapshot, &candidate)?;
    let fingerprint = newsly_db::news_lens_embeddings::input_hash(&format!(
        "nightly-naming-v1|{}|{}",
        services.gateway.naming_model_spec(),
        serde_json::to_string(&request)?
    ));
    let mut names = None;
    if !request.categories.is_empty() {
        if let Some(cached) = repository::cached_naming(&services.pool, user, &fingerprint).await? {
            if lease.ownership_lost() {
                return Ok(lease_lost());
            }
            names = Some(serde_json::from_value::<BriefingLensNamingBatch>(cached)?);
        } else {
            let requested_tokens = i64::try_from(serde_json::to_vec_pretty(&request)?.len())?
                + i64::try_from(BRIEFING_LENS_NAMING_MAX_OUTPUT_TOKENS)?
                + 8_192;
            for _ in 0..2 {
                if lease.ownership_lost() {
                    return Ok(lease_lost());
                }
                if !reserve_news_category_naming_attempt(
                    &services.pool,
                    &context,
                    requested_tokens,
                    handler.global_daily_token_limit,
                )
                .await?
                {
                    break;
                }
                if lease.ownership_lost() {
                    return Ok(lease_lost());
                }
                let attempt = uuid::Uuid::new_v4();
                let usage_sink = Arc::new(AgentUsageSink::new(
                    services.pool.clone(),
                    AgentUsageAttribution {
                        operation: "briefing.review_lens_names".to_owned(),
                        source: "queue".to_owned(),
                        task_id: plan.task_id,
                        content_id: None,
                        session_id: None,
                        message_id: None,
                        user_id: Some(user),
                    },
                ));
                let generated = services
                    .gateway
                    .review_lens_names(&request, usage_sink.clone())
                    .await;
                usage_sink.finish().await;
                match generated {
                    Ok(generated) => {
                        repository::record_naming_attempt(
                            &services.pool,
                            &context,
                            attempt,
                            &fingerprint,
                            Some(&serde_json::to_value(&generated.batch)?),
                        )
                        .await?;
                        if lease.ownership_lost() {
                            return Ok(lease_lost());
                        }
                        names = Some(generated.batch);
                        break;
                    }
                    Err(error) => {
                        repository::record_naming_attempt(
                            &services.pool,
                            &context,
                            attempt,
                            &fingerprint,
                            None,
                        )
                        .await?;
                        tracing::warn!(run_id, user_id=user,error=%error,"nightly category naming attempt failed");
                        if lease.ownership_lost() {
                            return Ok(lease_lost());
                        }
                    }
                }
            }
        }
    }
    let mut publication = names
        .as_ref()
        .map(|names| planning::publication(&candidate, names))
        .transpose()?;
    let status = if !request.categories.is_empty() && names.is_none() {
        NewsCategoryRunStatus::Failed
    } else if context.run.mode == NewsCategoryMaintenanceMode::Shadow {
        NewsCategoryRunStatus::Shadowed
    } else if publication.as_ref().is_none_or(Vec::is_empty) {
        NewsCategoryRunStatus::NoOp
    } else {
        NewsCategoryRunStatus::Published
    };
    if status != NewsCategoryRunStatus::Published {
        publication = None;
    } else if let Some(published) = &publication {
        for cluster in &mut candidate.clusters {
            if published
                .iter()
                .any(|lens| lens.key == cluster.cluster.plan_id)
            {
                cluster.published = true;
            }
        }
    }
    let diagnostics = json!({"diagnostics":candidate.diagnostics,"lineage":candidate.lineage,
        "naming_input_hash":fingerprint,"naming_category_count":request.categories.len(),
        "representatives_per_category":25,"snapshot_fingerprint":snapshot.fingerprint});
    let record = NewsCategoryCandidate {
        input_hash: snapshot.corpus_hash.clone(),
        candidate: serde_json::to_value(candidate)?,
        naming_result: names.map(serde_json::to_value).transpose()?,
    };
    Ok(completed(
        context,
        Some(snapshot),
        Some(record),
        publication,
        status,
        diagnostics,
    ))
}

fn completed(
    context: NewsCategoryRunContext,
    snapshot: Option<NewsCategorySnapshot>,
    candidate: Option<NewsCategoryCandidate>,
    publication: Option<Vec<repository::NewsCategoryPublicationLens>>,
    status: NewsCategoryRunStatus,
    output: serde_json::Value,
) -> HandlerExecution {
    HandlerExecution::with_finalizer(
        TaskResult::ok(),
        ReclusteringFinalizer {
            context,
            snapshot,
            candidate,
            publication,
            status,
            output,
        },
    )
}

async fn busy(pool: &sqlx::PgPool, user: i64) -> Result<bool, sqlx::Error> {
    sqlx::query_scalar("SELECT EXISTS(SELECT 1 FROM processing_tasks WHERE owner_user_id::bigint=$1 AND task_type='briefing_refresh' AND status='processing' AND lease_expires_at>timezone('UTC',clock_timestamp())) OR EXISTS(SELECT 1 FROM onboarding_first_edition_runs WHERE user_id::bigint=$1 AND status='active' AND NOT news_seed_settled)")
        .bind(user).fetch_one(pool).await
}
