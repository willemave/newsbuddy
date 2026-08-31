//! Durable preparation and publication for `learning_deck` LLM tasks.
//!
//! The repository deliberately exposes immutable values at every external-I/O boundary. Source
//! object reads, E2B/Rig execution, browser validation, and artifact writes happen after the
//! preparation transaction commits. Publication is performed later inside the queue kernel's
//! exact lease-fenced finalization transaction.

use std::collections::HashSet;

use chrono::{DateTime, NaiveDateTime, Utc};
use serde_json::{Map, Value, json};
use sqlx::{FromRow, Postgres, Transaction};
use thiserror::Error;

use crate::learning_decks::{LearningDeckRepositoryError, prepare_learning_deck_generation_source};

const TERMINAL_TASK_STATUSES: [&str; 3] = ["completed", "failed", "cancelled"];
const SOURCE_PREPARATION_TASK_TYPES: [&str; 5] = [
    "analyze_url",
    "process_content",
    "process_podcast_media",
    "download_tweet_video_audio",
    "transcribe_tweet_video",
];

#[derive(Debug, Clone, PartialEq)]
pub struct LearningDeckTaskSnapshot {
    pub id: i64,
    pub user_id: i64,
    pub deck_id: i64,
    pub deck_title: String,
    pub mode: String,
    pub workflow_key: String,
    pub approval_policy: Map<String, Value>,
    pub allowed_actions: Vec<String>,
    pub tool_policy: Map<String, Value>,
    pub input: Map<String, Value>,
    pub interests_prompt: Option<String>,
    pub vm_namespace: String,
    pub workspace_path: String,
    pub shared_workspace_path: String,
    pub created_at: DateTime<Utc>,
    pub source: LearningDeckSourceMaterial,
}

#[derive(Debug, Clone, PartialEq)]
pub enum LearningDeckSourceMaterial {
    Content {
        snapshot: Map<String, Value>,
        content_id: i64,
        content_status: String,
        body: ContentBodyMaterial,
    },
    Github {
        snapshot: Map<String, Value>,
    },
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ContentBodyMaterial {
    pub storage_key: Option<String>,
    pub fallback_text: Option<String>,
}

#[derive(Debug, Clone, PartialEq)]
pub enum LearningDeckPreparationOutcome {
    NotLearningDeck,
    Terminal,
    Cancelled,
    Failed { error_type: String, message: String },
    Ready(LearningDeckTaskSnapshot),
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum LearningDeckSourceSettlement {
    Terminal,
    Cancelled,
    Deferred {
        retry_delay_seconds: i64,
        message: String,
    },
    Failed {
        error_type: String,
        message: String,
    },
}

#[derive(Debug, Clone, PartialEq)]
pub enum MarkLearningDeckRunningOutcome {
    Ready,
    Terminal,
    Cancelled,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct StoredLearningDeckArtifact {
    pub storage_prefix: String,
    pub deck_object_key: String,
    pub source_notes_object_key: String,
    pub source_notes_html_object_key: String,
    pub thumbnail_object_key: Option<String>,
    pub artifact_object_keys: Vec<String>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct LearningDeckModelUsage {
    pub provider: String,
    pub model: String,
    pub provider_response_id: Option<String>,
    pub request_count: u64,
    pub input_tokens: u64,
    pub output_tokens: u64,
    pub cache_read_tokens: u64,
    pub cache_write_tokens: u64,
    pub metadata: Map<String, Value>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct PublishLearningDeck<'a> {
    pub task_id: i64,
    pub user_id: i64,
    pub deck_id: i64,
    pub artifact: &'a StoredLearningDeckArtifact,
    pub browser_validation: &'a Map<String, Value>,
    pub source_metadata_updates: &'a Map<String, Value>,
    pub model_provider: &'a str,
    pub model_name: &'a str,
    pub sandbox_provider: &'a str,
    pub sandbox_id: Option<&'a str>,
    pub agent_log_object_key: Option<&'a str>,
    pub usage_json: &'a Map<String, Value>,
    pub vendor_usage: Option<&'a LearningDeckModelUsage>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum PublishLearningDeckOutcome {
    Published { stale_object_keys: Vec<String> },
    Terminal,
    Cancelled,
    Failed { error_type: String, message: String },
}

#[derive(Debug, Clone, FromRow)]
struct TaskRow {
    id: i64,
    user_id: i64,
    task_kind: String,
    mode: String,
    workflow_key: String,
    status: String,
    approval_policy: Value,
    allowed_actions: Value,
    tool_policy: Value,
    input_json: Value,
    vm_namespace: Option<String>,
    workspace_path: Option<String>,
    shared_workspace_path: Option<String>,
    subject_id: Option<i64>,
    created_at: NaiveDateTime,
}

#[derive(Debug, Clone, FromRow)]
struct DeckRow {
    id: i64,
    user_id: i64,
    source_kind: String,
    source_identity: String,
    source_url: Option<String>,
    source_content_id: Option<i64>,
    source_title: Option<String>,
    source_metadata: Value,
    title: String,
    deleted_at: Option<NaiveDateTime>,
}

#[derive(Debug, Clone, FromRow)]
struct ContentRow {
    id: i64,
    content_type: String,
    status: String,
    content_metadata: Value,
}

/// Locks and prepares one Learning Deck ledger row without crossing an external-I/O boundary.
///
/// The returned source material contains only copied JSON/scalars and an optional object key. The
/// caller must commit before reading that object or starting E2B/Rig.
pub async fn begin_learning_deck_preparation(
    transaction: &mut Transaction<'_, Postgres>,
    task_id: i64,
    user_id: i64,
) -> Result<LearningDeckPreparationOutcome, LearningDeckTaskRepositoryError> {
    let Some(task) = load_task_for_update(transaction, task_id).await? else {
        return Err(LearningDeckTaskRepositoryError::TaskNotFound);
    };
    if task.task_kind != "learning_deck" {
        return Ok(LearningDeckPreparationOutcome::NotLearningDeck);
    }
    if task.user_id != user_id {
        return Err(LearningDeckTaskRepositoryError::OwnershipMismatch);
    }
    if is_terminal(&task.status) {
        return Ok(LearningDeckPreparationOutcome::Terminal);
    }
    let Some(deck_id) = task.subject_id.filter(|value| *value > 0) else {
        fail_task(
            transaction,
            task.id,
            "invalid_subject",
            "Learning Deck task is missing subject_id",
            None,
            None,
            None,
        )
        .await?;
        return Ok(LearningDeckPreparationOutcome::Failed {
            error_type: "invalid_subject".to_owned(),
            message: "Learning Deck task is missing subject_id".to_owned(),
        });
    };
    let Some(deck) = load_deck_for_update(transaction, deck_id).await? else {
        fail_task(
            transaction,
            task.id,
            "deck_not_found",
            "Learning Deck not found",
            None,
            None,
            None,
        )
        .await?;
        return Ok(LearningDeckPreparationOutcome::Failed {
            error_type: "deck_not_found".to_owned(),
            message: "Learning Deck not found".to_owned(),
        });
    };
    if deck.user_id != user_id {
        return Err(LearningDeckTaskRepositoryError::OwnershipMismatch);
    }
    if deck.deleted_at.is_some() {
        cancel_task(transaction, task.id, "Learning Deck was deleted").await?;
        return Ok(LearningDeckPreparationOutcome::Cancelled);
    }

    set_preparing(transaction, task.id, deck.id, "Preparing source material").await?;
    let source = match deck.source_kind.as_str() {
        "content" => {
            if let Some(source) = prepare_content_source(transaction, &deck).await? {
                source
            } else {
                fail_task(
                    transaction,
                    task.id,
                    "source_not_found",
                    "Source content no longer exists",
                    None,
                    None,
                    None,
                )
                .await?;
                return Ok(LearningDeckPreparationOutcome::Failed {
                    error_type: "source_not_found".to_owned(),
                    message: "Source content no longer exists".to_owned(),
                });
            }
        }
        "github_repo" => LearningDeckSourceMaterial::Github {
            snapshot: github_snapshot(&deck),
        },
        other => {
            let message = format!("Unsupported Learning Deck source kind: {other}");
            fail_task(
                transaction,
                task.id,
                "unsupported_source_kind",
                &message,
                None,
                None,
                None,
            )
            .await?;
            return Ok(LearningDeckPreparationOutcome::Failed {
                error_type: "unsupported_source_kind".to_owned(),
                message,
            });
        }
    };
    let Some(vm_namespace) = clean_string(task.vm_namespace.as_deref()) else {
        return fail_invalid_workspace(transaction, task.id).await;
    };
    let Some(workspace_path) = clean_string(task.workspace_path.as_deref()) else {
        return fail_invalid_workspace(transaction, task.id).await;
    };
    let Some(shared_workspace_path) = clean_string(task.shared_workspace_path.as_deref()) else {
        return fail_invalid_workspace(transaction, task.id).await;
    };

    Ok(LearningDeckPreparationOutcome::Ready(
        LearningDeckTaskSnapshot {
            id: task.id,
            user_id: task.user_id,
            deck_id: deck.id,
            deck_title: deck.title,
            mode: task.mode,
            workflow_key: task.workflow_key,
            approval_policy: json_object(task.approval_policy),
            allowed_actions: string_array(task.allowed_actions),
            tool_policy: json_object(task.tool_policy),
            input: json_object(task.input_json.clone()),
            interests_prompt: task
                .input_json
                .get("interests_prompt")
                .and_then(Value::as_str)
                .and_then(|value| clean_string(Some(value))),
            vm_namespace,
            workspace_path,
            shared_workspace_path,
            created_at: task.created_at.and_utc(),
            source,
        },
    ))
}

/// Rechecks the source pipeline after an external object lookup found no usable source body.
pub async fn settle_learning_deck_source_missing(
    transaction: &mut Transaction<'_, Postgres>,
    snapshot: &LearningDeckTaskSnapshot,
    waiting_message: &str,
) -> Result<LearningDeckSourceSettlement, LearningDeckTaskRepositoryError> {
    let Some(task) = load_task_for_update(transaction, snapshot.id).await? else {
        return Err(LearningDeckTaskRepositoryError::TaskNotFound);
    };
    if task.user_id != snapshot.user_id || task.task_kind != "learning_deck" {
        return Err(LearningDeckTaskRepositoryError::OwnershipMismatch);
    }
    if is_terminal(&task.status) {
        return Ok(LearningDeckSourceSettlement::Terminal);
    }
    let Some(deck) = load_deck_for_update(transaction, snapshot.deck_id).await? else {
        fail_task(
            transaction,
            task.id,
            "deck_not_found",
            "Learning Deck not found",
            None,
            None,
            None,
        )
        .await?;
        return Ok(LearningDeckSourceSettlement::Failed {
            error_type: "deck_not_found".to_owned(),
            message: "Learning Deck not found".to_owned(),
        });
    };
    if deck.deleted_at.is_some() {
        cancel_task(transaction, task.id, "Learning Deck was deleted").await?;
        return Ok(LearningDeckSourceSettlement::Cancelled);
    }
    let Some(content_id) = deck.source_content_id else {
        return fail_source_settlement(
            transaction,
            task.id,
            "source_not_found",
            "Source content no longer exists",
        )
        .await;
    };
    let content = sqlx::query_as::<_, ContentRow>(CONTENT_ROW_SQL)
        .bind(content_id)
        .fetch_optional(&mut **transaction)
        .await?;
    let Some(content) = content else {
        return fail_source_settlement(
            transaction,
            task.id,
            "source_not_found",
            "Source content no longer exists",
        )
        .await;
    };
    if matches!(content.status.as_str(), "failed" | "skipped") {
        return fail_source_settlement(
            transaction,
            task.id,
            "source_processing_failed",
            "Source content processing failed",
        )
        .await;
    }

    let active = sqlx::query_scalar::<_, bool>(
        r#"
        SELECT EXISTS(
            SELECT 1
            FROM processing_tasks
            WHERE content_id::bigint = $1
              AND task_type = ANY($2)
              AND status = ANY($3)
        )
        "#,
    )
    .bind(content.id)
    .bind(SOURCE_PREPARATION_TASK_TYPES.as_slice())
    .bind(["pending", "processing"].as_slice())
    .fetch_one(&mut **transaction)
    .await?;
    if active {
        set_preparing(transaction, task.id, deck.id, waiting_message).await?;
        return Ok(LearningDeckSourceSettlement::Deferred {
            retry_delay_seconds: source_wait_delay_seconds(task.created_at.and_utc()),
            message: waiting_message.to_owned(),
        });
    }

    let latest = sqlx::query_as::<_, (String, Option<String>)>(
        r#"
        SELECT status, error_message
        FROM processing_tasks
        WHERE content_id::bigint = $1
          AND task_type = ANY($2)
        ORDER BY id DESC
        LIMIT 1
        "#,
    )
    .bind(content.id)
    .bind(SOURCE_PREPARATION_TASK_TYPES.as_slice())
    .fetch_optional(&mut **transaction)
    .await?;
    if let Some((status, error_message)) = latest
        && status == "failed"
    {
        return fail_source_settlement(
            transaction,
            task.id,
            "source_processing_failed",
            error_message
                .as_deref()
                .unwrap_or("Source content processing failed"),
        )
        .await;
    }
    if matches!(content.status.as_str(), "completed" | "awaiting_image") {
        return fail_source_settlement(
            transaction,
            task.id,
            "source_text_unavailable",
            "Source content completed without readable source text",
        )
        .await;
    }
    fail_source_settlement(
        transaction,
        task.id,
        "source_pipeline_stalled",
        "Source content is not ready and has no active preparation task",
    )
    .await
}

/// Persists the body-free source snapshot and marks the durable task running.
pub async fn mark_learning_deck_running(
    transaction: &mut Transaction<'_, Postgres>,
    snapshot: &LearningDeckTaskSnapshot,
    persistable_source: &Map<String, Value>,
) -> Result<MarkLearningDeckRunningOutcome, LearningDeckTaskRepositoryError> {
    let Some(task) = load_task_for_update(transaction, snapshot.id).await? else {
        return Err(LearningDeckTaskRepositoryError::TaskNotFound);
    };
    if task.user_id != snapshot.user_id
        || task.task_kind != "learning_deck"
        || task.subject_id != Some(snapshot.deck_id)
    {
        return Err(LearningDeckTaskRepositoryError::OwnershipMismatch);
    }
    if is_terminal(&task.status) {
        return Ok(MarkLearningDeckRunningOutcome::Terminal);
    }
    let Some(deck) = load_deck_for_update(transaction, snapshot.deck_id).await? else {
        fail_task(
            transaction,
            task.id,
            "deck_not_found",
            "Learning Deck not found",
            None,
            None,
            None,
        )
        .await?;
        return Ok(MarkLearningDeckRunningOutcome::Terminal);
    };
    if deck.deleted_at.is_some() {
        cancel_task(transaction, task.id, "Learning Deck was deleted").await?;
        return Ok(MarkLearningDeckRunningOutcome::Cancelled);
    }
    let mut input = json_object(task.input_json);
    input.insert(
        "source".to_owned(),
        Value::Object(persistable_source.clone()),
    );
    set_running(transaction, task.id, &input).await?;
    Ok(MarkLearningDeckRunningOutcome::Ready)
}

/// Marks a Learning Deck product task failed inside a fresh lease-fenced transaction.
#[allow(clippy::too_many_arguments)]
pub async fn fail_learning_deck_task(
    transaction: &mut Transaction<'_, Postgres>,
    task_id: i64,
    user_id: i64,
    error_type: &str,
    message: &str,
    sandbox_provider: Option<&str>,
    sandbox_id: Option<&str>,
    agent_log_object_key: Option<&str>,
) -> Result<(), LearningDeckTaskRepositoryError> {
    let Some(task) = load_task_for_update(transaction, task_id).await? else {
        return Err(LearningDeckTaskRepositoryError::TaskNotFound);
    };
    if task.user_id != user_id || task.task_kind != "learning_deck" {
        return Err(LearningDeckTaskRepositoryError::OwnershipMismatch);
    }
    if is_terminal(&task.status) {
        return Ok(());
    }
    if let Some(deck_id) = task.subject_id
        && let Some(deck) = load_deck_for_update(transaction, deck_id).await?
        && deck.deleted_at.is_some()
    {
        cancel_task(transaction, task.id, "Learning Deck was deleted").await?;
        return Ok(());
    }
    fail_task(
        transaction,
        task.id,
        error_type,
        message,
        sandbox_provider,
        sandbox_id,
        agent_log_object_key,
    )
    .await
}

/// Promotes an immutable Learning Deck bundle under the locked task/deck rows.
pub async fn publish_learning_deck(
    transaction: &mut Transaction<'_, Postgres>,
    publication: &PublishLearningDeck<'_>,
) -> Result<PublishLearningDeckOutcome, LearningDeckTaskRepositoryError> {
    let Some(task) = load_task_for_update(transaction, publication.task_id).await? else {
        return Err(LearningDeckTaskRepositoryError::TaskNotFound);
    };
    if task.user_id != publication.user_id
        || task.task_kind != "learning_deck"
        || task.subject_id != Some(publication.deck_id)
    {
        return Err(LearningDeckTaskRepositoryError::OwnershipMismatch);
    }
    if is_terminal(&task.status) {
        return Ok(PublishLearningDeckOutcome::Terminal);
    }
    let Some(deck) = load_deck_for_update(transaction, publication.deck_id).await? else {
        let message = "Learning Deck not found";
        fail_task(
            transaction,
            task.id,
            "deck_not_found",
            message,
            Some(publication.sandbox_provider),
            publication.sandbox_id,
            publication.agent_log_object_key,
        )
        .await?;
        return Ok(PublishLearningDeckOutcome::Failed {
            error_type: "deck_not_found".to_owned(),
            message: message.to_owned(),
        });
    };
    if deck.deleted_at.is_some() {
        cancel_task(transaction, task.id, "Learning Deck was deleted").await?;
        return Ok(PublishLearningDeckOutcome::Cancelled);
    }

    let output = artifact_json(publication);
    let history = status_entry("completed", "completed", "Learning Deck is ready");
    sqlx::query(
        r#"
        UPDATE llm_tasks
        SET status = 'completed',
            workflow_state = 'completed',
            output_json = $2,
            artifact_manifest = $2,
            usage_json = $3,
            model_provider = $4,
            model_name = $5,
            sandbox_provider = $6,
            sandbox_id = $7,
            agent_log_object_key = $8,
            error_type = NULL,
            error_message = NULL,
            completed_at = timezone('UTC', clock_timestamp()),
            updated_at = timezone('UTC', clock_timestamp()),
            status_history = COALESCE(status_history, '[]'::jsonb) || jsonb_build_array($9::jsonb)
        WHERE id::bigint = $1
        "#,
    )
    .bind(task.id)
    .bind(Value::Object(output))
    .bind(Value::Object(publication.usage_json.clone()))
    .bind(publication.model_provider)
    .bind(publication.model_name)
    .bind(publication.sandbox_provider)
    .bind(publication.sandbox_id)
    .bind(publication.agent_log_object_key)
    .bind(history)
    .execute(&mut **transaction)
    .await?;

    let old_keys = string_array(deck_artifact_keys(transaction, deck.id).await?);
    let mut merged_metadata = json_object(deck.source_metadata);
    merged_metadata.extend(publication.source_metadata_updates.clone());
    sqlx::query(
        r#"
        UPDATE learning_decks
        SET artifact_storage_prefix = $2,
            deck_object_key = $3,
            source_notes_object_key = $4,
            source_notes_html_object_key = $5,
            artifact_object_keys = $6,
            latest_task_id = $7::bigint::integer,
            latest_successful_task_id = $7::bigint::integer,
            source_metadata = $8,
            updated_at = timezone('UTC', clock_timestamp())
        WHERE id::bigint = $1
        "#,
    )
    .bind(deck.id)
    .bind(&publication.artifact.storage_prefix)
    .bind(&publication.artifact.deck_object_key)
    .bind(&publication.artifact.source_notes_object_key)
    .bind(&publication.artifact.source_notes_html_object_key)
    .bind(json!(publication.artifact.artifact_object_keys))
    .bind(task.id)
    .bind(Value::Object(merged_metadata))
    .execute(&mut **transaction)
    .await?;

    if let Some(usage) = publication.vendor_usage {
        record_model_usage(transaction, task.id, task.user_id, usage).await?;
    }

    let new_keys = publication
        .artifact
        .artifact_object_keys
        .iter()
        .cloned()
        .collect::<HashSet<_>>();
    Ok(PublishLearningDeckOutcome::Published {
        stale_object_keys: old_keys
            .into_iter()
            .filter(|key| !new_keys.contains(key))
            .collect(),
    })
}

async fn prepare_content_source(
    transaction: &mut Transaction<'_, Postgres>,
    deck: &DeckRow,
) -> Result<Option<LearningDeckSourceMaterial>, LearningDeckTaskRepositoryError> {
    let source =
        match prepare_learning_deck_generation_source(transaction, deck.user_id, deck.id).await {
            Ok(source) => source,
            Err(
                LearningDeckRepositoryError::ContentSourceMissing
                | LearningDeckRepositoryError::DeckContentSourceMissing,
            ) => return Ok(None),
            Err(error) => return Err(error.into()),
        };
    let Some(content_id) = source.source_content_id else {
        return Ok(None);
    };
    let Some(content) = sqlx::query_as::<_, ContentRow>(CONTENT_ROW_SQL)
        .bind(content_id)
        .fetch_optional(&mut **transaction)
        .await?
    else {
        return Ok(None);
    };
    let metadata = json_object(content.content_metadata.clone());
    let mut snapshot = Map::from_iter([
        ("source_kind".to_owned(), Value::from(source.source_kind)),
        (
            "source_identity".to_owned(),
            Value::from(source.source_identity),
        ),
        ("source_content_id".to_owned(), Value::from(content_id)),
        ("source_url".to_owned(), option_string(source.source_url)),
        ("source_title".to_owned(), Value::from(source.source_title)),
        (
            "content_type".to_owned(),
            Value::from(content.content_type.clone()),
        ),
        ("metadata".to_owned(), Value::Object(metadata.clone())),
    ]);
    snapshot.retain(|_, value| !value.is_null());

    let storage_key = sqlx::query_scalar::<_, String>(
        r#"
        SELECT storage_key
        FROM content_bodies
        WHERE content_id::bigint = $1
          AND variant = 'source'
        LIMIT 1
        "#,
    )
    .bind(content.id)
    .fetch_optional(&mut **transaction)
    .await?;
    Ok(Some(LearningDeckSourceMaterial::Content {
        snapshot,
        content_id,
        content_status: content.status,
        body: ContentBodyMaterial {
            storage_key,
            fallback_text: source_body_fallback(&content.content_type, &metadata),
        },
    }))
}

async fn load_task_for_update(
    transaction: &mut Transaction<'_, Postgres>,
    task_id: i64,
) -> Result<Option<TaskRow>, sqlx::Error> {
    sqlx::query_as::<_, TaskRow>(
        r#"
        SELECT
            id::bigint AS id,
            user_id::bigint AS user_id,
            task_kind,
            mode,
            workflow_key,
            status,
            approval_policy,
            allowed_actions,
            tool_policy,
            input_json,
            vm_namespace,
            workspace_path,
            shared_workspace_path,
            subject_id::bigint AS subject_id,
            created_at
        FROM llm_tasks
        WHERE id::bigint = $1
        FOR UPDATE
        "#,
    )
    .bind(task_id)
    .fetch_optional(&mut **transaction)
    .await
}

async fn load_deck_for_update(
    transaction: &mut Transaction<'_, Postgres>,
    deck_id: i64,
) -> Result<Option<DeckRow>, sqlx::Error> {
    sqlx::query_as::<_, DeckRow>(
        r#"
        SELECT
            id::bigint AS id,
            user_id::bigint AS user_id,
            source_kind,
            source_identity,
            source_url,
            source_content_id::bigint AS source_content_id,
            source_title,
            source_metadata,
            title,
            deleted_at
        FROM learning_decks
        WHERE id::bigint = $1
        FOR UPDATE
        "#,
    )
    .bind(deck_id)
    .fetch_optional(&mut **transaction)
    .await
}

async fn set_preparing(
    transaction: &mut Transaction<'_, Postgres>,
    task_id: i64,
    deck_id: i64,
    note: &str,
) -> Result<(), sqlx::Error> {
    sqlx::query(
        r#"
        UPDATE llm_tasks
        SET status = 'preparing',
            workflow_state = 'preparing',
            started_at = COALESCE(started_at, timezone('UTC', clock_timestamp())),
            updated_at = timezone('UTC', clock_timestamp()),
            status_history = COALESCE(status_history, '[]'::jsonb) || jsonb_build_array($2::jsonb)
        WHERE id::bigint = $1
        "#,
    )
    .bind(task_id)
    .bind(status_entry("preparing", "preparing", note))
    .execute(&mut **transaction)
    .await?;
    sqlx::query(
        "UPDATE learning_decks SET latest_task_id = $2::bigint::integer, updated_at = timezone('UTC', clock_timestamp()) WHERE id::bigint = $1",
    )
    .bind(deck_id)
    .bind(task_id)
    .execute(&mut **transaction)
    .await?;
    Ok(())
}

async fn set_running(
    transaction: &mut Transaction<'_, Postgres>,
    task_id: i64,
    input: &Map<String, Value>,
) -> Result<(), sqlx::Error> {
    sqlx::query(
        r#"
        UPDATE llm_tasks
        SET status = 'running',
            workflow_state = 'running',
            input_json = $2,
            started_at = COALESCE(started_at, timezone('UTC', clock_timestamp())),
            updated_at = timezone('UTC', clock_timestamp()),
            status_history = COALESCE(status_history, '[]'::jsonb) || jsonb_build_array($3::jsonb)
        WHERE id::bigint = $1
        "#,
    )
    .bind(task_id)
    .bind(Value::Object(input.clone()))
    .bind(status_entry(
        "running",
        "running",
        "Running Learning Deck agent",
    ))
    .execute(&mut **transaction)
    .await?;
    Ok(())
}

#[allow(clippy::too_many_arguments)]
async fn fail_task(
    transaction: &mut Transaction<'_, Postgres>,
    task_id: i64,
    error_type: &str,
    message: &str,
    sandbox_provider: Option<&str>,
    sandbox_id: Option<&str>,
    agent_log_object_key: Option<&str>,
) -> Result<(), LearningDeckTaskRepositoryError> {
    sqlx::query(
        r#"
        UPDATE llm_tasks
        SET status = 'failed',
            workflow_state = 'failed',
            error_type = $2,
            error_message = left($3, 4000),
            sandbox_provider = COALESCE($4, sandbox_provider),
            sandbox_id = COALESCE($5, sandbox_id),
            agent_log_object_key = COALESCE($6, agent_log_object_key),
            completed_at = timezone('UTC', clock_timestamp()),
            updated_at = timezone('UTC', clock_timestamp()),
            status_history = COALESCE(status_history, '[]'::jsonb) || jsonb_build_array($7::jsonb)
        WHERE id::bigint = $1
          AND status <> ALL($8)
        "#,
    )
    .bind(task_id)
    .bind(error_type)
    .bind(message)
    .bind(sandbox_provider)
    .bind(sandbox_id)
    .bind(agent_log_object_key)
    .bind(status_entry(
        "failed",
        "failed",
        "Learning Deck generation failed",
    ))
    .bind(TERMINAL_TASK_STATUSES.as_slice())
    .execute(&mut **transaction)
    .await?;
    Ok(())
}

async fn cancel_task(
    transaction: &mut Transaction<'_, Postgres>,
    task_id: i64,
    message: &str,
) -> Result<(), sqlx::Error> {
    sqlx::query(
        r#"
        UPDATE llm_tasks
        SET status = 'cancelled',
            workflow_state = 'cancelled',
            error_type = 'deck_deleted',
            error_message = $2,
            completed_at = timezone('UTC', clock_timestamp()),
            updated_at = timezone('UTC', clock_timestamp()),
            status_history = COALESCE(status_history, '[]'::jsonb) || jsonb_build_array($3::jsonb)
        WHERE id::bigint = $1
          AND status <> ALL($4)
        "#,
    )
    .bind(task_id)
    .bind(message)
    .bind(status_entry("cancelled", "cancelled", message))
    .bind(TERMINAL_TASK_STATUSES.as_slice())
    .execute(&mut **transaction)
    .await?;
    Ok(())
}

async fn fail_invalid_workspace(
    transaction: &mut Transaction<'_, Postgres>,
    task_id: i64,
) -> Result<LearningDeckPreparationOutcome, LearningDeckTaskRepositoryError> {
    let message = "Learning Deck LLM task workspace is required";
    fail_task(
        transaction,
        task_id,
        "invalid_workspace",
        message,
        None,
        None,
        None,
    )
    .await?;
    Ok(LearningDeckPreparationOutcome::Failed {
        error_type: "invalid_workspace".to_owned(),
        message: message.to_owned(),
    })
}

async fn fail_source_settlement(
    transaction: &mut Transaction<'_, Postgres>,
    task_id: i64,
    error_type: &str,
    message: &str,
) -> Result<LearningDeckSourceSettlement, LearningDeckTaskRepositoryError> {
    fail_task(transaction, task_id, error_type, message, None, None, None).await?;
    Ok(LearningDeckSourceSettlement::Failed {
        error_type: error_type.to_owned(),
        message: message.to_owned(),
    })
}

async fn deck_artifact_keys(
    transaction: &mut Transaction<'_, Postgres>,
    deck_id: i64,
) -> Result<Value, sqlx::Error> {
    sqlx::query_scalar("SELECT artifact_object_keys FROM learning_decks WHERE id::bigint = $1")
        .bind(deck_id)
        .fetch_one(&mut **transaction)
        .await
}

async fn record_model_usage(
    transaction: &mut Transaction<'_, Postgres>,
    task_id: i64,
    user_id: i64,
    usage: &LearningDeckModelUsage,
) -> Result<(), sqlx::Error> {
    let total = usage.input_tokens.saturating_add(usage.output_tokens);
    sqlx::query(
        r#"
        INSERT INTO vendor_usage_records (
            provider, model, feature, operation, source, request_id, task_id, user_id,
            input_tokens, output_tokens, total_tokens, request_count,
            cache_read_tokens, cache_write_tokens, currency, metadata, created_at
        )
        SELECT
            $1, $2, 'learning_deck_generation', 'learning_deck.generate', 'queue', $3,
            $4::bigint::integer, users.id,
            $5::bigint::integer, $6::bigint::integer, $7::bigint::integer,
            $8::bigint::integer, $9::bigint::integer, $10::bigint::integer,
            'USD', $11::jsonb,
            timezone('UTC', clock_timestamp())
        FROM users
        WHERE users.id::bigint = $12 AND users.is_active IS TRUE
        "#,
    )
    .bind(&usage.provider)
    .bind(&usage.model)
    .bind(&usage.provider_response_id)
    .bind(task_id)
    .bind(i64_bound(usage.input_tokens))
    .bind(i64_bound(usage.output_tokens))
    .bind(i64_bound(total))
    .bind(i64_bound(usage.request_count))
    .bind(i64_bound(usage.cache_read_tokens))
    .bind(i64_bound(usage.cache_write_tokens))
    .bind(Value::Object(usage.metadata.clone()))
    .bind(user_id)
    .execute(&mut **transaction)
    .await?;
    Ok(())
}

fn artifact_json(publication: &PublishLearningDeck<'_>) -> Map<String, Value> {
    Map::from_iter([
        ("deck_id".to_owned(), Value::from(publication.deck_id)),
        (
            "deck_object_key".to_owned(),
            Value::from(publication.artifact.deck_object_key.clone()),
        ),
        (
            "source_notes_object_key".to_owned(),
            Value::from(publication.artifact.source_notes_object_key.clone()),
        ),
        (
            "source_notes_html_object_key".to_owned(),
            Value::from(publication.artifact.source_notes_html_object_key.clone()),
        ),
        (
            "thumbnail_object_key".to_owned(),
            publication
                .artifact
                .thumbnail_object_key
                .clone()
                .map_or(Value::Null, Value::from),
        ),
        (
            "artifact_storage_prefix".to_owned(),
            Value::from(publication.artifact.storage_prefix.clone()),
        ),
        (
            "artifact_object_keys".to_owned(),
            json!(publication.artifact.artifact_object_keys),
        ),
        (
            "browser_validation".to_owned(),
            Value::Object(publication.browser_validation.clone()),
        ),
    ])
}

fn github_snapshot(deck: &DeckRow) -> Map<String, Value> {
    let mut snapshot = Map::from_iter([
        ("source_kind".to_owned(), Value::from("github_repo")),
        (
            "source_identity".to_owned(),
            Value::from(deck.source_identity.clone()),
        ),
        (
            "source_url".to_owned(),
            option_string(deck.source_url.clone()),
        ),
        (
            "source_title".to_owned(),
            option_string(deck.source_title.clone()),
        ),
        (
            "source_metadata".to_owned(),
            Value::Object(json_object(deck.source_metadata.clone())),
        ),
    ]);
    snapshot.retain(|_, value| !value.is_null());
    snapshot
}

fn source_body_fallback(content_type: &str, metadata: &Map<String, Value>) -> Option<String> {
    let keys: &[&str] = match content_type {
        "podcast" => &["transcript", "content_to_summarize"],
        "article" | "news" => &["content_to_summarize", "content"],
        _ => &[],
    };
    keys.iter().find_map(|key| {
        metadata
            .get(*key)
            .and_then(Value::as_str)
            .and_then(|value| clean_string(Some(value)))
    })
}

fn source_wait_delay_seconds(created_at: DateTime<Utc>) -> i64 {
    let age_seconds = Utc::now()
        .signed_duration_since(created_at)
        .num_seconds()
        .max(0);
    let step = u32::try_from((age_seconds / 300).min(4)).unwrap_or(4);
    (30_i64.saturating_mul(2_i64.saturating_pow(step))).clamp(30, 300)
}

fn status_entry(status: &str, workflow_state: &str, note: &str) -> Value {
    json!({
        "status": status,
        "workflow_state": workflow_state,
        "note": note,
        "created_at": Utc::now().naive_utc().format("%Y-%m-%dT%H:%M:%S%.6f").to_string(),
    })
}

fn json_object(value: Value) -> Map<String, Value> {
    match value {
        Value::Object(object) => object,
        _ => Map::new(),
    }
}

fn string_array(value: Value) -> Vec<String> {
    match value {
        Value::Array(values) => values
            .into_iter()
            .filter_map(|value| value.as_str().map(str::to_owned))
            .collect(),
        _ => Vec::new(),
    }
}

fn option_string(value: Option<String>) -> Value {
    value.map_or(Value::Null, Value::from)
}

fn clean_string(value: Option<&str>) -> Option<String> {
    value.and_then(|value| {
        let value = value.trim();
        (!value.is_empty()).then(|| value.to_owned())
    })
}

fn is_terminal(status: &str) -> bool {
    TERMINAL_TASK_STATUSES.contains(&status)
}

fn i64_bound(value: u64) -> i64 {
    i64::try_from(value).unwrap_or(i64::MAX)
}

const CONTENT_ROW_SQL: &str = r#"
    SELECT
        id::bigint AS id,
        content_type,
        status,
        content_metadata::jsonb AS content_metadata
    FROM contents
    WHERE id::bigint = $1
"#;

#[derive(Debug, Error)]
pub enum LearningDeckTaskRepositoryError {
    #[error("Learning Deck LLM task not found")]
    TaskNotFound,
    #[error("Learning Deck LLM task ownership mismatch")]
    OwnershipMismatch,
    #[error(transparent)]
    LearningDeck(#[from] LearningDeckRepositoryError),
    #[error("PostgreSQL Learning Deck task operation failed")]
    Sqlx(#[from] sqlx::Error),
}
