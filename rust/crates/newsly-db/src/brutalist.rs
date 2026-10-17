use std::collections::{BTreeMap, BTreeSet};

use serde_json::Value;
use sqlx::{PgConnection, Postgres, Transaction};

use newsly_domain::{AGGREGATOR_KEY_NAMES, AggregatorKey};

pub async fn active_demand(
    connection: &mut PgConnection,
) -> Result<BTreeMap<String, BTreeSet<String>>, sqlx::Error> {
    let configs: Vec<Value> = sqlx::query_scalar(
        r"
        SELECT config.config::jsonb
        FROM user_scraper_configs config
        JOIN users owner ON owner.id = config.user_id
        WHERE config.is_active AND owner.is_active
          AND config.scraper_type = 'aggregator'
          AND lower(btrim(config.config::jsonb ->> 'key')) = ANY($1::text[])
        ",
    )
    .bind(&AGGREGATOR_KEY_NAMES[..])
    .fetch_all(connection)
    .await?;
    let mut demand = BTreeMap::<String, BTreeSet<String>>::new();
    for config in configs {
        let Some(key) = config
            .get("key")
            .and_then(Value::as_str)
            .map(|key| key.trim().to_lowercase())
        else {
            continue;
        };
        let demanded = demand.entry(key.clone()).or_default();
        let Some(catalog) = AggregatorKey::from_key(&key) else {
            continue;
        };
        let topics = catalog.topics();
        if topics.is_empty() {
            continue;
        }
        let selected = config.get("topics").and_then(Value::as_array);
        if selected.is_none_or(Vec::is_empty) {
            demanded.extend(topics.iter().copied().map(str::to_owned));
            continue;
        }
        for topic in selected.into_iter().flatten().filter_map(Value::as_str) {
            if let Some(topic) = catalog.canonical_topic(topic) {
                demanded.insert(topic.to_owned());
            }
        }
    }
    Ok(demand)
}

/// Snapshot the topics visible to active subscribers before a global fetch.
pub async fn demanded_topics(connection: &mut PgConnection) -> Result<Vec<String>, sqlx::Error> {
    Ok(active_demand(connection)
        .await?
        .remove("brutalist")
        .unwrap_or_default()
        .into_iter()
        .collect())
}

pub const DAILY_SECTION_LIMIT: i64 = 12;

pub async fn recent_section_counts(
    transaction: &mut Transaction<'_, Postgres>,
) -> Result<Vec<(String, String, i64)>, sqlx::Error> {
    sqlx::query_as(
        r"
        SELECT topic.value, COALESCE(item.raw_metadata::jsonb #>> '{aggregator,metadata,source_name}', ''), count(*)::bigint
        FROM news_items item
        CROSS JOIN LATERAL jsonb_array_elements_text(
          CASE WHEN jsonb_typeof(item.raw_metadata::jsonb #> '{aggregator,topics}') = 'array'
            THEN item.raw_metadata::jsonb #> '{aggregator,topics}'
            ELSE jsonb_build_array(item.raw_metadata::jsonb #>> '{aggregator,topic}') END
        ) topic(value)
        WHERE item.platform = 'brutalist'
          AND item.ingested_at >= timezone('UTC', now()) - interval '24 hours'
        GROUP BY topic.value, item.raw_metadata::jsonb #>> '{aggregator,metadata,source_name}'
        ",
    )
    .fetch_all(&mut **transaction)
    .await
}

pub async fn metadata_only_news_for_new_demand(
    transaction: &mut Transaction<'_, Postgres>,
    key: &str,
    new_topics: &[String],
) -> Result<Vec<i64>, sqlx::Error> {
    sqlx::query_scalar(
        r"
        SELECT item.id::bigint
        FROM news_items item
        WHERE item.visibility_scope='global' AND lower(item.platform)=$1
          AND item.ingested_at >= timezone('UTC',now()) - interval '24 hours'
          AND item.raw_metadata::jsonb #>> '{article_extraction,error_code}' = $3
          AND aggregator_config_admits(jsonb_build_object('key',$1,'topics',to_jsonb($2::text[])), item.platform, item.raw_metadata)
        ORDER BY item.ingested_at DESC, item.id DESC
        LIMIT 100
        ",
    )
    .bind(key)
    .bind(new_topics)
    .bind(crate::aggregator_corpus::FIRECRAWL_NO_DEMAND_ERROR_CODE)
    .fetch_all(&mut **transaction)
    .await
}

#[cfg(test)]
mod tests {
    use super::*;
    use sqlx::PgPool;

    #[sqlx::test(migrations = false)]
    async fn demand_and_rolling_section_counts_use_active_subscriptions(pool: PgPool) {
        crate::run_migrations(&pool).await.unwrap();
        let user_id: i64 = sqlx::query_scalar("INSERT INTO users(apple_id,email,is_active,is_admin) VALUES('brutalist-demand','brutalist-demand@example.com',true,false) RETURNING id::bigint")
            .fetch_one(&pool).await.unwrap();
        let mut connection = pool.acquire().await.unwrap();
        assert!(demanded_topics(&mut connection).await.unwrap().is_empty());
        sqlx::query("INSERT INTO user_scraper_configs(user_id,scraper_type,feed_url,config,is_active) VALUES($1::bigint::integer,'aggregator','aggregator://brutalist','{\"key\":\"brutalist\",\"topics\":[\"science\"]}',true)")
            .bind(user_id).execute(&mut *connection).await.unwrap();
        assert_eq!(
            demanded_topics(&mut connection).await.unwrap(),
            vec!["science"]
        );
        for index in 0..12 {
            sqlx::query("INSERT INTO news_items(ingest_key,visibility_scope,platform,status,raw_metadata,ingested_at,created_at) VALUES ($1,'global','brutalist','ready',$2,timezone('UTC',now()),timezone('UTC',now()))")
                .bind(format!("brutalist-budget-{index}"))
                .bind(serde_json::json!({"aggregator":{"topic":"science","topics":["science"],"metadata":{"source_name":"Example Section"}}}))
                .execute(&mut *connection).await.unwrap();
        }
        let mut transaction = pool.begin().await.unwrap();
        let counts = recent_section_counts(&mut transaction).await.unwrap();
        assert_eq!(
            counts,
            vec![("science".to_owned(), "Example Section".to_owned(), 12)]
        );
        transaction.rollback().await.unwrap();
        sqlx::query("UPDATE users SET is_active=false WHERE id::bigint=$1")
            .bind(user_id)
            .execute(&mut *connection)
            .await
            .unwrap();
        assert!(demanded_topics(&mut connection).await.unwrap().is_empty());
    }
}
