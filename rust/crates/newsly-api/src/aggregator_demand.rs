use std::collections::{BTreeMap, BTreeSet};

use anyhow::Result;
use newsly_queue::{EnqueueRequest, QueueKernel, TaskType};
use serde_json::{Value, json};
use sqlx::{Postgres, Transaction};

pub(super) type Demand = BTreeMap<String, BTreeSet<String>>;

pub(super) async fn snapshot(transaction: &mut Transaction<'_, Postgres>) -> Result<Demand> {
    Ok(newsly_db::brutalist::active_demand(transaction).await?)
}

/// A new audience can require a topic fetch and can use previously skipped article fallback.
pub(super) async fn enqueue_new_demand(
    transaction: &mut Transaction<'static, Postgres>,
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
            scrape.dedupe = Some(true);
            scrape.dedupe_key = Some("scrape:aggregator:brutalist:new-demand".to_owned());
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
