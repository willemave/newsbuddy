use std::fmt::Write as _;

use chrono::{DateTime, NaiveDate, NaiveDateTime, Utc};
use serde_json::Value;
use sqlx::{AssertSqlSafe, FromRow, PgPool};
use thiserror::Error;

use crate::content_read::ContentDetailProjection;

const AVAILABLE_DATES_LOOKBACK_DAYS: i64 = 120;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ContentFeedReadFilter {
    All,
    Read,
    Unread,
}

impl ContentFeedReadFilter {
    const fn as_str(self) -> &'static str {
        match self {
            Self::All => "all",
            Self::Read => "read",
            Self::Unread => "unread",
        }
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct ContentFeedCursor {
    pub last_id: i64,
    pub last_timestamp: NaiveDateTime,
    pub last_rank: Option<f64>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct ContentCardProjection {
    pub content: ContentDetailProjection,
    pub sort_timestamp: DateTime<Utc>,
    pub knowledge_saved_at: Option<DateTime<Utc>>,
    pub saved_from_x_bookmark: bool,
    pub search_rank: Option<f64>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct ContentFeedPage {
    pub items: Vec<ContentCardProjection>,
    pub available_dates: Vec<String>,
}

#[derive(Debug, FromRow)]
struct ContentCardRow {
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
    sort_timestamp: NaiveDateTime,
    knowledge_saved_at: Option<DateTime<Utc>>,
    saved_from_x_bookmark: bool,
    search_rank: Option<f64>,
}

impl From<ContentCardRow> for ContentCardProjection {
    fn from(row: ContentCardRow) -> Self {
        Self {
            content: ContentDetailProjection {
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
            },
            sort_timestamp: row.sort_timestamp.and_utc(),
            knowledge_saved_at: row.knowledge_saved_at,
            saved_from_x_bookmark: row.saved_from_x_bookmark,
            search_rank: row.search_rank,
        }
    }
}

const CARD_SELECT: &str = r#"
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
    (read_status.id IS NOT NULL) AS is_read,
    (knowledge_save.id IS NOT NULL) AS is_saved_to_knowledge,
    (body.content_id IS NOT NULL) AS body_available,
    body.content_format AS body_format,
    {sort_expression} AS sort_timestamp,
    knowledge_save.saved_at AS knowledge_saved_at,
    EXISTS (
        SELECT 1
        FROM user_integration_synced_items AS synced_item
        JOIN user_integration_connections AS connection
          ON connection.id = synced_item.connection_id
        WHERE connection.user_id::bigint = $1::bigint
          AND connection.provider = 'x'
          AND synced_item.channel = 'bookmarks'
          AND synced_item.content_id = content.id
    ) AS saved_from_x_bookmark,
    {rank_expression} AS search_rank
FROM contents AS content
LEFT JOIN content_read_status AS read_status
  ON read_status.content_id = content.id
 AND read_status.user_id::bigint = $1::bigint
LEFT JOIN content_knowledge_saves AS knowledge_save
  ON knowledge_save.content_id = content.id
 AND knowledge_save.user_id::bigint = $1::bigint
LEFT JOIN content_status AS user_status
  ON user_status.content_id = content.id
 AND user_status.user_id::bigint = $1::bigint
 AND user_status.status = 'inbox'
LEFT JOIN content_bodies AS body
  ON body.content_id = content.id
 AND body.variant = 'source'
"#;

const CONTENT_SEARCH_DOCUMENT: &str = r#"(
    setweight(to_tsvector('english', coalesce(content.content_metadata::jsonb -> 'summary' ->> 'title', '')), 'A')
    || setweight(to_tsvector('english', coalesce(content.title, '')), 'B')
    || setweight(to_tsvector('english', coalesce(content.source, '')), 'C')
    || setweight(to_tsvector('english', coalesce(content.search_text, '')), 'D')
)"#;

fn card_select(sort_expression: &str, rank_expression: &str) -> String {
    CARD_SELECT
        .replace("{sort_expression}", sort_expression)
        .replace("{rank_expression}", rank_expression)
}

pub async fn list_content_feed(
    pool: &PgPool,
    user_id: i64,
    content_types: &[String],
    date: Option<NaiveDate>,
    read_filter: ContentFeedReadFilter,
    cursor: Option<&ContentFeedCursor>,
    limit: usize,
    include_available_dates: bool,
) -> Result<ContentFeedPage, ContentFeedRepositoryError> {
    let sort = "coalesce(content.publication_date, content.processed_at, content.created_at)";
    let mut query = card_select(sort, "NULL::double precision");
    write!(
        &mut query,
        r#"
WHERE content.status = 'completed'
  AND (content.classification IS NULL OR content.classification <> 'skip')
  AND (content.content_type = 'news' OR user_status.id IS NOT NULL)
  AND (cardinality($2::text[]) = 0 OR content.content_type = ANY($2::text[]))
  AND ($3::date IS NULL OR ({sort})::date = $3::date)
  AND (
      $4::text = 'all'
      OR ($4::text = 'read' AND read_status.id IS NOT NULL)
      OR ($4::text = 'unread' AND read_status.id IS NULL)
  )
  AND (
      $5::timestamp IS NULL
      OR {sort} < $5::timestamp
      OR ({sort} = $5::timestamp AND content.id::bigint < $6::bigint)
  )
ORDER BY {sort} DESC, content.id DESC
LIMIT $7::bigint
"#,
    )
    .expect("writing SQL to a String cannot fail");
    let requested = i64::try_from(limit.saturating_add(1)).unwrap_or(i64::MAX);
    let rows = sqlx::query_as::<_, ContentCardRow>(AssertSqlSafe(query))
        .bind(user_id)
        .bind(content_types)
        .bind(date)
        .bind(read_filter.as_str())
        .bind(cursor.map(|value| value.last_timestamp))
        .bind(cursor.map(|value| value.last_id))
        .bind(requested)
        .fetch_all(pool)
        .await?;
    let available_dates = if include_available_dates && cursor.is_none() {
        list_inbox_dates(pool, user_id, content_types, read_filter).await?
    } else {
        Vec::new()
    };
    Ok(ContentFeedPage {
        items: rows.into_iter().map(Into::into).collect(),
        available_dates,
    })
}

async fn list_inbox_dates(
    pool: &PgPool,
    user_id: i64,
    content_types: &[String],
    read_filter: ContentFeedReadFilter,
) -> Result<Vec<String>, sqlx::Error> {
    sqlx::query_scalar::<_, String>(
        r#"
        SELECT DISTINCT to_char(
            coalesce(content.publication_date, content.processed_at, content.created_at)::date,
            'YYYY-MM-DD'
        ) AS value
        FROM contents AS content
        LEFT JOIN content_read_status AS read_status
          ON read_status.content_id = content.id
         AND read_status.user_id::bigint = $1::bigint
        LEFT JOIN content_status AS user_status
          ON user_status.content_id = content.id
         AND user_status.user_id::bigint = $1::bigint
         AND user_status.status = 'inbox'
        WHERE content.status = 'completed'
          AND (content.classification IS NULL OR content.classification <> 'skip')
          AND (content.content_type = 'news' OR user_status.id IS NOT NULL)
          AND (cardinality($2::text[]) = 0 OR content.content_type = ANY($2::text[]))
          AND (
              $3::text = 'all'
              OR ($3::text = 'read' AND read_status.id IS NOT NULL)
              OR ($3::text = 'unread' AND read_status.id IS NULL)
          )
          AND coalesce(content.publication_date, content.processed_at, content.created_at)
                >= statement_timestamp() - ($4::bigint * interval '1 day')
        ORDER BY value DESC
        LIMIT 90
        "#,
    )
    .bind(user_id)
    .bind(content_types)
    .bind(read_filter.as_str())
    .bind(AVAILABLE_DATES_LOOKBACK_DAYS)
    .fetch_all(pool)
    .await
}

pub async fn list_recently_read_content(
    pool: &PgPool,
    user_id: i64,
    content_types: &[String],
    date: Option<NaiveDate>,
    cursor: Option<&ContentFeedCursor>,
    limit: usize,
) -> Result<ContentFeedPage, ContentFeedRepositoryError> {
    let sort = "read_status.read_at AT TIME ZONE 'UTC'";
    let mut query = card_select(sort, "NULL::double precision");
    write!(
        &mut query,
        r#"
WHERE content.status = 'completed'
  AND (content.classification IS NULL OR content.classification <> 'skip')
  AND read_status.id IS NOT NULL
  AND (cardinality($2::text[]) = 0 OR content.content_type = ANY($2::text[]))
  AND ($3::date IS NULL OR read_status.read_at::date = $3::date)
  AND (
      $4::timestamp IS NULL
      OR {sort} < $4::timestamp
      OR ({sort} = $4::timestamp AND content.id::bigint < $5::bigint)
  )
ORDER BY {sort} DESC, content.id DESC
LIMIT $6::bigint
"#,
    )
    .expect("writing SQL to a String cannot fail");
    let requested = i64::try_from(limit.saturating_add(1)).unwrap_or(i64::MAX);
    let rows = sqlx::query_as::<_, ContentCardRow>(AssertSqlSafe(query))
        .bind(user_id)
        .bind(content_types)
        .bind(date)
        .bind(cursor.map(|value| value.last_timestamp))
        .bind(cursor.map(|value| value.last_id))
        .bind(requested)
        .fetch_all(pool)
        .await?;
    let available_dates = sqlx::query_scalar::<_, String>(
        r#"
        SELECT DISTINCT to_char(read_status.read_at::date, 'YYYY-MM-DD') AS value
        FROM content_read_status AS read_status
        JOIN contents AS content ON content.id = read_status.content_id
        WHERE read_status.user_id::bigint = $1::bigint
          AND content.status = 'completed'
          AND (content.classification IS NULL OR content.classification <> 'skip')
          AND (cardinality($2::text[]) = 0 OR content.content_type = ANY($2::text[]))
          AND read_status.read_at >= statement_timestamp() - ($3::bigint * interval '1 day')
        ORDER BY value DESC
        LIMIT 90
        "#,
    )
    .bind(user_id)
    .bind(content_types)
    .bind(AVAILABLE_DATES_LOOKBACK_DAYS)
    .fetch_all(pool)
    .await?;
    Ok(ContentFeedPage {
        items: rows.into_iter().map(Into::into).collect(),
        available_dates,
    })
}

pub async fn list_knowledge_content(
    pool: &PgPool,
    user_id: i64,
    query_text: Option<&str>,
    cursor: Option<&ContentFeedCursor>,
    limit: usize,
) -> Result<ContentFeedPage, ContentFeedRepositoryError> {
    let rank = content_rank_expression("$2");
    let matches = content_matches_expression("$2");
    let sort = "knowledge_save.saved_at AT TIME ZONE 'UTC'";
    let mut query = card_select(
        sort,
        &format!("CASE WHEN $2::text IS NULL THEN NULL::double precision ELSE {rank} END"),
    );
    write!(
        &mut query,
        r#"
WHERE knowledge_save.id IS NOT NULL
  AND ($2::text IS NULL OR {matches})
  AND (
      $3::timestamp IS NULL
      OR (
          $2::text IS NOT NULL AND $4::double precision IS NOT NULL
          AND (
              {rank} < $4::double precision
              OR (
                  {rank} = $4::double precision
                  AND (
                      {sort} < $3::timestamp
                      OR ({sort} = $3::timestamp AND content.id::bigint < $5::bigint)
                  )
              )
          )
      )
      OR (
          ($2::text IS NULL OR $4::double precision IS NULL)
          AND (
              {sort} < $3::timestamp
              OR ({sort} = $3::timestamp AND content.id::bigint < $5::bigint)
          )
      )
  )
ORDER BY search_rank DESC NULLS LAST, {sort} DESC, content.id DESC
LIMIT $6::bigint
"#,
    )
    .expect("writing SQL to a String cannot fail");
    let requested = i64::try_from(limit.saturating_add(1)).unwrap_or(i64::MAX);
    let rows = sqlx::query_as::<_, ContentCardRow>(AssertSqlSafe(query))
        .bind(user_id)
        .bind(query_text)
        .bind(cursor.map(|value| value.last_timestamp))
        .bind(cursor.and_then(|value| value.last_rank))
        .bind(cursor.map(|value| value.last_id))
        .bind(requested)
        .fetch_all(pool)
        .await?;
    Ok(ContentFeedPage {
        items: rows.into_iter().map(Into::into).collect(),
        available_dates: Vec::new(),
    })
}

pub async fn search_visible_content(
    pool: &PgPool,
    user_id: i64,
    query_text: &str,
    content_type: Option<&str>,
    cursor: Option<&ContentFeedCursor>,
    offset: usize,
    limit: usize,
) -> Result<ContentFeedPage, ContentFeedRepositoryError> {
    let rank = content_rank_expression("$2");
    let matches = content_matches_expression("$2");
    let mut query = card_select("content.created_at", &rank);
    write!(
        &mut query,
        r#"
WHERE content.status = 'completed'
  AND (content.classification IS NULL OR content.classification <> 'skip')
  AND (content.content_type = 'news' OR user_status.id IS NOT NULL)
  AND ($3::text IS NULL OR content.content_type = $3::text)
  AND {matches}
  AND (
      content.content_type = 'news'
      OR jsonb_typeof(content.content_metadata::jsonb -> 'summary') = 'object'
  )
  AND (
      $4::timestamp IS NULL
      OR {rank} < $5::double precision
      OR (
          {rank} = $5::double precision
          AND (
              content.created_at < $4::timestamp
              OR (content.created_at = $4::timestamp AND content.id::bigint < $6::bigint)
          )
      )
  )
ORDER BY search_rank DESC, content.created_at DESC, content.id DESC
OFFSET CASE WHEN $4::timestamp IS NULL THEN $7::bigint ELSE 0 END
LIMIT $8::bigint
"#,
    )
    .expect("writing SQL to a String cannot fail");
    let requested = i64::try_from(limit.saturating_add(1)).unwrap_or(i64::MAX);
    let offset = i64::try_from(offset).unwrap_or(i64::MAX);
    let rows = sqlx::query_as::<_, ContentCardRow>(AssertSqlSafe(query))
        .bind(user_id)
        .bind(query_text)
        .bind(content_type)
        .bind(cursor.map(|value| value.last_timestamp))
        .bind(cursor.and_then(|value| value.last_rank))
        .bind(cursor.map(|value| value.last_id))
        .bind(offset)
        .bind(requested)
        .fetch_all(pool)
        .await?;
    Ok(ContentFeedPage {
        items: rows.into_iter().map(Into::into).collect(),
        available_dates: Vec::new(),
    })
}

fn content_rank_expression(parameter: &str) -> String {
    format!(
        "greatest(ts_rank_cd({CONTENT_SEARCH_DOCUMENT}, websearch_to_tsquery('english', {parameter}::text)), greatest(public.word_similarity({parameter}::text, coalesce(content.content_metadata::jsonb -> 'summary' ->> 'title', '')), public.word_similarity({parameter}::text, coalesce(content.title, '')), public.word_similarity({parameter}::text, coalesce(content.source, ''))) * 0.25)"
    )
}

fn content_matches_expression(parameter: &str) -> String {
    format!(
        "({CONTENT_SEARCH_DOCUMENT} @@ websearch_to_tsquery('english', {parameter}::text) OR coalesce(content.content_metadata::jsonb -> 'summary' ->> 'title', '') OPERATOR(public.%>>) {parameter}::text OR content.title OPERATOR(public.%>>) {parameter}::text OR content.source OPERATOR(public.%>>) {parameter}::text)"
    )
}

#[derive(Debug, Error)]
pub enum ContentFeedRepositoryError {
    #[error("PostgreSQL content-feed query failed")]
    Sqlx(#[from] sqlx::Error),
}

#[cfg(test)]
mod tests {
    use chrono::{DateTime, Utc};
    use sqlx::PgPool;

    use super::{list_knowledge_content, list_recently_read_content};

    #[sqlx::test]
    async fn saved_and_read_feed_timestamps_decode_as_utc(pool: PgPool) {
        let user_id = sqlx::query_scalar::<_, i64>(
            r#"
            INSERT INTO users (apple_id, email, is_admin, is_active)
            VALUES ('content-feed-timestamp-user', 'content-feed-timestamp@example.test', FALSE, TRUE)
            RETURNING id::bigint
            "#,
        )
        .fetch_one(&pool)
        .await
        .expect("test user should insert");
        let content_id = sqlx::query_scalar::<_, i64>(
            r#"
            INSERT INTO contents (
                content_type,
                url,
                title,
                source,
                status,
                content_metadata,
                is_aggregate
            )
            VALUES (
                'article',
                'https://example.test/content-feed-timestamp',
                'Content feed timestamp',
                'Example',
                'completed',
                '{}'::json,
                FALSE
            )
            RETURNING id::bigint
            "#,
        )
        .fetch_one(&pool)
        .await
        .expect("test content should insert");
        let expected = DateTime::parse_from_rfc3339("2026-08-31T12:34:56.123456Z")
            .expect("test timestamp should parse")
            .with_timezone(&Utc);
        sqlx::query(
            r#"
            INSERT INTO content_knowledge_saves (user_id, content_id, saved_at, created_at)
            VALUES ($1, $2, $3, $3)
            "#,
        )
        .bind(user_id)
        .bind(content_id)
        .bind(expected)
        .execute(&pool)
        .await
        .expect("Knowledge save should insert");
        sqlx::query(
            r#"
            INSERT INTO content_read_status (user_id, content_id, read_at, created_at)
            VALUES ($1, $2, $3, $3)
            "#,
        )
        .bind(user_id)
        .bind(content_id)
        .bind(expected)
        .execute(&pool)
        .await
        .expect("read status should insert");

        let knowledge_page = list_knowledge_content(&pool, user_id, None, None, 25)
            .await
            .expect("Knowledge rows with timestamptz saves should decode");
        assert_eq!(knowledge_page.items.len(), 1);
        assert_eq!(knowledge_page.items[0].sort_timestamp, expected);
        assert_eq!(knowledge_page.items[0].knowledge_saved_at, Some(expected));

        let recent_page = list_recently_read_content(&pool, user_id, &[], None, None, 25)
            .await
            .expect("recent rows with timestamptz reads should decode");
        assert_eq!(recent_page.items.len(), 1);
        assert_eq!(recent_page.items[0].sort_timestamp, expected);
        assert_eq!(recent_page.items[0].knowledge_saved_at, Some(expected));
    }
}
