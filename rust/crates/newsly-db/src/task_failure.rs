use sqlx::{Postgres, Transaction};

/// Settle public state when a terminal queue failure bypasses a feature finalizer.
/// The caller must hold the queue's exact-lease finalization fence.
pub async fn settle_failed_task(
    tx: &mut Transaction<'_, Postgres>,
    task_id: i64,
    task_type: &str,
    content_id: Option<i64>,
    owner_user_id: Option<i64>,
    message: &str,
) -> Result<(), sqlx::Error> {
    let (payload, retry_count): (
        sqlx::types::Json<serde_json::Map<String, serde_json::Value>>,
        i32,
    ) = sqlx::query_as("SELECT payload, retry_count FROM processing_tasks WHERE id::bigint = $1")
        .bind(task_id)
        .fetch_one(&mut **tx)
        .await?;
    let payload = &payload.0;
    if let Some(user_id) = owner_user_id {
        settle_related_workflow(
            tx,
            task_id,
            task_type,
            user_id,
            retry_count,
            payload,
            message,
        )
        .await?;
    }
    if task_type == "run_llm_task" {
        let id = payload
            .get("llm_task_id")
            .and_then(serde_json::Value::as_i64);
        sqlx::query(
            r"
            UPDATE llm_tasks
            SET status = 'failed',
                workflow_state = 'failed',
                error_type = 'queue_terminal_failure',
                error_message = $3,
                completed_at = timezone('UTC', clock_timestamp()),
                updated_at = timezone('UTC', clock_timestamp())
            WHERE id::bigint = $1
              AND user_id::bigint = $2
              AND status NOT IN ('completed', 'failed', 'cancelled')
            ",
        )
        .bind(id)
        .bind(owner_user_id)
        .bind(message)
        .execute(&mut **tx)
        .await?;
    }
    if task_type == "process_news_item" {
        let id = payload
            .get("news_item_id")
            .and_then(serde_json::Value::as_i64);
        sqlx::query(
            r"
            UPDATE news_items
            SET status = 'failed',
                raw_metadata = (COALESCE(raw_metadata, '{}'::json)::jsonb || jsonb_build_object('processing_error', $2::text))::json,
                updated_at = timezone('UTC', clock_timestamp())
            WHERE id::bigint = $1
              AND status IN ('pending', 'processing')
            ",
        )
        .bind(id)
        .bind(message)
        .execute(&mut **tx)
        .await?;
    }
    if task_type == "generate_image" {
        settle_failed_artwork(tx, task_id, content_id, message).await?;
    } else if matches!(
        task_type,
        "analyze_url"
            | "process_content"
            | "summarize"
            | "process_podcast_media"
            | "download_tweet_video_audio"
            | "transcribe_tweet_video"
    ) {
        sqlx::query(
            r"
            UPDATE contents
            SET status = 'failed',
                error_message = $2,
                updated_at = timezone('UTC', clock_timestamp())
            WHERE id::bigint = $1
              AND status NOT IN ('completed', 'failed', 'skipped')
            ",
        )
        .bind(content_id)
        .bind(message)
        .execute(&mut **tx)
        .await?;
    }
    if matches!(task_type, "scrape" | "backfill_feeds") {
        settle_failed_sources(tx, task_type, owner_user_id, payload).await?;
    }
    Ok(())
}

async fn settle_failed_sources(
    tx: &mut Transaction<'_, Postgres>,
    task_type: &str,
    owner_user_id: Option<i64>,
    payload: &serde_json::Map<String, serde_json::Value>,
) -> Result<(), sqlx::Error> {
    let mut keys = Vec::new();
    if task_type == "backfill_feeds" {
        if let Some(ids) = payload
            .get("config_ids")
            .and_then(serde_json::Value::as_array)
        {
            keys.extend(
                ids.iter()
                    .filter_map(serde_json::Value::as_i64)
                    .map(|id| format!("feed:{id}")),
            );
        }
    } else if let Some(id) = payload.get("config_id").and_then(serde_json::Value::as_i64) {
        keys.push(format!("reddit:{id}"));
    } else if let Some(sources) = payload.get("sources").and_then(serde_json::Value::as_array) {
        keys.extend(
            sources
                .iter()
                .filter_map(serde_json::Value::as_str)
                .map(|source| format!("scraper:{source}")),
        );
    }
    if let Some(run_id) = payload
        .get("first_edition_run_id")
        .and_then(serde_json::Value::as_i64)
    {
        let changed = sqlx::query(
            r"
            UPDATE onboarding_first_edition_sources AS source
            SET status = 'unavailable',
                completed_at = now()
            FROM onboarding_first_edition_runs AS run
            WHERE source.run_id = run.id
              AND run.id::bigint = $1
              AND run.status = 'active'
              AND ($3::bigint IS NULL
                  OR run.user_id::bigint = $3)
              AND source.source_key = ANY($2::text[])
              AND source.status NOT IN ('processed', 'unavailable')
            ",
        )
        .bind(run_id)
        .bind(keys)
        .bind(owner_user_id)
        .execute(&mut **tx)
        .await?
        .rows_affected();
        if changed > 0 {
            sqlx::query("UPDATE onboarding_first_edition_runs SET revision = revision + 1 WHERE id::bigint = $1").bind(run_id).execute(&mut **tx).await?;
        }
    }
    Ok(())
}

async fn settle_related_workflow(
    tx: &mut Transaction<'_, Postgres>,
    task_id: i64,
    task_type: &str,
    user_id: i64,
    retry_count: i32,
    payload: &serde_json::Map<String, serde_json::Value>,
    message: &str,
) -> Result<(), sqlx::Error> {
    if matches!(task_type, "chat_turn" | "dig_deeper") {
        let message_id = payload
            .get("message_id")
            .and_then(serde_json::Value::as_i64);
        let current = sqlx::query_as::<_, (i64, Option<i32>)>(
            r"
            SELECT message.session_id::bigint, message.stream_generation
            FROM chat_messages AS message
            JOIN chat_sessions AS session ON session.id = message.session_id
            WHERE message.id::bigint = $1
              AND session.user_id::bigint = $2
              AND message.status = 'processing'
              AND COALESCE(message.stream_generation, 0) <= $3
              AND NOT EXISTS (SELECT 1
                FROM processing_tasks AS newer
                WHERE newer.id::bigint > $4
                  AND newer.status IN ('pending', 'processing')
                  AND newer.payload ->> 'message_id' = $1::bigint::text
                  AND newer.owner_user_id::bigint = $2)
            FOR UPDATE OF message
            ",
        )
        .bind(message_id)
        .bind(user_id)
        .bind(retry_count)
        .bind(task_id)
        .fetch_optional(&mut **tx)
        .await?;
        if let Some((session_id, generation)) = current {
            let llm_task_id = sqlx::query_scalar::<_, i64>(
                r"
                SELECT id::bigint
                FROM llm_tasks
                WHERE user_id::bigint = $1
                  AND input_json ->> 'queue_task_id' = $2::bigint::text
                  AND input_json ->> 'message_id' = $3::bigint::text
                  AND status NOT IN ('completed', 'failed', 'cancelled')
                ORDER BY id DESC
                LIMIT 1
                ",
            )
            .bind(user_id)
            .bind(task_id)
            .bind(message_id)
            .fetch_optional(&mut **tx)
            .await?;
            crate::fail_chat_turn(
                tx,
                &crate::ChatTaskRejection {
                    message_id,
                    session_id: Some(session_id),
                    user_id,
                    llm_task_id,
                    expected_stream_generation: generation,
                    public_message: "This response could not finish. Please retry.".to_owned(),
                    task_message: message.to_owned(),
                    error_type: "queue_terminal_failure".to_owned(),
                },
            )
            .await
            .map_err(|error| sqlx::Error::Protocol(error.to_string()))?;
        }
    }
    if task_type == "generate_audio_episode" {
        let id = payload
            .get("audio_episode_id")
            .and_then(serde_json::Value::as_i64);
        let started = sqlx::query_scalar::<_, chrono::DateTime<chrono::Utc>>(
            r"
            SELECT started_at AT TIME ZONE 'UTC'
            FROM audio_episodes
            WHERE id::bigint = $1
              AND user_id::bigint = $2
              AND status = 'processing'
              AND started_at IS NOT NULL
              AND NOT EXISTS (SELECT 1
                FROM processing_tasks AS newer
                WHERE newer.id::bigint > $3
                  AND newer.task_type = 'generate_audio_episode'
                  AND newer.status IN ('pending', 'processing')
                  AND newer.payload ->> 'audio_episode_id' = $1::bigint::text
                  AND newer.owner_user_id::bigint = $2)
            FOR UPDATE
            ",
        )
        .bind(id)
        .bind(user_id)
        .bind(task_id)
        .fetch_optional(&mut **tx)
        .await?;
        if let (Some(id), Some(started)) = (id, started) {
            crate::fail_audio_episode_generation(tx, user_id, id, started, message, false)
                .await
                .map_err(|error| sqlx::Error::Protocol(error.to_string()))?;
        }
    }
    if task_type == "onboarding_discover" {
        let run = sqlx::query_as::<_, (i64, i32)>(
            r"
            SELECT id::bigint, discovery_retry_count
            FROM onboarding_discovery_runs
            WHERE discovery_task_id::bigint = $1
              AND user_id::bigint = $2
              AND status = 'processing'
              AND discovery_retry_count <= $3
            FOR UPDATE
            ",
        )
        .bind(task_id)
        .bind(user_id)
        .bind(retry_count)
        .fetch_optional(&mut **tx)
        .await?;
        if let Some((id, generation)) = run {
            crate::settle_onboarding_discovery_attempt(
                tx,
                task_id,
                generation,
                id,
                user_id,
                crate::OnboardingAttemptStatus::Failed,
                message,
            )
            .await
            .map_err(|error| sqlx::Error::Protocol(error.to_string()))?;
        }
    }
    if task_type == "discover_feeds" {
        let run = sqlx::query_as::<_, (i64, i32)>(
            r"
            SELECT id::bigint, discovery_retry_count
            FROM feed_discovery_runs
            WHERE discovery_task_id::bigint = $1
              AND user_id::bigint = $2
              AND status = 'processing'
              AND discovery_retry_count <= $3
            FOR UPDATE
            ",
        )
        .bind(task_id)
        .bind(user_id)
        .bind(retry_count)
        .fetch_optional(&mut **tx)
        .await?;
        if let Some((id, generation)) = run {
            crate::settle_feed_discovery_attempt(
                tx,
                task_id,
                generation,
                id,
                user_id,
                crate::OnboardingAttemptStatus::Failed,
                message,
            )
            .await
            .map_err(|error| sqlx::Error::Protocol(error.to_string()))?;
        }
    }
    Ok(())
}

async fn settle_failed_artwork(
    tx: &mut Transaction<'_, Postgres>,
    task_id: i64,
    content_id: Option<i64>,
    message: &str,
) -> Result<(), sqlx::Error> {
    let metadata = sqlx::query_scalar::<_, serde_json::Value>(
        r"
        SELECT content_metadata::jsonb
        FROM contents
        WHERE id::bigint = $1
          AND NOT EXISTS (SELECT 1
            FROM processing_tasks AS newer
            WHERE newer.id::bigint > $2
              AND newer.task_type = 'generate_image'
              AND newer.content_id::bigint = $1
              AND newer.status IN ('pending', 'processing'))
        FOR UPDATE
        ",
    )
    .bind(content_id)
    .bind(task_id)
    .fetch_optional(&mut **tx)
    .await?;
    let Some(mut metadata) = metadata else {
        return Ok(());
    };
    let Some(root) = metadata.as_object_mut() else {
        return Ok(());
    };
    let generated = root
        .get("domain")
        .and_then(|domain| domain.get("image_generated_at"))
        .or_else(|| root.get("image_generated_at"))
        .is_some_and(|value| !value.is_null());
    let status = serde_json::Value::from(if generated { "ready" } else { "failed" });
    let error = serde_json::Value::from(message);
    if let Some(domain) = root
        .get_mut("domain")
        .and_then(serde_json::Value::as_object_mut)
    {
        domain.insert("artwork_status".to_owned(), status.clone());
        domain.insert("artwork_error".to_owned(), error.clone());
    }
    root.insert("artwork_status".to_owned(), status);
    root.insert("artwork_error".to_owned(), error);
    sqlx::query(
        r"
        UPDATE contents
        SET status = CASE WHEN status = 'awaiting_image' THEN 'completed' ELSE status END,
            content_metadata = $2,
            updated_at = timezone('UTC', clock_timestamp())
        WHERE id::bigint = $1
        ",
    )
    .bind(content_id)
    .bind(metadata)
    .execute(&mut **tx)
    .await?;
    Ok(())
}
