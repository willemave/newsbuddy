use newsly_providers::{AnalyzedContentType, GeneratedContentAnalysis, InstructionLink};
use newsly_queue::{OwnedWorkPlan, TaskResult};
use serde_json::{Map, Value};

use crate::{HandlerExecution, LeaseHealth};

use super::super::extraction::ExtractionAttempt;
use super::super::model::{
    ContentFinalizationPlan, ContentMutation, InstructionLinkPlan, ModelUsageWrite, UsageWrite,
};
use super::ContentWorkerServices;
use super::support::{
    classify_known_url, content_id, extraction_deadline, extraction_failure, is_tweet_url,
    payload_bool, payload_string, request_id, should_run_structured_analysis, storage_failure,
    with_finalizer,
};
use super::tweet::{execute_tweet_analysis, normalized_external_url};
use crate::content::repository::load_content_snapshot;

#[allow(clippy::too_many_lines)]
pub(super) async fn execute_analyze_url(
    services: &ContentWorkerServices,
    plan: &OwnedWorkPlan,
    mut lease: LeaseHealth,
) -> HandlerExecution {
    let Some(content_id) = content_id(plan) else {
        return HandlerExecution::from_result(TaskResult::fail(
            Some("analyze_url requires a positive content_id".to_owned()),
            false,
        ));
    };
    let snapshot = match load_content_snapshot(&services.pool, content_id).await {
        Ok(Some(snapshot)) => snapshot,
        Ok(None) => {
            return HandlerExecution::from_result(TaskResult::fail(
                Some(format!("content {content_id} does not exist")),
                false,
            ));
        }
        Err(error) => {
            return HandlerExecution::from_result(TaskResult::fail(Some(error.to_string()), true));
        }
    };
    let subscribe_to_feed = payload_bool(&plan.payload, "subscribe_to_feed");
    if snapshot.is_terminal() {
        return HandlerExecution::from_result(TaskResult::ok());
    }

    let scrub_instruction = plan.payload.contains_key("instruction");
    let instruction = payload_string(&plan.payload, "instruction");
    let crawl_links = payload_bool(&plan.payload, "crawl_links");
    let analysis_instruction =
        instruction.or(crawl_links.then_some("Extract relevant links from the submitted page."));
    if is_tweet_url(&snapshot.url) {
        return execute_tweet_analysis(services, plan, &snapshot, content_id, lease).await;
    }

    if !should_run_structured_analysis(&snapshot, analysis_instruction)
        && let Some(classification) = classify_known_url(&snapshot)
    {
        let finalization = ContentFinalizationPlan {
            task_id: plan.task_id,
            content_id,
            mutation: ContentMutation::AnalyzeClassified {
                content_type: classification.content_type,
                platform: classification.platform,
                metadata_updates: classification.metadata_updates,
                subscribe_to_feed,
                scrub_instruction,
            },
            usage: Vec::new(),
        };
        return with_finalizer(services, TaskResult::ok(), finalization);
    }

    let deadline = extraction_deadline(services.extraction_timeout);
    let request_id = request_id(plan);
    let extraction = services
        .extraction
        .analyze(&snapshot.url, &request_id, deadline);
    tokio::pin!(extraction);
    let attempt = tokio::select! {
        attempt = &mut extraction => attempt,
        () = lease.wait_for_ownership_loss() => {
            return HandlerExecution::from_result(TaskResult::fail(
                Some("lease ownership was lost during URL analysis".to_owned()),
                true,
            ));
        }
    };

    match attempt {
        ExtractionAttempt::Success { article, mut usage } => {
            let mut content_type = "article".to_owned();
            let mut platform = None;
            let mut title = article.title.clone();
            let mut metadata_updates = Map::new();
            let mut instruction_links = Vec::new();
            let analysis = services.content_analysis.analyze(
                &snapshot.url,
                &article.body,
                analysis_instruction,
            );
            tokio::pin!(analysis);
            match tokio::select! {
                result = &mut analysis => Some(result),
                () = lease.wait_for_ownership_loss() => None,
            } {
                None => {
                    return HandlerExecution::from_result(TaskResult::fail(
                        Some("lease ownership was lost during structured URL analysis".to_owned()),
                        true,
                    ));
                }
                Some(Ok(generated)) => {
                    apply_generated_analysis(
                        &generated,
                        &snapshot.url,
                        &mut content_type,
                        &mut platform,
                        &mut title,
                        &mut metadata_updates,
                        &mut instruction_links,
                        crawl_links,
                    );
                    usage.push(UsageWrite::Model(model_usage(&generated)));
                }
                Some(Err(error)) => {
                    tracing::warn!(
                        content_id,
                        error = %error,
                        "structured content analysis failed; preserving the extracted article"
                    );
                }
            }
            let body = match services
                .body_store
                .stage_source(content_id, &article.body)
                .await
            {
                Ok(body) => body,
                Err(error) => {
                    return storage_failure(
                        services,
                        plan,
                        content_id,
                        "analyze_url",
                        &error,
                        usage,
                    );
                }
            };
            let body_char_count = article.body.chars().count();
            let finalization = ContentFinalizationPlan {
                task_id: plan.task_id,
                content_id,
                mutation: ContentMutation::AnalyzeSuccess {
                    content_type,
                    platform,
                    title,
                    body,
                    body_char_count,
                    feed_candidates: article.feed_candidates,
                    extraction_method: article.extraction_method,
                    warnings: article.warnings,
                    timings: article.timings,
                    metadata_updates,
                    instruction_links,
                    subscribe_to_feed,
                    scrub_instruction,
                },
                usage,
            };
            with_finalizer(services, TaskResult::ok(), finalization)
        }
        ExtractionAttempt::Failure {
            reason,
            code,
            retryable,
            usage,
        } => extraction_failure(
            services,
            plan,
            content_id,
            "analyze_url",
            reason,
            code,
            retryable,
            usage,
        ),
    }
}

fn model_usage(generated: &GeneratedContentAnalysis) -> ModelUsageWrite {
    let (provider, model) = generated.model.split_once(':').map_or_else(
        || ("openai".to_owned(), generated.model.clone()),
        |(provider, model)| (provider.to_owned(), model.to_owned()),
    );
    ModelUsageWrite {
        provider,
        model,
        response_id: generated.provider_response_id.clone(),
        usage: generated.usage.clone(),
    }
}

#[allow(clippy::too_many_arguments)]
fn apply_generated_analysis(
    generated: &GeneratedContentAnalysis,
    original_url: &str,
    content_type: &mut String,
    platform: &mut Option<String>,
    title: &mut String,
    metadata_updates: &mut Map<String, Value>,
    instruction_links: &mut Vec<InstructionLinkPlan>,
    collect_instruction_links: bool,
) {
    match generated.analysis.content_type {
        AnalyzedContentType::Article => "article",
        AnalyzedContentType::Podcast | AnalyzedContentType::Video => "podcast",
    }
    .clone_into(content_type);
    if let Some(value) = normalized_optional(generated.analysis.platform.as_deref()) {
        *platform = Some(value.clone());
        metadata_updates.insert("platform".to_owned(), Value::String(value));
    }
    if let Some(value) = normalized_optional(generated.analysis.media_url.as_deref()) {
        metadata_updates.insert("audio_url".to_owned(), Value::String(value));
    }
    if let Some(value) = normalized_optional(generated.analysis.media_format.as_deref()) {
        metadata_updates.insert("media_format".to_owned(), Value::String(value));
    }
    if let Some(value) = normalized_optional(generated.analysis.title.as_deref()) {
        metadata_updates.insert("extracted_title".to_owned(), Value::String(value.clone()));
        *title = value;
    }
    if let Some(value) = normalized_optional(generated.analysis.description.as_deref()) {
        metadata_updates.insert("extracted_description".to_owned(), Value::String(value));
    }
    if let Some(value) = generated
        .analysis
        .duration_seconds
        .filter(|value| *value > 0)
    {
        metadata_updates.insert("duration".to_owned(), Value::from(value));
    }
    if generated.analysis.content_type == AnalyzedContentType::Video {
        metadata_updates.insert("is_video".to_owned(), Value::Bool(true));
        metadata_updates.insert(
            "video_url".to_owned(),
            Value::String(original_url.to_owned()),
        );
    }
    if generated.analysis.platform.as_deref() == Some("youtube")
        && content_type.as_str() == "podcast"
    {
        metadata_updates
            .entry("audio_url".to_owned())
            .or_insert_with(|| Value::String(original_url.to_owned()));
        metadata_updates
            .entry("video_url".to_owned())
            .or_insert_with(|| Value::String(original_url.to_owned()));
        metadata_updates
            .entry("youtube_video".to_owned())
            .or_insert(Value::Bool(true));
    }
    if collect_instruction_links {
        *instruction_links = generated
            .instruction
            .as_ref()
            .map_or(&[][..], |result| result.links.as_slice())
            .iter()
            .filter_map(|link| instruction_link_plan(link, original_url))
            .take(50)
            .collect();
    }
}

pub(super) fn instruction_link_plan(
    link: &InstructionLink,
    original_url: &str,
) -> Option<InstructionLinkPlan> {
    let normalized = normalized_external_url(&link.url)?;
    if normalized == normalized_external_url(original_url)? {
        return None;
    }
    Some(InstructionLinkPlan {
        url: normalized,
        title: normalized_optional(link.title.as_deref()),
        context: normalized_optional(link.context.as_deref()),
        content_type: normalized_optional(link.content_type.as_deref()),
        platform: normalized_optional(link.platform.as_deref()),
        source: normalized_optional(link.source.as_deref()),
    })
}

pub(super) fn normalized_optional(value: Option<&str>) -> Option<String> {
    value
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(str::to_owned)
}
