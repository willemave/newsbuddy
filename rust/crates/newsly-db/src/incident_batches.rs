//! Narrow operator inventories. Never infer permission to replay historical work from a migration.
use sqlx::{Postgres, Transaction};
use uuid::Uuid;

#[derive(Debug, serde::Serialize)]
pub struct ArchiveIncidentScope {
    pub task_ids: Vec<i64>,
    pub user_id: i64,
    pub config_id: i64,
    pub created_from: chrono::NaiveDateTime,
    pub created_before: chrono::NaiveDateTime,
    pub published_before: chrono::NaiveDateTime,
}

pub async fn archive_candidates(
    tx: &mut Transaction<'_, Postgres>,
    scope: &ArchiveIncidentScope,
) -> Result<Vec<i64>, sqlx::Error> {
    sqlx::query_scalar(r"
        SELECT t.id::bigint FROM processing_tasks t
        JOIN contents c ON c.id = t.content_id
        JOIN content_status s ON s.content_id = c.id AND s.user_id = $2
        WHERE t.id::bigint = ANY($1) AND t.status IN ('pending','processing')
          AND t.task_type IN ('process_content','process_podcast_media','summarize')
          AND c.status IN ('new','pending','processing') AND s.status = 'inbox'
          AND c.created_at >= $3 AND c.created_at < $4 AND c.publication_date < $5
          AND COALESCE(c.content_metadata::jsonb #>> '{domain,feed_config_id}',c.content_metadata::jsonb ->> 'feed_config_id') = $6
          AND NOT EXISTS (SELECT 1 FROM content_read_status r WHERE r.content_id = c.id)
          AND NOT EXISTS (SELECT 1 FROM content_knowledge_saves k WHERE k.content_id = c.id)
          AND NOT EXISTS (SELECT 1 FROM content_status other WHERE other.content_id = c.id AND other.user_id <> $2 AND other.status = 'inbox')
          AND NOT EXISTS (SELECT 1 FROM processing_tasks other WHERE other.content_id = c.id AND other.status IN ('pending','processing') AND NOT (other.id::bigint = ANY($1)))
        ORDER BY t.id FOR UPDATE OF t,c,s
    ").bind(&scope.task_ids).bind(scope.user_id).bind(scope.created_from).bind(scope.created_before).bind(scope.published_before).bind(scope.config_id.to_string()).fetch_all(&mut **tx).await
}

pub async fn apply_archive(
    tx: &mut Transaction<'_, Postgres>,
    scope: &ArchiveIncidentScope,
    reason: &str,
) -> Result<(), sqlx::Error> {
    let content_ids: Vec<i64> = sqlx::query_scalar("UPDATE processing_tasks SET status = 'failed', completed_at = timezone('UTC',now()), error_message = $2, locked_by = NULL, locked_at = NULL, lease_token = NULL, lease_expires_at = NULL WHERE id::bigint = ANY($1) RETURNING content_id::bigint")
        .bind(&scope.task_ids).bind(format!("incident_cancelled:{reason}")).fetch_all(&mut **tx).await?;
    sqlx::query("UPDATE content_status SET status = 'archived', updated_at = timezone('UTC',now()) WHERE user_id = $1 AND content_id::bigint = ANY($2) AND status = 'inbox'")
        .bind(scope.user_id).bind(&content_ids).execute(&mut **tx).await?;
    sqlx::query("UPDATE contents SET status = 'failed', error_message = $2, updated_at = timezone('UTC',now()) WHERE id::bigint = ANY($1)")
        .bind(&content_ids).bind(format!("incident_cancelled:{reason}")).execute(&mut **tx).await?;
    Ok(())
}

pub async fn artwork_candidates(
    tx: &mut Transaction<'_, Postgres>,
    ids: &[i64],
) -> Result<Vec<i64>, sqlx::Error> {
    sqlx::query_scalar(r"
        SELECT c.id::bigint FROM contents c
        WHERE c.id::bigint = ANY($1) AND c.status IN ('completed','awaiting_image')
          AND c.content_type IN ('article','podcast') AND c.classification IS DISTINCT FROM 'skip'
          AND jsonb_typeof(COALESCE(c.content_metadata::jsonb #> '{processing,summary}',c.content_metadata::jsonb #> '{domain,summary}',c.content_metadata::jsonb -> 'summary')) = 'object'
          AND NULLIF(COALESCE(c.content_metadata::jsonb #> '{domain,image_generated_at}',c.content_metadata::jsonb -> 'image_generated_at'),'null'::jsonb) IS NULL
          AND NOT EXISTS (SELECT 1 FROM processing_tasks t WHERE t.content_id = c.id AND t.task_type = 'generate_image')
          AND (EXISTS (SELECT 1 FROM content_status s JOIN users u ON u.id = s.user_id WHERE s.content_id = c.id AND s.status = 'inbox' AND u.is_active)
            OR EXISTS (SELECT 1 FROM content_knowledge_saves s JOIN users u ON u.id = s.user_id WHERE s.content_id = c.id AND u.is_active))
        ORDER BY c.id FOR UPDATE OF c
    ").bind(ids).fetch_all(&mut **tx).await
}

pub async fn mark_artwork_pending(
    tx: &mut Transaction<'_, Postgres>,
    ids: &[i64],
) -> Result<(), sqlx::Error> {
    sqlx::query("UPDATE contents SET status = 'awaiting_image', updated_at = timezone('UTC', now()) WHERE id::bigint = ANY($1)")
        .bind(ids).execute(&mut **tx).await?;
    Ok(())
}

/// Exact idempotency: reusing an ID with different authority or targets is rejected.
pub async fn record_batch(
    tx: &mut Transaction<'_, Postgres>,
    id: Uuid,
    operation: &str,
    actor: &str,
    reason: &str,
    targets: &serde_json::Value,
) -> Result<bool, sqlx::Error> {
    let inserted = sqlx::query("INSERT INTO operator_incident_batches(batch_id,operation,actor,reason,targets) VALUES($1,$2,$3,$4,$5) ON CONFLICT DO NOTHING")
        .bind(id).bind(operation).bind(actor).bind(reason).bind(targets).execute(&mut **tx).await?.rows_affected();
    let matches: bool = sqlx::query_scalar("SELECT operation = $2 AND actor = $3 AND reason = $4 AND targets = $5 FROM operator_incident_batches WHERE batch_id = $1")
        .bind(id).bind(operation).bind(actor).bind(reason).bind(targets).fetch_one(&mut **tx).await?;
    if !matches {
        return Err(sqlx::Error::Protocol(
            "batch ID already belongs to different targets or authority".to_owned(),
        ));
    }
    Ok(inserted == 1)
}

#[cfg(test)]
mod tests {
    use super::*;
    use sqlx::PgPool;

    #[sqlx::test]
    async fn artwork_migration_never_enqueues_and_inventory_excludes_exhausted_or_archived(
        pool: PgPool,
    ) {
        crate::run_migrations(&pool).await.unwrap();
        let user: i64 = sqlx::query_scalar("INSERT INTO users(apple_id,email,is_active,is_admin) VALUES('artwork-batch','artwork-batch@example.com',true,false) RETURNING id::bigint").fetch_one(&pool).await.unwrap();
        let ids: Vec<i64> = sqlx::query_scalar("INSERT INTO contents(content_type,url,status,is_aggregate,content_metadata) SELECT 'article','https://example.com/art/' || i,'completed',false,'{\"summary\":{\"title\":\"Ready\"}}'::json FROM generate_series(1,4) i RETURNING id::bigint").fetch_all(&pool).await.unwrap();
        for (index, id) in ids.iter().enumerate() {
            sqlx::query("INSERT INTO content_status(user_id,content_id,status) VALUES($1::bigint::integer,$2::bigint::integer,$3)").bind(user).bind(id).bind(if index < 2 {"inbox"} else {"archived"}).execute(&pool).await.unwrap();
        }
        sqlx::query("INSERT INTO content_knowledge_saves(user_id,content_id) VALUES($1::bigint::integer,$2::bigint::integer)").bind(user).bind(ids[2]).execute(&pool).await.unwrap();
        sqlx::query("INSERT INTO processing_tasks(task_type,content_id,status,queue_name,executor_runtime,executor_version,executor_namespace) VALUES('generate_image',$1::bigint::integer,'failed','image','rust',1,'generate_image')").bind(ids[1]).execute(&pool).await.unwrap();
        for _ in 0..2 {
            sqlx::raw_sql(include_str!(
                "../migrations/20260905000000_briefing_requires_artwork.sql"
            ))
            .execute(&pool)
            .await
            .unwrap();
        }
        let active: i64 = sqlx::query_scalar(
            "SELECT count(*) FROM processing_tasks WHERE status IN ('pending','processing')",
        )
        .fetch_one(&pool)
        .await
        .unwrap();
        assert_eq!(
            active, 0,
            "schema deployment never silently starts historical image work"
        );
        let mut tx = pool.begin().await.unwrap();
        assert_eq!(
            artwork_candidates(&mut tx, &ids).await.unwrap(),
            vec![ids[0], ids[2]]
        );
    }

    #[sqlx::test]
    async fn incident_inventory_preserves_reads_and_replays_are_noops(pool: PgPool) {
        crate::run_migrations(&pool).await.unwrap();
        let user: i64 = sqlx::query_scalar("INSERT INTO users(apple_id,email,is_active,is_admin) VALUES('incident-test','incident@example.com',true,false) RETURNING id::bigint").fetch_one(&pool).await.unwrap();
        let contents: Vec<i64> = sqlx::query_scalar("INSERT INTO contents(content_type,url,status,is_aggregate,content_metadata,publication_date) SELECT 'article','https://example.com/' || i,'processing',false,'{\"feed_config_id\":1}'::json,timezone('UTC',now()) - interval '1 year' FROM generate_series(1,2) i RETURNING id::bigint").fetch_all(&pool).await.unwrap();
        for id in &contents {
            sqlx::query(
                "INSERT INTO content_status(user_id,content_id,status) VALUES($1,$2,'inbox')",
            )
            .bind(user as i32)
            .bind(*id as i32)
            .execute(&pool)
            .await
            .unwrap();
        }
        sqlx::query(
            "INSERT INTO content_read_status(user_id,content_id,read_at) VALUES($1,$2,now())",
        )
        .bind(user as i32)
        .bind(contents[1] as i32)
        .execute(&pool)
        .await
        .unwrap();
        let tasks: Vec<i64> = sqlx::query_scalar("INSERT INTO processing_tasks(task_type,content_id,status,queue_name,executor_runtime,executor_version,executor_namespace) SELECT 'process_content',id,'pending','content','rust',1,'process_content' FROM contents ORDER BY id RETURNING id::bigint").fetch_all(&pool).await.unwrap();
        let now = chrono::Utc::now().naive_utc();
        let mut scope = ArchiveIncidentScope {
            task_ids: tasks.clone(),
            user_id: user,
            config_id: 1,
            created_from: now - chrono::Duration::hours(1),
            created_before: now + chrono::Duration::hours(1),
            published_before: now - chrono::Duration::days(30),
        };
        let mut tx = pool.begin().await.unwrap();
        assert_eq!(
            archive_candidates(&mut tx, &scope).await.unwrap(),
            vec![tasks[0]]
        );
        scope.task_ids = vec![tasks[0]];
        let batch = Uuid::new_v4();
        let targets = serde_json::to_value(&scope).unwrap();
        assert!(
            record_batch(
                &mut tx,
                batch,
                "archive_incident",
                "test",
                "regression",
                &targets
            )
            .await
            .unwrap()
        );
        apply_archive(&mut tx, &scope, "regression").await.unwrap();
        assert!(
            !record_batch(
                &mut tx,
                batch,
                "archive_incident",
                "test",
                "regression",
                &targets
            )
            .await
            .unwrap()
        );
        tx.commit().await.unwrap();
        let states: Vec<String> =
            sqlx::query_scalar("SELECT status FROM content_status ORDER BY content_id")
                .fetch_all(&pool)
                .await
                .unwrap();
        assert_eq!(states, ["archived", "inbox"]);
        let reads: i64 = sqlx::query_scalar("SELECT count(*) FROM content_read_status")
            .fetch_one(&pool)
            .await
            .unwrap();
        assert_eq!(reads, 1);
        let mut tx = pool.begin().await.unwrap();
        assert!(
            record_batch(
                &mut tx,
                batch,
                "archive_incident",
                "test",
                "different",
                &targets
            )
            .await
            .is_err()
        );
    }
}
