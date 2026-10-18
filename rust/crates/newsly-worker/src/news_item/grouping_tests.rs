use super::*;
use newsly_providers::NewsSummary;
use sqlx::PgPool;

async fn story(pool: &PgPool, key: &str, url: &str) -> i64 {
    sqlx::query_scalar("INSERT INTO news_items(ingest_key,visibility_scope,platform,article_url,raw_metadata,ingested_at,created_at,status) VALUES($1,'global','hackernews',$2,'{}',now(),now(),'new') RETURNING id::bigint")
        .bind(key).bind(url).fetch_one(pool).await.unwrap()
}

async fn prepared(pool: &PgPool, id: i64, title: &str) -> ProcessFinalizationPlan {
    let mut tx = pool.begin().await.unwrap();
    let prepared = prepare_processing(&mut tx, id).await.unwrap().unwrap();
    tx.commit().await.unwrap();
    let summary = NewsSummary {
        summarization_date: Utc::now(),
        title: title.into(),
        summary: "Researchers release a new science instrument for observing planets".into(),
        key_points: vec!["Planet observations".into()],
        article_url: prepared.snapshot.article_url.clone(),
    };
    ProcessFinalizationPlan {
        task_id: 0,
        mutation: Some(ProcessMutation {
            item_document: prepared.snapshot.relation_document(&summary),
            summary,
            relation_model: "test-relation".into(),
            relation_thresholds: newsly_domain::RelationThresholds::default(),
            links_prepared: true,
            used_existing_summary: false,
            relevant_links: None,
        }),
        snapshot: prepared.snapshot,
        failure: None,
        terminal_failure: false,
        finalized_at: Utc::now(),
        briefing_debounce_seconds: 900,
        briefing_batch_minimum: 3,
    }
}

async fn apply(pool: &PgPool, plan: &ProcessFinalizationPlan) -> NewsApplyOutcome {
    let mut tx = pool.begin().await.unwrap();
    let result = apply_processing(&mut tx, &QueueKernel::new(pool.clone()), plan)
        .await
        .unwrap();
    tx.commit().await.unwrap();
    result
}

async fn warm_current(pool: &PgPool, plan: &ProcessFinalizationPlan) {
    let item = &plan.mutation.as_ref().unwrap().item_document;
    let mut tx = pool.begin().await.unwrap();
    let candidates = prefilter_relation_candidates(
        item,
        load_relation_candidates(&mut tx, &plan.snapshot, item)
            .await
            .unwrap(),
    );
    let documents = candidates
        .iter()
        .map(|candidate| candidate.document.clone())
        .collect::<Vec<_>>();
    let texts = newsly_domain::prepare_relation_embedding_texts(item, &documents);
    let owners = super::super::relation_preparation::text_sources(item, &documents, &texts);
    let vectors = vec![vec![1.0, 0.0]; texts.len()];
    super::super::relation_preparation::save_vectors(
        &mut tx,
        &texts,
        &owners,
        "test-relation",
        &vectors,
    )
    .await
    .unwrap();
    tx.commit().await.unwrap();
}

#[sqlx::test(migrations = false)]
async fn independent_exact_preparations_publish_one_canonical_root(pool: PgPool) {
    newsly_db::run_migrations(&pool).await.unwrap();
    let a = story(&pool, "exact-a", "https://example.com/same").await;
    let b = story(&pool, "exact-b", "https://example.com/same").await;
    // Both unlocked preflights observed no published candidate.
    let pa = prepared(&pool, a, "Science instrument observes planets").await;
    let pb = prepared(&pool, b, "Science instrument observes planets").await;
    assert_eq!(apply(&pool, &pa).await, NewsApplyOutcome::Applied);
    assert_eq!(apply(&pool, &pb).await, NewsApplyOutcome::Applied);
    let roots: i64 = sqlx::query_scalar("SELECT count(*) FROM news_items WHERE status='ready' AND representative_news_item_id IS NULL").fetch_one(&pool).await.unwrap();
    assert_eq!(roots, 1);
    let root: i64 = sqlx::query_scalar(
        "SELECT representative_news_item_id::bigint FROM news_items WHERE id::bigint=$1",
    )
    .bind(b)
    .fetch_one(&pool)
    .await
    .unwrap();
    assert_eq!(root, a);
}

#[sqlx::test(migrations = false)]
async fn semantic_candidate_appearance_requires_preparation_before_fresh_grouping(pool: PgPool) {
    newsly_db::run_migrations(&pool).await.unwrap();
    let a = story(&pool, "semantic-a", "https://example.com/a").await;
    let b = story(&pool, "semantic-b", "https://example.com/b").await;
    let pa = prepared(&pool, a, "Science instrument observes planets").await;
    let pb = prepared(&pool, b, "Science instrument observes planets").await;
    assert_eq!(apply(&pool, &pa).await, NewsApplyOutcome::Applied);
    assert_eq!(apply(&pool, &pb).await, NewsApplyOutcome::CandidateChanged);
    let ready: bool =
        sqlx::query_scalar("SELECT status='ready' FROM news_items WHERE id::bigint=$1")
            .bind(b)
            .fetch_one(&pool)
            .await
            .unwrap();
    assert!(!ready);
    warm_current(&pool, &pb).await;
    assert_eq!(apply(&pool, &pb).await, NewsApplyOutcome::Applied);
    let root: i64 = sqlx::query_scalar(
        "SELECT representative_news_item_id::bigint FROM news_items WHERE id::bigint=$1",
    )
    .bind(b)
    .fetch_one(&pool)
    .await
    .unwrap();
    assert_eq!(root, a);
}

#[sqlx::test(migrations = false)]
async fn changed_and_removed_candidates_are_queried_fresh_and_stale_sources_cannot_publish(
    pool: PgPool,
) {
    newsly_db::run_migrations(&pool).await.unwrap();
    let a = story(&pool, "changed-a", "https://example.com/a").await;
    let b = story(&pool, "changed-b", "https://example.com/b").await;
    let pa = prepared(&pool, a, "Science instrument observes planets").await;
    assert_eq!(apply(&pool, &pa).await, NewsApplyOutcome::Applied);
    let pb = prepared(&pool, b, "Science instrument observes planets").await;
    warm_current(&pool, &pb).await;
    sqlx::query("UPDATE news_items SET summary_text='A changed candidate instrument report', raw_metadata=jsonb_set(raw_metadata::jsonb,'{summary,title}','\"Science instrument observes new planets\"')::json WHERE id::bigint=$1").bind(a).execute(&pool).await.unwrap();
    assert_eq!(apply(&pool, &pb).await, NewsApplyOutcome::CandidateChanged);
    sqlx::query("DELETE FROM news_items WHERE id::bigint=$1")
        .bind(a)
        .execute(&pool)
        .await
        .unwrap();
    assert_eq!(apply(&pool, &pb).await, NewsApplyOutcome::Applied);
    let c = story(&pool, "source-c", "https://example.com/c").await;
    let pc = prepared(&pool, c, "Another science instrument").await;
    sqlx::query("UPDATE news_items SET article_url='https://example.com/changed-source' WHERE id::bigint=$1").bind(c).execute(&pool).await.unwrap();
    assert_eq!(apply(&pool, &pc).await, NewsApplyOutcome::SourceChanged);
    sqlx::query("DELETE FROM news_items WHERE id::bigint=$1")
        .bind(c)
        .execute(&pool)
        .await
        .unwrap();
    assert_eq!(apply(&pool, &pc).await, NewsApplyOutcome::NewsItemMissing);
}

#[sqlx::test(migrations = false)]
async fn reprocessing_keeps_published_root_visible_and_moves_all_its_members(pool: PgPool) {
    newsly_db::run_migrations(&pool).await.unwrap();
    let target = story(&pool, "target", "https://example.com/target").await;
    let root = story(&pool, "old-root", "https://example.com/root").await;
    let member = story(&pool, "old-member", "https://example.com/root").await;
    let target_plan = prepared(&pool, target, "Science instrument observes planets").await;
    assert_eq!(apply(&pool, &target_plan).await, NewsApplyOutcome::Applied);
    // Isolate setup from semantic matching while constructing the older group.
    let mut root_plan = prepared(&pool, root, "Completely unrelated cooking recipe").await;
    root_plan.mutation.as_mut().unwrap().summary.summary = "Cooking a dinner recipe".into();
    root_plan
        .mutation
        .as_mut()
        .unwrap()
        .item_document
        .summary_text = Some("Cooking a dinner recipe".into());
    assert_eq!(apply(&pool, &root_plan).await, NewsApplyOutcome::Applied);
    let member_plan = prepared(&pool, member, "Cooking recipe").await;
    assert_eq!(apply(&pool, &member_plan).await, NewsApplyOutcome::Applied);
    sqlx::query("UPDATE news_items SET article_url='https://example.com/target',canonical_story_url='https://example.com/target' WHERE id::bigint=$1").bind(root).execute(&pool).await.unwrap();
    let replacement = prepared(&pool, root, "Science instrument observes planets").await;
    let visible: bool =
        sqlx::query_scalar("SELECT status='ready' FROM news_items WHERE id::bigint=$1")
            .bind(root)
            .fetch_one(&pool)
            .await
            .unwrap();
    assert!(visible);
    assert_eq!(apply(&pool, &replacement).await, NewsApplyOutcome::Applied);
    let members: Vec<(i64, Option<i64>)> = sqlx::query_as(
        "SELECT id::bigint, representative_news_item_id::bigint FROM news_items ORDER BY id",
    )
    .fetch_all(&pool)
    .await
    .unwrap();
    assert_eq!(
        members,
        vec![(target, None), (root, Some(target)), (member, Some(target))]
    );
}

#[sqlx::test(migrations = false)]
async fn contended_guard_defers_without_product_writes_and_unrelated_items_recover(pool: PgPool) {
    newsly_db::run_migrations(&pool).await.unwrap();
    let a = story(&pool, "guard-a", "https://example.com/a").await;
    let b = story(&pool, "guard-b", "https://example.com/b").await;
    let pa = prepared(&pool, a, "Science instrument observes planets").await;
    let mut pb = prepared(&pool, b, "Cooking a dinner recipe").await;
    pb.mutation.as_mut().unwrap().item_document.summary_text =
        Some("Cooking a dinner recipe".into());
    pb.mutation.as_mut().unwrap().summary.summary = "Cooking a dinner recipe".into();
    let mut first = pool.begin().await.unwrap();
    assert_eq!(
        apply_processing(&mut first, &QueueKernel::new(pool.clone()), &pa)
            .await
            .unwrap(),
        NewsApplyOutcome::Applied
    );
    assert_eq!(
        tokio::time::timeout(std::time::Duration::from_secs(1), apply(&pool, &pb))
            .await
            .unwrap(),
        NewsApplyOutcome::CandidateChanged
    );
    let ready: bool =
        sqlx::query_scalar("SELECT status='ready' FROM news_items WHERE id::bigint=$1")
            .bind(b)
            .fetch_one(&pool)
            .await
            .unwrap();
    assert!(!ready);
    first.commit().await.unwrap();
    assert_eq!(apply(&pool, &pb).await, NewsApplyOutcome::Applied);
    let roots: i64 = sqlx::query_scalar("SELECT count(*) FROM news_items WHERE status='ready' AND representative_news_item_id IS NULL").fetch_one(&pool).await.unwrap();
    assert_eq!(roots, 2);
}

#[sqlx::test(migrations = false)]
async fn relation_artifacts_reuse_exact_identities_and_cascade_with_sources(pool: PgPool) {
    newsly_db::run_migrations(&pool).await.unwrap();
    let id = story(&pool, "cache", "https://example.com/cache").await;
    let plan = prepared(&pool, id, "Science instrument observes planets").await;
    let document = &plan.mutation.as_ref().unwrap().item_document;
    let texts = newsly_domain::prepare_relation_embedding_texts(document, &[]);
    let owners = super::super::relation_preparation::text_sources(document, &[], &texts);
    let mut connection = pool.acquire().await.unwrap();
    let expected = vec![Some(vec![0.3, 0.7]); texts.len()];
    super::super::relation_preparation::save_vectors(
        &mut connection,
        &texts,
        &owners,
        "test-relation",
        &vec![vec![0.3, 0.7]; texts.len()],
    )
    .await
    .unwrap();
    assert_eq!(
        super::super::relation_preparation::load_vectors(
            &mut connection,
            &texts,
            &owners,
            "test-relation"
        )
        .await
        .unwrap(),
        expected
    );
    assert!(
        super::super::relation_preparation::load_vectors(
            &mut connection,
            &texts,
            &owners,
            "new-model"
        )
        .await
        .unwrap()
        .iter()
        .all(Option::is_none)
    );
    sqlx::query("UPDATE news_relation_embeddings SET encoder_version=2")
        .execute(&mut *connection)
        .await
        .unwrap();
    assert!(
        super::super::relation_preparation::load_vectors(
            &mut connection,
            &texts,
            &owners,
            "test-relation"
        )
        .await
        .unwrap()
        .iter()
        .all(Option::is_none)
    );
    assert!(
        super::super::relation_preparation::vector_store(&texts[..1], vec![vec![f64::NAN, 0.1]])
            .is_err()
    );
    super::super::relation_preparation::save_links(
        &mut connection,
        id,
        "hash-empty",
        "links-model",
        Some(&[]),
    )
    .await
    .unwrap();
    super::super::relation_preparation::save_links(
        &mut connection,
        id,
        "hash-failed",
        "links-model",
        None,
    )
    .await
    .unwrap();
    assert_eq!(
        super::super::relation_preparation::load_links(
            &mut connection,
            id,
            "hash-empty",
            "links-model"
        )
        .await
        .unwrap(),
        Some(Some(vec![]))
    );
    assert_eq!(
        super::super::relation_preparation::load_links(
            &mut connection,
            id,
            "hash-failed",
            "links-model"
        )
        .await
        .unwrap(),
        Some(None)
    );
    assert_eq!(
        super::super::relation_preparation::load_links(
            &mut connection,
            id,
            "changed",
            "links-model"
        )
        .await
        .unwrap(),
        None
    );
    sqlx::query("DELETE FROM news_items WHERE id::bigint=$1")
        .bind(id)
        .execute(&mut *connection)
        .await
        .unwrap();
    let count: i64 = sqlx::query_scalar("SELECT (SELECT count(*) FROM news_relation_embeddings)+(SELECT count(*) FROM news_relation_link_preparations)").fetch_one(&mut *connection).await.unwrap();
    assert_eq!(count, 0);
}

#[sqlx::test(migrations = false)]
async fn hundred_candidate_publication_uses_prepared_vectors_under_the_short_guard(pool: PgPool) {
    newsly_db::run_migrations(&pool).await.unwrap();
    sqlx::query("INSERT INTO news_items(ingest_key,visibility_scope,platform,article_url,article_domain,summary_text,summary_key_points,raw_metadata,ingested_at,created_at,status) SELECT 'scale-'||n,'global','hackernews','https://example.com/candidate/'||n,'publisher'||n||'.example','Research team '||n||' releases a science instrument for observing planets','[\"Planet observations\"]'::json,'{\"summary\":{\"title\":\"Science instrument observes planets\"}}'::json,now()-(n*interval '1 second'),now(),'ready' FROM generate_series(1,100) n")
        .execute(&pool).await.unwrap();
    sqlx::query("INSERT INTO users(apple_id,email,is_active,is_admin) SELECT 'group-scale-'||n,'group-scale-'||n||'@example.com',true,false FROM generate_series(1,100) n").execute(&pool).await.unwrap();
    sqlx::query("INSERT INTO user_scraper_configs(user_id,scraper_type,feed_url,config,is_active) SELECT id,'aggregator','aggregator://hackernews','{\"key\":\"hackernews\"}',true FROM users WHERE apple_id LIKE 'group-scale-%'").execute(&pool).await.unwrap();
    let id = story(&pool, "scale-incoming", "https://example.com/incoming").await;
    let plan = prepared(&pool, id, "Science instrument observes new planets").await;
    warm_current(&pool, &plan).await;
    sqlx::query(
        "UPDATE news_relation_embeddings SET vector='[0,1]'::jsonb WHERE news_item_id::bigint<>$1",
    )
    .bind(id)
    .execute(&pool)
    .await
    .unwrap();
    let start = std::time::Instant::now();
    assert_eq!(apply(&pool, &plan).await, NewsApplyOutcome::Applied);
    println!(
        "100-candidate + 100-subscriber guarded publication elapsed_ms={}",
        start.elapsed().as_millis()
    );
    let roots: i64 = sqlx::query_scalar(
        "SELECT count(*) FROM news_items WHERE representative_news_item_id IS NULL",
    )
    .fetch_one(&pool)
    .await
    .unwrap();
    assert_eq!(roots, 101); // Cached orthogonal candidate vectors preserve a new independent story.
    let admitted:i64 = sqlx::query_scalar("SELECT count(*) FROM briefing_pending_sources WHERE source_kind='news' AND source_id::bigint=$1").bind(id).fetch_one(&pool).await.unwrap();
    assert_eq!(admitted, 100);
}

#[sqlx::test(migrations = false)]
async fn successful_extraction_survives_guard_deferral_but_changed_sources_and_missing_files_do_not_reuse(
    pool: PgPool,
) {
    newsly_db::run_migrations(&pool).await.unwrap();
    let id = story(&pool, "extraction-cache", "https://example.com/extracted").await;
    let plan = prepared(&pool, id, "Science instrument observes planets").await;
    let temp = tempfile::tempdir().unwrap();
    let store = super::super::storage::NewsArticleBodyStore::new(
        temp.path().to_path_buf(),
        "bodies".into(),
    )
    .unwrap();
    let body = store
        .stage(id, "A successfully extracted article body")
        .await
        .unwrap();
    let mutation = EnrichmentMutation::Storage {
        article_url: "https://example.com/extracted".into(),
        final_url: "https://example.com/extracted".into(),
        title: Some("Article title".into()),
        article_domain: Some("example.com".into()),
        extraction_method: "firecrawl".into(),
        body: body.clone(),
    };
    super::super::enrichment_checkpoint::save(&pool, &plan.snapshot, &mutation)
        .await
        .unwrap();
    // This is re-enrichment of an already published row, which must coordinate with grouping.
    sqlx::query("UPDATE news_items SET status='ready' WHERE id::bigint=$1")
        .bind(id)
        .execute(&pool)
        .await
        .unwrap();
    let mut owner = pool.begin().await.unwrap();
    assert!(
        newsly_db::news_grouping::try_lock_scope(&mut owner, "global", None)
            .await
            .unwrap()
    );
    let enrichment = EnrichmentFinalizationPlan {
        task_id: 0,
        snapshot: plan.snapshot.clone(),
        mutation,
        finalized_at: Utc::now(),
    };
    let mut tx = pool.begin().await.unwrap();
    assert_eq!(
        apply_enrichment(&mut tx, &QueueKernel::new(pool.clone()), &enrichment)
            .await
            .unwrap(),
        NewsApplyOutcome::CandidateChanged
    );
    tx.commit().await.unwrap();
    owner.rollback().await.unwrap();
    let cached = super::super::enrichment_checkpoint::load(&pool, &plan.snapshot, &store)
        .await
        .unwrap()
        .unwrap();
    assert!(matches!(cached, EnrichmentMutation::Storage { .. }));
    // Replay the prepared value directly: no provider or network call is involved.
    let retry = EnrichmentFinalizationPlan {
        mutation: cached,
        ..enrichment
    };
    let mut tx = pool.begin().await.unwrap();
    assert_eq!(
        apply_enrichment(&mut tx, &QueueKernel::new(pool.clone()), &retry)
            .await
            .unwrap(),
        NewsApplyOutcome::Applied
    );
    tx.rollback().await.unwrap();
    sqlx::query(
        "UPDATE news_items SET article_url='https://example.com/changed' WHERE id::bigint=$1",
    )
    .bind(id)
    .execute(&pool)
    .await
    .unwrap();
    let changed = prepared(&pool, id, "Science instrument observes planets").await;
    assert!(
        super::super::enrichment_checkpoint::load(&pool, &changed.snapshot, &store)
            .await
            .unwrap()
            .is_none()
    );
    std::fs::remove_file(temp.path().join(&body.storage_key)).unwrap();
    assert!(
        super::super::enrichment_checkpoint::load(&pool, &plan.snapshot, &store)
            .await
            .unwrap()
            .is_none()
    );
    sqlx::query(
        "UPDATE news_items SET article_url='https://example.com/extracted' WHERE id::bigint=$1",
    )
    .bind(id)
    .execute(&pool)
    .await
    .unwrap();
    let replacement_body = store
        .stage(
            id,
            "Replacement extraction body after a missing checkpoint file",
        )
        .await
        .unwrap();
    let mut replacement = retry.mutation.clone();
    let EnrichmentMutation::Storage { body, .. } = &mut replacement else {
        panic!("storage checkpoint")
    };
    *body = replacement_body.clone();
    super::super::enrichment_checkpoint::save(&pool, &plan.snapshot, &replacement)
        .await
        .unwrap();
    let mut guard = pool.begin().await.unwrap();
    assert!(
        newsly_db::news_grouping::try_lock_scope(&mut guard, "global", None)
            .await
            .unwrap()
    );
    let replacement_plan = EnrichmentFinalizationPlan {
        mutation: replacement,
        ..retry
    };
    let mut tx = pool.begin().await.unwrap();
    assert_eq!(
        apply_enrichment(&mut tx, &QueueKernel::new(pool.clone()), &replacement_plan)
            .await
            .unwrap(),
        NewsApplyOutcome::CandidateChanged
    );
    tx.commit().await.unwrap();
    guard.rollback().await.unwrap();
    let repaired = super::super::enrichment_checkpoint::load(&pool, &plan.snapshot, &store)
        .await
        .unwrap()
        .unwrap();
    let EnrichmentMutation::Storage { body, .. } = repaired else {
        panic!("replacement checkpoint")
    };
    assert_eq!(body.storage_key, replacement_body.storage_key);
    std::fs::write(temp.path().join(&body.storage_key), [0xff_u8]).unwrap();
    assert!(
        super::super::enrichment_checkpoint::load(&pool, &plan.snapshot, &store)
            .await
            .unwrap()
            .is_none()
    );
    super::super::enrichment_checkpoint::save(
        &pool,
        &changed.snapshot,
        &EnrichmentMutation::MetadataOnly {
            article_url: "https://example.com/changed".into(),
            reason: "no demand".into(),
        },
    )
    .await
    .unwrap();
    assert!(
        super::super::enrichment_checkpoint::load(&pool, &changed.snapshot, &store)
            .await
            .unwrap()
            .is_none()
    );
}

#[sqlx::test(migrations = false)]
async fn identical_private_and_global_evidence_never_crosses_owner_pools(pool: PgPool) {
    newsly_db::run_migrations(&pool).await.unwrap();
    let global = story(&pool, "scope-global", "https://example.com/scoped").await;
    let first = story(&pool, "scope-first", "https://example.com/scoped").await;
    let second = story(&pool, "scope-second", "https://example.com/scoped").await;
    for (index, id) in [first, second].into_iter().enumerate() {
        let user:i64 = sqlx::query_scalar("INSERT INTO users(apple_id,email,is_active,is_admin) VALUES($1,$2,true,false) RETURNING id::bigint").bind(format!("scope-{index}")).bind(format!("scope-{index}@example.com")).fetch_one(&pool).await.unwrap();
        sqlx::query("UPDATE news_items SET visibility_scope='user',owner_user_id=$2::bigint::integer WHERE id::bigint=$1").bind(id).bind(user).execute(&pool).await.unwrap();
    }
    for id in [global, first, second] {
        let plan = prepared(&pool, id, "Science instrument observes planets").await;
        assert_eq!(apply(&pool, &plan).await, NewsApplyOutcome::Applied);
    }
    let roots:i64 = sqlx::query_scalar("SELECT count(*) FROM news_items WHERE status='ready' AND representative_news_item_id IS NULL").fetch_one(&pool).await.unwrap();
    assert_eq!(roots, 3);
}

async fn claim_task(pool: &PgPool, news_id: i64, task_type: TaskType) -> newsly_queue::ClaimedTask {
    use newsly_domain::{ResourceKey, RuntimeOwner};
    use newsly_queue::{ClaimRequest, ClaimRuntimeScope, TaskQueue};
    let queue = QueueKernel::new(pool.clone());
    let mut request = EnqueueRequest::new(task_type);
    request.payload = json!({"news_item_id":news_id}).as_object().cloned();
    let task = queue.enqueue(request).await.unwrap();
    let scope = ClaimRuntimeScope::namespaces(
        RuntimeOwner::Rust,
        [ResourceKey::new(task_type.as_str()).unwrap()],
    )
    .unwrap();
    let claim = queue
        .claim(&ClaimRequest::for_queue(
            "guard-backoff",
            TaskQueue::Content,
            scope,
        ))
        .await
        .unwrap()
        .unwrap();
    assert_eq!(claim.id, task);
    claim
}

#[sqlx::test(migrations = false)]
async fn provider_failure_preserves_retry_backoff_while_another_group_is_publishing(pool: PgPool) {
    use super::super::finalizer::ProcessNewsFinalizer;
    use crate::{TaskFinalizer, TaskFinalizerResult};
    newsly_db::run_migrations(&pool).await.unwrap();
    let id = story(&pool, "provider-backoff", "https://example.com/backoff").await;
    let mut plan = prepared(&pool, id, "Science instrument observes planets").await;
    sqlx::query("UPDATE news_items SET status='ready' WHERE id::bigint=$1")
        .bind(id)
        .execute(&pool)
        .await
        .unwrap();
    let claim = claim_task(&pool, id, TaskType::ProcessNewsItem).await;
    plan.task_id = claim.id;
    plan.mutation = None;
    plan.failure = Some("provider unavailable".into());
    let queue = QueueKernel::new(pool.clone());
    let mut owner = pool.begin().await.unwrap();
    assert!(
        newsly_db::news_grouping::try_lock_scope(&mut owner, "global", None)
            .await
            .unwrap()
    );
    let mut fence = queue
        .begin_fenced_finalization(
            &claim,
            &newsly_queue::TaskResult::fail(Some("provider unavailable".into()), true),
            3,
        )
        .await
        .unwrap()
        .unwrap();
    assert!(matches!(
        ProcessNewsFinalizer::new(queue.clone(), plan)
            .apply(fence.transaction_mut())
            .await
            .unwrap(),
        TaskFinalizerResult::Keep
    ));
    fence.finish().await.unwrap().unwrap();
    let (retry_count,wait,ready):(i32,f64,bool) = sqlx::query_as("SELECT task.retry_count,extract(epoch FROM task.available_at-timezone('UTC',clock_timestamp()))::double precision,news.status='ready' FROM processing_tasks task JOIN news_items news ON news.id::bigint=$2 WHERE task.id::bigint=$1").bind(claim.id).bind(id).fetch_one(&pool).await.unwrap();
    assert_eq!(retry_count, 1);
    assert!(
        wait > 30.0,
        "provider retry backoff must not become a grouping defer: {wait}"
    );
    assert!(ready);
}

#[sqlx::test(migrations = false)]
async fn credit_resume_ignores_unrelated_grouping_and_ends_the_old_conflict_episode(pool: PgPool) {
    use super::super::finalizer::EnrichmentFinalizer;
    use crate::{TaskFinalizer, TaskFinalizerResult};
    newsly_db::run_migrations(&pool).await.unwrap();
    let id = story(&pool, "credit-backoff", "https://example.com/credit").await;
    let prepared = prepared(&pool, id, "Science instrument observes planets").await;
    let claim = claim_task(&pool, id, TaskType::EnrichNewsItemArticle).await;
    let queue = QueueKernel::new(pool.clone());
    let mut tx = pool.begin().await.unwrap();
    newsly_db::news_grouping::record_deferral(&mut tx, claim.id)
        .await
        .unwrap();
    sqlx::query("UPDATE news_grouping_deferrals SET first_deferred_at=clock_timestamp()-interval '2 hours',deferral_count=1200 WHERE task_id::bigint=$1").bind(claim.id).execute(&mut *tx).await.unwrap();
    tx.commit().await.unwrap();
    let plan = EnrichmentFinalizationPlan {
        task_id: claim.id,
        snapshot: prepared.snapshot,
        mutation: EnrichmentMutation::Deferred {
            article_url: Some("https://example.com/credit".into()),
            reason: "credit recovery".into(),
            resume_at: Utc::now() + Duration::minutes(15),
        },
        finalized_at: Utc::now(),
    };
    let mut owner = pool.begin().await.unwrap();
    assert!(
        newsly_db::news_grouping::try_lock_scope(&mut owner, "global", None)
            .await
            .unwrap()
    );
    let mut fence = queue
        .begin_fenced_finalization(&claim, &newsly_queue::TaskResult::defer(900), 3)
        .await
        .unwrap()
        .unwrap();
    assert!(matches!(
        EnrichmentFinalizer::new(queue.clone(), plan)
            .apply(fence.transaction_mut())
            .await
            .unwrap(),
        TaskFinalizerResult::Keep
    ));
    fence.finish().await.unwrap().unwrap();
    let (retry_count,wait):(i32,f64) = sqlx::query_as("SELECT retry_count,extract(epoch FROM available_at-timezone('UTC',clock_timestamp()))::double precision FROM processing_tasks WHERE id::bigint=$1").bind(claim.id).fetch_one(&pool).await.unwrap();
    assert_eq!(retry_count, 0);
    assert!(wait > 850.0, "credit resume must remain scheduled: {wait}");
    let mut tx = pool.begin().await.unwrap();
    let next = newsly_db::news_grouping::record_deferral(&mut tx, claim.id)
        .await
        .unwrap();
    assert_eq!(next.count, 1);
    assert!(!next.exhausted);
}

#[sqlx::test(migrations = false)]
async fn unsuccessful_new_article_extraction_does_not_replay_on_grouping_contention(pool: PgPool) {
    newsly_db::run_migrations(&pool).await.unwrap();
    let queue = QueueKernel::new(pool.clone());
    let mut owner = pool.begin().await.unwrap();
    assert!(
        newsly_db::news_grouping::try_lock_scope(&mut owner, "global", None)
            .await
            .unwrap()
    );
    for (index, mutation) in [
        EnrichmentMutation::Failed {
            article_url: Some("https://example.com/failed".into()),
            final_url: None,
            strategy: Some("firecrawl".into()),
            reason: "provider unavailable".into(),
        },
        EnrichmentMutation::MetadataOnly {
            article_url: "https://example.com/failed".into(),
            reason: "no subscriber demand".into(),
        },
    ]
    .into_iter()
    .enumerate()
    {
        let id = story(
            &pool,
            &format!("extraction-no-replay-{index}"),
            "https://example.com/failed",
        )
        .await;
        let prepared = prepared(&pool, id, "Science instrument observes planets").await;
        let plan = EnrichmentFinalizationPlan {
            task_id: 0,
            snapshot: prepared.snapshot,
            mutation,
            finalized_at: Utc::now(),
        };
        let mut tx = pool.begin().await.unwrap();
        assert_eq!(
            apply_enrichment(&mut tx, &queue, &plan).await.unwrap(),
            NewsApplyOutcome::Applied
        );
        tx.commit().await.unwrap();
    }
}
