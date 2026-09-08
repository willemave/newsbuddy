use super::*;
use crate::image_generation::{
    model::PreparedImageAttempt,
    prompt::{build_infographic_prompt, image_input_fingerprint},
    repository::load_image_snapshot,
    storage::ImageFileStore,
};
use crate::{
    HandlerExecution, HandlerFuture, HandlerRegistry, LeaseHealth, TaskHandler, WorkerConfig,
    WorkerKernel,
};
use newsly_domain::{ResourceKey, RuntimeOwner};
use newsly_providers::ImageGenerationUsage;
use newsly_queue::{
    ClaimRequest, ClaimRuntimeScope, EnqueueRequest, OwnedWorkPlan, TaskQueue, TaskType,
};
use serde_json::json;
use sqlx::PgPool;
use std::{
    io::Cursor,
    sync::{Arc, Mutex},
};

#[derive(Debug)]
struct PreparedImageHandler(Mutex<Option<ImageFinalizer>>);
impl TaskHandler for PreparedImageHandler {
    fn task_type(&self) -> TaskType {
        TaskType::GenerateImage
    }
    fn execute(&self, _plan: Arc<OwnedWorkPlan>, _lease: LeaseHealth) -> HandlerFuture<'_> {
        let finalizer = self.0.lock().unwrap().take().unwrap();
        Box::pin(async move { HandlerExecution::with_finalizer(TaskResult::ok(), finalizer) })
    }
}

async fn finalize_case(pool: PgPool, changed: bool) {
    newsly_db::run_migrations(&pool).await.unwrap();
    let user: i64 = sqlx::query_scalar("INSERT INTO users(apple_id,email,is_active,is_admin) VALUES('image-finalizer','image-finalizer@example.com',true,false) RETURNING id::bigint").fetch_one(&pool).await.unwrap();
    let content_id: i64 = sqlx::query_scalar("INSERT INTO contents(content_type,url,title,status,is_aggregate,content_metadata) VALUES('article','https://example.com/image','Test','awaiting_image',false,$1) RETURNING id::bigint")
        .bind(json!({"summary":{"title":"Test","overview":"An evidence-backed result."}})).fetch_one(&pool).await.unwrap();
    sqlx::query("INSERT INTO content_status(user_id,content_id,status) VALUES($1::bigint::integer,$2::bigint::integer,'inbox')").bind(user).bind(content_id).execute(&pool).await.unwrap();
    let queue = QueueKernel::new(pool.clone());
    let mut enqueue = EnqueueRequest::new(TaskType::GenerateImage);
    enqueue.content_id = Some(content_id);
    let task_id = queue.enqueue(enqueue).await.unwrap();
    let mut tx = pool.begin().await.unwrap();
    let content = load_image_snapshot(&mut tx, content_id)
        .await
        .unwrap()
        .unwrap();
    tx.commit().await.unwrap();
    let fingerprint = image_input_fingerprint(
        &build_infographic_prompt(
            "article",
            content.title.as_deref(),
            &content.content_metadata,
        )
        .unwrap(),
    );
    let root = tempfile::tempdir().unwrap();
    let store = ImageFileStore::new(root.path().to_path_buf(), 2_000_000).unwrap();
    let mut bytes = Cursor::new(Vec::new());
    image::DynamicImage::new_rgb8(640, 360)
        .write_to(&mut bytes, image::ImageFormat::Png)
        .unwrap();
    let staged = store
        .stage(&pool, content_id, task_id, bytes.get_ref())
        .await
        .unwrap();
    let image_path = root
        .path()
        .join(staged.image_url().strip_prefix("/static/images/").unwrap());
    let finalizer = ImageFinalizer::new(
        ImageFinalizationPlan {
            attempt: PreparedImageAttempt {
                task_id,
                content,
                input_fingerprint: fingerprint,
                force: false,
            },
            staged,
            generated_at: chrono::Utc::now(),
            usage: ImageGenerationUsage {
                provider: "fixture".to_owned(),
                model: "fixture".to_owned(),
                request_id: None,
                input_tokens: None,
                cache_read_tokens: None,
                output_tokens: None,
                total_tokens: None,
                request_count: 1,
                response_cost_usd: None,
                metadata: json!({}),
            },
        },
        queue.clone(),
        0,
        1,
    );
    if changed {
        sqlx::query("UPDATE contents SET content_metadata = $1 WHERE id = $2")
            .bind(json!({"summary":{"title":"Changed","overview":"Different evidence."}}))
            .bind(content_id)
            .execute(&pool)
            .await
            .unwrap();
    }
    let scope = ClaimRuntimeScope::namespaces(
        RuntimeOwner::Rust,
        [ResourceKey::new("generate_image").unwrap()],
    )
    .unwrap();
    let mut claim = ClaimRequest::for_queue("image-finalizer-test", TaskQueue::Image, scope);
    claim.task_type = Some(TaskType::GenerateImage);
    let mut handlers = HandlerRegistry::new();
    handlers
        .register(PreparedImageHandler(Mutex::new(Some(finalizer))))
        .unwrap();
    WorkerKernel::new(queue, handlers, WorkerConfig::new(claim), None)
        .unwrap()
        .run_once()
        .await
        .unwrap();
    let status: String = sqlx::query_scalar("SELECT status FROM contents WHERE id = $1")
        .bind(content_id)
        .fetch_one(&pool)
        .await
        .unwrap();
    let followups: i64 = sqlx::query_scalar(
        "SELECT count(*) FROM processing_tasks WHERE task_type = 'briefing_refresh'",
    )
    .fetch_one(&pool)
    .await
    .unwrap();
    assert_eq!(
        status,
        if changed {
            "awaiting_image"
        } else {
            "completed"
        }
    );
    assert_eq!(followups, i64::from(!changed));
    assert_eq!(image_path.is_file(), !changed);
}

#[sqlx::test]
async fn artwork_publication_commits_files_readiness_and_briefing_fanout(pool: PgPool) {
    finalize_case(pool, false).await;
}

#[sqlx::test]
async fn changed_summary_cannot_publish_artwork_or_briefing_fanout(pool: PgPool) {
    finalize_case(pool, true).await;
}
