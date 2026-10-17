//! Shared transactional aggregator audience transitions for HTTP and chat.

use std::collections::{BTreeMap, BTreeSet};

use anyhow::Result;
use newsly_queue::{EnqueueRequest, QueueKernel, TaskType};
use serde_json::{Value, json};
use sqlx::{Postgres, Transaction};

pub type Demand = BTreeMap<String, BTreeSet<String>>;

pub async fn snapshot(transaction: &mut Transaction<'_, Postgres>) -> Result<Demand> {
    newsly_db::aggregator_corpus::lock_aggregator_demand(transaction).await?;
    Ok(newsly_db::brutalist::active_demand(transaction).await?)
}

/// A new audience can require a topic fetch and can use previously skipped article fallback.
pub async fn enqueue_new_demand(
    transaction: &mut Transaction<'_, Postgres>,
    queue: &QueueKernel,
    before: &Demand,
) -> Result<()> {
    let after = snapshot(transaction).await?;
    let mut requests = Vec::new();
    for (key, topics) in &after {
        let was_subscribed = before.contains_key(key);
        let new_topics = topics
            .difference(before.get(key).unwrap_or(&BTreeSet::new()))
            .cloned()
            .collect::<Vec<_>>();
        if key == "brutalist" && !new_topics.is_empty() {
            let mut scrape = EnqueueRequest::new(TaskType::Scrape);
            scrape.payload = json!({"sources": [key]}).as_object().cloned();
            // The audience lock serializes this actual transition. Do not absorb a
            // remove/re-add into an older task that prepared during the no-demand gap.
            scrape.dedupe = Some(false);
            scrape.priority = 5;
            requests.push(scrape);
        }
        if was_subscribed && new_topics.is_empty() {
            continue;
        }
        let ids =
            newsly_db::brutalist::metadata_only_news_for_new_demand(transaction, key, &new_topics)
                .await?;
        for id in ids {
            let mut enrich = EnqueueRequest::new(TaskType::EnrichNewsItemArticle);
            enrich.payload = Some(serde_json::Map::from_iter([(
                "news_item_id".to_owned(),
                Value::from(id),
            )]));
            enrich.dedupe = Some(true);
            enrich.dedupe_key = Some(format!("enrich-news-demand:{id}"));
            requests.push(enrich);
        }
    }
    if !requests.is_empty() {
        queue
            .enqueue_many_in_transaction(transaction, requests)
            .await?;
    }
    Ok(())
}

/// Chat subscriptions use the same audience fence and durable work as HTTP writes.
pub async fn subscribe_aggregator(
    pool: &sqlx::PgPool,
    queue: &QueueKernel,
    user_id: i64,
    key: newsly_domain::AggregatorKey,
    topics: Option<&[&str]>,
) -> Result<newsly_db::AppliedFeedSubscription> {
    let mut transaction = pool.begin().await?;
    let before = snapshot(&mut transaction).await?;
    let applied =
        newsly_db::apply_aggregator_subscription(&mut transaction, user_id, key, topics).await?;
    enqueue_new_demand(&mut transaction, queue, &before).await?;
    transaction.commit().await?;
    Ok(applied)
}

#[cfg(test)]
mod tests {
    use super::*;
    use sqlx::PgPool;

    #[sqlx::test(migrations = false)]
    async fn first_brutalist_topic_queues_fetch_and_recent_reenrichment(pool: PgPool) {
        newsly_db::run_migrations(&pool).await.unwrap();
        let mut tx = pool.begin().await.unwrap();
        let before = snapshot(&mut tx).await.unwrap();
        let user_id: i64 = sqlx::query_scalar("INSERT INTO users(apple_id,email,is_active,is_admin) VALUES('demand-transition','demand-transition@example.com',true,false) RETURNING id::bigint")
            .fetch_one(&mut *tx).await.unwrap();
        sqlx::query("INSERT INTO user_scraper_configs(user_id,scraper_type,feed_url,config,is_active) VALUES($1::bigint::integer,'aggregator','aggregator://brutalist','{\"key\":\"brutalist\",\"topics\":[\"science\"]}',true)")
            .bind(user_id).execute(&mut *tx).await.unwrap();
        let item_id: i64 = sqlx::query_scalar("INSERT INTO news_items(ingest_key,visibility_scope,platform,status,raw_metadata,ingested_at,created_at) VALUES('demand-item','global','brutalist','ready',$1,timezone('UTC',now()),timezone('UTC',now())) RETURNING id::bigint")
            .bind(json!({"aggregator":{"topic":"science","topics":["science"]},"article_extraction":{"error_code":"fallback_not_permitted"}}))
            .fetch_one(&mut *tx).await.unwrap();
        enqueue_new_demand(&mut tx, &QueueKernel::new(pool.clone()), &before)
            .await
            .unwrap();
        let tasks: Vec<(String, Value)> =
            sqlx::query_as("SELECT task_type, payload::jsonb FROM processing_tasks ORDER BY id")
                .fetch_all(&mut *tx)
                .await
                .unwrap();
        assert!(
            tasks
                .iter()
                .any(|(kind, payload)| kind == "scrape"
                    && payload["sources"] == json!(["brutalist"]))
        );
        assert!(
            tasks
                .iter()
                .any(|(kind, payload)| kind == "enrich_news_item_article"
                    && payload["news_item_id"] == item_id)
        );
        tx.rollback().await.unwrap();
    }
}

#[cfg(test)]
mod transitions {
    use super::*;
    use sqlx::PgPool;

    #[sqlx::test(migrations = false)]
    async fn added_topic_is_not_lost_behind_an_existing_fetch_and_reactivation_replays(
        pool: PgPool,
    ) {
        newsly_db::run_migrations(&pool).await.unwrap();
        let user_id:i64=sqlx::query_scalar("INSERT INTO users(apple_id,email,is_active,is_admin) VALUES('demand-reactivation','demand-reactivation@example.com',true,false) RETURNING id::bigint").fetch_one(&pool).await.unwrap();
        let mut tx = pool.begin().await.unwrap();
        let before = snapshot(&mut tx).await.unwrap();
        let config_id:i64=sqlx::query_scalar("INSERT INTO user_scraper_configs(user_id,scraper_type,config,is_active) VALUES($1,'aggregator',$2,true) RETURNING id::bigint").bind(user_id).bind(json!({"key":"brutalist","topics":["science"]})).fetch_one(&mut *tx).await.unwrap();
        let queue = QueueKernel::new(pool.clone());
        enqueue_new_demand(&mut tx, &queue, &before).await.unwrap();
        let before = snapshot(&mut tx).await.unwrap();
        sqlx::query("UPDATE user_scraper_configs SET config=$2 WHERE id::bigint=$1")
            .bind(config_id)
            .bind(json!({"key":"brutalist","topics":["science","politics"]}))
            .execute(&mut *tx)
            .await
            .unwrap();
        enqueue_new_demand(&mut tx, &queue, &before).await.unwrap();
        let count: i64 =
            sqlx::query_scalar("SELECT count(*) FROM processing_tasks WHERE task_type='scrape'")
                .fetch_one(&mut *tx)
                .await
                .unwrap();
        assert_eq!(
            count, 2,
            "a prior topic snapshot cannot absorb the new topic"
        );
        let before = snapshot(&mut tx).await.unwrap();
        enqueue_new_demand(&mut tx, &queue, &before).await.unwrap();
        let unchanged: i64 =
            sqlx::query_scalar("SELECT count(*) FROM processing_tasks WHERE task_type='scrape'")
                .fetch_one(&mut *tx)
                .await
                .unwrap();
        assert_eq!(unchanged, 2);
        sqlx::query("UPDATE user_scraper_configs SET is_active=false WHERE id::bigint=$1")
            .bind(config_id)
            .execute(&mut *tx)
            .await
            .unwrap();
        let before = snapshot(&mut tx).await.unwrap();
        sqlx::query("UPDATE user_scraper_configs SET is_active=true WHERE id::bigint=$1")
            .bind(config_id)
            .execute(&mut *tx)
            .await
            .unwrap();
        // Reactivation is another real transition; an older prepared task cannot absorb it.
        enqueue_new_demand(&mut tx, &queue, &before).await.unwrap();
        let reactivated: i64 =
            sqlx::query_scalar("SELECT count(*) FROM processing_tasks WHERE task_type='scrape'")
                .fetch_one(&mut *tx)
                .await
                .unwrap();
        assert_eq!(reactivated, 3);
        tx.rollback().await.unwrap();
    }
}

#[cfg(test)]
mod chat_tests {
    use super::*;
    use newsly_domain::AggregatorKey;
    use sqlx::PgPool;

    #[sqlx::test(migrations = false)]
    async fn chat_create_topic_update_and_reactivation_publish_demand_work(pool: PgPool) {
        newsly_db::run_migrations(&pool).await.unwrap();
        let user_id:i64=sqlx::query_scalar("INSERT INTO users(apple_id,email,is_active,is_admin) VALUES('chat-demand','chat-demand@example.com',true,false) RETURNING id::bigint").fetch_one(&pool).await.unwrap();
        let item_id:i64=sqlx::query_scalar("INSERT INTO news_items(ingest_key,visibility_scope,platform,status,raw_metadata,ingested_at,created_at) VALUES('chat-denied','global','brutalist','ready',$1,timezone('UTC',now()),timezone('UTC',now())) RETURNING id::bigint").bind(json!({"aggregator":{"topic":"science","topics":["science"]},"article_extraction":{"error_code":newsly_db::aggregator_corpus::FIRECRAWL_NO_DEMAND_ERROR_CODE}})).fetch_one(&pool).await.unwrap();
        let queue = QueueKernel::new(pool.clone());
        let created = subscribe_aggregator(
            &pool,
            &queue,
            user_id,
            AggregatorKey::Brutalist,
            Some(&["science"]),
        )
        .await
        .unwrap();
        assert_eq!(created.mutation.as_str(), "created");
        let replay:i64=sqlx::query_scalar("SELECT count(*) FROM processing_tasks WHERE task_type='enrich_news_item_article' AND payload::jsonb->>'news_item_id'=$1").bind(item_id.to_string()).fetch_one(&pool).await.unwrap();
        assert_eq!(replay, 1);
        subscribe_aggregator(
            &pool,
            &queue,
            user_id,
            AggregatorKey::Brutalist,
            Some(&["science", "politics"]),
        )
        .await
        .unwrap();
        let fetches: i64 =
            sqlx::query_scalar("SELECT count(*) FROM processing_tasks WHERE task_type='scrape'")
                .fetch_one(&pool)
                .await
                .unwrap();
        assert_eq!(
            fetches, 2,
            "chat topic expansion needs a successor even behind an active earlier snapshot"
        );
        sqlx::query("UPDATE user_scraper_configs SET is_active=false WHERE id::bigint=$1")
            .bind(created.config.id)
            .execute(&pool)
            .await
            .unwrap();
        sqlx::query(
            "UPDATE processing_tasks SET status='completed',completed_at=timezone('UTC',now())",
        )
        .execute(&pool)
        .await
        .unwrap();
        let reactivated =
            subscribe_aggregator(&pool, &queue, user_id, AggregatorKey::Brutalist, None)
                .await
                .unwrap();
        assert_eq!(reactivated.mutation.as_str(), "reactivated");
        let pending: i64 = sqlx::query_scalar(
            "SELECT count(*) FROM processing_tasks WHERE task_type='scrape' AND status='pending'",
        )
        .fetch_one(&pool)
        .await
        .unwrap();
        assert_eq!(pending, 1);
    }
}
