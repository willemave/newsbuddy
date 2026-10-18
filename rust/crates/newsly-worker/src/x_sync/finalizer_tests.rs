use std::collections::BTreeMap;

use chrono::Utc;
use newsly_db::{PrepareXSyncOutcome, list_knowledge_content, prepare_x_sync};
use newsly_providers::XTweet;
use newsly_queue::QueueKernel;
use sqlx::PgPool;

use super::finalizer::XSyncFinalizer;
use super::model::{
    BookmarkFetchOutcome, DurableXCredentials, XSyncFinalizationPlan, XSyncMutation,
};
use crate::{TaskFinalizer, TaskFinalizerResult};

async fn sync_plan(pool: &PgPool) -> XSyncFinalizationPlan {
    newsly_db::run_migrations(pool).await.unwrap();
    let user_id: i64 = sqlx::query_scalar(
        "INSERT INTO users (apple_id,email,is_admin,is_active) VALUES ('x-finalizer','x-finalizer@example.test',FALSE,TRUE) RETURNING id::bigint",
    ).fetch_one(pool).await.unwrap();
    sqlx::query("INSERT INTO user_integration_connections (user_id,provider,access_token_encrypted,scopes,created_at) VALUES ($1::integer,'x','test-token','[\"bookmark.read\"]'::json,timezone('UTC',now()))")
        .bind(user_id).execute(pool).await.unwrap();
    let mut transaction = pool.begin().await.unwrap();
    let PrepareXSyncOutcome::Prepared(prepared) =
        prepare_x_sync(&mut transaction, user_id, true, Utc::now(), 15, 15)
            .await
            .unwrap()
    else {
        panic!("test X connection must prepare")
    };
    transaction.commit().await.unwrap();
    let tweet: XTweet = serde_json::from_value(serde_json::json!({
        "id":"1840000000000000000", "text":"Bookmark available before processing",
        "author_id":"test-author", "author_username":"reader", "author_name":"Reader",
        "created_at":null, "like_count":null, "retweet_count":null, "reply_count":null,
        "conversation_id":null, "in_reply_to_user_id":null,
        "referenced_tweet_types":[], "article_title":null, "article_text":null,
        "note_tweet_text":null, "external_urls":[], "linked_tweet_ids":[],
        "has_video":false, "video_duration_ms":null
    }))
    .unwrap();
    XSyncFinalizationPlan {
        task_id: 0,
        prepared: *prepared,
        mutation: XSyncMutation::Complete {
            credentials: DurableXCredentials {
                provider_user_id: Some("test-author".to_owned()),
                provider_username: Some("reader".to_owned()),
                access_token_encrypted: "test-token".to_owned(),
                refresh_token_encrypted: None,
                token_expires_at: None,
                scopes: vec!["bookmark.read".to_owned()],
            },
            bookmarks: BookmarkFetchOutcome {
                status: "synced",
                fetched: 1,
                tweets: vec![tweet],
                included_tweets: BTreeMap::new(),
                newest_item_id: Some("1840000000000000000".to_owned()),
                continuation: None,
                pending_newest_item_id: None,
                finished: true,
                failure: None,
            },
            usage: vec![],
            completed_at: Utc::now(),
        },
    }
}

async fn counts(pool: &PgPool) -> (i64, i64, i64, i64) {
    sqlx::query_as("SELECT (SELECT count(*) FROM contents), (SELECT count(*) FROM content_knowledge_saves), (SELECT count(*) FROM user_integration_synced_items), (SELECT count(*) FROM processing_tasks)")
        .fetch_one(pool).await.unwrap()
}

#[sqlx::test(migrations = false)]
async fn sync_publishes_pending_knowledge_before_processing_and_replay_is_idempotent(pool: PgPool) {
    let plan = sync_plan(&pool).await;
    let finalizer = XSyncFinalizer::new(QueueKernel::new(pool.clone()), plan.clone());
    let mut transaction = pool.begin().await.unwrap();
    assert!(matches!(
        finalizer.apply(&mut transaction).await.unwrap(),
        TaskFinalizerResult::Keep
    ));
    transaction.commit().await.unwrap();

    let page = list_knowledge_content(&pool, plan.prepared.user_id, None, None, 20)
        .await
        .unwrap();
    assert_eq!(page.items.len(), 1);
    let card = &page.items[0];
    assert_eq!(card.content.status, "new");
    assert!(card.content.is_saved_to_knowledge);
    assert!(card.saved_from_x_bookmark);
    assert_eq!(card.content.platform.as_deref(), Some("twitter"));
    assert_eq!(
        card.content.content_metadata["tweet_snapshot"]["text"],
        "Bookmark available before processing"
    );
    let before = counts(&pool).await;
    assert_eq!((before.0, before.1, before.2), (1, 1, 1));
    let analyze_count: i64 = sqlx::query_scalar(
        "SELECT count(*) FROM processing_tasks WHERE task_type='analyze_url' AND status='pending'",
    )
    .fetch_one(&pool)
    .await
    .unwrap();
    assert_eq!(
        analyze_count, 1,
        "work must remain queued when Knowledge is already visible"
    );

    let mut transaction = pool.begin().await.unwrap();
    finalizer.apply(&mut transaction).await.unwrap();
    transaction.commit().await.unwrap();
    assert_eq!(counts(&pool).await, before);
    let replay = list_knowledge_content(&pool, plan.prepared.user_id, None, None, 20)
        .await
        .unwrap();
    assert_eq!(replay.items.len(), 1);
    assert_eq!(replay.items[0].content.id, card.content.id);
    assert_eq!(replay.items[0].knowledge_saved_at, card.knowledge_saved_at);
}

#[sqlx::test(migrations = false)]
async fn interrupted_sync_publishes_fetched_bookmarks_before_retry(pool: PgPool) {
    let mut plan = sync_plan(&pool).await;
    if let XSyncMutation::Complete { bookmarks, .. } = &mut plan.mutation {
        bookmarks.finished = false;
        bookmarks.failure = Some("next page temporarily unavailable".to_owned());
        bookmarks.continuation = Some("next-page".to_owned());
        bookmarks.newest_item_id = None;
        bookmarks.pending_newest_item_id = Some("1840000000000000000".to_owned());
    }
    let finalizer = XSyncFinalizer::new(QueueKernel::new(pool.clone()), plan.clone());
    let mut transaction = pool.begin().await.unwrap();
    assert!(matches!(
        finalizer.apply(&mut transaction).await.unwrap(),
        TaskFinalizerResult::Override(result) if !result.success && result.retryable && result.error_message.as_deref() == Some("next page temporarily unavailable")
    ));
    transaction.commit().await.unwrap();
    let page = list_knowledge_content(&pool, plan.prepared.user_id, None, None, 20)
        .await
        .unwrap();
    assert_eq!(page.items.len(), 1);
    assert_eq!(page.items[0].content.status, "new");
    let before = counts(&pool).await;
    let mut transaction = pool.begin().await.unwrap();
    finalizer.apply(&mut transaction).await.unwrap();
    transaction.commit().await.unwrap();
    assert_eq!(counts(&pool).await, before);
}

#[sqlx::test(migrations = false)]
async fn rolled_back_sync_publishes_neither_knowledge_nor_processing_work(pool: PgPool) {
    let plan = sync_plan(&pool).await;
    let finalizer = XSyncFinalizer::new(QueueKernel::new(pool.clone()), plan.clone());
    let mut transaction = pool.begin().await.unwrap();
    finalizer.apply(&mut transaction).await.unwrap();
    transaction.rollback().await.unwrap();
    assert_eq!(counts(&pool).await, (0, 0, 0, 0));
    assert!(
        list_knowledge_content(&pool, plan.prepared.user_id, None, None, 20)
            .await
            .unwrap()
            .items
            .is_empty()
    );
}

#[sqlx::test(migrations = false)]
async fn replaced_connection_discards_obsolete_sync_without_saving_or_enqueueing(pool: PgPool) {
    let plan = sync_plan(&pool).await;
    sqlx::query("UPDATE user_integration_connections SET access_token_encrypted='replacement-token' WHERE id::bigint=$1")
        .bind(plan.prepared.connection_id).execute(&pool).await.unwrap();
    let finalizer = XSyncFinalizer::new(QueueKernel::new(pool.clone()), plan.clone());
    let mut transaction = pool.begin().await.unwrap();
    assert!(matches!(
        finalizer.apply(&mut transaction).await.unwrap(),
        TaskFinalizerResult::Override(_)
    ));
    transaction.commit().await.unwrap();
    assert_eq!(counts(&pool).await, (0, 0, 0, 0));
    assert!(
        list_knowledge_content(&pool, plan.prepared.user_id, None, None, 20)
            .await
            .unwrap()
            .items
            .is_empty()
    );
}
