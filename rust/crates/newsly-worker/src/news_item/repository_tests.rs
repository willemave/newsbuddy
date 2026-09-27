use super::*;
use chrono::Utc;
use sqlx::PgPool;

#[test]
fn denied_fallback_reason_is_persisted_for_metadata_only_processing() {
    let snapshot = NewsSnapshot {
        id: 42,
        owner_user_id: None,
        visibility_scope: "global".to_owned(),
        platform: Some("hackernews".to_owned()),
        source_type: None,
        source_label: None,
        source_external_id: None,
        canonical_item_url: None,
        canonical_story_url: None,
        article_url: Some("https://example.com/story".to_owned()),
        article_domain: Some("example.com".to_owned()),
        discussion_url: None,
        summary_key_points: Vec::new(),
        summary_text: None,
        raw_metadata: json!({}),
        ingested_at: Utc::now(),
        body_source: BodySource::None,
        fingerprint: "fixture".to_owned(),
    };
    let mutation = EnrichmentMutation::Failed {
        article_url: snapshot.article_url.clone(),
        final_url: snapshot.article_url.clone(),
        strategy: Some("metadata_only".to_owned()),
        error_code: "fallback_not_permitted".to_owned(),
        reason: "not visible to an active subscriber".to_owned(),
    };
    let mut metadata = Map::new();

    apply_enrichment_metadata(&mut metadata, &snapshot, &mutation, Utc::now());

    assert_eq!(metadata["article_extraction"]["strategy"], "metadata_only");
    assert_eq!(
        metadata["article_extraction"]["error_code"],
        "fallback_not_permitted"
    );
    assert_eq!(
        metadata["article_extraction"]["error"],
        "not visible to an active subscriber"
    );
}

async fn prepared_firecrawl_policy(pool: &PgPool, news_item_id: i64) -> bool {
    let mut transaction = pool.begin().await.unwrap();
    let preparation = prepare_enrichment(&mut transaction, news_item_id)
        .await
        .unwrap();
    transaction.rollback().await.unwrap();
    match preparation {
        EnrichmentPreparation::Extract {
            allow_firecrawl, ..
        }
        | EnrichmentPreparation::Content {
            allow_firecrawl, ..
        } => allow_firecrawl,
        other => panic!("expected extractable news item, got {other:?}"),
    }
}

#[sqlx::test]
async fn paid_fallback_requires_an_active_subscriber_for_global_aggregators(pool: PgPool) {
    newsly_db::run_migrations(&pool).await.unwrap();
    let global_id: i64 = sqlx::query_scalar(
        r#"
        INSERT INTO news_items (
            ingest_key, visibility_scope, platform, article_url, raw_metadata,
            ingested_at, created_at
        )
        VALUES (
            'firecrawl-global', 'global', 'hackernews', 'https://example.com/global',
            '{"excerpt":"Aggregator metadata remains usable"}', now(), now()
        )
        RETURNING id::bigint
        "#,
    )
    .fetch_one(&pool)
    .await
    .unwrap();
    let user_scoped_id: i64 = sqlx::query_scalar(
        r#"
        INSERT INTO news_items (
            ingest_key, visibility_scope, platform, article_url, raw_metadata,
            ingested_at, created_at
        )
        VALUES (
            'firecrawl-user', 'user', 'hackernews', 'https://example.com/user',
            '{}', now(), now()
        )
        RETURNING id::bigint
        "#,
    )
    .fetch_one(&pool)
    .await
    .unwrap();

    assert!(!prepared_firecrawl_policy(&pool, global_id).await);
    assert!(prepared_firecrawl_policy(&pool, user_scoped_id).await);

    let user_id: i64 = sqlx::query_scalar(
        r#"
        INSERT INTO users (apple_id, email, is_admin, is_active)
        VALUES ('firecrawl-gate', 'firecrawl-gate@example.test', false, false)
        RETURNING id::bigint
        "#,
    )
    .fetch_one(&pool)
    .await
    .unwrap();
    sqlx::query(
        r#"
        INSERT INTO user_scraper_configs (
            user_id, scraper_type, feed_url, config, is_active
        )
        VALUES (
            $1::bigint::integer, 'aggregator', 'aggregator://hackernews',
            '{"key":"hackernews"}', true
        )
        "#,
    )
    .bind(user_id)
    .execute(&pool)
    .await
    .unwrap();

    assert!(!prepared_firecrawl_policy(&pool, global_id).await);
    sqlx::query("UPDATE users SET is_active = true WHERE id::bigint = $1")
        .bind(user_id)
        .execute(&pool)
        .await
        .unwrap();
    assert!(prepared_firecrawl_policy(&pool, global_id).await);
}

#[sqlx::test]
async fn brutalist_topic_selection_is_part_of_subscriber_visibility(pool: PgPool) {
    newsly_db::run_migrations(&pool).await.unwrap();
    let user_id: i64 = sqlx::query_scalar(
        r#"
        INSERT INTO users (apple_id, email, is_admin, is_active)
        VALUES ('firecrawl-topic', 'firecrawl-topic@example.test', false, true)
        RETURNING id::bigint
        "#,
    )
    .fetch_one(&pool)
    .await
    .unwrap();
    sqlx::query(
        r#"
        INSERT INTO user_scraper_configs (
            user_id, scraper_type, feed_url, config, is_active
        )
        VALUES (
            $1::bigint::integer, 'aggregator', 'aggregator://brutalist',
            '{"key":"brutalist","topics":["sports"]}', true
        )
        "#,
    )
    .bind(user_id)
    .execute(&pool)
    .await
    .unwrap();
    let news_item_id: i64 = sqlx::query_scalar(
        r#"
        INSERT INTO news_items (
            ingest_key, visibility_scope, platform, article_url, raw_metadata,
            ingested_at, created_at
        )
        VALUES (
            'firecrawl-topic', 'global', 'brutalist', 'https://example.com/topic',
            '{"aggregator":{"topic":"science"}}', now(), now()
        )
        RETURNING id::bigint
        "#,
    )
    .fetch_one(&pool)
    .await
    .unwrap();

    assert!(!prepared_firecrawl_policy(&pool, news_item_id).await);
    sqlx::query(
        r#"
        UPDATE user_scraper_configs
        SET config = '{"key":"brutalist","topics":["science"]}'
        WHERE user_id::bigint = $1
        "#,
    )
    .bind(user_id)
    .execute(&pool)
    .await
    .unwrap();
    assert!(prepared_firecrawl_policy(&pool, news_item_id).await);
}
