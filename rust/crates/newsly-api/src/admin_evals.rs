use std::collections::{BTreeMap, HashSet, VecDeque};
use std::env;
use std::fmt::Write as _;
use std::time::{Duration, Instant};

use axum::extract::rejection::JsonRejection;
use axum::extract::{Extension, OriginalUri, State};
use axum::http::{HeaderMap, StatusCode};
use axum::response::{Html, IntoResponse, Response};
use axum::routing::get;
use axum::{Json, Router};
use chrono::Utc;
use newsly_db::{AdminEvalCandidate, list_admin_eval_candidates};
use newsly_providers::{SummarizationGateway, SummarizationSource};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use sha2::{Digest, Sha256};
use utoipa::ToSchema;

use crate::admin_api_keys::{admin_login_redirect, escape_html, has_valid_admin_session};
use crate::error::ApiError;
use crate::gateway::RouteOwnershipStamp;
use crate::write_support::{decode_json, internal_error, require_operation, verify_stamp};
use crate::{AppState, request_id_from_headers};

const PAGE_OPERATION_ID: &str = "adminEvalSummariesPage";
const RUN_OPERATION_ID: &str = "adminEvalSummariesRun";
const EVAL_CALL_TIMEOUT: Duration = Duration::from_secs(15);
const MAX_EVAL_INPUT_CHARS: usize = 120_000;

const MODELS: [EvalModel; 4] = [
    EvalModel {
        alias: "cheap",
        label: "Cheap",
        model_spec: "openai:gpt-5.6-luna",
        credential_env: "OPENAI_API_KEY",
    },
    EvalModel {
        alias: "smart_openai",
        label: "Smart OpenAI",
        model_spec: "openai:gpt-5.6-terra",
        credential_env: "OPENAI_API_KEY",
    },
    EvalModel {
        alias: "smart_claude",
        label: "Smart Claude",
        model_spec: "anthropic:claude-opus-4-6",
        credential_env: "ANTHROPIC_API_KEY",
    },
    EvalModel {
        alias: "openrouter_deepseek_flash",
        label: "OpenRouter DeepSeek V4 Flash",
        model_spec: "openrouter:deepseek/deepseek-v4-flash",
        credential_env: "OPENROUTER_API_KEY",
    },
];

#[derive(Debug, Clone, Copy)]
struct EvalModel {
    alias: &'static str,
    label: &'static str,
    model_spec: &'static str,
    credential_env: &'static str,
}

#[derive(Debug, Clone, Default, Deserialize, Serialize, ToSchema)]
#[serde(deny_unknown_fields)]
struct ModelPricing {
    input_per_million_usd: Option<f64>,
    output_per_million_usd: Option<f64>,
}

#[derive(Debug, Clone, Deserialize, Serialize, ToSchema)]
#[serde(default, deny_unknown_fields)]
pub(super) struct AdminEvalRunRequest {
    content_types: Vec<String>,
    models: Vec<String>,
    longform_template: String,
    recent_pool_size: usize,
    sample_size: usize,
    seed: Option<i64>,
    pricing: BTreeMap<String, ModelPricing>,
}

impl Default for AdminEvalRunRequest {
    fn default() -> Self {
        Self {
            content_types: vec![
                "article".to_owned(),
                "podcast".to_owned(),
                "news".to_owned(),
            ],
            models: MODELS.iter().map(|model| model.alias.to_owned()).collect(),
            longform_template: "editorial_narrative_v1".to_owned(),
            recent_pool_size: 200,
            sample_size: 3,
            seed: None,
            pricing: BTreeMap::new(),
        }
    }
}

#[derive(Debug, Clone, Serialize, ToSchema)]
struct AdminEvalModelDescription {
    alias: String,
    label: String,
    model_spec: String,
}

#[derive(Debug, Clone, Serialize, ToSchema)]
struct AdminEvalSkippedModel {
    alias: String,
    reason: String,
}

#[derive(Debug, Clone, Serialize, ToSchema)]
struct AdminEvalSampleSummary {
    content_id: i64,
    created_at: String,
    url: String,
    source_title: Option<String>,
}

#[derive(Debug, Clone, Serialize, ToSchema)]
// These suffixes are stable wire names that distinguish token directions.
#[allow(clippy::struct_field_names)]
struct AdminEvalUsage {
    input_tokens: u64,
    output_tokens: u64,
    total_tokens: u64,
}

#[derive(Debug, Clone, Serialize, ToSchema)]
struct AdminEvalCell {
    model_alias: String,
    model_label: String,
    model_spec: String,
    status: String,
    error: Option<String>,
    latency_ms: u64,
    usage: AdminEvalUsage,
    estimated_cost_usd: Option<f64>,
    cost_reason: Option<String>,
    generated_title: Option<String>,
    title_chars: usize,
    request_chars: usize,
    request_tokens_estimate: usize,
    request_tokens_actual: Option<u64>,
    output_chars: usize,
    display_output: Option<Value>,
    raw_output: Option<Value>,
    prompt_type: String,
}

#[derive(Debug, Clone, Serialize, ToSchema)]
struct AdminEvalItemResult {
    content_id: i64,
    content_type: String,
    created_at: String,
    url: String,
    source_title: Option<String>,
    existing_summary_title: Option<String>,
    input_chars: usize,
    model_results: Vec<AdminEvalCell>,
}

#[derive(Debug, Clone, Serialize, ToSchema)]
struct AdminEvalConfigResponse {
    content_types: Vec<String>,
    models: Vec<String>,
    longform_template: String,
    recent_pool_size: usize,
    sample_size: usize,
    seed: Option<i64>,
}

#[derive(Debug, Clone, Serialize, ToSchema)]
struct AdminEvalAggregate {
    items_total: usize,
    cells_total: usize,
    cells_successful: usize,
    cells_failed: usize,
    avg_latency_ms: Option<f64>,
    avg_input_tokens: Option<f64>,
    avg_output_tokens: Option<f64>,
    avg_output_chars: Option<f64>,
    avg_request_chars: Option<f64>,
    avg_request_tokens_estimate: Option<f64>,
    avg_request_tokens_actual: Option<f64>,
    total_estimated_cost_usd: f64,
}

#[derive(Debug, Clone, Serialize, ToSchema)]
struct AdminEvalRunResponse {
    run_started_at: String,
    run_completed_at: String,
    config: AdminEvalConfigResponse,
    available_models: Vec<AdminEvalModelDescription>,
    skipped_models: Vec<AdminEvalSkippedModel>,
    samples_by_type: BTreeMap<String, Vec<AdminEvalSampleSummary>>,
    results: Vec<AdminEvalItemResult>,
    aggregate: AdminEvalAggregate,
}

#[derive(Debug, Clone)]
struct EvalSource {
    candidate: AdminEvalCandidate,
    text: String,
}

pub(super) fn router() -> Router<AppState> {
    Router::new()
        .route("/admin/evals/summaries", get(page))
        .route("/admin/evals/summaries/run", axum::routing::post(run))
}

#[utoipa::path(
    get,
    path = "/admin/evals/summaries",
    operation_id = "adminEvalSummariesPage",
    tag = "admin",
    responses(
        (status = 200, description = "Admin production-summary comparison page", content_type = "text/html", body = String),
        (status = 303, description = "Admin login required"),
        (status = 409, description = "Stale runtime owner", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn page(
    State(state): State<AppState>,
    OriginalUri(uri): OriginalUri,
    headers: HeaderMap,
    Extension(stamp): Extension<RouteOwnershipStamp>,
) -> Result<Response, ApiError> {
    if !has_valid_admin_session(&state, &headers) {
        return Ok(admin_login_redirect(uri.path()));
    }
    let request_id = request_id_from_headers(&headers);
    require_operation(&stamp, PAGE_OPERATION_ID, &request_id)?;
    Ok(Html(render_page()).into_response())
}

#[utoipa::path(
    post,
    path = "/admin/evals/summaries/run",
    operation_id = "adminEvalSummariesRun",
    tag = "admin",
    request_body = AdminEvalRunRequest,
    responses(
        (status = 200, description = "Completed bounded production-summary comparison", body = AdminEvalRunResponse),
        (status = 303, description = "Admin login required"),
        (status = 409, description = "Stale runtime owner", body = newsly_contracts::ErrorEnvelope),
        (status = 422, description = "Validation error", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn run(
    State(state): State<AppState>,
    OriginalUri(uri): OriginalUri,
    headers: HeaderMap,
    Extension(stamp): Extension<RouteOwnershipStamp>,
    payload: Result<Json<AdminEvalRunRequest>, JsonRejection>,
) -> Result<Response, ApiError> {
    if !has_valid_admin_session(&state, &headers) {
        return Ok(admin_login_redirect(uri.path()));
    }
    let request_id = request_id_from_headers(&headers);
    require_operation(&stamp, RUN_OPERATION_ID, &request_id)?;
    let Json(mut request) = decode_json(payload, &request_id)?;
    validate_request(&mut request, &request_id)?;

    // The endpoint is classified as a write for cutover/drain purposes even though comparison
    // results are ephemeral. Verify its owner in a short transaction, then release the connection
    // before object-store and model-provider calls.
    let mut transaction = state
        .database
        .pool()
        .begin()
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    verify_stamp(&mut transaction, &stamp, &request_id).await?;
    transaction
        .commit()
        .await
        .map_err(|error| internal_error(error, &request_id))?;

    let started_at = Utc::now();
    let candidates = list_admin_eval_candidates(
        state.database.pool(),
        &request.content_types,
        i64::try_from(request.recent_pool_size).unwrap_or(2_000),
    )
    .await
    .map_err(|error| internal_error(error, &request_id))?;
    let sources = load_balanced_sources(&state, &request, candidates, &request_id).await;
    let (models, mut skipped_models) = resolve_models(&request);
    let available_models = models
        .iter()
        .map(|(model, _)| AdminEvalModelDescription {
            alias: model.alias.to_owned(),
            label: model.label.to_owned(),
            model_spec: model.model_spec.to_owned(),
        })
        .collect::<Vec<_>>();

    let mut disabled = HashSet::new();
    let mut results = Vec::with_capacity(sources.len());
    for source in &sources {
        let mut model_results = Vec::new();
        for (model, gateway) in &models {
            if disabled.contains(model.alias) {
                continue;
            }
            let cell = run_cell(source, *model, gateway, request.pricing.get(model.alias)).await;
            if cell.status == "error" && cell.error.as_deref().is_some_and(is_hard_provider_error) {
                disabled.insert(model.alias);
                skipped_models.push(AdminEvalSkippedModel {
                    alias: model.alias.to_owned(),
                    reason: format!(
                        "disabled_after_error: {}",
                        cell.error.as_deref().unwrap_or("provider error")
                    )
                    .chars()
                    .take(220)
                    .collect(),
                });
            }
            model_results.push(cell);
        }
        results.push(AdminEvalItemResult {
            content_id: source.candidate.content_id,
            content_type: source.candidate.content_type.clone(),
            created_at: source.candidate.created_at.and_utc().to_rfc3339(),
            url: source.candidate.url.clone(),
            source_title: source.candidate.source_title.clone(),
            existing_summary_title: existing_summary_title(&source.candidate.content_metadata),
            input_chars: source.text.chars().count(),
            model_results,
        });
    }

    let response = AdminEvalRunResponse {
        run_started_at: started_at.to_rfc3339(),
        run_completed_at: Utc::now().to_rfc3339(),
        config: AdminEvalConfigResponse {
            content_types: request.content_types.clone(),
            models: request.models.clone(),
            longform_template: request.longform_template.clone(),
            recent_pool_size: request.recent_pool_size,
            sample_size: request.sample_size,
            seed: request.seed,
        },
        available_models,
        skipped_models,
        samples_by_type: sample_summary(&sources, &request.content_types),
        aggregate: aggregate(&results),
        results,
    };
    Ok(Json(response).into_response())
}

fn validate_request(request: &mut AdminEvalRunRequest, request_id: &str) -> Result<(), ApiError> {
    dedupe(&mut request.content_types);
    dedupe(&mut request.models);
    let allowed_types = ["article", "podcast", "news"];
    if request.content_types.is_empty()
        || request
            .content_types
            .iter()
            .any(|value| !allowed_types.contains(&value.as_str()))
    {
        return Err(validation_error(
            "content_types must contain article, podcast, or news",
            request_id,
        ));
    }
    if request.models.is_empty()
        || request
            .models
            .iter()
            .any(|alias| MODELS.iter().all(|model| model.alias != alias))
    {
        return Err(validation_error(
            "models contains an unknown alias",
            request_id,
        ));
    }
    if !(10..=2_000).contains(&request.recent_pool_size) {
        return Err(validation_error(
            "recent_pool_size must be between 10 and 2000",
            request_id,
        ));
    }
    if !(1..=100).contains(&request.sample_size) || request.sample_size > request.recent_pool_size {
        return Err(validation_error(
            "sample_size must be between 1 and 100 and no larger than recent_pool_size",
            request_id,
        ));
    }
    if !matches!(
        request.longform_template.as_str(),
        "long_bullets_v1" | "interleaved_v2" | "structured_v1" | "editorial_narrative_v1"
    ) {
        return Err(validation_error("unknown longform_template", request_id));
    }
    if request.pricing.values().any(|pricing| {
        [
            pricing.input_per_million_usd,
            pricing.output_per_million_usd,
        ]
        .into_iter()
        .flatten()
        .any(|value| !value.is_finite() || value < 0.0)
    }) {
        return Err(validation_error(
            "pricing values must be finite and nonnegative",
            request_id,
        ));
    }
    Ok(())
}

fn dedupe(values: &mut Vec<String>) {
    let mut seen = HashSet::new();
    values.retain(|value| seen.insert(value.clone()));
}

fn resolve_models(
    request: &AdminEvalRunRequest,
) -> (
    Vec<(EvalModel, SummarizationGateway)>,
    Vec<AdminEvalSkippedModel>,
) {
    let mut available = Vec::new();
    let mut skipped = Vec::new();
    for alias in &request.models {
        let model = MODELS
            .iter()
            .copied()
            .find(|model| model.alias == alias)
            .expect("request aliases were validated");
        if env::var(model.credential_env)
            .ok()
            .is_none_or(|value| value.trim().is_empty())
        {
            skipped.push(AdminEvalSkippedModel {
                alias: alias.clone(),
                reason: format!("{} not configured", model.credential_env),
            });
            continue;
        }
        match SummarizationGateway::from_env_for_model(model.model_spec, EVAL_CALL_TIMEOUT) {
            Ok(gateway) => available.push((model, gateway)),
            Err(error) => skipped.push(AdminEvalSkippedModel {
                alias: alias.clone(),
                reason: format!("model unavailable: {error}"),
            }),
        }
    }
    (available, skipped)
}

async fn load_balanced_sources(
    state: &AppState,
    request: &AdminEvalRunRequest,
    candidates: Vec<AdminEvalCandidate>,
    request_id: &str,
) -> Vec<EvalSource> {
    let mut pools = BTreeMap::<String, Vec<AdminEvalCandidate>>::new();
    for candidate in candidates {
        pools
            .entry(candidate.content_type.clone())
            .or_default()
            .push(candidate);
    }
    for (content_type, pool) in &mut pools {
        pool.sort_by_key(|candidate| sample_key(request.seed, content_type, candidate.content_id));
    }
    let mut queues = pools
        .into_iter()
        .map(|(key, rows)| (key, VecDeque::from(rows)))
        .collect::<BTreeMap<_, _>>();
    let mut selected = Vec::with_capacity(request.sample_size);
    while selected.len() < request.sample_size {
        let mut progressed = false;
        for content_type in &request.content_types {
            let Some(pool) = queues.get_mut(content_type) else {
                continue;
            };
            while let Some(candidate) = pool.pop_front() {
                progressed = true;
                match source_text(state, &candidate).await {
                    Ok(Some(text)) => {
                        selected.push(EvalSource { candidate, text });
                        break;
                    }
                    Ok(None) => {}
                    Err(error) => tracing::warn!(
                        error = %error,
                        content_id = candidate.content_id,
                        request_id,
                        "admin eval skipped an unreadable content body"
                    ),
                }
            }
            if selected.len() >= request.sample_size {
                break;
            }
        }
        if !progressed {
            break;
        }
    }
    selected
}

async fn source_text(
    state: &AppState,
    candidate: &AdminEvalCandidate,
) -> Result<Option<String>, crate::content_body_storage::ContentBodyStoreError> {
    let stored = if let Some(key) = candidate.storage_key.as_deref() {
        state.content_body_store.get_text(key).await?
    } else {
        None
    };
    let fallback = fallback_source_text(&candidate.content_type, &candidate.content_metadata);
    Ok(stored
        .or(fallback)
        .filter(|text| !text.trim().is_empty())
        .map(|text| clip_text(&text, MAX_EVAL_INPUT_CHARS)))
}

fn fallback_source_text(content_type: &str, metadata: &Value) -> Option<String> {
    let object = metadata.as_object()?;
    let keys = if content_type == "podcast" {
        ["transcript", "content_to_summarize", "content"]
    } else {
        ["content_to_summarize", "content", "transcript"]
    };
    keys.into_iter().find_map(|key| {
        object
            .get(key)
            .and_then(Value::as_str)
            .map(str::trim)
            .filter(|value| !value.is_empty())
            .map(str::to_owned)
    })
}

fn sample_key(seed: Option<i64>, content_type: &str, content_id: i64) -> [u8; 32] {
    let mut digest = Sha256::new();
    digest.update(seed.unwrap_or_default().to_be_bytes());
    digest.update(content_type.as_bytes());
    digest.update(content_id.to_be_bytes());
    digest.finalize().into()
}

async fn run_cell(
    source: &EvalSource,
    model: EvalModel,
    gateway: &SummarizationGateway,
    pricing: Option<&ModelPricing>,
) -> AdminEvalCell {
    let request_chars = source.text.chars().count();
    let request_tokens_estimate = request_chars.div_ceil(4);
    let started = Instant::now();
    let provider_source = SummarizationSource {
        content_id: source.candidate.content_id,
        content_type: source.candidate.content_type.clone(),
        title: source.candidate.source_title.clone(),
        url: source.candidate.url.clone(),
        source_name: source.candidate.source_name.clone(),
        platform: source.candidate.platform.clone(),
        publication_date: source
            .candidate
            .publication_date
            .map(|value| value.and_utc().to_rfc3339()),
        metadata: source.candidate.content_metadata.clone(),
        text: source.text.clone(),
    };
    match gateway.summarize(&provider_source).await {
        Ok(output) => {
            let latency_ms = duration_millis(started.elapsed());
            let generated_title = Some(output.summary.title.clone());
            let output_chars = serde_json::to_string(&output.summary_json)
                .unwrap_or_default()
                .chars()
                .count();
            let estimated_cost_usd = estimate_cost(
                output.usage.input_tokens,
                output.usage.output_tokens,
                pricing,
            );
            AdminEvalCell {
                model_alias: model.alias.to_owned(),
                model_label: model.label.to_owned(),
                model_spec: model.model_spec.to_owned(),
                status: "ok".to_owned(),
                error: None,
                latency_ms,
                usage: AdminEvalUsage {
                    input_tokens: output.usage.input_tokens,
                    output_tokens: output.usage.output_tokens,
                    total_tokens: output
                        .usage
                        .input_tokens
                        .saturating_add(output.usage.output_tokens),
                },
                estimated_cost_usd,
                cost_reason: estimated_cost_usd
                    .is_none()
                    .then_some("pricing_not_configured".to_owned()),
                generated_title: generated_title.clone(),
                title_chars: generated_title.as_deref().map_or(0, str::len),
                request_chars,
                request_tokens_estimate,
                request_tokens_actual: Some(output.usage.input_tokens),
                output_chars,
                display_output: Some(output.summary_json.clone()),
                raw_output: Some(output.summary_json),
                prompt_type: "production_longform_artifact_v1".to_owned(),
            }
        }
        Err(error) => AdminEvalCell {
            model_alias: model.alias.to_owned(),
            model_label: model.label.to_owned(),
            model_spec: model.model_spec.to_owned(),
            status: "error".to_owned(),
            error: Some(error.to_string()),
            latency_ms: duration_millis(started.elapsed()),
            usage: AdminEvalUsage {
                input_tokens: 0,
                output_tokens: 0,
                total_tokens: 0,
            },
            estimated_cost_usd: None,
            cost_reason: Some("error".to_owned()),
            generated_title: None,
            title_chars: 0,
            request_chars,
            request_tokens_estimate,
            request_tokens_actual: None,
            output_chars: 0,
            display_output: None,
            raw_output: None,
            prompt_type: "production_longform_artifact_v1".to_owned(),
        },
    }
}

fn estimate_cost(input: u64, output: u64, pricing: Option<&ModelPricing>) -> Option<f64> {
    let pricing = pricing?;
    let input_rate = pricing.input_per_million_usd?;
    let output_rate = pricing.output_per_million_usd?;
    Some(
        (u64_metric(input) / 1_000_000.0) * input_rate
            + (u64_metric(output) / 1_000_000.0) * output_rate,
    )
}

fn aggregate(items: &[AdminEvalItemResult]) -> AdminEvalAggregate {
    let cells = items
        .iter()
        .flat_map(|item| item.model_results.iter())
        .collect::<Vec<_>>();
    let successful = cells
        .iter()
        .copied()
        .filter(|cell| cell.status == "ok")
        .collect::<Vec<_>>();
    AdminEvalAggregate {
        items_total: items.len(),
        cells_total: cells.len(),
        cells_successful: successful.len(),
        cells_failed: cells.len().saturating_sub(successful.len()),
        avg_latency_ms: average(successful.iter().map(|cell| u64_metric(cell.latency_ms))),
        avg_input_tokens: average(
            successful
                .iter()
                .map(|cell| u64_metric(cell.usage.input_tokens)),
        ),
        avg_output_tokens: average(
            successful
                .iter()
                .map(|cell| u64_metric(cell.usage.output_tokens)),
        ),
        avg_output_chars: average(
            successful
                .iter()
                .map(|cell| usize_metric(cell.output_chars)),
        ),
        avg_request_chars: average(
            successful
                .iter()
                .map(|cell| usize_metric(cell.request_chars)),
        ),
        avg_request_tokens_estimate: average(
            successful
                .iter()
                .map(|cell| usize_metric(cell.request_tokens_estimate)),
        ),
        avg_request_tokens_actual: average(
            successful
                .iter()
                .filter_map(|cell| cell.request_tokens_actual.map(u64_metric)),
        ),
        total_estimated_cost_usd: successful
            .iter()
            .filter_map(|cell| cell.estimated_cost_usd)
            .sum(),
    }
}

fn average(values: impl Iterator<Item = f64>) -> Option<f64> {
    let values = values.collect::<Vec<_>>();
    (!values.is_empty()).then(|| values.iter().sum::<f64>() / usize_metric(values.len()))
}

// Eval counts are bounded display metrics; sub-unit precision above 2^53 is irrelevant.
#[allow(clippy::cast_precision_loss)]
fn u64_metric(value: u64) -> f64 {
    value as f64
}

// Eval collections and request bodies are bounded well below exact f64 integer precision.
#[allow(clippy::cast_precision_loss)]
fn usize_metric(value: usize) -> f64 {
    value as f64
}

fn sample_summary(
    sources: &[EvalSource],
    content_types: &[String],
) -> BTreeMap<String, Vec<AdminEvalSampleSummary>> {
    content_types
        .iter()
        .map(|content_type| {
            let samples = sources
                .iter()
                .filter(|source| source.candidate.content_type == *content_type)
                .map(|source| AdminEvalSampleSummary {
                    content_id: source.candidate.content_id,
                    created_at: source.candidate.created_at.and_utc().to_rfc3339(),
                    url: source.candidate.url.clone(),
                    source_title: source.candidate.source_title.clone(),
                })
                .collect();
            (content_type.clone(), samples)
        })
        .collect()
}

fn existing_summary_title(metadata: &Value) -> Option<String> {
    metadata
        .get("summary")
        .and_then(|summary| summary.get("title"))
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(str::to_owned)
}

fn is_hard_provider_error(message: &str) -> bool {
    let lowered = message.to_ascii_lowercase();
    [
        "status 400",
        "status 401",
        "status 403",
        "status 404",
        "timed out",
        "timeout",
        "model_not_found",
        "authentication",
        "permission",
    ]
    .iter()
    .any(|marker| lowered.contains(marker))
}

fn clip_text(text: &str, max_chars: usize) -> String {
    const MARKER: &str = "\n\n[... CONTENT TRUNCATED FOR EVAL ...]\n\n";

    if text.chars().count() <= max_chars {
        return text.to_owned();
    }
    let remaining = max_chars.saturating_sub(MARKER.chars().count());
    let head_count = remaining / 2;
    let tail_count = remaining.saturating_sub(head_count);
    let head = text.chars().take(head_count).collect::<String>();
    let tail = text
        .chars()
        .rev()
        .take(tail_count)
        .collect::<String>()
        .chars()
        .rev()
        .collect::<String>();
    format!("{}{MARKER}{}", head.trim_end(), tail.trim_start())
}

fn duration_millis(duration: Duration) -> u64 {
    u64::try_from(duration.as_millis()).unwrap_or(u64::MAX)
}

fn validation_error(message: impl Into<String>, request_id: &str) -> ApiError {
    ApiError::new(
        StatusCode::UNPROCESSABLE_ENTITY,
        "validation_error",
        message,
        request_id.to_owned(),
    )
}

fn render_page() -> String {
    let mut model_options = String::new();
    for model in MODELS {
        let _ = write!(
            model_options,
            "<label><input type=\"checkbox\" class=\"model\" value=\"{}\" checked> {} <small>{}</small></label>",
            escape_html(model.alias),
            escape_html(model.label),
            escape_html(model.model_spec),
        );
    }
    format!(
        r#"<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Summary Eval</title><style>
body{{font:15px system-ui;margin:0;background:#f7f7f5;color:#191919}}main{{max-width:1000px;margin:2rem auto;padding:0 1rem}}section,pre{{background:white;border:1px solid #ddd;border-radius:10px;padding:1rem}}label{{display:block;margin:.5rem 0}}small{{color:#777;font-family:monospace}}input,button{{font:inherit;padding:.45rem}}button{{margin-top:1rem}}pre{{white-space:pre-wrap;overflow:auto;max-height:65vh}}
</style></head><body><main><nav><a href="/admin/">Dashboard</a> · <a href="/admin/evals/summaries">Evals</a></nav><h1>Production summary comparison</h1><p>This runs the exact Rust long-form artifact contract. Offline Python remains the owner of dataset construction and embedding/model experiment pipelines.</p><section>
<strong>Content types</strong><label><input type="checkbox" class="type" value="article" checked> Article</label><label><input type="checkbox" class="type" value="podcast" checked> Podcast</label><label><input type="checkbox" class="type" value="news" checked> News</label><strong>Models</strong>{model_options}<label>Recent pool <input id="pool" type="number" min="10" max="2000" value="200"></label><label>Sample size <input id="sample" type="number" min="1" max="100" value="3"></label><label>Seed <input id="seed" type="number"></label><button id="run">Run comparison</button> <span id="status"></span></section><h2>Result</h2><pre id="result">No run yet.</pre></main><script>
const selected=(selector)=>[...document.querySelectorAll(selector)].filter(x=>x.checked).map(x=>x.value);
document.getElementById('run').onclick=async()=>{{const button=document.getElementById('run');const status=document.getElementById('status');button.disabled=true;status.textContent='Running…';try{{const seed=document.getElementById('seed').value;const response=await fetch('/admin/evals/summaries/run',{{method:'POST',headers:{{'content-type':'application/json'}},body:JSON.stringify({{content_types:selected('.type'),models:selected('.model'),longform_template:'editorial_narrative_v1',recent_pool_size:Number(document.getElementById('pool').value),sample_size:Number(document.getElementById('sample').value),seed:seed?Number(seed):null,pricing:{{}}}})}});const payload=await response.json();document.getElementById('result').textContent=JSON.stringify(payload,null,2);status.textContent=response.ok?'Done':'Failed';}}catch(error){{status.textContent='Failed';document.getElementById('result').textContent=String(error);}}finally{{button.disabled=false;}}}};
</script></body></html>"#
    )
}
