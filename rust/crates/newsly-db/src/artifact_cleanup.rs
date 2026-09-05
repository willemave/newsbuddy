use sqlx::PgPool;

pub async fn track_artifact(pool: &PgPool, key: &str, llm_task_id: i64) -> Result<(), sqlx::Error> {
    sqlx::query("INSERT INTO artifact_cleanup_candidates (object_key, llm_task_id) VALUES ($1, $2) ON CONFLICT DO NOTHING").bind(key).bind(llm_task_id).execute(pool).await?;
    Ok(())
}

pub async fn artifact_cleanup_candidates(
    pool: &PgPool,
    kind: &str,
) -> Result<Vec<String>, sqlx::Error> {
    sqlx::query_scalar(
        r"
        SELECT candidate.object_key
        FROM artifact_cleanup_candidates AS candidate
        WHERE candidate.kind = $1
          AND candidate.created_at < now() - interval '1 day'
          AND NOT EXISTS (SELECT 1
            FROM llm_tasks AS task
            WHERE task.id::bigint = candidate.llm_task_id
              AND task.status NOT IN ('completed', 'failed', 'cancelled'))
          AND NOT EXISTS (SELECT 1
            FROM processing_tasks AS task
            WHERE task.id::bigint = candidate.processing_task_id
              AND task.status IN ('pending', 'processing'))
          AND NOT EXISTS (SELECT 1
            FROM contents
            WHERE candidate.kind = 'image'
              AND strpos(content_metadata::text, candidate.object_key) > 0)
          AND NOT EXISTS (SELECT 1
            FROM learning_decks AS deck
            WHERE deck.artifact_object_keys::jsonb ? candidate.object_key)
          AND NOT EXISTS (SELECT 1
            FROM llm_tasks AS task
            WHERE task.agent_log_object_key = candidate.object_key)
        ORDER BY candidate.created_at
        LIMIT 64
        ",
    )
    .bind(kind)
    .fetch_all(pool)
    .await
}

pub async fn forget_cleaned_artifact(pool: &PgPool, key: &str) -> Result<(), sqlx::Error> {
    sqlx::query("DELETE FROM artifact_cleanup_candidates WHERE object_key = $1")
        .bind(key)
        .execute(pool)
        .await?;
    Ok(())
}

pub async fn track_image_artifact(
    pool: &PgPool,
    key: &str,
    task_id: i64,
) -> Result<(), sqlx::Error> {
    sqlx::query("INSERT INTO artifact_cleanup_candidates (object_key, processing_task_id, kind) VALUES ($1, $2, 'image') ON CONFLICT DO NOTHING").bind(key).bind(task_id).execute(pool).await?;
    Ok(())
}
