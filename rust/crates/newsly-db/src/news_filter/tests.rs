use super::*;
use crate::news_filter_preferences;
use serde_json::json;

type StoredUsage = (String, Option<f64>, Option<String>, Value, i32);

async fn user(pool: &PgPool, label: &str) -> i64 {
    let id = sqlx::query_scalar::<_, i64>("INSERT INTO users(apple_id,email,is_admin,is_active) VALUES($1,$2,false,true) RETURNING id::bigint")
        .bind(label).bind(format!("{label}@example.test")).fetch_one(pool).await.unwrap();
    sqlx::query("INSERT INTO user_scraper_configs(user_id,scraper_type,feed_url,config,is_active) VALUES($1::bigint::integer,'aggregator','aggregator://hackernews','{\"key\":\"hackernews\"}',true)")
        .bind(id).execute(pool).await.unwrap();
    let mut tx = pool.begin().await.unwrap();
    news_filter_preferences::save(&mut tx, id, 0, label, true)
        .await
        .unwrap();
    tx.commit().await.unwrap();
    id
}

async fn news(pool: &PgPool, count: i32) -> Vec<i64> {
    sqlx::query_scalar("INSERT INTO news_items(ingest_key,visibility_scope,platform,status,summary_text,summary_key_points,raw_metadata,ingested_at,created_at) SELECT 'filter-test-' || n::text,'global','hackernews','ready','Factual evidence','[\"Concrete fact\"]'::json,'{\"summary\":{\"title\":\"Evidence headline\"}}'::json,timezone('UTC',now()) - n * interval '1 second',timezone('UTC',now()) FROM generate_series(1,$1) n RETURNING id::bigint")
        .bind(count).fetch_all(pool).await.unwrap()
}

async fn task(pool: &PgPool, user_id: i64) -> i64 {
    sqlx::query_scalar("INSERT INTO processing_tasks(task_type,payload,status,queue_name,owner_user_id,started_at,locked_at,locked_by,lease_token,lease_expires_at,executor_runtime,executor_version,executor_namespace) VALUES('briefing_refresh','{}'::json,'processing','llm',$1::bigint::integer,timezone('UTC',now()),timezone('UTC',now()),'filter-test-worker',$2,timezone('UTC',now())+interval '5 minutes','rust',1,'briefing_refresh') RETURNING id::bigint")
        .bind(user_id).bind(Uuid::new_v4()).fetch_one(pool).await.unwrap()
}

async fn prepare(pool: &PgPool, task_id: i64, user_id: i64) -> NewsFilterPreparation {
    let mut tx = pool.begin().await.unwrap();
    let expected_lease: Uuid =
        sqlx::query_scalar("SELECT lease_token FROM processing_tasks WHERE id::bigint=$1")
            .bind(task_id)
            .fetch_one(&mut *tx)
            .await
            .unwrap();
    let prepared = prepare_refresh(&mut tx, task_id, user_id, expected_lease)
        .await
        .unwrap()
        .unwrap();
    tx.commit().await.unwrap();
    prepared
}

fn decision(candidate: &NewsFilterCandidate, excluded: bool) -> NewsFilterStoredDecision {
    NewsFilterStoredDecision {
        news_item_id: candidate.news_item_id,
        input_hash: candidate.input_hash.clone(),
        choice: if excluded { "no_match" } else { "match" }.into(),
        no_match_probability: if excluded { 0.95 } else { 0.02 },
        probabilities: json!({"match":if excluded {0.05} else {0.98},"no_match":if excluded {0.95} else {0.02}}),
    }
}

async fn store(
    pool: &PgPool,
    task_id: i64,
    user_id: i64,
    prepared: &NewsFilterPreparation,
    excluded: bool,
) -> bool {
    let decisions = prepared
        .candidates
        .iter()
        .map(|candidate| decision(candidate, excluded))
        .collect::<Vec<_>>();
    let mut tx = pool.begin().await.unwrap();
    let stored = store_decisions(&mut tx, task_id, user_id, prepared, &decisions)
        .await
        .unwrap();
    tx.commit().await.unwrap();
    stored
}

async fn admits(pool: &PgPool, user_id: i64, news_id: i64) -> bool {
    sqlx::query_scalar("SELECT news_filter_admits($1,$2)")
        .bind(user_id)
        .bind(news_id)
        .fetch_one(pool)
        .await
        .unwrap()
}

#[sqlx::test]
async fn opposite_users_have_independent_decisions_and_unknown_news_waits(pool: PgPool) {
    let include = user(&pool, "include-ai").await;
    let exclude = user(&pool, "exclude-ai").await;
    let id = news(&pool, 1).await[0];
    assert!(
        !admits(&pool, include, id).await,
        "enabled unknown cannot enter Briefing"
    );
    for (user_id, excluded) in [(include, false), (exclude, true)] {
        let task_id = task(&pool, user_id).await;
        let prepared = prepare(&pool, task_id, user_id).await;
        assert_eq!(prepared.candidates.len(), 1);
        assert!(store(&pool, task_id, user_id, &prepared, excluded).await);
    }
    assert!(admits(&pool, include, id).await);
    assert!(!admits(&pool, exclude, id).await);
    let unavailable_override: bool = sqlx::query_scalar("SELECT news_filter_admits($1,$2,true)")
        .bind(exclude)
        .bind(id)
        .fetch_one(&pool)
        .await
        .unwrap();
    assert!(
        !unavailable_override,
        "a concurrent unavailable pass cannot override a current exclusion"
    );
    assert_eq!(
        list_news_ids(&pool, include, true, None, 20)
            .await
            .unwrap()
            .len(),
        0
    );
    let filtered = list_news_ids(&pool, exclude, true, None, 20).await.unwrap();
    assert_eq!(filtered.len(), 1);
    assert_eq!(filtered[0].news_item_id, id);
    assert_eq!(filtered[0].filter_status, "filtered_out");
}

#[sqlx::test]
async fn evidence_and_preference_changes_invalidate_without_rereading_editorial_metadata(
    pool: PgPool,
) {
    let user_id = user(&pool, "identity").await;
    let id = news(&pool, 1).await[0];
    let task_id = task(&pool, user_id).await;
    let initial = prepare(&pool, task_id, user_id).await;
    assert!(store(&pool, task_id, user_id, &initial, false).await);
    assert!(prepare(&pool, task_id, user_id).await.candidates.is_empty());
    sqlx::query("UPDATE news_items SET raw_metadata=(raw_metadata::jsonb || '{\"classification\":\"skip\",\"unrelated\":true}')::json WHERE id::bigint=$1")
        .bind(id).execute(&pool).await.unwrap();
    assert!(
        prepare(&pool, task_id, user_id).await.candidates.is_empty(),
        "non-evidence metadata cannot invalidate a paid decision"
    );
    sqlx::query(
        "UPDATE news_items SET summary_text='Changed factual evidence' WHERE id::bigint=$1",
    )
    .bind(id)
    .execute(&pool)
    .await
    .unwrap();
    assert!(!admits(&pool, user_id, id).await);
    let changed = prepare(&pool, task_id, user_id).await;
    assert_ne!(
        initial.candidates[0].input_hash,
        changed.candidates[0].input_hash
    );
    assert!(store(&pool, task_id, user_id, &changed, false).await);
    let mut tx = pool.begin().await.unwrap();
    news_filter_preferences::save(&mut tx, user_id, 1, "Only astronomy", true)
        .await
        .unwrap();
    tx.commit().await.unwrap();
    assert!(!admits(&pool, user_id, id).await);
    assert_eq!(
        prepare(&pool, task_id, user_id).await.preference.revision,
        2
    );
    let mut tx = pool.begin().await.unwrap();
    news_filter_preferences::save(&mut tx, user_id, 2, "Only astronomy", false)
        .await
        .unwrap();
    tx.commit().await.unwrap();
    assert!(admits(&pool, user_id, id).await);
}

#[sqlx::test]
async fn changed_evidence_and_lost_lease_cannot_publish_old_decisions(pool: PgPool) {
    let user_id = user(&pool, "stale").await;
    let id = news(&pool, 1).await[0];
    let task_id = task(&pool, user_id).await;
    let initial = prepare(&pool, task_id, user_id).await;
    sqlx::query("UPDATE news_items SET summary_text='New version' WHERE id::bigint=$1")
        .bind(id)
        .execute(&pool)
        .await
        .unwrap();
    assert!(store(&pool, task_id, user_id, &initial, false).await);
    let count: i64 = sqlx::query_scalar("SELECT count(*) FROM news_filter_decisions")
        .fetch_one(&pool)
        .await
        .unwrap();
    assert_eq!(count, 0, "stale evidence must not create a decision");
    let current = prepare(&pool, task_id, user_id).await;
    sqlx::query("UPDATE processing_tasks SET lease_token=$2 WHERE id::bigint=$1")
        .bind(task_id)
        .bind(Uuid::new_v4())
        .execute(&pool)
        .await
        .unwrap();
    let mut stale_tx = pool.begin().await.unwrap();
    assert!(
        matches!(
            prepare_refresh(&mut stale_tx, task_id, user_id, current.lease_token).await,
            Err(sqlx::Error::RowNotFound)
        ),
        "old worker cannot adopt a replacement task lease at preparation"
    );
    stale_tx.rollback().await.unwrap();
    assert!(!store(&pool, task_id, user_id, &current, true).await);
    assert!(!admits(&pool, user_id, id).await);
}

#[sqlx::test]
async fn background_reservation_caps_oversized_pass_and_preview_lease_is_token_fenced(
    pool: PgPool,
) {
    let user_id = user(&pool, "bounded").await;
    news(&pool, 140).await;
    let task_id = task(&pool, user_id).await;
    let first = prepare(&pool, task_id, user_id).await;
    assert_eq!(first.candidates.len(), 128);
    assert!(first.more_work);
    sqlx::query("UPDATE news_filter_daily_usage SET background_count=499 WHERE user_id::bigint=$1")
        .bind(user_id)
        .execute(&pool)
        .await
        .unwrap();
    assert_eq!(prepare(&pool, task_id, user_id).await.candidates.len(), 1);
    let exhausted = prepare(&pool, task_id, user_id).await;
    assert!(exhausted.candidates.is_empty());
    assert!(exhausted.examined);
    assert!(
        !exhausted.more_work,
        "cap must settle progress rather than hot-loop"
    );
    assert_eq!(
        list_news_ids(&pool, user_id, false, None, 1).await.unwrap()[0].filter_status,
        "deferred"
    );
    let mut tx = pool.begin().await.unwrap();
    let token = reserve_preview(&mut tx, user_id, 20)
        .await
        .unwrap()
        .unwrap();
    tx.commit().await.unwrap();
    release_preview(&pool, user_id, Uuid::new_v4(), 0)
        .await
        .unwrap();
    let mut tx = pool.begin().await.unwrap();
    assert!(
        reserve_preview(&mut tx, user_id, 1)
            .await
            .unwrap()
            .is_none(),
        "wrong token cannot clear busy preview"
    );
    tx.commit().await.unwrap();
    release_preview(&pool, user_id, token, 0).await.unwrap();
    sqlx::query("UPDATE news_filter_daily_usage SET preview_count=199 WHERE user_id::bigint=$1")
        .bind(user_id)
        .execute(&pool)
        .await
        .unwrap();
    let mut tx = pool.begin().await.unwrap();
    assert!(
        reserve_preview(&mut tx, user_id, 2)
            .await
            .unwrap()
            .is_none()
    );
    assert!(
        reserve_preview(&mut tx, user_id, 21)
            .await
            .unwrap()
            .is_none()
    );
    assert!(
        reserve_preview(&mut tx, user_id, 1)
            .await
            .unwrap()
            .is_some()
    );
    tx.rollback().await.unwrap();
}

#[sqlx::test]
async fn exclusion_threshold_uncertainty_and_outside_window_have_distinct_admission(pool: PgPool) {
    let user_id = user(&pool, "threshold").await;
    let ids = news(&pool, 3).await;
    let task_id = task(&pool, user_id).await;
    let prepared = prepare(&pool, task_id, user_id).await;
    let mut decisions = prepared
        .candidates
        .iter()
        .map(|c| decision(c, true))
        .collect::<Vec<_>>();
    decisions[0].no_match_probability = 0.899;
    decisions[1].choice = "insufficient_evidence".into();
    decisions[1].no_match_probability = 0.0;
    decisions[2].no_match_probability = 0.90;
    let mut tx = pool.begin().await.unwrap();
    assert!(
        store_decisions(&mut tx, task_id, user_id, &prepared, &decisions)
            .await
            .unwrap()
    );
    tx.commit().await.unwrap();
    assert!(admits(&pool, user_id, decisions[0].news_item_id).await);
    assert!(admits(&pool, user_id, decisions[1].news_item_id).await);
    assert!(!admits(&pool, user_id, decisions[2].news_item_id).await);
    // The Rust writer's evaluator version and the SQL admission/listing rules must agree.
    let excluded = list_news_ids(&pool, user_id, true, None, 20).await.unwrap();
    assert_eq!(excluded.len(), 1);
    assert_eq!(excluded[0].news_item_id, decisions[2].news_item_id);
    let settled: bool = sqlx::query_scalar("SELECT news_filter_settled($1,$2)")
        .bind(user_id)
        .bind(decisions[2].news_item_id)
        .fetch_one(&pool)
        .await
        .unwrap();
    assert!(settled, "model exclusions must settle first-run progress");
    sqlx::query("UPDATE news_items SET ingested_at=timezone('UTC',now())-interval '8 days' WHERE id::bigint=$1").bind(ids[0]).execute(&pool).await.unwrap();
    assert!(!admits(&pool, user_id, ids[0]).await);
    let all = list_news_ids(&pool, user_id, false, None, 20)
        .await
        .unwrap();
    assert!(
        all.iter().any(|row| row.news_item_id == ids[0]),
        "outside-window sources stay directly accessible"
    );
}

#[sqlx::test]
async fn exclusions_do_not_consume_news_admission_quota(pool: PgPool) {
    let user_id = user(&pool, "quota").await;
    news(&pool, 50).await;
    let task_id = task(&pool, user_id).await;
    let prepared = prepare(&pool, task_id, user_id).await;
    let decisions = prepared
        .candidates
        .iter()
        .enumerate()
        .map(|(index, c)| decision(c, index < 45))
        .collect::<Vec<_>>();
    let mut tx = pool.begin().await.unwrap();
    assert!(
        store_decisions(&mut tx, task_id, user_id, &prepared, &decisions)
            .await
            .unwrap()
    );
    let added = crate::briefing_refresh::preparation::seed_news_pending(&mut tx, user_id, false)
        .await
        .unwrap();
    assert_eq!(
        added, 5,
        "45 newer exclusions cannot hide five older matches behind admission quota"
    );
    tx.commit().await.unwrap();
}

#[sqlx::test]
async fn fully_excluded_onboarding_cohort_settles_after_bounded_examination(pool: PgPool) {
    use crate::{BriefingRefreshConfig, BriefingRefreshMode};
    let user_id = user(&pool, "cohort").await;
    news(&pool, 3).await;
    let task_id = task(&pool, user_id).await;
    let prepared = prepare(&pool, task_id, user_id).await;
    assert!(store(&pool, task_id, user_id, &prepared, true).await);
    let run:i64=sqlx::query_scalar("INSERT INTO onboarding_first_edition_runs(user_id,status,revision,started_at) VALUES($1::bigint::integer,'active',1,timezone('UTC',now())) RETURNING id::bigint")
        .bind(user_id).fetch_one(&pool).await.unwrap();
    let config = BriefingRefreshConfig {
        masthead_title: "Test edition".into(),
        window_min: 3,
        news_window_max: 4,
        new_lens_min_items: 3,
        pending_max_age_seconds: 1500,
        max_news_lenses: 10,
        category_cluster_similarity: 0.62,
        centroid_max_weight: 32,
        sweep_seconds: 3600,
        lens_idle_days: 7,
    };
    let mut tx = pool.begin().await.unwrap();
    crate::prepare_briefing_refresh(
        &mut tx,
        task_id,
        user_id,
        BriefingRefreshMode::Append,
        &config,
        &NewsFilterPass {
            revision: 1,
            unavailable: vec![],
            examined: true,
            more_work: false,
        },
        prepared.lease_token,
    )
    .await
    .unwrap();
    tx.commit().await.unwrap();
    let settled:(bool,bool)=sqlx::query_as("SELECT news_seeded,news_seed_settled FROM onboarding_first_edition_runs WHERE id::bigint=$1")
        .bind(run).fetch_one(&pool).await.unwrap();
    assert_eq!(
        settled,
        (true, true),
        "all filtered must finish the cohort instead of waiting forever"
    );
    let pending:i64=sqlx::query_scalar("SELECT count(*) FROM briefing_pending_sources WHERE user_id::bigint=$1 AND source_kind='news'")
        .bind(user_id).fetch_one(&pool).await.unwrap();
    assert_eq!(pending, 0);
}

#[sqlx::test]
async fn unavailable_pending_is_reused_until_policy_or_evidence_changes(pool: PgPool) {
    let user_id = user(&pool, "unavailable").await;
    let id = news(&pool, 1).await[0];
    let task_id = task(&pool, user_id).await;
    let prepared = prepare(&pool, task_id, user_id).await;
    sqlx::query("INSERT INTO briefing_pending_sources(user_id,source_kind,source_id,enqueued_at,filter_revision,filter_input_hash,filter_outcome) VALUES($1::bigint::integer,'news',$2::bigint::integer,timezone('UTC',now()),1,$3,'unavailable')")
        .bind(user_id).bind(id).bind(&prepared.candidates[0].input_hash).execute(&pool).await.unwrap();
    assert!(
        prepare(&pool, task_id, user_id).await.candidates.is_empty(),
        "unavailable sources already admitted cannot hot-loop paid retries"
    );
    assert_eq!(
        list_news_ids(&pool, user_id, false, None, 20)
            .await
            .unwrap()[0]
            .filter_status,
        "unavailable"
    );
    sqlx::query("UPDATE news_items SET summary_text='Replacement evidence' WHERE id::bigint=$1")
        .bind(id)
        .execute(&pool)
        .await
        .unwrap();
    assert_eq!(
        prepare(&pool, task_id, user_id).await.candidates.len(),
        1,
        "new evidence is a fresh identity"
    );
}

#[sqlx::test]
async fn usage_distinguishes_reported_cost_from_unknown_billing(pool: PgPool) {
    let user_id = user(&pool, "usage").await;
    let id = news(&pool, 1).await[0];
    let mut connection = pool.acquire().await.unwrap();
    for (generation, cost) in [("actual-cost", Some(0.000_123)), ("unknown-cost", None)] {
        record_usage(
            &mut connection,
            user_id,
            None,
            &NewsFilterUsage {
                news_item_id: id,
                generation_id: Some(generation.into()),
                input_tokens: 100,
                output_tokens: 3,
                cost_usd: cost,
                outcome: "match".into(),
            },
        )
        .await
        .unwrap();
    }
    let rows:Vec<StoredUsage>=sqlx::query_as("SELECT request_id,cost_usd,cost_basis,metadata,total_tokens FROM vendor_usage_records WHERE feature='news_filter' ORDER BY request_id")
        .fetch_all(&mut *connection).await.unwrap();
    assert_eq!(rows.len(), 2);
    assert_eq!(rows[0].0, "actual-cost");
    assert!((rows[0].1.unwrap() - 0.000_123).abs() < 1e-12);
    assert_eq!(rows[0].2.as_deref(), Some("provider_reported"));
    assert_eq!(rows[0].3["billing_known"], true);
    assert_eq!(rows[0].4, 103);
    assert_eq!(
        rows[1].1, None,
        "unknown billing must never be recorded as zero cost"
    );
    assert_eq!(rows[1].3["billing_known"], false);
}

fn config() -> crate::BriefingRefreshConfig {
    crate::BriefingRefreshConfig {
        masthead_title: "Test edition".into(),
        window_min: 3,
        news_window_max: 4,
        new_lens_min_items: 3,
        pending_max_age_seconds: 1500,
        max_news_lenses: 10,
        category_cluster_similarity: 0.62,
        centroid_max_weight: 32,
        sweep_seconds: 3600,
        lens_idle_days: 7,
    }
}

#[sqlx::test]
async fn settings_revision_fences_old_admission_pass_and_articles_podcasts_bypass_filter(
    pool: PgPool,
) {
    let user_id = user(&pool, "boundary").await;
    news(&pool, 1).await;
    let task_id = task(&pool, user_id).await;
    let expected_lease: Uuid =
        sqlx::query_scalar("SELECT lease_token FROM processing_tasks WHERE id::bigint=$1")
            .bind(task_id)
            .fetch_one(&pool)
            .await
            .unwrap();
    let mut tx = pool.begin().await.unwrap();
    news_filter_preferences::save(&mut tx, user_id, 1, "Changed preference", true)
        .await
        .unwrap();
    tx.commit().await.unwrap();
    let mut tx = pool.begin().await.unwrap();
    let stale = crate::prepare_briefing_refresh(
        &mut tx,
        task_id,
        user_id,
        crate::BriefingRefreshMode::Append,
        &config(),
        &NewsFilterPass {
            revision: 1,
            ..Default::default()
        },
        expected_lease,
    )
    .await;
    assert!(matches!(
        stale,
        Err(crate::BriefingRefreshRepositoryError::FilterChanged)
    ));
    tx.rollback().await.unwrap();
    let mut stale_claim_tx = pool.begin().await.unwrap();
    let stale_claim = crate::prepare_briefing_refresh(
        &mut stale_claim_tx,
        task_id,
        user_id,
        crate::BriefingRefreshMode::Append,
        &config(),
        &NewsFilterPass {
            revision: 2,
            ..Default::default()
        },
        Uuid::new_v4(),
    )
    .await;
    assert!(
        matches!(
            stale_claim,
            Err(crate::BriefingRefreshRepositoryError::ClaimOwnershipLost)
        ),
        "old worker cannot adopt a replacement lease during canonical admission"
    );
    stale_claim_tx.rollback().await.unwrap();
    for kind in ["article", "podcast"] {
        let content:i64=sqlx::query_scalar("INSERT INTO contents(content_type,url,title,source,status,content_metadata,is_aggregate) VALUES($1,$2,'Ready source','Example','completed','{\"summary\":{\"overview\":\"Useful evidence\"},\"image_generated_at\":\"2026-10-17T12:00:00Z\",\"image_url\":\"/image.png\",\"thumbnail_url\":\"/thumb.png\"}'::json,false) RETURNING id::bigint")
            .bind(kind).bind(format!("https://example.test/{kind}")).fetch_one(&pool).await.unwrap();
        sqlx::query("INSERT INTO content_status(user_id,content_id,status) VALUES($1::bigint::integer,$2::bigint::integer,'inbox')")
            .bind(user_id).bind(content).execute(&pool).await.unwrap();
    }
    let mut tx = pool.begin().await.unwrap();
    crate::prepare_briefing_refresh(
        &mut tx,
        task_id,
        user_id,
        crate::BriefingRefreshMode::Append,
        &config(),
        &NewsFilterPass {
            revision: 2,
            ..Default::default()
        },
        expected_lease,
    )
    .await
    .unwrap();
    tx.commit().await.unwrap();
    let pending:Vec<String>=sqlx::query_scalar("SELECT source_kind FROM briefing_pending_sources WHERE user_id::bigint=$1 ORDER BY source_id")
        .bind(user_id).fetch_all(&pool).await.unwrap();
    assert_eq!(
        pending,
        vec!["content", "content"],
        "both opted-in kinds bypass unknown News evaluation"
    );
}

#[sqlx::test]
async fn full_rebuild_keeps_published_news_after_new_exclusion(pool: PgPool) {
    let user_id = user(&pool, "rebuild").await;
    let id = news(&pool, 1).await[0];
    let task_id = task(&pool, user_id).await;
    let prepared = prepare(&pool, task_id, user_id).await;
    assert!(store(&pool, task_id, user_id, &prepared, true).await);
    let lens:i64=sqlx::query_scalar("INSERT INTO briefing_lenses(user_id,key,tier,title,deck,position,status,centroid_weight) VALUES($1::bigint::integer,'published-news','news','News','Published',2,'active',1) RETURNING id::bigint")
        .bind(user_id).fetch_one(&pool).await.unwrap();
    sqlx::query("INSERT INTO briefing_segments(lens_id,user_id,blocks,markdown_raw,narration_text,source_keys,status,model,prompt_version,warnings) VALUES($1::bigint::integer,$2::bigint::integer,'[]','Published evidence','Published evidence',$3,'active','test','test','[]')")
        .bind(lens).bind(user_id).bind(json!([format!("news:{id}")])).execute(&pool).await.unwrap();
    assert!(
        admits(&pool, user_id, id).await,
        "new filter exclusions must preserve readable published coverage"
    );
    let mut tx = pool.begin().await.unwrap();
    crate::prepare_briefing_refresh(
        &mut tx,
        task_id,
        user_id,
        crate::BriefingRefreshMode::Full,
        &config(),
        &NewsFilterPass {
            revision: 1,
            ..Default::default()
        },
        prepared.lease_token,
    )
    .await
    .unwrap();
    tx.commit().await.unwrap();
    let pending:i64=sqlx::query_scalar("SELECT count(*) FROM briefing_pending_sources WHERE user_id::bigint=$1 AND source_kind='news' AND source_id::bigint=$2")
        .bind(user_id).bind(id).fetch_one(&pool).await.unwrap();
    assert_eq!(
        pending, 1,
        "Full rebuild preparation must retain published coverage despite current exclusion"
    );
    let status: String =
        sqlx::query_scalar("SELECT status FROM briefing_segments WHERE user_id::bigint=$1")
            .bind(user_id)
            .fetch_one(&pool)
            .await
            .unwrap();
    assert_eq!(
        status, "active",
        "preparation cannot retire the edition before replacement publication"
    );
}

async fn freeze_cohort_from_preparation(
    pool: &PgPool,
    task_id: i64,
    user_id: i64,
    prepared: &NewsFilterPreparation,
) -> (bool, bool) {
    let run:i64=sqlx::query_scalar("INSERT INTO onboarding_first_edition_runs(user_id,status,revision,started_at) VALUES($1::bigint::integer,'active',1,timezone('UTC',now())) RETURNING id::bigint")
        .bind(user_id).fetch_one(pool).await.unwrap();
    let mut tx = pool.begin().await.unwrap();
    crate::prepare_briefing_refresh(
        &mut tx,
        task_id,
        user_id,
        crate::BriefingRefreshMode::Append,
        &config(),
        &NewsFilterPass {
            revision: prepared.preference.revision,
            unavailable: vec![],
            examined: prepared.examined,
            more_work: prepared.more_work,
        },
        prepared.lease_token,
    )
    .await
    .unwrap();
    tx.commit().await.unwrap();
    sqlx::query_as("SELECT news_seeded,news_seed_settled FROM onboarding_first_edition_runs WHERE id::bigint=$1")
        .bind(run).fetch_one(pool).await.unwrap()
}

#[sqlx::test]
async fn cached_only_retry_after_decision_commit_settles_excluded_onboarding(pool: PgPool) {
    let user_id = user(&pool, "cached-cohort").await;
    news(&pool, 3).await;
    let task_id = task(&pool, user_id).await;
    let initial = prepare(&pool, task_id, user_id).await;
    assert!(store(&pool, task_id, user_id, &initial, true).await);
    // Simulate a worker stopping after durable decisions, before first-edition cohort preparation.
    let retry = prepare(&pool, task_id, user_id).await;
    assert!(
        retry.candidates.is_empty(),
        "retry must reuse already paid decisions"
    );
    assert!(
        retry.examined,
        "cached-only corpus still constitutes a completed examination"
    );
    assert_eq!(
        freeze_cohort_from_preparation(&pool, task_id, user_id, &retry).await,
        (true, true)
    );
}

#[sqlx::test]
async fn outside_window_only_onboarding_settles_without_provider_candidates(pool: PgPool) {
    let user_id = user(&pool, "outside-cohort").await;
    news(&pool, 2).await;
    sqlx::query("UPDATE news_items SET ingested_at=timezone('UTC',now())-interval '8 days'")
        .execute(&pool)
        .await
        .unwrap();
    let task_id = task(&pool, user_id).await;
    let prepared = prepare(&pool, task_id, user_id).await;
    assert!(
        prepared.candidates.is_empty(),
        "old News must not consume automatic inference budget"
    );
    assert!(
        prepared.examined,
        "outside-window corpus is settled work rather than an empty first-run wait"
    );
    assert_eq!(
        freeze_cohort_from_preparation(&pool, task_id, user_id, &prepared).await,
        (true, true)
    );
    let listed = list_news_ids(&pool, user_id, false, None, 20)
        .await
        .unwrap();
    assert_eq!(listed.len(), 2);
    assert!(
        listed
            .iter()
            .all(|row| row.filter_status == "outside_window")
    );
    let used: i32 = sqlx::query_scalar(
        "SELECT background_count FROM news_filter_daily_usage WHERE user_id::bigint=$1",
    )
    .bind(user_id)
    .fetch_one(&pool)
    .await
    .unwrap();
    assert_eq!(used, 0);
}

#[path = "publication_tests.rs"]
mod publication_tests;

#[sqlx::test]
async fn unused_background_reservations_refund_only_their_original_utc_day(pool: PgPool) {
    let user_id = user(&pool, "background-refund").await;
    news(&pool, 10).await;
    let task_id = task(&pool, user_id).await;
    let prepared = prepare(&pool, task_id, user_id).await;
    let previous_day = prepared.usage_day - chrono::Duration::days(1);
    sqlx::query("INSERT INTO news_filter_daily_usage(user_id,day,background_count) VALUES($1::bigint::integer,$2,99)")
        .bind(user_id).bind(previous_day).execute(&pool).await.unwrap();
    let mut connection = pool.acquire().await.unwrap();
    refund_background(&mut connection, user_id, previous_day, 10)
        .await
        .unwrap();
    refund_background(&mut connection, user_id, prepared.usage_day, 4)
        .await
        .unwrap();
    let counts:Vec<(chrono::NaiveDate,i32)>=sqlx::query_as("SELECT day,background_count FROM news_filter_daily_usage WHERE user_id::bigint=$1 ORDER BY day")
        .bind(user_id).fetch_all(&mut *connection).await.unwrap();
    assert_eq!(counts, vec![(previous_day, 89), (prepared.usage_day, 6)]);
    refund_background(&mut connection, user_id, prepared.usage_day, usize::MAX)
        .await
        .unwrap();
    let remaining: i32 = sqlx::query_scalar(
        "SELECT background_count FROM news_filter_daily_usage WHERE user_id::bigint=$1 AND day=$2",
    )
    .bind(user_id)
    .bind(prepared.usage_day)
    .fetch_one(&mut *connection)
    .await
    .unwrap();
    assert_eq!(
        remaining, 0,
        "refund is bounded and cannot underflow daily accounting"
    );
}

#[sqlx::test]
async fn preview_unused_refund_requires_current_token_and_cannot_repeat(pool: PgPool) {
    let user_id = user(&pool, "preview-refund").await;
    let mut tx = pool.begin().await.unwrap();
    let first = reserve_preview(&mut tx, user_id, 20)
        .await
        .unwrap()
        .unwrap();
    tx.commit().await.unwrap();
    release_preview(&pool, user_id, Uuid::new_v4(), 20)
        .await
        .unwrap();
    let mut tx = pool.begin().await.unwrap();
    assert!(
        reserve_preview(&mut tx, user_id, 1)
            .await
            .unwrap()
            .is_none(),
        "stale token neither releases preview nor refunds slots"
    );
    tx.commit().await.unwrap();
    release_preview(&pool, user_id, first, 7).await.unwrap();
    release_preview(&pool, user_id, first, 20).await.unwrap();
    let mut tx = pool.begin().await.unwrap();
    let second = reserve_preview(&mut tx, user_id, 20)
        .await
        .unwrap()
        .unwrap();
    tx.commit().await.unwrap();
    release_preview(&pool, user_id, first, 20).await.unwrap();
    let current: (i32, Option<Uuid>) = sqlx::query_as(
        "SELECT preview_count,preview_token FROM news_filter_daily_usage WHERE user_id::bigint=$1",
    )
    .bind(user_id)
    .fetch_one(&pool)
    .await
    .unwrap();
    assert_eq!(
        current,
        (33, Some(second)),
        "old token cannot consume a newer preview's refund capability"
    );
    release_preview(&pool, user_id, second, 20).await.unwrap();
    let current: (i32, Option<Uuid>) = sqlx::query_as(
        "SELECT preview_count,preview_token FROM news_filter_daily_usage WHERE user_id::bigint=$1",
    )
    .bind(user_id)
    .fetch_one(&pool)
    .await
    .unwrap();
    assert_eq!(current, (13, None));
}

#[sqlx::test]
async fn spent_provider_usage_survives_account_deletion_with_anonymous_owner(pool: PgPool) {
    let user_id = user(&pool, "deleted-billing").await;
    sqlx::query("DELETE FROM users WHERE id::bigint=$1")
        .bind(user_id)
        .execute(&pool)
        .await
        .unwrap();
    let mut connection = pool.acquire().await.unwrap();
    record_usage(
        &mut connection,
        user_id,
        None,
        &NewsFilterUsage {
            news_item_id: 1,
            generation_id: Some("after-deletion".into()),
            input_tokens: 10,
            output_tokens: 2,
            cost_usd: Some(0.000_123),
            outcome: "cancelled".into(),
        },
    )
    .await
    .unwrap();
    let actual:(Option<i32>,Option<f64>,String)=sqlx::query_as("SELECT user_id,cost_usd,cost_basis FROM vendor_usage_records WHERE request_id='after-deletion'")
        .fetch_one(&mut *connection).await.unwrap();
    assert_eq!(actual.0, None);
    assert_eq!(actual.1, Some(0.000_123));
    assert_eq!(actual.2, "provider_reported");
}
