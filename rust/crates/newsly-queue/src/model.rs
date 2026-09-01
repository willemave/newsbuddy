use std::fmt::{self, Display, Formatter};
use std::str::FromStr;

use chrono::{DateTime, Utc};
use newsly_domain::{LeaseToken, RuntimeOwner};
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};
use thiserror::Error;

use crate::TaskExecutorStamp;

macro_rules! string_enum {
    ($name:ident { $($variant:ident => $value:literal),+ $(,)? }) => {
        #[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
        #[serde(rename_all = "snake_case")]
        pub enum $name {
            $($variant),+
        }

        impl $name {
            /// Every supported wire value, in declaration order.
            pub const ALL: &'static [Self] = &[$(Self::$variant),+];

            pub const fn as_str(self) -> &'static str {
                match self {
                    $(Self::$variant => $value),+
                }
            }
        }

        impl Display for $name {
            fn fmt(&self, formatter: &mut Formatter<'_>) -> fmt::Result {
                formatter.write_str(self.as_str())
            }
        }

        impl FromStr for $name {
            type Err = UnknownQueueValue;

            fn from_str(value: &str) -> Result<Self, Self::Err> {
                match value {
                    $($value => Ok(Self::$variant)),+,
                    _ => Err(UnknownQueueValue {
                        kind: stringify!($name),
                        value: value.to_owned(),
                    }),
                }
            }
        }
    };
}

string_enum!(TaskType {
    Scrape => "scrape",
    BackfillFeeds => "backfill_feeds",
    AnalyzeUrl => "analyze_url",
    ProcessContent => "process_content",
    EnrichNewsItemArticle => "enrich_news_item_article",
    ProcessNewsItem => "process_news_item",
    ProcessPodcastMedia => "process_podcast_media",
    DownloadTweetVideoAudio => "download_tweet_video_audio",
    TranscribeTweetVideo => "transcribe_tweet_video",
    Summarize => "summarize",
    FetchNewsItemDiscussion => "fetch_news_item_discussion",
    GenerateImage => "generate_image",
    DiscoverFeeds => "discover_feeds",
    OnboardingDiscover => "onboarding_discover",
    DigDeeper => "dig_deeper",
    ChatTurn => "chat_turn",
    SyncIntegration => "sync_integration",
    GenerateAudioEpisode => "generate_audio_episode",
    RunLlmTask => "run_llm_task",
    BriefingRefresh => "briefing_refresh",
    SyncAgentData => "sync_agent_data",
    IndexAgentData => "index_agent_data",
    BackfillAgentData => "backfill_agent_data",
    ReconcileAgentData => "reconcile_agent_data",
    DeleteUserAccount => "delete_user_account",
});

string_enum!(TaskQueue {
    Content => "content",
    Media => "media",
    AudioEpisode => "audio_episode",
    Image => "image",
    Onboarding => "onboarding",
    Backfill => "backfill",
    Discussion => "discussion",
    Twitter => "twitter",
    Chat => "chat",
    Llm => "llm",
});

string_enum!(TaskStatus {
    Pending => "pending",
    Processing => "processing",
    Completed => "completed",
    Failed => "failed",
});

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct TaskSpec {
    pub task_type: TaskType,
    pub queue: TaskQueue,
    pub handler: &'static str,
    pub dedupe_by_content: bool,
    pub requires_owner: bool,
}

impl TaskType {
    pub const fn spec(self) -> TaskSpec {
        let (queue, dedupe_by_content, requires_owner) = match self {
            Self::Scrape | Self::AnalyzeUrl | Self::EnrichNewsItemArticle => {
                (TaskQueue::Content, false, false)
            }
            Self::ProcessContent | Self::ProcessNewsItem | Self::Summarize => {
                (TaskQueue::Content, true, false)
            }
            Self::DiscoverFeeds => (TaskQueue::Content, false, true),
            Self::ProcessPodcastMedia
            | Self::DownloadTweetVideoAudio
            | Self::TranscribeTweetVideo => (TaskQueue::Media, true, false),
            Self::FetchNewsItemDiscussion => (TaskQueue::Discussion, true, false),
            Self::GenerateImage => (TaskQueue::Image, true, false),
            Self::OnboardingDiscover => (TaskQueue::Onboarding, true, true),
            Self::DigDeeper | Self::ChatTurn => (TaskQueue::Chat, false, true),
            Self::SyncIntegration => (TaskQueue::Twitter, true, true),
            Self::GenerateAudioEpisode => (TaskQueue::AudioEpisode, true, true),
            Self::RunLlmTask | Self::BriefingRefresh => (TaskQueue::Llm, false, true),
            Self::BackfillFeeds
            | Self::SyncAgentData
            | Self::IndexAgentData
            | Self::BackfillAgentData
            | Self::ReconcileAgentData => (TaskQueue::Backfill, false, true),
            Self::DeleteUserAccount => (TaskQueue::Backfill, false, false),
        };
        TaskSpec {
            task_type: self,
            queue,
            handler: self.handler_name(),
            dedupe_by_content,
            requires_owner,
        }
    }

    /// Returns the concrete Rust handler type that owns this durable task contract.
    pub const fn handler_name(self) -> &'static str {
        match self {
            Self::Scrape => "newsly_worker::scrape::ScrapeHandler",
            Self::BackfillFeeds => "newsly_worker::feed_backfill::BackfillFeedsHandler",
            Self::AnalyzeUrl => "newsly_worker::content::AnalyzeUrlHandler",
            Self::ProcessContent => "newsly_worker::content::ProcessContentHandler",
            Self::EnrichNewsItemArticle => "newsly_worker::news_item::EnrichNewsItemArticleHandler",
            Self::ProcessNewsItem => "newsly_worker::news_item::ProcessNewsItemHandler",
            Self::ProcessPodcastMedia => "newsly_worker::media::ProcessPodcastMediaHandler",
            Self::DownloadTweetVideoAudio => "newsly_worker::media::DownloadTweetVideoAudioHandler",
            Self::TranscribeTweetVideo => "newsly_worker::media::TranscribeTweetVideoHandler",
            Self::Summarize => "newsly_worker::summarization::SummarizeHandler",
            Self::FetchNewsItemDiscussion => {
                "newsly_worker::discussion::FetchNewsItemDiscussionHandler"
            }
            Self::GenerateImage => "newsly_worker::image_generation::GenerateImageHandler",
            Self::DiscoverFeeds => "newsly_worker::feed_discovery::DiscoverFeedsHandler",
            Self::OnboardingDiscover => {
                "newsly_worker::onboarding_discovery::OnboardingDiscoverHandler"
            }
            Self::DigDeeper | Self::ChatTurn => "newsly_worker::chat_turn::ChatPartitionHandler",
            Self::SyncIntegration => "newsly_worker::x_sync::XSyncIntegrationHandler",
            Self::GenerateAudioEpisode => {
                "newsly_worker::audio_episode::GenerateAudioEpisodeHandler"
            }
            Self::RunLlmTask => "newsly_worker::run_llm_task::RunLlmTaskHandler",
            Self::BriefingRefresh => "newsly_worker::briefing_refresh::BriefingRefreshHandler",
            Self::SyncAgentData => "newsly_worker::agent_data::SyncAgentDataHandler",
            Self::IndexAgentData => "newsly_worker::agent_data::IndexAgentDataHandler",
            Self::BackfillAgentData => "newsly_worker::agent_data::BackfillAgentDataHandler",
            Self::ReconcileAgentData => "newsly_worker::agent_data::ReconcileAgentDataHandler",
            Self::DeleteUserAccount => "newsly_account_deletion_worker::AccountDeletionHandler",
        }
    }

    pub const fn reclaim_consumes_retry(self) -> bool {
        matches!(
            self,
            Self::ChatTurn | Self::DigDeeper | Self::GenerateAudioEpisode | Self::RunLlmTask
        )
    }

    /// Applies the same defaults, null elision, and closed/open-object rules as the checked-in
    /// task schemas. This is deliberately performed both at enqueue and before dispatch.
    ///
    /// # Errors
    ///
    /// Returns a field-specific error when the payload does not satisfy the task contract.
    #[allow(clippy::too_many_lines)]
    pub fn normalize_payload(
        self,
        payload: Option<Map<String, Value>>,
    ) -> Result<Map<String, Value>, PayloadError> {
        let mut payload = payload.unwrap_or_default();

        if matches!(
            self,
            Self::SyncAgentData
                | Self::IndexAgentData
                | Self::BackfillAgentData
                | Self::ReconcileAgentData
        ) {
            let allowed: &[&str] = match self {
                Self::SyncAgentData => &[
                    "user_id",
                    "content_ids",
                    "news_item_ids",
                    "chat_session_ids",
                    "briefing_dates",
                ],
                Self::IndexAgentData => &["user_id"],
                Self::BackfillAgentData => &["user_id", "stage", "before_id"],
                Self::ReconcileAgentData => &["user_id", "before_id"],
                _ => unreachable!(),
            };
            if let Some(key) = payload.keys().find(|key| !allowed.contains(&key.as_str())) {
                return Err(PayloadError::UnexpectedField {
                    task_type: self,
                    field: key.clone(),
                });
            }
        }

        match self {
            Self::AnalyzeUrl => {
                optional_integer(&mut payload, self, "content_id", false)?;
                optional_string(&mut payload, self, "instruction")?;
                default_bool(&mut payload, self, "crawl_links", false)?;
                default_bool(&mut payload, self, "subscribe_to_feed", false)?;
            }
            Self::ProcessContent
            | Self::DownloadTweetVideoAudio
            | Self::TranscribeTweetVideo
            | Self::Summarize => {
                optional_integer(&mut payload, self, "content_id", false)?;
            }
            Self::ProcessPodcastMedia => {
                optional_integer(&mut payload, self, "content_id", false)?;
                optional_string(&mut payload, self, "media_url")?;
            }
            Self::GenerateImage => {
                optional_integer(&mut payload, self, "content_id", false)?;
                default_bool(&mut payload, self, "force", false)?;
            }
            Self::ProcessNewsItem | Self::FetchNewsItemDiscussion => {
                required_integer(&payload, self, "news_item_id", false)?;
            }
            Self::EnrichNewsItemArticle => {
                required_integer(&payload, self, "news_item_id", true)?;
            }
            Self::DiscoverFeeds | Self::DeleteUserAccount => {
                required_integer(&payload, self, "user_id", false)?;
            }
            Self::OnboardingDiscover => {
                required_integer(&payload, self, "user_id", false)?;
                optional_integer(&mut payload, self, "run_id", true)?;
            }
            Self::DigDeeper => {
                required_integer(&payload, self, "user_id", false)?;
                optional_integer(&mut payload, self, "session_id", true)?;
                optional_integer(&mut payload, self, "message_id", true)?;
                optional_string(&mut payload, self, "initial_message")?;
                optional_string(&mut payload, self, "prompt")?;
            }
            Self::ChatTurn => {
                required_integer(&payload, self, "user_id", false)?;
                required_integer(&payload, self, "session_id", false)?;
                required_integer(&payload, self, "message_id", false)?;
            }
            Self::GenerateAudioEpisode => {
                required_integer(&payload, self, "user_id", true)?;
                required_integer(&payload, self, "audio_episode_id", true)?;
            }
            Self::RunLlmTask => {
                required_integer(&payload, self, "user_id", false)?;
                required_integer(&payload, self, "llm_task_id", false)?;
            }
            Self::SyncIntegration => {
                required_integer(&payload, self, "user_id", false)?;
                default_string(&mut payload, self, "provider", "x")?;
                default_string(&mut payload, self, "trigger", "cron")?;
            }
            Self::BriefingRefresh => {
                required_integer(&payload, self, "user_id", false)?;
                default_string(&mut payload, self, "mode", "append")?;
            }
            Self::BackfillFeeds => {
                required_integer(&payload, self, "user_id", true)?;
                required_integer_array(&payload, self, "config_ids", false, true)?;
                let count = required_integer(&payload, self, "count", true)?;
                if count > 50 {
                    return Err(PayloadError::OutOfRange {
                        task_type: self,
                        field: "count",
                    });
                }
                optional_integer(&mut payload, self, "first_edition_run_id", true)?;
                payload.retain(|key, _| {
                    matches!(
                        key.as_str(),
                        "user_id" | "config_ids" | "count" | "first_edition_run_id"
                    )
                });
            }
            Self::SyncAgentData => {
                required_integer(&payload, self, "user_id", true)?;
                default_array(&mut payload, self, "content_ids", ValueKind::Integer)?;
                default_array(&mut payload, self, "news_item_ids", ValueKind::Integer)?;
                default_array(&mut payload, self, "chat_session_ids", ValueKind::Integer)?;
                default_array(&mut payload, self, "briefing_dates", ValueKind::String)?;
            }
            Self::IndexAgentData => {
                required_integer(&payload, self, "user_id", true)?;
            }
            Self::BackfillAgentData => {
                required_integer(&payload, self, "user_id", true)?;
                optional_integer(&mut payload, self, "before_id", true)?;
                default_string(&mut payload, self, "stage", "knowledge")?;
                let stage = payload
                    .get("stage")
                    .and_then(Value::as_str)
                    .unwrap_or_default();
                if !matches!(
                    stage,
                    "knowledge" | "content" | "news" | "chats" | "briefings"
                ) {
                    return Err(PayloadError::OutOfRange {
                        task_type: self,
                        field: "stage",
                    });
                }
            }
            Self::ReconcileAgentData => {
                required_integer(&payload, self, "user_id", true)?;
                optional_integer(&mut payload, self, "before_id", true)?;
            }
            Self::Scrape => {
                if payload.contains_key("sources") {
                    let sources = payload.get("sources").and_then(Value::as_array).ok_or(
                        PayloadError::WrongType {
                            task_type: self,
                            field: "sources",
                        },
                    )?;
                    if sources.is_empty() {
                        return Err(PayloadError::OutOfRange {
                            task_type: self,
                            field: "sources",
                        });
                    }
                    if sources.iter().any(|source| !source.is_string()) {
                        return Err(PayloadError::WrongType {
                            task_type: self,
                            field: "sources",
                        });
                    }
                } else {
                    payload.insert("sources".to_owned(), Value::Array(vec![Value::from("all")]));
                }
                optional_integer(&mut payload, self, "first_edition_run_id", true)?;
            }
        }

        payload.retain(|_, value| !value.is_null());
        Ok(payload)
    }
}

#[derive(Debug, Clone)]
pub struct EnqueueRequest {
    pub task_type: TaskType,
    pub content_id: Option<i64>,
    pub payload: Option<Map<String, Value>>,
    pub queue_name: Option<TaskQueue>,
    pub dedupe: Option<bool>,
    pub dedupe_key: Option<String>,
    pub owner_user_id: Option<i64>,
    pub access_user_id: Option<i64>,
    pub available_at: Option<DateTime<Utc>>,
}

impl EnqueueRequest {
    pub const fn new(task_type: TaskType) -> Self {
        Self {
            task_type,
            content_id: None,
            payload: None,
            queue_name: None,
            dedupe: None,
            dedupe_key: None,
            owner_user_id: None,
            access_user_id: None,
            available_at: None,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ClaimedTask {
    pub id: i64,
    pub owner_user_id: Option<i64>,
    pub task_type: TaskType,
    pub content_id: Option<i64>,
    pub payload: Map<String, Value>,
    pub retry_count: i32,
    pub status: TaskStatus,
    pub queue_name: TaskQueue,
    pub executor_runtime: RuntimeOwner,
    pub executor_version: i64,
    pub executor_namespace: String,
    pub created_at: Option<DateTime<Utc>>,
    pub available_at: DateTime<Utc>,
    pub started_at: DateTime<Utc>,
    pub locked_at: DateTime<Utc>,
    pub locked_by: String,
    pub lease_token: LeaseToken,
    pub lease_expires_at: DateTime<Utc>,
}

impl ClaimedTask {
    /// Reconstructs the immutable executor fence stamped at enqueue time.
    ///
    /// # Errors
    ///
    /// Returns a validation error for invalid durable ownership values.
    pub fn executor_stamp(&self) -> Result<TaskExecutorStamp, QueueModelError> {
        Ok(TaskExecutorStamp {
            runtime: self.executor_runtime,
            ownership_version: newsly_domain::OwnershipVersion::new(self.executor_version)?,
            namespace: newsly_domain::ResourceKey::new(self.executor_namespace.clone())?,
        })
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct TaskResult {
    pub success: bool,
    #[serde(default)]
    pub error_message: Option<String>,
    #[serde(default)]
    pub retry_delay_seconds: Option<i64>,
    #[serde(default = "default_true")]
    pub retryable: bool,
    #[serde(default)]
    pub deferred: bool,
}

impl TaskResult {
    pub const fn ok() -> Self {
        Self {
            success: true,
            error_message: None,
            retry_delay_seconds: None,
            retryable: true,
            deferred: false,
        }
    }

    pub fn fail(error_message: impl Into<Option<String>>, retryable: bool) -> Self {
        Self {
            success: false,
            error_message: error_message.into(),
            retry_delay_seconds: None,
            retryable,
            deferred: false,
        }
    }

    pub const fn defer(retry_delay_seconds: i64) -> Self {
        Self {
            success: false,
            error_message: None,
            retry_delay_seconds: Some(retry_delay_seconds),
            retryable: false,
            deferred: true,
        }
    }
}

const fn default_true() -> bool {
    true
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum FinalizationOutcome {
    Succeeded,
    Failed,
    Retry,
    Deferred,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ResolvedFinalization {
    pub outcome: FinalizationOutcome,
    pub error_message: Option<String>,
    pub retry_delay_seconds: Option<i64>,
}

impl ResolvedFinalization {
    /// Resolves one handler result exactly once before the fresh finalization transaction.
    ///
    /// # Errors
    ///
    /// A successful result cannot also be marked deferred.
    pub fn from_result(
        claim: &ClaimedTask,
        result: &TaskResult,
        max_retries: i32,
    ) -> Result<Self, QueueModelError> {
        if result.success && result.deferred {
            return Err(QueueModelError::SuccessfulDeferral);
        }
        if result.success {
            return Ok(Self {
                outcome: FinalizationOutcome::Succeeded,
                error_message: None,
                retry_delay_seconds: None,
            });
        }
        if result.deferred {
            return Ok(Self {
                outcome: FinalizationOutcome::Deferred,
                error_message: None,
                retry_delay_seconds: Some(result.retry_delay_seconds.unwrap_or(0).max(0)),
            });
        }
        let error_message = Some(
            result
                .error_message
                .clone()
                .unwrap_or_else(|| "Task failed without error details".to_owned()),
        );
        if result.retryable && claim.retry_count < max_retries.max(0) {
            let default_delay = if claim.retry_count >= 6 {
                3_600
            } else {
                60 * (1_i64 << claim.retry_count)
            };
            return Ok(Self {
                outcome: FinalizationOutcome::Retry,
                error_message,
                retry_delay_seconds: Some(
                    result.retry_delay_seconds.unwrap_or(default_delay).max(0),
                ),
            });
        }
        Ok(Self {
            outcome: FinalizationOutcome::Failed,
            error_message,
            retry_delay_seconds: None,
        })
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct TaskTransition {
    pub task_type: TaskType,
    pub queue_name: TaskQueue,
    pub content_id: Option<i64>,
    pub error_message: Option<String>,
    pub status: TaskStatus,
    pub retry_count: i32,
    pub retry_delay_seconds: Option<i64>,
    pub deferred: bool,
    pub available_at: DateTime<Utc>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct OwnedWorkPlan {
    pub task_id: i64,
    pub owner_user_id: Option<i64>,
    pub task_type: TaskType,
    pub content_id: Option<i64>,
    pub payload: Map<String, Value>,
    pub retry_count: i32,
    pub queue_name: TaskQueue,
    pub executor_runtime: RuntimeOwner,
    pub executor_version: i64,
    pub executor_namespace: String,
}

impl From<&ClaimedTask> for OwnedWorkPlan {
    fn from(claim: &ClaimedTask) -> Self {
        Self {
            task_id: claim.id,
            owner_user_id: claim.owner_user_id,
            task_type: claim.task_type,
            content_id: claim.content_id,
            payload: claim.payload.clone(),
            retry_count: claim.retry_count,
            queue_name: claim.queue_name,
            executor_runtime: claim.executor_runtime,
            executor_version: claim.executor_version,
            executor_namespace: claim.executor_namespace.clone(),
        }
    }
}

#[derive(Debug, Error, Clone, PartialEq, Eq)]
#[error("unknown {kind} value {value:?}")]
pub struct UnknownQueueValue {
    pub kind: &'static str,
    pub value: String,
}

#[derive(Debug, Error, Clone, PartialEq, Eq)]
pub enum PayloadError {
    #[error("invalid payload for {task_type}: missing {field}")]
    MissingField {
        task_type: TaskType,
        field: &'static str,
    },
    #[error("invalid payload for {task_type}: {field} has the wrong type")]
    WrongType {
        task_type: TaskType,
        field: &'static str,
    },
    #[error("invalid payload for {task_type}: {field} is out of range")]
    OutOfRange {
        task_type: TaskType,
        field: &'static str,
    },
    #[error("invalid payload for {task_type}: unexpected field {field}")]
    UnexpectedField { task_type: TaskType, field: String },
}

#[derive(Debug, Error)]
pub enum QueueModelError {
    #[error("a successful task cannot also be deferred")]
    SuccessfulDeferral,
    #[error(transparent)]
    Ownership(#[from] newsly_domain::InvalidOwnershipValue),
}

#[derive(Debug, Clone, Copy)]
enum ValueKind {
    Integer,
    String,
}

fn required_integer(
    payload: &Map<String, Value>,
    task_type: TaskType,
    field: &'static str,
    positive: bool,
) -> Result<i64, PayloadError> {
    let value = payload
        .get(field)
        .ok_or(PayloadError::MissingField { task_type, field })?;
    let value = value
        .as_i64()
        .ok_or(PayloadError::WrongType { task_type, field })?;
    if positive && value <= 0 {
        return Err(PayloadError::OutOfRange { task_type, field });
    }
    Ok(value)
}

fn optional_integer(
    payload: &mut Map<String, Value>,
    task_type: TaskType,
    field: &'static str,
    positive: bool,
) -> Result<(), PayloadError> {
    if payload.get(field).is_none_or(Value::is_null) {
        payload.remove(field);
        return Ok(());
    }
    required_integer(payload, task_type, field, positive).map(|_| ())
}

fn optional_string(
    payload: &mut Map<String, Value>,
    task_type: TaskType,
    field: &'static str,
) -> Result<(), PayloadError> {
    if payload.get(field).is_none_or(Value::is_null) {
        payload.remove(field);
        return Ok(());
    }
    if payload.get(field).is_none_or(|value| !value.is_string()) {
        return Err(PayloadError::WrongType { task_type, field });
    }
    Ok(())
}

fn default_bool(
    payload: &mut Map<String, Value>,
    task_type: TaskType,
    field: &'static str,
    default: bool,
) -> Result<(), PayloadError> {
    match payload.get(field) {
        None => {
            payload.insert(field.to_owned(), Value::Bool(default));
            Ok(())
        }
        Some(Value::Bool(_)) => Ok(()),
        Some(_) => Err(PayloadError::WrongType { task_type, field }),
    }
}

fn default_string(
    payload: &mut Map<String, Value>,
    task_type: TaskType,
    field: &'static str,
    default: &'static str,
) -> Result<(), PayloadError> {
    match payload.get(field) {
        None => {
            payload.insert(field.to_owned(), Value::from(default));
            Ok(())
        }
        Some(Value::String(_)) => Ok(()),
        Some(_) => Err(PayloadError::WrongType { task_type, field }),
    }
}

fn required_integer_array(
    payload: &Map<String, Value>,
    task_type: TaskType,
    field: &'static str,
    positive: bool,
    nonempty: bool,
) -> Result<(), PayloadError> {
    let values = payload
        .get(field)
        .ok_or(PayloadError::MissingField { task_type, field })?
        .as_array()
        .ok_or(PayloadError::WrongType { task_type, field })?;
    if nonempty && values.is_empty() {
        return Err(PayloadError::OutOfRange { task_type, field });
    }
    if values
        .iter()
        .any(|value| value.as_i64().is_none_or(|value| positive && value <= 0))
    {
        return Err(PayloadError::WrongType { task_type, field });
    }
    Ok(())
}

fn default_array(
    payload: &mut Map<String, Value>,
    task_type: TaskType,
    field: &'static str,
    kind: ValueKind,
) -> Result<(), PayloadError> {
    let Some(value) = payload.get(field) else {
        payload.insert(field.to_owned(), Value::Array(Vec::new()));
        return Ok(());
    };
    let values = value
        .as_array()
        .ok_or(PayloadError::WrongType { task_type, field })?;
    let valid = values.iter().all(|value| match kind {
        ValueKind::Integer => value.as_i64().is_some(),
        ValueKind::String => value.is_string(),
    });
    if !valid {
        return Err(PayloadError::WrongType { task_type, field });
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::{
        ClaimedTask, FinalizationOutcome, PayloadError, ResolvedFinalization, TaskResult,
        TaskStatus, TaskType,
    };

    #[test]
    fn retry_policy_matches_queue_transition_fixture() {
        let claim: ClaimedTask = serde_json::from_value(json!({
            "available_at": "2026-08-30T12:00:01Z",
            "content_id": null,
            "created_at": "2026-08-30T12:00:00Z",
            "executor_namespace": "run_llm_task",
            "executor_runtime": "python",
            "executor_version": 1,
            "id": 41001,
            "lease_expires_at": "2026-08-30T12:01:02Z",
            "lease_token": "00000000-0000-4000-8000-000000000001",
            "locked_at": "2026-08-30T12:00:02Z",
            "locked_by": "fixture-worker",
            "owner_user_id": 51,
            "payload": {"llm_task_id": 8801, "user_id": 51},
            "queue_name": "llm",
            "retry_count": 0,
            "started_at": "2026-08-30T12:00:02Z",
            "status": "processing",
            "task_type": "run_llm_task"
        }))
        .unwrap();
        let mut result = TaskResult::fail(Some("fixture transient failure".to_owned()), true);
        result.retry_delay_seconds = Some(30);
        let resolved = ResolvedFinalization::from_result(&claim, &result, 3).unwrap();
        assert_eq!(resolved.retry_delay_seconds, Some(30));
        assert_eq!(claim.status, TaskStatus::Processing);
    }

    #[test]
    fn payload_normalization_materializes_server_defaults() {
        let normalized = TaskType::AnalyzeUrl.normalize_payload(None).unwrap();
        assert_eq!(normalized.get("crawl_links"), Some(&json!(false)));
        assert_eq!(normalized.get("subscribe_to_feed"), Some(&json!(false)));
        assert!(!normalized.contains_key("content_id"));
    }

    #[test]
    fn enrich_news_item_article_requires_news_item_id() {
        assert_eq!(
            TaskType::EnrichNewsItemArticle.normalize_payload(None),
            Err(PayloadError::MissingField {
                task_type: TaskType::EnrichNewsItemArticle,
                field: "news_item_id",
            })
        );
    }

    #[test]
    fn enrich_news_item_article_rejects_nonpositive_news_item_id() {
        for news_item_id in [0, -1] {
            let payload = json!({"news_item_id": news_item_id}).as_object().cloned();
            assert_eq!(
                TaskType::EnrichNewsItemArticle.normalize_payload(payload),
                Err(PayloadError::OutOfRange {
                    task_type: TaskType::EnrichNewsItemArticle,
                    field: "news_item_id",
                })
            );
        }
    }

    #[test]
    fn enrich_news_item_article_accepts_positive_news_item_id() {
        let payload = json!({"news_item_id": 42}).as_object().unwrap().clone();

        assert_eq!(
            TaskType::EnrichNewsItemArticle.normalize_payload(Some(payload.clone())),
            Ok(payload)
        );
    }

    #[test]
    fn enrich_news_item_article_preserves_unexpected_fields() {
        let payload = json!({"news_item_id": 42, "future_field": "value"})
            .as_object()
            .unwrap()
            .clone();

        assert_eq!(
            TaskType::EnrichNewsItemArticle.normalize_payload(Some(payload.clone())),
            Ok(payload)
        );
    }

    #[test]
    fn generated_queue_transition_cases_match_retry_resolution() {
        let fixture: serde_json::Value = serde_json::from_str(include_str!(
            "../../../../contracts/tasks/queue-transitions.json"
        ))
        .unwrap();
        for case in fixture["cases"].as_array().unwrap() {
            let Some(result) = case.get("result") else {
                continue;
            };
            let claim: ClaimedTask = serde_json::from_value(case["claim"].clone()).unwrap();
            let result: TaskResult = serde_json::from_value(result.clone()).unwrap();
            let resolved = ResolvedFinalization::from_result(&claim, &result, 3).unwrap();
            let expected = &case["expected"];
            let (status, retry_count) = match resolved.outcome {
                FinalizationOutcome::Succeeded => (TaskStatus::Completed, claim.retry_count),
                FinalizationOutcome::Failed => (TaskStatus::Failed, claim.retry_count),
                FinalizationOutcome::Retry => (TaskStatus::Pending, claim.retry_count + 1),
                FinalizationOutcome::Deferred => (TaskStatus::Pending, claim.retry_count),
            };
            assert_eq!(status.as_str(), expected["status"].as_str().unwrap());
            assert_eq!(
                i64::from(retry_count),
                expected["retry_count"].as_i64().unwrap()
            );
            assert_eq!(
                resolved.retry_delay_seconds,
                expected["retry_delay_seconds"].as_i64()
            );
            assert_eq!(
                resolved.outcome == FinalizationOutcome::Deferred,
                expected["deferred"].as_bool().unwrap()
            );
        }
    }
}
