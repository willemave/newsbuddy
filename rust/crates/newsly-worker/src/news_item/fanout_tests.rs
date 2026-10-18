use super::*;
use sqlx::PgPool;

async fn insert_story(pool: &PgPool, key: &str, owner: Option<i64>) -> i64 {
    sqlx::query_scalar(
        "INSERT INTO news_items(ingest_key,visibility_scope,owner_user_id,platform,article_url,raw_metadata,ingested_at,created_at,status) VALUES($1,CASE WHEN $2::bigint IS NULL THEN 'global' ELSE 'user' END,$2::bigint::integer,'hackernews','https://example.com/story','{}',now(),now(),'ready') RETURNING id::bigint",
    )
    .bind(key)
    .bind(owner)
    .fetch_one(pool)
    .await
    .unwrap()
}

async fn plan(pool: &PgPool, id: i64) -> ProcessFinalizationPlan {
    let mut transaction = pool.begin().await.unwrap();
    let row = load_news_row(&mut transaction, id).await.unwrap().unwrap();
    ProcessFinalizationPlan {
        task_id: 0,
        snapshot: snapshot_from_row(&row, BodySource::None),
        mutation: None,
        failure: None,
        terminal_failure: false,
        finalized_at: Utc::now(),
        briefing_debounce_seconds: 900,
        briefing_batch_minimum: 3,
    }
}

async fn append_task(queue: &QueueKernel, user_id: i64) -> i64 {
    let mut request = EnqueueRequest::new(TaskType::BriefingRefresh);
    request.owner_user_id = Some(user_id);
    request.payload = json!({"user_id":user_id,"mode":"append"})
        .as_object()
        .cloned();
    request.dedupe = Some(true);
    request.dedupe_key = Some(format!("briefing_refresh:{user_id}:append"));
    request.available_at = Some(Utc::now() + Duration::hours(1));
    queue.enqueue(request).await.unwrap()
}

#[sqlx::test(migrations = false)]
async fn hundred_subscriber_fanout_batches_admission_and_preserves_wakes(pool: PgPool) {
    newsly_db::run_migrations(&pool).await.unwrap();
    let user_ids = sqlx::query_scalar::<_, i64>(
        "INSERT INTO users(apple_id,email,is_active,is_admin) SELECT 'fanout-'||n,'fanout-'||n||'@example.com',true,false FROM generate_series(1,100) n RETURNING id::bigint",
    )
    .fetch_all(&pool)
    .await
    .unwrap();
    sqlx::query("INSERT INTO user_scraper_configs(user_id,scraper_type,feed_url,config,is_active) SELECT id,'aggregator','aggregator://hackernews','{\"key\":\"hackernews\"}',true FROM users WHERE id::bigint=ANY($1)")
        .bind(&user_ids).execute(&pool).await.unwrap();
    let story = insert_story(&pool, "fanout-story", None).await;
    sqlx::query("INSERT INTO news_item_read_status(user_id,news_item_id,read_at,created_at) VALUES($1::bigint::integer,$2::bigint::integer,now(),now())")
        .bind(user_ids[0]).bind(story).execute(&pool).await.unwrap();
    sqlx::query("UPDATE users SET is_active=false WHERE id::bigint=$1")
        .bind(user_ids[1])
        .execute(&pool)
        .await
        .unwrap();
    sqlx::query("UPDATE user_scraper_configs SET is_active=false WHERE user_id::bigint=$1")
        .bind(user_ids[2])
        .execute(&pool)
        .await
        .unwrap();
    sqlx::query("DELETE FROM users WHERE id::bigint=$1")
        .bind(user_ids[3])
        .execute(&pool)
        .await
        .unwrap();
    sqlx::query("INSERT INTO onboarding_first_edition_runs(user_id,status,revision,started_at) VALUES($1::bigint::integer,'active',1,now())")
        .bind(user_ids[4]).execute(&pool).await.unwrap();
    sqlx::query("INSERT INTO news_filter_preferences(user_id,revision,text,enabled,origin) VALUES($1::bigint::integer,1,'Science stories',true,'settings'),($2::bigint::integer,1,'Science stories',true,'settings'),($2::bigint::integer,2,'',false,'settings')")
        .bind(user_ids[5]).bind(user_ids[6]).execute(&pool).await.unwrap();
    let queue = QueueKernel::new(pool.clone());
    let retry = append_task(&queue, user_ids[7]).await;
    sqlx::query("UPDATE processing_tasks SET retry_count=1 WHERE id::bigint=$1")
        .bind(retry)
        .execute(&pool)
        .await
        .unwrap();
    let existing = append_task(&queue, user_ids[8]).await;
    // This user already has two unassigned stories: the new third source bypasses debounce.
    for key in ["fanout-prior-one", "fanout-prior-two"] {
        let prior = insert_story(&pool, key, None).await;
        sqlx::query("INSERT INTO briefing_pending_sources(user_id,source_kind,source_id,enqueued_at) VALUES($1::bigint::integer,'news',$2,now())")
            .bind(user_ids[9]).bind(prior).execute(&pool).await.unwrap();
    }
    let plan = plan(&pool, story).await;
    let before = Utc::now().naive_utc();
    let retry_deadline: NaiveDateTime =
        sqlx::query_scalar("SELECT available_at FROM processing_tasks WHERE id::bigint=$1")
            .bind(retry)
            .fetch_one(&pool)
            .await
            .unwrap();
    let mut transaction = pool.begin().await.unwrap();
    enqueue_ready_fanout(&mut transaction, &queue, &plan)
        .await
        .unwrap();
    transaction.rollback().await.unwrap();
    let count: i64 =
        sqlx::query_scalar("SELECT count(*) FROM briefing_pending_sources WHERE source_id=$1")
            .bind(story)
            .fetch_one(&pool)
            .await
            .unwrap();
    assert_eq!(
        count, 0,
        "pending insertion is part of publication transaction"
    );
    let tasks: i64 = sqlx::query_scalar(
        "SELECT count(*) FROM processing_tasks WHERE task_type='briefing_refresh'",
    )
    .fetch_one(&pool)
    .await
    .unwrap();
    assert_eq!(
        tasks, 2,
        "rollback also removes newly enqueued refresh tasks"
    );
    let mut transaction = pool.begin().await.unwrap();
    enqueue_ready_fanout(&mut transaction, &queue, &plan)
        .await
        .unwrap();
    enqueue_ready_fanout(&mut transaction, &queue, &plan)
        .await
        .unwrap();
    transaction.commit().await.unwrap();
    let pending: Vec<i64> = sqlx::query_scalar("SELECT user_id::bigint FROM briefing_pending_sources WHERE source_kind='news' AND source_id=$1 ORDER BY user_id")
        .bind(story).fetch_all(&pool).await.unwrap();
    assert_eq!(pending, user_ids[6..].to_vec());
    let tasks: Vec<(i64, NaiveDateTime)> = sqlx::query_as("SELECT owner_user_id::bigint,available_at FROM processing_tasks WHERE task_type='briefing_refresh' ORDER BY owner_user_id")
        .fetch_all(&pool).await.unwrap();
    assert_eq!(
        tasks.len(),
        96,
        "one coalesced wake for each active unread subscriber"
    );
    assert_eq!(
        tasks.iter().map(|(id, _)| *id).collect::<Vec<_>>(),
        user_ids[4..]
    );
    for (user_id, deadline) in tasks {
        if user_id == user_ids[7] {
            assert_eq!(
                deadline, retry_deadline,
                "fanout cannot bypass retry backoff"
            );
        } else if [user_ids[5], user_ids[9]].contains(&user_id) {
            assert!(deadline >= before && deadline < before + Duration::seconds(5));
        } else {
            assert!(deadline >= before + Duration::seconds(900));
            assert!(deadline < before + Duration::seconds(905));
        }
    }
    let expedited: NaiveDateTime =
        sqlx::query_scalar("SELECT available_at FROM processing_tasks WHERE id::bigint=$1")
            .bind(existing)
            .fetch_one(&pool)
            .await
            .unwrap();
    assert!(expedited < before + Duration::seconds(905));
    let invalid_filter_pending: i64 = sqlx::query_scalar(
        "SELECT count(*) FROM briefing_pending_sources WHERE user_id::bigint=$1",
    )
    .bind(user_ids[5])
    .fetch_one(&pool)
    .await
    .unwrap();
    assert_eq!(
        invalid_filter_pending, 0,
        "enabled filters admit only through canonical preparation"
    );
}

#[sqlx::test(migrations = false)]
async fn private_story_fanout_only_wakes_the_owner(pool: PgPool) {
    newsly_db::run_migrations(&pool).await.unwrap();
    let users: Vec<i64> = sqlx::query_scalar("INSERT INTO users(apple_id,email,is_active,is_admin) SELECT 'private-fanout-'||n,'private-fanout-'||n||'@example.com',true,false FROM generate_series(1,2) n RETURNING id::bigint")
        .fetch_all(&pool).await.unwrap();
    let story = insert_story(&pool, "private-fanout-story", Some(users[0])).await;
    let plan = plan(&pool, story).await;
    let queue = QueueKernel::new(pool.clone());
    let mut transaction = pool.begin().await.unwrap();
    enqueue_ready_fanout(&mut transaction, &queue, &plan)
        .await
        .unwrap();
    transaction.commit().await.unwrap();
    let pending: Vec<i64> =
        sqlx::query_scalar("SELECT user_id::bigint FROM briefing_pending_sources")
            .fetch_all(&pool)
            .await
            .unwrap();
    let owners: Vec<i64> = sqlx::query_scalar(
        "SELECT owner_user_id::bigint FROM processing_tasks WHERE task_type='briefing_refresh'",
    )
    .fetch_all(&pool)
    .await
    .unwrap();
    assert_eq!(pending, vec![users[0]]);
    assert_eq!(owners, vec![users[0]]);
}

#[sqlx::test(migrations = false)]
async fn filtered_news_arriving_during_append_preserves_an_immediate_sweep(pool: PgPool) {
    use newsly_domain::{ResourceKey, RuntimeOwner};
    use newsly_queue::{ClaimRequest, ClaimRuntimeScope, TaskResult};
    newsly_db::run_migrations(&pool).await.unwrap();
    let user: i64 = sqlx::query_scalar("INSERT INTO users(apple_id,email,is_active,is_admin) VALUES('filtered-active-append','filtered-active-append@example.com',true,false) RETURNING id::bigint")
        .fetch_one(&pool).await.unwrap();
    sqlx::query("INSERT INTO news_filter_preferences(user_id,revision,text,enabled,origin) VALUES($1::bigint::integer,1,'Science stories',true,'settings')")
        .bind(user).execute(&pool).await.unwrap();
    let queue = QueueKernel::new(pool.clone());
    let append = append_task(&queue, user).await;
    sqlx::query("UPDATE processing_tasks SET available_at=now() WHERE id::bigint=$1")
        .bind(append)
        .execute(&pool)
        .await
        .unwrap();
    let request = ClaimRequest::for_queue(
        "filtered-admission",
        TaskType::BriefingRefresh.spec().queue,
        ClaimRuntimeScope::namespaces(
            RuntimeOwner::Rust,
            [ResourceKey::new("briefing_refresh").unwrap()],
        )
        .unwrap(),
    );
    let running = queue.claim(&request).await.unwrap().unwrap();
    assert_eq!(running.id, append);
    // The running request has already prepared its filter snapshot. This source arrives later.
    let story = insert_story(&pool, "filtered-after-preparation", Some(user)).await;
    let plan = plan(&pool, story).await;
    for _ in 0..2 {
        let mut transaction = pool.begin().await.unwrap();
        enqueue_ready_fanout(&mut transaction, &queue, &plan)
            .await
            .unwrap();
        transaction.commit().await.unwrap();
    }
    let pending: i64 = sqlx::query_scalar(
        "SELECT count(*) FROM briefing_pending_sources WHERE user_id::bigint=$1",
    )
    .bind(user)
    .fetch_one(&pool)
    .await
    .unwrap();
    assert_eq!(
        pending, 0,
        "filtered admission must not create unversioned pending rows"
    );
    let (sweep, mode, retries, available): (i64, String, i32, NaiveDateTime) = sqlx::query_as("SELECT id::bigint,payload->>'mode',retry_count,available_at FROM processing_tasks WHERE owner_user_id::bigint=$1 AND status='pending' AND dedupe_key=$2")
        .bind(user).bind(format!("briefing_refresh:{user}:sweep")).fetch_one(&pool).await.unwrap();
    assert_eq!(mode, "sweep");
    assert_eq!(retries, 0);
    assert!(available <= Utc::now().naive_utc());
    let count: i64 =
        sqlx::query_scalar("SELECT count(*) FROM processing_tasks WHERE owner_user_id::bigint=$1")
            .bind(user)
            .fetch_one(&pool)
            .await
            .unwrap();
    assert_eq!(
        count, 2,
        "repeated publications coalesce into one durable successor"
    );
    assert!(
        queue.claim(&request).await.unwrap().is_none(),
        "same-owner exclusion keeps the sweep pending"
    );
    // A fresh publication must also preserve an existing successor's provider backoff.
    sqlx::query("UPDATE processing_tasks SET retry_count=1,available_at=now()+interval '1 hour' WHERE id::bigint=$1")
        .bind(sweep).execute(&pool).await.unwrap();
    let before: NaiveDateTime =
        sqlx::query_scalar("SELECT available_at FROM processing_tasks WHERE id::bigint=$1")
            .bind(sweep)
            .fetch_one(&pool)
            .await
            .unwrap();
    let mut transaction = pool.begin().await.unwrap();
    enqueue_ready_fanout(&mut transaction, &queue, &plan)
        .await
        .unwrap();
    transaction.commit().await.unwrap();
    let after: NaiveDateTime =
        sqlx::query_scalar("SELECT available_at FROM processing_tasks WHERE id::bigint=$1")
            .bind(sweep)
            .fetch_one(&pool)
            .await
            .unwrap();
    assert_eq!(after, before);
    sqlx::query("UPDATE processing_tasks SET retry_count=0,available_at=now() WHERE id::bigint=$1")
        .bind(sweep)
        .execute(&pool)
        .await
        .unwrap();
    queue
        .finalize(&running, &TaskResult::ok(), 3)
        .await
        .unwrap()
        .unwrap();
    let successor = queue.claim(&request).await.unwrap().unwrap();
    assert_eq!(successor.id, sweep);
    assert_eq!(successor.payload["mode"], "sweep");
}
