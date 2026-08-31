use std::path::PathBuf;

use newsly_db::{MediaMutation, MediaTranscriptionUsage};

#[derive(Debug, Clone)]
pub(super) struct MediaFinalizationPlan {
    pub(super) content_id: i64,
    pub(super) mutation: MediaMutation,
    pub(super) usage: Option<MediaTranscriptionUsage>,
    pub(super) cleanup_tweet_attempt: Option<PathBuf>,
}
