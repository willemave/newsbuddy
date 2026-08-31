use sqlx::{Postgres, Transaction};
use thiserror::Error;

pub async fn content_exists(
    transaction: &mut Transaction<'_, Postgres>,
    content_id: i64,
) -> Result<bool, ContentActionRepositoryError> {
    Ok(sqlx::query_scalar::<_, bool>(
        "SELECT EXISTS(SELECT 1 FROM contents WHERE id::bigint = $1::bigint)",
    )
    .bind(content_id)
    .fetch_one(&mut **transaction)
    .await?)
}

pub async fn mark_content_read(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
    content_id: i64,
) -> Result<(), ContentActionRepositoryError> {
    sqlx::query(
        r#"
        INSERT INTO content_read_status (user_id, content_id, read_at, created_at)
        VALUES ($1::bigint::integer, $2::bigint::integer, timezone('UTC', now()), timezone('UTC', now()))
        ON CONFLICT (user_id, content_id)
        DO UPDATE SET read_at = EXCLUDED.read_at
        "#,
    )
    .bind(user_id)
    .bind(content_id)
    .execute(&mut **transaction)
    .await?;
    Ok(())
}

pub async fn mark_content_unread(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
    content_id: i64,
) -> Result<u64, ContentActionRepositoryError> {
    Ok(sqlx::query(
        "DELETE FROM content_read_status WHERE user_id::bigint = $1::bigint AND content_id::bigint = $2::bigint",
    )
    .bind(user_id)
    .bind(content_id)
    .execute(&mut **transaction)
    .await?
    .rows_affected())
}

pub async fn mark_contents_read(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
    content_ids: &[i64],
) -> Result<BulkReadResult, ContentActionRepositoryError> {
    let mut unique_ids = content_ids.to_vec();
    unique_ids.sort_unstable();
    unique_ids.dedup();

    let mut existing_ids = sqlx::query_scalar::<_, i64>(
        "SELECT id::bigint FROM contents WHERE id::bigint = ANY($1::bigint[]) ORDER BY id",
    )
    .bind(&unique_ids)
    .fetch_all(&mut **transaction)
    .await?;
    existing_ids.sort_unstable();
    let failed_ids = unique_ids
        .iter()
        .copied()
        .filter(|content_id| existing_ids.binary_search(content_id).is_err())
        .collect::<Vec<_>>();
    if !failed_ids.is_empty() {
        return Ok(BulkReadResult {
            marked_count: 0,
            failed_ids,
        });
    }

    if !existing_ids.is_empty() {
        sqlx::query(
            r#"
            INSERT INTO content_read_status (user_id, content_id, read_at, created_at)
            SELECT $1::bigint::integer, content_id::integer, timezone('UTC', now()), timezone('UTC', now())
            FROM unnest($2::bigint[]) AS content_id
            ON CONFLICT (user_id, content_id)
            DO UPDATE SET read_at = EXCLUDED.read_at
            "#,
        )
        .bind(user_id)
        .bind(&existing_ids)
        .execute(&mut **transaction)
        .await?;
    }

    Ok(BulkReadResult {
        marked_count: existing_ids.len(),
        failed_ids,
    })
}

pub async fn save_content_to_knowledge(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
    content_id: i64,
) -> Result<(), ContentActionRepositoryError> {
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
    Ok(())
}

pub async fn remove_content_from_knowledge(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
    content_id: i64,
) -> Result<bool, ContentActionRepositoryError> {
    Ok(sqlx::query(
        "DELETE FROM content_knowledge_saves WHERE user_id::bigint = $1::bigint AND content_id::bigint = $2::bigint",
    )
    .bind(user_id)
    .bind(content_id)
    .execute(&mut **transaction)
    .await?
    .rows_affected()
        > 0)
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BulkReadResult {
    pub marked_count: usize,
    pub failed_ids: Vec<i64>,
}

#[derive(Debug, Error)]
pub enum ContentActionRepositoryError {
    #[error("content action database operation failed")]
    Sqlx(#[from] sqlx::Error),
}
