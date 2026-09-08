use std::sync::{Arc, Mutex};

use newsly_domain::{ResourceKey, RuntimeOwner};
use newsly_providers::{
    ScrapeFailure, ScrapeProviderOutcome, ScrapedContentItem, ScrapedItem, ScrapedNewsItem,
    normalize_feed_document,
};
use newsly_queue::{
    ClaimRequest, ClaimRuntimeScope, EnqueueRequest, OwnedWorkPlan, QueueKernel, TaskQueue,
    TaskResult, TaskType,
};
use serde_json::{Map, Value, json};
use sqlx::PgPool;

use super::{
    AggregatorKey, RequestedSource, ScrapeFinalizationFailures, ScrapeFinalizer, ScrapeRequest,
    SourceOutcome, SourcePlanKind, build_source_plans, configured_source_outcome,
};
use crate::{
    HandlerExecution, HandlerFuture, HandlerRegistry, LeaseHealth, TaskFinalizerResult,
    TaskHandler, WorkerAttempt, WorkerConfig, WorkerKernel,
};

#[derive(Debug)]
struct PreparedScrapeHandler(Mutex<Option<ScrapeFinalizer>>);

impl TaskHandler for PreparedScrapeHandler {
    fn task_type(&self) -> TaskType {
        TaskType::Scrape
    }
    fn execute(&self, _plan: Arc<OwnedWorkPlan>, _lease: LeaseHealth) -> HandlerFuture<'_> {
        let finalizer = self.0.lock().unwrap().take().unwrap();
        Box::pin(async move { HandlerExecution::with_finalizer(TaskResult::ok(), finalizer) })
    }
}

fn task(sources: Vec<Value>) -> OwnedWorkPlan {
    OwnedWorkPlan {
        task_id: 1,
        owner_user_id: None,
        task_type: TaskType::Scrape,
        content_id: None,
        payload: Map::from_iter([("sources".to_owned(), Value::Array(sources))]),
        retry_count: 0,
        queue_name: TaskQueue::Content,
        executor_runtime: RuntimeOwner::Rust,
        executor_version: 1,
        executor_namespace: "scrape".to_owned(),
    }
}

#[test]
fn all_expands_to_every_native_source() {
    let request =
        ScrapeRequest::parse(&task(vec![Value::from("all")])).expect("all should normalize");
    assert!(request.sources.contains(&RequestedSource::Reddit));
    assert!(request.sources.contains(&RequestedSource::Podcast));
    assert!(
        request
            .sources
            .contains(&RequestedSource::Aggregator(AggregatorKey::HackerNews))
    );
}

#[test]
fn unknown_source_is_rejected_before_provider_work() {
    let error = ScrapeRequest::parse(&task(vec![Value::from("unknown")]))
        .expect_err("unknown source should fail");
    assert!(error.contains("unknown scrape source"));
}

#[test]
fn display_names_normalize_to_canonical_aggregators() {
    let request = ScrapeRequest::parse(&task(vec![Value::from("Hacker News")]))
        .expect("legacy display name should normalize");
    assert_eq!(
        request.sources,
        vec![RequestedSource::Aggregator(AggregatorKey::HackerNews)]
    );
}

#[test]
fn configured_feed_dispatch_keeps_each_target_in_its_own_task() {
    let targets = [41, 42]
        .into_iter()
        .map(|id| newsly_providers::FeedScrapeTarget {
            config_id: id,
            user_id: 7,
            entry_selection: newsly_providers::FeedEntrySelection::StopAtKnown,
            scraper_type: "podcast_rss".to_owned(),
            display_name: None,
            feed_url: format!("https://example.com/{id}/feed"),
            limit: 10,
            fingerprint: "fixture".to_owned(),
            known_urls: std::collections::BTreeSet::default(),
        })
        .collect();
    let requests = super::isolated_scrape_requests(
        &[super::SourcePlan {
            source: "podcast".to_owned(),
            kind: super::SourcePlanKind::Feed(targets),
        }],
        Some(99),
    );
    assert_eq!(requests.len(), 2);
    for (request, id) in requests.iter().zip([41, 42]) {
        assert_eq!(request.owner_user_id, Some(7));
        let payload = request.payload.as_ref().unwrap();
        assert_eq!(payload.get("config_id"), Some(&json!(id)));
        assert_eq!(payload.get("sources"), Some(&json!(["podcast"])));
        assert_eq!(payload.get("first_edition_run_id"), Some(&json!(99)));
    }
    assert_ne!(requests[0].dedupe_key, requests[1].dedupe_key);
}

fn scraped_news(url: &str, visibility_scope: &str) -> ScrapedItem {
    ScrapedItem::News(Box::new(ScrapedNewsItem {
        url: url.to_owned(),
        title: Some("Story".to_owned()),
        visibility_scope: visibility_scope.to_owned(),
        owner_user_id: None,
        platform: "sciurls".to_owned(),
        source_type: "SciURLs".to_owned(),
        source_label: None,
        source_external_id: None,
        user_scraper_config_id: None,
        canonical_item_url: Some(url.to_owned()),
        canonical_story_url: Some(url.to_owned()),
        article_url: Some(url.to_owned()),
        article_domain: Some("example.com".to_owned()),
        discussion_url: None,
        summary_key_points: Vec::new(),
        summary_text: None,
        raw_metadata: json!({}),
        status: "ready".to_owned(),
        published_at: None,
    }))
}

fn scraped_content(url: &str, content_type: &str, user_id: i64, config_id: i64) -> ScrapedItem {
    ScrapedItem::Content(Box::new(ScrapedContentItem {
        url: url.to_owned(),
        source_url: url.to_owned(),
        title: Some(format!("Test {content_type}")),
        content_type: content_type.to_owned(),
        user_id,
        source: Some("Test feed".to_owned()),
        platform: "rss".to_owned(),
        metadata: json!({}),
        published_at: None,
        config_id,
    }))
}

#[test]
fn persistence_failure_keeps_an_existing_retryable_source_failure() {
    let outcomes = [SourceOutcome {
        source: "atom".to_owned(),
        required_config_ids: Vec::new(),
        result: Err(newsly_providers::ScrapeFailure {
            retry_after: None,
            message: "feed timed out".to_owned(),
            retryable: true,
        }),
        discussion_catchup: false,
    }];
    let mut failures = ScrapeFinalizationFailures::new(&outcomes, false);
    failures.record_persistence(&newsly_db::ScrapeRepositoryError::InvalidRecord(
        "invalid test record",
    ));

    assert!(matches!(
        failures.into_result(),
        TaskFinalizerResult::Override(ref result) if result.retryable
    ));
}

#[test]
fn configured_source_preserves_retry_policy_and_target_identity() {
    let outcome = configured_source_outcome(
        "podcast".to_owned(),
        Some((
            42,
            Err(newsly_providers::ScrapeFailure {
                message: "http_status".to_owned(),
                retryable: true,
                retry_after: Some(120),
            }),
        )),
    );
    assert_eq!(outcome.required_config_ids, [42]);
    assert!(outcome.failed_without_progress());
    assert!(outcome.retryable_failure());
    assert_eq!(outcome.result.unwrap_err().retry_after, Some(120));
    let empty = configured_source_outcome("podcast".to_owned(), None);
    assert!(empty.required_config_ids.is_empty());
    assert!(!empty.failed_without_progress());
}

const ARTICLE_FEED_WITH_ARCHIVE: &[u8] = br#"<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Articles</title>
  <entry><id>new-article</id><title>New article</title><link href="https://example.com/articles/new" /></entry>
  <entry><id>broken-article</id><title>Broken article</title></entry>
  <entry><id>known-article</id><title>Known article</title><link href="https://example.com/articles/known" /></entry>
  <entry><id>archived-article</id><title>Archived article</title><link href="https://example.com/articles/archive" /></entry>
</feed>"#;

const PODCAST_FEED_WITH_ARCHIVE: &[u8] = br#"<rss version="2.0"><channel><title>Podcasts</title>
  <item><guid>new-podcast</guid><title>New podcast</title><enclosure url="https://example.com/podcasts/new.mp3" type="audio/mpeg" /></item>
  <item><guid>broken-podcast</guid><title>Broken podcast</title></item>
  <item><guid>known-podcast</guid><title>Known podcast</title><enclosure url="https://example.com/podcasts/known.mp3" type="audio/mpeg" /></item>
  <item><guid>archived-podcast</guid><title>Archived podcast</title><enclosure url="https://example.com/podcasts/archive.mp3" type="audio/mpeg" /></item>
</channel></rss>"#;

async fn run_scheduled_feed_cycle(pool: &PgPool, user_id: i64, new_head: bool) {
    let request = ScrapeRequest {
        sources: vec![RequestedSource::Atom, RequestedSource::Podcast],
        first_edition_run_id: None,
    };
    let prepared = newsly_db::prepare_scrape_sources(
        pool,
        None,
        None,
        Some(user_id),
        &["atom", "podcast_rss"],
    )
    .await
    .expect("scheduled feed configs should prepare");
    let plans = build_source_plans(&request, &prepared).expect("source plans should build");
    let mut outcomes = Vec::new();

    for plan in plans {
        let SourcePlanKind::Feed(mut targets) = plan.kind else {
            panic!("scheduled article and podcast plans must be feeds");
        };
        let Some(target) = targets.first_mut() else {
            continue;
        };
        let content_type = if target.scraper_type == "podcast_rss" {
            "podcast"
        } else {
            "article"
        };
        target.known_urls = newsly_db::known_feed_urls(pool, target.user_id, content_type)
            .await
            .expect("known feed frontier should load");
        let document = if content_type == "podcast" {
            PODCAST_FEED_WITH_ARCHIVE
        } else {
            ARTICLE_FEED_WITH_ARCHIVE
        };
        let document = String::from_utf8(document.to_vec()).unwrap();
        let document = if new_head {
            document
                .replace("/new", "/newer")
                .replace("new-podcast", "newer-podcast")
                .replace("new-article", "newer-article")
        } else {
            document
        };
        outcomes.push(SourceOutcome {
            source: plan.source,
            required_config_ids: vec![target.config_id],
            result: normalize_feed_document(target, document.as_bytes())
                .map_err(ScrapeFailure::from),
            discussion_catchup: false,
        });
    }

    let finalizer = ScrapeFinalizer {
        queue: QueueKernel::new(pool.clone()),
        request,
        prepared,
        outcomes,
    };
    let queue = QueueKernel::new(pool.clone());
    let mut enqueue = EnqueueRequest::new(TaskType::Scrape);
    enqueue.payload = json!({"sources":["atom","podcast"]}).as_object().cloned();
    let parent = queue.enqueue(enqueue).await.unwrap();
    let scope =
        ClaimRuntimeScope::namespaces(RuntimeOwner::Rust, [ResourceKey::new("scrape").unwrap()])
            .unwrap();
    let mut claim = ClaimRequest::for_queue("scheduled-frontier-test", TaskQueue::Content, scope);
    claim.task_type = Some(TaskType::Scrape);
    let mut handlers = HandlerRegistry::new();
    handlers
        .register(PreparedScrapeHandler(Mutex::new(Some(finalizer))))
        .unwrap();
    let worker = WorkerKernel::new(queue, handlers, WorkerConfig::new(claim), None).unwrap();
    assert!(matches!(
        worker.run_once().await.unwrap(),
        WorkerAttempt::Completed(_)
    ));
    let status: String = sqlx::query_scalar("SELECT status FROM processing_tasks WHERE id = $1")
        .bind(parent)
        .fetch_one(pool)
        .await
        .unwrap();
    assert_eq!(status, "completed");
}

#[sqlx::test]
async fn scheduled_feed_cycles_never_cross_the_persisted_frontier(pool: PgPool) {
    newsly_db::run_migrations(&pool)
        .await
        .expect("schema should migrate");
    let user_id = sqlx::query_scalar::<_, i64>(
        r"
        INSERT INTO users (apple_id, email, is_admin, is_active)
        VALUES ('scheduled-frontier-test', 'scheduled-frontier@example.com', false, true)
        RETURNING id::bigint
        ",
    )
    .fetch_one(&pool)
    .await
    .expect("test user should insert");
    sqlx::query(
        r#"
        INSERT INTO user_scraper_configs (
            user_id, scraper_type, display_name, feed_url, config, is_active
        )
        VALUES
            ($1::bigint::integer, 'atom', 'Articles', 'https://example.com/articles.xml', '{"limit": 10}', true),
            ($1::bigint::integer, 'podcast_rss', 'Podcasts', 'https://example.com/podcasts.xml', '{"limit": 10}', true)
        "#,
    )
    .bind(user_id)
    .execute(&pool)
    .await
    .expect("test scraper configs should insert");
    let known = sqlx::query_as::<_, (i64, String)>(
        r"
        INSERT INTO contents (
            content_type, url, title, status, content_metadata, is_aggregate
        )
        VALUES
            ('article', 'https://example.com/articles/known', 'Known article', 'completed', '{}', false),
            ('podcast', 'https://example.com/podcasts/known.mp3', 'Known podcast', 'completed', '{}', false)
        RETURNING id::bigint, content_type
        ",
    )
    .fetch_all(&pool)
    .await
    .expect("known frontier content should insert");
    for (content_id, _) in known {
        sqlx::query(
            r"
            INSERT INTO content_status (user_id, content_id, status, created_at, updated_at)
            VALUES ($1::bigint::integer, $2::bigint::integer, 'archived', timezone('UTC', now()), timezone('UTC', now()))
            ",
        )
        .bind(user_id)
        .bind(content_id)
        .execute(&pool)
        .await
        .expect("archived membership should insert");
        sqlx::query("INSERT INTO content_read_status(user_id,content_id,read_at) VALUES($1::bigint::integer,$2::bigint::integer,now())").bind(user_id).bind(content_id).execute(&pool).await.unwrap();
        sqlx::query("INSERT INTO content_knowledge_saves(user_id,content_id) VALUES($1::bigint::integer,$2::bigint::integer)").bind(user_id).bind(content_id).execute(&pool).await.unwrap();
    }

    run_scheduled_feed_cycle(&pool, user_id, false).await;
    run_scheduled_feed_cycle(&pool, user_id, false).await;

    let urls =
        sqlx::query_scalar::<_, String>("SELECT url FROM contents ORDER BY content_type, url")
            .fetch_all(&pool)
            .await
            .expect("persisted URLs should be queryable");
    assert_eq!(
        urls,
        [
            "https://example.com/articles/known",
            "https://example.com/articles/new",
            "https://example.com/podcasts/known.mp3",
            "https://example.com/podcasts/new.mp3",
        ]
    );
    let downstream_count = sqlx::query_scalar::<_, i64>(
        "SELECT count(*)::bigint FROM processing_tasks WHERE task_type = 'process_content'",
    )
    .fetch_one(&pool)
    .await
    .expect("downstream task count should be queryable");
    assert_eq!(downstream_count, 2);
    let source_counts = sqlx::query_as::<_, (i64, i64)>(
        r"
        SELECT persisted_count, new_count
        FROM source_ingestion_health
        WHERE config_id IS NOT NULL
        ORDER BY config_id
        ",
    )
    .fetch_all(&pool)
    .await
    .expect("source health should be queryable");
    assert_eq!(source_counts, [(0, 0), (0, 0)]);
    run_scheduled_feed_cycle(&pool, user_id, true).await;
    run_scheduled_feed_cycle(&pool, user_id, true).await;
    let child_count: i64 = sqlx::query_scalar(
        "SELECT count(*) FROM processing_tasks WHERE task_type = 'process_content'",
    )
    .fetch_one(&pool)
    .await
    .unwrap();
    assert_eq!(child_count, 4);
    let archived: i64 = sqlx::query_scalar(
        "SELECT count(*) FROM content_status WHERE user_id = $1 AND status = 'archived'",
    )
    .bind(user_id)
    .fetch_one(&pool)
    .await
    .unwrap();
    assert_eq!(
        archived, 2,
        "polling must preserve archived known memberships"
    );
    let historical: i64 =
        sqlx::query_scalar("SELECT count(*) FROM contents WHERE url LIKE '%/archive%'")
            .fetch_one(&pool)
            .await
            .unwrap();
    assert_eq!(historical, 0);
    let reads: i64 = sqlx::query_scalar("SELECT count(*) FROM content_read_status")
        .fetch_one(&pool)
        .await
        .unwrap();
    let saves: i64 = sqlx::query_scalar("SELECT count(*) FROM content_knowledge_saves")
        .fetch_one(&pool)
        .await
        .unwrap();
    assert_eq!((reads, saves), (2, 2));
    sqlx::query("UPDATE user_scraper_configs SET is_active = false")
        .execute(&pool)
        .await
        .unwrap();
    run_scheduled_feed_cycle(&pool, user_id, false).await;
    let remaining: i64 = sqlx::query_scalar(
        "SELECT count(*) FROM processing_tasks WHERE task_type = 'process_content'",
    )
    .fetch_one(&pool)
    .await
    .unwrap();
    assert_eq!(
        remaining, 4,
        "disabled sources do not schedule fresh child work"
    );
}

#[sqlx::test]
async fn database_failure_preserves_independent_items(pool: PgPool) {
    newsly_db::run_migrations(&pool)
        .await
        .expect("schema should migrate");
    let user_id = sqlx::query_scalar::<_, i64>(
        r"
        INSERT INTO users (apple_id, email, is_admin, is_active)
        VALUES ('scrape-finalizer-test', 'scrape-finalizer@example.com', false, true)
        RETURNING id::bigint
        ",
    )
    .fetch_one(&pool)
    .await
    .expect("test user should insert");
    let config_ids = sqlx::query_scalar::<_, i64>(
        r"
        INSERT INTO user_scraper_configs (
            user_id, scraper_type, display_name, feed_url, config, is_active
        )
        VALUES
            ($1::bigint::integer, 'atom', 'Articles', 'https://example.com/articles.xml', '{}', true),
            ($1::bigint::integer, 'podcast_rss', 'Podcasts', 'https://example.com/podcasts.xml', '{}', true)
        RETURNING id::bigint
        ",
    )
    .bind(user_id)
    .fetch_all(&pool)
    .await
    .expect("test scraper configs should insert");
    let prepared = newsly_db::prepare_scrape_sources(
        &pool,
        None,
        None,
        None,
        &["atom", "podcast_rss", "aggregator"],
    )
    .await
    .expect("scraper configs should prepare");
    let article_url = "https://example.com/article";
    let podcast_url = "https://example.com/podcast";
    let news_url = "https://example.com/news";
    let rejected_url = "https://example.com/rejected";
    let finalizer = ScrapeFinalizer {
        queue: QueueKernel::new(pool.clone()),
        request: ScrapeRequest {
            sources: vec![RequestedSource::Aggregator(AggregatorKey::SciUrls)],
            first_edition_run_id: None,
        },
        prepared,
        outcomes: vec![SourceOutcome {
            source: "sciurls".to_owned(),
            required_config_ids: Vec::new(),
            result: Ok(ScrapeProviderOutcome {
                retryable_failure: false,
                items: vec![
                    scraped_content(
                        rejected_url,
                        "article",
                        i64::from(i32::MAX) + 1,
                        config_ids[0],
                    ),
                    scraped_content(article_url, "article", user_id, config_ids[0]),
                    scraped_content(podcast_url, "podcast", user_id, config_ids[1]),
                    scraped_news(news_url, "global"),
                ],
                item_errors: Vec::new(),
            }),
            discussion_catchup: false,
        }],
    };

    let mut transaction = pool.begin().await.expect("transaction should begin");
    let result = finalizer
        .apply_inner(&mut transaction)
        .await
        .expect("database failure should be contained");
    transaction
        .commit()
        .await
        .expect("transaction should commit");

    assert!(matches!(
        result,
        TaskFinalizerResult::Override(ref task_result)
            if !task_result.success && task_result.retryable
    ));
    let rejected_count =
        sqlx::query_scalar::<_, i64>("SELECT count(*)::bigint FROM contents WHERE url = $1")
            .bind(rejected_url)
            .fetch_one(&pool)
            .await
            .expect("rejected content should be queryable");
    assert_eq!(rejected_count, 0);
    let persisted_content_types = sqlx::query_scalar::<_, String>(
        "SELECT content_type FROM contents WHERE url = ANY($1) ORDER BY content_type",
    )
    .bind([article_url, podcast_url])
    .fetch_all(&pool)
    .await
    .expect("persisted content should be queryable");
    assert_eq!(persisted_content_types, ["article", "podcast"]);
    let processing_count = sqlx::query_scalar::<_, i64>(
        r"
        SELECT count(*)::bigint
        FROM processing_tasks AS task
        JOIN contents AS content ON content.id = task.content_id
        WHERE task.task_type = 'process_content'
          AND content.url = ANY($1)
        ",
    )
    .bind([article_url, podcast_url])
    .fetch_one(&pool)
    .await
    .expect("downstream content work should be queryable");
    assert_eq!(processing_count, 2);
    let news_count = sqlx::query_scalar::<_, i64>(
        "SELECT count(*)::bigint FROM news_items WHERE canonical_item_url = $1",
    )
    .bind(news_url)
    .fetch_one(&pool)
    .await
    .expect("persisted news should be queryable");
    assert_eq!(news_count, 1);
}

#[allow(dead_code)]
fn _assert_send_sync() {
    fn assert_send_sync<T: Send + Sync>() {}
    assert_send_sync::<Arc<ScrapeRequest>>();
}
