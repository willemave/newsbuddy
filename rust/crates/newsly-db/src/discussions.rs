use serde_json::Value;
use sqlx::{FromRow, PgPool};
use thiserror::Error;

use crate::content_read::{find_visible_content_detail, find_visible_news_item_detail};

#[derive(Debug, Clone, PartialEq)]
pub struct ContentDiscussionProjection {
    pub content_id: i64,
    pub platform: Option<String>,
    pub discussion_url: Option<String>,
    /// Full `content_discussions` row serialized by PostgreSQL. This preserves the intentionally
    /// heterogeneous legacy JSON payload while the API presenter owns wire normalization.
    pub stored_discussion: Option<Value>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct NewsDiscussionProjection {
    pub news_item_id: i64,
    pub platform: Option<String>,
    pub discussion_url: Option<String>,
    pub stored_news_discussion: Option<Value>,
    pub stored_legacy_discussion: Option<Value>,
    pub embedded_discussion: Option<Value>,
    pub embedded_status: Option<String>,
    pub embedded_error: Option<String>,
    pub embedded_fetched_at: Option<String>,
}

#[derive(Debug, FromRow)]
struct ContentDiscussionRow {
    stored_discussion: Option<Value>,
}

#[derive(Debug, FromRow)]
struct NewsDiscussionRow {
    stored_news_discussion: Option<Value>,
    stored_legacy_discussion: Option<Value>,
}

/// Return a discussion row only after strict Content visibility has been established. Numeric
/// News IDs are never consulted as a compatibility fallback.
pub async fn find_visible_content_discussion(
    pool: &PgPool,
    user_id: i64,
    content_id: i64,
) -> Result<Option<ContentDiscussionProjection>, DiscussionRepositoryError> {
    let Some(content) = find_visible_content_detail(pool, user_id, content_id).await? else {
        return Ok(None);
    };
    let row = sqlx::query_as::<_, ContentDiscussionRow>(
        r#"
        SELECT to_jsonb(discussion) AS stored_discussion
        FROM (SELECT $1::bigint AS requested_content_id) AS requested
        LEFT JOIN content_discussions AS discussion
          ON discussion.content_id::bigint = requested.requested_content_id
        "#,
    )
    .bind(content_id)
    .fetch_one(pool)
    .await?;
    let discussion_url = content
        .content_metadata
        .get("discussion_url")
        .and_then(Value::as_str)
        .map(str::to_owned);
    let platform = content
        .content_metadata
        .get("platform")
        .and_then(Value::as_str)
        .map(str::to_owned)
        .or(content.platform);
    Ok(Some(ContentDiscussionProjection {
        content_id,
        platform,
        discussion_url,
        stored_discussion: row.stored_discussion,
    }))
}

/// Return discussion state only after the canonical News visibility predicate succeeds. The
/// legacy Content row is used solely as a payload source for an already-authorized News item.
pub async fn find_visible_news_discussion(
    pool: &PgPool,
    user_id: i64,
    news_item_id: i64,
) -> Result<Option<NewsDiscussionProjection>, DiscussionRepositoryError> {
    let Some(news) = find_visible_news_item_detail(pool, user_id, news_item_id).await? else {
        return Ok(None);
    };
    let row = sqlx::query_as::<_, NewsDiscussionRow>(
        r#"
        SELECT
            to_jsonb(news_discussion) AS stored_news_discussion,
            to_jsonb(legacy_discussion) AS stored_legacy_discussion
        FROM news_items AS news
        LEFT JOIN news_item_discussions AS news_discussion
          ON news_discussion.news_item_id = news.id
        LEFT JOIN content_discussions AS legacy_discussion
          ON legacy_discussion.content_id = news.legacy_content_id
        WHERE news.id::bigint = $1::bigint
        "#,
    )
    .bind(news_item_id)
    .fetch_optional(pool)
    .await?
    .unwrap_or(NewsDiscussionRow {
        stored_news_discussion: None,
        stored_legacy_discussion: None,
    });
    let embedded_discussion = news
        .raw_metadata
        .get("discussion_payload")
        .filter(|value| value.is_object())
        .cloned();
    let embedded_status = json_string(&news.raw_metadata, "discussion_status");
    let embedded_error = json_string(&news.raw_metadata, "discussion_error");
    let embedded_fetched_at = json_string(&news.raw_metadata, "discussion_fetched_at");
    Ok(Some(NewsDiscussionProjection {
        news_item_id,
        platform: news.platform,
        discussion_url: news.discussion_url.or(news.canonical_item_url),
        stored_news_discussion: row.stored_news_discussion,
        stored_legacy_discussion: row.stored_legacy_discussion,
        embedded_discussion,
        embedded_status,
        embedded_error,
        embedded_fetched_at,
    }))
}

fn json_string(value: &Value, key: &str) -> Option<String> {
    value.get(key).and_then(|entry| {
        entry
            .as_str()
            .map(str::to_owned)
            .or_else(|| (!entry.is_null()).then(|| entry.to_string()))
    })
}

#[derive(Debug, Error)]
pub enum DiscussionRepositoryError {
    #[error("PostgreSQL discussion read-model query failed")]
    Sqlx(#[from] sqlx::Error),
    #[error("Content or News visibility query failed")]
    ContentRead(#[from] crate::content_read::ContentReadRepositoryError),
}
