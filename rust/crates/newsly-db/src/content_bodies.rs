use chrono::{DateTime, NaiveDateTime, Utc};
use serde_json::{Map, Value};
use sqlx::{FromRow, PgPool};
use thiserror::Error;

use crate::content_read::{find_visible_content_detail, find_visible_news_item_detail};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ContentBodyVariant {
    Source,
    Rendered,
}

impl ContentBodyVariant {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Source => "source",
            Self::Rendered => "rendered",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ContentBodyPointer {
    pub storage_provider: String,
    pub storage_bucket: Option<String>,
    pub storage_key: String,
    pub content_format: String,
    pub updated_at: Option<DateTime<Utc>>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ContentBodyProjection {
    pub response_content_id: i64,
    pub content_type: String,
    pub title: Option<String>,
    pub source: Option<String>,
    pub publication_date: Option<DateTime<Utc>>,
    pub metadata: Value,
    pub variant: ContentBodyVariant,
    pub kind: String,
    pub fallback_format: Option<String>,
    pub fallback_text: Option<String>,
    pub fallback_updated_at: Option<DateTime<Utc>>,
    pub pointer: Option<ContentBodyPointer>,
}

#[derive(Debug, FromRow)]
struct BodyPointerRow {
    storage_provider: String,
    storage_bucket: Option<String>,
    storage_key: String,
    content_format: String,
    updated_at: Option<NaiveDateTime>,
}

impl From<BodyPointerRow> for ContentBodyPointer {
    fn from(row: BodyPointerRow) -> Self {
        Self {
            storage_provider: row.storage_provider,
            storage_bucket: row.storage_bucket,
            storage_key: row.storage_key,
            content_format: row.content_format,
            updated_at: row.updated_at.map(|value| value.and_utc()),
        }
    }
}

#[derive(Debug, FromRow)]
struct ArticleContentRow {
    id: i64,
    content_type: String,
    content_metadata: Value,
    updated_at: Option<NaiveDateTime>,
}

pub async fn find_visible_content_body(
    pool: &PgPool,
    user_id: i64,
    content_id: i64,
    variant: ContentBodyVariant,
) -> Result<Option<ContentBodyProjection>, ContentBodyRepositoryError> {
    let Some(content) = find_visible_content_detail(pool, user_id, content_id).await? else {
        return Ok(None);
    };
    let pointer = find_pointer(pool, content_id, variant).await?;
    let fallback = fallback_body(
        &content.content_type,
        content.content_metadata.as_object(),
        variant,
    );
    let kind = body_kind(&content.content_type).to_owned();
    Ok(Some(ContentBodyProjection {
        response_content_id: content_id,
        content_type: content.content_type,
        title: content.title,
        source: content.source,
        publication_date: content.publication_date,
        metadata: content.content_metadata,
        variant,
        kind,
        fallback_format: fallback.as_ref().map(|(format, _)| (*format).to_owned()),
        fallback_text: fallback.map(|(_, text)| text),
        fallback_updated_at: content.updated_at,
        pointer,
    }))
}

pub async fn find_visible_news_item_body(
    pool: &PgPool,
    user_id: i64,
    news_item_id: i64,
    variant: ContentBodyVariant,
) -> Result<Option<ContentBodyProjection>, ContentBodyRepositoryError> {
    let Some(news) = find_visible_news_item_detail(pool, user_id, news_item_id).await? else {
        return Ok(None);
    };

    if let Some(body_ref) = news
        .raw_metadata
        .get("article_body_ref")
        .and_then(Value::as_object)
    {
        match body_ref.get("kind").and_then(Value::as_str) {
            Some("content") => {
                if let Some(content_id) = body_ref.get("content_id").and_then(Value::as_i64)
                    && let Some(projection) =
                        load_article_body(pool, content_id, news_item_id, variant).await?
                {
                    return Ok(Some(projection));
                }
            }
            Some("storage") if variant == ContentBodyVariant::Source => {
                if let Some(storage_key) = clean_string(body_ref.get("storage_key")) {
                    let pointer = ContentBodyPointer {
                        storage_provider: clean_string(body_ref.get("storage_provider"))
                            .unwrap_or_else(|| "local".to_owned()),
                        storage_bucket: clean_string(body_ref.get("storage_bucket")),
                        storage_key,
                        content_format: clean_string(body_ref.get("content_format"))
                            .filter(|value| matches!(value.as_str(), "text" | "markdown"))
                            .unwrap_or_else(|| "text".to_owned()),
                        updated_at: body_ref
                            .get("updated_at")
                            .and_then(Value::as_str)
                            .and_then(parse_datetime),
                    };
                    return Ok(Some(ContentBodyProjection {
                        response_content_id: news_item_id,
                        content_type: "news".to_owned(),
                        title: None,
                        source: news.source_label,
                        publication_date: news.published_at,
                        metadata: news.raw_metadata,
                        variant,
                        kind: "article".to_owned(),
                        fallback_format: None,
                        fallback_text: None,
                        fallback_updated_at: None,
                        pointer: Some(pointer),
                    }));
                }
            }
            _ => {}
        }
    }

    let article_url = news.article_url.or(news.canonical_story_url);
    let Some(article_url) = article_url else {
        return Ok(Some(empty_news_projection(news_item_id, variant)));
    };
    let article_id = sqlx::query_scalar::<_, i64>(
        r#"
        SELECT content.id::bigint
        FROM contents AS content
        WHERE content.content_type = 'article'
          AND (content.url = $1 OR content.source_url = $1)
        ORDER BY content.id ASC
        LIMIT 1
        "#,
    )
    .bind(article_url)
    .fetch_optional(pool)
    .await?;
    if let Some(article_id) = article_id
        && let Some(projection) = load_article_body(pool, article_id, news_item_id, variant).await?
    {
        return Ok(Some(projection));
    }
    Ok(Some(empty_news_projection(news_item_id, variant)))
}

async fn load_article_body(
    pool: &PgPool,
    content_id: i64,
    response_content_id: i64,
    variant: ContentBodyVariant,
) -> Result<Option<ContentBodyProjection>, ContentBodyRepositoryError> {
    let article = sqlx::query_as::<_, ArticleContentRow>(
        r#"
        SELECT
            content.id::bigint AS id,
            content.content_type,
            content.content_metadata::jsonb AS content_metadata,
            content.updated_at
        FROM contents AS content
        WHERE content.id::bigint = $1::bigint
          AND content.content_type = 'article'
        "#,
    )
    .bind(content_id)
    .fetch_optional(pool)
    .await?;
    let Some(article) = article else {
        return Ok(None);
    };
    let pointer = find_pointer(pool, article.id, variant).await?;
    let fallback = fallback_body(
        &article.content_type,
        article.content_metadata.as_object(),
        variant,
    );
    Ok(Some(ContentBodyProjection {
        response_content_id,
        content_type: article.content_type,
        title: None,
        source: None,
        publication_date: None,
        metadata: article.content_metadata,
        variant,
        kind: "article".to_owned(),
        fallback_format: fallback.as_ref().map(|(format, _)| (*format).to_owned()),
        fallback_text: fallback.map(|(_, text)| text),
        fallback_updated_at: article.updated_at.map(|value| value.and_utc()),
        pointer,
    }))
}

async fn find_pointer(
    pool: &PgPool,
    content_id: i64,
    variant: ContentBodyVariant,
) -> Result<Option<ContentBodyPointer>, sqlx::Error> {
    Ok(sqlx::query_as::<_, BodyPointerRow>(
        r#"
        SELECT
            storage_provider,
            storage_bucket,
            storage_key,
            content_format,
            updated_at
        FROM content_bodies
        WHERE content_id::bigint = $1::bigint
          AND variant = $2
        "#,
    )
    .bind(content_id)
    .bind(variant.as_str())
    .fetch_optional(pool)
    .await?
    .map(Into::into))
}

fn fallback_body(
    content_type: &str,
    metadata: Option<&Map<String, Value>>,
    variant: ContentBodyVariant,
) -> Option<(&'static str, String)> {
    let metadata = metadata?;
    match variant {
        ContentBodyVariant::Rendered => metadata
            .get("summary")
            .and_then(Value::as_object)
            .and_then(|summary| clean_string(summary.get("full_markdown")))
            .map(|text| ("markdown", text)),
        ContentBodyVariant::Source if content_type == "podcast" => {
            clean_string(metadata.get("transcript"))
                .or_else(|| clean_string(metadata.get("content_to_summarize")))
                .map(|text| ("text", text))
        }
        ContentBodyVariant::Source if matches!(content_type, "article" | "news") => {
            clean_string(metadata.get("content_to_summarize"))
                .or_else(|| clean_string(metadata.get("content")))
                .map(|text| ("text", text))
        }
        ContentBodyVariant::Source => None,
    }
}

fn clean_string(value: Option<&Value>) -> Option<String> {
    value
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(str::to_owned)
}

fn body_kind(content_type: &str) -> &'static str {
    match content_type {
        "podcast" => "transcript",
        "article" | "news" => "article",
        _ => "source",
    }
}

fn empty_news_projection(news_item_id: i64, variant: ContentBodyVariant) -> ContentBodyProjection {
    ContentBodyProjection {
        response_content_id: news_item_id,
        content_type: "news".to_owned(),
        title: None,
        source: None,
        publication_date: None,
        metadata: Value::Object(Map::new()),
        variant,
        kind: "article".to_owned(),
        fallback_format: None,
        fallback_text: None,
        fallback_updated_at: None,
        pointer: None,
    }
}

fn parse_datetime(value: &str) -> Option<DateTime<Utc>> {
    DateTime::parse_from_rfc3339(value)
        .map(|value| value.with_timezone(&Utc))
        .ok()
        .or_else(|| {
            NaiveDateTime::parse_from_str(value, "%Y-%m-%dT%H:%M:%S%.f")
                .map(|value| value.and_utc())
                .ok()
        })
}

#[derive(Debug, Error)]
pub enum ContentBodyRepositoryError {
    #[error("PostgreSQL content-body query failed")]
    Sqlx(#[from] sqlx::Error),
    #[error("Content or News visibility query failed")]
    ContentRead(#[from] crate::content_read::ContentReadRepositoryError),
}
