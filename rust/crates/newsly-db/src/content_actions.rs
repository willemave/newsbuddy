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

/// Content-pipeline task types whose active rows mean a saved item is still being prepared.
const CONTENT_PIPELINE_TASK_TYPES: [&str; 7] = [
    "analyze_url",
    "process_content",
    "process_podcast_media",
    "download_tweet_video_audio",
    "transcribe_tweet_video",
    "summarize",
    "generate_image",
];

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum KnowledgeReprocessOutcome {
    /// The content row was reset to `new`; the caller must enqueue `analyze_url` in the same
    /// transaction so the whole pipeline runs again from classification.
    Reset,
    /// A pipeline task is already pending or leased for this content; nothing was changed.
    AlreadyActive,
    /// The content is already prepared and readable.
    AlreadyReady,
    /// The user has not saved this content to Knowledge.
    NotSaved,
}

/// Resets one saved, unprepared content row so its full processing pipeline can run again.
///
/// Workers ignore finalization for terminal (`failed`/`skipped`) rows, so re-enqueueing alone
/// is not enough: the row must return to `new` first.
///
/// # Errors
///
/// Returns a database error; the caller must roll back and must not enqueue work.
pub async fn reset_saved_content_for_reprocessing(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
    content_id: i64,
) -> Result<KnowledgeReprocessOutcome, ContentActionRepositoryError> {
    let status = sqlx::query_scalar::<_, String>(
        r"
        SELECT content.status
        FROM contents AS content
        JOIN content_knowledge_saves AS save
            ON save.content_id = content.id AND save.user_id::bigint = $1::bigint
        WHERE content.id::bigint = $2::bigint
        FOR UPDATE OF content
        ",
    )
    .bind(user_id)
    .bind(content_id)
    .fetch_optional(&mut **transaction)
    .await?;
    let Some(status) = status else {
        return Ok(KnowledgeReprocessOutcome::NotSaved);
    };
    if status == "completed" {
        return Ok(KnowledgeReprocessOutcome::AlreadyReady);
    }

    let active_task = sqlx::query_scalar::<_, bool>(
        r"
        SELECT EXISTS(
            SELECT 1
            FROM processing_tasks
            WHERE
                content_id::bigint = $1::bigint
                AND task_type = ANY($2::text[])
                AND status IN ('pending', 'processing')
        )
        ",
    )
    .bind(content_id)
    .bind(&CONTENT_PIPELINE_TASK_TYPES[..])
    .fetch_one(&mut **transaction)
    .await?;
    if active_task {
        return Ok(KnowledgeReprocessOutcome::AlreadyActive);
    }

    sqlx::query(
        r"
        UPDATE contents
        SET
            status = 'new',
            error_message = NULL,
            retry_count = 0,
            processed_at = NULL,
            updated_at = timezone('UTC', now())
        WHERE id::bigint = $1::bigint
        ",
    )
    .bind(content_id)
    .execute(&mut **transaction)
    .await?;
    Ok(KnowledgeReprocessOutcome::Reset)
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

#[cfg(test)]
mod tests {
    use sqlx::PgPool;

    use super::{
        KnowledgeReprocessOutcome, reset_saved_content_for_reprocessing, save_content_to_knowledge,
    };

    async fn seed(pool: &PgPool, status: &str) -> (i64, i64) {
        crate::run_migrations(pool).await.unwrap();
        let user_id = sqlx::query_scalar::<_, i64>(
            "INSERT INTO users (apple_id, email, is_admin, is_active) VALUES ('reprocess', 'reprocess@example.com', FALSE, TRUE) RETURNING id::bigint",
        )
        .fetch_one(pool)
        .await
        .unwrap();
        let content_id = sqlx::query_scalar::<_, i64>(
            r"
            INSERT INTO contents (
                content_type, url, source_url, is_aggregate, status, retry_count, error_message,
                classification, content_metadata, processed_at, created_at, updated_at
            )
            VALUES (
                'podcast', 'https://example.test/reprocess', 'https://example.test/reprocess',
                FALSE, $1, 3, 'transcription failed', 'to_read', '{}'::json,
                timezone('UTC', now()), timezone('UTC', now()), timezone('UTC', now())
            )
            RETURNING id::bigint
            ",
        )
        .bind(status)
        .fetch_one(pool)
        .await
        .unwrap();
        (user_id, content_id)
    }

    async fn reprocess(pool: &PgPool, user_id: i64, content_id: i64) -> KnowledgeReprocessOutcome {
        let mut transaction = pool.begin().await.unwrap();
        let outcome = reset_saved_content_for_reprocessing(&mut transaction, user_id, content_id)
            .await
            .unwrap();
        transaction.commit().await.unwrap();
        outcome
    }

    async fn save(pool: &PgPool, user_id: i64, content_id: i64) {
        let mut transaction = pool.begin().await.unwrap();
        save_content_to_knowledge(&mut transaction, user_id, content_id)
            .await
            .unwrap();
        transaction.commit().await.unwrap();
    }

    #[sqlx::test(migrations = false)]
    async fn failed_saved_content_resets_to_new(pool: PgPool) {
        let (user_id, content_id) = seed(&pool, "failed").await;
        save(&pool, user_id, content_id).await;

        assert_eq!(
            reprocess(&pool, user_id, content_id).await,
            KnowledgeReprocessOutcome::Reset
        );
        let (status, retry_count, error_message, processed_at) =
            sqlx::query_as::<_, (String, i32, Option<String>, Option<chrono::NaiveDateTime>)>(
                "SELECT status, retry_count, error_message, processed_at FROM contents WHERE id::bigint = $1",
            )
            .bind(content_id)
            .fetch_one(&pool)
            .await
            .unwrap();
        assert_eq!(status, "new");
        assert_eq!(retry_count, 0);
        assert_eq!(error_message, None);
        assert_eq!(processed_at, None);
    }

    #[sqlx::test(migrations = false)]
    async fn unsaved_content_is_not_reprocessed(pool: PgPool) {
        let (user_id, content_id) = seed(&pool, "failed").await;

        assert_eq!(
            reprocess(&pool, user_id, content_id).await,
            KnowledgeReprocessOutcome::NotSaved
        );
        let status =
            sqlx::query_scalar::<_, String>("SELECT status FROM contents WHERE id::bigint = $1")
                .bind(content_id)
                .fetch_one(&pool)
                .await
                .unwrap();
        assert_eq!(status, "failed");
    }

    #[sqlx::test(migrations = false)]
    async fn completed_content_is_already_ready(pool: PgPool) {
        let (user_id, content_id) = seed(&pool, "completed").await;
        save(&pool, user_id, content_id).await;

        assert_eq!(
            reprocess(&pool, user_id, content_id).await,
            KnowledgeReprocessOutcome::AlreadyReady
        );
    }

    #[sqlx::test(migrations = false)]
    async fn active_pipeline_task_blocks_reset(pool: PgPool) {
        let (user_id, content_id) = seed(&pool, "processing").await;
        save(&pool, user_id, content_id).await;
        sqlx::query(
            "INSERT INTO processing_tasks(task_type,content_id,queue_name,status,available_at,executor_runtime,executor_version,executor_namespace) VALUES('process_podcast_media',$1::bigint::integer,'media','pending',timezone('UTC',now()),'rust',1,'process_podcast_media')",
        )
        .bind(content_id)
        .execute(&pool)
        .await
        .unwrap();

        assert_eq!(
            reprocess(&pool, user_id, content_id).await,
            KnowledgeReprocessOutcome::AlreadyActive
        );
    }
}
