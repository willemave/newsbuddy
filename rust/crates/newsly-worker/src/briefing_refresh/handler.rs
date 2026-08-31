use std::collections::HashMap;
use std::sync::Arc;
use std::time::Instant;

use chrono::Utc;
use futures_util::stream::{self, StreamExt};
use newsly_db::{
    ApplyBriefingLensAssignmentOutcome, BriefingEmbeddingUsage, BriefingPendingIdentity,
    BriefingRefreshLens, BriefingRefreshMode, BriefingRefreshPublication, BriefingRefreshSource,
    BriefingSegmentUsage, ComposedBriefingAppend, ComposedBriefingCompaction,
    ComposedBriefingSegment, PrepareBriefingRefreshOutcome, PreparedBriefingRefresh,
    apply_briefing_lens_assignment, prepare_briefing_refresh,
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

const PROMPT_VERSION: &str = "briefing-v6";

#[derive(Debug, Clone)]
pub struct BriefingRefreshWorkerServices {
    pool: PgPool,
    queue: QueueKernel,
    gateway: BriefingCompositionGateway,
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
            &services.config.repository,
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
    let lens_plan = {
        let lens_planning = plan_semantic_lenses(
            &services.gateway,
            &seed,
            &services.config.repository,
            services.config.embedding_batch_size,
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
    if lease.ownership_lost() {
        return plain_failure("queue lease was lost after Briefing lens planning", true);
    }
    let prepared = {
        let mut transaction = match services.pool.begin().await {
            Ok(transaction) => transaction,
            Err(error) => return plain_failure(error.to_string(), true),
        };
        let outcome = match apply_briefing_lens_assignment(
            &mut transaction,
            seed,
            &lens_plan,
            &services.config.repository,
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
        if let Err(error) = transaction.commit().await {
            return plain_failure(error.to_string(), true);
        }
        prepared
    };
    if lease.ownership_lost() {
        return plain_failure("queue lease was lost before Briefing composition", true);
    }

    let external = build_publication(services, prepared);
    tokio::pin!(external);
    let publication = tokio::select! {
        result = &mut external => result,
        () = lease.wait_for_ownership_loss() => {
            return plain_failure("queue lease was lost during Briefing composition", true);
        }
    };
    let publication = match publication {
        Ok(publication) => publication,
        Err(error) => return plain_failure(error.to_string(), true),
    };
    if lease.ownership_lost() {
        return plain_failure("queue lease was lost after Briefing composition", true);
    }
    HandlerExecution::with_finalizer(
        TaskResult::ok(),
        BriefingRefreshFinalizer::new(
            services.queue.clone(),
            publication,
            services.config.repository.clone(),
        ),
    )
}

async fn build_publication(
    services: &BriefingRefreshWorkerServices,
    prepared: PreparedBriefingRefresh,
) -> Result<BriefingRefreshPublication, BriefingRefreshExecutionError> {
    let mut units = Vec::new();
    let mut embedding_usage = Vec::new();
    let mut ordinal = 0_usize;
    let mut compaction_selected = vec![false; prepared.compaction_batches.len()];

    for batch in &prepared.append_batches {
        let planned = plan_windows(
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
                provider: "openrouter".to_owned(),
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
            units.push(CompositionUnit {
                ordinal,
                lens: batch.lens.clone(),
                window,
                kind: CompositionUnitKind::Append { pending_rows },
            });
            ordinal += 1;
        }
    }

    for (batch_index, batch) in prepared.compaction_batches.iter().enumerate() {
        let planned = plan_windows(
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
                provider: "openrouter".to_owned(),
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
    let max_attempts = services.config.max_compose_attempts;
    let max_figures_deep = services.config.max_figures_deep;
    let mut composed = stream::iter(units.into_iter().map(|unit| async move {
        compose_unit(gateway, unit, max_attempts, max_figures_deep).await
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

async fn compose_unit(
    gateway: &BriefingCompositionGateway,
    unit: CompositionUnit,
    max_attempts: usize,
    max_figures_deep: usize,
) -> Result<ComposedUnit, BriefingRefreshExecutionError> {
    let started = Instant::now();
    let request = BriefingCompositionRequest {
        lens_title: unit.lens.title.clone(),
        tier: unit.lens.tier.clone(),
        sources: unit.window.sources.iter().map(composition_source).collect(),
    };
    let mut warnings = Vec::new();
    for attempt in 1..=max_attempts {
        let generated = match gateway.compose(&request).await {
            Ok(generated) => generated,
            Err(error) if attempt < max_attempts => {
                warnings.push(format!("llm_error_retry:{attempt}"));
                tracing::warn!(
                    lens_key = %unit.lens.key,
                    tier = %unit.lens.tier,
                    source_count = unit.window.sources.len(),
                    attempt,
                    error = %error,
                    "Briefing composition failed; retrying the complete window"
                );
                continue;
            }
            Err(error) => {
                return Err(BriefingRefreshExecutionError::Composition {
                    lens_key: unit.lens.key.clone(),
                    attempts: attempt,
                    message: error.to_string(),
                });
            }
        };
        let figure_budget = if unit.lens.tier == "news" {
            0
        } else {
            max_figures_deep
        };
        let normalized = match normalize_layout(
            &generated.layout,
            &unit.window.sources,
            &unit.lens.tier,
            figure_budget,
        ) {
            Ok(normalized) => normalized,
            Err(error) if attempt < max_attempts => {
                warnings.push(format!("llm_layout_policy_retry:{attempt}"));
                tracing::warn!(
                    lens_key = %unit.lens.key,
                    tier = %unit.lens.tier,
                    attempt,
                    error = %error,
                    "Briefing normalized layout failed policy; regenerating the window"
                );
                continue;
            }
            Err(error) => {
                return Err(BriefingRefreshExecutionError::Composition {
                    lens_key: unit.lens.key.clone(),
                    attempts: attempt,
                    message: error.to_string(),
                });
            }
        };
        warnings.extend(normalized.warnings);
        if matches!(&unit.kind, CompositionUnitKind::Compaction { .. }) {
            warnings.push("compaction_segment".to_owned());
        }
        let (provider, fallback_model) = split_model_spec(gateway.model_spec());
        let model = nonempty(&generated.model).unwrap_or_else(|| fallback_model.to_owned());
        let input_tokens = Some(u64_to_i32(generated.usage.input_tokens));
        let output_tokens = Some(u64_to_i32(generated.usage.output_tokens));
        let generation_ms = i32::try_from(started.elapsed().as_millis()).unwrap_or(i32::MAX);
        let usage = BriefingSegmentUsage {
            provider: provider.to_owned(),
            model: model.clone(),
            provider_response_id: generated.provider_response_id,
            usage: generated.usage,
            operation: "briefing.compose_window".to_owned(),
        };
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
                prompt_version: PROMPT_VERSION.to_owned(),
                input_tokens,
                output_tokens,
                generation_ms,
                warnings,
                usage,
            },
        });
    }
    Err(BriefingRefreshExecutionError::Composition {
        lens_key: unit.lens.key,
        attempts: max_attempts,
        message: "composition attempt budget was empty".to_owned(),
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

fn split_model_spec(value: &str) -> (&str, &str) {
    value.split_once(':').unwrap_or(("unknown", value))
}

fn nonempty(value: &str) -> Option<String> {
    let value = value.trim();
    (!value.is_empty()).then(|| value.to_owned())
}

fn u64_to_i32(value: u64) -> i32 {
    i32::try_from(value).unwrap_or(i32::MAX)
}

fn plain_failure(message: impl Into<String>, retryable: bool) -> HandlerExecution {
    HandlerExecution::from_result(TaskResult::fail(Some(message.into()), retryable))
}

#[derive(Debug, Error)]
enum BriefingRefreshExecutionError {
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
