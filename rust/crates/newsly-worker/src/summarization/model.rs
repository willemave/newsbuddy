use chrono::{NaiveDateTime, Utc};
use newsly_agent_runtime::ProviderUsage;
use serde_json::Value;
use sqlx::FromRow;

#[derive(Debug, Clone, FromRow)]
pub(super) struct SummarizationSnapshot {
    pub(super) id: i64,
    pub(super) content_type: String,
    pub(super) url: String,
    pub(super) title: Option<String>,
    pub(super) source: Option<String>,
    pub(super) platform: Option<String>,
    pub(super) status: String,
    pub(super) content_metadata: Value,
    pub(super) publication_date: Option<NaiveDateTime>,
    pub(super) body_storage_provider: Option<String>,
    pub(super) body_storage_key: Option<String>,
    pub(super) body_sha256: Option<String>,
}

impl SummarizationSnapshot {
    pub(super) fn body_pointer(&self) -> Option<SummaryBodyPointer> {
        Some(SummaryBodyPointer {
            storage_provider: self.body_storage_provider.clone()?,
            storage_key: self.body_storage_key.clone()?,
            sha256: self.body_sha256.clone()?,
        })
    }

    pub(super) fn is_terminal(&self) -> bool {
        matches!(self.status.as_str(), "failed" | "skipped")
    }

    pub(super) fn publication_date_rfc3339(&self) -> Option<String> {
        self.publication_date
            .map(|value| value.and_utc().to_rfc3339())
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(super) struct SummaryBodyPointer {
    pub(super) storage_provider: String,
    pub(super) storage_key: String,
    pub(super) sha256: String,
}

#[derive(Debug, Clone)]
pub(super) struct PreparedSummarizationAttempt {
    pub(super) task_id: i64,
    pub(super) content: SummarizationSnapshot,
    pub(super) input_fingerprint: String,
}

#[derive(Debug, Clone)]
pub(super) struct SummaryUsage {
    pub(super) provider: String,
    pub(super) model: String,
    pub(super) provider_response_id: Option<String>,
    pub(super) usage: ProviderUsage,
}

#[derive(Debug, Clone)]
pub(super) enum SummarizationMutation {
    Complete {
        summary: Value,
        usage: SummaryUsage,
    },
    Unchanged,
    Failed {
        reason: String,
        retry_scheduled: bool,
        skipped: bool,
    },
}

#[derive(Debug, Clone)]
pub(super) struct SummarizationFinalizationPlan {
    pub(super) attempt: PreparedSummarizationAttempt,
    pub(super) mutation: SummarizationMutation,
    pub(super) finalized_at: chrono::DateTime<Utc>,
}

#[derive(Debug, Clone, PartialEq)]
pub(super) enum SummarizationApplyOutcome {
    Applied(AppliedSummarization),
    ContentMissing,
    SourceChanged,
}

#[derive(Debug, Clone, PartialEq)]
pub(super) struct AppliedSummarization {
    pub(super) content_id: i64,
    pub(super) content_type: String,
    pub(super) status: String,
    pub(super) classification: Option<String>,
    pub(super) metadata: Value,
    pub(super) pending_chat_requests: Vec<PendingChatRequest>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(super) struct PendingChatRequest {
    pub(super) user_id: i64,
    pub(super) initial_message: Option<String>,
}
