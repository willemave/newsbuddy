//! Content and News source resolution or conversion for Learning Deck creation.

use super::{
    ContentSourceOutcome, ContentSourceRow, ConvertedNewsSource, LearningDeckRepositoryError,
    LearningDeckSourceProjection, Map, NewsItemRow, Postgres, Transaction, Value,
    VisibleNewsItemProjection,
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
    if !row.body_available {
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
        WHERE content.id::bigint = $1
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
/// The caller must enqueue `PROCESS_CONTENT` when requested and enqueue the Knowledge corpus sync
/// in the same transaction.
///
/// # Errors
///
/// Returns [`LearningDeckRepositoryError`] when the source changed, persistence failed, or the
/// Knowledge overlay could not be saved.
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
            classification, platform, is_aggregate, content_metadata, created_at, updated_at
        )
        VALUES (
            'article', $1, $1, $2, $3, 'pending', 0,
            NULL, NULL, FALSE, '{}'::jsonb, timezone('UTC', now()), timezone('UTC', now())
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
        WHERE id::bigint = $1
        FOR SHARE
        "#,
    )
    .bind(content_id)
    .fetch_one(&mut **transaction)
    .await?;
    Ok(ConvertedNewsSource {
        source: content_source_from_row(&row),
        enqueue_process_content: inserted_id.is_some(),
        enqueue_agent_data_sync: true,
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
        (
            EXISTS(
                SELECT 1 FROM content_bodies AS body
                WHERE body.content_id = content.id
                  AND body.variant = 'source'
                  AND body.char_count > 0
            )
            OR CASE
                WHEN content.content_type = 'podcast' THEN
                    NULLIF(btrim(COALESCE(content.content_metadata->>'transcript', content.content_metadata->>'content_to_summarize', '')), '') IS NOT NULL
                WHEN content.content_type IN ('article', 'news') THEN
                    NULLIF(btrim(COALESCE(content.content_metadata->>'content_to_summarize', content.content_metadata->>'content', '')), '') IS NOT NULL
                ELSE FALSE
            END
        ) AS body_available
    FROM contents AS content
    WHERE content.id::bigint = $2
      AND content.status IN ('completed', 'awaiting_image')
      AND (
          EXISTS(
              SELECT 1 FROM content_knowledge_saves AS save
              WHERE save.user_id::bigint = $1 AND save.content_id = content.id
          )
          OR (
              EXISTS(
                  SELECT 1 FROM content_status AS inbox
                  WHERE inbox.user_id::bigint = $1
                    AND inbox.content_id = content.id
                    AND inbox.status = 'inbox'
              )
              AND (content.classification IS NULL OR content.classification <> 'skip')
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
    WHERE item.id::bigint = $2
      AND item.status = 'ready'
      AND item.representative_news_item_id IS NULL
      AND (
          (item.visibility_scope = 'user' AND item.owner_user_id::bigint = $1)
          OR (
              item.visibility_scope = 'global'
              AND EXISTS(
                  SELECT 1
                  FROM user_scraper_configs AS config
                  WHERE config.user_id::bigint = $1
                    AND config.scraper_type = 'aggregator'
                    AND config.is_active = TRUE
                    AND lower(btrim(config.config->>'key')) IN (
                        'brutalist', 'finurls', 'hackernews', 'mediagazer',
                        'memeorandum', 'sciurls', 'techmeme'
                    )
                    AND lower(btrim(config.config->>'key')) = lower(COALESCE(item.platform, ''))
                    AND (
                        lower(btrim(config.config->>'key')) <> 'brutalist'
                        OR jsonb_array_length(COALESCE(config.config::jsonb->'topics', '[]'::jsonb)) = 0
                        OR EXISTS(
                            SELECT 1
                            FROM jsonb_array_elements_text(COALESCE(config.config::jsonb->'topics', '[]'::jsonb)) AS topic(value)
                            WHERE lower(btrim(topic.value)) = lower(btrim(item.raw_metadata::jsonb->'aggregator'->>'topic'))
                        )
                    )
              )
          )
      )
    FOR SHARE
"#;
