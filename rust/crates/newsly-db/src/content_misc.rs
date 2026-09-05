use chrono::{DateTime, NaiveDateTime, Utc};
use serde_json::Value;
use sqlx::{Acquire, FromRow, PgPool, Postgres, Transaction};
use thiserror::Error;

use crate::content_read::{
    ContentDetailProjection, ContentReadRepositoryError, find_visible_content_detail,
    find_visible_news_item_detail,
};

#[derive(Debug, Clone, PartialEq)]
pub struct ContentConversionPlan {
    pub original_content_id: i64,
    pub article_url: String,
    pub title: Option<String>,
    pub source: Option<String>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct NewsConversionPlan {
    pub news_item_id: i64,
    pub article_url: String,
    pub title: Option<String>,
    pub source: Option<String>,
    pub published_at: Option<DateTime<Utc>>,
    pub raw_metadata: Value,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ConvertedArticle {
    pub content_id: i64,
    pub already_exists: bool,
    pub reused_body: bool,
}

pub async fn prepare_content_conversion(
    pool: &PgPool,
    user_id: i64,
    content_id: i64,
) -> Result<Option<ContentConversionPlan>, ContentMiscRepositoryError> {
    let Some(content) = find_visible_content_detail(pool, user_id, content_id).await? else {
        return Ok(None);
    };
    if content.content_type != "news" {
        return Err(ContentMiscRepositoryError::NotNewsContent);
    }
    let article = content
        .content_metadata
        .get("article")
        .and_then(Value::as_object);
    let article_url = clean_string(Some(&Value::String(content.url.clone())))
        .or_else(|| article.and_then(|value| clean_string(value.get("url"))))
        .ok_or(ContentMiscRepositoryError::ArticleUrlMissing)?;
    Ok(Some(ContentConversionPlan {
        original_content_id: content_id,
        article_url,
        title: article.and_then(|value| clean_string(value.get("title"))),
        source: article.and_then(|value| clean_string(value.get("source_domain"))),
    }))
}

pub async fn prepare_news_conversion(
    pool: &PgPool,
    user_id: i64,
    news_item_id: i64,
) -> Result<Option<NewsConversionPlan>, ContentMiscRepositoryError> {
    let Some(news) = find_visible_news_item_detail(pool, user_id, news_item_id).await? else {
        return Ok(None);
    };
    let article_url = news
        .article_url
        .clone()
        .or_else(|| news.canonical_story_url.clone())
        .ok_or(ContentMiscRepositoryError::ArticleUrlMissing)?;
    let title = news
        .raw_metadata
        .get("article_title")
        .and_then(value_string)
        .or_else(|| {
            news.raw_metadata
                .get("article")
                .and_then(Value::as_object)
                .and_then(|value| clean_string(value.get("title")))
        });
    Ok(Some(NewsConversionPlan {
        news_item_id,
        article_url,
        title,
        source: news.article_domain,
        published_at: news.published_at,
        raw_metadata: news.raw_metadata,
    }))
}

pub async fn finalize_article_conversion(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
    article_url: &str,
    title: Option<&str>,
    source: Option<&str>,
    published_at: Option<DateTime<Utc>>,
    news_metadata: Option<&Value>,
) -> Result<ConvertedArticle, ContentMiscRepositoryError> {
    let inserted = sqlx::query_scalar::<_, i64>(
        r#"
        INSERT INTO contents (
            content_type, url, source_url, title, source, platform, is_aggregate,
            status, retry_count, classification, content_metadata,
            created_at, updated_at, publication_date
        )
        VALUES (
            'article', $1, $1, $2, $3, NULL, FALSE,
            'pending', 0, NULL, '{}'::jsonb,
            timezone('UTC', now()), timezone('UTC', now()), $4
        )
        ON CONFLICT (url, content_type) DO NOTHING
        RETURNING id::bigint
        "#,
    )
    .bind(article_url)
    .bind(title)
    .bind(source)
    .bind(published_at.map(|value| value.naive_utc()))
    .fetch_optional(&mut **transaction)
    .await?;
    let (content_id, already_exists) = if let Some(content_id) = inserted {
        (content_id, false)
    } else {
        let content_id = sqlx::query_scalar::<_, i64>(
            "SELECT id::bigint FROM contents WHERE url = $1 AND content_type = 'article' ORDER BY id LIMIT 1",
        )
        .bind(article_url)
        .fetch_one(&mut **transaction)
        .await?;
        (content_id, true)
    };

    let reused_body = if already_exists {
        false
    } else {
        reuse_news_body(transaction, content_id, news_metadata).await?
    };
    if reused_body {
        sqlx::query(
            "UPDATE contents SET status = 'processing', updated_at = timezone('UTC', now()) WHERE id::bigint = $1",
        )
        .bind(content_id)
        .execute(&mut **transaction)
        .await?;
    }
    sqlx::query(
        r#"
        INSERT INTO content_knowledge_saves (user_id, content_id, saved_at, created_at)
        VALUES ($1::bigint::integer, $2::bigint::integer, timezone('UTC', now()), timezone('UTC', now()))
        ON CONFLICT (user_id, content_id) DO NOTHING
        "#,
    )
    .bind(user_id)
    .bind(content_id)
    .execute(&mut **transaction)
    .await?;
    Ok(ConvertedArticle {
        content_id,
        already_exists,
        reused_body,
    })
}

async fn reuse_news_body(
    transaction: &mut Transaction<'_, Postgres>,
    content_id: i64,
    metadata: Option<&Value>,
) -> Result<bool, sqlx::Error> {
    let Some(body_ref) = metadata
        .and_then(|value| value.get("article_body_ref"))
        .and_then(Value::as_object)
    else {
        return Ok(false);
    };
    match body_ref.get("kind").and_then(Value::as_str) {
        Some("content") => {
            let Some(source_content_id) = json_i64(body_ref.get("content_id")) else {
                return Ok(false);
            };
            let copied = sqlx::query(
                r#"
                INSERT INTO content_bodies (
                    content_id, variant, storage_provider, storage_bucket, storage_key,
                    content_format, sha256, byte_size, char_count, created_at, updated_at
                )
                SELECT
                    $1::bigint::integer, variant, storage_provider, storage_bucket, storage_key,
                    content_format, sha256, byte_size, char_count,
                    timezone('UTC', now()), timezone('UTC', now())
                FROM content_bodies
                WHERE content_id::bigint = $2::bigint
                ON CONFLICT (content_id, variant) DO NOTHING
                "#,
            )
            .bind(content_id)
            .bind(source_content_id)
            .execute(&mut **transaction)
            .await?
            .rows_affected();
            Ok(copied > 0)
        }
        Some("storage") => {
            let Some(storage_key) = body_ref.get("storage_key").and_then(value_string) else {
                return Ok(false);
            };
            let content_format = body_ref
                .get("content_format")
                .and_then(value_string)
                .unwrap_or_else(|| "text".to_owned());
            let inserted = sqlx::query(
                r#"
                INSERT INTO content_bodies (
                    content_id, variant, storage_provider, storage_bucket, storage_key,
                    content_format, sha256, byte_size, char_count, created_at, updated_at
                )
                VALUES (
                    $1::bigint::integer, 'source', $2, $3, $4,
                    $5, $6, $7::bigint::integer, $8::bigint::integer,
                    timezone('UTC', now()), timezone('UTC', now())
                )
                ON CONFLICT (content_id, variant) DO NOTHING
                "#,
            )
            .bind(content_id)
            .bind(
                body_ref
                    .get("storage_provider")
                    .and_then(value_string)
                    .unwrap_or_else(|| "local".to_owned()),
            )
            .bind(body_ref.get("storage_bucket").and_then(value_string))
            .bind(storage_key)
            .bind(content_format)
            .bind(
                body_ref
                    .get("sha256")
                    .and_then(value_string)
                    .unwrap_or_else(|| "0".repeat(64)),
            )
            .bind(json_i64(body_ref.get("byte_size")).unwrap_or(0))
            .bind(json_i64(body_ref.get("char_count")).unwrap_or(0))
            .execute(&mut **transaction)
            .await?
            .rows_affected();
            Ok(inserted > 0)
        }
        _ => Ok(false),
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct ContentNarrationPlan {
    pub content: ContentDetailProjection,
}

pub async fn prepare_content_narration(
    pool: &PgPool,
    user_id: i64,
    content_id: i64,
) -> Result<Option<ContentNarrationPlan>, ContentMiscRepositoryError> {
    Ok(find_visible_content_detail(pool, user_id, content_id)
        .await?
        .map(|content| ContentNarrationPlan { content }))
}

#[derive(Debug, Clone, PartialEq)]
pub struct TweetContentPlan {
    pub id: i64,
    pub url: String,
    pub title: String,
    pub source: Option<String>,
    pub content_type: String,
    pub status: String,
    pub metadata: Value,
}

pub async fn prepare_tweet_content(
    pool: &PgPool,
    user_id: i64,
    content_id: i64,
) -> Result<Option<TweetContentPlan>, ContentMiscRepositoryError> {
    let Some(content) = find_visible_content_detail(pool, user_id, content_id).await? else {
        return Ok(None);
    };
    Ok(Some(TweetContentPlan {
        id: content.id,
        url: content.url,
        title: content
            .title
            .filter(|value| !value.trim().is_empty())
            .unwrap_or_else(|| format!("Content {}", content.id)),
        source: content.source,
        content_type: content.content_type,
        status: content.status,
        metadata: content.content_metadata,
    }))
}

#[derive(Debug, Clone, PartialEq)]
pub struct FeedBackfillPlan {
    pub content_id: i64,
    pub config_id: i64,
    pub scraper_type: String,
    pub display_name: Option<String>,
    pub feed_url: String,
    pub base_limit: usize,
    pub target_limit: usize,
}

#[derive(Debug, Clone, PartialEq)]
pub enum FeedBackfillPreparation {
    Ready(FeedBackfillPlan),
    ContentNotFound,
    ContentNotAccessible,
    NotLongForm,
    FeedConfigNotFound,
}

#[derive(Debug, FromRow)]
struct FeedContentRow {
    content_type: String,
    source: Option<String>,
    content_metadata: Value,
    is_accessible: bool,
}

#[derive(Debug, FromRow)]
struct FeedConfigRow {
    id: i64,
    scraper_type: String,
    display_name: Option<String>,
    feed_url: Option<String>,
    config: Value,
}

pub async fn prepare_feed_backfill(
    pool: &PgPool,
    user_id: i64,
    content_id: i64,
    count: usize,
) -> Result<FeedBackfillPreparation, ContentMiscRepositoryError> {
    let content = sqlx::query_as::<_, FeedContentRow>(
        r#"
        SELECT
            content.content_type,
            content.source,
            content.content_metadata::jsonb AS content_metadata,
            EXISTS (
                SELECT 1 FROM content_status AS user_status
                WHERE user_status.user_id::bigint = $1::bigint
                  AND user_status.content_id = content.id
                  AND user_status.status = 'inbox'
            ) OR EXISTS (
                SELECT 1 FROM content_knowledge_saves AS knowledge
                WHERE knowledge.user_id::bigint = $1::bigint
                  AND knowledge.content_id = content.id
            ) AS is_accessible
        FROM contents AS content
        WHERE content.id::bigint = $2::bigint
        "#,
    )
    .bind(user_id)
    .bind(content_id)
    .fetch_optional(pool)
    .await?;
    let Some(content) = content else {
        return Ok(FeedBackfillPreparation::ContentNotFound);
    };
    if !matches!(content.content_type.as_str(), "article" | "podcast") {
        return Ok(FeedBackfillPreparation::NotLongForm);
    }
    if !content.is_accessible {
        return Ok(FeedBackfillPreparation::ContentNotAccessible);
    }
    let config = resolve_feed_config(pool, user_id, &content).await?;
    let Some(config) = config else {
        return Ok(FeedBackfillPreparation::FeedConfigNotFound);
    };
    let feed_url = config
        .feed_url
        .clone()
        .or_else(|| config.config.get("feed_url").and_then(value_string))
        .ok_or(ContentMiscRepositoryError::FeedUrlMissing)?;
    let base_limit = config
        .config
        .get("limit")
        .and_then(Value::as_u64)
        .and_then(|value| usize::try_from(value).ok())
        .filter(|value| (1..=100).contains(value))
        .unwrap_or(10);
    Ok(FeedBackfillPreparation::Ready(FeedBackfillPlan {
        content_id,
        config_id: config.id,
        scraper_type: config.scraper_type,
        display_name: config.display_name,
        feed_url,
        base_limit,
        target_limit: base_limit.saturating_add(count).min(100),
    }))
}

async fn resolve_feed_config(
    pool: &PgPool,
    user_id: i64,
    content: &FeedContentRow,
) -> Result<Option<FeedConfigRow>, sqlx::Error> {
    if let Some(config_id) = json_i64(content.content_metadata.get("feed_config_id")) {
        let row = sqlx::query_as::<_, FeedConfigRow>(
            r#"
            SELECT id::bigint, scraper_type, display_name, feed_url, config::jsonb AS config
            FROM user_scraper_configs
            WHERE id::bigint = $1 AND user_id::bigint = $2 AND is_active = TRUE
            "#,
        )
        .bind(config_id)
        .bind(user_id)
        .fetch_optional(pool)
        .await?;
        if row.is_some() {
            return Ok(row);
        }
    }
    if let Some(feed_url) = content
        .content_metadata
        .get("feed_url")
        .and_then(value_string)
    {
        let row = sqlx::query_as::<_, FeedConfigRow>(
            r#"
            SELECT id::bigint, scraper_type, display_name, feed_url, config::jsonb AS config
            FROM user_scraper_configs
            WHERE user_id::bigint = $1 AND is_active = TRUE
              AND lower(trim(trailing '/' from coalesce(feed_url, config::jsonb ->> 'feed_url', '')))
                    = lower(trim(trailing '/' from $2::text))
            ORDER BY id
            LIMIT 1
            "#,
        )
        .bind(user_id)
        .bind(feed_url)
        .fetch_optional(pool)
        .await?;
        if row.is_some() {
            return Ok(row);
        }
    }
    let Some(source) = content
        .source
        .as_deref()
        .map(str::trim)
        .filter(|value| !value.is_empty())
    else {
        return Ok(None);
    };
    sqlx::query_as::<_, FeedConfigRow>(
        r#"
        SELECT id::bigint, scraper_type, display_name, feed_url, config::jsonb AS config
        FROM user_scraper_configs
        WHERE user_id::bigint = $1 AND is_active = TRUE
          AND (
              lower(coalesce(display_name, '')) = lower($2)
              OR lower(coalesce(config::jsonb ->> 'name', '')) = lower($2)
          )
        ORDER BY id
        LIMIT 1
        "#,
    )
    .bind(user_id)
    .bind(source)
    .fetch_optional(pool)
    .await
}

#[derive(Debug, Clone, PartialEq)]
pub struct FeedBackfillEntry {
    pub url: String,
    pub source_url: String,
    pub title: Option<String>,
    pub source: Option<String>,
    pub platform: String,
    pub metadata: Value,
    pub published_at: Option<DateTime<Utc>>,
    pub content_type: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FeedBackfillPersistence {
    pub saved: usize,
    pub duplicates: usize,
    pub rejected: usize,
    pub content_ids: Vec<i64>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum FeedBackfillOrigin {
    Background,
    DownloadMore,
}

impl FeedBackfillOrigin {
    const fn as_str(self) -> &'static str {
        match self {
            Self::Background => "feed_backfill",
            Self::DownloadMore => "download_more",
        }
    }
}

pub async fn persist_feed_backfill(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
    origin: FeedBackfillOrigin,
    entries: &[FeedBackfillEntry],
) -> Result<FeedBackfillPersistence, ContentMiscRepositoryError> {
    let mut content_ids = Vec::new();
    let mut duplicates = 0;
    let mut rejected = 0;
    for entry in entries {
        let mut item_tx = transaction.begin().await?;
        match persist_backfill_entry(&mut item_tx, user_id, origin, entry).await {
            Ok((id, created)) => {
                item_tx.commit().await?;
                if created {
                    content_ids.push(id);
                } else {
                    duplicates += 1;
                }
            }
            Err(error)
                if error.as_database_error().is_some_and(|e| {
                    e.code()
                        .is_some_and(|c| c.starts_with("22") || c.starts_with("23"))
                }) =>
            {
                item_tx.rollback().await?;
                rejected += 1;
                tracing::warn!(user_id, error_code = ?error.as_database_error().and_then(sqlx::error::DatabaseError::code), "feed entry rejected; siblings preserved");
            }
            Err(error) => return Err(error.into()),
        }
    }
    Ok(FeedBackfillPersistence {
        saved: content_ids.len(),
        duplicates,
        rejected,
        content_ids,
    })
}

async fn persist_backfill_entry(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
    origin: FeedBackfillOrigin,
    entry: &FeedBackfillEntry,
) -> Result<(i64, bool), sqlx::Error> {
    let mut metadata = entry.metadata.clone();
    if let Some(object) = metadata.as_object_mut() {
        object.insert(
            "submitted_via".to_owned(),
            Value::String(origin.as_str().to_owned()),
        );
    }
    let inserted = sqlx::query_scalar::<_, i64>(
        r#"
            INSERT INTO contents (
                content_type, url, source_url, title, source, platform, is_aggregate,
                status, retry_count, classification, content_metadata,
                created_at, updated_at, publication_date
            )
            VALUES (
                $1, $2, $3, $4, $5, $6, FALSE,
                'pending', 0, NULL, $7,
                timezone('UTC', now()), timezone('UTC', now()), $8
            )
            ON CONFLICT (url, content_type) DO NOTHING
            RETURNING id::bigint
            "#,
    )
    .bind(&entry.content_type)
    .bind(&entry.url)
    .bind(&entry.source_url)
    .bind(&entry.title)
    .bind(&entry.source)
    .bind(&entry.platform)
    .bind(metadata)
    .bind(entry.published_at.map(|value| value.naive_utc()))
    .fetch_optional(&mut **transaction)
    .await?;
    let (content_id, was_created) = if let Some(content_id) = inserted {
        (content_id, true)
    } else {
        let content_id = sqlx::query_scalar::<_, i64>(
            r#"
                SELECT id::bigint
                FROM contents
                WHERE url = $1 AND content_type = $2
                ORDER BY id
                LIMIT 1
                "#,
        )
        .bind(&entry.url)
        .bind(&entry.content_type)
        .fetch_one(&mut **transaction)
        .await?;
        (content_id, false)
    };
    sqlx::query(
            r#"
            INSERT INTO content_status (user_id, content_id, status, created_at, updated_at)
            VALUES ($1::bigint::integer, $2::bigint::integer, 'inbox', timezone('UTC', now()), timezone('UTC', now()))
            ON CONFLICT (user_id, content_id) DO NOTHING
            "#,
        )
        .bind(user_id)
        .bind(content_id)
        .execute(&mut **transaction)
        .await?;
    Ok((content_id, was_created))
}

#[derive(Debug, Clone, PartialEq)]
pub struct SubmissionProjection {
    pub id: i64,
    pub mode: String,
    pub task_status: String,
    pub task_error: Option<String>,
    pub input: Value,
    pub output: Value,
    pub created_at: DateTime<Utc>,
    pub completed_at: Option<DateTime<Utc>>,
    pub action_name: Option<String>,
    pub action_status: Option<String>,
    pub action_input: Option<Value>,
    pub action_result: Option<Value>,
    pub action_rationale: Option<String>,
    pub action_error: Option<String>,
    pub action_completed_at: Option<DateTime<Utc>>,
    pub content_id: Option<i64>,
    pub content_type: Option<String>,
    pub content_url: Option<String>,
    pub content_source_url: Option<String>,
    pub content_title: Option<String>,
    pub content_status: Option<String>,
    pub content_error: Option<String>,
    pub content_metadata: Option<Value>,
    pub content_processed_at: Option<DateTime<Utc>>,
}

#[derive(Debug, FromRow)]
struct SubmissionRow {
    id: i64,
    mode: String,
    task_status: String,
    task_error: Option<String>,
    input: Value,
    output: Value,
    created_at: NaiveDateTime,
    completed_at: Option<NaiveDateTime>,
    action_name: Option<String>,
    action_status: Option<String>,
    action_input: Option<Value>,
    action_result: Option<Value>,
    action_rationale: Option<String>,
    action_error: Option<String>,
    action_completed_at: Option<NaiveDateTime>,
    content_id: Option<i64>,
    content_type: Option<String>,
    content_url: Option<String>,
    content_source_url: Option<String>,
    content_title: Option<String>,
    content_status: Option<String>,
    content_error: Option<String>,
    content_metadata: Option<Value>,
    content_processed_at: Option<NaiveDateTime>,
}

impl From<SubmissionRow> for SubmissionProjection {
    fn from(row: SubmissionRow) -> Self {
        Self {
            id: row.id,
            mode: row.mode,
            task_status: row.task_status,
            task_error: row.task_error,
            input: row.input,
            output: row.output,
            created_at: row.created_at.and_utc(),
            completed_at: row.completed_at.map(|value| value.and_utc()),
            action_name: row.action_name,
            action_status: row.action_status,
            action_input: row.action_input,
            action_result: row.action_result,
            action_rationale: row.action_rationale,
            action_error: row.action_error,
            action_completed_at: row.action_completed_at.map(|value| value.and_utc()),
            content_id: row.content_id,
            content_type: row.content_type,
            content_url: row.content_url,
            content_source_url: row.content_source_url,
            content_title: row.content_title,
            content_status: row.content_status,
            content_error: row.content_error,
            content_metadata: row.content_metadata,
            content_processed_at: row.content_processed_at.map(|value| value.and_utc()),
        }
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct SubmissionPage {
    pub items: Vec<SubmissionProjection>,
    pub has_more: bool,
}

pub async fn list_submission_projections(
    pool: &PgPool,
    user_id: i64,
    cursor: Option<(DateTime<Utc>, i64)>,
    limit: usize,
) -> Result<SubmissionPage, ContentMiscRepositoryError> {
    let requested = i64::try_from(limit.saturating_add(1)).unwrap_or(i64::MAX);
    let rows = sqlx::query_as::<_, SubmissionRow>(
        r#"
        SELECT
            task.id::bigint AS id,
            task.mode,
            task.status AS task_status,
            task.error_message AS task_error,
            task.input_json::jsonb AS input,
            task.output_json::jsonb AS output,
            task.created_at,
            task.completed_at,
            action.action_name,
            action.action_status,
            action.action_input::jsonb AS action_input,
            action.action_result::jsonb AS action_result,
            action.rationale AS action_rationale,
            action.error_message AS action_error,
            action.completed_at AS action_completed_at,
            content.id::bigint AS content_id,
            content.content_type,
            content.url AS content_url,
            content.source_url AS content_source_url,
            content.title AS content_title,
            content.status AS content_status,
            content.error_message AS content_error,
            content.content_metadata::jsonb AS content_metadata,
            content.processed_at AS content_processed_at
        FROM llm_tasks AS task
        LEFT JOIN LATERAL (
            SELECT candidate.*
            FROM llm_task_actions AS candidate
            WHERE candidate.llm_task_id = task.id
            ORDER BY (candidate.action_status = 'applied') DESC, candidate.created_at DESC, candidate.id DESC
            LIMIT 1
        ) AS action ON TRUE
        LEFT JOIN contents AS content
          ON coalesce(action.action_result::jsonb ->> 'content_id', '') ~ '^[0-9]+$'
         AND content.id::bigint = (action.action_result::jsonb ->> 'content_id')::bigint
        WHERE task.user_id::bigint = $1::bigint
          AND task.task_kind = 'share_action'
          AND (
              $2::timestamp IS NULL
              OR task.created_at < $2::timestamp
              OR (task.created_at = $2::timestamp AND task.id::bigint < $3::bigint)
          )
        ORDER BY task.created_at DESC, task.id DESC
        LIMIT $4::bigint
        "#,
    )
    .bind(user_id)
    .bind(cursor.map(|value| value.0.naive_utc()))
    .bind(cursor.map(|value| value.1))
    .bind(requested)
    .fetch_all(pool)
    .await?;
    let has_more = rows.len() > limit;
    Ok(SubmissionPage {
        items: rows.into_iter().take(limit).map(Into::into).collect(),
        has_more,
    })
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DiscussionTargetKind {
    Content,
    NewsItem,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DiscussionRefreshPlan {
    pub kind: DiscussionTargetKind,
    pub id: i64,
    pub platform: Option<String>,
    pub discussion_url: Option<String>,
    pub external_id: Option<String>,
}

pub async fn prepare_content_discussion_refresh(
    pool: &PgPool,
    user_id: i64,
    content_id: i64,
) -> Result<Option<DiscussionRefreshPlan>, ContentMiscRepositoryError> {
    let Some(content) = find_visible_content_detail(pool, user_id, content_id).await? else {
        return Ok(None);
    };
    Ok(Some(DiscussionRefreshPlan {
        kind: DiscussionTargetKind::Content,
        id: content_id,
        platform: content
            .content_metadata
            .get("platform")
            .and_then(value_string)
            .or(content.platform),
        discussion_url: content
            .content_metadata
            .get("discussion_url")
            .and_then(value_string),
        external_id: content
            .content_metadata
            .get("external_id")
            .and_then(value_string),
    }))
}

pub async fn prepare_news_discussion_refresh(
    pool: &PgPool,
    user_id: i64,
    news_item_id: i64,
) -> Result<Option<DiscussionRefreshPlan>, ContentMiscRepositoryError> {
    let Some(news) = find_visible_news_item_detail(pool, user_id, news_item_id).await? else {
        return Ok(None);
    };
    Ok(Some(DiscussionRefreshPlan {
        kind: DiscussionTargetKind::NewsItem,
        id: news_item_id,
        platform: news.platform,
        discussion_url: news.discussion_url.or(news.canonical_item_url),
        external_id: news
            .raw_metadata
            .get("external_id")
            .and_then(value_string)
            .or_else(|| {
                news.raw_metadata
                    .get("source_external_id")
                    .and_then(value_string)
            }),
    }))
}

pub async fn persist_content_discussion(
    transaction: &mut Transaction<'_, Postgres>,
    plan: &DiscussionRefreshPlan,
    status: &str,
    discussion_data: &Value,
    error_message: Option<&str>,
) -> Result<(), ContentMiscRepositoryError> {
    sqlx::query(
        r#"
        INSERT INTO content_discussions (
            content_id, platform, status, discussion_data, error_message,
            created_at, updated_at, fetched_at
        )
        VALUES (
            $1::bigint::integer, $2, $3, $4, $5,
            timezone('UTC', now()), timezone('UTC', now()), timezone('UTC', now())
        )
        ON CONFLICT (content_id) DO UPDATE SET
            platform = EXCLUDED.platform,
            status = EXCLUDED.status,
            discussion_data = EXCLUDED.discussion_data,
            error_message = EXCLUDED.error_message,
            updated_at = EXCLUDED.updated_at,
            fetched_at = EXCLUDED.fetched_at
        "#,
    )
    .bind(plan.id)
    .bind(&plan.platform)
    .bind(status)
    .bind(discussion_data)
    .bind(error_message)
    .execute(&mut **transaction)
    .await?;
    Ok(())
}

pub async fn persist_news_discussion(
    transaction: &mut Transaction<'_, Postgres>,
    plan: &DiscussionRefreshPlan,
    status: &str,
    discussion_data: &Value,
    error_message: Option<&str>,
) -> Result<(), ContentMiscRepositoryError> {
    let comments = discussion_data
        .get("comments")
        .cloned()
        .unwrap_or_else(|| Value::Array(Vec::new()));
    let comment_count = comments
        .as_array()
        .and_then(|values| i64::try_from(values.len()).ok());
    sqlx::query(
        r#"
        INSERT INTO news_item_discussions (
            news_item_id, platform, external_id, discussion_url,
            comment_count, raw_comments_ref, fetched_comment_count,
            last_comments_fetched_at, summary_status,
            last_refresh_status, last_refresh_error, created_at, updated_at
        )
        VALUES (
            $1::bigint::integer, coalesce($2, 'unknown'), $3, $4,
            $5::bigint::integer, $6, $5::bigint::integer,
            timezone('UTC', now()), 'not_ready',
            $7, $8, timezone('UTC', now()), timezone('UTC', now())
        )
        ON CONFLICT (news_item_id) DO UPDATE SET
            platform = EXCLUDED.platform,
            external_id = EXCLUDED.external_id,
            discussion_url = EXCLUDED.discussion_url,
            comment_count = EXCLUDED.comment_count,
            raw_comments_ref = EXCLUDED.raw_comments_ref,
            fetched_comment_count = EXCLUDED.fetched_comment_count,
            last_comments_fetched_at = EXCLUDED.last_comments_fetched_at,
            last_refresh_status = EXCLUDED.last_refresh_status,
            last_refresh_error = EXCLUDED.last_refresh_error,
            updated_at = EXCLUDED.updated_at
        "#,
    )
    .bind(plan.id)
    .bind(&plan.platform)
    .bind(&plan.external_id)
    .bind(&plan.discussion_url)
    .bind(comment_count)
    .bind(comments)
    .bind(status)
    .bind(error_message)
    .execute(&mut **transaction)
    .await?;
    Ok(())
}

fn clean_string(value: Option<&Value>) -> Option<String> {
    value.and_then(value_string)
}

fn value_string(value: &Value) -> Option<String> {
    value
        .as_str()
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(str::to_owned)
}

fn json_i64(value: Option<&Value>) -> Option<i64> {
    value.and_then(|value| {
        value.as_i64().or_else(|| {
            value
                .as_str()
                .filter(|value| value.chars().all(|character| character.is_ascii_digit()))
                .and_then(|value| value.parse().ok())
        })
    })
}

#[derive(Debug, Error)]
pub enum ContentMiscRepositoryError {
    #[error("PostgreSQL content operation failed")]
    Sqlx(#[from] sqlx::Error),
    #[error("content visibility query failed")]
    ContentRead(#[from] ContentReadRepositoryError),
    #[error("only news content can be converted to an article")]
    NotNewsContent,
    #[error("no article URL was found")]
    ArticleUrlMissing,
    #[error("feed config has no feed URL")]
    FeedUrlMissing,
}

#[cfg(test)]
mod tests;

/// Include every existing membership, including archived content, when catching up a feed.
pub async fn known_feed_urls(
    pool: &PgPool,
    user_id: i64,
    content_type: &str,
) -> Result<std::collections::BTreeSet<String>, sqlx::Error> {
    Ok(sqlx::query_scalar::<_, String>("SELECT content.url FROM contents AS content JOIN content_status AS membership ON membership.content_id = content.id WHERE membership.user_id::bigint = $1 AND content.content_type = $2")
        .bind(user_id).bind(content_type).fetch_all(pool).await?.into_iter().collect())
}
