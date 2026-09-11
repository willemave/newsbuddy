use super::BriefingRefreshWorkerServices;
use crate::{
    HandlerExecution, HandlerFinalizerFuture, HandlerFuture, LeaseHealth, TaskFinalizer,
    TaskFinalizerResult, TaskHandler,
};
use newsly_db::news_lens_embeddings::{self, PreparedNewsEmbedding};
use newsly_queue::{EnqueueRequest, OwnedWorkPlan, TaskResult, TaskType};
use sqlx::{Postgres, Transaction};
use std::sync::Arc;

#[derive(Debug)]
pub struct PrepareNewsLensHandler {
    services: Arc<BriefingRefreshWorkerServices>,
}
impl PrepareNewsLensHandler {
    pub fn new(services: Arc<BriefingRefreshWorkerServices>) -> Self {
        Self { services }
    }
}
impl TaskHandler for PrepareNewsLensHandler {
    fn task_type(&self) -> TaskType {
        TaskType::PrepareNewsLens
    }
    fn execute(&self, plan: Arc<OwnedWorkPlan>, mut lease: LeaseHealth) -> HandlerFuture<'_> {
        Box::pin(async move {
            let result = tokio::select! {
                result = prepare(&self.services, &plan) => result,
                () = lease.wait_for_ownership_loss() => return HandlerExecution::from_result(TaskResult::fail(Some("news embedding lease lost".into()), true)),
            };
            match result {
                Ok(Preparation::Ready(embedding, usage)) => HandlerExecution::with_finalizer(
                    TaskResult::ok(),
                    EmbeddingFinalizer {
                        embedding,
                        usage,
                        task_id: plan.task_id,
                    },
                ),
                Ok(Preparation::Checkpoint { model, input_hash }) => {
                    HandlerExecution::with_finalizer(
                        TaskResult::defer(0),
                        InputCheckpoint {
                            task_id: plan.task_id,
                            model,
                            input_hash,
                        },
                    )
                }
                Ok(Preparation::Absent) => HandlerExecution::from_result(TaskResult::ok()),
                Err(error) => HandlerExecution::from_result(TaskResult::fail(
                    Some(format!(
                        "news lens preparation ({}) failed: {error}",
                        self.services.gateway.embedding_model()
                    )),
                    true,
                )),
            }
        })
    }
}

enum Preparation {
    Absent,
    Checkpoint {
        model: String,
        input_hash: String,
    },
    Ready(
        PreparedNewsEmbedding,
        Option<newsly_db::BriefingLensAssignmentUsage>,
    ),
}

#[derive(Debug)]
struct InputCheckpoint {
    task_id: i64,
    model: String,
    input_hash: String,
}
impl TaskFinalizer for InputCheckpoint {
    fn apply<'a>(
        &'a self,
        tx: &'a mut Transaction<'static, Postgres>,
    ) -> HandlerFinalizerFuture<'a> {
        Box::pin(async move {
            sqlx::query("UPDATE processing_tasks SET payload=(payload::jsonb || jsonb_build_object('embedding_model',$2::text,'input_hash',$3::text,'encoder_version',$4::integer))::json WHERE id::bigint=$1")
                .bind(self.task_id).bind(&self.model).bind(&self.input_hash).bind(news_lens_embeddings::ENCODER_VERSION).execute(&mut **tx).await?;
            Ok(TaskFinalizerResult::Keep)
        })
    }
}

async fn prepare(
    services: &BriefingRefreshWorkerServices,
    plan: &OwnedWorkPlan,
) -> Result<Preparation, Box<dyn std::error::Error + Send + Sync>> {
    let id = plan
        .payload
        .get("news_item_id")
        .and_then(serde_json::Value::as_i64)
        .ok_or("missing news item")?;
    let model = services.gateway.embedding_model().to_owned();
    let text = {
        let mut connection = services.pool.acquire().await?;
        let Some(source) = news_lens_embeddings::source(&mut connection, id).await? else {
            return Ok(Preparation::Absent);
        };
        let text = source.embedding_text();
        if let Some(vector) = news_lens_embeddings::load(&mut connection, id, &model, &text).await?
        {
            return Ok(Preparation::Ready(
                PreparedNewsEmbedding {
                    news_item_id: id,
                    model,
                    input_hash: news_lens_embeddings::input_hash(&text),
                    vector,
                },
                None,
            ));
        }
        if news_lens_embeddings::preparation_failed(&mut connection, id, &model, &text).await? {
            return Ok(Preparation::Absent);
        }
        let hash = news_lens_embeddings::input_hash(&text);
        if plan
            .payload
            .get("input_hash")
            .and_then(serde_json::Value::as_str)
            != Some(hash.as_str())
            || plan
                .payload
                .get("embedding_model")
                .and_then(serde_json::Value::as_str)
                != Some(model.as_str())
            || plan
                .payload
                .get("encoder_version")
                .and_then(serde_json::Value::as_i64)
                != Some(i64::from(news_lens_embeddings::ENCODER_VERSION))
        {
            return Ok(Preparation::Checkpoint {
                model,
                input_hash: hash,
            });
        }
        text
    };
    let mut batch = services.gateway.embed(std::slice::from_ref(&text)).await?;
    if batch.vectors.len() != 1 {
        return Err("news embedding response count mismatch".into());
    }
    let vector = batch.vectors.remove(0);
    if vector.is_empty()
        || vector.iter().any(|v| !v.is_finite())
        || !vector.iter().any(|v| *v != 0.0)
    {
        return Err("invalid news embedding vector".into());
    }
    let expected_dimensions: Option<i32> =
        sqlx::query_scalar("SELECT dimensions FROM news_lens_embedding_models WHERE model=$1")
            .bind(&model)
            .fetch_optional(&services.pool)
            .await?;
    if expected_dimensions
        .is_some_and(|expected| usize::try_from(expected).ok() != Some(vector.len()))
    {
        return Err("news embedding dimensions changed for the configured model".into());
    }
    Ok(Preparation::Ready(
        PreparedNewsEmbedding {
            news_item_id: id,
            model,
            input_hash: news_lens_embeddings::input_hash(&text),
            vector,
        },
        Some(newsly_db::BriefingLensAssignmentUsage {
            provider: "openrouter".into(),
            model: batch.model,
            provider_response_id: batch.provider_response_id,
            usage: batch.usage,
            feature: "news_lens_preparation".into(),
            operation: "news.prepare_lens_embedding".into(),
        }),
    ))
}

#[derive(Debug)]
struct EmbeddingFinalizer {
    embedding: PreparedNewsEmbedding,
    usage: Option<newsly_db::BriefingLensAssignmentUsage>,
    task_id: i64,
}
impl TaskFinalizer for EmbeddingFinalizer {
    fn apply<'a>(
        &'a self,
        tx: &'a mut Transaction<'static, Postgres>,
    ) -> HandlerFinalizerFuture<'a> {
        Box::pin(async move {
            // The worker owns the exact queue lease here; lock the source before checking its hash.
            sqlx::query("SELECT id FROM news_items WHERE id::bigint = $1 FOR UPDATE")
                .bind(self.embedding.news_item_id)
                .execute(&mut **tx)
                .await?;
            let saved = news_lens_embeddings::save(tx, &self.embedding).await?;
            if let Some(usage) = &self.usage {
                newsly_db::news_lens_embeddings::record_usage(tx, self.task_id, usage).await?;
            }
            Ok(if saved {
                TaskFinalizerResult::Keep
            } else {
                TaskFinalizerResult::Override(TaskResult::defer(5))
            })
        })
    }
}

#[derive(Default)]
pub(super) struct ReadyNews {
    pub sources: Vec<newsly_db::BriefingUnassignedSource>,
    pub vectors: Vec<Vec<f64>>,
    pub failed: Vec<newsly_db::BriefingUnassignedSource>,
    pub requests: Vec<EnqueueRequest>,
}

/// Missing or failed news never removes ready siblings from the composition pass.
pub(super) async fn ready_news(
    pool: &sqlx::PgPool,
    pending: &[newsly_db::BriefingUnassignedSource],
    model: &str,
) -> Result<ReadyNews, sqlx::Error> {
    let mut result = ReadyNews::default();
    let mut connection = pool.acquire().await?;
    for source in pending {
        let text = source.source.embedding_text();
        if let Some(vector) =
            news_lens_embeddings::load(&mut connection, source.source_id, model, &text).await?
        {
            result.sources.push(source.clone());
            result.vectors.push(vector);
        } else if news_lens_embeddings::preparation_failed(
            &mut connection,
            source.source_id,
            model,
            &text,
        )
        .await?
        {
            result.failed.push(source.clone());
        } else {
            result.requests.push(request(source.source_id));
        }
    }
    Ok(result)
}

pub(super) fn request(id: i64) -> EnqueueRequest {
    let mut request = EnqueueRequest::new(TaskType::PrepareNewsLens);
    request.priority = 10;
    request.payload = Some(serde_json::Map::from_iter([(
        "news_item_id".into(),
        serde_json::json!(id),
    )]));
    request.dedupe = Some(true);
    request.dedupe_key = Some(format!("news-lens:{id}"));
    request
}

#[cfg(test)]
mod tests {
    use super::*;
    use newsly_domain::{ResourceKey, RuntimeOwner};
    use newsly_queue::{ClaimRequest, ClaimRuntimeScope, QueueKernel, TaskQueue};

    #[sqlx::test]
    async fn foreground_preparation_promotes_the_existing_global_task(pool: sqlx::PgPool) {
        newsly_db::run_migrations(&pool).await.unwrap();
        let queue = QueueKernel::new(pool.clone());
        let mut first = request(1);
        first.priority = -10;
        let old = queue.enqueue(first).await.unwrap();
        let mut second = request(2);
        second.priority = -10;
        let needed = queue.enqueue(second).await.unwrap();
        assert_eq!(queue.enqueue(request(2)).await.unwrap(), needed);
        let count: i64 = sqlx::query_scalar(
            "SELECT count(*) FROM processing_tasks WHERE task_type='prepare_news_lens'",
        )
        .fetch_one(&pool)
        .await
        .unwrap();
        assert_eq!(count, 2);
        let scope = ClaimRuntimeScope::namespaces(
            RuntimeOwner::Rust,
            [ResourceKey::new("prepare_news_lens").unwrap()],
        )
        .unwrap();
        let claim = ClaimRequest::for_queue("warm-priority-test", TaskQueue::Llm, scope);
        let next = queue.claim(&claim).await.unwrap().unwrap();
        assert_eq!(next.id, needed);
        assert_ne!(next.id, old);
    }
}

#[cfg(test)]
mod readiness_tests {
    use super::*;

    #[sqlx::test(migrations = false)]
    async fn cached_missing_and_failed_stories_are_isolated(pool: sqlx::PgPool) {
        newsly_db::run_migrations(&pool).await.unwrap();
        let mut pending = Vec::new();
        let mut connection = pool.acquire().await.unwrap();
        for key in ["cached", "missing", "failed"] {
            let id:i64=sqlx::query_scalar("INSERT INTO news_items(ingest_key,visibility_scope,platform,status,summary_text,raw_metadata,ingested_at,created_at) VALUES($1,'global','hackernews','ready','Summary','{}',timezone('UTC',now()),timezone('UTC',now())) RETURNING id::bigint").bind(key).fetch_one(&mut *connection).await.unwrap();
            let source = news_lens_embeddings::source(&mut connection, id)
                .await
                .unwrap()
                .unwrap();
            let hash = news_lens_embeddings::input_hash(&source.embedding_text());
            if key == "cached" {
                news_lens_embeddings::save(
                    &mut connection,
                    &PreparedNewsEmbedding {
                        news_item_id: id,
                        model: "test-model".into(),
                        input_hash: hash,
                        vector: vec![0.2, 0.8],
                    },
                )
                .await
                .unwrap();
            } else if key == "failed" {
                let payload = serde_json::json!({"news_item_id":id,"embedding_model":"test-model","input_hash":hash,"encoder_version":news_lens_embeddings::ENCODER_VERSION});
                sqlx::query("INSERT INTO processing_tasks(task_type,status,queue_name,payload,completed_at,executor_runtime,executor_version,executor_namespace) VALUES('prepare_news_lens','failed','llm',$1,timezone('UTC',now()),'rust',1,'prepare_news_lens')").bind(payload).execute(&mut *connection).await.unwrap();
            }
            pending.push(newsly_db::BriefingUnassignedSource {
                pending_id: id,
                source_id: id,
                source_kind: "news".into(),
                enqueued_at: chrono::Utc::now(),
                source,
            });
        }
        drop(connection);
        let ready = ready_news(&pool, &pending, "test-model").await.unwrap();
        assert_eq!(ready.sources, vec![pending[0].clone()]);
        assert_eq!(ready.vectors, vec![vec![0.2, 0.8]]);
        assert_eq!(ready.failed, vec![pending[2].clone()]);
        assert_eq!(ready.requests.len(), 1);
        assert_eq!(
            ready.requests[0].payload.as_ref().unwrap()["news_item_id"],
            pending[1].source_id
        );

        let seed = newsly_db::PreparedBriefingRefreshSeed {
            task_id: 1,
            user_id: 1,
            mode: newsly_db::BriefingRefreshMode::Append,
            starting_version: 1,
            pending_added: 0,
            prepared_state_changed: false,
            lens_assignment: newsly_db::BriefingLensAssignmentSnapshot {
                pending_sources: pending.clone(),
                active_lenses: vec![],
                active_news_lens_keys: vec!["technology".into()],
                all_lens_keys: vec!["technology".into()],
                next_news_position: 1,
            },
            claim_fence: newsly_db::BriefingRefreshClaimFence {
                locked_by: "test".into(),
                lease_token: uuid::Uuid::nil(),
                retry_count: 0,
                executor_runtime: "rust".into(),
                executor_version: 1,
                executor_namespace: "briefing_refresh".into(),
            },
        };
        let healthy = newsly_db::BriefingPendingLensAssignment {
            pending_id: pending[0].pending_id,
            source_id: pending[0].source_id,
            source_kind: "news".into(),
            lens_key: "technology".into(),
        };
        let mut plan = newsly_db::BriefingLensAssignmentPlan {
            task_id: 1,
            user_id: 1,
            starting_version: 1,
            assignments: vec![healthy.clone()],
            centroid_mutations: vec![],
            new_lenses: vec![],
            usage: vec![],
        };
        let config = crate::briefing_refresh::BriefingRefreshWorkerConfig::from_env()
            .unwrap()
            .repository;
        crate::briefing_refresh::semantic_lenses::assign_failed_sources(
            &mut plan,
            &seed,
            &ready.failed,
            &config,
        );
        assert_eq!(plan.assignments[0], healthy);
        assert_eq!(plan.assignments[1].source_id, pending[2].source_id);
        assert_eq!(plan.assignments[1].lens_key, "misc");
        assert_eq!(plan.assignments.len(), 2); // Missing story remains pending; it is not assigned or marked read.
    }
}
