use sqlx::PgPool;

use super::{
    ApplyBriefingLensAssignmentOutcome, BriefingLensAssignmentPlan, BriefingRefreshConfig,
    BriefingRefreshMode, BriefingRefreshPublication, BriefingRefreshSource, BriefingSegmentUsage,
    ComposedBriefingAppend, ComposedBriefingSegment, PrepareBriefingRefreshOutcome,
    PreparedBriefingRefresh, PreparedBriefingRefreshSeed, ProviderUsage, Utc,
    apply_briefing_lens_assignment, apply_briefing_refresh, json, prepare_briefing_refresh,
    sources::{news_topic, parse_source_ids},
};

fn test_config() -> BriefingRefreshConfig {
    BriefingRefreshConfig {
        masthead_title: "The Unread Times".to_owned(),
        window_min: 3,
        news_window_max: 4,
        new_lens_min_items: 3,
        pending_max_age_seconds: 1_500,
        max_news_lenses: 10,
        category_similarity: 0.55,
        category_cluster_similarity: 0.62,
        category_absorb_similarity: 0.45,
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
                'completed', '{"summary":{"overview":"Useful context"}}'::json, false
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
                prompt_version: "briefing-v6".to_owned(),
                input_tokens: Some(10),
                output_tokens: Some(5),
                generation_ms: 25,
                warnings: Vec::new(),
                usage: BriefingSegmentUsage {
                    provider: "openai".to_owned(),
                    model: "gpt-5.6-luna".to_owned(),
                    provider_response_id: Some("response-test".to_owned()),
                    usage: ProviderUsage {
                        request_count: 1,
                        input_tokens: 10,
                        output_tokens: 5,
                        ..ProviderUsage::default()
                    },
                    operation: "briefing.compose_window".to_owned(),
                },
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
fn topic_slug_is_stable_and_bounded() {
    let metadata = json!({"aggregator": {"topic": "AI & Machine Learning"}});
    assert_eq!(
        news_topic(&metadata),
        (
            Some("ai-machine-learning".to_owned()),
            Some("Ai Machine Learning".to_owned())
        )
    );
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
    let usage: i64 = sqlx::query_scalar(
            "SELECT count(*) FROM vendor_usage_records WHERE user_id::bigint = $1 AND feature = 'briefing_compose'",
        )
        .bind(user_id)
        .fetch_one(&pool)
        .await
        .expect("usage count");
    assert_eq!((pending, segments, usage), (0, 1, 1));
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
