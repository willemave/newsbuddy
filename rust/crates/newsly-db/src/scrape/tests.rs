use serde_json::json;
use sqlx::PgPool;

use super::{
    ScrapedContentRecord, ScrapedNewsRecord, due_discussion_refresh_ids, merge_brutalist_topics,
    news_ingest_key, persist_scraped_content, persist_scraped_news,
};

#[test]
fn brutalist_rescrape_keeps_all_topic_visibility() {
    let previous = json!({"aggregator":{"topic":"science","topics":["science"]}});
    let incoming = json!({"aggregator":{"topic":"politics","topics":["politics"]}});
    let merged = merge_brutalist_topics(&previous, &incoming);
    assert_eq!(merged["aggregator"]["topic"], "science");
    assert_eq!(
        merged["aggregator"]["topics"],
        json!(["politics", "science"])
    );
}

#[sqlx::test(migrations = false)]
async fn brutalist_rescrape_persists_cross_listed_topics(pool: PgPool) {
    crate::run_migrations(&pool).await.unwrap();
    let mut story = record();
    story.platform = "brutalist".to_owned();
    story.source_type = "Brutalist Report".to_owned();
    story.source_external_id = None;
    story.discussion_url = None;
    story.raw_metadata = json!({"aggregator":{"topic":"science","topics":["science"]}});
    let mut tx = pool.begin().await.unwrap();
    let first = persist_scraped_news(&mut tx, &story).await.unwrap();
    story.raw_metadata = json!({"aggregator":{"topic":"politics","topics":["politics"]}});
    let second = persist_scraped_news(&mut tx, &story).await.unwrap();
    assert_eq!(first.news_item_id, second.news_item_id);
    let metadata: serde_json::Value =
        sqlx::query_scalar("SELECT raw_metadata::jsonb FROM news_items WHERE id::bigint=$1")
            .bind(first.news_item_id)
            .fetch_one(&mut *tx)
            .await
            .unwrap();
    assert_eq!(
        metadata["aggregator"]["topics"],
        json!(["politics", "science"])
    );
    tx.rollback().await.unwrap();
}

#[sqlx::test(migrations = false)]
async fn brutalist_topic_visibility_accepts_cross_listed_item(pool: PgPool) {
    crate::run_migrations(&pool).await.unwrap();
    let visible: (bool, bool) = sqlx::query_as(
        "SELECT newsly_brutalist_visible('[\"politics\"]'::jsonb, '{\"aggregator\":{\"topic\":\"science\",\"topics\":[\"science\",\"politics\"]}}'::jsonb), newsly_brutalist_visible('[\"sports\"]'::jsonb, '{\"aggregator\":{\"topic\":\"science\",\"topics\":[\"science\",\"politics\"]}}'::jsonb)"
    ).fetch_one(&pool).await.unwrap();
    assert_eq!(visible, (true, false));
}

fn record() -> ScrapedNewsRecord {
    ScrapedNewsRecord {
        visibility_scope: "global".to_owned(),
        owner_user_id: None,
        platform: "hackernews".to_owned(),
        source_type: "Hacker News".to_owned(),
        source_label: Some("example.com".to_owned()),
        source_external_id: Some("123".to_owned()),
        user_scraper_config_id: None,
        canonical_item_url: Some("https://news.ycombinator.com/item?id=123".to_owned()),
        canonical_story_url: Some("https://example.com/story".to_owned()),
        article_url: Some("https://example.com/story".to_owned()),
        article_domain: Some("example.com".to_owned()),
        discussion_url: Some("https://news.ycombinator.com/item?id=123".to_owned()),
        article_title: Some("Story".to_owned()),
        summary_key_points: Vec::new(),
        summary_text: None,
        raw_metadata: json!({}),
        status: "new".to_owned(),
        published_at: None,
    }
}

#[test]
fn ingest_key_prefers_platform_external_identity() {
    let first = news_ingest_key(&record()).expect("identity should serialize");
    let mut changed = record();
    changed.article_url = Some("https://mirror.example/story".to_owned());
    assert_eq!(
        first,
        news_ingest_key(&changed).expect("identity should serialize")
    );
}

async fn insert_news_item(
    pool: &PgPool,
    ingest_key: &str,
    canonical_item_url: &str,
    canonical_story_url: &str,
    representative_news_item_id: Option<i64>,
) -> i64 {
    sqlx::query_scalar(
        r#"
        INSERT INTO news_items (
            ingest_key, visibility_scope, platform, source_type,
            canonical_item_url, canonical_story_url, article_url,
            status, ingested_at, cluster_size, created_at, updated_at,
            representative_news_item_id
        )
        VALUES (
            $1, 'global', 'sciurls', 'SciURLs',
            $2, $3, $3,
            'ready', timezone('UTC', now()), 2, timezone('UTC', now()),
            timezone('UTC', now()), $4::bigint::integer
        )
        RETURNING id::bigint
        "#,
    )
    .bind(ingest_key)
    .bind(canonical_item_url)
    .bind(canonical_story_url)
    .bind(representative_news_item_id)
    .fetch_one(pool)
    .await
    .expect("news item should insert")
}

#[sqlx::test]
async fn exact_ingest_key_wins_over_a_representatives_story_alias(pool: PgPool) {
    let canonical_url = "https://example.com/story/";
    let representative_id = insert_news_item(
        &pool,
        "representative-key",
        "https://example.com/story",
        canonical_url,
        None,
    )
    .await;
    let mut incoming = record();
    incoming.source_external_id = None;
    incoming.canonical_item_url = Some(canonical_url.to_owned());
    incoming.canonical_story_url = Some(canonical_url.to_owned());
    incoming.article_url = Some(canonical_url.to_owned());
    let incoming_key = news_ingest_key(&incoming).expect("identity should serialize");
    let child_id = insert_news_item(
        &pool,
        &incoming_key,
        canonical_url,
        canonical_url,
        Some(representative_id),
    )
    .await;

    let mut transaction = pool.begin().await.expect("transaction should begin");
    let persisted = persist_scraped_news(&mut transaction, &incoming)
        .await
        .expect("existing child should update without colliding");
    transaction
        .commit()
        .await
        .expect("transaction should commit");

    assert_eq!(persisted.news_item_id, child_id);
    assert!(!persisted.created);
    let representative_key =
        sqlx::query_scalar::<_, String>("SELECT ingest_key FROM news_items WHERE id = $1")
            .bind(representative_id)
            .fetch_one(&pool)
            .await
            .expect("representative should remain");
    assert_eq!(representative_key, "representative-key");
}

#[sqlx::test]
async fn canonical_item_identity_does_not_match_a_story_alias(pool: PgPool) {
    let canonical_url = "https://example.com/shared";
    let representative_id = insert_news_item(
        &pool,
        "representative-key",
        "https://example.com/representative",
        canonical_url,
        None,
    )
    .await;
    let child_id = insert_news_item(
        &pool,
        "legacy-child-key",
        canonical_url,
        canonical_url,
        Some(representative_id),
    )
    .await;
    let mut incoming = record();
    incoming.source_external_id = None;
    incoming.canonical_item_url = Some(canonical_url.to_owned());
    incoming.canonical_story_url = None;
    incoming.article_url = Some(canonical_url.to_owned());

    let mut transaction = pool.begin().await.expect("transaction should begin");
    let persisted = persist_scraped_news(&mut transaction, &incoming)
        .await
        .expect("same-field identity should update the child");
    transaction
        .commit()
        .await
        .expect("transaction should commit");

    assert_eq!(persisted.news_item_id, child_id);
    let representative_key =
        sqlx::query_scalar::<_, String>("SELECT ingest_key FROM news_items WHERE id = $1")
            .bind(representative_id)
            .fetch_one(&pool)
            .await
            .expect("representative should remain");
    assert_eq!(representative_key, "representative-key");
}

#[sqlx::test]
async fn duplicate_scrape_does_not_reactivate_existing_membership(pool: PgPool) {
    let user_id = sqlx::query_scalar::<_, i64>(
        r#"
        INSERT INTO users (apple_id, email, is_admin, is_active)
        VALUES ('scrape-membership-test', 'scrape-membership@example.com', false, true)
        RETURNING id::bigint
        "#,
    )
    .fetch_one(&pool)
    .await
    .expect("test user should insert");
    let record = ScrapedContentRecord {
        url: "https://example.com/article".to_owned(),
        source_url: "https://example.com/article".to_owned(),
        title: Some("Article".to_owned()),
        content_type: "article".to_owned(),
        user_id,
        source: Some("Example".to_owned()),
        platform: "atom".to_owned(),
        metadata: json!({}),
        published_at: None,
    };
    let mut transaction = pool.begin().await.expect("transaction should begin");
    let inserted = persist_scraped_content(&mut transaction, &record)
        .await
        .expect("content should persist");
    transaction
        .commit()
        .await
        .expect("transaction should commit");
    assert!(inserted.created);

    sqlx::query(
        "UPDATE content_status SET status = 'archived' WHERE user_id::bigint = $1 AND content_id::bigint = $2",
    )
    .bind(user_id)
    .bind(inserted.content_id)
    .execute(&pool)
    .await
    .expect("membership should update");

    let mut transaction = pool.begin().await.expect("transaction should begin");
    let duplicate = persist_scraped_content(&mut transaction, &record)
        .await
        .expect("duplicate scrape should persist");
    transaction
        .commit()
        .await
        .expect("transaction should commit");
    assert!(!duplicate.created);
    let status = sqlx::query_scalar::<_, String>(
        "SELECT status FROM content_status WHERE user_id::bigint = $1 AND content_id::bigint = $2",
    )
    .bind(user_id)
    .bind(inserted.content_id)
    .fetch_one(&pool)
    .await
    .expect("membership should exist");
    assert_eq!(status, "archived");
}

#[sqlx::test]
async fn discussion_catch_up_requires_item_demand_and_bounds_merge_refreshes(pool: PgPool) {
    let user_id = sqlx::query_scalar::<_, i64>(
        r#"
        INSERT INTO users (apple_id, email, is_admin, is_active)
        VALUES ('discussion-demand-test', 'discussion-demand@example.com', false, true)
        RETURNING id::bigint
        "#,
    )
    .fetch_one(&pool)
    .await
    .expect("test user should insert");
    sqlx::query(
        r#"
        INSERT INTO user_scraper_configs (
            user_id, scraper_type, display_name, feed_url, config, is_active,
            created_at, updated_at
        )
        VALUES ($1::bigint::integer, 'aggregator', 'Hacker News', 'aggregator://hackernews',
                '{"key":"hackernews"}'::json, true, now(), now())
        "#,
    )
    .bind(user_id)
    .execute(&pool)
    .await
    .expect("subscription should insert");
    let news_item_id = sqlx::query_scalar::<_, i64>(
        r#"
        INSERT INTO news_items (
            ingest_key, visibility_scope, platform, source_type, source_external_id,
            canonical_item_url, discussion_url, status, published_at, ingested_at,
            cluster_size, created_at, updated_at
        )
        VALUES (
            'discussion-demand-item', 'global', 'hackernews', 'Hacker News', '42',
            'https://news.ycombinator.com/item?id=42',
            'https://news.ycombinator.com/item?id=42', 'ready',
            now() - interval '48 hours', now() - interval '48 hours', 1,
            now() - interval '48 hours', now()
        )
        RETURNING id::bigint
        "#,
    )
    .fetch_one(&pool)
    .await
    .expect("news item should insert");

    let mut transaction = pool.begin().await.expect("transaction should begin");
    assert!(
        due_discussion_refresh_ids(&mut transaction, 10)
            .await
            .expect("catch-up should succeed")
            .is_empty()
    );
    transaction
        .commit()
        .await
        .expect("transaction should commit");

    sqlx::query(
        "INSERT INTO briefing_pending_sources (user_id, source_kind, source_id) VALUES ($1, 'news', $2)",
    )
    .bind(user_id)
    .bind(news_item_id)
    .execute(&pool)
    .await
    .expect("pending source should insert");
    let mut transaction = pool.begin().await.expect("transaction should begin");
    assert_eq!(
        due_discussion_refresh_ids(&mut transaction, 10)
            .await
            .expect("catch-up should succeed"),
        vec![news_item_id]
    );
    transaction
        .commit()
        .await
        .expect("transaction should commit");

    sqlx::query(
        r#"
        UPDATE news_item_discussions
        SET summary = '{"overview":"ready"}'::json,
            summary_status = 'completed',
            raw_comments_ref = '{"kind":"storage"}'::json,
            next_refresh_after = NULL
        WHERE news_item_id = $1
        "#,
    )
    .bind(news_item_id)
    .execute(&pool)
    .await
    .expect("discussion should update");
    let mut transaction = pool.begin().await.expect("transaction should begin");
    assert!(
        due_discussion_refresh_ids(&mut transaction, 10)
            .await
            .expect("catch-up should succeed")
            .is_empty()
    );
    transaction
        .commit()
        .await
        .expect("transaction should commit");

    sqlx::query("DELETE FROM briefing_pending_sources WHERE user_id = $1 AND source_id = $2")
        .bind(user_id)
        .bind(news_item_id)
        .execute(&pool)
        .await
        .expect("pending source should delete");
    sqlx::query(
        "INSERT INTO news_item_read_status (user_id, news_item_id, read_at, created_at) VALUES ($1, $2, now(), now())",
    )
    .bind(user_id)
    .bind(news_item_id)
    .execute(&pool)
    .await
    .expect("open should insert");
    sqlx::query(
        "UPDATE news_item_discussions SET summary = NULL, summary_status = 'not_ready' WHERE news_item_id = $1",
    )
    .bind(news_item_id)
    .execute(&pool)
    .await
    .expect("initial summary state should restore");
    let mut transaction = pool.begin().await.expect("transaction should begin");
    assert_eq!(
        due_discussion_refresh_ids(&mut transaction, 10)
            .await
            .expect("catch-up should succeed"),
        vec![news_item_id]
    );
    transaction
        .commit()
        .await
        .expect("transaction should commit");

    sqlx::query(
        "INSERT INTO briefing_pending_sources (user_id, source_kind, source_id) VALUES ($1, 'news', $2)",
    )
    .bind(user_id)
    .bind(news_item_id)
    .execute(&pool)
    .await
    .expect("pending source should reinsert");
    sqlx::query("UPDATE news_items SET published_at = now(), ingested_at = now() WHERE id = $1")
        .bind(news_item_id)
        .execute(&pool)
        .await
        .expect("news item should become recent");
    sqlx::query(
        r#"
        UPDATE news_item_discussions
        SET summary = '{"overview":"ready"}'::json,
            summary_status = 'completed',
            raw_comments_ref = '{"kind":"storage"}'::json,
            next_refresh_after = NULL
        WHERE news_item_id = $1
        "#,
    )
    .bind(news_item_id)
    .execute(&pool)
    .await
    .expect("recent completed discussion should update");
    let mut transaction = pool.begin().await.expect("transaction should begin");
    assert!(
        due_discussion_refresh_ids(&mut transaction, 10)
            .await
            .expect("catch-up should succeed")
            .is_empty()
    );
}
