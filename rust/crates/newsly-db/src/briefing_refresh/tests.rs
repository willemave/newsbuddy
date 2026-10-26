use crate::briefing::parse_source_ids;
use sqlx::PgPool;

use super::{
    ApplyBriefingLensAssignmentOutcome, BRIEFING_COMPOSITION_PROMPT_VERSION,
    BriefingLensAssignmentPlan, BriefingRefreshConfig, BriefingRefreshMode,
    BriefingRefreshPublication, BriefingRefreshSource, ComposedBriefingAppend,
    ComposedBriefingSegment, PrepareBriefingRefreshOutcome, PreparedBriefingRefresh,
    PreparedBriefingRefreshSeed, Utc, apply_briefing_lens_assignment, apply_briefing_refresh, json,
    prepare_briefing_refresh, sources::briefing_context,
};

fn test_config() -> BriefingRefreshConfig {
    BriefingRefreshConfig {
        masthead_title: "The Unread Times".to_owned(),
        window_min: 3,
        news_window_max: 4,
        new_lens_min_items: 3,
        pending_max_age_seconds: 1_500,
        max_news_lenses: 10,
        category_cluster_similarity: 0.62,
        centroid_max_weight: 32,
        sweep_seconds: 3_600,
        lens_idle_days: 7,
    }
}

async fn insert_eligible_article(pool: &PgPool) -> i64 {
    let user_id = sqlx::query_scalar::<_, i64>(
        r#"
            INSERT INTO users (apple_id, email, is_admin, is_active)
            VALUES ('briefing-test', 'briefing@example.com', false, true)
            RETURNING id::bigint
            "#,
    )
    .fetch_one(pool)
    .await
    .expect("test user should insert");
    let content_id = sqlx::query_scalar::<_, i64>(
        r#"
            INSERT INTO contents (
                content_type, url, title, source, status, content_metadata, is_aggregate
            )
            VALUES (
                'article', 'https://example.com/article', 'A test article', 'Example',
                'completed', '{"summary":{"overview":"Useful context"},"image_generated_at":"2026-09-05T12:00:00Z","image_url":"/static/images/content/test.png","thumbnail_url":"/static/images/thumbnails/test.png"}'::json, false
            )
            RETURNING id::bigint
            "#,
    )
    .fetch_one(pool)
    .await
    .expect("test content should insert");
    sqlx::query(
            "INSERT INTO content_status (user_id, content_id, status) VALUES ($1::bigint::integer, $2::bigint::integer, 'inbox')",
        )
        .bind(user_id)
        .bind(content_id)
        .execute(pool)
        .await
        .expect("test content membership should insert");
    user_id
}

async fn insert_live_briefing_task(pool: &PgPool, user_id: i64) -> i64 {
    sqlx::query_scalar::<_, i64>(
        r#"
            INSERT INTO processing_tasks (
                task_type, payload, status, queue_name, owner_user_id, started_at,
                locked_at, locked_by, lease_token, lease_expires_at,
                executor_runtime, executor_version, executor_namespace
            )
            VALUES (
                'briefing_refresh', '{}'::json, 'processing', 'llm',
                $1::bigint::integer, timezone('UTC', clock_timestamp()),
                timezone('UTC', clock_timestamp()), 'briefing-test-worker',
                '00000000-0000-4000-8000-000000000091'::uuid,
                timezone('UTC', clock_timestamp()) + interval '5 minutes',
                'rust', 1, 'briefing_refresh'
            )
            RETURNING id::bigint
            "#,
    )
    .bind(user_id)
    .fetch_one(pool)
    .await
    .expect("live Briefing task should insert")
}

async fn prepare_seed(pool: &PgPool, task_id: i64, user_id: i64) -> PreparedBriefingRefreshSeed {
    let mut transaction = pool.begin().await.expect("prepare transaction");
    let seed = prepare_briefing_refresh(
        &mut transaction,
        task_id,
        user_id,
        BriefingRefreshMode::Append,
        &test_config(),
        &crate::news_filter::NewsFilterPass::default(),
        "00000000-0000-4000-8000-000000000091".parse().unwrap(),
    )
    .await
    .expect("Briefing prepare should succeed");
    transaction.commit().await.expect("prepare commit");
    let PrepareBriefingRefreshOutcome::Ready(seed) = seed else {
        panic!("active test user should be Briefing eligible");
    };
    seed
}

async fn prepare_article(pool: &PgPool, user_id: i64) -> PreparedBriefingRefresh {
    let task_id = insert_live_briefing_task(pool, user_id).await;
    let seed = prepare_seed(pool, task_id, user_id).await;
    let plan = BriefingLensAssignmentPlan {
        task_id,
        user_id,
        starting_version: seed.starting_version,
        assignments: Vec::new(),
        centroid_mutations: Vec::new(),
        new_lenses: Vec::new(),
        usage: Vec::new(),
    };
    let mut transaction = pool.begin().await.expect("lens apply transaction");
    let outcome = apply_briefing_lens_assignment(&mut transaction, seed, &plan, &test_config())
        .await
        .expect("Briefing lens apply should succeed");
    transaction.commit().await.expect("lens apply commit");
    let ApplyBriefingLensAssignmentOutcome::Ready(prepared) = outcome else {
        panic!("live task and unchanged source snapshot should apply");
    };
    assert_eq!(prepared.append_batches.len(), 1);
    prepared
}

fn article_publication(prepared: PreparedBriefingRefresh) -> BriefingRefreshPublication {
    let batch = &prepared.append_batches[0];
    let source_keys = batch
        .sources
        .iter()
        .map(|source| source.source_key.clone())
        .collect::<Vec<_>>();
    BriefingRefreshPublication {
        append_segments: vec![ComposedBriefingAppend {
            pending_rows: batch.pending_rows.clone(),
            segment: ComposedBriefingSegment {
                lens: batch.lens.clone(),
                blocks: json!([{"type":"passage"}]),
                markdown_raw: "Test article".to_owned(),
                narration_text: "Test article".to_owned(),
                source_keys: source_keys.clone(),
                event_groups: source_keys.iter().cloned().map(|key| vec![key]).collect(),
                model: "openai:gpt-5.6-luna".to_owned(),
                prompt_version: BRIEFING_COMPOSITION_PROMPT_VERSION.to_owned(),
            },
        }],
        compactions: Vec::new(),
        embedding_usage: Vec::new(),
        finalized_at: Utc::now(),
        prepared,
    }
}

#[test]
fn refresh_modes_are_closed() {
    assert_eq!(
        BriefingRefreshMode::try_from("append").expect("mode"),
        BriefingRefreshMode::Append
    );
    assert!(BriefingRefreshMode::try_from("replace").is_err());
}

#[test]
fn source_id_parser_rejects_invalid_keys_and_deduplicates() {
    let keys = vec![
        "content:3".to_owned(),
        "content:3".to_owned(),
        "news:9".to_owned(),
        "news:-1".to_owned(),
        "bad:2".to_owned(),
    ];
    assert_eq!(parse_source_ids(&keys), (vec![3], vec![9]));
}

#[test]
fn embedding_text_keeps_title_summary_and_points() {
    let source = BriefingRefreshSource {
        source_key: "news:1".to_owned(),
        kind: "news".to_owned(),
        id: 1,
        title: "A title".to_owned(),
        original_title: "A title".to_owned(),
        source_name: None,
        summary: Some("A summary".to_owned()),
        key_points: vec!["One".to_owned(), "Two".to_owned()],
        url: None,
        image_url: None,
        thumbnail_url: None,
        published_at: None,
        briefing_context: None,
    };
    assert_eq!(source.embedding_text(), "A title\nA summary\nOne Two");
}

#[test]
fn briefing_context_preserves_enriched_article_artifacts() {
    let metadata = json!({
        "summary": {
            "one_line": "A concise thesis.",
            "artifact": {
                "payload": {
                    "key_points": [
                        {"heading": "Evidence", "text": "A concrete supporting result."},
                        "A second supporting result."
                    ],
                    "quotes": [
                        {"attribution": "Researcher", "text": "An attributable quotation."}
                    ],
                    "takeaway": "The practical consequence.",
                    "extras": {"counterpoint": "A meaningful limitation."}
                }
            },
            "feed_preview": {
                "reason_to_read": "It connects the evidence to practice.",
                "preview_bullets": ["First preview point."]
            }
        },
        "excerpt": "A source excerpt."
    });
    let context = briefing_context(metadata.as_object().expect("object metadata"))
        .expect("rich metadata should produce context");

    assert!(context.contains("One line: A concise thesis."));
    assert!(context.contains("Artifact context:"));
    assert!(context.contains("Evidence: A concrete supporting result."));
    assert!(context.contains("Researcher: An attributable quotation."));
    assert!(context.contains("Counterpoint: A meaningful limitation."));
    assert!(context.contains("Feed preview:"));
    assert!(context.contains("Excerpt: A source excerpt."));
}

#[test]
fn briefing_context_remains_bounded() {
    let metadata = json!({"summary": {"one_line": "word ".repeat(1_000)}});
    let context = briefing_context(metadata.as_object().expect("object metadata"))
        .expect("long metadata should produce context");

    assert!((2_300..=2_400).contains(&context.chars().count()));
    assert!(context.ends_with("..."));
}

#[sqlx::test]
async fn reclaimed_claim_rejects_prepared_lens_assignment(pool: PgPool) {
    let user_id = insert_eligible_article(&pool).await;
    let task_id = insert_live_briefing_task(&pool, user_id).await;
    let seed = prepare_seed(&pool, task_id, user_id).await;
    let plan = BriefingLensAssignmentPlan {
        task_id,
        user_id,
        starting_version: seed.starting_version,
        assignments: Vec::new(),
        centroid_mutations: Vec::new(),
        new_lenses: Vec::new(),
        usage: Vec::new(),
    };
    sqlx::query(
            "UPDATE processing_tasks SET locked_by = 'replacement-worker', lease_token = '00000000-0000-4000-8000-000000000092'::uuid WHERE id::bigint = $1",
        )
        .bind(task_id)
        .execute(&pool)
        .await
        .expect("claim should be replaced");
    let mut transaction = pool.begin().await.expect("lens apply transaction");
    let outcome = apply_briefing_lens_assignment(&mut transaction, seed, &plan, &test_config())
        .await
        .expect("stale fence should be a domain outcome");
    transaction.commit().await.expect("stale check commit");
    assert_eq!(outcome, ApplyBriefingLensAssignmentOutcome::Stale);
}

#[sqlx::test]
async fn refresh_task_observation_is_scoped_to_its_owner(pool: PgPool) {
    let user_id = insert_eligible_article(&pool).await;
    let task_id = insert_live_briefing_task(&pool, user_id).await;
    prepare_seed(&pool, task_id, user_id).await;

    let observation = crate::briefing::load_briefing_refresh_task(&pool, user_id, task_id)
        .await
        .expect("refresh observation should load")
        .expect("owner should see the refresh task");
    assert_eq!(observation.status, "processing");
    assert_eq!(observation.version, 0);

    let hidden = crate::briefing::load_briefing_refresh_task(&pool, user_id + 1, task_id)
        .await
        .expect("non-owner observation should be a valid query");
    assert!(hidden.is_none());
}

#[sqlx::test]
async fn publication_atomically_replaces_pending_ownership_with_a_segment(pool: PgPool) {
    let user_id = insert_eligible_article(&pool).await;
    let publication = article_publication(prepare_article(&pool, user_id).await);
    let mut transaction = pool.begin().await.expect("publication transaction");
    let outcome = apply_briefing_refresh(&mut transaction, &publication, &test_config())
        .await
        .expect("Briefing publication should succeed");
    transaction.commit().await.expect("publication commit");

    assert_eq!(outcome.appended_segments, 1);
    assert!(!outcome.stale);
    assert_eq!(outcome.version, 1);
    let pending: i64 = sqlx::query_scalar(
        "SELECT count(*) FROM briefing_pending_sources WHERE user_id::bigint = $1",
    )
    .bind(user_id)
    .fetch_one(&pool)
    .await
    .expect("pending count");
    let segments: i64 = sqlx::query_scalar(
        "SELECT count(*) FROM briefing_segments WHERE user_id::bigint = $1 AND status = 'active'",
    )
    .bind(user_id)
    .fetch_one(&pool)
    .await
    .expect("segment count");
    let prompt_version: String = sqlx::query_scalar(
        "SELECT prompt_version FROM briefing_segments WHERE user_id::bigint = $1 AND status = 'active'",
    )
    .bind(user_id)
    .fetch_one(&pool)
    .await
    .expect("persisted prompt version");
    let usage: i64 = sqlx::query_scalar(
            "SELECT count(*) FROM vendor_usage_records WHERE user_id::bigint = $1 AND feature = 'briefing_compose'",
        )
        .bind(user_id)
        .fetch_one(&pool)
        .await
        .expect("usage count");
    assert_eq!((pending, segments, usage), (0, 1, 0));
    assert_eq!(prompt_version, BRIEFING_COMPOSITION_PROMPT_VERSION);
}

#[sqlx::test]
async fn stale_version_preserves_pending_sources_and_the_last_usable_edition(pool: PgPool) {
    let user_id = insert_eligible_article(&pool).await;
    let publication = article_publication(prepare_article(&pool, user_id).await);
    sqlx::query("UPDATE briefing_states SET version = version + 1 WHERE user_id::bigint = $1")
        .bind(user_id)
        .execute(&pool)
        .await
        .expect("state version should advance");

    let mut transaction = pool.begin().await.expect("publication transaction");
    let outcome = apply_briefing_refresh(&mut transaction, &publication, &test_config())
        .await
        .expect("stale Briefing publication should finish safely");
    transaction.commit().await.expect("publication commit");

    assert!(outcome.stale);
    assert_eq!(outcome.appended_segments, 0);
    let pending: i64 = sqlx::query_scalar(
        "SELECT count(*) FROM briefing_pending_sources WHERE user_id::bigint = $1",
    )
    .bind(user_id)
    .fetch_one(&pool)
    .await
    .expect("pending count");
    let segments: i64 =
        sqlx::query_scalar("SELECT count(*) FROM briefing_segments WHERE user_id::bigint = $1")
            .bind(user_id)
            .fetch_one(&pool)
            .await
            .expect("segment count");
    assert_eq!((pending, segments), (1, 0));
}

#[sqlx::test(migrations = false)]
async fn ready_article_can_publish_while_news_has_no_embedding(pool: PgPool) {
    crate::run_migrations(&pool).await.unwrap();
    let user_id = insert_eligible_article(&pool).await;
    sqlx::query("INSERT INTO user_scraper_configs(user_id,scraper_type,feed_url,config,is_active) VALUES($1::bigint::integer,'aggregator','aggregator://hackernews','{\"key\":\"hackernews\"}',true)").bind(user_id).execute(&pool).await.unwrap();
    sqlx::query("INSERT INTO news_items(ingest_key,visibility_scope,platform,status,summary_text,raw_metadata,ingested_at,created_at) VALUES('cold-news','global','hackernews','ready','Prepared text','{}',timezone('UTC',now()),timezone('UTC',now()))").execute(&pool).await.unwrap();
    let prepared = prepare_article(&pool, user_id).await;
    assert_eq!(prepared.append_batches[0].sources[0].kind, "content");
    let publication = article_publication(prepared);
    let mut tx = pool.begin().await.unwrap();
    let result = apply_briefing_refresh(&mut tx, &publication, &test_config())
        .await
        .unwrap();
    assert!(!result.stale);
    tx.commit().await.unwrap();
    let pending:i64=sqlx::query_scalar("SELECT count(*) FROM briefing_pending_sources WHERE user_id::bigint=$1 AND source_kind='news' AND lens_key IS NULL").bind(user_id).fetch_one(&pool).await.unwrap();
    assert_eq!(pending, 1);
    let embeddings: i64 = sqlx::query_scalar("SELECT count(*) FROM news_lens_embeddings")
        .fetch_one(&pool)
        .await
        .unwrap();
    assert_eq!(embeddings, 0);
}

async fn insert_news_backlog(pool: &PgPool, user_id: i64, count: i32) {
    sqlx::query("INSERT INTO user_scraper_configs(user_id,scraper_type,feed_url,config,is_active) VALUES($1::bigint::integer,'aggregator','aggregator://hackernews','{\"key\":\"hackernews\"}',true),($1::bigint::integer,'aggregator','aggregator://techmeme','{\"key\":\"techmeme\"}',true)")
        .bind(user_id).execute(pool).await.unwrap();
    sqlx::query("INSERT INTO news_items(ingest_key,visibility_scope,platform,status,summary_text,raw_metadata,ingested_at,created_at) SELECT 'bounded-' || n::text,'global',CASE WHEN n % 2 = 0 THEN 'hackernews' ELSE 'techmeme' END,'ready','Prepared summary','{\"summary\":{\"title\":\"Backlog story\"}}',timezone('UTC',now()) - n * interval '1 day',timezone('UTC',now()) FROM generate_series(1,$1) n")
        .bind(count).execute(pool).await.unwrap();
}

#[sqlx::test(migrations = false)]
async fn normal_news_admission_is_bounded_fair_and_eventually_keeps_every_story(pool: PgPool) {
    crate::run_migrations(&pool).await.unwrap();
    let user_id = insert_eligible_article(&pool).await;
    insert_news_backlog(&pool, user_id, 180).await;
    for expected in [40, 40, 40, 40, 20, 0] {
        let mut tx = pool.begin().await.unwrap();
        assert_eq!(
            super::preparation::seed_news_pending(&mut tx, user_id, false)
                .await
                .unwrap(),
            expected
        );
        tx.commit().await.unwrap();
        if expected == 40 {
            let counts: Vec<i64> = sqlx::query_scalar("SELECT count(*) FROM briefing_pending_sources p JOIN news_items n ON n.id=p.source_id WHERE p.user_id::bigint=$1 AND p.source_kind='news' GROUP BY n.platform ORDER BY n.platform")
                .bind(user_id).fetch_all(&pool).await.unwrap();
            assert_eq!(
                counts[0], counts[1],
                "each admission shares the batch across sources"
            );
        }
    }
    let pending: i64 = sqlx::query_scalar("SELECT count(*) FROM briefing_pending_sources WHERE user_id::bigint=$1 AND source_kind='news'").bind(user_id).fetch_one(&pool).await.unwrap();
    assert_eq!(
        pending, 180,
        "older deferred stories remain eligible without duplicates"
    );
    let read: i64 =
        sqlx::query_scalar("SELECT count(*) FROM news_item_read_status WHERE user_id::bigint=$1")
            .bind(user_id)
            .fetch_one(&pool)
            .await
            .unwrap();
    assert_eq!(read, 0, "deferral never marks stories read");
}

#[sqlx::test(migrations = false)]
async fn legacy_news_backlog_assigns_in_bounded_chunks_with_source_fencing(pool: PgPool) {
    crate::run_migrations(&pool).await.unwrap();
    let user_id = insert_eligible_article(&pool).await;
    insert_news_backlog(&pool, user_id, 85).await;
    sqlx::query("INSERT INTO briefing_pending_sources(user_id,source_kind,source_id,enqueued_at) SELECT $1::bigint::integer,'news',id,timezone('UTC',now()) FROM news_items ORDER BY id")
        .bind(user_id).execute(&pool).await.unwrap();
    let task_id = insert_live_briefing_task(&pool, user_id).await;
    let seed = prepare_seed(&pool, task_id, user_id).await;
    assert_eq!(seed.lens_assignment.pending_sources.len(), 40);
    let first_ids: Vec<_> = seed
        .lens_assignment
        .pending_sources
        .iter()
        .map(|source| source.pending_id)
        .collect();
    let plan = BriefingLensAssignmentPlan {
        task_id,
        user_id,
        starting_version: seed.starting_version,
        assignments: seed
            .lens_assignment
            .pending_sources
            .iter()
            .map(|source| super::BriefingPendingLensAssignment {
                pending_id: source.pending_id,
                source_kind: source.source_kind.clone(),
                source_id: source.source_id,
                lens_key: "news-bounded".to_owned(),
            })
            .collect(),
        centroid_mutations: Vec::new(),
        new_lenses: vec![super::BriefingPlannedLens {
            key: "news-bounded".to_owned(),
            title: "Bounded news".to_owned(),
            deck: "The current group of backlog stories.".to_owned(),
            position: 2,
            centroid: None,
            centroid_weight: 0,
            centroid_model: None,
        }],
        usage: Vec::new(),
    };
    let mut tx = pool.begin().await.unwrap();
    let result = apply_briefing_lens_assignment(&mut tx, seed, &plan, &test_config())
        .await
        .unwrap();
    assert!(
        matches!(result, ApplyBriefingLensAssignmentOutcome::Ready(_)),
        "deferred rows do not invalidate the prepared subset"
    );
    tx.commit().await.unwrap();
    let second = prepare_seed(&pool, task_id, user_id).await;
    assert_eq!(second.lens_assignment.pending_sources.len(), 40);
    assert!(
        second
            .lens_assignment
            .pending_sources
            .iter()
            .all(|source| !first_ids.contains(&source.pending_id)),
        "assigned rows do not starve the next chunk"
    );
    sqlx::query(
        "UPDATE news_items SET summary_text='Changed after preparation' WHERE id::bigint=$1",
    )
    .bind(second.lens_assignment.pending_sources[0].source_id)
    .execute(&pool)
    .await
    .unwrap();
    let empty_plan = BriefingLensAssignmentPlan {
        task_id,
        user_id,
        starting_version: second.starting_version,
        assignments: Vec::new(),
        centroid_mutations: Vec::new(),
        new_lenses: Vec::new(),
        usage: Vec::new(),
    };
    let mut tx = pool.begin().await.unwrap();
    assert!(matches!(
        apply_briefing_lens_assignment(&mut tx, second, &empty_plan, &test_config())
            .await
            .unwrap(),
        ApplyBriefingLensAssignmentOutcome::Stale
    ));
    tx.rollback().await.unwrap();
    let pending: i64 = sqlx::query_scalar("SELECT count(*) FROM briefing_pending_sources WHERE user_id::bigint=$1 AND source_kind='news'").bind(user_id).fetch_one(&pool).await.unwrap();
    assert_eq!(pending, 85);
}

#[sqlx::test(migrations = false)]
async fn full_news_rebuild_preserves_its_complete_source_set(pool: PgPool) {
    crate::run_migrations(&pool).await.unwrap();
    let user_id = insert_eligible_article(&pool).await;
    insert_news_backlog(&pool, user_id, 85).await;
    let task_id = insert_live_briefing_task(&pool, user_id).await;
    let mut tx = pool.begin().await.unwrap();
    let outcome = prepare_briefing_refresh(
        &mut tx,
        task_id,
        user_id,
        BriefingRefreshMode::Full,
        &test_config(),
        &crate::news_filter::NewsFilterPass::default(),
        "00000000-0000-4000-8000-000000000091".parse().unwrap(),
    )
    .await
    .unwrap();
    let PrepareBriefingRefreshOutcome::Ready(seed) = outcome else {
        panic!("expected rebuild");
    };
    assert_eq!(seed.lens_assignment.pending_sources.len(), 85);
    tx.rollback().await.unwrap();
}

#[sqlx::test(migrations = false)]
async fn assigned_news_backlog_composition_is_bounded_and_drains_without_losing_sources(
    pool: PgPool,
) {
    crate::run_migrations(&pool).await.unwrap();
    let user_id = insert_eligible_article(&pool).await;
    insert_news_backlog(&pool, user_id, 85).await;
    sqlx::query("INSERT INTO briefing_lenses(user_id,key,tier,title,deck,position,status,centroid_weight) VALUES($1::bigint::integer,'news-a','news','First desk','The first test category.',2,'active',0),($1::bigint::integer,'news-b','news','Second desk','The second test category.',3,'active',0)")
        .bind(user_id).execute(&pool).await.unwrap();
    sqlx::query("INSERT INTO briefing_pending_sources(user_id,source_kind,source_id,lens_key,enqueued_at) SELECT $1::bigint::integer,'news',id,CASE WHEN platform='hackernews' THEN 'news-a' ELSE 'news-b' END,timezone('UTC',now()) - interval '1 hour' FROM news_items ORDER BY id")
        .bind(user_id).execute(&pool).await.unwrap();
    let mut tx = pool.begin().await.unwrap();
    let first = super::preparation::load_append_batches(
        &mut tx,
        user_id,
        BriefingRefreshMode::Append,
        &test_config(),
    )
    .await
    .unwrap();
    assert_eq!(first.len(), 2);
    assert_eq!(
        first
            .iter()
            .map(|batch| batch.sources.len())
            .collect::<Vec<_>>(),
        vec![20, 20]
    );
    let ids: Vec<_> = first
        .iter()
        .flat_map(|batch| &batch.pending_rows)
        .map(|pending| pending.id)
        .collect();
    let full = super::preparation::load_append_batches(
        &mut tx,
        user_id,
        BriefingRefreshMode::Full,
        &test_config(),
    )
    .await
    .unwrap();
    assert_eq!(
        full.iter().map(|batch| batch.sources.len()).sum::<usize>(),
        85
    );
    // Simulate covered pending identities leaving the queue, as publication does.
    sqlx::query("DELETE FROM briefing_pending_sources WHERE user_id::bigint=$1 AND id::bigint=ANY($2::bigint[])").bind(user_id).bind(&ids).execute(&mut *tx).await.unwrap();
    let next = super::preparation::load_append_batches(
        &mut tx,
        user_id,
        BriefingRefreshMode::Append,
        &test_config(),
    )
    .await
    .unwrap();
    assert_eq!(
        next.iter().map(|batch| batch.sources.len()).sum::<usize>(),
        40
    );
    assert!(
        next.iter()
            .flat_map(|batch| &batch.pending_rows)
            .all(|pending| !ids.contains(&pending.id))
    );
    let pending:i64=sqlx::query_scalar("SELECT count(*) FROM briefing_pending_sources WHERE user_id::bigint=$1 AND source_kind='news'").bind(user_id).fetch_one(&mut *tx).await.unwrap();
    assert_eq!(pending, 45, "unselected identities remain pending");
    tx.rollback().await.unwrap();
}

#[sqlx::test(migrations = false)]
async fn pending_pruning_preserves_visibility_cluster_reads_and_source_kinds(pool: PgPool) {
    crate::run_migrations(&pool).await.unwrap();
    let user = insert_eligible_article(&pool).await;
    let other: i64 = sqlx::query_scalar("INSERT INTO users(apple_id,email,is_active,is_admin) VALUES('prune-other','prune-other@example.com',true,false) RETURNING id::bigint").fetch_one(&pool).await.unwrap();
    sqlx::query("INSERT INTO user_scraper_configs(user_id,scraper_type,feed_url,config,is_active) VALUES($1::bigint::integer,'aggregator','aggregator://brutalist','{\"key\":\"brutalist\",\"topics\":[\"science\"]}',true)")
        .bind(user).execute(&pool).await.unwrap();
    let mut ids = Vec::new();
    for (key, scope, owner, platform, status, metadata) in [
        ("owned", "user", Some(user), "reddit", "ready", json!({})),
        ("foreign", "user", Some(other), "reddit", "ready", json!({})),
        (
            "cross-listed",
            "global",
            None,
            "brutalist",
            "ready",
            json!({"aggregator":{"topic":"politics","topics":["politics","science"]}}),
        ),
        (
            "wrong-topic",
            "global",
            None,
            "brutalist",
            "ready",
            json!({"aggregator":{"topic":"politics"}}),
        ),
        ("not-ready", "user", Some(user), "reddit", "new", json!({})),
        (
            "read-representative",
            "user",
            Some(user),
            "reddit",
            "ready",
            json!({}),
        ),
        (
            "read-member",
            "user",
            Some(user),
            "reddit",
            "ready",
            json!({}),
        ),
        (
            "other-reader",
            "user",
            Some(user),
            "reddit",
            "ready",
            json!({}),
        ),
        ("member", "user", Some(user), "reddit", "ready", json!({})),
    ] {
        let id: i64 = sqlx::query_scalar("INSERT INTO news_items(ingest_key,visibility_scope,owner_user_id,platform,status,raw_metadata,ingested_at,created_at) VALUES($1,$2,$3::bigint::integer,$4,$5,$6,now(),now()) RETURNING id::bigint")
            .bind(key).bind(scope).bind(owner).bind(platform).bind(status).bind(metadata)
            .fetch_one(&pool).await.unwrap();
        ids.push(id);
    }
    sqlx::query(
        "UPDATE news_items SET representative_news_item_id=$2::bigint::integer WHERE id=$1::bigint",
    )
    .bind(ids[8])
    .bind(ids[6])
    .execute(&pool)
    .await
    .unwrap();
    for (reader, item) in [(user, ids[5]), (user, ids[8]), (other, ids[7])] {
        sqlx::query("INSERT INTO news_item_read_status(user_id,news_item_id,read_at,created_at) VALUES($1::bigint::integer,$2::bigint::integer,now(),now())")
            .bind(reader).bind(item).execute(&pool).await.unwrap();
    }
    sqlx::query("INSERT INTO briefing_pending_sources(user_id,source_kind,source_id) SELECT $1::bigint::integer,'news',unnest($2::integer[])")
        .bind(user).bind(ids.iter().map(|id| i32::try_from(*id).unwrap()).collect::<Vec<_>>())
        .execute(&pool).await.unwrap();
    sqlx::query("INSERT INTO briefing_pending_sources(user_id,source_kind,source_id) VALUES($1::bigint::integer,'news',$2::bigint::integer),($3::bigint::integer,'unknown',1),($3::bigint::integer,'news',2147483647)")
        .bind(other).bind(ids[0]).bind(user).execute(&pool).await.unwrap();
    let content: i64 = sqlx::query_scalar(
        "SELECT content_id::bigint FROM content_status WHERE user_id=$1::bigint",
    )
    .bind(user)
    .fetch_one(&pool)
    .await
    .unwrap();
    // Independent content/news sequences deliberately overlap in this fixture.
    assert_eq!(content, ids[0]);
    sqlx::query("INSERT INTO briefing_pending_sources(user_id,source_kind,source_id) VALUES($1::bigint::integer,'content',$2::bigint::integer)")
        .bind(user).bind(content).execute(&pool).await.unwrap();
    let mut tx = pool.begin().await.unwrap();
    // Compare against the old full visible-news relation on the same snapshot.
    let expected: Vec<i64> = sqlx::query_scalar(sqlx::AssertSqlSafe(format!(
        "WITH visible_news AS ({}) SELECT pending.source_id::bigint FROM briefing_pending_sources pending WHERE pending.user_id=$1::bigint AND pending.source_kind='news' AND EXISTS(SELECT 1 FROM visible_news news WHERE news.id=pending.source_id AND NOT EXISTS(SELECT 1 FROM news_items member JOIN news_item_read_status read_status ON read_status.news_item_id=member.id WHERE read_status.user_id=$1::bigint AND coalesce(member.representative_news_item_id,member.id)=news.id)) ORDER BY pending.source_id",
        super::sources::visible_news_sql()
    ))).bind(user).fetch_all(&mut *tx).await.unwrap();
    assert_eq!(expected, vec![ids[0], ids[2], ids[7]]);
    let eligible = super::sources::load_eligible_sources_for_keys(
        &mut tx,
        user,
        &ids.iter()
            .map(|id| format!("news:{id}"))
            .chain([format!("content:{content}")])
            .collect::<Vec<_>>(),
    )
    .await
    .unwrap();
    assert_eq!(eligible.len(), 4);
    assert_eq!(
        super::preparation::prune_ineligible_pending(&mut tx, user)
            .await
            .unwrap(),
        8
    );
    let remaining: Vec<i64> = sqlx::query_scalar("SELECT source_id::bigint FROM briefing_pending_sources WHERE user_id=$1::bigint AND source_kind='news' ORDER BY source_id")
        .bind(user).fetch_all(&mut *tx).await.unwrap();
    assert_eq!(remaining, expected);
    let content_count: i64 = sqlx::query_scalar("SELECT count(*) FROM briefing_pending_sources WHERE user_id=$1::bigint AND source_kind='content'").bind(user).fetch_one(&mut *tx).await.unwrap();
    assert_eq!(content_count, 1);
    let other_count: i64 = sqlx::query_scalar(
        "SELECT count(*) FROM briefing_pending_sources WHERE user_id=$1::bigint",
    )
    .bind(other)
    .fetch_one(&mut *tx)
    .await
    .unwrap();
    assert_eq!(other_count, 1);
    assert_eq!(
        super::preparation::prune_ineligible_pending(&mut tx, i64::MAX)
            .await
            .unwrap(),
        0
    );
    // A later sweep must observe subscription loss and newly read content.
    sqlx::query("UPDATE user_scraper_configs SET is_active=false WHERE user_id=$1::bigint")
        .bind(user)
        .execute(&mut *tx)
        .await
        .unwrap();
    sqlx::query("INSERT INTO content_read_status(user_id,content_id,read_at,created_at) VALUES($1::bigint::integer,$2::bigint::integer,now(),now())")
        .bind(user).bind(content).execute(&mut *tx).await.unwrap();
    assert_eq!(
        super::preparation::prune_ineligible_pending(&mut tx, user)
            .await
            .unwrap(),
        2
    );
    tx.rollback().await.unwrap();
}

#[path = "category_routing_tests.rs"]
mod category_routing_tests;
