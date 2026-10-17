use super::*;
use crate::content::FirecrawlDemandPolicy;
use sqlx::PgPool;

async fn insert_story(pool: &PgPool, key: &str, scope: &str, metadata: Value) -> i64 {
    sqlx::query_scalar("INSERT INTO news_items(ingest_key,visibility_scope,platform,article_url,raw_metadata,ingested_at,created_at) VALUES($1,$2,$1,'https://example.com/story',$3,now(),now()) RETURNING id::bigint")
        .bind(key).bind(scope).bind(metadata).fetch_one(pool).await.unwrap()
}

async fn policy(pool: &PgPool, id: i64) -> FirecrawlDemandPolicy {
    let mut tx = pool.begin().await.unwrap();
    let preparation = prepare_enrichment(&mut tx, id).await.unwrap();
    let EnrichmentPreparation::Extract { demand_policy, .. } = preparation else {
        panic!("story with no reusable body must prepare extraction");
    };
    demand_policy
}

#[sqlx::test(migrations = false)]
async fn firecrawl_demand_follows_active_subscriber_visibility(pool: PgPool) {
    newsly_db::run_migrations(&pool).await.unwrap();
    let global = insert_story(
        &pool,
        "brutalist",
        "global",
        json!({"aggregator":{"topic":"science","topics":["science","politics"]}}),
    )
    .await;
    let private = insert_story(&pool, "hackernews", "user", json!({})).await;
    let other = insert_story(&pool, "rss", "global", json!({})).await;
    assert_eq!(
        policy(&pool, global).await,
        FirecrawlDemandPolicy::NoSubscriberDemand
    );
    assert_eq!(policy(&pool, private).await, FirecrawlDemandPolicy::Allowed);
    assert_eq!(policy(&pool, other).await, FirecrawlDemandPolicy::Allowed);
    let user: i64 = sqlx::query_scalar("INSERT INTO users(apple_id,email,is_active,is_admin) VALUES('firecrawl-demand','demand@example.com',true,false) RETURNING id::bigint")
        .fetch_one(&pool).await.unwrap();
    sqlx::query("INSERT INTO user_scraper_configs(user_id,scraper_type,feed_url,config,is_active) VALUES($1::bigint::integer,'aggregator','aggregator://brutalist','{\"key\":\"brutalist\",\"topics\":[\"business\"]}',true)")
        .bind(user).execute(&pool).await.unwrap();
    assert_eq!(
        policy(&pool, global).await,
        FirecrawlDemandPolicy::NoSubscriberDemand
    );
    sqlx::query("UPDATE user_scraper_configs SET config='{\"key\":\"brutalist\",\"topics\":[\"science\"]}' WHERE user_id::bigint=$1")
        .bind(user).execute(&pool).await.unwrap();
    assert_eq!(policy(&pool, global).await, FirecrawlDemandPolicy::Allowed);
    sqlx::query("UPDATE user_scraper_configs SET is_active=false WHERE user_id::bigint=$1")
        .bind(user)
        .execute(&pool)
        .await
        .unwrap();
    assert_eq!(
        policy(&pool, global).await,
        FirecrawlDemandPolicy::NoSubscriberDemand
    );
    sqlx::query("UPDATE user_scraper_configs SET is_active=true WHERE user_id::bigint=$1")
        .bind(user)
        .execute(&pool)
        .await
        .unwrap();
    sqlx::query("UPDATE users SET is_active=false WHERE id::bigint=$1")
        .bind(user)
        .execute(&pool)
        .await
        .unwrap();
    assert_eq!(
        policy(&pool, global).await,
        FirecrawlDemandPolicy::NoSubscriberDemand
    );
}

#[sqlx::test(migrations = false)]
async fn firecrawl_demand_denial_is_replayable_and_allows_metadata_processing(pool: PgPool) {
    newsly_db::run_migrations(&pool).await.unwrap();
    let id = insert_story(&pool, "hackernews", "global", json!({})).await;
    let queue = QueueKernel::new(pool.clone());
    let mut tx = pool.begin().await.unwrap();
    let preparation = prepare_enrichment(&mut tx, id).await.unwrap();
    let plan = EnrichmentFinalizationPlan {
        task_id: 17,
        snapshot: preparation.snapshot().unwrap().clone(),
        mutation: EnrichmentMutation::MetadataOnly {
            article_url: "https://example.com/story".to_owned(),
            reason: "No active subscriber sees this story".to_owned(),
        },
        usage: Vec::new(),
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
    assert_eq!(metadata["article_extraction"]["status"], "failed");
    assert_eq!(metadata["article_extraction"]["strategy"], "metadata_only");
    assert_eq!(
        metadata["article_extraction"]["error_code"],
        "fallback_not_permitted"
    );
    assert!(metadata.get("article_body_ref").is_none());
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
async fn firecrawl_demand_subscription_during_extraction_reprepares_without_queue_deadlock(
    pool: PgPool,
) {
    use crate::news_item::finalizer::EnrichmentFinalizer;
    use crate::{TaskFinalizer, TaskFinalizerResult};
    use newsly_domain::{ResourceKey, RuntimeOwner};
    use newsly_queue::{ClaimRequest, ClaimRuntimeScope, TaskQueue, TaskResult};
    use std::time::Duration;
    newsly_db::run_migrations(&pool).await.unwrap();
    let id = insert_story(&pool, "hackernews", "global", json!({})).await;
    let user: i64 = sqlx::query_scalar("INSERT INTO users(apple_id,email,is_active,is_admin) VALUES('during-extraction','during@example.com',true,false) RETURNING id::bigint")
        .fetch_one(&pool).await.unwrap();
    let queue = QueueKernel::new(pool.clone());
    let mut request = EnqueueRequest::new(TaskType::EnrichNewsItemArticle);
    request.payload = json!({"news_item_id":id}).as_object().cloned();
    request.dedupe_key = Some(format!("enrich-news-demand:{id}"));
    let task = queue.enqueue(request.clone()).await.unwrap();
    let scope = ClaimRuntimeScope::namespaces(
        RuntimeOwner::Rust,
        [ResourceKey::new("enrich_news_item_article").unwrap()],
    )
    .unwrap();
    let claim = queue
        .claim(&ClaimRequest::for_queue(
            "demand-race",
            TaskQueue::Content,
            scope,
        ))
        .await
        .unwrap()
        .unwrap();
    let mut tx = pool.begin().await.unwrap();
    let preparation = prepare_enrichment(&mut tx, id).await.unwrap();
    tx.commit().await.unwrap();
    let finalizer = EnrichmentFinalizer::new(
        queue.clone(),
        EnrichmentFinalizationPlan {
            task_id: task,
            snapshot: preparation.snapshot().unwrap().clone(),
            mutation: EnrichmentMutation::MetadataOnly {
                article_url: "https://example.com/story".to_owned(),
                reason: "No audience at preparation".to_owned(),
            },
            usage: Vec::new(),
            finalized_at: Utc::now(),
        },
    );
    // Exact finalization holds the processing task row, while the subscription
    // transaction holds the audience lock. Dedupe must not require that row lock.
    let mut fenced = queue
        .begin_fenced_finalization(&claim, &TaskResult::ok(), 3)
        .await
        .unwrap()
        .unwrap();
    let mut subscription = pool.begin().await.unwrap();
    newsly_db::aggregator_corpus::lock_aggregator_demand(&mut subscription)
        .await
        .unwrap();
    let result = {
        let finalization = finalizer.apply(fenced.transaction_mut());
        tokio::pin!(finalization);
        assert!(
            tokio::time::timeout(Duration::from_millis(30), finalization.as_mut())
                .await
                .is_err(),
            "denial must serialize behind the subscriber transition"
        );
        sqlx::query("INSERT INTO user_scraper_configs(user_id,scraper_type,feed_url,config,is_active) VALUES($1::bigint::integer,'aggregator','aggregator://hackernews','{\"key\":\"hackernews\"}',true)")
            .bind(user).execute(&mut *subscription).await.unwrap();
        let replay = tokio::time::timeout(
            Duration::from_secs(2),
            queue.enqueue_many_in_transaction(&mut subscription, vec![request]),
        )
        .await
        .expect("replay dedupe must not deadlock against the queue fence")
        .unwrap();
        assert_eq!(replay.task_ids, vec![task]);
        subscription.commit().await.unwrap();
        tokio::time::timeout(Duration::from_secs(2), finalization)
            .await
            .unwrap()
            .unwrap()
    };
    let TaskFinalizerResult::Override(result) = result else {
        panic!("new demand must reprepare");
    };
    assert!(result.deferred);
    assert_eq!(result.retry_delay_seconds, Some(1));
    fenced.replace_result(&result, 3).unwrap();
    assert!(fenced.finish().await.unwrap().is_some());
    let row: (String, i32) =
        sqlx::query_as("SELECT status,retry_count FROM processing_tasks WHERE id::bigint=$1")
            .bind(task)
            .fetch_one(&pool)
            .await
            .unwrap();
    assert_eq!(row, ("pending".to_owned(), 0));
    let metadata: Value =
        sqlx::query_scalar("SELECT raw_metadata FROM news_items WHERE id::bigint=$1")
            .bind(id)
            .fetch_one(&pool)
            .await
            .unwrap();
    assert!(
        metadata.get("article_extraction").is_none(),
        "no denial marker can escape after demand appears"
    );
    let summaries: i64 = sqlx::query_scalar(
        "SELECT count(*) FROM processing_tasks WHERE task_type='process_news_item'",
    )
    .fetch_one(&pool)
    .await
    .unwrap();
    assert_eq!(
        summaries, 0,
        "recovery cannot acquire a metadata-only summary"
    );
}

#[sqlx::test(migrations = false)]
async fn firecrawl_demand_full_body_replay_invalidates_metadata_only_summary(pool: PgPool) {
    use crate::news_item::storage::NewsArticleBodyStore;
    newsly_db::run_migrations(&pool).await.unwrap();
    let id = insert_story(
        &pool,
        "hackernews",
        "global",
        json!({
            "article_extraction":{"error_code":"fallback_not_permitted"},
            "summary_version":1,"summary_kind":"short_news",
            "summary":{"title":"Metadata-only headline","summary":"Only the headline was available"}
        }),
    )
    .await;
    let mut tx = pool.begin().await.unwrap();
    assert!(
        prepare_processing(&mut tx, id)
            .await
            .unwrap()
            .unwrap()
            .reusable_summary
            .is_some(),
        "fixture must demonstrate prior summary reuse"
    );
    tx.rollback().await.unwrap();
    let dir = tempfile::tempdir().unwrap();
    let store = NewsArticleBodyStore::new(dir.path().to_path_buf(), "bodies".into()).unwrap();
    let body = store
        .stage(
            id,
            "Full article evidence with details unavailable in the headline",
        )
        .await
        .unwrap();
    let mut tx = pool.begin().await.unwrap();
    let preparation = prepare_enrichment(&mut tx, id).await.unwrap();
    let plan = EnrichmentFinalizationPlan {
        task_id: 17,
        snapshot: preparation.snapshot().unwrap().clone(),
        mutation: EnrichmentMutation::Storage {
            article_url: "https://example.com/story".to_owned(),
            final_url: "https://example.com/story".to_owned(),
            title: Some("Full article".to_owned()),
            article_domain: Some("example.com".to_owned()),
            extraction_method: "firecrawl".to_owned(),
            body,
        },
        usage: Vec::new(),
        finalized_at: Utc::now(),
    };
    apply_enrichment(&mut tx, &QueueKernel::new(pool.clone()), &plan)
        .await
        .unwrap();
    tx.commit().await.unwrap();
    let mut tx = pool.begin().await.unwrap();
    let processing = prepare_processing(&mut tx, id).await.unwrap().unwrap();
    assert!(
        processing.reusable_summary.is_none(),
        "full article must be summarized anew"
    );
    assert_eq!(
        processing.snapshot.raw_metadata["summary"]["summary"], "Only the headline was available",
        "keep the previous display summary until replacement publication"
    );
    assert!(matches!(
        processing.snapshot.body_source,
        BodySource::Stored(_)
    ));
}

#[sqlx::test(migrations = false)]
async fn firecrawl_demand_full_body_replay_rejects_metadata_only_sibling_summary(pool: PgPool) {
    use crate::news_item::storage::NewsArticleBodyStore;
    newsly_db::run_migrations(&pool).await.unwrap();
    let id = insert_story(
        &pool,
        "hackernews",
        "global",
        json!({"article_extraction":{"error_code":"fallback_not_permitted"}}),
    )
    .await;
    let sibling=insert_story(&pool,"brutalist","global",json!({
        "article_extraction":{"error_code":"fallback_not_permitted"},"summary_version":1,
        "summary_kind":"short_news","summary":{"title":"Metadata-only headline","summary":"Denied sibling evidence"}
    })).await;
    sqlx::query("UPDATE news_items SET status='ready',canonical_story_url='https://example.com/story' WHERE id::bigint=$1")
        .bind(sibling).execute(&pool).await.unwrap();
    let dir = tempfile::tempdir().unwrap();
    let store = NewsArticleBodyStore::new(dir.path().to_path_buf(), "bodies".into()).unwrap();
    let body = store
        .stage(
            id,
            "Complete article evidence must supersede the sibling headline",
        )
        .await
        .unwrap();
    let mut tx = pool.begin().await.unwrap();
    let preparation = prepare_enrichment(&mut tx, id).await.unwrap();
    let plan = EnrichmentFinalizationPlan {
        task_id: 17,
        snapshot: preparation.snapshot().unwrap().clone(),
        mutation: EnrichmentMutation::Storage {
            article_url: "https://example.com/story".to_owned(),
            final_url: "https://example.com/story".to_owned(),
            title: None,
            article_domain: None,
            extraction_method: "firecrawl".to_owned(),
            body,
        },
        usage: Vec::new(),
        finalized_at: Utc::now(),
    };
    apply_enrichment(&mut tx, &QueueKernel::new(pool.clone()), &plan)
        .await
        .unwrap();
    tx.commit().await.unwrap();
    for error_code in ["fallback_not_permitted", "provider_failed"] {
        sqlx::query("UPDATE news_items SET raw_metadata=jsonb_set(raw_metadata::jsonb,'{article_extraction,error_code}',to_jsonb($2::text))::json WHERE id::bigint=$1")
            .bind(sibling).bind(error_code).execute(&pool).await.unwrap();
        let mut tx = pool.begin().await.unwrap();
        let processing = prepare_processing(&mut tx, id).await.unwrap().unwrap();
        assert!(
            processing.reusable_summary.is_none(),
            "full evidence cannot reuse {error_code} metadata-only sibling"
        );
        assert!(processing.reusable_representative_id.is_none());
        tx.rollback().await.unwrap();
    }
}
