use chrono::{DateTime, NaiveDateTime, Utc};
use serde_json::Value;
use sqlx::{AssertSqlSafe, FromRow, PgPool};
use thiserror::Error;

#[derive(Debug, Clone, PartialEq)]
pub struct ContentDetailProjection {
    pub id: i64,
    pub content_type: String,
    pub url: String,
    pub source_url: Option<String>,
    pub title: Option<String>,
    pub source: Option<String>,
    pub platform: Option<String>,
    pub status: String,
    pub error_message: Option<String>,
    pub retry_count: i32,
    pub content_metadata: Value,
    pub created_at: DateTime<Utc>,
    pub updated_at: Option<DateTime<Utc>>,
    pub processed_at: Option<DateTime<Utc>>,
    pub checked_out_by: Option<String>,
    pub checked_out_at: Option<DateTime<Utc>>,
    pub publication_date: Option<DateTime<Utc>>,
    pub is_read: bool,
    pub is_saved_to_knowledge: bool,
    pub body_available: bool,
    pub body_format: Option<String>,
}

#[derive(Debug, FromRow)]
struct ContentDetailRow {
    id: i64,
    content_type: String,
    url: String,
    source_url: Option<String>,
    title: Option<String>,
    source: Option<String>,
    platform: Option<String>,
    status: String,
    error_message: Option<String>,
    retry_count: i32,
    content_metadata: Value,
    created_at: NaiveDateTime,
    updated_at: Option<NaiveDateTime>,
    processed_at: Option<NaiveDateTime>,
    checked_out_by: Option<String>,
    checked_out_at: Option<NaiveDateTime>,
    publication_date: Option<NaiveDateTime>,
    is_read: bool,
    is_saved_to_knowledge: bool,
    body_available: bool,
    body_format: Option<String>,
}

impl From<ContentDetailRow> for ContentDetailProjection {
    fn from(row: ContentDetailRow) -> Self {
        Self {
            id: row.id,
            content_type: row.content_type,
            url: row.url,
            source_url: row.source_url,
            title: row.title,
            source: row.source,
            platform: row.platform,
            status: row.status,
            error_message: row.error_message,
            retry_count: row.retry_count,
            content_metadata: row.content_metadata,
            created_at: row.created_at.and_utc(),
            updated_at: row.updated_at.map(|value| value.and_utc()),
            processed_at: row.processed_at.map(|value| value.and_utc()),
            checked_out_by: row.checked_out_by,
            checked_out_at: row.checked_out_at.map(|value| value.and_utc()),
            publication_date: row.publication_date.map(|value| value.and_utc()),
            is_read: row.is_read,
            is_saved_to_knowledge: row.is_saved_to_knowledge,
            body_available: row.body_available,
            body_format: row.body_format,
        }
    }
}

/// Return one completed long-form Content row only through the current user's inbox or Knowledge
/// membership. This query deliberately never reads `news_items`, even when the numeric IDs match.
pub async fn find_visible_content_detail(
    pool: &PgPool,
    user_id: i64,
    content_id: i64,
) -> Result<Option<ContentDetailProjection>, ContentReadRepositoryError> {
    let row = sqlx::query_as::<_, ContentDetailRow>(
        r#"
        SELECT
            content.id::bigint AS id,
            content.content_type,
            content.url,
            content.source_url,
            content.title,
            content.source,
            content.platform,
            content.status,
            content.error_message,
            coalesce(content.retry_count, 0)::integer AS retry_count,
            content.content_metadata::jsonb AS content_metadata,
            content.created_at,
            content.updated_at,
            content.processed_at,
            content.checked_out_by,
            content.checked_out_at,
            content.publication_date,
            EXISTS (
                SELECT 1
                FROM content_read_status AS read_status
                WHERE read_status.user_id::bigint = $1::bigint
                  AND read_status.content_id = content.id
            ) AS is_read,
            EXISTS (
                SELECT 1
                FROM content_knowledge_saves AS knowledge_save
                WHERE knowledge_save.user_id::bigint = $1::bigint
                  AND knowledge_save.content_id = content.id
            ) AS is_saved_to_knowledge,
            (body.content_id IS NOT NULL) AS body_available,
            body.content_format AS body_format
        FROM contents AS content
        LEFT JOIN content_bodies AS body
          ON body.content_id = content.id
         AND body.variant = 'source'
        WHERE content.id::bigint = $2::bigint
          AND content.status = 'completed'
          AND (
              EXISTS (
                  SELECT 1
                  FROM content_knowledge_saves AS knowledge_save
                  WHERE knowledge_save.user_id::bigint = $1::bigint
                    AND knowledge_save.content_id = content.id
              )
              OR (
                  (content.classification IS NULL OR content.classification <> 'skip')
                  AND EXISTS (
                      SELECT 1
                      FROM content_status AS user_status
                      WHERE user_status.user_id::bigint = $1::bigint
                        AND user_status.content_id = content.id
                        AND user_status.status = 'inbox'
                  )
              )
          )
        "#,
    )
    .bind(user_id)
    .bind(content_id)
    .fetch_optional(pool)
    .await?;
    Ok(row.map(Into::into))
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum NewsReadFilter {
    All,
    Read,
    Unread,
}

impl NewsReadFilter {
    const fn as_str(self) -> &'static str {
        match self {
            Self::All => "all",
            Self::Read => "read",
            Self::Unread => "unread",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct NewsListCursor {
    pub last_id: i64,
    pub last_sort_timestamp: NaiveDateTime,
}

#[derive(Debug, Clone, PartialEq)]
pub struct NewsItemProjection {
    pub id: i64,
    pub platform: Option<String>,
    pub source_label: Option<String>,
    pub canonical_item_url: Option<String>,
    pub canonical_story_url: Option<String>,
    pub article_url: Option<String>,
    pub article_domain: Option<String>,
    pub discussion_url: Option<String>,
    pub summary_key_points: Value,
    pub summary_text: Option<String>,
    pub raw_metadata: Value,
    pub status: String,
    pub cluster_size: i32,
    pub published_at: Option<DateTime<Utc>>,
    pub ingested_at: DateTime<Utc>,
    pub processed_at: Option<DateTime<Utc>>,
    pub created_at: DateTime<Utc>,
    pub updated_at: Option<DateTime<Utc>>,
    pub sort_timestamp: DateTime<Utc>,
    pub is_read: bool,
    pub discussion_summary: Option<Value>,
    pub body_format: Option<String>,
}

impl NewsItemProjection {
    pub const fn body_available(&self) -> bool {
        self.body_format.is_some()
    }
}

#[derive(Debug, FromRow)]
struct NewsItemRow {
    id: i64,
    platform: Option<String>,
    source_label: Option<String>,
    canonical_item_url: Option<String>,
    canonical_story_url: Option<String>,
    article_url: Option<String>,
    article_domain: Option<String>,
    discussion_url: Option<String>,
    summary_key_points: Value,
    summary_text: Option<String>,
    raw_metadata: Value,
    status: String,
    cluster_size: i32,
    published_at: Option<NaiveDateTime>,
    ingested_at: NaiveDateTime,
    processed_at: Option<NaiveDateTime>,
    created_at: NaiveDateTime,
    updated_at: Option<NaiveDateTime>,
    sort_timestamp: NaiveDateTime,
    is_read: bool,
    discussion_summary: Option<Value>,
    body_format: Option<String>,
}

impl From<NewsItemRow> for NewsItemProjection {
    fn from(row: NewsItemRow) -> Self {
        Self {
            id: row.id,
            platform: row.platform,
            source_label: row.source_label,
            canonical_item_url: row.canonical_item_url,
            canonical_story_url: row.canonical_story_url,
            article_url: row.article_url,
            article_domain: row.article_domain,
            discussion_url: row.discussion_url,
            summary_key_points: row.summary_key_points,
            summary_text: row.summary_text,
            raw_metadata: row.raw_metadata,
            status: row.status,
            cluster_size: row.cluster_size,
            published_at: row.published_at.map(|value| value.and_utc()),
            ingested_at: row.ingested_at.and_utc(),
            processed_at: row.processed_at.map(|value| value.and_utc()),
            created_at: row.created_at.and_utc(),
            updated_at: row.updated_at.map(|value| value.and_utc()),
            sort_timestamp: row.sort_timestamp.and_utc(),
            is_read: row.is_read,
            discussion_summary: row.discussion_summary,
            body_format: row.body_format,
        }
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct NewsListPage {
    pub items: Vec<NewsItemProjection>,
    pub total: i64,
}

const VISIBLE_NEWS_CTE: &str = r#"
WITH valid_aggregators AS (
    SELECT
        lower(btrim(config::jsonb ->> 'key')) AS source_key,
        CASE
            WHEN jsonb_typeof(config::jsonb -> 'topics') = 'array'
            THEN config::jsonb -> 'topics'
            ELSE '[]'::jsonb
        END AS topics
    FROM user_scraper_configs
    WHERE user_id::bigint = $1::bigint
      AND scraper_type = 'aggregator'
      AND is_active IS TRUE
      AND lower(btrim(config::jsonb ->> 'key')) = ANY(ARRAY[
          'brutalist', 'finurls', 'hackernews', 'mediagazer',
          'memeorandum', 'sciurls', 'techmeme'
      ])
)
"#;

const VISIBLE_NEWS_PREDICATE: &str = r#"
news.status = 'ready'
AND news.representative_news_item_id IS NULL
AND (
    (news.visibility_scope = 'user' AND news.owner_user_id::bigint = $1::bigint)
    OR (
        news.visibility_scope = 'global'
        AND EXISTS (
            SELECT 1
            FROM valid_aggregators AS aggregator
            WHERE lower(news.platform) = aggregator.source_key
              AND (
                  aggregator.source_key <> 'brutalist'
                  OR jsonb_array_length(aggregator.topics) = 0
                  OR EXISTS (
                      SELECT 1
                      FROM jsonb_array_elements_text(aggregator.topics) AS topic(value)
                      WHERE lower(btrim(topic.value)) = lower(btrim(
                          news.raw_metadata::jsonb #>> '{aggregator,topic}'
                      ))
                  )
              )
        )
    )
)
"#;

fn visible_news_count_query() -> String {
    format!(
        r#"{VISIBLE_NEWS_CTE}
        SELECT count(*)::bigint
        FROM news_items AS news
        WHERE {VISIBLE_NEWS_PREDICATE}
          AND (
              $2::text = 'all'
              OR ($2::text = 'read' AND EXISTS (
                  SELECT 1 FROM news_item_read_status AS read_status
                  WHERE read_status.user_id::bigint = $1::bigint
                    AND read_status.news_item_id = news.id
              ))
              OR ($2::text = 'unread' AND NOT EXISTS (
                  SELECT 1 FROM news_item_read_status AS read_status
                  WHERE read_status.user_id::bigint = $1::bigint
                    AND read_status.news_item_id = news.id
              ))
          )
        "#,
    )
}

fn visible_news_items_query(include_id_filter: bool) -> String {
    let id_filter = if include_id_filter {
        "AND news.id::bigint = $2::bigint"
    } else {
        ""
    };
    format!(
        r#"{VISIBLE_NEWS_CTE}
        SELECT
            news.id::bigint AS id,
            news.platform,
            news.source_label,
            news.canonical_item_url,
            news.canonical_story_url,
            news.article_url,
            news.article_domain,
            news.discussion_url,
            news.summary_key_points::jsonb AS summary_key_points,
            news.summary_text,
            news.raw_metadata::jsonb AS raw_metadata,
            news.status,
            coalesce(news.cluster_size, 1)::integer AS cluster_size,
            news.published_at,
            news.ingested_at,
            news.processed_at,
            news.created_at,
            news.updated_at,
            coalesce(news.published_at, news.processed_at, news.ingested_at, news.created_at)
                AS sort_timestamp,
            EXISTS (
                SELECT 1 FROM news_item_read_status AS read_status
                WHERE read_status.user_id::bigint = $1::bigint
                  AND read_status.news_item_id = news.id
            ) AS is_read,
            discussion.summary::jsonb AS discussion_summary,
            CASE
                WHEN news.raw_metadata::jsonb #>> '{{article_body_ref,kind}}' = 'content'
                  AND coalesce(news.raw_metadata::jsonb #>> '{{article_body_ref,content_id}}', '') ~ '^[0-9]+$'
                THEN 'text'
                WHEN news.raw_metadata::jsonb #>> '{{article_body_ref,kind}}' = 'storage'
                  AND btrim(coalesce(news.raw_metadata::jsonb #>> '{{article_body_ref,storage_key}}', '')) <> ''
                THEN CASE
                    WHEN news.raw_metadata::jsonb #>> '{{article_body_ref,content_format}}' = 'markdown'
                    THEN 'markdown'
                    ELSE 'text'
                END
                ELSE (
                    SELECT coalesce(
                        body.content_format,
                        CASE
                            WHEN btrim(coalesce(article.content_metadata::jsonb ->> 'content_to_summarize', '')) <> ''
                              OR btrim(coalesce(article.content_metadata::jsonb ->> 'content', '')) <> ''
                            THEN 'text'
                            WHEN btrim(coalesce(article.content_metadata::jsonb #>> '{{summary,full_markdown}}', '')) <> ''
                            THEN 'markdown'
                            ELSE NULL
                        END
                    )
                    FROM contents AS article
                    LEFT JOIN content_bodies AS body
                      ON body.content_id = article.id
                     AND body.variant IN ('source', 'rendered')
                    WHERE article.content_type = 'article'
                      AND (
                          article.url = coalesce(news.article_url, news.canonical_story_url)
                          OR article.source_url = coalesce(news.article_url, news.canonical_story_url)
                      )
                    ORDER BY article.id, CASE body.variant WHEN 'source' THEN 0 ELSE 1 END
                    LIMIT 1
                )
            END AS body_format
        FROM news_items AS news
        LEFT JOIN news_item_discussions AS discussion
          ON discussion.news_item_id = news.id
        WHERE {VISIBLE_NEWS_PREDICATE}
          {id_filter}
        "#,
    )
}

/// Return one visible canonical News row. This query deliberately never reads `contents` as a
/// fallback identity; the only Content lookup is a body-pointer convenience after News ownership
/// has already been established.
pub async fn find_visible_news_item_detail(
    pool: &PgPool,
    user_id: i64,
    news_item_id: i64,
) -> Result<Option<NewsItemProjection>, ContentReadRepositoryError> {
    // The dynamic text contains only module-owned SQL fragments; every runtime value is bound.
    let row = sqlx::query_as::<_, NewsItemRow>(AssertSqlSafe(visible_news_items_query(true)))
        .bind(user_id)
        .bind(news_item_id)
        .fetch_optional(pool)
        .await?;
    Ok(row.map(Into::into))
}

pub async fn list_visible_news_items(
    pool: &PgPool,
    user_id: i64,
    read_filter: NewsReadFilter,
    cursor: Option<&NewsListCursor>,
    limit: usize,
) -> Result<NewsListPage, ContentReadRepositoryError> {
    // The dynamic text contains only module-owned SQL fragments; every runtime value is bound.
    let total = sqlx::query_scalar::<_, i64>(AssertSqlSafe(visible_news_count_query()))
        .bind(user_id)
        .bind(read_filter.as_str())
        .fetch_one(pool)
        .await?;

    let mut query = visible_news_items_query(false);
    query.push_str(
        r#"
          AND (
              $2::text = 'all'
              OR ($2::text = 'read' AND EXISTS (
                  SELECT 1 FROM news_item_read_status AS read_status
                  WHERE read_status.user_id::bigint = $1::bigint
                    AND read_status.news_item_id = news.id
              ))
              OR ($2::text = 'unread' AND NOT EXISTS (
                  SELECT 1 FROM news_item_read_status AS read_status
                  WHERE read_status.user_id::bigint = $1::bigint
                    AND read_status.news_item_id = news.id
              ))
          )
          AND (
              $3::timestamp IS NULL
              OR coalesce(news.published_at, news.processed_at, news.ingested_at, news.created_at)
                    < $3::timestamp
              OR (
                  coalesce(news.published_at, news.processed_at, news.ingested_at, news.created_at)
                        = $3::timestamp
                  AND news.id::bigint < $4::bigint
              )
          )
        ORDER BY
            coalesce(news.published_at, news.processed_at, news.ingested_at, news.created_at) DESC,
            news.id DESC
        LIMIT $5::bigint
        "#,
    );
    let requested = i64::try_from(limit.saturating_add(1)).unwrap_or(i64::MAX);
    // The dynamic text contains only module-owned SQL fragments; every runtime value is bound.
    let rows = sqlx::query_as::<_, NewsItemRow>(AssertSqlSafe(query))
        .bind(user_id)
        .bind(read_filter.as_str())
        .bind(cursor.map(|value| value.last_sort_timestamp))
        .bind(cursor.map(|value| value.last_id))
        .bind(requested)
        .fetch_all(pool)
        .await?;

    Ok(NewsListPage {
        items: rows.into_iter().map(Into::into).collect(),
        total,
    })
}

pub async fn list_active_feed_urls(
    pool: &PgPool,
    user_id: i64,
    feed_type: &str,
) -> Result<Vec<String>, ContentReadRepositoryError> {
    Ok(sqlx::query_scalar::<_, String>(
        r#"
        SELECT feed_url
        FROM user_scraper_configs
        WHERE user_id::bigint = $1::bigint
          AND scraper_type = $2
          AND is_active IS TRUE
          AND feed_url IS NOT NULL
        "#,
    )
    .bind(user_id)
    .bind(feed_type)
    .fetch_all(pool)
    .await?)
}

#[derive(Debug, Error)]
pub enum ContentReadRepositoryError {
    #[error("PostgreSQL content read-model query failed")]
    Sqlx(#[from] sqlx::Error),
}
