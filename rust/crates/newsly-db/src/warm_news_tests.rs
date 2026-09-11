use crate::{aggregator_corpus::aggregator_due, news_lens_embeddings as embeddings};
use chrono::{Duration, Utc};
use sqlx::PgPool;

async fn user(pool: &PgPool) -> i64 {
    sqlx::query_scalar("INSERT INTO users(apple_id,email,is_active,is_admin) VALUES ('warm-user','warm@example.com',true,false) RETURNING id::bigint").fetch_one(pool).await.unwrap()
}
async fn news(pool: &PgPool, key: &str) -> i64 {
    sqlx::query_scalar("INSERT INTO news_items(ingest_key,visibility_scope,platform,status,summary_text,raw_metadata,ingested_at,created_at) VALUES ($1,'global','hackernews','ready','Prepared summary','{\"summary\":{\"title\":\"Title\"}}',now(),now()) RETURNING id::bigint")
        .bind(key).fetch_one(pool).await.unwrap()
}
async fn run(pool: &PgPool, user_id: i64) -> i64 {
    sqlx::query_scalar("INSERT INTO onboarding_first_edition_runs(user_id,status,revision,started_at) VALUES ($1::bigint::integer,'active',1,now()) RETURNING id::bigint").bind(user_id).fetch_one(pool).await.unwrap()
}

#[sqlx::test(migrations = false)]
async fn warm_cadence_uses_active_demand_and_exact_boundaries(pool: PgPool) {
    crate::run_migrations(&pool).await.unwrap();
    let id = user(&pool).await;
    let now = Utc::now();
    let mut c = pool.acquire().await.unwrap();
    assert!(aggregator_due(&mut c, "hackernews", now).await.unwrap());
    sqlx::query("INSERT INTO source_ingestion_health(source_key,last_attempt_at) VALUES ('aggregator:hackernews',$1)").bind(now).execute(&mut *c).await.unwrap();
    assert!(
        !aggregator_due(&mut c, "hackernews", now + Duration::minutes(119))
            .await
            .unwrap()
    );
    assert!(
        aggregator_due(&mut c, "hackernews", now + Duration::hours(2))
            .await
            .unwrap()
    );
    sqlx::query("INSERT INTO user_scraper_configs(user_id,scraper_type,feed_url,config,is_active) VALUES ($1::bigint::integer,'aggregator','aggregator://hackernews','{\"key\":\"hackernews\"}',true)").bind(id).execute(&mut *c).await.unwrap();
    assert!(
        !aggregator_due(&mut c, "hackernews", now + Duration::minutes(59))
            .await
            .unwrap()
    );
    assert!(
        aggregator_due(&mut c, "hackernews", now + Duration::hours(1))
            .await
            .unwrap()
    );
    sqlx::query("UPDATE users SET is_active=false WHERE id::bigint=$1")
        .bind(id)
        .execute(&mut *c)
        .await
        .unwrap();
    assert!(
        !aggregator_due(&mut c, "hackernews", now + Duration::hours(1))
            .await
            .unwrap()
    );
}

#[sqlx::test(migrations = false)]
async fn warm_embeddings_reuse_exact_input_and_reject_changed_sources(pool: PgPool) {
    crate::run_migrations(&pool).await.unwrap();
    let id = news(&pool, "cache").await;
    let mut c = pool.acquire().await.unwrap();
    let text = embeddings::source(&mut c, id)
        .await
        .unwrap()
        .unwrap()
        .embedding_text();
    let e = embeddings::PreparedNewsEmbedding {
        news_item_id: id,
        model: "test-model".into(),
        input_hash: embeddings::input_hash(&text),
        vector: vec![0.1, 0.2],
    };
    assert!(embeddings::save(&mut c, &e).await.unwrap());
    assert_eq!(
        embeddings::load(&mut c, id, "test-model", &text)
            .await
            .unwrap(),
        Some(e.vector.clone())
    );
    assert!(
        embeddings::load(&mut c, id, "other-model", &text)
            .await
            .unwrap()
            .is_none()
    );
    let mut wrong_dimensions = e.clone();
    wrong_dimensions.vector = vec![0.5];
    assert!(embeddings::save(&mut c, &wrong_dimensions).await.is_err());
    sqlx::query("UPDATE news_items SET summary_text='Changed' WHERE id::bigint=$1")
        .bind(id)
        .execute(&mut *c)
        .await
        .unwrap();
    assert!(!embeddings::save(&mut c, &e).await.unwrap());
    let text = embeddings::source(&mut c, id)
        .await
        .unwrap()
        .unwrap()
        .embedding_text();
    assert!(
        embeddings::load(&mut c, id, "test-model", &text)
            .await
            .unwrap()
            .is_none()
    );
    let mut invalid = e;
    invalid.input_hash = embeddings::input_hash(&text);
    invalid.vector = vec![f64::NAN];
    assert!(!embeddings::save(&mut c, &invalid).await.unwrap());
}

#[sqlx::test(migrations = false)]
async fn warm_progress_tracks_reuse_without_double_counting(pool: PgPool) {
    crate::run_migrations(&pool).await.unwrap();
    let id = user(&pool).await;
    let run_id = run(&pool, id).await;
    let content_id=sqlx::query_scalar::<_,i64>("INSERT INTO contents(content_type,url,status,is_aggregate,content_metadata) VALUES ('article','https://example.com/warm','completed',false,'{\"image_generated_at\":\"2026-09-01\",\"image_url\":\"https://example.com/image\"}') RETURNING id::bigint").fetch_one(&pool).await.unwrap();
    sqlx::query("INSERT INTO content_status(user_id,content_id,status) VALUES ($1::bigint::integer,$2::bigint::integer,'inbox')").bind(id).bind(content_id).execute(&pool).await.unwrap();
    let mut c = pool.acquire().await.unwrap();
    for _ in 0..2 {
        crate::first_edition_progress::attach_content(&mut c, run_id, id, &[content_id])
            .await
            .unwrap();
    }
    drop(c);
    let tiers = crate::first_edition_progress::tiers(&pool, run_id)
        .await
        .unwrap();
    let tier = tiers.iter().find(|t| t.tier == "longform").unwrap();
    assert_eq!((tier.discovered, tier.ready, tier.processing), (1, 0, 1));
    let before: i32 = sqlx::query_scalar(
        "SELECT revision FROM onboarding_first_edition_runs WHERE id::bigint=$1",
    )
    .bind(run_id)
    .fetch_one(&pool)
    .await
    .unwrap();
    sqlx::query("UPDATE contents SET status='failed' WHERE id::bigint=$1")
        .bind(content_id)
        .execute(&pool)
        .await
        .unwrap();
    let after: i32 = sqlx::query_scalar(
        "SELECT revision FROM onboarding_first_edition_runs WHERE id::bigint=$1",
    )
    .bind(run_id)
    .fetch_one(&pool)
    .await
    .unwrap();
    assert_eq!(after, before); // Progress is included in the response validator; item writes do not lock runs.
    let tiers = crate::first_edition_progress::tiers(&pool, run_id)
        .await
        .unwrap();
    let tier = tiers.iter().find(|t| t.tier == "longform").unwrap();
    assert_eq!((tier.discovered, tier.failed, tier.processing), (1, 1, 0));
}

#[sqlx::test(migrations = false)]
async fn warm_first_batch_is_frozen_and_remaining_news_is_eventually_admitted(pool: PgPool) {
    crate::run_migrations(&pool).await.unwrap();
    let id = user(&pool).await;
    let run_id = run(&pool, id).await;
    sqlx::query("INSERT INTO user_scraper_configs(user_id,scraper_type,feed_url,config,is_active) VALUES ($1::bigint::integer,'aggregator','aggregator://hackernews','{\"key\":\"hackernews\"}',true)").bind(id).execute(&pool).await.unwrap();
    for i in 0..55 {
        news(&pool, &format!("cohort-{i}")).await;
    }
    let mut tx = pool.begin().await.unwrap();
    let first = crate::briefing_refresh::preparation::seed_news_pending(&mut tx, id, false)
        .await
        .unwrap();
    tx.commit().await.unwrap();
    assert_eq!(first, 40);
    news(&pool, "later-arrival").await;
    let mut tx = pool.begin().await.unwrap();
    assert_eq!(
        crate::briefing_refresh::preparation::seed_news_pending(&mut tx, id, false)
            .await
            .unwrap(),
        0
    );
    tx.commit().await.unwrap();
    let members:i64=sqlx::query_scalar("SELECT count(*) FROM onboarding_first_edition_items WHERE run_id::bigint=$1 AND source_kind='news'").bind(run_id).fetch_one(&pool).await.unwrap();
    assert_eq!(members, 40);
    sqlx::query("INSERT INTO news_item_read_status(user_id,news_item_id,read_at,created_at) SELECT $1::bigint::integer,source_id,now(),now() FROM onboarding_first_edition_items WHERE run_id::bigint=$2 AND source_kind='news'").bind(id).bind(run_id).execute(&pool).await.unwrap();
    let mut tx = pool.begin().await.unwrap();
    assert_eq!(
        crate::briefing_refresh::preparation::seed_news_pending(&mut tx, id, false)
            .await
            .unwrap(),
        16
    );
    tx.commit().await.unwrap();
}

#[sqlx::test(migrations = false)]
async fn aggregator_progress_freezes_a_run_check_and_ignores_later_arrivals(pool: PgPool) {
    crate::run_migrations(&pool).await.unwrap();
    let id = user(&pool).await;
    let run_id = run(&pool, id).await;
    sqlx::query("INSERT INTO user_scraper_configs(user_id,scraper_type,feed_url,config,is_active) VALUES ($1::bigint::integer,'aggregator','aggregator://hackernews','{\"key\":\"hackernews\"}',true)").bind(id).execute(&pool).await.unwrap();
    sqlx::query("INSERT INTO onboarding_first_edition_sources(run_id,source_key,source_kind,display_name,position,status,processed_item_count) VALUES ($1::bigint::integer,'scraper:hackernews','aggregator','HN',0,'queued',0)").bind(run_id).execute(&pool).await.unwrap();
    let story = news(&pool, "first-check").await;
    sqlx::query("UPDATE news_items SET status='processing' WHERE id::bigint=$1")
        .bind(story)
        .execute(&pool)
        .await
        .unwrap();
    sqlx::query("INSERT INTO source_ingestion_health(source_key,last_success_at,last_attempt_at) VALUES ('aggregator:hackernews',now(),now())").execute(&pool).await.unwrap();
    let mut c = pool.acquire().await.unwrap();
    crate::first_edition_progress::reconcile_sources(&mut c, id)
        .await
        .unwrap();
    let state: String = sqlx::query_scalar(
        "SELECT status FROM onboarding_first_edition_sources WHERE run_id::bigint=$1",
    )
    .bind(run_id)
    .fetch_one(&mut *c)
    .await
    .unwrap();
    assert_eq!(state, "processing");
    sqlx::query("UPDATE news_items SET status='ready' WHERE id::bigint=$1")
        .bind(story)
        .execute(&mut *c)
        .await
        .unwrap();
    crate::first_edition_progress::reconcile_sources(&mut c, id)
        .await
        .unwrap();
    let later = news(&pool, "later").await;
    sqlx::query("UPDATE news_items SET status='processing' WHERE id::bigint=$1")
        .bind(later)
        .execute(&mut *c)
        .await
        .unwrap();
    sqlx::query("UPDATE source_ingestion_health SET last_success_at=now()-interval '2 hours',error_code='fetch_failed'").execute(&mut *c).await.unwrap();
    crate::first_edition_progress::reconcile_sources(&mut c, id)
        .await
        .unwrap();
    let state:(String,i32)=sqlx::query_as("SELECT status,processed_item_count FROM onboarding_first_edition_sources WHERE run_id::bigint=$1").bind(run_id).fetch_one(&mut *c).await.unwrap();
    assert_eq!(state, ("processed".into(), 1));
}

#[sqlx::test(migrations = false)]
async fn terminal_artwork_archival_and_missing_artwork_settle_progress(pool: PgPool) {
    crate::run_migrations(&pool).await.unwrap();
    let id = user(&pool).await;
    let run_id = run(&pool, id).await;
    let mut ids = Vec::new();
    for (url, status, membership, metadata) in [
        (
            "blocked",
            "awaiting_image",
            "inbox",
            serde_json::json!({"artwork_status":"failed"}),
        ),
        (
            "archived",
            "completed",
            "archived",
            serde_json::json!({"image_generated_at":"2026-09-01","image_url":"https://example.com/image"}),
        ),
        ("missing", "completed", "inbox", serde_json::json!({})),
    ] {
        let content:i64=sqlx::query_scalar("INSERT INTO contents(content_type,url,status,is_aggregate,content_metadata) VALUES('podcast',$1,$2,false,$3) RETURNING id::bigint").bind(format!("https://example.com/{url}")).bind(status).bind(metadata).fetch_one(&pool).await.unwrap();
        sqlx::query("INSERT INTO content_status(user_id,content_id,status) VALUES($1::bigint::integer,$2::bigint::integer,$3)").bind(id).bind(content).bind(membership).execute(&pool).await.unwrap();
        ids.push(content);
    }
    let mut c = pool.acquire().await.unwrap();
    crate::first_edition_progress::attach_content(&mut c, run_id, id, &ids)
        .await
        .unwrap();
    drop(c);
    let tiers = crate::first_edition_progress::tiers(&pool, run_id)
        .await
        .unwrap();
    let audio = tiers.iter().find(|t| t.tier == "audio").unwrap();
    assert_eq!(
        (
            audio.discovered,
            audio.failed,
            audio.skipped,
            audio.processing
        ),
        (3, 2, 1, 0)
    );
    sqlx::query("INSERT INTO processing_tasks(task_type,content_id,status,queue_name,executor_runtime,executor_version,executor_namespace) VALUES('generate_image',$1::bigint::integer,'pending','content','rust',1,'generate_image')").bind(ids[0]).execute(&pool).await.unwrap();
    let tiers = crate::first_edition_progress::tiers(&pool, run_id)
        .await
        .unwrap();
    let audio = tiers.iter().find(|t| t.tier == "audio").unwrap();
    assert_eq!((audio.failed, audio.processing), (1, 1));
    sqlx::query("UPDATE onboarding_first_edition_runs SET started_at=timezone('UTC',now())-interval '25 hours' WHERE id::bigint=$1").bind(run_id).execute(&pool).await.unwrap();
    crate::first_edition_progress::expire_runs(&mut pool.acquire().await.unwrap(), id)
        .await
        .unwrap();
    let status: String =
        sqlx::query_scalar("SELECT status FROM onboarding_first_edition_runs WHERE id::bigint=$1")
            .bind(run_id)
            .fetch_one(&pool)
            .await
            .unwrap();
    assert_eq!(status, "expired");
}

#[sqlx::test(migrations = false)]
async fn embedding_failure_cooldown_uses_exact_input_model_and_terminal_time(pool: PgPool) {
    crate::run_migrations(&pool).await.unwrap();
    let id = news(&pool, "failure").await;
    let text = "canonical news text";
    let payload = serde_json::json!({"news_item_id":id,"embedding_model":"test-model","input_hash":embeddings::input_hash(text),"encoder_version":embeddings::ENCODER_VERSION});
    sqlx::query("INSERT INTO processing_tasks(task_type,status,queue_name,payload,error_message,completed_at,executor_runtime,executor_version,executor_namespace) VALUES('prepare_news_lens','failed','llm',$1,'Task execution deadline exceeded',timezone('UTC',now()),'rust',1,'prepare_news_lens')").bind(payload).execute(&pool).await.unwrap();
    let mut c = pool.acquire().await.unwrap();
    sqlx::query("SET TIME ZONE 'US/Pacific'")
        .execute(&mut *c)
        .await
        .unwrap();
    assert!(
        embeddings::preparation_failed(&mut c, id, "test-model", text)
            .await
            .unwrap()
    );
    assert!(
        !embeddings::preparation_failed(&mut c, id, "other-model", text)
            .await
            .unwrap()
    );
    assert!(
        !embeddings::preparation_failed(&mut c, id, "test-model", "changed")
            .await
            .unwrap()
    );
    sqlx::query(
        "UPDATE processing_tasks SET completed_at=timezone('UTC',now())-interval '7 hours'",
    )
    .execute(&mut *c)
    .await
    .unwrap();
    assert!(
        !embeddings::preparation_failed(&mut c, id, "test-model", text)
            .await
            .unwrap()
    );
}
