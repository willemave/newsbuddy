use std::path::PathBuf;

use newsly_db::MediaMutation;

#[derive(Debug, Clone)]
pub(super) struct MediaFinalizationPlan {
    pub(super) content_id: i64,
    pub(super) mutation: MediaMutation,
    pub(super) cleanup_tweet_attempt: Option<PathBuf>,
}
