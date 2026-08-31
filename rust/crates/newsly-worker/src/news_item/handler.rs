use std::sync::Arc;

use chrono::{Duration, Utc};
use newsly_domain::{
    EmbeddingVector, EmbeddingVectorStore, RelationThresholds, prepare_relation_embedding_texts,
    related_representatives,
};
use newsly_providers::{NewsItemGateway, NewsItemGatewayError};
use newsly_queue::{OwnedWorkPlan, QueueKernel, TaskResult, TaskType};
use serde_json::{Value, json};
use sqlx::PgPool;

use crate::content::{ContentExtractionRuntime, ExtractionAttempt};
use crate::{HandlerExecution, HandlerFuture, LeaseHealth, TaskHandler};

use super::finalizer::{EnrichmentFinalizer, ProcessNewsFinalizer};
use super::input::{
    build_summary_prompt, prefilter_relation_candidates, relevant_link_input, resolved_summary,
};
use super::model::{
    BodySource, EnrichmentFinalizationPlan, EnrichmentMutation, EnrichmentPreparation,
    ModelUsageWrite, ProcessFinalizationPlan, ProcessMutation,
};
use super::repository::{load_relation_candidates, prepare_enrichment, prepare_processing};
use super::storage::NewsArticleBodyStore;

#[derive(Debug, Clone)]
pub struct NewsItemWorkerServices {
    pool: PgPool,
    queue: QueueKernel,
    gateway: NewsItemGateway,
    extraction: ContentExtractionRuntime,
    body_store: NewsArticleBodyStore,
    max_retries: i32,
    relation_thresholds: RelationThresholds,
    extraction_timeout: Duration,
    briefing_debounce_seconds: i64,
    briefing_batch_minimum: i64,
}

impl NewsItemWorkerServices {
    #[allow(clippy::too_many_arguments)]
    pub const fn new(
        pool: PgPool,
        queue: QueueKernel,
        gateway: NewsItemGateway,
        extraction: ContentExtractionRuntime,
        body_store: NewsArticleBodyStore,
        max_retries: i32,
        relation_thresholds: RelationThresholds,
        extraction_timeout: Duration,
        briefing_debounce_seconds: i64,
        briefing_batch_minimum: i64,
    ) -> Self {
        Self {
            pool,
            queue,
            gateway,
            extraction,
            body_store,
            max_retries,
            relation_thresholds,
            extraction_timeout,
            briefing_debounce_seconds,
            briefing_batch_minimum,
        }
    }
}

#[derive(Debug, Clone)]
pub struct EnrichNewsItemArticleHandler {
    services: Arc<NewsItemWorkerServices>,
}

impl EnrichNewsItemArticleHandler {
    pub fn new(services: Arc<NewsItemWorkerServices>) -> Self {
        Self { services }
    }
}

impl TaskHandler for EnrichNewsItemArticleHandler {
    fn task_type(&self) -> TaskType {
        TaskType::EnrichNewsItemArticle
    }

    fn execute(&self, plan: Arc<OwnedWorkPlan>, lease: LeaseHealth) -> HandlerFuture<'_> {
        let services = Arc::clone(&self.services);
        Box::pin(async move { execute_enrichment(&services, &plan, lease).await })
    }
}

#[derive(Debug, Clone)]
pub struct ProcessNewsItemHandler {
    services: Arc<NewsItemWorkerServices>,
}

impl ProcessNewsItemHandler {
    pub fn new(services: Arc<NewsItemWorkerServices>) -> Self {
        Self { services }
    }
}

impl TaskHandler for ProcessNewsItemHandler {
    fn task_type(&self) -> TaskType {
        TaskType::ProcessNewsItem
    }

    fn execute(&self, plan: Arc<OwnedWorkPlan>, lease: LeaseHealth) -> HandlerFuture<'_> {
        let services = Arc::clone(&self.services);
        Box::pin(async move { execute_processing(&services, &plan, lease).await })
    }
}

async fn execute_enrichment(
    services: &NewsItemWorkerServices,
    plan: &OwnedWorkPlan,
    mut lease: LeaseHealth,
) -> HandlerExecution {
    let Some(news_item_id) = news_item_id(plan) else {
        return plain_failure(
            "enrich_news_item_article requires a positive news_item_id",
            false,
        );
    };
    let preparation = {
        let mut transaction = match services.pool.begin().await {
            Ok(transaction) => transaction,
            Err(error) => return plain_failure(error.to_string(), true),
        };
        let preparation = match prepare_enrichment(&mut transaction, news_item_id).await {
            Ok(preparation) => preparation,
            Err(error) => return plain_failure(error.to_string(), true),
        };
        if let Err(error) = transaction.commit().await {
            return plain_failure(error.to_string(), true);
        }
        preparation
    };
    let Some(snapshot) = preparation.snapshot().cloned() else {
        return plain_failure(format!("news item {news_item_id} does not exist"), false);
    };
    if lease.ownership_lost() {
        return plain_failure("queue lease was lost before news enrichment", true);
    }
    let (mutation, usage) = match preparation {
        EnrichmentPreparation::NotFound => unreachable!("snapshot checked"),
        EnrichmentPreparation::Existing { .. } => (EnrichmentMutation::Existing, Vec::new()),
        EnrichmentPreparation::Metadata {
            text, source_url, ..
        } => (
            EnrichmentMutation::Metadata { text, source_url },
            Vec::new(),
        ),
        EnrichmentPreparation::Skip { reason, .. } => (
            EnrichmentMutation::Skipped {
                article_url: None,
                reason,
            },
            Vec::new(),
        ),
        EnrichmentPreparation::Content {
            content_id,
            final_url,
            source_metadata,
            ..
        } => {
            let article_url = super::input::choose_article_url(&snapshot);
            match read_body_with_lease(services, &snapshot.body_source, &mut lease).await {
                BodyRead::Ready(body) => (
                    EnrichmentMutation::Content {
                        content_id,
                        article_url: article_url.unwrap_or_default(),
                        final_url,
                        extracted_chars: i32::try_from(body.chars().count()).unwrap_or(i32::MAX),
                        source_metadata,
                    },
                    Vec::new(),
                ),
                BodyRead::Missing => {
                    let Some(article_url) = article_url else {
                        return soft_enrichment(
                            services,
                            plan,
                            snapshot,
                            EnrichmentMutation::Skipped {
                                article_url: None,
                                reason:
                                    "Existing article body is missing and no URL can be extracted"
                                        .to_owned(),
                            },
                            Vec::new(),
                        );
                    };
                    return extract_enrichment(services, plan, snapshot, article_url, lease).await;
                }
                BodyRead::LeaseLost => {
                    return plain_failure("queue lease was lost reading news article body", true);
                }
                BodyRead::Failed(error) => return plain_failure(error, true),
            }
        }
        EnrichmentPreparation::Extract { article_url, .. } => {
            return extract_enrichment(services, plan, snapshot, article_url, lease).await;
        }
    };
    soft_enrichment(services, plan, snapshot, mutation, usage)
}

async fn extract_enrichment(
    services: &NewsItemWorkerServices,
    plan: &OwnedWorkPlan,
    snapshot: super::model::NewsSnapshot,
    article_url: String,
    mut lease: LeaseHealth,
) -> HandlerExecution {
    let deadline = Utc::now() + services.extraction_timeout;
    let request_id = format!("news-item-{}-task-{}", snapshot.id, plan.task_id);
    let extraction_url = article_url.clone();
    let extraction =
        services
            .extraction
            .process_article(&extraction_url, "article", &request_id, deadline);
    tokio::pin!(extraction);
    let attempt = tokio::select! {
        attempt = &mut extraction => attempt,
        () = lease.wait_for_ownership_loss() => {
            return plain_failure("queue lease was lost during news article extraction", true);
        }
    };
    match attempt {
        ExtractionAttempt::Success { article, usage } => {
            let staged = match services.body_store.stage(snapshot.id, &article.body).await {
                Ok(staged) => staged,
                Err(error) => {
                    tracing::warn!(
                        news_item_id = snapshot.id,
                        error = %error,
                        "news article body staging failed softly; processing will continue"
                    );
                    return soft_enrichment(
                        services,
                        plan,
                        snapshot,
                        EnrichmentMutation::Failed {
                            article_url: Some(article_url),
                            final_url: Some(article.final_url),
                            strategy: Some(article.extraction_method),
                            reason: error.to_string(),
                        },
                        usage,
                    );
                }
            };
            let domain = url::Url::parse(&article.final_url)
                .ok()
                .and_then(|url| url.host_str().map(str::to_owned));
            soft_enrichment(
                services,
                plan,
                snapshot,
                EnrichmentMutation::Storage {
                    article_url,
                    final_url: article.final_url,
                    title: Some(article.title),
                    article_domain: domain,
                    extraction_method: article.extraction_method,
                    body: staged,
                },
                usage,
            )
        }
        ExtractionAttempt::Failure {
            reason,
            code,
            retryable,
            usage,
        } => {
            tracing::warn!(
                news_item_id = snapshot.id,
                error_code = code,
                retryable,
                error = %reason,
                "news article extraction failed softly; processing will continue"
            );
            soft_enrichment(
                services,
                plan,
                snapshot,
                EnrichmentMutation::Failed {
                    article_url: Some(article_url.clone()),
                    final_url: Some(article_url),
                    strategy: Some("document_extractor".to_owned()),
                    reason,
                },
                usage,
            )
        }
    }
}

fn soft_enrichment(
    services: &NewsItemWorkerServices,
    plan: &OwnedWorkPlan,
    snapshot: super::model::NewsSnapshot,
    mutation: EnrichmentMutation,
    usage: Vec<crate::content::UsageWrite>,
) -> HandlerExecution {
    HandlerExecution::with_finalizer(
        TaskResult::ok(),
        EnrichmentFinalizer::new(
            services.queue.clone(),
            EnrichmentFinalizationPlan {
                task_id: plan.task_id,
                snapshot,
                mutation,
                usage,
                finalized_at: Utc::now(),
            },
        ),
    )
}

#[allow(clippy::too_many_lines)]
async fn execute_processing(
    services: &NewsItemWorkerServices,
    plan: &OwnedWorkPlan,
    mut lease: LeaseHealth,
) -> HandlerExecution {
    let Some(news_item_id) = news_item_id(plan) else {
        return plain_failure("process_news_item requires a positive news_item_id", false);
    };
    let preparation = {
        let mut transaction = match services.pool.begin().await {
            Ok(transaction) => transaction,
            Err(error) => return plain_failure(error.to_string(), true),
        };
        let preparation = match prepare_processing(&mut transaction, news_item_id).await {
            Ok(preparation) => preparation,
            Err(error) => return plain_failure(error.to_string(), true),
        };
        if let Err(error) = transaction.commit().await {
            return plain_failure(error.to_string(), true);
        }
        preparation
    };
    let Some(preparation) = preparation else {
        return plain_failure(format!("news item {news_item_id} does not exist"), false);
    };
    if lease.ownership_lost() {
        return processing_failure(
            services,
            plan,
            preparation.snapshot,
            "queue lease was lost",
            true,
        );
    }
    let article_body =
        match read_body_with_lease(services, &preparation.snapshot.body_source, &mut lease).await {
            BodyRead::Ready(body) => Some(body),
            BodyRead::Missing => None,
            BodyRead::LeaseLost => {
                return processing_failure(
                    services,
                    plan,
                    preparation.snapshot,
                    "queue lease was lost reading article body",
                    true,
                );
            }
            BodyRead::Failed(error) => {
                return processing_failure(services, plan, preparation.snapshot, &error, true);
            }
        };
    let mut usage = Vec::new();
    let (summary, used_existing_summary) = if let Some(summary) = preparation.reusable_summary {
        tracing::info!(
            news_item_id,
            representative_news_item_id = ?preparation.reusable_representative_id,
            "reusing materialized short-form news summary"
        );
        (resolved_summary(summary, &preparation.snapshot), true)
    } else {
        let prompt = build_summary_prompt(&preparation.snapshot, article_body.as_deref());
        let call = services.gateway.summarize(prompt);
        tokio::pin!(call);
        let generated = tokio::select! {
            result = &mut call => match result {
                Ok(result) => result,
                Err(error) => {
                    return processing_failure(
                        services,
                        plan,
                        preparation.snapshot,
                        &error.to_string(),
                        provider_retryable(&error),
                    );
                }
            },
            () = lease.wait_for_ownership_loss() => {
                return processing_failure(
                    services,
                    plan,
                    preparation.snapshot,
                    "queue lease was lost during news summarization",
                    true,
                );
            }
        };
        usage.push(model_usage(
            &generated.model,
            "news_processing",
            "news_processing.summarize_short_form",
            generated.provider_response_id,
            generated.usage,
            news_item_id,
            preparation.snapshot.source_type.as_deref(),
        ));
        (
            resolved_summary(generated.summary, &preparation.snapshot),
            false,
        )
    };
    let item_document = preparation.snapshot.relation_document(&summary);
    let candidates = {
        let mut transaction = match services.pool.begin().await {
            Ok(transaction) => transaction,
            Err(error) => {
                return processing_failure_with_usage(
                    services,
                    plan,
                    preparation.snapshot,
                    &error.to_string(),
                    true,
                    usage,
                );
            }
        };
        let candidates =
            match load_relation_candidates(&mut transaction, &preparation.snapshot, &item_document)
                .await
            {
                Ok(candidates) => candidates,
                Err(error) => {
                    return processing_failure_with_usage(
                        services,
                        plan,
                        preparation.snapshot,
                        &error.to_string(),
                        true,
                        usage,
                    );
                }
            };
        if let Err(error) = transaction.commit().await {
            return processing_failure_with_usage(
                services,
                plan,
                preparation.snapshot,
                &error.to_string(),
                true,
                usage,
            );
        }
        prefilter_relation_candidates(&item_document, candidates)
    };
    let texts = prepare_relation_embedding_texts(
        &item_document,
        &candidates
            .iter()
            .map(|candidate| candidate.document.clone())
            .collect::<Vec<_>>(),
    );
    let exact_match = item_document
        .exact_relation_key
        .as_ref()
        .is_some_and(|key| {
            candidates
                .iter()
                .any(|candidate| candidate.document.exact_relation_key.as_ref() == Some(key))
        });
    let embeddings = if candidates.is_empty() || exact_match {
        match EmbeddingVectorStore::new(1, Vec::<EmbeddingVector>::new()) {
            Ok(store) => store,
            Err(error) => {
                return processing_failure_with_usage(
                    services,
                    plan,
                    preparation.snapshot,
                    &error.to_string(),
                    false,
                    usage,
                );
            }
        }
    } else {
        let inputs = texts
            .iter()
            .map(|text| text.text.clone())
            .collect::<Vec<_>>();
        let call = services.gateway.embed(&inputs);
        tokio::pin!(call);
        let batch = tokio::select! {
            result = &mut call => match result {
                Ok(result) => result,
                Err(error) => {
                    return processing_failure_with_usage(
                        services,
                        plan,
                        preparation.snapshot,
                        &error.to_string(),
                        provider_retryable(&error),
                        usage,
                    );
                }
            },
            () = lease.wait_for_ownership_loss() => {
                return processing_failure_with_usage(
                    services,
                    plan,
                    preparation.snapshot,
                    "queue lease was lost during relation embeddings",
                    true,
                    usage,
                );
            }
        };
        usage.push(ModelUsageWrite {
            provider: "openrouter".to_owned(),
            model: batch.model.clone(),
            feature: "news_processing",
            operation: "news_processing.embed_relations",
            provider_response_id: batch.provider_response_id,
            usage: batch.usage,
            metadata: json!({
                "news_item_id": news_item_id,
                "source_type": preparation.snapshot.source_type.as_deref(),
                "text_count": inputs.len(),
            }),
        });
        let dimensions = batch.vectors.first().map_or(0, Vec::len);
        let vectors = texts
            .iter()
            .zip(batch.vectors)
            .map(|(text, vector)| EmbeddingVector {
                id: text.id.clone(),
                text_sha256: text.text_sha256.clone(),
                vector,
            })
            .collect::<Vec<_>>();
        match EmbeddingVectorStore::new(dimensions, vectors) {
            Ok(store) => store,
            Err(error) => {
                return processing_failure_with_usage(
                    services,
                    plan,
                    preparation.snapshot,
                    &error.to_string(),
                    true,
                    usage,
                );
            }
        }
    };
    let relation = match related_representatives(
        &item_document,
        &candidates
            .iter()
            .map(|candidate| candidate.document.clone())
            .collect::<Vec<_>>(),
        services.relation_thresholds,
        &embeddings,
    ) {
        Ok(relation) => relation,
        Err(error) => {
            return processing_failure_with_usage(
                services,
                plan,
                preparation.snapshot,
                &error.to_string(),
                true,
                usage,
            );
        }
    };
    let relevant_links = if relation.accepted_ids.is_empty() {
        match article_body
            .as_deref()
            .and_then(|body| relevant_link_input(&preparation.snapshot, body))
        {
            Some(input) => {
                let call = services.gateway.select_relevant_links(
                    input.title.as_deref(),
                    input.source_url.as_deref(),
                    &input.candidates,
                );
                tokio::pin!(call);
                let selection = tokio::select! {
                    result = &mut call => result,
                    () = lease.wait_for_ownership_loss() => {
                        return processing_failure_with_usage(
                            services,
                            plan,
                            preparation.snapshot,
                            "queue lease was lost during relevant-link selection",
                            true,
                            usage,
                        );
                    }
                };
                match selection {
                    Ok(selected) => {
                        usage.push(model_usage(
                            &selected.model,
                            "news_relevant_links",
                            "news_processing.select_article_links",
                            selected.provider_response_id,
                            selected.usage,
                            news_item_id,
                            preparation.snapshot.source_type.as_deref(),
                        ));
                        Some(selected.links)
                    }
                    Err(error) => {
                        tracing::warn!(
                            news_item_id,
                            error = %error,
                            "relevant-link selection failed closed"
                        );
                        None
                    }
                }
            }
            None => None,
        }
    } else {
        None
    };
    let relation_trace = serde_json::to_value(&relation).unwrap_or_else(|_| json!({}));
    HandlerExecution::with_finalizer(
        TaskResult::ok(),
        ProcessNewsFinalizer::new(
            services.queue.clone(),
            ProcessFinalizationPlan {
                task_id: plan.task_id,
                snapshot: preparation.snapshot,
                mutation: Some(ProcessMutation {
                    summary,
                    used_existing_summary,
                    item_document,
                    candidates,
                    accepted_ids: relation.accepted_ids,
                    relevant_links,
                    relation_trace,
                    usage,
                }),
                failure: None,
                failure_usage: Vec::new(),
                terminal_failure: false,
                finalized_at: Utc::now(),
                briefing_debounce_seconds: services.briefing_debounce_seconds,
                briefing_batch_minimum: services.briefing_batch_minimum,
            },
        ),
    )
}

enum BodyRead {
    Ready(String),
    Missing,
    Failed(String),
    LeaseLost,
}

async fn read_body_with_lease(
    services: &NewsItemWorkerServices,
    source: &BodySource,
    lease: &mut LeaseHealth,
) -> BodyRead {
    match source {
        BodySource::Inline(text) => BodyRead::Ready(text.clone()),
        BodySource::None => BodyRead::Missing,
        BodySource::Stored(pointer) => {
            let read = services.body_store.read(pointer);
            tokio::pin!(read);
            tokio::select! {
                result = &mut read => match result {
                    Ok(Some(body)) => BodyRead::Ready(body),
                    Ok(None) => BodyRead::Missing,
                    Err(error) => BodyRead::Failed(error.to_string()),
                },
                () = lease.wait_for_ownership_loss() => BodyRead::LeaseLost,
            }
        }
    }
}

fn processing_failure(
    services: &NewsItemWorkerServices,
    plan: &OwnedWorkPlan,
    snapshot: super::model::NewsSnapshot,
    error: &str,
    retryable: bool,
) -> HandlerExecution {
    processing_failure_with_usage(services, plan, snapshot, error, retryable, Vec::new())
}

fn processing_failure_with_usage(
    services: &NewsItemWorkerServices,
    plan: &OwnedWorkPlan,
    snapshot: super::model::NewsSnapshot,
    error: &str,
    retryable: bool,
    usage: Vec<ModelUsageWrite>,
) -> HandlerExecution {
    let terminal = !retryable || plan.retry_count >= services.max_retries.max(0);
    HandlerExecution::with_finalizer(
        TaskResult::fail(Some(error.to_owned()), retryable),
        ProcessNewsFinalizer::new(
            services.queue.clone(),
            ProcessFinalizationPlan {
                task_id: plan.task_id,
                snapshot,
                mutation: None,
                failure: Some(error.to_owned()),
                failure_usage: usage,
                terminal_failure: terminal,
                finalized_at: Utc::now(),
                briefing_debounce_seconds: services.briefing_debounce_seconds,
                briefing_batch_minimum: services.briefing_batch_minimum,
            },
        ),
    )
}

fn model_usage(
    model: &str,
    feature: &'static str,
    operation: &'static str,
    provider_response_id: Option<String>,
    usage: newsly_agent_runtime::ProviderUsage,
    news_item_id: i64,
    source_type: Option<&str>,
) -> ModelUsageWrite {
    let provider = model
        .split_once(':')
        .map_or("openai", |(provider, _)| provider)
        .to_owned();
    ModelUsageWrite {
        provider,
        model: model.to_owned(),
        feature,
        operation,
        provider_response_id,
        usage,
        metadata: json!({"news_item_id": news_item_id, "source_type": source_type}),
    }
}

fn news_item_id(plan: &OwnedWorkPlan) -> Option<i64> {
    plan.payload
        .get("news_item_id")
        .and_then(Value::as_i64)
        .filter(|value| *value > 0)
}

fn provider_retryable(error: &NewsItemGatewayError) -> bool {
    !matches!(
        error,
        NewsItemGatewayError::EmptySummaryInput
            | NewsItemGatewayError::InvalidSummary(_)
            | NewsItemGatewayError::UnsupportedEmbeddingModel(_)
            | NewsItemGatewayError::TooManyEmbeddingInputs(_)
            | NewsItemGatewayError::InvalidEmbeddingInputLength(_)
            | NewsItemGatewayError::EmbeddingInputTooLarge(_)
            | NewsItemGatewayError::InvalidEmbedding(_)
            | NewsItemGatewayError::TooManyLinkCandidates(_)
            | NewsItemGatewayError::TooManySelectedLinks(_)
            | NewsItemGatewayError::InventedRelevantLink(_)
            | NewsItemGatewayError::InvalidRelevantLink(_)
            | NewsItemGatewayError::InvalidUrl(_)
    )
}

fn plain_failure(message: impl Into<String>, retryable: bool) -> HandlerExecution {
    HandlerExecution::from_result(TaskResult::fail(Some(message.into()), retryable))
}
