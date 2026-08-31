use sqlx::{Postgres, Transaction};
use thiserror::Error;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BulkNewsReadResult {
    pub marked_count: usize,
    pub failed_ids: Vec<i64>,
}

/// Marks only ready, representative news items that are visible to the requesting user.
pub async fn mark_visible_news_items_read(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
    requested_ids: &[i64],
) -> Result<BulkNewsReadResult, NewsActionRepositoryError> {
    let mut unique_ids = requested_ids.to_vec();
    unique_ids.sort_unstable();
    unique_ids.dedup();

    let mut visible_ids = sqlx::query_scalar::<_, i64>(
        r#"
        SELECT news_item.id::bigint
        FROM news_items AS news_item
        WHERE news_item.id::bigint = ANY($2::bigint[])
          AND news_item.status = 'ready'
          AND news_item.representative_news_item_id IS NULL
          AND (
              (
                  news_item.visibility_scope = 'user'
                  AND news_item.owner_user_id::bigint = $1::bigint
              )
              OR (
                  news_item.visibility_scope = 'global'
                  AND EXISTS (
                      SELECT 1
                      FROM user_scraper_configs AS config
                      WHERE config.user_id::bigint = $1::bigint
                        AND config.scraper_type = 'aggregator'
                        AND config.is_active IS TRUE
                        AND lower(btrim(COALESCE(config.config::jsonb ->> 'key', ''))) =
                            lower(btrim(COALESCE(news_item.platform, '')))
                        AND lower(btrim(COALESCE(config.config::jsonb ->> 'key', ''))) = ANY(
                            ARRAY[
                                'brutalist', 'finurls', 'hackernews', 'mediagazer',
                                'memeorandum', 'sciurls', 'techmeme'
                            ]::text[]
                        )
                        AND (
                            lower(btrim(COALESCE(config.config::jsonb ->> 'key', ''))) <> 'brutalist'
                            OR COALESCE(jsonb_typeof(config.config::jsonb -> 'topics'), 'null') <> 'array'
                            OR jsonb_array_length(config.config::jsonb -> 'topics') = 0
                            OR lower(btrim(COALESCE(
                                news_item.raw_metadata::jsonb #>> '{aggregator,topic}',
                                ''
                            ))) IN (
                                SELECT lower(btrim(topic))
                                FROM jsonb_array_elements_text(config.config::jsonb -> 'topics') AS topic
                            )
                        )
                  )
              )
          )
        ORDER BY news_item.id
        "#,
    )
    .bind(user_id)
    .bind(&unique_ids)
    .fetch_all(&mut **transaction)
    .await?;
    visible_ids.sort_unstable();

    if visible_ids.is_empty() {
        return Ok(BulkNewsReadResult {
            marked_count: 0,
            failed_ids: requested_ids.to_vec(),
        });
    }

    let marked_count = sqlx::query_scalar::<_, i64>(
        r#"
        WITH inserted AS (
            INSERT INTO news_item_read_status (user_id, news_item_id, read_at, created_at)
            SELECT
                $1::bigint::integer,
                news_item_id::integer,
                timezone('UTC', now()),
                timezone('UTC', now())
            FROM unnest($2::bigint[]) AS news_item_id
            ON CONFLICT (user_id, news_item_id) DO NOTHING
            RETURNING news_item_id
        )
        SELECT count(*)::bigint FROM inserted
        "#,
    )
    .bind(user_id)
    .bind(&visible_ids)
    .fetch_one(&mut **transaction)
    .await?;

    let failed_ids = unique_ids
        .into_iter()
        .filter(|news_item_id| visible_ids.binary_search(news_item_id).is_err())
        .collect();
    Ok(BulkNewsReadResult {
        marked_count: usize::try_from(marked_count).unwrap_or(0),
        failed_ids,
    })
}

#[derive(Debug, Error)]
pub enum NewsActionRepositoryError {
    #[error("news action database operation failed")]
    Sqlx(#[from] sqlx::Error),
}
