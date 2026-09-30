use super::*;
use crate::news_lens_embeddings::{self, PreparedNewsEmbedding};
use crate::{
    DueNewsCategoryRun, NewsCategoryMaintenanceMode, NewsCategoryRunClaimFence,
    NewsCategoryRunContext,
};
use chrono::Duration;
use serde_json::json;
use sqlx::PgPool;
use uuid::Uuid;

async fn fixture(pool: &PgPool) -> i64 {
    crate::run_migrations(pool).await.unwrap();
    let user: i64 = sqlx::query_scalar("INSERT INTO users(apple_id,email,is_active,is_admin) VALUES('nightly-test','nightly@example.com',true,false) RETURNING id::bigint")
        .fetch_one(pool).await.unwrap();
    sqlx::query("INSERT INTO briefing_states(user_id,version,masthead_title,masthead_deck) VALUES($1,3,'News','Recent news')")
        .bind(user).execute(pool).await.unwrap();
    sqlx::query("INSERT INTO user_scraper_configs(user_id,scraper_type,feed_url,config,is_active) VALUES($1,'aggregator','aggregator://hackernews','{\"key\":\"hackernews\"}',true)")
        .bind(user).execute(pool).await.unwrap();
    sqlx::query("INSERT INTO briefing_lenses(user_id,key,tier,title,deck,position,status,centroid_weight) VALUES($1,'news-old','news','Old topic','Previously composed stories',2,'active',3)")
        .bind(user).execute(pool).await.unwrap();
    for n in 0..4 {
        let id: i64 = sqlx::query_scalar("INSERT INTO news_items(ingest_key,visibility_scope,platform,status,summary_text,raw_metadata,ingested_at,created_at) VALUES($1,'global','hackernews','ready','Example story summary','{}',timezone('UTC',now())-interval '1 hour',timezone('UTC',now())-interval '1 hour') RETURNING id::bigint")
            .bind(format!("nightly-{n}")).fetch_one(pool).await.unwrap();
        let mut connection = pool.acquire().await.unwrap();
        let source = news_lens_embeddings::source(&mut connection, id)
            .await
            .unwrap()
            .unwrap();
        news_lens_embeddings::save(
            &mut connection,
            &PreparedNewsEmbedding {
                news_item_id: id,
                model: "test-embedding".into(),
                input_hash: news_lens_embeddings::input_hash(&source.embedding_text()),
                vector: vec![1.0; 4096],
            },
        )
        .await
        .unwrap();
        sqlx::query("INSERT INTO briefing_pending_sources(user_id,source_kind,source_id,lens_key,enqueued_at) VALUES($1,'news',$2,'news-old',timezone('UTC',now()))")
            .bind(user).bind(id).execute(&mut *connection).await.unwrap();
    }
    user
}

async fn snapshot(pool: &PgPool, user: i64) -> NewsCategorySnapshot {
    let mut tx = pool.begin().await.unwrap();
    let snapshot = load_snapshot(&mut tx, user, Utc::now(), "test-embedding")
        .await
        .unwrap()
        .unwrap();
    tx.commit().await.unwrap();
    snapshot
}

#[sqlx::test(migrations = false)]
async fn publication_drains_old_lenses_and_moves_only_pending(pool: PgPool) {
    let user = fixture(&pool).await;
    let seed = snapshot(&pool, user).await;
    let unfitted_source: i64 = sqlx::query_scalar("INSERT INTO news_items(ingest_key,visibility_scope,platform,status,summary_text,raw_metadata,ingested_at,created_at) VALUES('nightly-unfitted','global','hackernews','ready','Unfitted story summary','{}',timezone('UTC',now())-interval '1 hour',timezone('UTC',now())-interval '1 hour') RETURNING id::bigint")
        .fetch_one(&pool).await.unwrap();
    let unfitted_pending: i64 = sqlx::query_scalar("INSERT INTO briefing_pending_sources(user_id,source_kind,source_id,lens_key,enqueued_at) VALUES($1,'news',$2,'news-old',timezone('UTC',now())) RETURNING id::bigint")
        .bind(user).bind(unfitted_source).fetch_one(&pool).await.unwrap();
    let other_user: i64 = sqlx::query_scalar("INSERT INTO users(apple_id,email,is_active,is_admin) VALUES('nightly-other','nightly-other@example.com',true,false) RETURNING id::bigint")
        .fetch_one(&pool).await.unwrap();
    let wrong_owner_pending: i64 = sqlx::query_scalar("INSERT INTO briefing_pending_sources(user_id,source_kind,source_id,lens_key,enqueued_at) VALUES($1,'news',$2,'news-old',timezone('UTC',now())) RETURNING id::bigint")
        .bind(other_user).bind(seed.stories[1].source.id).fetch_one(&pool).await.unwrap();
    let wrong_kind_pending: i64 = sqlx::query_scalar("INSERT INTO briefing_pending_sources(user_id,source_kind,source_id,lens_key,enqueued_at) VALUES($1,'podcast',$2,'news-old',timezone('UTC',now())) RETURNING id::bigint")
        .bind(user).bind(seed.stories[1].source.id).fetch_one(&pool).await.unwrap();
    let segment: i64 = sqlx::query_scalar("INSERT INTO briefing_segments(lens_id,user_id,blocks,markdown_raw,narration_text,source_keys,status,model,prompt_version,warnings) SELECT id,$1,'[]','Existing roundup','Existing narration',$2,'active','test','test','[]' FROM briefing_lenses WHERE user_id::bigint=$1 AND key='news-old' RETURNING briefing_segments.id::bigint")
        .bind(user).bind(json!([format!("news:{}",seed.stories[0].source.id)]))
        .fetch_one(&pool).await.unwrap();
    // A composed source is normally absent from pending; preserve that boundary.
    sqlx::query(
        "DELETE FROM briefing_pending_sources WHERE user_id::bigint=$1 AND source_id::bigint=$2",
    )
    .bind(user)
    .bind(seed.stories[0].source.id)
    .execute(&pool)
    .await
    .unwrap();
    let mut seed = snapshot(&pool, user).await;
    seed.pending.push(NewsCategoryPending {
        id: wrong_owner_pending,
        source_id: seed.stories[1].source.id,
        lens_key: Some("news-old".into()),
    });
    seed.pending.push(NewsCategoryPending {
        id: wrong_kind_pending,
        source_id: seed.stories[1].source.id,
        lens_key: Some("news-old".into()),
    });
    let new = NewsCategoryPublicationLens {
        key: "news-new".into(),
        title: "New topic".into(),
        deck: "A new recurring subject".into(),
        routing_rule: "A coherent topic".into(),
        centroid: vec![1.0; 4096],
        story_ids: seed.stories.iter().take(3).map(|s| s.source.id).collect(),
    };
    let mut tx = pool.begin().await.unwrap();
    assert!(snapshot_is_current(&mut tx, &seed).await.unwrap());
    apply_partition(&mut tx, &seed, &[new]).await.unwrap();
    tx.commit().await.unwrap();
    let old:(String,bool)=sqlx::query_as("SELECT status,accepts_news FROM briefing_lenses WHERE user_id::bigint=$1 AND key='news-old'")
        .bind(user).fetch_one(&pool).await.unwrap();
    assert_eq!(old, ("active".into(), false));
    let assigned: i64=sqlx::query_scalar("SELECT count(*) FROM briefing_pending_sources WHERE user_id::bigint=$1 AND lens_key='news-new'")
        .bind(user).fetch_one(&pool).await.unwrap();
    assert_eq!(assigned, 2);
    let preserved: (String, String, serde_json::Value) = sqlx::query_as(
        "SELECT markdown_raw,narration_text,source_keys FROM briefing_segments WHERE id::bigint=$1",
    )
    .bind(segment)
    .fetch_one(&pool)
    .await
    .unwrap();
    assert_eq!(
        preserved,
        (
            "Existing roundup".into(),
            "Existing narration".into(),
            json!([format!("news:{}", seed.stories[0].source.id)])
        )
    );
    let mixed:i64=sqlx::query_scalar("SELECT count(*) FROM briefing_pending_sources WHERE user_id::bigint=$1 AND lens_key='misc'")
        .bind(user).fetch_one(&pool).await.unwrap();
    assert_eq!(mixed, 1);
    let unfitted_key: Option<String> =
        sqlx::query_scalar("SELECT lens_key FROM briefing_pending_sources WHERE id::bigint=$1")
            .bind(unfitted_pending)
            .fetch_one(&pool)
            .await
            .unwrap();
    assert_eq!(unfitted_key.as_deref(), Some("news-old"));
    let decoy_keys: Vec<Option<String>> = sqlx::query_scalar(
        "SELECT lens_key FROM briefing_pending_sources WHERE id::bigint=ANY($1::bigint[]) ORDER BY id",
    )
    .bind(vec![wrong_owner_pending, wrong_kind_pending])
    .fetch_all(&pool)
    .await
    .unwrap();
    assert_eq!(decoy_keys, vec![Some("news-old".into()); 2]);
    let version: i32 =
        sqlx::query_scalar("SELECT version FROM briefing_states WHERE user_id::bigint=$1")
            .bind(user)
            .fetch_one(&pool)
            .await
            .unwrap();
    assert_eq!(version, 4);
    let mut tx = pool.begin().await.unwrap();
    let routing = crate::briefing_refresh::preparation::semantic_lens_rows(&mut tx, user)
        .await
        .unwrap();
    assert_eq!(routing.len(), 1);
    assert_eq!(routing[0].key, "news-new");
}

#[sqlx::test(migrations = false)]
async fn stale_text_or_pending_assignment_prevents_publication(pool: PgPool) {
    let user = fixture(&pool).await;
    let seed = snapshot(&pool, user).await;
    sqlx::query("UPDATE news_items SET summary_text='Changed after planning' WHERE id::bigint=$1")
        .bind(seed.stories[0].source.id)
        .execute(&pool)
        .await
        .unwrap();
    let mut tx = pool.begin().await.unwrap();
    assert!(!snapshot_is_current(&mut tx, &seed).await.unwrap());
    tx.rollback().await.unwrap();
    let next = snapshot(&pool, user).await;
    assert_eq!(next.stories.len(), 3); // stale cached vector is excluded
    sqlx::query("UPDATE briefing_pending_sources SET lens_key=NULL WHERE id::bigint=$1")
        .bind(next.pending[0].id)
        .execute(&pool)
        .await
        .unwrap();
    let mut tx = pool.begin().await.unwrap();
    assert!(!snapshot_is_current(&mut tx, &next).await.unwrap());
}

#[sqlx::test(migrations = false)]
async fn snapshot_uses_relation_representatives_as_event_identities(pool: PgPool) {
    let user = fixture(&pool).await;
    let before = snapshot(&pool, user).await;
    let representative = before.stories[0].source.id;
    sqlx::query("INSERT INTO news_items(ingest_key,visibility_scope,platform,status,summary_text,raw_metadata,ingested_at,created_at,representative_news_item_id) VALUES('nightly-related-member','global','hackernews','ready','Related member','{}',timezone('UTC',now())-interval '30 minutes',timezone('UTC',now())-interval '30 minutes',$1)")
        .bind(representative).execute(&pool).await.unwrap();

    let after = snapshot(&pool, user).await;
    assert_eq!(after.stories.len(), before.stories.len());
    assert!(
        after
            .stories
            .iter()
            .all(|story| story.event_id == story.source.id)
    );
}

#[sqlx::test(migrations = false)]
async fn recent_read_story_can_train_without_restoring_read_coverage(pool: PgPool) {
    let user = fixture(&pool).await;
    let before = snapshot(&pool, user).await;
    let id = before.stories[0].source.id;
    sqlx::query("INSERT INTO news_item_read_status(user_id,news_item_id,read_at,created_at) VALUES($1,$2,timezone('UTC',now()),timezone('UTC',now()))")
        .bind(user).bind(id).execute(&pool).await.unwrap();
    let after = snapshot(&pool, user).await;
    assert_eq!(after.stories.len(), 4);
    let mut tx = pool.begin().await.unwrap();
    let candidate = NewsCategoryCandidate {
        input_hash: after.corpus_hash.clone(),
        candidate: json!({"schema_version":1}),
        naming_result: None,
    };
    save_candidate(&mut tx, user, &candidate).await.unwrap();
    tx.commit().await.unwrap();
    assert_eq!(
        load_candidate(&pool, user)
            .await
            .unwrap()
            .unwrap()
            .candidate,
        json!({"schema_version":1})
    );
    let count:i64=sqlx::query_scalar("SELECT count(*) FROM news_item_read_status WHERE user_id::bigint=$1 AND news_item_id::bigint=$2")
        .bind(user).bind(id).fetch_one(&pool).await.unwrap();
    assert_eq!(count, 1);
}

#[sqlx::test(migrations = false)]
async fn successful_naming_attempt_is_cacheable(pool: PgPool) {
    crate::run_migrations(&pool).await.unwrap();
    let user: i64 = sqlx::query_scalar("INSERT INTO users(apple_id,email,is_active,is_admin) VALUES('nightly-naming-cache','nightly-naming-cache@example.com',true,false) RETURNING id::bigint")
        .fetch_one(&pool).await.unwrap();
    let lease_token = Uuid::new_v4();
    let task_id: i64 = sqlx::query_scalar("INSERT INTO processing_tasks(task_type,queue_name,status,owner_user_id,locked_by,locked_at,lease_token,lease_expires_at,executor_runtime,executor_version,executor_namespace) VALUES('recluster_news_lenses','llm','processing',$1,'test',timezone('UTC',now()),$2,timezone('UTC',now())+interval '5 minutes','rust',1,'recluster_news_lenses') RETURNING id::bigint")
        .bind(user).bind(lease_token).fetch_one(&pool).await.unwrap();
    let now = Utc::now();
    let run_id: i64 = sqlx::query_scalar("INSERT INTO news_category_maintenance_runs(user_id,local_date,timezone,timezone_revision,scheduled_at,window_start_at,window_end_at,mode,status,task_id) VALUES($1,$2,'UTC',1,$3,$4,$5,'shadow','running',$6) RETURNING id")
        .bind(user).bind(now.date_naive()).bind(now.naive_utc())
        .bind((now-Duration::minutes(5)).naive_utc())
        .bind((now+Duration::hours(1)).naive_utc()).bind(task_id)
        .fetch_one(&pool).await.unwrap();
    let context = NewsCategoryRunContext {
        task_id,
        run: DueNewsCategoryRun {
            run_id,
            user_id: user,
            local_date: now.date_naive(),
            timezone: "UTC".to_owned(),
            timezone_revision: 1,
            scheduled_at: now,
            window_start_at: now - Duration::minutes(5),
            window_end_at: now + Duration::hours(1),
            mode: NewsCategoryMaintenanceMode::Shadow,
        },
        claim_fence: NewsCategoryRunClaimFence {
            locked_by: "test".to_owned(),
            lease_token,
            retry_count: 0,
            executor_runtime: "rust".to_owned(),
            executor_version: 1,
            executor_namespace: "recluster_news_lenses".to_owned(),
        },
    };
    let input_hash = "a".repeat(64);
    let result = json!({"categories":[{"stable_id":"news-example","title":"Example"}]});
    record_naming_attempt(
        &pool,
        &context,
        Uuid::new_v4(),
        &input_hash,
        Some(&result),
        None,
    )
    .await
    .unwrap();
    assert_eq!(
        cached_naming(&pool, user, &input_hash).await.unwrap(),
        Some(result)
    );
}
