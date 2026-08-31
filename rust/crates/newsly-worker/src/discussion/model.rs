use std::collections::BTreeMap;

use chrono::{DateTime, NaiveDateTime, Utc};
use newsly_agent_runtime::ProviderUsage;
use serde_json::Value;
use uuid::Uuid;

#[derive(Debug, Clone, PartialEq)]
pub(super) struct DiscussionSnapshot {
    pub(super) discussion_id: i64,
    pub(super) news_item_id: i64,
    pub(super) owner_user_id: Option<i64>,
    pub(super) platform: String,
    pub(super) external_id: String,
    pub(super) discussion_url: String,
    pub(super) title: Option<String>,
    pub(super) author: Option<String>,
    pub(super) score: Option<i32>,
    pub(super) comment_count: Option<i32>,
    pub(super) raw_comments_sha256: Option<String>,
    pub(super) summary: Option<Value>,
    pub(super) summary_status: String,
    pub(super) summary_input_sha256: Option<String>,
    pub(super) summary_comment_fingerprints: Option<BTreeMap<String, String>>,
    pub(super) summary_seen_input_sha256: Option<String>,
    pub(super) summary_incremental_update_count: i32,
    pub(super) summary_generated_at: Option<NaiveDateTime>,
    pub(super) claim_token: Uuid,
}

#[derive(Debug, Clone, PartialEq)]
pub(super) enum DiscussionPreparation {
    Ready(DiscussionSnapshot),
    Fresh,
    Deferred(i64),
    Terminal,
    NotFound,
    Unsupported,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(super) struct SummaryPromptComment {
    pub(super) comment_id: String,
    pub(super) author: String,
    pub(super) depth: i64,
    pub(super) text: String,
    pub(super) fingerprint: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(super) struct SummaryPromptLink {
    pub(super) url: String,
    pub(super) title: Option<String>,
    pub(super) comment_id: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(super) struct DiscussionSummaryInput {
    pub(super) prompt: String,
    pub(super) input_sha256: String,
    pub(super) comment_count: i32,
    pub(super) comment_fingerprints: BTreeMap<String, String>,
    pub(super) comments: Vec<SummaryPromptComment>,
    pub(super) links: Vec<SummaryPromptLink>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) enum DiscussionSummaryMode {
    None,
    TrackSummarized,
    TrackSeen,
    Full,
    Merge,
}

impl DiscussionSummaryMode {
    pub(super) const fn usage_label(self) -> &'static str {
        match self {
            Self::Merge => "merge",
            Self::Full => "full",
            Self::None | Self::TrackSummarized | Self::TrackSeen => "none",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(super) struct DiscussionSummaryPlan {
    pub(super) mode: DiscussionSummaryMode,
    pub(super) changed_comments: Vec<SummaryPromptComment>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(super) struct RawDiscussionPointer {
    pub(super) storage_provider: &'static str,
    pub(super) storage_bucket: Option<String>,
    pub(super) storage_key: String,
    pub(super) content_format: &'static str,
    pub(super) sha256: String,
    pub(super) byte_size: i32,
    pub(super) comment_count: i32,
    pub(super) updated_at: DateTime<Utc>,
}

impl RawDiscussionPointer {
    pub(super) fn to_json(&self) -> Value {
        serde_json::json!({
            "kind": "storage",
            "storage_provider": self.storage_provider,
            "storage_bucket": self.storage_bucket,
            "storage_key": self.storage_key,
            "content_format": self.content_format,
            "sha256": self.sha256,
            "byte_size": self.byte_size,
            "comment_count": self.comment_count,
            "updated_at": self.updated_at.to_rfc3339(),
        })
    }
}

#[derive(Debug, Clone, PartialEq)]
pub(super) struct FetchedDiscussionArtifact {
    pub(super) pointer: RawDiscussionPointer,
    pub(super) title: Option<String>,
    pub(super) author: Option<String>,
    pub(super) score: Option<i32>,
    pub(super) declared_comment_count: Option<i32>,
    pub(super) fetched_comment_count: i32,
    pub(super) fetched_at: DateTime<Utc>,
}

#[derive(Debug, Clone)]
pub(super) struct DiscussionUsage {
    pub(super) provider: String,
    pub(super) model: String,
    pub(super) provider_response_id: Option<String>,
    pub(super) usage: ProviderUsage,
    pub(super) summary_mode: DiscussionSummaryMode,
    pub(super) summary_input_sha256: String,
    pub(super) summary_comment_count: i32,
    pub(super) changed_comment_count: i32,
}

#[derive(Debug, Clone)]
pub(super) enum SummaryPublication {
    Preserve,
    NotReady,
    TrackSummarized {
        input: DiscussionSummaryInput,
    },
    TrackSeen {
        input: DiscussionSummaryInput,
    },
    Generated {
        input: DiscussionSummaryInput,
        summary: Value,
        model: String,
        mode: DiscussionSummaryMode,
        usage: DiscussionUsage,
    },
}

#[derive(Debug, Clone)]
pub(super) enum DiscussionMutation {
    Completed {
        fetched: FetchedDiscussionArtifact,
        summary: SummaryPublication,
    },
    Failed {
        reason: String,
        fetched: Option<FetchedDiscussionArtifact>,
    },
    Terminal {
        status: String,
        reason: String,
    },
}

#[derive(Debug, Clone)]
pub(super) struct DiscussionFinalizationPlan {
    pub(super) task_id: i64,
    pub(super) snapshot: DiscussionSnapshot,
    pub(super) mutation: DiscussionMutation,
    pub(super) finalized_at: DateTime<Utc>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(super) enum DiscussionApplyOutcome {
    Applied,
    NewsItemMissing,
    IdentityChanged,
    ClaimLost { retry_after_seconds: i64 },
}
