use std::sync::Arc;

use newsly_domain::RuntimeOwner;
use newsly_providers::{ScrapeProviderOutcome, ScrapedContentItem, ScrapedItem, ScrapedNewsItem};
use newsly_queue::{OwnedWorkPlan, QueueKernel, TaskQueue, TaskType};
use serde_json::{Map, Value, json};
use sqlx::PgPool;

use super::{
    AggregatorKey, RequestedSource, ScrapeFinalizationFailures, ScrapeFinalizer, ScrapeRequest,
    SourceOutcome, configured_source_outcome,
};
use crate::TaskFinalizerResult;

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
