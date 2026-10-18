use std::sync::Arc;

use chrono::{Duration, Utc};
use newsly_domain::{
    EmbeddingVector, EmbeddingVectorStore, RelationThresholds, prepare_relation_embedding_texts,
    related_representatives,
};
use newsly_providers::NewsItemGateway;
use newsly_queue::{OwnedWorkPlan, QueueKernel, TaskResult, TaskType};
use serde_json::{Value, json};
use sqlx::PgPool;

use crate::agent_usage::{AgentUsageAttribution, AgentUsageRecorder};
use crate::content::{ContentExtractionRuntime, ExtractionAttempt};
use crate::{HandlerExecution, HandlerFuture, LeaseHealth, TaskHandler};

use super::finalizer::{EnrichmentFinalizer, ProcessNewsFinalizer};
use super::input::{
    build_summary_prompt, prefilter_relation_candidates, relevant_link_input, resolved_summary,
};
use super::model::{
    BodySource, EnrichmentFinalizationPlan, EnrichmentMutation, EnrichmentPreparation,
    ProcessFinalizationPlan, ProcessMutation,
};
use super::repository::{
    checkpoint_summary, load_relation_candidates, prepare_enrichment, prepare_processing,
};
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
        Box::pin(async move { Box::pin(execute_enrichment(&services, &plan, lease)).await })
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
        Box::pin(async move { Box::pin(execute_processing(&services, &plan, lease)).await })
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
    match super::enrichment_checkpoint::load(&services.pool, &snapshot, &services.body_store).await
    {
        Ok(Some(mutation)) => {
            return soft_enrichment(services, plan, snapshot, mutation);
        }
        Ok(None) => {}
        Err(error) => return plain_failure(error, true),
    }
    let mutation = match preparation {
        EnrichmentPreparation::NotFound => unreachable!("snapshot checked"),
        EnrichmentPreparation::Existing { .. } => EnrichmentMutation::Existing,
        EnrichmentPreparation::Metadata {
            text, source_url, ..
        } => EnrichmentMutation::Metadata { text, source_url },
        EnrichmentPreparation::Skip { reason, .. } => EnrichmentMutation::Skipped {
            article_url: None,
            reason,
        },
        EnrichmentPreparation::Content {
            content_id,
            final_url,
            source_metadata,
            demand_policy,
            ..
        } => {
            let article_url = super::input::choose_article_url(&snapshot);
            match read_body_with_lease(services, &snapshot.body_source, &mut lease).await {
                BodyRead::Ready(body) => EnrichmentMutation::Content {
                    content_id,
                    article_url: article_url.unwrap_or_default(),
                    final_url,
                    extracted_chars: i32::try_from(body.chars().count()).unwrap_or(i32::MAX),
                    source_metadata,
                },
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
                        );
                    };
                    return extract_enrichment(
                        services,
                        plan,
                        snapshot,
                        article_url,
                        demand_policy,
                        lease,
                    )
                    .await;
                }
                BodyRead::LeaseLost => {
                    return plain_failure("queue lease was lost reading news article body", true);
                }
                BodyRead::Failed(error) => return plain_failure(error, true),
            }
        }
        EnrichmentPreparation::Extract {
            article_url,
            demand_policy,
            ..
        } => {
            return extract_enrichment(services, plan, snapshot, article_url, demand_policy, lease)
                .await;
        }
    };
    soft_enrichment(services, plan, snapshot, mutation)
}

async fn extract_enrichment(
    services: &NewsItemWorkerServices,
    plan: &OwnedWorkPlan,
    snapshot: super::model::NewsSnapshot,
    article_url: String,
    demand_policy: crate::content::FirecrawlDemandPolicy,
    mut lease: LeaseHealth,
) -> HandlerExecution {
    let deadline = Utc::now() + services.extraction_timeout;
    let request_id = format!("news-item-{}-task-{}", snapshot.id, plan.task_id);
    let extraction_url = article_url.clone();
    let runtime = services
        .extraction
        .for_task(crate::content::FirecrawlAttribution {
            task: plan.task_id,
            content: None,
            news_item: Some(snapshot.id),
            user: snapshot.owner_user_id,
        })
        .with_demand_policy(demand_policy);
    let extraction = runtime.process_article(&extraction_url, "article", &request_id, deadline);
    tokio::pin!(extraction);
    let attempt = tokio::select! {
        attempt = &mut extraction => attempt,
        () = lease.wait_for_ownership_loss() => {
            return plain_failure("queue lease was lost during news article extraction", true);
        }
    };
    let observed_usage = match &attempt {
        ExtractionAttempt::Success { usage, .. }
        | ExtractionAttempt::Deferred { usage, .. }
        | ExtractionAttempt::Failure { usage, .. } => usage,
    };
    // Observations are independent of grouping, source validity and the final lease decision.
    let recorded = async {
        let mut tx = services.pool.begin().await?;
        super::repository::persist_extraction_usage(
            &mut tx,
            plan.task_id,
            snapshot.owner_user_id,
            snapshot.id,
            observed_usage,
        )
        .await?;
        tx.commit().await?;
        Ok::<_, super::repository::NewsRepositoryError>(())
    }
    .await;
    if let Err(error) = recorded {
        return plain_failure(error.to_string(), true);
    }
    match attempt {
        ExtractionAttempt::Success { article, .. } => {
            let staged = match services.body_store.stage(snapshot.id, &article.body).await {
                Ok(staged) => staged,
                Err(error) => {
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
                    );
                }
            };
            let domain = url::Url::parse(&article.final_url)
                .ok()
                .and_then(|url| url.host_str().map(str::to_owned));
            let mutation = EnrichmentMutation::Storage {
                article_url,
                final_url: article.final_url,
                title: Some(article.title),
                article_domain: domain,
                extraction_method: article.extraction_method,
                body: staged,
            };
            if let Err(error) =
                super::enrichment_checkpoint::save(&services.pool, &snapshot, &mutation).await
            {
                return plain_failure(error, true);
            }
            soft_enrichment(services, plan, snapshot, mutation)
        }
        ExtractionAttempt::Deferred {
            reason, resume_at, ..
        } => HandlerExecution::with_finalizer(
            TaskResult::defer((resume_at - Utc::now()).num_seconds().max(1)),
            EnrichmentFinalizer::new(
                services.queue.clone(),
                EnrichmentFinalizationPlan {
                    task_id: plan.task_id,
                    snapshot,
                    mutation: EnrichmentMutation::Deferred {
                        article_url: Some(article_url),
                        reason,
                        resume_at,
                    },
                    finalized_at: Utc::now(),
                },
            ),
        ),
        ExtractionAttempt::Failure {
            reason,
            code,
            retryable,
            ..
        } => {
            if code == newsly_db::aggregator_corpus::FIRECRAWL_NO_DEMAND_ERROR_CODE {
                return soft_enrichment(
                    services,
                    plan,
                    snapshot,
                    EnrichmentMutation::MetadataOnly {
                        article_url,
                        reason,
                    },
                );
            }
            tracing::warn!(news_item_id=snapshot.id,error_code=code,retryable,error=%reason,"news article extraction failed softly; processing will continue");
            soft_enrichment(
                services,
                plan,
                snapshot,
                EnrichmentMutation::Failed {
                    article_url: Some(article_url.clone()),
                    final_url: Some(article_url),
                    strategy: Some("document_extractor".into()),
                    reason,
                },
            )
        }
    }
}

fn soft_enrichment(
    services: &NewsItemWorkerServices,
    plan: &OwnedWorkPlan,
    snapshot: super::model::NewsSnapshot,
    mutation: EnrichmentMutation,
) -> HandlerExecution {
    HandlerExecution::with_finalizer(
        TaskResult::ok(),
        EnrichmentFinalizer::new(
            services.queue.clone(),
            EnrichmentFinalizationPlan {
                task_id: plan.task_id,
                snapshot,
                mutation,
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
    let (summary, used_existing_summary) = if let Some(summary) = preparation.reusable_summary {
        tracing::info!(
            news_item_id,
            representative_news_item_id = ?preparation.reusable_representative_id,
            "reusing materialized short-form news summary"
        );
        (resolved_summary(summary, &preparation.snapshot), true)
    } else {
        let prompt = build_summary_prompt(&preparation.snapshot, article_body.as_deref());
        let usage_sink = Arc::new(AgentUsageRecorder::new(
            services.pool.clone(),
            AgentUsageAttribution {
                operation: "news_processing.summarize_short_form".to_owned(),
                source: "queue".to_owned(),
                task_id: plan.task_id,
                content_id: None,
                session_id: None,
                message_id: None,
                user_id: preparation.snapshot.owner_user_id,
            },
        ));
        let call = services
            .gateway
            .summarize_with_events(prompt, usage_sink.clone());
        tokio::pin!(call);
        let generated = tokio::select! {
            result = &mut call => Some(result),
            () = lease.wait_for_ownership_loss() => None,
        };
        usage_sink.finish().await;
        let generated = match generated {
            Some(Ok(result)) => result,
            Some(Err(error)) => {
                return processing_failure(
                    services,
                    plan,
                    preparation.snapshot,
                    &error.to_string(),
                    error.retryable(),
                );
            }
            None => {
                return processing_failure(
                    services,
                    plan,
                    preparation.snapshot,
                    "queue lease was lost during news summarization",
                    true,
                );
            }
        };
        (
            resolved_summary(generated.summary, &preparation.snapshot),
            false,
        )
    };
    if !used_existing_summary {
        match checkpoint_summary(
            &services.queue,
            &lease.claim,
            &preparation.snapshot,
            &summary,
        )
        .await
        {
            Ok(true) => {}
            Ok(false) => return HandlerExecution::from_result(TaskResult::defer(1)),
            Err(error) => {
                return processing_failure(
                    services,
                    plan,
                    preparation.snapshot,
                    &error.to_string(),
                    true,
                );
            }
        }
    }
    let item_document = preparation.snapshot.relation_document(&summary);
    let candidates = {
        let mut transaction = match services.pool.begin().await {
            Ok(transaction) => transaction,
            Err(error) => {
                return processing_failure(
                    services,
                    plan,
                    preparation.snapshot,
                    &error.to_string(),
                    true,
                );
            }
        };
        let candidates =
            match load_relation_candidates(&mut transaction, &preparation.snapshot, &item_document)
                .await
            {
                Ok(candidates) => candidates,
                Err(error) => {
                    return processing_failure(
                        services,
                        plan,
                        preparation.snapshot,
                        &error.to_string(),
                        true,
                    );
                }
            };
        if let Err(error) = transaction.commit().await {
            return processing_failure(
                services,
                plan,
                preparation.snapshot,
                &error.to_string(),
                true,
            );
        }
        prefilter_relation_candidates(&item_document, candidates)
    };
    let candidate_documents = candidates
        .iter()
        .map(|candidate| candidate.document.clone())
        .collect::<Vec<_>>();
    let texts = prepare_relation_embedding_texts(&item_document, &candidate_documents);
    let exact_match = item_document
        .exact_relation_key
        .as_ref()
        .is_some_and(|key| {
            candidates
                .iter()
                .any(|candidate| candidate.document.exact_relation_key.as_ref() == Some(key))
        });
    let owners =
        super::relation_preparation::text_sources(&item_document, &candidate_documents, &texts);
    let embeddings = if candidates.is_empty() || exact_match {
        EmbeddingVectorStore::new(1, Vec::<EmbeddingVector>::new()).expect("empty embedding store")
    } else {
        let prepared = prepare_relation_vectors(
            services,
            plan,
            &preparation.snapshot,
            &texts,
            &owners,
            &std::iter::once(news_item_id)
                .chain(candidate_documents.iter().map(|document| document.id))
                .collect::<Vec<_>>(),
            &mut lease,
        )
        .await;
        match prepared {
            Ok(store) => store,
            Err((error, retryable)) => {
                return processing_failure(services, plan, preparation.snapshot, &error, retryable);
            }
        }
    };
    let relation = match related_representatives(
        &item_document,
        &candidate_documents,
        services.relation_thresholds,
        &embeddings,
    ) {
        Ok(relation) => relation,
        Err(error) => {
            return processing_failure(
                services,
                plan,
                preparation.snapshot,
                &error.to_string(),
                true,
            );
        }
    };
    let links_prepared = relation.accepted_ids.is_empty();
    let link_input = article_body
        .as_deref()
        .and_then(|body| relevant_link_input(&preparation.snapshot, body));
    let link_identity = newsly_domain::relation_embedding_key(&json!({
        "source_fingerprint": preparation.snapshot.fingerprint,
        "input": link_input.as_ref().map(|input| json!({"title":input.title,"source_url":input.source_url,"candidates":input.candidates})),
        "prompt_version": 1,
    }).to_string());
    let cached_links = if links_prepared {
        let mut connection = match services.pool.acquire().await {
            Ok(value) => value,
            Err(error) => return plain_failure(error.to_string(), true),
        };
        match super::relation_preparation::load_links(
            &mut connection,
            news_item_id,
            &link_identity,
            services.gateway.link_model(),
        )
        .await
        {
            Ok(value) => value,
            Err(error) => return plain_failure(error.to_string(), true),
        }
    } else {
        None
    };
    let relevant_links = if let Some(links) = cached_links {
        links
    } else {
        let links = if relation.accepted_ids.is_empty() {
            match link_input {
                Some(input) => {
                    let usage_sink = Arc::new(AgentUsageRecorder::new(
                        services.pool.clone(),
                        AgentUsageAttribution {
                            operation: "news_processing.select_article_links".to_owned(),
                            source: "queue".to_owned(),
                            task_id: plan.task_id,
                            content_id: None,
                            session_id: None,
                            message_id: None,
                            user_id: preparation.snapshot.owner_user_id,
                        },
                    ));
                    let call = services.gateway.select_relevant_links_with_events(
                        input.title.as_deref(),
                        input.source_url.as_deref(),
                        &input.candidates,
                        usage_sink.clone(),
                    );
                    tokio::pin!(call);
                    let selection = tokio::select! {
                        result = &mut call => Some(result),
                        () = lease.wait_for_ownership_loss() => None,
                    };
                    usage_sink.finish().await;
                    let Some(selection) = selection else {
                        return processing_failure(
                            services,
                            plan,
                            preparation.snapshot,
                            "queue lease was lost during relevant-link selection",
                            true,
                        );
                    };
                    match selection {
                        Ok(selected) => Some(selected.links),
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
        if links_prepared {
            let mut connection = match services.pool.acquire().await {
                Ok(value) => value,
                Err(error) => return plain_failure(error.to_string(), true),
            };
            if let Err(error) = super::relation_preparation::save_links(
                &mut connection,
                news_item_id,
                &link_identity,
                services.gateway.link_model(),
                links.as_deref(),
            )
            .await
            {
                return plain_failure(error.to_string(), true);
            }
        }
        links
    };
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
                    relation_model: services.gateway.embedding_model().to_owned(),
                    relation_thresholds: services.relation_thresholds,
                    links_prepared,
                    item_document,
                    relevant_links,
                }),
                failure: None,
                terminal_failure: false,
                finalized_at: Utc::now(),
                briefing_debounce_seconds: services.briefing_debounce_seconds,
                briefing_batch_minimum: services.briefing_batch_minimum,
            },
        ),
    )
}

async fn prepare_relation_vectors(
    services: &NewsItemWorkerServices,
    plan: &OwnedWorkPlan,
    snapshot: &super::model::NewsSnapshot,
    texts: &[newsly_domain::RelationEmbeddingText],
    owners: &[i64],
    authorized_sources: &[i64],
    lease: &mut LeaseHealth,
) -> Result<EmbeddingVectorStore, (String, bool)> {
    let model = services.gateway.embedding_model();
    let mut vectors = {
        let mut connection = services
            .pool
            .acquire()
            .await
            .map_err(|error| (error.to_string(), true))?;
        super::relation_preparation::load_vectors(&mut connection, texts, authorized_sources, model)
            .await
            .map_err(|error| (error.to_string(), true))?
    };
    let missing = vectors
        .iter()
        .enumerate()
        .filter_map(|(index, vector)| vector.is_none().then_some(index))
        .collect::<Vec<_>>();
    if !missing.is_empty() {
        tracing::info!(
            news_item_id = snapshot.id,
            source_type = snapshot.source_type.as_deref(),
            missing_text_count = missing.len(),
            model,
            "preparing missing News relation vectors"
        );
        let inputs = missing
            .iter()
            .map(|index| texts[*index].text.clone())
            .collect::<Vec<_>>();
        let call = services.gateway.embed(&inputs);
        tokio::pin!(call);
        let batch = tokio::select! {
            result = &mut call => result.map_err(|error| (error.to_string(), error.retryable()))?,
            () = lease.wait_for_ownership_loss() => return Err(("queue lease lost during relation embeddings".into(), true)),
        };
        // Observed costs survive invalid vectors, stale publication, cancellation and lease loss.
        let recorder = AgentUsageRecorder::new(
            services.pool.clone(),
            AgentUsageAttribution {
                operation: "news_processing.embed_relations".into(),
                source: "queue".into(),
                task_id: plan.task_id,
                content_id: None,
                session_id: None,
                message_id: None,
                user_id: snapshot.owner_user_id,
            },
        );
        recorder
            .record(newsly_agent_runtime::AgentModelUsageObservation {
                run_id: uuid::Uuid::new_v4(),
                sequence: 1,
                feature: "news_processing".into(),
                provider: "openrouter".into(),
                model: batch.model,
                endpoint: "embeddings".into(),
                response_id: batch.provider_response_id,
                provider_request_id: None,
                requested_service_tier: None,
                processing_tier: None,
                usage_is_observed: true,
                provider_reported_cost_usd: None,
                usage: batch.usage,
            })
            .map_err(|error| (error.to_string(), true))?;
        recorder.finish().await;
        if batch.vectors.len() != missing.len() {
            return Err(("relation embedding response count mismatch".into(), true));
        }
        let missing_texts = missing
            .iter()
            .map(|index| texts[*index].clone())
            .collect::<Vec<_>>();
        let missing_owners = missing
            .iter()
            .map(|index| owners[*index])
            .collect::<Vec<_>>();
        super::relation_preparation::vector_store(&missing_texts, batch.vectors.clone())
            .map_err(|error| (error.to_string(), true))?;
        {
            let mut connection = services
                .pool
                .acquire()
                .await
                .map_err(|error| (error.to_string(), true))?;
            super::relation_preparation::save_vectors(
                &mut connection,
                &missing_texts,
                &missing_owners,
                model,
                &batch.vectors,
            )
            .await
            .map_err(|error| (error.to_string(), true))?;
        }
        for (index, vector) in missing.into_iter().zip(batch.vectors) {
            vectors[index] = Some(vector);
        }
    }
    super::relation_preparation::vector_store(
        texts,
        vectors.into_iter().map(Option::unwrap).collect(),
    )
    .map_err(|error| (error.to_string(), true))
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
                terminal_failure: terminal,
                finalized_at: Utc::now(),
                briefing_debounce_seconds: services.briefing_debounce_seconds,
                briefing_batch_minimum: services.briefing_batch_minimum,
            },
        ),
    )
}

fn news_item_id(plan: &OwnedWorkPlan) -> Option<i64> {
    plan.payload
        .get("news_item_id")
        .and_then(Value::as_i64)
        .filter(|value| *value > 0)
}

fn plain_failure(message: impl Into<String>, retryable: bool) -> HandlerExecution {
    HandlerExecution::from_result(TaskResult::fail(Some(message.into()), retryable))
}
