use chrono::{DateTime, Utc};
use newsly_providers::ImageGenerationUsage;
use serde_json::Value;
use sqlx::FromRow;

use super::storage::StagedImage;

#[derive(Debug, Clone, FromRow)]
pub(super) struct ImageContentSnapshot {
    pub(super) id: i64,
    pub(super) content_type: String,
    pub(super) title: Option<String>,
    pub(super) status: String,
    pub(super) content_metadata: Value,
}

#[derive(Debug, Clone)]
pub(super) struct PreparedImageAttempt {
    pub(super) task_id: i64,
    pub(super) content: ImageContentSnapshot,
    pub(super) input_fingerprint: String,
    pub(super) force: bool,
}

#[derive(Debug)]
pub(super) struct ImageFinalizationPlan {
    pub(super) attempt: PreparedImageAttempt,
    pub(super) staged: StagedImage,
    pub(super) usage: ImageGenerationUsage,
    pub(super) generated_at: DateTime<Utc>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) enum ImageTargetOutcome {
    Ready,
    ContentMissing,
    InputChanged,
    ContentBecameNews,
    AlreadyGenerated,
    InvalidStatus,
}
