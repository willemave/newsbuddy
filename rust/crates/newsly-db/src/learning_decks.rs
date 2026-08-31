use std::collections::BTreeSet;

use chrono::{DateTime, NaiveDateTime, SecondsFormat, Utc};
use serde_json::{Map, Value, json};
use sqlx::{AssertSqlSafe, FromRow, PgPool, Postgres, Transaction};
use thiserror::Error;

use crate::content_actions::{ContentActionRepositoryError, save_content_to_knowledge};

const ACTIVE_ATTEMPT_STATUSES: &[&str] = &[
    "queued",
    "preparing",
    "running",
    "awaiting_approval",
    "applying",
];

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LearningDeckSourceProjection {
    pub source_kind: String,
    pub source_identity: String,
    pub source_url: Option<String>,
    pub source_content_id: Option<i64>,
    pub source_title: String,
    pub source_metadata: Map<String, Value>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct LearningDeckAttemptProjection {
    pub id: i64,
    pub status: String,
    pub interests_prompt: Option<String>,
    pub timeline: Vec<LearningDeckTimelineProjection>,
    pub error_type: Option<String>,
    pub error_message: Option<String>,
    pub started_at: Option<DateTime<Utc>>,
    pub completed_at: Option<DateTime<Utc>>,
    pub created_at: DateTime<Utc>,
    pub updated_at: Option<DateTime<Utc>>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LearningDeckTimelineProjection {
    pub status: String,
    pub note: String,
    pub created_at: DateTime<Utc>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct LearningDeckProjection {
    pub id: i64,
    pub user_id: i64,
    pub title: String,
    pub source_kind: String,
    pub source_url: Option<String>,
    pub source_content_id: Option<i64>,
    pub source_metadata: Map<String, Value>,
    pub share_enabled: bool,
    pub viewer_available: bool,
    pub source_notes_available: bool,
    pub thumbnail_available: bool,
    pub artifact_storage_prefix: Option<String>,
    pub latest_successful_attempt_id: Option<i64>,
    pub latest_attempt: Option<LearningDeckAttemptProjection>,
    pub created_at: DateTime<Utc>,
    pub updated_at: Option<DateTime<Utc>>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct HostedLearningDeckProjection {
    pub id: i64,
    pub user_id: i64,
    pub artifact_storage_prefix: Option<String>,
    pub deck_object_key: Option<String>,
    pub source_notes_html_object_key: Option<String>,
    pub artifact_object_keys: BTreeSet<String>,
    pub share_enabled: bool,
    pub share_token_hash: Option<String>,
    pub share_token_nonce: Option<String>,
    pub latest_successful_attempt_id: Option<i64>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ContentSourceOutcome {
    Ready(LearningDeckSourceProjection),
    NotFoundOrNotReady,
    TextUnavailable,
}

#[derive(Debug, Clone, PartialEq)]
pub struct VisibleNewsItemProjection {
    pub id: i64,
    pub article_url: Option<String>,
    pub canonical_story_url: Option<String>,
    pub article_domain: Option<String>,
    pub raw_metadata: Map<String, Value>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct ConvertedNewsSource {
    pub source: LearningDeckSourceProjection,
    pub enqueue_process_content: bool,
    pub enqueue_agent_data_sync: bool,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CreateLearningDeckOutcome {
    AttemptCreated { deck_id: i64, task_id: i64 },
    ExistingActiveAttempt { deck_id: i64 },
    AnotherDeckActive,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RetryLearningDeckOutcome {
    AttemptCreated { deck_id: i64, task_id: i64 },
    ExistingActiveRetry { deck_id: i64 },
    DeckNotFound,
    AnotherDeckActive,
    NoFailedAttempt,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum EnableLearningDeckShareOutcome {
    Ready { deck_id: i64, nonce: String },
    DeckNotFound,
    DeckNotReady,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DisableLearningDeckShareOutcome {
    Disabled,
    DeckNotFound,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DeletedLearningDeck {
    pub object_keys: Vec<String>,
}

#[derive(Debug, Clone, FromRow)]
struct LearningDeckRow {
    id: i64,
    user_id: i64,
    source_kind: String,
    source_url: Option<String>,
    source_content_id: Option<i64>,
    source_title: Option<String>,
    source_metadata: Value,
    stored_title: String,
    artifact_storage_prefix: Option<String>,
    deck_object_key: Option<String>,
    source_notes_html_object_key: Option<String>,
    artifact_object_keys: Value,
    share_enabled: bool,
    created_at: NaiveDateTime,
    updated_at: Option<NaiveDateTime>,
    latest_successful_task_id: Option<i64>,
    latest_successful_run_id: Option<i64>,
    content_title: Option<String>,
    content_metadata: Option<Value>,
    task_id: Option<i64>,
    task_status: Option<String>,
    task_input_json: Option<Value>,
    task_status_history: Option<Value>,
    task_error_type: Option<String>,
    task_error_message: Option<String>,
    task_started_at: Option<NaiveDateTime>,
    task_completed_at: Option<NaiveDateTime>,
    task_created_at: Option<NaiveDateTime>,
    task_updated_at: Option<NaiveDateTime>,
    run_id: Option<i64>,
    run_status: Option<String>,
    run_interests_prompt: Option<String>,
    run_timeline: Option<Value>,
    run_error_message: Option<String>,
    run_started_at: Option<NaiveDateTime>,
    run_completed_at: Option<NaiveDateTime>,
    run_created_at: Option<NaiveDateTime>,
    run_updated_at: Option<NaiveDateTime>,
}

#[derive(Debug, Clone, FromRow)]
struct ContentSourceRow {
    id: i64,
    content_type: String,
    url: String,
    source_url: Option<String>,
    title: Option<String>,
    status: String,
    content_metadata: Value,
    body_available: bool,
}

#[derive(Debug, Clone, FromRow)]
struct NewsItemRow {
    id: i64,
    article_url: Option<String>,
    canonical_story_url: Option<String>,
    article_domain: Option<String>,
    raw_metadata: Value,
}

#[derive(Debug, Clone, FromRow)]
struct ActiveAttemptRow {
    subject_id: Option<i64>,
    input_json: Value,
}

#[derive(Debug, Clone, FromRow)]
struct AttemptStateRow {
    id: i64,
    status: String,
    interests_prompt: Option<String>,
}

#[derive(Debug, Clone, FromRow)]
struct CanonicalContentRow {
    id: i64,
    content_type: String,
    url: String,
    source_url: Option<String>,
    title: Option<String>,
    status: String,
    content_metadata: Value,
}

#[derive(Debug, Clone, FromRow)]
struct PersistedDeckSourceRow {
    source_kind: String,
    source_identity: String,
    source_url: Option<String>,
    source_content_id: Option<i64>,
    source_title: Option<String>,
    title: String,
    source_metadata: Value,
}

#[derive(Debug, Clone, FromRow)]
struct HostedLearningDeckRow {
    id: i64,
    user_id: i64,
    artifact_storage_prefix: Option<String>,
    deck_object_key: Option<String>,
    source_notes_html_object_key: Option<String>,
    artifact_object_keys: Value,
    share_enabled: bool,
    share_token_hash: Option<String>,
    share_token_nonce: Option<String>,
    latest_successful_task_id: Option<i64>,
    latest_successful_run_id: Option<i64>,
}

#[derive(Debug, Error)]
pub enum LearningDeckRepositoryError {
    #[error("PostgreSQL Learning Deck operation failed")]
    Sqlx(#[from] sqlx::Error),
    #[error("content overlay persistence failed")]
    ContentAction(#[from] ContentActionRepositoryError),
    #[error("submitted content {0} disappeared before deck creation")]
    SubmittedContentMissing(i64),
    #[error("converted article row for {0:?} disappeared")]
    ConvertedArticleMissing(String),
    #[error("Learning Deck content source does not exist")]
    ContentSourceMissing,
    #[error("Learning Deck does not have a content source id")]
    DeckContentSourceMissing,
    #[error("unsupported Learning Deck source kind {0:?}")]
    UnsupportedSource(String),
}

mod attempts;
mod canonical;
mod common;
mod lifecycle;
mod projection;
mod sources;

pub use attempts::{
    create_or_rerun_learning_deck, is_active_learning_deck_conflict, retry_learning_deck,
};
pub(crate) use canonical::prepare_learning_deck_generation_source;
pub use lifecycle::{
    delete_learning_deck, disable_learning_deck_share, persist_learning_deck_share,
    prepare_enable_learning_deck_share,
};
pub use projection::{get_hosted_learning_deck, get_learning_deck, list_learning_decks};
pub use sources::{
    convert_news_item_to_learning_deck_source, find_visible_news_item_for_learning_deck,
    load_submitted_content_learning_deck_source, resolve_content_learning_deck_source,
};
