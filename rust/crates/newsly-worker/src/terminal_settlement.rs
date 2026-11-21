//! Terminal queue failure settlement: the product projection settles inside a savepoint in the
//! same fenced finalization, so a deterministic settlement error cannot wedge the queue row.

use newsly_queue::{ClaimedTask, QueueError, TaskTransition, TaskType};
use tracing::error;

pub(crate) async fn finish_finalization(
    mut finalization: newsly_queue::FencedFinalization,
    claim: &ClaimedTask,
) -> Result<Option<TaskTransition>, QueueError> {
    if let Some(message) = finalization.terminal_failure().map(str::to_owned) {
        // Settle the product projection inside a savepoint. A deterministic settlement error
        // (bad payload id, domain mismatch) must not roll back the queue transition too, or the
        // row would be reclaimed after every lease expiry and never become terminal.
        let mut savepoint = sqlx::Connection::begin(&mut **finalization.transaction_mut()).await?;
        let settled = newsly_db::settle_failed_task(
            &mut savepoint,
            claim.id,
            terminal_task_settlement(claim.task_type),
            claim.content_id,
            claim.owner_user_id,
            &message,
        )
        .await;
        match settled {
            Ok(()) => savepoint.commit().await?,
            Err(error) if is_deterministic_settlement_error(&error) => {
                savepoint.rollback().await?;
                finalization.prefix_terminal_failure(SETTLEMENT_FAILED_PREFIX);
                error!(
                    task_id = claim.id,
                    task_type = claim.task_type.as_str(),
                    error_code = "terminal_settlement_failed",
                    error = %error,
                    "terminal product settlement failed; committing the queue failure without it"
                );
            }
            Err(error) => return Err(QueueError::from(error)),
        }
    }
    finalization.finish().await
}

/// Durable marker on a terminal queue row whose product settlement was skipped; the watchdog's
/// terminal mismatch gauge counts these rows.
const SETTLEMENT_FAILED_PREFIX: &str = "settlement_failed: ";

/// Transport, pool and transient database errors keep today's lease-expiry recovery. Errors that
/// would repeat identically on every reclaim are absorbed so the queue row can still terminate.
/// Settlement reports domain/payload errors as `Protocol`. Unique and exclusion violations are
/// excluded because concurrent writers can cause them and a later reclaim may succeed.
fn is_deterministic_settlement_error(error: &sqlx::Error) -> bool {
    match error {
        sqlx::Error::Protocol(_)
        | sqlx::Error::RowNotFound
        | sqlx::Error::ColumnNotFound(_)
        | sqlx::Error::ColumnDecode { .. }
        | sqlx::Error::Decode(_)
        | sqlx::Error::Encode(_) => true,
        sqlx::Error::Database(database) => database.code().is_some_and(|code| {
            code.starts_with("22") || matches!(code.as_ref(), "23502" | "23503" | "23514")
        }),
        _ => false,
    }
}

const fn terminal_task_settlement(task_type: TaskType) -> newsly_db::TerminalTaskSettlement {
    use newsly_db::TerminalTaskSettlement as Settlement;

    match task_type {
        TaskType::Scrape => Settlement::Scrape,
        TaskType::BackfillFeeds => Settlement::BackfillFeeds,
        TaskType::AnalyzeUrl
        | TaskType::ProcessContent
        | TaskType::ProcessPodcastMedia
        | TaskType::DownloadTweetVideoAudio
        | TaskType::TranscribeTweetVideo => Settlement::Content,
        TaskType::Summarize => Settlement::Summarization,
        TaskType::ProcessNewsItem => Settlement::NewsItem,
        TaskType::GenerateImage => Settlement::Artwork,
        TaskType::DigDeeper | TaskType::ChatTurn => Settlement::ChatTurn,
        TaskType::DiscoverFeeds => Settlement::FeedDiscover,
        TaskType::OnboardingDiscover => Settlement::OnboardingDiscover,
        TaskType::GenerateAudioEpisode => Settlement::AudioEpisode,
        TaskType::RunLlmTask => Settlement::LlmTask,
        TaskType::EnrichNewsItemArticle
        | TaskType::FetchNewsItemDiscussion
        | TaskType::SyncIntegration
        | TaskType::BriefingRefresh
        | TaskType::PrepareNewsLens
        | TaskType::ReclusterNewsLenses
        | TaskType::DeleteUserAccount => Settlement::None,
    }
}

#[cfg(test)]
mod tests {
    use newsly_domain::{ResourceKey, RuntimeOwner};
    use newsly_queue::{
        ClaimRequest, ClaimRuntimeScope, EnqueueRequest, QueueKernel, TaskQueue, TaskResult,
    };
    use serde_json::json;
    use sqlx::PgPool;

    use super::{QueueError, TaskType, finish_finalization};

    #[sqlx::test]
    async fn summarization_terminal_failure_preserves_ready_source(pool: PgPool) {
        newsly_db::run_migrations(&pool).await.unwrap();
        let queue = QueueKernel::new(pool.clone());
        let scope = ClaimRuntimeScope::namespaces(
            RuntimeOwner::Rust,
            [ResourceKey::new("summarize").unwrap()],
        )
        .unwrap();
        let claim_request = ClaimRequest::for_queue("summary-failure", TaskQueue::Content, scope);
        for (before, after) in [
            ("awaiting_image", "awaiting_image"),
            ("processing", "failed"),
        ] {
            let content_id: i64 = sqlx::query_scalar(
                "INSERT INTO contents (content_type, url, is_aggregate, status, content_metadata)
                 VALUES ('article', $1, FALSE, $2, '{}') RETURNING id::bigint",
            )
            .bind(format!("https://example.test/summary-{before}"))
            .bind(before)
            .fetch_one(&pool)
            .await
            .unwrap();
            let mut request = EnqueueRequest::new(TaskType::Summarize);
            request.content_id = Some(content_id);
            let task_id = queue.enqueue(request).await.unwrap();
            // Both SQL NULL and JSON null are accepted as empty payloads by the queue.
            sqlx::query("UPDATE processing_tasks SET payload = $2::json WHERE id = $1::bigint")
                .bind(task_id)
                .bind((before == "awaiting_image").then_some("null"))
                .execute(&pool)
                .await
                .unwrap();
            let claim = queue.claim(&claim_request).await.unwrap().unwrap();
            let result = TaskResult::fail(Some("Provider failed".to_owned()), false);
            let finalization = queue
                .begin_fenced_finalization(&claim, &result, 0)
                .await
                .unwrap()
                .unwrap();
            finish_finalization(finalization, &claim)
                .await
                .unwrap()
                .unwrap();
            let (status, queue_status, error): (String, String, String) = sqlx::query_as(
                "SELECT content.status, task.status, task.error_message
                 FROM contents AS content JOIN processing_tasks AS task ON task.content_id = content.id
                 WHERE task.id = $1::bigint",
            )
            .bind(task_id)
            .fetch_one(&pool)
            .await
            .unwrap();
            assert_eq!(
                (status.as_str(), queue_status.as_str(), error.as_str()),
                (after, "failed", "Provider failed")
            );
        }
    }

    #[sqlx::test]
    async fn transient_product_settlement_rolls_back_the_terminal_queue_transition(pool: PgPool) {
        newsly_db::run_migrations(&pool).await.unwrap();
        let user_id: i64 = sqlx::query_scalar(
            "INSERT INTO users (apple_id, email, is_admin, is_active)
             VALUES ('settlement-retry', 'settlement-retry@example.test', FALSE, TRUE) RETURNING id::bigint",
        )
        .fetch_one(&pool)
        .await
        .unwrap();
        let episode_id: i64 = sqlx::query_scalar(
            "INSERT INTO audio_episodes (user_id, kind, status, title, input_hash, source_item_ids,
                                         source_snapshot, prompt_version, audio_content_type, share_enabled, started_at)
             VALUES ($1::bigint::integer, 'briefing', 'processing', 'Test', 'hash', '[]', '{}', 1,
                     'audio/mpeg', FALSE, timezone('UTC', now())) RETURNING id::bigint",
        )
        .bind(user_id)
        .fetch_one(&pool)
        .await
        .unwrap();
        sqlx::raw_sql(
            "CREATE FUNCTION fixture_settlement_conflict() RETURNS trigger LANGUAGE plpgsql AS $$
             BEGIN RAISE EXCEPTION 'transient settlement fixture' USING ERRCODE = '40001'; END; $$;
             CREATE TRIGGER fixture_settlement_conflict BEFORE UPDATE ON audio_episodes
             FOR EACH ROW EXECUTE FUNCTION fixture_settlement_conflict();",
        )
        .execute(&pool)
        .await
        .unwrap();
        let queue = QueueKernel::new(pool.clone());
        let mut request = EnqueueRequest::new(TaskType::GenerateAudioEpisode);
        request.owner_user_id = Some(user_id);
        request.payload = json!({"audio_episode_id": episode_id, "user_id": user_id})
            .as_object()
            .cloned();
        let task_id = queue.enqueue(request).await.unwrap();
        let scope = ClaimRuntimeScope::namespaces(
            RuntimeOwner::Rust,
            [ResourceKey::new("generate_audio_episode").unwrap()],
        )
        .unwrap();
        let claim = queue
            .claim(&ClaimRequest::for_queue(
                "settlement-retry",
                TaskQueue::AudioEpisode,
                scope,
            ))
            .await
            .unwrap()
            .unwrap();
        let result = TaskResult::fail(Some("Audio provider failed".to_owned()), false);
        let finalization = queue
            .begin_fenced_finalization(&claim, &result, 0)
            .await
            .unwrap()
            .unwrap();
        let error = finish_finalization(finalization, &claim).await.unwrap_err();
        assert!(
            matches!(error, QueueError::Sqlx(sqlx::Error::Database(error)) if error.code().as_deref() == Some("40001"))
        );
        let state: (String, bool, Option<String>) = sqlx::query_as(
            "SELECT status, lease_token IS NOT NULL, error_message FROM processing_tasks WHERE id = $1::bigint",
        )
        .bind(task_id)
        .fetch_one(&pool)
        .await
        .unwrap();
        assert_eq!(state, ("processing".to_owned(), true, None));
    }
}
