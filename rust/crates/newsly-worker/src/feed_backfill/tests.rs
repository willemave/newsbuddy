use std::sync::Mutex;

use newsly_domain::{ResourceKey, RuntimeOwner};
use newsly_providers::{ScrapeProviderOutcome, ScrapedContentItem, ScrapedItem};
use newsly_queue::{
    ClaimRequest, ClaimRuntimeScope, EnqueueRequest, OwnedWorkPlan, QueueKernel, TaskQueue,
    TaskResult, TaskType,
};
use serde_json::{Value, json};
use sqlx::PgPool;

use crate::{
    HandlerExecution, HandlerFuture, HandlerRegistry, LeaseHealth, TaskFinalizerResult,
    TaskHandler, WorkerAttempt, WorkerConfig, WorkerKernel,
};

use super::{
    FeedBackfillFinalizer, FeedBackfillRequest, FeedFetchOutcome, PreparedFeedConfigs,
    feed_outcome_succeeded, prepare_configs,
};

async fn insert_user(pool: &PgPool) -> i64 {
    newsly_db::run_migrations(pool)
        .await
        .expect("schema should migrate");
    sqlx::query_scalar::<_, i64>(
        r"
        INSERT INTO users (apple_id, email, is_admin, is_active)
        VALUES ('feed-pipeline-user', 'feed-pipeline@example.test', FALSE, TRUE)
        RETURNING id::bigint
        ",
    )
    .fetch_one(pool)
    .await
    .expect("test user should insert")
}

async fn insert_feed_configs(pool: &PgPool, user_id: i64) -> Vec<i64> {
    let mut config_ids = sqlx::query_scalar::<_, i64>(
        r#"
        INSERT INTO user_scraper_configs (
            user_id, scraper_type, display_name, feed_url, config, is_active
        )
        VALUES
            ($1::bigint::integer, 'atom', 'Article source',
             'https://articles.example.test/feed.xml', '{"limit": 10}', TRUE),
            ($1::bigint::integer, 'podcast_rss', 'Podcast show',
             'https://podcasts.example.test/feed.xml', '{"limit": 10}', TRUE)
        RETURNING id::bigint
        "#,
    )
    .bind(user_id)
    .fetch_all(pool)
    .await
    .expect("test feed configs should insert");
    config_ids.sort_unstable();
    config_ids
}

fn request(user_id: i64, config_ids: Vec<i64>) -> FeedBackfillRequest {
    FeedBackfillRequest {
        user_id,
        config_ids,
        count: 2,
        first_edition_run_id: None,
    }
}

fn article(config_id: i64, user_id: i64) -> ScrapedItem {
    ScrapedItem::Content(Box::new(ScrapedContentItem {
        url: "https://articles.example.test/posts/one".to_owned(),
        source_url: "https://articles.example.test/posts/one".to_owned(),
        title: Some("Article one".to_owned()),
        content_type: "article".to_owned(),
        user_id,
        source: Some("Article source".to_owned()),
        platform: "atom".to_owned(),
        metadata: json!({
            "feed_config_id": config_id,
            "feed_url": "https://articles.example.test/feed.xml",
            "author": "Article author",
            "rss_content": "Article body",
            "word_count": 2,
            "tags": ["rust"],
        }),
        published_at: None,
        config_id,
    }))
}

fn podcast(config_id: i64, user_id: i64) -> ScrapedItem {
    ScrapedItem::Content(Box::new(ScrapedContentItem {
        url: "https://podcasts.example.test/episodes/one".to_owned(),
        source_url: "https://podcasts.example.test/episodes/one".to_owned(),
        title: Some("Episode one".to_owned()),
        content_type: "podcast".to_owned(),
        user_id,
        source: Some("Podcast show".to_owned()),
        platform: "podcast".to_owned(),
        metadata: json!({
            "feed_config_id": config_id,
            "feed_url": "https://podcasts.example.test/feed.xml",
            "audio_url": "https://cdn.example.test/episodes/one.mp3",
            "episode_number": 7,
            "duration_seconds": 1234,
        }),
        published_at: None,
        config_id,
    }))
}

fn outcomes(prepared: &PreparedFeedConfigs, user_id: i64) -> Vec<FeedFetchOutcome> {
    prepared
        .plans
        .iter()
        .cloned()
        .map(|plan| {
            let item = if plan.scraper_type == "podcast_rss" {
                podcast(plan.id, user_id)
            } else {
                article(plan.id, user_id)
            };
            FeedFetchOutcome {
                plan,
                result: Ok(ScrapeProviderOutcome {
                    retryable_failure: false,
                    items: vec![item],
                    item_errors: Vec::new(),
                }),
            }
        })
        .collect()
}

async fn apply_and_commit(pool: &PgPool, finalizer: &FeedBackfillFinalizer) -> TaskFinalizerResult {
    let mut transaction = pool.begin().await.expect("transaction should begin");
    let result = finalizer
        .apply_inner(&mut transaction)
        .await
        .expect("feed backfill should finalize");
    transaction
        .commit()
        .await
        .expect("transaction should commit");
    result
}

#[derive(Debug)]
struct PreparedBackfillHandler {
    finalizer: Mutex<Option<FeedBackfillFinalizer>>,
}

impl TaskHandler for PreparedBackfillHandler {
    fn task_type(&self) -> TaskType {
        TaskType::BackfillFeeds
    }

    fn execute(
        &self,
        _plan: std::sync::Arc<OwnedWorkPlan>,
        _lease: LeaseHealth,
    ) -> HandlerFuture<'_> {
        let finalizer = self
            .finalizer
            .lock()
            .expect("test finalizer lock should not be poisoned")
            .take();
        Box::pin(async move {
            finalizer.map_or_else(
                || {
                    HandlerExecution::from_result(TaskResult::fail(
                        Some("test finalizer already consumed".to_owned()),
                        false,
                    ))
                },
                |finalizer| HandlerExecution::with_finalizer(TaskResult::ok(), finalizer),
            )
        })
    }
}

#[test]
fn item_errors_without_usable_entries_fail_the_feed() {
    assert!(!feed_outcome_succeeded(&ScrapeProviderOutcome {
        retryable_failure: false,
        items: Vec::new(),
        item_errors: vec!["episode has no audio".to_owned()],
    }));
}

#[test]
fn empty_feed_without_item_errors_is_still_valid() {
    assert!(feed_outcome_succeeded(&ScrapeProviderOutcome {
        retryable_failure: false,
        items: Vec::new(),
        item_errors: Vec::new(),
    }));
}

#[sqlx::test]
async fn article_and_podcast_backfill_commit_with_membership_and_work(pool: PgPool) {
    let user_id = insert_user(&pool).await;
    let config_ids = insert_feed_configs(&pool, user_id).await;
    let request = request(user_id, config_ids);
    let prepared = prepare_configs(&pool, &request)
        .await
        .expect("configs should prepare")
        .expect("active user should prepare");
    let finalizer = FeedBackfillFinalizer {
        queue: QueueKernel::new(pool.clone()),
        results: outcomes(&prepared, user_id),
        request,
    };

    assert!(matches!(
        apply_and_commit(&pool, &finalizer).await,
        TaskFinalizerResult::Keep
    ));

    let contents = sqlx::query_as::<_, (i64, String, String, Option<String>, Value)>(
        r"
        SELECT id::bigint, content_type, status, platform, content_metadata::jsonb
        FROM contents
        WHERE url IN (
            'https://articles.example.test/posts/one',
            'https://podcasts.example.test/episodes/one'
        )
        ORDER BY content_type
        ",
    )
    .fetch_all(&pool)
    .await
    .expect("content should be queryable");
    assert_eq!(contents.len(), 2);
    assert_eq!(contents[0].1, "article");
    assert_eq!(contents[0].2, "pending");
    assert_eq!(contents[0].3.as_deref(), Some("atom"));
    assert_eq!(contents[0].4["rss_content"], "Article body");
    assert_eq!(contents[0].4["submitted_via"], "feed_backfill");
    assert_eq!(contents[1].1, "podcast");
    assert_eq!(contents[1].2, "pending");
    assert_eq!(contents[1].3.as_deref(), Some("podcast"));
    assert_eq!(
        contents[1].4["audio_url"],
        "https://cdn.example.test/episodes/one.mp3"
    );
    assert_eq!(contents[1].4["duration_seconds"], 1234);
    assert_eq!(contents[1].4["submitted_via"], "feed_backfill");

    let memberships = sqlx::query_as::<_, (i64, String)>(
        r"
        SELECT content_id::bigint, status
        FROM content_status
        WHERE user_id::bigint = $1
        ORDER BY content_id
        ",
    )
    .bind(user_id)
    .fetch_all(&pool)
    .await
    .expect("memberships should be queryable");
    assert_eq!(memberships.len(), 2);
    assert!(memberships.iter().all(|(_, status)| status == "inbox"));

    let queued_ids = sqlx::query_scalar::<_, i64>(
        r"
        SELECT content_id::bigint
        FROM processing_tasks
        WHERE task_type = 'process_content'
        ORDER BY content_id
        ",
    )
    .fetch_all(&pool)
    .await
    .expect("processing work should be queryable");
    assert_eq!(
        queued_ids,
        contents.iter().map(|row| row.0).collect::<Vec<_>>()
    );
}

#[sqlx::test]
async fn queue_fence_commits_parent_completion_with_backfill_outputs(pool: PgPool) {
    let user_id = insert_user(&pool).await;
    let config_ids = insert_feed_configs(&pool, user_id).await;
    let request = request(user_id, config_ids.clone());
    let prepared = prepare_configs(&pool, &request)
        .await
        .expect("configs should prepare")
        .expect("active user should prepare");
    let finalizer = FeedBackfillFinalizer {
        queue: QueueKernel::new(pool.clone()),
        results: outcomes(&prepared, user_id),
        request,
    };
    let queue = QueueKernel::new(pool.clone());
    let mut enqueue = EnqueueRequest::new(TaskType::BackfillFeeds);
    enqueue.owner_user_id = Some(user_id);
    enqueue.payload = json!({
        "user_id": user_id,
        "config_ids": config_ids,
        "count": 2,
    })
    .as_object()
    .cloned();
    let parent_task_id = queue
        .enqueue(enqueue)
        .await
        .expect("parent backfill task should enqueue");
    let scope = ClaimRuntimeScope::namespaces(
        RuntimeOwner::Rust,
        [ResourceKey::new(TaskType::BackfillFeeds.as_str())
            .expect("backfill namespace should be valid")],
    )
    .expect("claim scope should be valid");
    let mut claim = ClaimRequest::for_queue("feed-pipeline-test", TaskQueue::Backfill, scope);
    claim.task_type = Some(TaskType::BackfillFeeds);
    let mut handlers = HandlerRegistry::new();
    handlers
        .register(PreparedBackfillHandler {
            finalizer: Mutex::new(Some(finalizer)),
        })
        .expect("test handler should register");
    let worker = WorkerKernel::new(queue, handlers, WorkerConfig::new(claim), None)
        .expect("test worker should build");

    let attempt = worker
        .run_once()
        .await
        .expect("worker should finalize once");
    assert!(matches!(attempt, WorkerAttempt::Completed(_)));
    let parent_status = sqlx::query_scalar::<_, String>(
        "SELECT status FROM processing_tasks WHERE id::bigint = $1",
    )
    .bind(parent_task_id)
    .fetch_one(&pool)
    .await
    .expect("parent status should be queryable");
    assert_eq!(parent_status, "completed");
    let child_count = sqlx::query_scalar::<_, i64>(
        r"
        SELECT count(*)::bigint
        FROM processing_tasks
        WHERE task_type = 'process_content' AND status = 'pending'
        ",
    )
    .fetch_one(&pool)
    .await
    .expect("child tasks should be queryable");
    assert_eq!(child_count, 2);
    let content_count = sqlx::query_scalar::<_, i64>("SELECT count(*)::bigint FROM contents")
        .fetch_one(&pool)
        .await
        .expect("content should be queryable");
    assert_eq!(content_count, 2);
}

#[sqlx::test]
async fn duplicate_backfill_preserves_user_state_and_shares_existing_work(pool: PgPool) {
    let user_id = insert_user(&pool).await;
    let config_ids = insert_feed_configs(&pool, user_id).await;
    let first_request = request(user_id, config_ids.clone());
    let first_prepared = prepare_configs(&pool, &first_request)
        .await
        .expect("configs should prepare")
        .expect("active user should prepare");
    let first = FeedBackfillFinalizer {
        queue: QueueKernel::new(pool.clone()),
        results: outcomes(&first_prepared, user_id),
        request: first_request,
    };
    apply_and_commit(&pool, &first).await;

    sqlx::query(
        r"
        UPDATE content_status AS membership
        SET status = 'archived'
        FROM contents AS content
        WHERE membership.content_id = content.id
          AND membership.user_id::bigint = $1
          AND content.content_type = 'podcast'
        ",
    )
    .bind(user_id)
    .execute(&pool)
    .await
    .expect("podcast membership should archive");

    let repeat_request = request(user_id, config_ids);
    let repeat_prepared = prepare_configs(&pool, &repeat_request)
        .await
        .expect("configs should prepare again")
        .expect("active user should prepare again");
    let repeat = FeedBackfillFinalizer {
        queue: QueueKernel::new(pool.clone()),
        results: outcomes(&repeat_prepared, user_id),
        request: repeat_request,
    };
    assert!(matches!(
        apply_and_commit(&pool, &repeat).await,
        TaskFinalizerResult::Keep
    ));

    let second_user_id = sqlx::query_scalar::<_, i64>(
        r"
        INSERT INTO users (apple_id, email, is_admin, is_active)
        VALUES ('feed-pipeline-second-user', 'feed-pipeline-second@example.test', FALSE, TRUE)
        RETURNING id::bigint
        ",
    )
    .fetch_one(&pool)
    .await
    .expect("second user should insert");
    let second_config_ids = insert_feed_configs(&pool, second_user_id).await;
    let second_request = request(second_user_id, second_config_ids);
    let second_prepared = prepare_configs(&pool, &second_request)
        .await
        .expect("second user's configs should prepare")
        .expect("second active user should prepare");
    let second = FeedBackfillFinalizer {
        queue: QueueKernel::new(pool.clone()),
        results: outcomes(&second_prepared, second_user_id),
        request: second_request,
    };
    assert!(matches!(
        apply_and_commit(&pool, &second).await,
        TaskFinalizerResult::Keep
    ));

    let content_count = sqlx::query_scalar::<_, i64>(
        "SELECT count(*)::bigint FROM contents WHERE content_type IN ('article', 'podcast')",
    )
    .fetch_one(&pool)
    .await
    .expect("content count should be queryable");
    assert_eq!(content_count, 2);
    let task_count = sqlx::query_scalar::<_, i64>(
        "SELECT count(*)::bigint FROM processing_tasks WHERE task_type = 'process_content'",
    )
    .fetch_one(&pool)
    .await
    .expect("task count should be queryable");
    assert_eq!(task_count, 2);
    let second_membership_count = sqlx::query_scalar::<_, i64>(
        "SELECT count(*)::bigint FROM content_status WHERE user_id::bigint = $1 AND status = 'inbox'",
    )
    .bind(second_user_id)
    .fetch_one(&pool)
    .await
    .expect("second user's memberships should be queryable");
    assert_eq!(second_membership_count, 2);
    let podcast_status = sqlx::query_scalar::<_, String>(
        r"
        SELECT membership.status
        FROM content_status AS membership
        JOIN contents AS content ON content.id = membership.content_id
        WHERE membership.user_id::bigint = $1 AND content.content_type = 'podcast'
        ",
    )
    .bind(user_id)
    .fetch_one(&pool)
    .await
    .expect("podcast membership should be queryable");
    assert_eq!(podcast_status, "archived");
}

#[sqlx::test]
async fn partial_backfill_records_exact_first_edition_outcomes(pool: PgPool) {
    let user_id = insert_user(&pool).await;
    let config_ids = insert_feed_configs(&pool, user_id).await;
    let run_id = sqlx::query_scalar::<_, i64>(
        r"
        INSERT INTO onboarding_first_edition_runs (
            user_id, status, revision, started_at, completed_at
        )
        VALUES ($1::bigint::integer, 'active', 1, timezone('UTC', now()), NULL)
        RETURNING id::bigint
        ",
    )
    .bind(user_id)
    .fetch_one(&pool)
    .await
    .expect("first-edition run should insert");
    for (position, config_id) in config_ids.iter().enumerate() {
        sqlx::query(
            r#"
            INSERT INTO onboarding_first_edition_sources (
                run_id, source_key, display_name, source_kind, "position",
                status, processed_item_count, completed_at
            )
            VALUES (
                $1::bigint::integer, $2, $3, 'feed', $4,
                'queued', 0, NULL
            )
            "#,
        )
        .bind(run_id)
        .bind(format!("feed:{config_id}"))
        .bind(format!("Feed {config_id}"))
        .bind(i32::try_from(position).expect("fixture position should fit"))
        .execute(&pool)
        .await
        .expect("first-edition source should insert");
    }
    let mut request = request(user_id, config_ids.clone());
    request.first_edition_run_id = Some(run_id);
    let prepared = prepare_configs(&pool, &request)
        .await
        .expect("configs should prepare")
        .expect("active user should prepare");
    let results = prepared
        .plans
        .iter()
        .cloned()
        .map(|plan| {
            let result = if plan.id == config_ids[0] {
                ScrapeProviderOutcome {
                    retryable_failure: false,
                    items: vec![article(plan.id, user_id)],
                    item_errors: vec!["one malformed article".to_owned()],
                }
            } else {
                ScrapeProviderOutcome {
                    retryable_failure: false,
                    items: Vec::new(),
                    item_errors: vec!["episode has no audio".to_owned()],
                }
            };
            FeedFetchOutcome {
                plan,
                result: Ok(result),
            }
        })
        .collect();
    let finalizer = FeedBackfillFinalizer {
        queue: QueueKernel::new(pool.clone()),
        request,
        results,
    };

    assert!(matches!(
        apply_and_commit(&pool, &finalizer).await,
        TaskFinalizerResult::Keep
    ));

    let progress = sqlx::query_as::<_, (String, String, i32)>(
        r"
        SELECT source_key, status, processed_item_count
        FROM onboarding_first_edition_sources
        WHERE run_id::bigint = $1
        ORDER BY source_key
        ",
    )
    .bind(run_id)
    .fetch_all(&pool)
    .await
    .expect("first-edition progress should be queryable");
    assert_eq!(
        progress,
        [
            (format!("feed:{}", config_ids[0]), "processed".to_owned(), 1),
            (
                format!("feed:{}", config_ids[1]),
                "unavailable".to_owned(),
                0
            ),
        ]
    );
    let revision = sqlx::query_scalar::<_, i32>(
        "SELECT revision FROM onboarding_first_edition_runs WHERE id::bigint = $1",
    )
    .bind(run_id)
    .fetch_one(&pool)
    .await
    .expect("run revision should be queryable");
    assert_eq!(revision, 2);
    let content_count = sqlx::query_scalar::<_, i64>("SELECT count(*)::bigint FROM contents")
        .fetch_one(&pool)
        .await
        .expect("content should be queryable");
    assert_eq!(content_count, 1);
    let task_count = sqlx::query_scalar::<_, i64>(
        "SELECT count(*)::bigint FROM processing_tasks WHERE task_type = 'process_content'",
    )
    .fetch_one(&pool)
    .await
    .expect("tasks should be queryable");
    assert_eq!(task_count, 1);
    let health: Vec<(i64, Option<String>, bool)> = sqlx::query_as("SELECT config_id::bigint, error_code, last_success_at IS NULL FROM source_ingestion_health ORDER BY config_id").fetch_all(&pool).await.unwrap();
    assert_eq!(
        health,
        config_ids
            .iter()
            .map(|id| (*id, Some("source_items_rejected".to_owned()), true))
            .collect::<Vec<_>>()
    );
}

#[sqlx::test]
async fn changed_feed_config_fences_content_and_queue_publication(pool: PgPool) {
    let user_id = insert_user(&pool).await;
    let config_ids = insert_feed_configs(&pool, user_id).await;
    let run_id: i64 = sqlx::query_scalar("INSERT INTO onboarding_first_edition_runs (user_id, status, revision, started_at) VALUES ($1::integer, 'active', 1, now()) RETURNING id::bigint").bind(user_id).fetch_one(&pool).await.unwrap();
    for (position, config_id) in config_ids.iter().enumerate() {
        sqlx::query(r#"INSERT INTO onboarding_first_edition_sources (run_id, source_key, display_name, source_kind, "position", status, processed_item_count) VALUES ($1::integer, $2, 'Feed', 'feed', $3, 'queued', 0)"#).bind(run_id).bind(format!("feed:{config_id}")).bind(i32::try_from(position).unwrap()).execute(&pool).await.unwrap();
    }
    let mut request = request(user_id, config_ids.clone());
    request.first_edition_run_id = Some(run_id);
    let prepared = prepare_configs(&pool, &request)
        .await
        .expect("configs should prepare")
        .expect("active user should prepare");
    let results = outcomes(&prepared, user_id);
    sqlx::query("UPDATE user_scraper_configs SET display_name = 'Changed' WHERE id::bigint = $1")
        .bind(config_ids[0])
        .execute(&pool)
        .await
        .expect("config should change after provider preparation");
    let finalizer = FeedBackfillFinalizer {
        queue: QueueKernel::new(pool.clone()),
        request,
        results,
    };

    let result = apply_and_commit(&pool, &finalizer).await;
    assert!(matches!(
        result,
        TaskFinalizerResult::Override(ref task_result)
            if !task_result.success && task_result.retryable
    ));
    let content_count = sqlx::query_scalar::<_, i64>("SELECT count(*)::bigint FROM contents")
        .fetch_one(&pool)
        .await
        .expect("content count should be queryable");
    assert_eq!(content_count, 1);
    let task_count = sqlx::query_scalar::<_, i64>("SELECT count(*)::bigint FROM processing_tasks")
        .fetch_one(&pool)
        .await
        .expect("task count should be queryable");
    assert_eq!(task_count, 1);
    let states: Vec<String> = sqlx::query_scalar("SELECT status FROM onboarding_first_edition_sources WHERE run_id::bigint = $1 ORDER BY source_key").bind(run_id).fetch_all(&pool).await.unwrap();
    assert_eq!(states, ["queued", "processed"]);
}

#[sqlx::test]
async fn database_rejection_does_not_rollback_valid_backfill_siblings(pool: PgPool) {
    let user_id = insert_user(&pool).await;
    let entry = |suffix: &str, title: String| newsly_db::FeedBackfillEntry {
        url: format!("https://example.com/{suffix}"),
        source_url: format!("https://example.com/{suffix}"),
        title: Some(title),
        source: None,
        platform: "rss".to_owned(),
        metadata: json!({}),
        published_at: None,
        content_type: "article".to_owned(),
    };
    let entries = [
        entry("first", "First".to_owned()),
        entry("rejected", "x".repeat(501)),
        entry("last", "Last".to_owned()),
    ];
    let mut tx = pool.begin().await.unwrap();
    let result = newsly_db::persist_feed_backfill(
        &mut tx,
        user_id,
        newsly_db::FeedBackfillOrigin::Background,
        &entries,
    )
    .await
    .unwrap();
    tx.commit().await.unwrap();
    assert_eq!((result.saved, result.rejected), (2, 1));
    let count: i64 = sqlx::query_scalar("SELECT count(*) FROM content_status")
        .fetch_one(&pool)
        .await
        .unwrap();
    assert_eq!(count, 2);
}
