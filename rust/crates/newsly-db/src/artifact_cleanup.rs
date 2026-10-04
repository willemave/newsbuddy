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
            WHERE deck.deleted_at IS NULL
              AND deck.artifact_object_keys::jsonb ? candidate.object_key)
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

#[cfg(test)]
mod tests {
    use super::*;

    #[sqlx::test]
    async fn deleted_deck_artifacts_can_be_swept_but_live_decks_stay_protected(pool: PgPool) {
        crate::run_migrations(&pool).await.unwrap();
        let user: i64 = sqlx::query_scalar(
            "INSERT INTO users(apple_id,email,is_admin,is_active)
             VALUES('artifact-cleanup','artifact-cleanup@example.com',false,true) RETURNING id::bigint",
        )
        .fetch_one(&pool)
        .await
        .unwrap();
        for (identity, deleted, key) in [
            ("live", false, "learning-decks/live.html"),
            ("deleted", true, "learning-decks/deleted.html"),
        ] {
            sqlx::query(
                "INSERT INTO learning_decks(user_id,source_kind,source_identity,source_metadata,title,
                 artifact_object_keys,share_enabled,deleted_at)
                 VALUES($1,'url',$2,'{}','Test deck',jsonb_build_array($3::text),false,
                 CASE WHEN $4 THEN timezone('UTC',now()) ELSE NULL END)",
            )
            .bind(user)
            .bind(identity)
            .bind(key)
            .bind(deleted)
            .execute(&pool)
            .await
            .unwrap();
            sqlx::query(
                "INSERT INTO artifact_cleanup_candidates(object_key,kind,created_at)
                 VALUES($1,'deck',now() - interval '2 days')",
            )
            .bind(key)
            .execute(&pool)
            .await
            .unwrap();
        }
        assert_eq!(
            artifact_cleanup_candidates(&pool, "deck").await.unwrap(),
            ["learning-decks/deleted.html"]
        );
    }
}
