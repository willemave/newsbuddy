use super::*;

use sqlx::PgPool;

fn source(platform: Option<&str>, url: Option<&str>, external_id: Option<&str>) -> NewsItemSource {
    NewsItemSource {
        id: 1,
        owner_user_id: None,
        platform: platform.map(str::to_owned),
        source_external_id: external_id.map(str::to_owned),
        canonical_item_url: None,
        discussion_url: url.map(str::to_owned),
        raw_metadata: json!({}),
        merge_refresh_deadline: Utc::now().naive_utc() + Duration::hours(24),
    }
}

#[test]
fn normalizes_hn_alias_and_extracts_identity() {
    let identity = resolve_identity(&source(
        Some("HN"),
        Some("http://news.ycombinator.com/item?id=123"),
        None,
    ))
    .unwrap();
    assert_eq!(identity.platform, "hackernews");
    assert_eq!(identity.external_id, "123");
    assert!(identity.discussion_url.starts_with("https://"));
}

#[test]
fn infers_and_canonicalizes_reddit() {
    let identity = resolve_identity(&source(
        None,
        Some("https://old.reddit.com/r/rust/comments/AbC123/example"),
        None,
    ))
    .unwrap();
    assert_eq!(identity.platform, "reddit");
    assert_eq!(identity.external_id, "abc123");
    assert_eq!(
        Url::parse(&identity.discussion_url).unwrap().host_str(),
        Some("www.reddit.com")
    );
}

#[test]
fn rejects_unrelated_discussion_source() {
    assert!(resolve_identity(&source(None, Some("https://example.com/thread"), None)).is_none());
}

#[sqlx::test]
async fn summary_demand_distinguishes_open_from_unread_briefing_use(pool: PgPool) {
    newsly_db::run_migrations(&pool)
        .await
        .expect("migrations should run");
    let user_id = sqlx::query_scalar::<_, i64>(
        r"
            INSERT INTO users (apple_id, email, is_admin, is_active)
            VALUES ('worker-discussion-demand', 'worker-discussion-demand@example.com', false, true)
            RETURNING id::bigint
            ",
    )
    .fetch_one(&pool)
    .await
    .expect("user should insert");
    let news_item_id = sqlx::query_scalar::<_, i64>(
        r"
            INSERT INTO news_items (
                ingest_key, visibility_scope, owner_user_id, platform, source_type,
                source_external_id, canonical_item_url, discussion_url, status,
                ingested_at, cluster_size, created_at, updated_at
            )
            VALUES (
                'worker-discussion-demand-item', 'user', $1::bigint::integer,
                'reddit', 'Reddit', 'abc123',
                'https://www.reddit.com/r/rust/comments/abc123/example',
                'https://www.reddit.com/r/rust/comments/abc123/example',
                'ready', now(), 1, now(), now()
            )
            RETURNING id::bigint
            ",
    )
    .bind(user_id)
    .fetch_one(&pool)
    .await
    .expect("news item should insert");

    let mut transaction = pool.begin().await.expect("transaction should begin");
    assert!(
        !discussion_summary_is_demanded(&mut transaction, news_item_id, true)
            .await
            .expect("initial demand should load")
    );
    transaction
        .rollback()
        .await
        .expect("transaction should roll back");

    sqlx::query(
            "INSERT INTO news_item_read_status (user_id, news_item_id, read_at, created_at) VALUES ($1, $2, now(), now())",
        )
        .bind(user_id)
        .bind(news_item_id)
        .execute(&pool)
        .await
        .expect("open should insert");
    let mut transaction = pool.begin().await.expect("transaction should begin");
    assert!(
        discussion_summary_is_demanded(&mut transaction, news_item_id, true)
            .await
            .expect("initial demand should load")
    );
    assert!(
        !discussion_summary_is_demanded(&mut transaction, news_item_id, false)
            .await
            .expect("refresh demand should load")
    );
    transaction
        .rollback()
        .await
        .expect("transaction should roll back");

    sqlx::query(
            "INSERT INTO briefing_pending_sources (user_id, source_kind, source_id) VALUES ($1, 'news', $2)",
        )
        .bind(user_id)
        .bind(news_item_id)
        .execute(&pool)
        .await
        .expect("pending source should insert");
    sqlx::query("DELETE FROM news_item_read_status WHERE user_id = $1 AND news_item_id = $2")
        .bind(user_id)
        .bind(news_item_id)
        .execute(&pool)
        .await
        .expect("read state should delete");
    let mut transaction = pool.begin().await.expect("transaction should begin");
    assert!(
        discussion_summary_is_demanded(&mut transaction, news_item_id, false)
            .await
            .expect("refresh demand should load")
    );
}
