//! Content and News source resolution or conversion for Learning Deck creation.

use crate::content_metadata::{legacy_summarization_text, runtime_metadata, summary_is_readable};

use super::{
    ContentSourceOutcome, ContentSourceRow, ConvertedNewsSource, LearningDeckRepositoryError,
    LearningDeckSourceProjection, Map, NewsItemRow, Postgres, Transaction, Value,
    VisibleNewsItemProjection,
    canonical::resolve_canonical_content,
    common::{clean_optional, content_display_title, json_object, nested_clean_text},
    save_content_to_knowledge,
};

/// Resolves a visible, ready content source and proves that readable source text is represented by
/// either the canonical body pointer or the legacy metadata fallback.
///
/// # Errors
///
/// Returns [`LearningDeckRepositoryError::Sqlx`] when PostgreSQL cannot complete the query.
pub async fn resolve_content_learning_deck_source(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
    content_id: i64,
) -> Result<ContentSourceOutcome, LearningDeckRepositoryError> {
    let row = sqlx::query_as::<_, ContentSourceRow>(CONTENT_SOURCE_SQL)
        .bind(user_id)
        .bind(content_id)
        .fetch_optional(&mut **transaction)
        .await?;
    let Some(row) = row else {
        return Ok(ContentSourceOutcome::NotFoundOrNotReady);
    };
    if !row.body_available
        && legacy_summarization_text(&row.content_type, &row.content_metadata).is_none()
    {
        return Ok(ContentSourceOutcome::TextUnavailable);
    }
    Ok(ContentSourceOutcome::Ready(content_source_from_row(&row)))
}

/// Loads a content row created by a trusted submission transaction as a deck source.
///
/// # Errors
///
/// Returns [`LearningDeckRepositoryError`] when the row disappeared or PostgreSQL failed.
pub async fn load_submitted_content_learning_deck_source(
    transaction: &mut Transaction<'_, Postgres>,
    content_id: i64,
) -> Result<LearningDeckSourceProjection, LearningDeckRepositoryError> {
    let row = sqlx::query_as::<_, ContentSourceRow>(
        r#"
        SELECT
            content.id::bigint AS id,
            content.content_type,
            content.url,
            content.source_url,
            content.title,
            content.status,
            content.content_metadata,
            TRUE AS body_available
        FROM contents AS content
        WHERE content.id = $1::bigint
        FOR SHARE
        "#,
    )
    .bind(content_id)
    .fetch_optional(&mut **transaction)
    .await?
    .ok_or(LearningDeckRepositoryError::SubmittedContentMissing(
        content_id,
    ))?;
    Ok(content_source_from_row(&row))
}

/// Returns the canonical content ID when a ready Learning Deck source needs a summary task.
///
/// The source is share-locked until commit so a concurrent summary finalizer cannot change
/// eligibility between this check and enqueue. The caller must use standard queue deduplication
/// to coalesce concurrent creation requests.
///
/// # Errors
///
/// Returns a repository error when the canonical source cannot be loaded or queried.
pub async fn learning_deck_source_needing_summary(
    transaction: &mut Transaction<'_, Postgres>,
    content_id: Option<i64>,
) -> Result<Option<i64>, LearningDeckRepositoryError> {
    let Some(content_id) = content_id else {
        return Ok(None);
    };
    let content = resolve_canonical_content(transaction, content_id).await?;
    if !matches!(content.status.as_str(), "completed" | "awaiting_image")
        || !matches!(
            content.content_type.as_str(),
            "article" | "news" | "podcast"
        )
    {
        return Ok(None);
    }
    if content
        .content_metadata
        .as_object()
        .is_some_and(|metadata| {
            summary_is_readable(&runtime_metadata(metadata), &content.content_type)
        })
    {
        return Ok(None);
    }
    let legacy_text =
        legacy_summarization_text(&content.content_type, &content.content_metadata).is_some();
    let needs_summary = sqlx::query_scalar::<_, bool>(
        r"
        SELECT (
            $2 OR EXISTS (
                SELECT 1 FROM content_bodies
                WHERE content_id = $1 AND variant = 'source' AND char_count > 0
            )
        ) AND NOT EXISTS (
            SELECT 1 FROM processing_tasks
            WHERE content_id = $1 AND task_type = 'summarize'
              AND status IN ('pending', 'processing')
        )
        ",
    )
    .bind(content.id)
    .bind(legacy_text)
    .fetch_one(&mut **transaction)
    .await?;
    Ok(needs_summary.then_some(content.id))
}

/// Finds a visible representative Fast Read source.
///
/// # Errors
///
/// Returns [`LearningDeckRepositoryError::Sqlx`] when PostgreSQL cannot complete the query.
pub async fn find_visible_news_item_for_learning_deck(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
    news_item_id: i64,
) -> Result<Option<VisibleNewsItemProjection>, LearningDeckRepositoryError> {
    let row = sqlx::query_as::<_, NewsItemRow>(VISIBLE_NEWS_ITEM_SQL)
        .bind(user_id)
        .bind(news_item_id)
        .fetch_optional(&mut **transaction)
        .await?;
    Ok(row.map(|row| VisibleNewsItemProjection {
        id: row.id,
        article_url: row.article_url,
        canonical_story_url: row.canonical_story_url,
        article_domain: row.article_domain,
        raw_metadata: json_object(row.raw_metadata),
    }))
}

/// Converts a previously resolved Fast Read article URL to a saved content source.
///
/// The caller must enqueue `PROCESS_CONTENT` when requested in the same transaction.
///
/// # Errors
///
/// Returns [`LearningDeckRepositoryError`] when the source changed or persistence failed.
pub async fn convert_news_item_to_learning_deck_source(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
    item: &VisibleNewsItemProjection,
    normalized_article_url: &str,
) -> Result<ConvertedNewsSource, LearningDeckRepositoryError> {
    let title = nested_clean_text(
        &Value::Object(item.raw_metadata.clone()),
        &["article", "title"],
    );
    let inserted_id = sqlx::query_scalar::<_, i64>(
        r#"
        INSERT INTO contents (
            content_type, url, source_url, title, source, status, retry_count,
            platform, is_aggregate, content_metadata, created_at, updated_at
        )
        VALUES (
            'article', $1, $1, $2, $3, 'pending', 0,
            NULL, FALSE, '{"deck_source_only":true,"processing":{"deck_source_only":true}}'::jsonb, timezone('UTC', now()), timezone('UTC', now())
        )
        ON CONFLICT (url, content_type) DO NOTHING
        RETURNING id::bigint
        "#,
    )
    .bind(normalized_article_url)
    .bind(title.as_deref())
    .bind(item.article_domain.as_deref())
    .fetch_optional(&mut **transaction)
    .await?;
    let content_id = match inserted_id {
        Some(content_id) => content_id,
        None => sqlx::query_scalar::<_, i64>(
            "SELECT id::bigint FROM contents WHERE url = $1 AND content_type = 'article' ORDER BY id LIMIT 1 FOR SHARE",
        )
        .bind(normalized_article_url)
        .fetch_optional(&mut **transaction)
        .await?
        .ok_or_else(|| LearningDeckRepositoryError::ConvertedArticleMissing(normalized_article_url.to_owned()))?,
    };
    save_content_to_knowledge(transaction, user_id, content_id).await?;
    let row = sqlx::query_as::<_, ContentSourceRow>(
        r#"
        SELECT
            id::bigint AS id, content_type, url, source_url, title, status, content_metadata,
            TRUE AS body_available
        FROM contents
        WHERE id = $1::bigint
        FOR SHARE
        "#,
    )
    .bind(content_id)
    .fetch_one(&mut **transaction)
    .await?;
    Ok(ConvertedNewsSource {
        source: content_source_from_row(&row),
        enqueue_process_content: inserted_id.is_some(),
    })
}

pub(super) fn content_source_from_row(row: &ContentSourceRow) -> LearningDeckSourceProjection {
    let source_metadata = Map::from_iter([
        (
            "content_type".to_owned(),
            Value::from(row.content_type.clone()),
        ),
        ("status".to_owned(), Value::from(row.status.clone())),
    ]);
    LearningDeckSourceProjection {
        source_kind: "content".to_owned(),
        source_identity: format!("content:{}", row.id),
        source_url: clean_optional(row.source_url.as_deref())
            .map(str::to_owned)
            .or_else(|| Some(row.url.clone())),
        source_content_id: Some(row.id),
        source_title: content_display_title(row.id, row.title.as_deref(), &row.content_metadata),
        source_metadata,
    }
}

const CONTENT_SOURCE_SQL: &str = r#"
    SELECT
        content.id::bigint AS id,
        content.content_type,
        content.url,
        content.source_url,
        content.title,
        content.status,
        content.content_metadata,
        EXISTS (
            SELECT 1 FROM content_bodies AS body
            WHERE body.content_id = content.id
              AND body.variant = 'source'
              AND body.char_count > 0
        ) AS body_available
    FROM contents AS content
    WHERE content.id = $2::bigint
      AND content.status IN ('completed', 'awaiting_image')
      AND (
          EXISTS(
              SELECT 1 FROM content_knowledge_saves AS save
              WHERE save.user_id = $1::bigint AND save.content_id = content.id
          )
          OR (
              EXISTS(
                  SELECT 1 FROM content_status AS inbox
                  WHERE inbox.user_id = $1::bigint
                    AND inbox.content_id = content.id
                    AND inbox.status = 'inbox'
              )
          )
      )
    FOR SHARE
"#;

const VISIBLE_NEWS_ITEM_SQL: &str = r#"
    SELECT
        item.id::bigint AS id,
        item.article_url,
        item.canonical_story_url,
        item.article_domain,
        item.raw_metadata
    FROM news_items AS item
    WHERE item.id = $2::bigint
      AND item.status = 'ready'
      AND item.representative_news_item_id IS NULL
      AND (
          (item.visibility_scope = 'user' AND item.owner_user_id = $1::bigint)
          OR (
              item.visibility_scope = 'global'
              AND EXISTS(
                  SELECT 1
                  FROM user_scraper_configs AS config
                  WHERE config.user_id = $1::bigint
                    AND config.scraper_type = 'aggregator'
                    AND config.is_active = TRUE
                    AND aggregator_config_admits(config.config::jsonb, item.platform, item.raw_metadata)
              )
          )
      )
    FOR SHARE
"#;
