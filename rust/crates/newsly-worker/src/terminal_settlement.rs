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
        | TaskType::TranscribeTweetVideo
        | TaskType::Summarize => Settlement::Content,
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
