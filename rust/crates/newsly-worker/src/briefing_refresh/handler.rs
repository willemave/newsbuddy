use std::collections::HashMap;
use std::fmt::Write;
use std::sync::Arc;

use chrono::Utc;
use futures_util::stream::{self, StreamExt};
use newsly_db::{
    ApplyBriefingLensAssignmentOutcome, BRIEFING_COMPOSITION_PROMPT_VERSION,
    BriefingEmbeddingUsage, BriefingPendingIdentity, BriefingRefreshLens, BriefingRefreshMode,
    BriefingRefreshPublication, BriefingRefreshSource, BriefingSegmentUsage,
    ComposedBriefingAppend, ComposedBriefingCompaction, ComposedBriefingSegment,
    PrepareBriefingRefreshOutcome, PreparedBriefingRefresh, apply_briefing_lens_assignment,
    prepare_briefing_refresh,
};
use newsly_providers::{
    BriefingCompositionGateway, BriefingCompositionRequest, BriefingCompositionSource,
};
use newsly_queue::{OwnedWorkPlan, QueueKernel, TaskResult, TaskType};
use serde_json::Value;
use sqlx::PgPool;
use thiserror::Error;

use crate::{HandlerExecution, HandlerFuture, LeaseHealth, TaskHandler};

use super::config::BriefingRefreshWorkerConfig;
use super::finalizer::BriefingRefreshFinalizer;
use super::normalize::normalize_layout;
use super::planning::{PlannedBriefingWindow, plan_windows};
use super::semantic_lenses::plan_semantic_lenses;
use super::{EMBEDDING_PROVIDER, nonempty, split_model_spec};

#[derive(Debug, Clone)]
pub struct BriefingRefreshWorkerServices {
    pub(super) pool: PgPool,
    pub(super) queue: QueueKernel,
    pub(super) gateway: BriefingCompositionGateway,
    config: BriefingRefreshWorkerConfig,
}

impl BriefingRefreshWorkerServices {
    pub const fn new(
        pool: PgPool,
        queue: QueueKernel,
        gateway: BriefingCompositionGateway,
        config: BriefingRefreshWorkerConfig,
    ) -> Self {
        Self {
            pool,
            queue,
            gateway,
            config,
        }
    }
}

#[derive(Debug, Clone)]
pub struct BriefingRefreshHandler {
    services: Arc<BriefingRefreshWorkerServices>,
}

impl BriefingRefreshHandler {
    pub fn new(services: Arc<BriefingRefreshWorkerServices>) -> Self {
        Self { services }
    }
}

impl TaskHandler for BriefingRefreshHandler {
    fn task_type(&self) -> TaskType {
        TaskType::BriefingRefresh
    }

    fn execute(&self, plan: Arc<OwnedWorkPlan>, lease: LeaseHealth) -> HandlerFuture<'_> {
        let services = Arc::clone(&self.services);
        Box::pin(async move { execute_refresh(&services, &plan, lease).await })
    }
}

async fn execute_refresh(
    services: &BriefingRefreshWorkerServices,
    plan: &OwnedWorkPlan,
    mut lease: LeaseHealth,
) -> HandlerExecution {
    let Some(user_id) = plan
        .payload
        .get("user_id")
        .and_then(Value::as_i64)
        .filter(|value| *value > 0)
    else {
        return plain_failure("briefing_refresh requires a positive user_id", false);
    };
    if plan.owner_user_id != Some(user_id) {
        return plain_failure(
            "briefing_refresh owner_user_id does not match its payload user_id",
            false,
        );
    }
    let mode = match BriefingRefreshMode::try_from(
        plan.payload
            .get("mode")
            .and_then(Value::as_str)
            .unwrap_or("append"),
    ) {
        Ok(mode) => mode,
        Err(error) => return plain_failure(error.to_string(), false),
    };

    // Nightly maintenance has its own worker. Avoid starting paid composition
    // while it holds this user's routing generation; nightly work reciprocally
    // defers behind an already running normal refresh.
    match sqlx::query_scalar::<_, bool>("SELECT EXISTS(SELECT 1 FROM processing_tasks WHERE owner_user_id::bigint=$1 AND task_type='recluster_news_lenses' AND status='processing' AND lease_expires_at>timezone('UTC',clock_timestamp()))")
        .bind(user_id).fetch_one(&services.pool).await {
        Ok(true) => return HandlerExecution::from_result(TaskResult::defer(30)),
        Ok(false) => {},
        Err(error) => return plain_failure(error.to_string(), true),
    }

    let mut repository_config = services.config.repository.clone();
    let initial = match sqlx::query_scalar::<_, bool>("SELECT EXISTS(SELECT 1 FROM onboarding_first_edition_runs WHERE user_id::bigint=$1 AND status='active' AND NOT news_seed_settled)").bind(user_id).fetch_one(&services.pool).await {
        Ok(value) => value, Err(error) => return plain_failure(error.to_string(), true),
    };
    let progressive = initial && mode != BriefingRefreshMode::Full;
    if initial {
        repository_config.pending_max_age_seconds = 0;
    }
    let preparation = {
        let mut transaction = match services.pool.begin().await {
            Ok(transaction) => transaction,
            Err(error) => return plain_failure(error.to_string(), true),
        };
        let preparation = match prepare_briefing_refresh(
            &mut transaction,
            plan.task_id,
            user_id,
            mode,
            &repository_config,
        )
        .await
        {
            Ok(preparation) => preparation,
            Err(error) => return plain_failure(error.to_string(), true),
        };
        if let Err(error) = transaction.commit().await {
            return plain_failure(error.to_string(), true);
        }
        preparation
    };
    let seed = match preparation {
        PrepareBriefingRefreshOutcome::Disabled { version } => {
            tracing::info!(
                task_id = plan.task_id,
                user_id,
                version,
                "Briefing refresh skipped because the owner is inactive"
            );
            return HandlerExecution::from_result(TaskResult::ok());
        }
        PrepareBriefingRefreshOutcome::Ready(seed) => seed,
    };
    if lease.ownership_lost() {
        return plain_failure("queue lease was lost before Briefing lens planning", true);
    }
    let readiness = match super::warm_news::ready_news(
        &services.pool,
        &seed.lens_assignment.pending_sources,
        services.gateway.embedding_model(),
    )
    .await
    {
        Ok(readiness) => readiness,
        Err(error) => return plain_failure(error.to_string(), true),
    };
    let mut semantic_seed = seed.clone();
    semantic_seed.lens_assignment.pending_sources = readiness.sources;
    let cached_vectors = readiness.vectors;
    let failed_sources = readiness.failed;
    let missing = readiness.requests;
    let waiting_for_news = !missing.is_empty();
    let lens_planning_started = std::time::Instant::now();
    let mut lens_plan = {
        let lens_planning = plan_semantic_lenses(
            &services.gateway,
            &semantic_seed,
            &repository_config,
            services.config.embedding_batch_size,
            cached_vectors,
            if progressive { 1 } else { usize::MAX },
        );
        tokio::pin!(lens_planning);
        let result = tokio::select! {
            result = &mut lens_planning => result,
            () = lease.wait_for_ownership_loss() => {
                return plain_failure("queue lease was lost during Briefing lens planning", true);
            }
        };
        match result {
            Ok(plan) => plan,
            Err(error) => return plain_failure(error.to_string(), true),
        }
    };
    tracing::info!(
        task_id = plan.task_id,
        user_id,
        elapsed_ms = lens_planning_started.elapsed().as_millis(),
        new_categories = lens_plan.new_lenses.len(),
        progressive,
        "Briefing category planning completed"
    );
    if lease.ownership_lost() {
        return plain_failure("queue lease was lost after Briefing lens planning", true);
    }
    super::semantic_lenses::assign_failed_sources(
        &mut lens_plan,
        &seed,
        &failed_sources,
        &repository_config,
    );
    let new_category_keys = lens_plan
        .new_lenses
        .iter()
        .map(|lens| lens.key.clone())
        .collect::<Vec<_>>();
    let composition_fence = seed.claim_fence.clone();
    let prepared = {
        let mut transaction = match services.pool.begin().await {
            Ok(transaction) => transaction,
            Err(error) => return plain_failure(error.to_string(), true),
        };
        let outcome = match apply_briefing_lens_assignment(
            &mut transaction,
            seed,
            &lens_plan,
            &repository_config,
        )
        .await
        {
            Ok(outcome) => outcome,
            Err(error) => return plain_failure(error.to_string(), true),
        };
        let prepared = match outcome {
            ApplyBriefingLensAssignmentOutcome::Stale => {
                return plain_failure(
                    "Briefing lens assignment snapshot or queue fence became stale",
                    true,
                );
            }
            ApplyBriefingLensAssignmentOutcome::Ready(prepared) => prepared,
        };
        if let Err(error) = services
            .queue
            .enqueue_many_in_transaction(&mut transaction, missing)
            .await
        {
            return plain_failure(error.to_string(), true);
        }
        if let Err(error) = transaction.commit().await {
            return plain_failure(error.to_string(), true);
        }
        prepared
    };
    if lease.ownership_lost() {
        return plain_failure("queue lease was lost before Briefing composition", true);
    }

    let external = build_publication(
        services,
        prepared,
        &composition_fence,
        progressive,
        &new_category_keys,
    );
    tokio::pin!(external);
    let publication = tokio::select! {
        result = &mut external => result,
        () = lease.wait_for_ownership_loss() => {
            return plain_failure("queue lease was lost during Briefing composition", true);
        }
    };
    let publication = match publication {
        Ok(publication) => publication,
        Err(error) => return plain_failure(error.to_string(), error.retryable()),
    };
    if lease.ownership_lost() {
        return plain_failure("queue lease was lost after Briefing composition", true);
    }
    HandlerExecution::with_finalizer(
        TaskResult::ok(),
        BriefingRefreshFinalizer::new(
            services.queue.clone(),
            publication,
            repository_config,
            plan.payload
                .get("stale_recoveries")
                .and_then(Value::as_u64)
                .unwrap_or(0),
            waiting_for_news,
        ),
    )
}

async fn build_publication(
    services: &BriefingRefreshWorkerServices,
    prepared: PreparedBriefingRefresh,
    fence: &newsly_db::BriefingRefreshClaimFence,
    progressive: bool,
    new_category_keys: &[String],
) -> Result<BriefingRefreshPublication, BriefingRefreshExecutionError> {
    let mut units = Vec::new();
    let mut embedding_usage = Vec::new();
    let mut ordinal = 0_usize;
    let mut compaction_selected = vec![false; prepared.compaction_batches.len()];
    let context = CompositionContext {
        pool: &services.pool,
        task_id: prepared.task_id,
        user_id: prepared.user_id,
        fence,
    };
    let mut batches = prepared.append_batches.iter().collect::<Vec<_>>();
    if progressive {
        batches.sort_by_key(|batch| {
            (
                batch.lens.tier != "news",
                !new_category_keys.contains(&batch.lens.key),
                batch.lens.position,
            )
        });
    }

    'append_batches: for batch in batches {
        let planned = plan_windows(
            &services.pool,
            &services.gateway,
            &batch.sources,
            &batch.lens.tier,
            services.config.repository.news_window_max,
            services.config.event_similarity,
            services.config.embedding_batch_size,
        )
        .await;
        embedding_usage.extend(planned.embedding_batches.into_iter().map(|batch| {
            BriefingEmbeddingUsage {
                provider: EMBEDDING_PROVIDER.to_owned(),
                model: batch.model,
                provider_response_id: batch.provider_response_id,
                usage: batch.usage,
            }
        }));
        let pending_by_source = batch
            .pending_rows
            .iter()
            .map(|pending| {
                (
                    format!("{}:{}", pending.source_kind, pending.source_id),
                    pending.clone(),
                )
            })
            .collect::<HashMap<_, _>>();
        let take = if prepared.mode == BriefingRefreshMode::Full {
            planned.windows.len()
        } else {
            planned.windows.len().min(1)
        };
        for window in planned.windows.into_iter().take(take) {
            let pending_rows = window
                .sources
                .iter()
                .map(|source| {
                    pending_by_source
                        .get(&source.source_key)
                        .cloned()
                        .ok_or_else(|| {
                            BriefingRefreshExecutionError::MissingPendingSource(
                                source.source_key.clone(),
                            )
                        })
                })
                .collect::<Result<Vec<_>, _>>()?;
            let unit = CompositionUnit {
                ordinal,
                lens: batch.lens.clone(),
                window,
                kind: CompositionUnitKind::Append { pending_rows },
            };
            if progressive {
                if !composition_is_ready(
                    services.gateway.model_spec(),
                    services.config.max_figures_deep,
                    &unit,
                    context,
                )
                .await?
                {
                    continue;
                }
                units.push(unit);
                if batch.lens.tier == "news" {
                    // Do not plan or request embeddings for later categories
                    // before publishing this complete window.
                    break 'append_batches;
                }
            } else {
                units.push(unit);
            }
            ordinal += 1;
        }
    }

    for (batch_index, batch) in prepared
        .compaction_batches
        .iter()
        .enumerate()
        .filter(|_| !progressive)
    {
        let planned = plan_windows(
            &services.pool,
            &services.gateway,
            &batch.sources,
            &batch.lens.tier,
            services.config.repository.news_window_max,
            services.config.event_similarity,
            services.config.embedding_batch_size,
        )
        .await;
        embedding_usage.extend(planned.embedding_batches.into_iter().map(|batch| {
            BriefingEmbeddingUsage {
                provider: EMBEDDING_PROVIDER.to_owned(),
                model: batch.model,
                provider_response_id: batch.provider_response_id,
                usage: batch.usage,
            }
        }));
        if !batch.repair_required && planned.windows.len() >= batch.donors.len() {
            continue;
        }
        compaction_selected[batch_index] = true;
        for window in planned.windows {
            units.push(CompositionUnit {
                ordinal,
                lens: batch.lens.clone(),
                window,
                kind: CompositionUnitKind::Compaction { batch_index },
            });
            ordinal += 1;
        }
    }

    let parallelism = services.config.compose_parallelism.min(units.len().max(1));
    let gateway = &services.gateway;
    let max_attempts = super::config::MAX_COMPOSE_ATTEMPTS;
    let max_figures_deep = services.config.max_figures_deep;
    let mut composed = stream::iter(units.into_iter().map(|unit| async move {
        compose_unit(gateway, unit, max_attempts, max_figures_deep, context).await
    }))
    .buffer_unordered(parallelism)
    .collect::<Vec<_>>()
    .await
    .into_iter()
    .collect::<Result<Vec<_>, _>>()?;
    composed.sort_by_key(|unit| unit.ordinal);

    let mut append_segments = Vec::new();
    let mut compacted_segments = vec![Vec::new(); prepared.compaction_batches.len()];
    for unit in composed {
        match unit.kind {
            CompositionUnitKind::Append { pending_rows } => {
                append_segments.push(ComposedBriefingAppend {
                    pending_rows,
                    segment: unit.segment,
                });
            }
            CompositionUnitKind::Compaction { batch_index } => {
                compacted_segments[batch_index].push(unit.segment);
            }
        }
    }
    let compactions = prepared
        .compaction_batches
        .iter()
        .zip(compacted_segments)
        .zip(compaction_selected)
        .filter_map(|((batch, segments), selected)| {
            selected.then(|| ComposedBriefingCompaction {
                donors: batch.donors.clone(),
                planned_source_keys: batch.planned_source_keys.clone(),
                segments,
            })
        })
        .collect();
    Ok(BriefingRefreshPublication {
        prepared,
        append_segments,
        compactions,
        embedding_usage,
        finalized_at: Utc::now(),
    })
}

#[derive(Debug, Clone)]
struct CompositionUnit {
    ordinal: usize,
    lens: BriefingRefreshLens,
    window: PlannedBriefingWindow,
    kind: CompositionUnitKind,
}

#[derive(Debug, Clone)]
enum CompositionUnitKind {
    Append {
        pending_rows: Vec<BriefingPendingIdentity>,
    },
    Compaction {
        batch_index: usize,
    },
}

#[derive(Debug)]
struct ComposedUnit {
    ordinal: usize,
    kind: CompositionUnitKind,
    segment: ComposedBriefingSegment,
}

#[derive(Clone, Copy)]
struct CompositionContext<'a> {
    pool: &'a PgPool,
    task_id: i64,
    user_id: i64,
    fence: &'a newsly_db::BriefingRefreshClaimFence,
}

trait BriefingComposer: Sync {
    fn model_spec(&self) -> &str;
    fn compose(
        &self,
        request: &BriefingCompositionRequest,
        feedback: Option<&str>,
    ) -> impl std::future::Future<
        Output = Result<
            newsly_providers::GeneratedBriefingLayout,
            newsly_providers::BriefingCompositionGatewayError,
        >,
    > + Send;
}

impl BriefingComposer for BriefingCompositionGateway {
    fn model_spec(&self) -> &str {
        Self::model_spec(self)
    }
    async fn compose(
        &self,
        request: &BriefingCompositionRequest,
        feedback: Option<&str>,
    ) -> Result<
        newsly_providers::GeneratedBriefingLayout,
        newsly_providers::BriefingCompositionGatewayError,
    > {
        Self::compose(self, request, feedback).await
    }
}

fn composition_request(unit: &CompositionUnit) -> BriefingCompositionRequest {
    BriefingCompositionRequest {
        lens_title: unit.lens.title.clone(),
        tier: unit.lens.tier.clone(),
        sources: unit.window.sources.iter().map(composition_source).collect(),
    }
}

fn composition_fingerprint(
    model: &str,
    max_figures_deep: usize,
    request: &BriefingCompositionRequest,
) -> String {
    use sha2::{Digest, Sha256};
    let digest = Sha256::digest(format!(
        "{BRIEFING_COMPOSITION_PROMPT_VERSION}|{model}|{max_figures_deep}|{request:?}"
    ));
    digest
        .iter()
        .fold(String::with_capacity(64), |mut text, byte| {
            let _ = write!(text, "{byte:02x}");
            text
        })
}

async fn composition_is_ready(
    model: &str,
    max_figures_deep: usize,
    unit: &CompositionUnit,
    context: CompositionContext<'_>,
) -> Result<bool, BriefingRefreshExecutionError> {
    let fingerprint = composition_fingerprint(model, max_figures_deep, &composition_request(unit));
    Ok(
        !newsly_db::briefing_attempts::cooling_down(context.pool, context.user_id, &fingerprint)
            .await?,
    )
}

async fn compose_unit(
    gateway: &impl BriefingComposer,
    unit: CompositionUnit,
    max_attempts: usize,
    max_figures_deep: usize,
    context: CompositionContext<'_>,
) -> Result<ComposedUnit, BriefingRefreshExecutionError> {
    let request = composition_request(&unit);
    let fingerprint = composition_fingerprint(gateway.model_spec(), max_figures_deep, &request);
    if newsly_db::briefing_attempts::cooling_down(context.pool, context.user_id, &fingerprint)
        .await?
    {
        return Err(BriefingRefreshExecutionError::Composition {
            lens_key: unit.lens.key,
            attempts: 0,
            message: "unchanged input is cooling down for 24 hours".to_owned(),
        });
    }
    let mut feedback = None;
    for attempt in 1..=max_attempts {
        let attempt_id = uuid::Uuid::new_v4();
        let provider_started = std::time::Instant::now();
        let generated = gateway.compose(&request, feedback.as_deref()).await;
        tracing::info!(task_id = context.task_id, user_id = context.user_id, lens_key = %unit.lens.key, attempt, elapsed_ms = provider_started.elapsed().as_millis(), source_count = request.sources.len(), succeeded = generated.is_ok(), "Briefing composition provider attempt completed");
        let generated = match generated {
            Ok(generated) => generated,
            Err(error) => {
                let retryable = error.retryable();
                let (provider, model) = split_model_spec(gateway.model_spec());
                let observed = error.observed_usage().map(|usage| BriefingSegmentUsage {
                    provider: provider.to_owned(),
                    model: model.to_owned(),
                    provider_response_id: None,
                    usage: usage.clone(),
                    operation: "briefing.compose_window.observed".to_owned(),
                });
                newsly_db::briefing_attempts::record_attempt(
                    context.pool,
                    context.task_id,
                    context.user_id,
                    context.fence,
                    attempt_id,
                    &fingerprint,
                    if retryable {
                        "transport_usage_unknown"
                    } else if observed.is_some() {
                        "rejected"
                    } else {
                        "rejected_usage_unknown"
                    },
                    observed.as_ref(),
                    !retryable && attempt == max_attempts,
                )
                .await?;
                if retryable {
                    return Err(BriefingRefreshExecutionError::Transport(error.to_string()));
                }
                feedback = Some(error.to_string());
                continue;
            }
        };
        let (provider, fallback_model) = split_model_spec(gateway.model_spec());
        let model = nonempty(&generated.model)
            .unwrap_or(fallback_model)
            .to_owned();
        let usage = BriefingSegmentUsage {
            provider: provider.to_owned(),
            model,
            provider_response_id: generated.provider_response_id,
            usage: generated.usage,
            operation: "briefing.compose_window.observed".to_owned(),
        };
        let normalized = generated.layout.and_then(|layout| {
            normalize_layout(
                &layout,
                &unit.window.sources,
                &unit.lens.tier,
                if unit.lens.tier == "news" {
                    0
                } else {
                    max_figures_deep
                },
            )
            .map_err(|error| error.to_string())
        });
        newsly_db::briefing_attempts::record_attempt(
            context.pool,
            context.task_id,
            context.user_id,
            context.fence,
            attempt_id,
            &fingerprint,
            if normalized.is_ok() {
                "accepted"
            } else {
                "rejected"
            },
            Some(&usage),
            normalized.is_err() && attempt == max_attempts,
        )
        .await?;
        let normalized = match normalized {
            Ok(normalized) => normalized,
            Err(error) => {
                feedback = Some(error);
                continue;
            }
        };
        let mut warnings = normalized.warnings;
        if matches!(&unit.kind, CompositionUnitKind::Compaction { .. }) {
            warnings.push("compaction_segment".to_owned());
        }
        if !warnings.is_empty() {
            tracing::warn!(
                lens_key = unit.lens.key,
                warnings = ?warnings,
                "briefing composition completed with warnings"
            );
        }
        return Ok(ComposedUnit {
            ordinal: unit.ordinal,
            kind: unit.kind,
            segment: ComposedBriefingSegment {
                lens: unit.lens,
                blocks: normalized.blocks,
                markdown_raw: normalized.markdown_raw,
                narration_text: normalized.narration_text,
                source_keys: unit
                    .window
                    .sources
                    .iter()
                    .map(|source| source.source_key.clone())
                    .collect(),
                event_groups: unit.window.event_groups,
                model: gateway.model_spec().to_owned(),
                prompt_version: BRIEFING_COMPOSITION_PROMPT_VERSION.to_owned(),
            },
        });
    }
    Err(BriefingRefreshExecutionError::Composition {
        lens_key: unit.lens.key,
        attempts: max_attempts,
        message: feedback.unwrap_or_else(|| "composition attempt budget was empty".to_owned()),
    })
}

fn composition_source(source: &BriefingRefreshSource) -> BriefingCompositionSource {
    BriefingCompositionSource {
        source_key: source.source_key.clone(),
        kind: source.kind.clone(),
        id: source.id,
        title: source.title.clone(),
        source_name: source.source_name.clone(),
        summary: source.summary.clone(),
        key_points: source.key_points.clone(),
        url: source.url.clone(),
        image_url: source.image_url.clone(),
        thumbnail_url: source.thumbnail_url.clone(),
        published_at: source.published_at.map(|value| value.to_rfc3339()),
        briefing_context: source.briefing_context.clone(),
    }
}

fn plain_failure(message: impl Into<String>, retryable: bool) -> HandlerExecution {
    HandlerExecution::from_result(TaskResult::fail(Some(message.into()), retryable))
}

#[derive(Debug, Error)]
enum BriefingRefreshExecutionError {
    #[error("Briefing attempt persistence failed: {0}")]
    Accounting(#[from] sqlx::Error),
    #[error("Briefing provider transport failed: {0}")]
    Transport(String),
    #[error("Briefing append plan lost pending row for {0}")]
    MissingPendingSource(String),
    #[error(
        "Briefing composition for lens {lens_key:?} failed after {attempts} attempts: {message}"
    )]
    Composition {
        lens_key: String,
        attempts: usize,
        message: String,
    },
}

impl BriefingRefreshExecutionError {
    fn retryable(&self) -> bool {
        matches!(self, Self::Accounting(_) | Self::Transport(_))
    }
}

#[cfg(test)]
mod retry_tests;

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn model_specs_split_only_on_the_first_colon() {
        assert_eq!(
            split_model_spec("openrouter:vendor/model:free"),
            ("openrouter", "vendor/model:free")
        );
        assert_eq!(split_model_spec("model"), ("unknown", "model"));
    }

    #[test]
    fn composition_source_preserves_the_owned_provider_boundary() {
        let source = BriefingRefreshSource {
            source_key: "news:4".to_owned(),
            kind: "news".to_owned(),
            id: 4,
            title: "Title".to_owned(),
            source_name: Some("Source".to_owned()),
            summary: Some("Summary".to_owned()),
            key_points: vec!["Point".to_owned()],
            url: Some("https://example.com".to_owned()),
            image_url: None,
            thumbnail_url: None,
            published_at: None,
            briefing_context: Some("Context".to_owned()),
        };
        let provider = composition_source(&source);
        assert_eq!(provider.source_key, "news:4");
        assert_eq!(provider.briefing_context.as_deref(), Some("Context"));
    }
}
