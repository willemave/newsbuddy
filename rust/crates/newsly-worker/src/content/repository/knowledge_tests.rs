use super::*;
use crate::content::model::ExtractedArticle;

#[sqlx::test(migrations = false)]
async fn article_redirect_preserves_knowledge_and_x_provenance_without_duplicate_work(
    pool: PgPool,
) {
    newsly_db::run_migrations(&pool).await.unwrap();
    let user_id: i64 = sqlx::query_scalar(
        "INSERT INTO users (apple_id, email, is_admin, is_active) VALUES ('knowledge-redirect', 'knowledge-redirect@example.test', FALSE, TRUE) RETURNING id::bigint",
    )
    .fetch_one(&pool)
    .await
    .unwrap();
    let winner: i64 = sqlx::query_scalar(
        "INSERT INTO contents (content_type,url,title,status,content_metadata,is_aggregate) VALUES ('article','https://example.com/canonical','Ready article','completed','{}',FALSE) RETURNING id::bigint",
    )
    .fetch_one(&pool)
    .await
    .unwrap();
    let alias: i64 = sqlx::query_scalar(
        "INSERT INTO contents (content_type,url,status,content_metadata,is_aggregate) VALUES ('article','https://example.com/shared','processing',$1,FALSE) RETURNING id::bigint",
    )
    .bind(json!({"processing": {"submitted_by_user_id": user_id, "submitted_via": "share_sheet"}}))
    .fetch_one(&pool)
    .await
    .unwrap();
    // A URL can be saved both from the share sheet and from an X bookmark.
    let connection_id: i64 = sqlx::query_scalar(
        "INSERT INTO user_integration_connections (user_id,provider,created_at) VALUES ($1,'x',timezone('UTC',now())) RETURNING id::bigint",
    )
    .bind(user_id)
    .fetch_one(&pool)
    .await
    .unwrap();
    let mut transaction = pool.begin().await.unwrap();
    newsly_db::save_x_bookmark_destination(&mut transaction, user_id, alias)
        .await
        .unwrap();
    newsly_db::upsert_x_bookmark_ledger(
        &mut transaction,
        connection_id,
        "123456789",
        alias,
        "https://x.com/i/status/123456789",
        Utc::now(),
    )
    .await
    .unwrap();
    sqlx::query("INSERT INTO content_read_status (user_id,content_id,read_at,created_at) VALUES ($1,$2,timezone('UTC',now()),timezone('UTC',now()))")
        .bind(user_id).bind(alias).execute(&mut *transaction).await.unwrap();
    transaction.commit().await.unwrap();
    let before = newsly_db::list_knowledge_content(&pool, user_id, None, None, 25)
        .await
        .unwrap();
    assert_eq!(before.items.len(), 1);
    assert_eq!(before.items[0].content.id, alias);
    assert_eq!(before.items[0].content.status, "processing");
    assert!(before.items[0].saved_from_x_bookmark);

    let finalizer = ContentFinalizer::new(
        QueueKernel::new(pool.clone()),
        ContentFinalizationPlan {
            task_id: 1,
            content_id: alias,
            usage: vec![],
            mutation: ContentMutation::ProcessArticle {
                article: ExtractedArticle {
                    original_url: "https://example.com/shared".to_owned(),
                    final_url: "https://example.com/canonical".to_owned(),
                    title: "Fetched alias title".to_owned(),
                    author: None,
                    published_at: None,
                    body: "Fetched source text".to_owned(),
                    feed_candidates: vec![],
                    extraction_method: "fixture".to_owned(),
                    warnings: vec![],
                    timings: vec![],
                    used_firecrawl: false,
                },
                body: StagedContentBody {
                    storage_provider: "local",
                    storage_key: "fixture/source.txt".to_owned(),
                    content_format: "text",
                    sha256: "a".repeat(64),
                    byte_size: 19,
                    char_count: 19,
                },
                subscribe_to_feed: false,
            },
        },
    );
    // A rolled-back finalize must not move the save or provenance.
    let mut transaction = pool.begin().await.unwrap();
    finalizer.apply_inner(&mut transaction).await.unwrap();
    transaction.rollback().await.unwrap();
    let rolled_back = newsly_db::list_knowledge_content(&pool, user_id, None, None, 25)
        .await
        .unwrap();
    assert_eq!(rolled_back.items[0].content.id, alias);

    for _ in 0..2 {
        let mut transaction = pool.begin().await.unwrap();
        finalizer.apply_inner(&mut transaction).await.unwrap();
        transaction.commit().await.unwrap();
    }
    let after = newsly_db::list_knowledge_content(&pool, user_id, None, None, 25)
        .await
        .unwrap();
    assert_eq!(after.items.len(), 1);
    assert_eq!(after.items[0].content.id, winner);
    assert_eq!(after.items[0].content.status, "completed");
    assert_eq!(
        after.items[0].content.title.as_deref(),
        Some("Ready article")
    );
    assert!(after.items[0].saved_from_x_bookmark);
    assert!(after.items[0].content.is_read);
    assert_eq!(
        after.items[0].knowledge_saved_at,
        before.items[0].knowledge_saved_at
    );
    let (status, metadata): (String, Value) =
        sqlx::query_as("SELECT status,content_metadata FROM contents WHERE id::bigint=$1")
            .bind(alias)
            .fetch_one(&pool)
            .await
            .unwrap();
    assert_eq!(status, "skipped");
    assert_eq!(
        runtime_value(metadata.as_object().unwrap(), "canonical_content_id"),
        Some(&json!(winner))
    );
    let tasks: i64 = sqlx::query_scalar("SELECT count(*) FROM processing_tasks")
        .fetch_one(&pool)
        .await
        .unwrap();
    assert_eq!(
        tasks, 0,
        "canonical reuse must not queue duplicate processing or X sync"
    );

    // Canonical reuse must still honor a separately requested feed subscription.
    let subscription_alias: i64 = sqlx::query_scalar(
        "INSERT INTO contents (content_type,url,status,content_metadata,is_aggregate) VALUES ('article','https://example.com/subscribe','processing',$1,FALSE) RETURNING id::bigint",
    )
    .bind(json!({"processing": {"submitted_by_user_id": user_id}}))
    .fetch_one(&pool).await.unwrap();
    let mut subscription_finalizer = finalizer.clone();
    subscription_finalizer.plan.content_id = subscription_alias;
    let ContentMutation::ProcessArticle {
        article,
        subscribe_to_feed,
        ..
    } = &mut subscription_finalizer.plan.mutation
    else {
        unreachable!()
    };
    *subscribe_to_feed = true;
    article.feed_candidates.push(FeedCandidate {
        url: "https://example.com/feed.xml".to_owned(),
        feed_type: "rss".to_owned(),
        title: Some("Example feed".to_owned()),
    });
    let mut transaction = pool.begin().await.unwrap();
    subscription_finalizer
        .apply_inner(&mut transaction)
        .await
        .unwrap();
    transaction.commit().await.unwrap();
    let subscribed: bool = sqlx::query_scalar(
        "SELECT EXISTS (SELECT 1 FROM user_scraper_configs WHERE user_id::bigint=$1 AND feed_url='https://example.com/feed.xml' AND is_active)",
    ).bind(user_id).fetch_one(&pool).await.unwrap();
    assert!(subscribed);

    for status in ["failed", "skipped"] {
        assert_unusable_duplicate_keeps_extracted_source(
            &pool, user_id, winner, &finalizer, status,
        )
        .await;
    }
}

async fn assert_unusable_duplicate_keeps_extracted_source(
    pool: &PgPool,
    user_id: i64,
    winner: i64,
    finalizer: &ContentFinalizer,
    winner_status: &str,
) {
    sqlx::query("UPDATE contents SET status=$2 WHERE id::bigint=$1")
        .bind(winner)
        .bind(winner_status)
        .execute(pool)
        .await
        .unwrap();
    let submitted_url = format!("https://example.com/retry-{winner_status}");
    let retry_alias: i64 = sqlx::query_scalar(
        "INSERT INTO contents (content_type,url,status,content_metadata,is_aggregate) VALUES ('article',$1,'processing',$2,FALSE) RETURNING id::bigint",
    )
    .bind(&submitted_url)
    .bind(json!({"processing": {"submitted_by_user_id": user_id}}))
    .fetch_one(pool).await.unwrap();
    let mut retry_finalizer = finalizer.clone();
    retry_finalizer.plan.content_id = retry_alias;
    let mut transaction = pool.begin().await.unwrap();
    newsly_db::save_x_bookmark_destination(&mut transaction, user_id, retry_alias)
        .await
        .unwrap();
    retry_finalizer.apply_inner(&mut transaction).await.unwrap();
    transaction.commit().await.unwrap();
    let after = newsly_db::list_knowledge_content(pool, user_id, None, None, 25)
        .await
        .unwrap();
    let saved = after
        .items
        .iter()
        .find(|item| item.content.id == retry_alias)
        .expect("new successful extraction must remain in Knowledge");
    assert_eq!(saved.content.status, "processing");
    assert_eq!(saved.content.url, submitted_url);
    assert!(saved.content.body_available);
    assert!(
        runtime_value(
            saved.content.content_metadata.as_object().unwrap(),
            "canonical_content_id"
        )
        .is_none()
    );
    let summaries: i64 = sqlx::query_scalar(
        "SELECT count(*) FROM processing_tasks WHERE content_id::bigint=$1 AND task_type='summarize'",
    ).bind(retry_alias).fetch_one(pool).await.unwrap();
    assert_eq!(summaries, 1);
}
