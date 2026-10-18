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
        "summary":"A saved summary", "classification":"skip", "summarization_date":Utc::now()
    }))
    .unwrap();
    assert!(
        checkpoint_summary(&queue, &claim, &prepared.snapshot, &summary)
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
        !checkpoint_summary(&queue, &claim, &prepared.snapshot, &summary)
            .await
            .unwrap()
    );
    let mut tx = pool.begin().await.unwrap();
    let changed = prepare_processing(&mut tx, id).await.unwrap().unwrap();
    assert!(changed.reusable_summary.is_none());
    tx.commit().await.unwrap();
    sqlx::query("UPDATE processing_tasks SET lease_expires_at = now() - interval '1 second' WHERE id::bigint = $1").bind(claim.id).execute(&pool).await.unwrap();
    assert!(
        !checkpoint_summary(&queue, &claim, &changed.snapshot, &summary)
            .await
            .unwrap()
    );
}

#[sqlx::test(migrations = false)]
async fn refetch_discovery_time_preserves_news_work_but_title_changes_invalidate(pool: PgPool) {
    use newsly_db::{ScrapedNewsRecord, persist_scraped_news};
    newsly_db::run_migrations(&pool).await.unwrap();
    let mut record = ScrapedNewsRecord {
        visibility_scope: "global".to_owned(),
        owner_user_id: None,
        platform: "hackernews".to_owned(),
        source_type: "Hacker News".to_owned(),
        source_label: None,
        source_external_id: Some("discovery-fixture".to_owned()),
        user_scraper_config_id: None,
        canonical_item_url: Some("https://news.ycombinator.com/item?id=1".to_owned()),
        canonical_story_url: Some("https://example.com/story".to_owned()),
        article_url: Some("https://example.com/story".to_owned()),
        article_domain: Some("example.com".to_owned()),
        discussion_url: None,
        article_title: Some("Original title".to_owned()),
        summary_key_points: Vec::new(),
        summary_text: None,
        status: "new".to_owned(),
        published_at: None,
        raw_metadata: json!({"article":{"title":"Original title"},"discovery_time":"2026-10-17T12:00:00Z"}),
    };
    let mut tx = pool.begin().await.unwrap();
    let persisted = persist_scraped_news(&mut tx, &record).await.unwrap();
    let id = persisted.news_item_id;
    assert!(persisted.created);
    tx.commit().await.unwrap();
    let queue = QueueKernel::new(pool.clone());
    let mut request = EnqueueRequest::new(TaskType::ProcessNewsItem);
    request.payload = json!({"news_item_id":id}).as_object().cloned();
    queue.enqueue(request).await.unwrap();
    let scope = ClaimRuntimeScope::namespaces(
        RuntimeOwner::Rust,
        [ResourceKey::new("process_news_item").unwrap()],
    )
    .unwrap();
    let claim = queue
        .claim(&ClaimRequest::for_queue(
            "discovery-stable",
            TaskQueue::Content,
            scope,
        ))
        .await
        .unwrap()
        .unwrap();
    let mut tx = pool.begin().await.unwrap();
    let enrichment = prepare_enrichment(&mut tx, id).await.unwrap();
    let processing = prepare_processing(&mut tx, id).await.unwrap().unwrap();
    tx.commit().await.unwrap();
    // A later check sees the exact same source input, only its own clock changed.
    record.raw_metadata["discovery_time"] = json!("2026-10-17T12:20:00Z");
    let mut tx = pool.begin().await.unwrap();
    let repeated = persist_scraped_news(&mut tx, &record).await.unwrap();
    assert_eq!(repeated.news_item_id, id);
    assert!(!repeated.created);
    let row = load_news_row(&mut tx, id).await.unwrap().unwrap();
    assert_eq!(
        source_fingerprint(&row),
        enrichment.snapshot().unwrap().fingerprint,
        "identical re-fetch must not invalidate in-flight extraction"
    );
    assert_eq!(row.raw_metadata["discovery_time"], "2026-10-17T12:00:00Z");
    tx.commit().await.unwrap();
    let summary = newsly_providers::NewsSummary {
        title: "Saved summary".to_owned(),
        article_url: Some("https://example.com/story".to_owned()),
        key_points: vec!["A fact".to_owned()],
        summary: "A saved summary".to_owned(),
        summarization_date: Utc::now(),
    };
    assert!(
        checkpoint_summary(&queue, &claim, &processing.snapshot, &summary)
            .await
            .unwrap(),
        "unchanged source must accept paid processing output behind the same lease"
    );
    // Real input changes still fence both enrichment and model checkpoints.
    record.raw_metadata["article"]["title"] = json!("Updated article title");
    let mut tx = pool.begin().await.unwrap();
    persist_scraped_news(&mut tx, &record).await.unwrap();
    let row = load_news_row(&mut tx, id).await.unwrap().unwrap();
    assert_ne!(
        source_fingerprint(&row),
        enrichment.snapshot().unwrap().fingerprint
    );
    tx.commit().await.unwrap();
    assert!(
        !checkpoint_summary(&queue, &claim, &processing.snapshot, &summary)
            .await
            .unwrap()
    );
}

#[sqlx::test(migrations = false)]
async fn polling_metrics_preserve_news_work_and_prompts_but_evidence_changes_do_not(pool: PgPool) {
    newsly_db::run_migrations(&pool).await.unwrap();
    let metadata = json!({
        "article":{"url":"https://example.com/story","title":"Original title","source_domain":"example.com"},
        "aggregator":{"key":"hackernews","name":"Hacker News","title":"Original title","external_id":"123","author":"author",
            "metadata":{"score":100,"comments_count":5,"item_type":"story","timestamp":1_792_240_000,"hn_linked_url":"https://example.com/story"}},
        "discussion_url":"https://news.ycombinator.com/item?id=123","excerpt":"Original excerpt",
        "discovery_time":"2026-10-17T12:00:00Z","comment_count":5,
        "discussion_payload":{"compact_comments":["Original discussion evidence"]}
    });
    let mut record = newsly_db::ScrapedNewsRecord {
        visibility_scope: "global".to_owned(),
        owner_user_id: None,
        platform: "hackernews".to_owned(),
        source_type: "Hacker News".to_owned(),
        source_label: None,
        source_external_id: Some("123".to_owned()),
        user_scraper_config_id: None,
        canonical_item_url: Some("https://news.ycombinator.com/item?id=123".to_owned()),
        canonical_story_url: Some("https://example.com/story".to_owned()),
        article_url: Some("https://example.com/story".to_owned()),
        article_domain: Some("example.com".to_owned()),
        discussion_url: Some("https://news.ycombinator.com/item?id=123".to_owned()),
        article_title: Some("Original title".to_owned()),
        summary_key_points: Vec::new(),
        summary_text: None,
        raw_metadata: metadata.clone(),
        status: "new".to_owned(),
        published_at: None,
    };
    let mut tx = pool.begin().await.unwrap();
    let id = newsly_db::persist_scraped_news(&mut tx, &record)
        .await
        .unwrap()
        .news_item_id;
    tx.commit().await.unwrap();
    let queue = QueueKernel::new(pool.clone());
    let mut request = EnqueueRequest::new(TaskType::ProcessNewsItem);
    request.payload = json!({"news_item_id":id}).as_object().cloned();
    queue.enqueue(request).await.unwrap();
    let scope = ClaimRuntimeScope::namespaces(
        RuntimeOwner::Rust,
        [ResourceKey::new("process_news_item").unwrap()],
    )
    .unwrap();
    let claim = queue
        .claim(&ClaimRequest::for_queue(
            "metrics-stable",
            TaskQueue::Content,
            scope,
        ))
        .await
        .unwrap()
        .unwrap();
    let mut tx = pool.begin().await.unwrap();
    let prepared = prepare_processing(&mut tx, id).await.unwrap().unwrap();
    tx.commit().await.unwrap();
    let body =
        "Full article with [supporting evidence](https://www.nature.com/articles/new-study).";
    let prompt = super::super::input::build_summary_prompt(&prepared.snapshot, Some(body));
    let links = super::super::input::relevant_link_input(&prepared.snapshot, body);
    assert!(
        links.is_some(),
        "fixture must exercise link-evidence preparation"
    );
    let mut polled = metadata.clone();
    polled["comment_count"] = json!(9);
    polled["aggregator"]["metadata"]["score"] = json!(200);
    polled["aggregator"]["metadata"]["comments_count"] = json!(9);
    // Reddit adds these fields; neither is evidence consumed by this pipeline.
    polled["scraped_at"] = json!("2026-10-17T12:20:00Z");
    polled["aggregator"]["metadata"]["upvote_ratio"] = json!(0.97);
    record.raw_metadata = polled.clone();
    let mut tx = pool.begin().await.unwrap();
    assert_eq!(
        newsly_db::persist_scraped_news(&mut tx, &record)
            .await
            .unwrap()
            .news_item_id,
        id
    );
    tx.commit().await.unwrap();
    let mut tx = pool.begin().await.unwrap();
    let row = load_news_row(&mut tx, id).await.unwrap().unwrap();
    assert_eq!(
        row.raw_metadata["aggregator"]["metadata"]["score"], 200,
        "fresh counters remain stored"
    );
    assert_eq!(source_fingerprint(&row), prepared.snapshot.fingerprint);
    let current = snapshot_from_row(&row, BodySource::None);
    assert_eq!(
        super::super::input::build_summary_prompt(&current, Some(body)),
        prompt
    );
    assert_eq!(
        super::super::input::relevant_link_input(&current, body),
        links
    );
    tx.commit().await.unwrap();
    let summary:newsly_providers::NewsSummary=serde_json::from_value(json!({"title":"Saved summary","article_url":"https://example.com/story","key_points":["A fact"],"summary":"Saved evidence","classification":"to_read","summarization_date":Utc::now()})).unwrap();
    assert!(
        checkpoint_summary(&queue, &claim, &prepared.snapshot, &summary)
            .await
            .unwrap()
    );
    for (path, value) in [
        (
            vec!["article", "title"],
            json!("A materially changed title"),
        ),
        (vec!["article", "url"], json!("https://example.com/updated")),
        (vec!["aggregator", "topic"], json!("science")),
        (
            vec!["article_body_ref"],
            json!({"kind":"inline","text":"Changed body evidence"}),
        ),
        (
            vec!["discussion_payload", "compact_comments"],
            json!(["Changed discussion evidence"]),
        ),
    ] {
        let mut changed = polled.clone();
        let mut target = &mut changed;
        for key in &path[..path.len() - 1] {
            target = &mut target[*key];
        }
        target[path[path.len() - 1]] = value;
        sqlx::query("UPDATE news_items SET raw_metadata=$2 WHERE id::bigint=$1")
            .bind(id)
            .bind(&changed)
            .execute(&pool)
            .await
            .unwrap();
        let mut tx = pool.begin().await.unwrap();
        let row = load_news_row(&mut tx, id).await.unwrap().unwrap();
        assert_ne!(
            source_fingerprint(&row),
            prepared.snapshot.fingerprint,
            "evidence path {path:?} must remain fenced"
        );
        tx.rollback().await.unwrap();
        assert!(
            !checkpoint_summary(&queue, &claim, &prepared.snapshot, &summary)
                .await
                .unwrap()
        );
    }
    // Completing the accepted in-flight work must not restore old snapshot counters.
    sqlx::query("UPDATE news_items SET raw_metadata=$2 WHERE id::bigint=$1")
        .bind(id)
        .bind(&polled)
        .execute(&pool)
        .await
        .unwrap();
    let mutation = ProcessMutation {
        relation_model: "test-model".into(),
        relation_thresholds: newsly_domain::RelationThresholds::default(),
        links_prepared: true,
        item_document: prepared.snapshot.relation_document(&summary),
        summary,
        used_existing_summary: false,
        relevant_links: None,
    };
    let plan = ProcessFinalizationPlan {
        task_id: claim.id,
        snapshot: prepared.snapshot,
        mutation: Some(mutation),
        failure: None,
        terminal_failure: false,
        finalized_at: Utc::now(),
        briefing_debounce_seconds: 0,
        briefing_batch_minimum: 1,
    };
    let mut fenced = queue
        .begin_fenced_finalization(&claim, &newsly_queue::TaskResult::ok(), 3)
        .await
        .unwrap()
        .unwrap();
    assert_eq!(
        apply_processing(fenced.transaction_mut(), &queue, &plan)
            .await
            .unwrap(),
        NewsApplyOutcome::Applied
    );
    assert!(fenced.finish().await.unwrap().is_some());
    let published: Value =
        sqlx::query_scalar("SELECT raw_metadata FROM news_items WHERE id::bigint=$1")
            .bind(id)
            .fetch_one(&pool)
            .await
            .unwrap();
    assert_eq!(published["comment_count"], 9);
    assert_eq!(published["aggregator"]["metadata"]["score"], 200);
    assert_eq!(published["aggregator"]["metadata"]["comments_count"], 9);
    assert_eq!(
        published["aggregator"]["metadata"]["upvote_ratio"],
        json!(0.97)
    );
    assert_eq!(published["scraped_at"], polled["scraped_at"]);
}
