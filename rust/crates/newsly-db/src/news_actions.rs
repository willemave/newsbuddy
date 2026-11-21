use sqlx::{AssertSqlSafe, Postgres, Transaction};

use crate::news_visibility::{SUBSCRIBED_AGGREGATORS_CTE, visible_news_predicate};
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

    let mut visible_ids = sqlx::query_scalar::<_, i64>(AssertSqlSafe(format!(
        r"
        {SUBSCRIBED_AGGREGATORS_CTE}
        SELECT news_item.id::bigint
        FROM news_items AS news_item
        WHERE news_item.id = ANY($2::bigint[])
          AND news_item.status = 'ready'
          AND news_item.representative_news_item_id IS NULL
          AND {visibility}
        ORDER BY news_item.id
        ",
        visibility = visible_news_predicate("news_item"),
    )))
    .bind(user_id)
    .bind(&unique_ids)
    .fetch_all(&mut **transaction)
    .await?;
    visible_ids.sort_unstable();

    if visible_ids.is_empty() {
        return Ok(BulkNewsReadResult {
            marked_count: 0,
            failed_ids: unique_ids,
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
