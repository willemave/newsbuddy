use super::news_queries::{
    NEWS_COLUMNS, NEWS_FROM, NEWS_SEARCH_FILTER, NEWS_SEARCH_RANK, news_visibility_filter,
};
use super::{ChatToolRepositoryError, NewsHitRow};
use crate::news_visibility::SUBSCRIBED_AGGREGATORS_CTE;
use serde::Serialize;
use serde_json::Value;
use sqlx::{AssertSqlSafe, FromRow, PgPool, Postgres, Transaction};

#[derive(Debug, Serialize, FromRow)]
pub struct ChatSource {
    pub id: i64,
    pub title: String,
    pub feed_url: Option<String>,
    pub kind: String,
    pub is_active: bool,
}

pub async fn list_chat_sources(
    pool: &PgPool,
    user_id: i64,
    query: &str,
    kind: Option<&str>,
    active_only: bool,
    limit: i64,
    offset: i64,
) -> Result<Vec<ChatSource>, ChatToolRepositoryError> {
    if user_id <= 0
        || !(1..=100).contains(&limit)
        || !(0..=100_000).contains(&offset)
        || query.len() > 2000
    {
        return Err(ChatToolRepositoryError::InvalidInput);
    }
    Ok(sqlx::query_as::<_, ChatSource>(r#"
      SELECT config.id::bigint, COALESCE(NULLIF(BTRIM(display_name),''), NULLIF(BTRIM(config->>'name'),''), feed_url, 'Unnamed source') AS title,
        feed_url, CASE scraper_type WHEN 'podcast_rss' THEN 'podcast' WHEN 'aggregator' THEN 'aggregator' ELSE 'publication' END AS kind, config.is_active
      FROM user_scraper_configs config JOIN users account ON account.id=config.user_id AND account.is_active
      WHERE config.user_id::bigint=$1 AND (NOT $4 OR config.is_active)
        AND ($2='' OR strpos(lower(COALESCE(display_name,'') || ' ' || COALESCE(config->>'name','') || ' ' || COALESCE(feed_url,'')),lower($2))>0)
        AND ($3::text IS NULL OR CASE scraper_type WHEN 'podcast_rss' THEN 'podcast' WHEN 'aggregator' THEN 'aggregator' ELSE 'publication' END=$3)
      ORDER BY config.id LIMIT $5 OFFSET $6
    "#).bind(user_id).bind(query.trim()).bind(kind).bind(active_only).bind(limit).bind(offset).fetch_all(pool).await?)
}

pub async fn find_chat_source(
    pool: &PgPool,
    user_id: i64,
    id: i64,
) -> Result<Option<ChatSource>, ChatToolRepositoryError> {
    Ok(sqlx::query_as::<_,ChatSource>("SELECT config.id::bigint,COALESCE(NULLIF(BTRIM(display_name),''),NULLIF(BTRIM(config->>'name'),''),feed_url,'Unnamed source') AS title,feed_url,CASE scraper_type WHEN 'podcast_rss' THEN 'podcast' WHEN 'aggregator' THEN 'aggregator' ELSE 'publication' END AS kind,config.is_active FROM user_scraper_configs config JOIN users account ON account.id=config.user_id AND account.is_active WHERE config.user_id::bigint=$1 AND config.id::bigint=$2").bind(user_id).bind(id).fetch_optional(pool).await?)
}

pub async fn search_chat_news_page(
    pool: &PgPool,
    user_id: i64,
    query: &str,
    unread_only: bool,
    source: Option<&str>,
    limit: i64,
    offset: i64,
) -> Result<Value, ChatToolRepositoryError> {
    super::validate_search(user_id, query, limit)?;
    if !(0..=100_000).contains(&offset) {
        return Err(ChatToolRepositoryError::InvalidInput);
    }
    // Filter the authorized match set before counting and paging. Rank is internal only;
    // both page selection and JSON aggregation use the same deterministic ordering.
    let sql = format!(
        r#"
        {SUBSCRIBED_AGGREGATORS_CTE}, matches AS (
            SELECT {NEWS_COLUMNS}, {NEWS_SEARCH_RANK} AS search_rank
            {NEWS_FROM}
            {visibility}
            AND (NOT $3::boolean OR read.id IS NULL)
            AND ($4::text IS NULL OR item.platform = $4::text)
            {NEWS_SEARCH_FILTER}
        ), page AS (
            SELECT * FROM matches
            ORDER BY search_rank DESC, published_at DESC, news_item_id DESC
            LIMIT $5::bigint OFFSET $6::bigint
        )
        SELECT jsonb_build_object(
            'items', COALESCE((SELECT jsonb_agg(
                (to_jsonb(page) - 'search_rank') || jsonb_build_object(
                    'reference', jsonb_build_object('kind', 'news_item', 'id', page.news_item_id)
                ) ORDER BY search_rank DESC, published_at DESC, news_item_id DESC
            ) FROM page), '[]'::jsonb),
            'total_count', (SELECT count(*) FROM matches),
            'returned_count', (SELECT count(*) FROM page),
            'next_offset', CASE WHEN $6::bigint + $5::bigint < (SELECT count(*) FROM matches)
                THEN $6::bigint + $5::bigint ELSE NULL END
        )
        "#,
        visibility = news_visibility_filter(),
    );
    Ok(sqlx::query_scalar::<_, Value>(AssertSqlSafe(sql))
        .bind(user_id)
        .bind(query.trim())
        .bind(unread_only)
        .bind(source)
        .bind(limit)
        .bind(offset)
        .fetch_one(pool)
        .await?)
}

pub async fn read_chat_news(
    pool: &PgPool,
    user_id: i64,
    id: i64,
) -> Result<Option<Value>, ChatToolRepositoryError> {
    let sql = format!(
        "{SUBSCRIBED_AGGREGATORS_CTE} SELECT {NEWS_COLUMNS} {NEWS_FROM} {visibility} AND item.id::bigint = $2::bigint",
        visibility = news_visibility_filter(),
    );
    let row = sqlx::query_as::<_, NewsHitRow>(AssertSqlSafe(sql))
        .bind(user_id)
        .bind(id)
        .fetch_optional(pool)
        .await?;
    row.map(|row| {
        let mut value = serde_json::to_value(super::ChatNewsHit::from(row))?;
        value["reference"] = serde_json::json!({"kind":"news_item","id":id});
        Ok(value)
    })
    .transpose()
}

pub async fn set_chat_news_read(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
    id: i64,
    is_read: bool,
) -> Result<bool, ChatToolRepositoryError> {
    // Recheck access within the mutation transaction. No numeric Content fallback.
    let authorization_sql = format!(
        r#"{SUBSCRIBED_AGGREGATORS_CTE}
        SELECT EXISTS (
            SELECT 1 FROM news_items AS item
            JOIN users AS account ON account.id::bigint = $1::bigint AND account.is_active
            {visibility}
            AND item.id::bigint = $2::bigint
        )"#,
        visibility = news_visibility_filter(),
    );
    let authorized = sqlx::query_scalar::<_, bool>(AssertSqlSafe(authorization_sql))
        .bind(user_id)
        .bind(id)
        .fetch_one(&mut **transaction)
        .await?;
    if !authorized {
        return Err(ChatToolRepositoryError::InvalidInput);
    }
    let result = if is_read {
        sqlx::query("INSERT INTO news_item_read_status(user_id,news_item_id,read_at,created_at) VALUES($1::bigint::integer,$2::bigint::integer,timezone('UTC',now()),timezone('UTC',now())) ON CONFLICT(user_id,news_item_id) DO NOTHING").bind(user_id).bind(id).execute(&mut **transaction).await?
    } else {
        sqlx::query("DELETE FROM news_item_read_status WHERE user_id::bigint=$1 AND news_item_id::bigint=$2").bind(user_id).bind(id).execute(&mut **transaction).await?
    };
    Ok(result.rows_affected() > 0)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[sqlx::test(migrations = false)]
    async fn source_ownership_and_news_filters_precede_paging(pool: PgPool) {
        crate::run_migrations(&pool).await.unwrap();
        sqlx::raw_sql(r#"
   INSERT INTO users(id,apple_id,email,is_active,is_admin) VALUES(9201,'assistant-one','assistant-one@test',true,false),(9202,'assistant-two','assistant-two@test',true,false);
   INSERT INTO user_scraper_configs(id,user_id,scraper_type,feed_url,display_name,config,is_active) VALUES(9201,9201,'podcast_rss','https://example.test/feed','Example','{}',true),(9202,9202,'podcast_rss','https://private.test/feed','Private','{}',true);
   INSERT INTO news_items(id,ingest_key,visibility_scope,owner_user_id,platform,status,summary_text,raw_metadata,created_at,ingested_at) VALUES
   (9201,'assistant-news-one','user',9201,'test','ready','First item','{}',now(),now()),
   (9202,'assistant-news-two','user',9202,'test','ready','Private item','{}',now(),now()),
   (9203,'assistant-news-three','user',9201,'test','ready','Third item','{}',now(),now());
   INSERT INTO news_item_read_status(user_id,news_item_id,read_at,created_at) VALUES(9201,9203,now(),now());
  "#).execute(&pool).await.unwrap();
        assert!(find_chat_source(&pool, 9201, 9202).await.unwrap().is_none());
        assert_eq!(
            list_chat_sources(&pool, 9201, "", None, true, 20, 0)
                .await
                .unwrap()
                .len(),
            1
        );
        let page = search_chat_news_page(&pool, 9201, "", true, None, 1, 0)
            .await
            .unwrap();
        assert_eq!(page["total_count"], 1);
        assert_eq!(
            page["items"][0]["reference"],
            serde_json::json!({"kind":"news_item","id":9201})
        );
        let all = search_chat_news_page(&pool, 9201, "", false, None, 1, 0)
            .await
            .unwrap();
        assert_eq!(all["total_count"], 2);
        assert_eq!(all["next_offset"], 1);
        assert_eq!(all["items"][0]["news_item_id"], 9203);
        assert!(all["items"][0].get("search_rank").is_none());
        let second = search_chat_news_page(&pool, 9201, "", false, Some("test"), 1, 1)
            .await
            .unwrap();
        assert_eq!(second["total_count"], 2);
        assert_eq!(second["items"][0]["news_item_id"], 9201);
        assert!(second["next_offset"].is_null());
        let source_miss = search_chat_news_page(&pool, 9201, "", false, Some("other"), 1, 0)
            .await
            .unwrap();
        assert_eq!(source_miss["total_count"], 0);
        assert_eq!(source_miss["items"], serde_json::json!([]));
        let query_match = search_chat_news_page(&pool, 9201, "First", false, None, 1, 0)
            .await
            .unwrap();
        assert_eq!(query_match["total_count"], 1);
        assert_eq!(query_match["items"][0]["news_item_id"], 9201);
        assert_eq!(
            read_chat_news(&pool, 9201, 9203).await.unwrap().unwrap()["is_read"],
            true
        );
        assert!(read_chat_news(&pool, 9201, 9202).await.unwrap().is_none());
        let mut tx = pool.begin().await.unwrap();
        assert!(set_chat_news_read(&mut tx, 9201, 9202, true).await.is_err());
        tx.rollback().await.unwrap();
        let mut tx = pool.begin().await.unwrap();
        assert!(set_chat_news_read(&mut tx, 9201, 9201, true).await.unwrap());
        tx.commit().await.unwrap();
        assert_eq!(
            search_chat_news_page(&pool, 9201, "", true, None, 1, 0)
                .await
                .unwrap()["total_count"],
            0
        );
        let mut tx = pool.begin().await.unwrap();
        set_chat_news_read(&mut tx, 9201, 9201, false)
            .await
            .unwrap();
        tx.commit().await.unwrap();
        assert_eq!(
            search_chat_news_page(&pool, 9201, "", true, None, 1, 0)
                .await
                .unwrap()["total_count"],
            1
        );
    }
}
