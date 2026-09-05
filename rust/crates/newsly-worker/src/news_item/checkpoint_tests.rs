use super::*;
use newsly_domain::{ResourceKey, RuntimeOwner};
use newsly_queue::{ClaimRequest, ClaimRuntimeScope, TaskQueue};
use sqlx::PgPool;

#[sqlx::test]
async fn summary_checkpoint_survives_relation_failure_but_rejects_changed_input(pool: PgPool) {
    newsly_db::run_migrations(&pool).await.unwrap();
    let id: i64 = sqlx::query_scalar(r#"INSERT INTO news_items (ingest_key, visibility_scope, article_url, raw_metadata, ingested_at, created_at) VALUES ('checkpoint-test', 'global', 'https://example.com/story', '{"article": {"title": "Original title"}}', now(), now()) RETURNING id::bigint"#).fetch_one(&pool).await.unwrap();
    let queue = QueueKernel::new(pool.clone());
    let mut request = EnqueueRequest::new(TaskType::ProcessNewsItem);
    request.payload = json!({"news_item_id": id}).as_object().cloned();
    queue.enqueue(request).await.unwrap();
    let scope = ClaimRuntimeScope::namespaces(
        RuntimeOwner::Rust,
        [ResourceKey::new("process_news_item").unwrap()],
    )
    .unwrap();
    let claim_request = ClaimRequest::for_queue("checkpoint-test", TaskQueue::Content, scope);
    let claim = queue.claim(&claim_request).await.unwrap().unwrap();
    let mut tx = pool.begin().await.unwrap();
    let prepared = prepare_processing(&mut tx, id).await.unwrap().unwrap();
    tx.commit().await.unwrap();
    let summary: newsly_providers::NewsSummary = serde_json::from_value(json!({
        "title":"Saved summary", "article_url":"https://example.com/story", "key_points":["A fact"],
        "summary":"A saved summary", "classification":"to_read", "summarization_date":Utc::now()
    }))
    .unwrap();
    assert!(
        checkpoint_summary(&queue, &claim, &prepared.snapshot, &summary, &[])
            .await
            .unwrap()
    );
    sqlx::query(r#"UPDATE news_items SET status = 'new', raw_metadata = (raw_metadata::jsonb || '{"processing_error":"embedding timeout"}'::jsonb)::json WHERE id::bigint = $1"#).bind(id).execute(&pool).await.unwrap();
    let mut tx = pool.begin().await.unwrap();
    let retry = prepare_processing(&mut tx, id).await.unwrap().unwrap();
    tx.commit().await.unwrap();
    assert_eq!(retry.reusable_summary, Some(summary.clone()));
    sqlx::query(
        "UPDATE news_items SET article_url = 'https://example.com/changed' WHERE id::bigint = $1",
    )
    .bind(id)
    .execute(&pool)
    .await
    .unwrap();
    assert!(
        !checkpoint_summary(&queue, &claim, &prepared.snapshot, &summary, &[])
            .await
            .unwrap()
    );
    let mut tx = pool.begin().await.unwrap();
    let changed = prepare_processing(&mut tx, id).await.unwrap().unwrap();
    assert!(changed.reusable_summary.is_none());
    tx.commit().await.unwrap();
    sqlx::query("UPDATE processing_tasks SET lease_expires_at = now() - interval '1 second' WHERE id::bigint = $1").bind(claim.id).execute(&pool).await.unwrap();
    assert!(
        !checkpoint_summary(&queue, &claim, &changed.snapshot, &summary, &[])
            .await
            .unwrap()
    );
}
