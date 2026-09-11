use thiserror::Error;

#[derive(Debug, Error)]
pub(in crate::news_item) enum NewsRepositoryError {
    #[error("content-analysis usage was passed to a news-item extraction finalizer")]
    UnexpectedContentAnalysisUsage,
    #[error("accepted relation candidate {0} was not loaded")]
    MissingAcceptedCandidate(i64),
    #[error("news representative {0} disappeared")]
    MissingRepresentative(i64),
    #[error(transparent)]
    Sqlx(#[from] sqlx::Error),
    #[error(transparent)]
    Queue(#[from] newsly_queue::QueueError),
    #[error(transparent)]
    ContentSubmission(#[from] newsly_db::ContentSubmissionRepositoryError),
    #[error(transparent)]
    Json(#[from] serde_json::Error),
}
