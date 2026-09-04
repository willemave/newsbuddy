use serde_json::json;
use sqlx::PgPool;

use super::{ScrapedNewsRecord, news_ingest_key, persist_scraped_news};

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
