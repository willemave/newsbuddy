use super::*;
use sqlx::PgPool;

#[sqlx::test(migrations = false)]
async fn firecrawl_deferral_keeps_article_pending_until_full_body_arrives(pool: PgPool) {
    newsly_db::run_migrations(&pool).await.unwrap();
    let id: i64 = sqlx::query_scalar(r#"INSERT INTO news_items(ingest_key,visibility_scope,article_url,raw_metadata,ingested_at,created_at) VALUES('credit-deferral','global','https://example.com/story','{"article":{"title":"Complete coverage"}}',now(),now()) RETURNING id::bigint"#).fetch_one(&pool).await.unwrap();
    let queue = QueueKernel::new(pool.clone());
    let mut tx = pool.begin().await.unwrap();
    let preparation = prepare_enrichment(&mut tx, id).await.unwrap();
    let snapshot = preparation.snapshot().unwrap().clone();
    let plan = EnrichmentFinalizationPlan {
        task_id: 17,
        snapshot: snapshot.clone(),
        mutation: EnrichmentMutation::Deferred {
            article_url: Some("https://example.com/story".to_owned()),
            reason: "Credit prerequisite unavailable".to_owned(),
            resume_at: Utc::now() + chrono::Duration::minutes(15),
        },
        finalized_at: Utc::now(),
    };
    assert_eq!(
        apply_enrichment(&mut tx, &queue, &plan).await.unwrap(),
        NewsApplyOutcome::Applied
    );
    tx.commit().await.unwrap();
    let metadata: Value =
        sqlx::query_scalar("SELECT raw_metadata FROM news_items WHERE id::bigint=$1")
            .bind(id)
            .fetch_one(&pool)
            .await
            .unwrap();
    assert_eq!(metadata["article_extraction"]["status"], "deferred");
    assert!(metadata.get("article_body_ref").is_none());
    let tasks: i64 = sqlx::query_scalar(
        "SELECT count(*) FROM processing_tasks WHERE task_type='process_news_item'",
    )
    .fetch_one(&pool)
    .await
    .unwrap();
    assert_eq!(
        tasks, 0,
        "metadata-only processing must not make recovery reuse an incomplete summary"
    );
    let mut tx = pool.begin().await.unwrap();
    let refreshed = prepare_enrichment(&mut tx, id).await.unwrap();
    let plan = EnrichmentFinalizationPlan {
        snapshot: refreshed.snapshot().unwrap().clone(),
        mutation: EnrichmentMutation::Metadata {
            text: "Complete recovered article body".to_owned(),
            source_url: Some("https://example.com/story".to_owned()),
        },
        ..plan
    };
    apply_enrichment(&mut tx, &queue, &plan).await.unwrap();
    tx.commit().await.unwrap();
    let metadata: Value =
        sqlx::query_scalar("SELECT raw_metadata FROM news_items WHERE id::bigint=$1")
            .bind(id)
            .fetch_one(&pool)
            .await
            .unwrap();
    assert_eq!(metadata["article_extraction"]["status"], "completed");
    assert!(metadata.get("article_extraction_resume_at").is_none());
    let tasks: i64 = sqlx::query_scalar(
        "SELECT count(*) FROM processing_tasks WHERE task_type='process_news_item'",
    )
    .fetch_one(&pool)
    .await
    .unwrap();
    assert_eq!(tasks, 1);
}

#[sqlx::test(migrations = false)]
async fn firecrawl_deferral_fences_product_state_and_preserves_retry_budget(pool: PgPool) {
    use newsly_domain::{ResourceKey, RuntimeOwner};
    use newsly_queue::{ClaimRequest, ClaimRuntimeScope, TaskQueue, TaskResult};
    newsly_db::run_migrations(&pool).await.unwrap();
    let id:i64=sqlx::query_scalar(r#"INSERT INTO news_items(ingest_key,visibility_scope,article_url,raw_metadata,ingested_at,created_at) VALUES('credit-fence','global','https://example.com/story','{"article":{"title":"Pending evidence"}}',now(),now()) RETURNING id::bigint"#).fetch_one(&pool).await.unwrap();
    let queue = QueueKernel::new(pool.clone());
    let mut request = EnqueueRequest::new(TaskType::EnrichNewsItemArticle);
    request.payload = json!({"news_item_id":id}).as_object().cloned();
    queue.enqueue(request).await.unwrap();
    let scope = ClaimRuntimeScope::namespaces(
        RuntimeOwner::Rust,
        [ResourceKey::new("enrich_news_item_article").unwrap()],
    )
    .unwrap();
    let claim = queue
        .claim(&ClaimRequest::for_queue(
            "credit-fence",
            TaskQueue::Content,
            scope,
        ))
        .await
        .unwrap()
        .unwrap();
    let mut tx = pool.begin().await.unwrap();
    let preparation = prepare_enrichment(&mut tx, id).await.unwrap();
    tx.commit().await.unwrap();
    let plan = EnrichmentFinalizationPlan {
        task_id: claim.id,
        snapshot: preparation.snapshot().unwrap().clone(),
        mutation: EnrichmentMutation::Deferred {
            article_url: Some("https://example.com/story".to_owned()),
            reason: "Credit prerequisite".to_owned(),
            resume_at: Utc::now() + chrono::Duration::minutes(15),
        },
        finalized_at: Utc::now(),
    };
    let result = TaskResult::defer(900);
    let mut fenced = queue
        .begin_fenced_finalization(&claim, &result, 3)
        .await
        .unwrap()
        .unwrap();
    apply_enrichment(fenced.transaction_mut(), &queue, &plan)
        .await
        .unwrap();
    assert!(fenced.finish().await.unwrap().is_some());
    let row:(String,i32,bool)=sqlx::query_as("SELECT status,retry_count,available_at>timezone('UTC',now()) FROM processing_tasks WHERE id::bigint=$1").bind(claim.id).fetch_one(&pool).await.unwrap();
    assert_eq!(row, ("pending".to_owned(), 0, true));
    assert!(
        queue
            .begin_fenced_finalization(&claim, &result, 3)
            .await
            .unwrap()
            .is_none(),
        "old claim cannot publish after deferral"
    );
}

#[sqlx::test(migrations = false)]
async fn firecrawl_demand_replay_credit_pause_cannot_reuse_the_old_metadata_summary(pool: PgPool) {
    newsly_db::run_migrations(&pool).await.unwrap();
    let id:i64=sqlx::query_scalar("INSERT INTO news_items(ingest_key,visibility_scope,article_url,raw_metadata,ingested_at,created_at) VALUES('denied-credit-replay','global','https://example.com/story',$1,now(),now()) RETURNING id::bigint")
        .bind(json!({"article_extraction":{"error_code":"fallback_not_permitted"},"summary_version":1,"summary_kind":"short_news","summary":{"title":"Metadata-only headline","summary":"Old metadata evidence"}}))
        .fetch_one(&pool).await.unwrap();
    let queue = QueueKernel::new(pool.clone());
    let mut tx = pool.begin().await.unwrap();
    let preparation = prepare_enrichment(&mut tx, id).await.unwrap();
    let mut plan = EnrichmentFinalizationPlan {
        task_id: 17,
        snapshot: preparation.snapshot().unwrap().clone(),
        mutation: EnrichmentMutation::Deferred {
            article_url: Some("https://example.com/story".to_owned()),
            reason: "Credit paused on replay".to_owned(),
            resume_at: Utc::now() + chrono::Duration::minutes(15),
        },
        finalized_at: Utc::now(),
    };
    apply_enrichment(&mut tx, &queue, &plan).await.unwrap();
    tx.commit().await.unwrap();
    let dir = tempfile::tempdir().unwrap();
    let store = crate::news_item::storage::NewsArticleBodyStore::new(
        dir.path().to_path_buf(),
        "bodies".into(),
    )
    .unwrap();
    let body = store
        .stage(id, "Complete evidence after Firecrawl credit recovers")
        .await
        .unwrap();
    let mut tx = pool.begin().await.unwrap();
    let preparation = prepare_enrichment(&mut tx, id).await.unwrap();
    plan.snapshot = preparation.snapshot().unwrap().clone();
    assert!(plan.snapshot.raw_metadata.get("summary_version").is_none());
    plan.mutation = EnrichmentMutation::Storage {
        article_url: "https://example.com/story".to_owned(),
        final_url: "https://example.com/story".to_owned(),
        title: None,
        article_domain: None,
        extraction_method: "firecrawl".to_owned(),
        body,
    };
    apply_enrichment(&mut tx, &queue, &plan).await.unwrap();
    tx.commit().await.unwrap();
    let mut tx = pool.begin().await.unwrap();
    let processing = prepare_processing(&mut tx, id).await.unwrap().unwrap();
    assert!(
        processing.reusable_summary.is_none(),
        "credit pause must not lose replay's summary invalidation"
    );
}
