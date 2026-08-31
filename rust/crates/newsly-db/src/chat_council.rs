//! Durable council-chat staging and aggregate publication.
//!
//! HTTP requests only create immutable hidden branch turns and enqueue them in the caller's
//! transaction. Provider work remains owned by the chat worker. Each exact lease-fenced branch
//! finalizer then advances the parent candidate metadata in the same transaction as the branch
//! terminal state.

use std::collections::HashSet;

use chrono::{NaiveDateTime, Utc};
use newsly_agent_runtime::{
    AssistantPart, MessagePart, MessageRole, NewslyMessage, NewslyTranscript, ProviderUsage,
    RequestPart,
};
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value, json};
use sqlx::{FromRow, Postgres, Transaction};
use thiserror::Error;

use crate::chat::StagedChatTurn;
use crate::chat_tasks::{ChatTurnKind, ChatTurnProcessingContext, ChatTurnSessionSnapshot};
use crate::chat_transcripts::{ChatTranscriptError, decode_transcript, processing_transcript};

const MIN_COUNCIL_PERSONAS: usize = 2;
const MAX_COUNCIL_PERSONAS: usize = 3;
const MAX_USER_PROMPT_CHARS: usize = 10_000;
const COUNCIL_PREPARING_TEXT: &str = "The council is preparing its perspectives.";

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum CouncilRunKind {
    Start,
    Retry,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CouncilRunContext {
    pub parent_message_id: i64,
    pub candidate_order: i32,
    pub run_kind: CouncilRunKind,
}

impl CouncilRunContext {
    pub(crate) fn validate(&self) -> Result<(), String> {
        if self.parent_message_id <= 0 {
            return Err("council_run.parent_message_id must be positive".to_owned());
        }
        if !(0..i32::try_from(MAX_COUNCIL_PERSONAS).expect("small constant"))
            .contains(&self.candidate_order)
        {
            return Err("council_run.candidate_order is out of range".to_owned());
        }
        Ok(())
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CouncilPersonaSeed {
    pub id: String,
    pub display_name: String,
    pub context_suffix: String,
    pub impersonation_prompt: String,
    pub sort_order: i32,
}

#[derive(Debug, Clone)]
pub struct StageCouncilStartInput<'a> {
    pub user_id: i64,
    pub session_id: i64,
    pub user_prompt: &'a str,
    pub personas: &'a [CouncilPersonaSeed],
}

#[derive(Debug, Clone, Copy)]
pub struct StageCouncilRetryInput {
    pub user_id: i64,
    pub session_id: i64,
    pub child_session_id: i64,
}

#[derive(Debug, Clone, Copy)]
pub struct SelectCouncilBranchInput {
    pub user_id: i64,
    pub session_id: i64,
    pub child_session_id: i64,
}

#[derive(Debug, Clone, PartialEq)]
pub struct StagedCouncilWork {
    pub parent_session_id: i64,
    pub parent_message_id: i64,
    pub turns: Vec<StagedChatTurn>,
}

#[derive(Debug, Clone, PartialEq)]
pub enum CouncilStageOutcome {
    Staged(StagedCouncilWork),
    NotFound,
    Forbidden,
    Archived,
    Hidden,
    AlreadyActive,
    CouncilInactive,
    UnsupportedSessionType,
    InvalidPersonas,
    BranchNotFound,
    CandidateNotFound,
    CouncilMessageNotFound,
    AlreadyProcessing,
    UserInactive,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CouncilSelectOutcome {
    Applied,
    Unchanged,
    NotFound,
    Forbidden,
    Archived,
    CouncilInactive,
    BranchNotFound,
    CandidateNotFound,
    CouncilMessageNotFound,
}

#[derive(Debug, Clone, Copy)]
pub enum CouncilCandidateCompletion<'a> {
    Completed(&'a str),
    Failed(&'a str),
}

#[derive(Debug, Clone, FromRow)]
struct CouncilSessionRow {
    id: i64,
    user_id: i64,
    content_id: Option<i64>,
    news_item_id: Option<i64>,
    parent_session_id: Option<i64>,
    title: Option<String>,
    session_type: Option<String>,
    topic: Option<String>,
    context_snapshot: Option<String>,
    council_persona_id: Option<String>,
    council_persona_name: Option<String>,
    council_persona_prompt: Option<String>,
    council_mode: bool,
    active_child_session_id: Option<i64>,
    council_message_id: Option<i64>,
    is_hidden_from_history: bool,
    llm_model: String,
    llm_provider: String,
    last_message_at: Option<NaiveDateTime>,
    is_archived: bool,
}

#[derive(Debug, Clone, FromRow)]
struct CouncilMessageRow {
    id: i64,
    session_id: i64,
    message_list: String,
    render_metadata: Option<Value>,
}

#[derive(Debug, Clone, FromRow)]
struct InsertedMessageRow {
    id: i64,
    created_at: NaiveDateTime,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
struct StoredCouncilCandidate {
    persona_id: String,
    persona_name: String,
    child_session_id: i64,
    content: String,
    status: String,
    order: i32,
}

/// Fork an owned visible session into immutable hidden persona sessions and stage every first
/// branch turn. The caller enqueues all returned turns before committing this transaction.
pub async fn stage_council_start(
    transaction: &mut Transaction<'_, Postgres>,
    input: &StageCouncilStartInput<'_>,
) -> Result<CouncilStageOutcome, CouncilRepositoryError> {
    if !valid_user_prompt(input.user_prompt) || !valid_personas(input.personas) {
        return Ok(CouncilStageOutcome::InvalidPersonas);
    }
    let Some(parent) = load_session_for_update(transaction, input.session_id).await? else {
        return Ok(CouncilStageOutcome::NotFound);
    };
    if parent.user_id != input.user_id {
        return Ok(CouncilStageOutcome::Forbidden);
    }
    if parent.is_archived {
        return Ok(CouncilStageOutcome::Archived);
    }
    if parent.is_hidden_from_history {
        return Ok(CouncilStageOutcome::Hidden);
    }
    if parent.council_mode {
        return Ok(CouncilStageOutcome::AlreadyActive);
    }
    if parent.session_type.as_deref() == Some("deep_research") {
        return Ok(CouncilStageOutcome::UnsupportedSessionType);
    }
    if !lock_active_user(transaction, input.user_id).await? {
        return Ok(CouncilStageOutcome::UserInactive);
    }

    let now = Utc::now().naive_utc();
    let mut children = Vec::with_capacity(input.personas.len());
    let mut candidates = Vec::with_capacity(input.personas.len());
    for persona in input.personas {
        let context_snapshot = join_context(
            parent.context_snapshot.as_deref(),
            Some(persona.context_suffix.as_str()),
        );
        let child_id = insert_child_session(
            transaction,
            &parent,
            persona,
            context_snapshot.as_deref(),
            now,
        )
        .await?;
        clone_terminal_history(transaction, parent.id, child_id).await?;
        let child = CouncilSessionRow {
            id: child_id,
            user_id: parent.user_id,
            content_id: parent.content_id,
            news_item_id: parent.news_item_id,
            parent_session_id: Some(parent.id),
            title: parent.title.clone(),
            session_type: parent.session_type.clone(),
            topic: parent.topic.clone(),
            context_snapshot,
            council_persona_id: Some(persona.id.clone()),
            council_persona_name: Some(persona.display_name.clone()),
            council_persona_prompt: Some(persona.impersonation_prompt.clone()),
            council_mode: false,
            active_child_session_id: None,
            council_message_id: None,
            is_hidden_from_history: true,
            llm_model: parent.llm_model.clone(),
            llm_provider: parent.llm_provider.clone(),
            last_message_at: parent.last_message_at,
            is_archived: false,
        };
        candidates.push(StoredCouncilCandidate {
            persona_id: persona.id.clone(),
            persona_name: persona.display_name.clone(),
            child_session_id: child_id,
            content: preparing_candidate_text(&persona.display_name),
            status: "processing".to_owned(),
            order: persona.sort_order,
        });
        children.push(child);
    }

    let active_child_session_id = children.first().map(|child| child.id);
    let parent_message = insert_parent_council_message(
        transaction,
        parent.id,
        input.user_prompt,
        &candidates,
        active_child_session_id,
        now,
    )
    .await?;
    sqlx::query(
        r#"
        UPDATE chat_sessions
        SET council_mode = TRUE,
            active_child_session_id = $2::bigint::integer,
            council_message_id = $3::bigint::integer,
            updated_at = $4,
            last_message_at = $4
        WHERE id::bigint = $1
        "#,
    )
    .bind(parent.id)
    .bind(active_child_session_id)
    .bind(parent_message.id)
    .bind(parent_message.created_at)
    .execute(&mut **transaction)
    .await?;

    let mut turns = Vec::with_capacity(children.len());
    for (child, candidate) in children.iter().zip(&candidates) {
        turns.push(
            stage_branch_turn(
                transaction,
                &parent,
                child,
                input.user_prompt,
                CouncilRunContext {
                    parent_message_id: parent_message.id,
                    candidate_order: candidate.order,
                    run_kind: CouncilRunKind::Start,
                },
            )
            .await?,
        );
    }
    Ok(CouncilStageOutcome::Staged(StagedCouncilWork {
        parent_session_id: parent.id,
        parent_message_id: parent_message.id,
        turns,
    }))
}

/// Mark one stored candidate as processing and stage a replacement hidden-branch turn.
pub async fn stage_council_retry(
    transaction: &mut Transaction<'_, Postgres>,
    input: StageCouncilRetryInput,
) -> Result<CouncilStageOutcome, CouncilRepositoryError> {
    let Some(parent) = load_session_for_update(transaction, input.session_id).await? else {
        return Ok(CouncilStageOutcome::NotFound);
    };
    if parent.user_id != input.user_id {
        return Ok(CouncilStageOutcome::Forbidden);
    }
    if parent.is_archived {
        return Ok(CouncilStageOutcome::Archived);
    }
    if !parent.council_mode {
        return Ok(CouncilStageOutcome::CouncilInactive);
    }
    if !lock_active_user(transaction, input.user_id).await? {
        return Ok(CouncilStageOutcome::UserInactive);
    }
    let Some(child) = load_session_for_update(transaction, input.child_session_id).await? else {
        return Ok(CouncilStageOutcome::BranchNotFound);
    };
    if child.user_id != input.user_id
        || child.parent_session_id != Some(parent.id)
        || !child.is_hidden_from_history
        || child.is_archived
    {
        return Ok(CouncilStageOutcome::BranchNotFound);
    }
    let Some(parent_message_id) = parent.council_message_id else {
        return Ok(CouncilStageOutcome::CouncilMessageNotFound);
    };
    let Some(message) = load_council_message_for_update(transaction, parent_message_id).await?
    else {
        return Ok(CouncilStageOutcome::CouncilMessageNotFound);
    };
    if message.session_id != parent.id {
        return Err(CouncilRepositoryError::InvalidStoredState(
            "council message does not belong to its parent session".to_owned(),
        ));
    }
    let user_prompt = transcript_user_prompt(&message.message_list)?;
    let mut candidates = council_candidates(message.render_metadata.as_ref())?;
    let Some(candidate) = candidates
        .iter_mut()
        .find(|candidate| candidate.child_session_id == child.id)
    else {
        return Ok(CouncilStageOutcome::CandidateNotFound);
    };
    if candidate.status == "processing" {
        return Ok(CouncilStageOutcome::AlreadyProcessing);
    }
    "processing".clone_into(&mut candidate.status);
    candidate.content = preparing_candidate_text(&candidate.persona_name);
    let candidate_order = candidate.order;
    let metadata = council_metadata(
        message.render_metadata.as_ref(),
        &candidates,
        parent.active_child_session_id,
    )?;
    sqlx::query(
        r#"
        UPDATE chat_messages
        SET render_metadata = $2,
            status = 'processing',
            error = NULL,
            partial_text = NULL,
            tool_progress = NULL
        WHERE id::bigint = $1
        "#,
    )
    .bind(message.id)
    .bind(metadata)
    .execute(&mut **transaction)
    .await?;

    let turn = stage_branch_turn(
        transaction,
        &parent,
        &child,
        &user_prompt,
        CouncilRunContext {
            parent_message_id: message.id,
            candidate_order,
            run_kind: CouncilRunKind::Retry,
        },
    )
    .await?;
    sqlx::query(
        r#"
        UPDATE chat_sessions
        SET updated_at = $2, last_message_at = $2
        WHERE id::bigint = $1
        "#,
    )
    .bind(parent.id)
    .bind(turn.created_at.naive_utc())
    .execute(&mut **transaction)
    .await?;
    Ok(CouncilStageOutcome::Staged(StagedCouncilWork {
        parent_session_id: parent.id,
        parent_message_id: message.id,
        turns: vec![turn],
    }))
}

/// Switch the visible branch without invoking a provider.
pub async fn select_council_branch(
    transaction: &mut Transaction<'_, Postgres>,
    input: SelectCouncilBranchInput,
) -> Result<CouncilSelectOutcome, CouncilRepositoryError> {
    let Some(parent) = load_session_for_update(transaction, input.session_id).await? else {
        return Ok(CouncilSelectOutcome::NotFound);
    };
    if parent.user_id != input.user_id {
        return Ok(CouncilSelectOutcome::Forbidden);
    }
    if parent.is_archived {
        return Ok(CouncilSelectOutcome::Archived);
    }
    if !parent.council_mode {
        return Ok(CouncilSelectOutcome::CouncilInactive);
    }
    let Some(child) = load_session_for_update(transaction, input.child_session_id).await? else {
        return Ok(CouncilSelectOutcome::BranchNotFound);
    };
    if child.user_id != input.user_id
        || child.parent_session_id != Some(parent.id)
        || !child.is_hidden_from_history
    {
        return Ok(CouncilSelectOutcome::BranchNotFound);
    }
    if parent.active_child_session_id == Some(child.id) {
        return Ok(CouncilSelectOutcome::Unchanged);
    }
    let Some(parent_message_id) = parent.council_message_id else {
        return Ok(CouncilSelectOutcome::CouncilMessageNotFound);
    };
    let Some(message) = load_council_message_for_update(transaction, parent_message_id).await?
    else {
        return Ok(CouncilSelectOutcome::CouncilMessageNotFound);
    };
    let candidates = council_candidates(message.render_metadata.as_ref())?;
    let Some(candidate) = candidates
        .iter()
        .find(|candidate| candidate.child_session_id == child.id)
    else {
        return Ok(CouncilSelectOutcome::CandidateNotFound);
    };
    let user_prompt = transcript_user_prompt(&message.message_list)?;
    let transcript = council_transcript(&user_prompt, &candidate.content, Utc::now());
    let message_list = serde_json::to_string(&transcript)?;
    let metadata = council_metadata(
        message.render_metadata.as_ref(),
        &candidates,
        Some(child.id),
    )?;
    sqlx::query(
        r#"
        UPDATE chat_messages
        SET message_list = $2, render_metadata = $3
        WHERE id::bigint = $1
        "#,
    )
    .bind(message.id)
    .bind(message_list)
    .bind(metadata)
    .execute(&mut **transaction)
    .await?;
    sqlx::query(
        r#"
        UPDATE chat_sessions
        SET active_child_session_id = $2::bigint::integer,
            updated_at = timezone('UTC', clock_timestamp()),
            last_message_at = COALESCE($3, last_message_at)
        WHERE id::bigint = $1
        "#,
    )
    .bind(parent.id)
    .bind(child.id)
    .bind(child.last_message_at)
    .execute(&mut **transaction)
    .await?;
    Ok(CouncilSelectOutcome::Applied)
}

/// Advance one parent candidate after its branch transcript was published successfully.
pub async fn finalize_council_candidate(
    transaction: &mut Transaction<'_, Postgres>,
    run: &CouncilRunContext,
    user_id: i64,
    child_session_id: i64,
    child_message_id: i64,
    completion: CouncilCandidateCompletion<'_>,
) -> Result<(), CouncilRepositoryError> {
    run.validate()
        .map_err(CouncilRepositoryError::InvalidStoredState)?;
    let Some(child) = load_session_for_update(transaction, child_session_id).await? else {
        return Err(CouncilRepositoryError::InvalidStoredState(
            "council child session is missing during finalization".to_owned(),
        ));
    };
    let Some(parent_id) = child.parent_session_id else {
        return Err(CouncilRepositoryError::InvalidStoredState(
            "council child has no parent session".to_owned(),
        ));
    };
    let Some(parent) = load_session_for_update(transaction, parent_id).await? else {
        return Err(CouncilRepositoryError::InvalidStoredState(
            "council parent session is missing during finalization".to_owned(),
        ));
    };
    if child.user_id != user_id
        || parent.user_id != user_id
        || !child.is_hidden_from_history
        || !parent.council_mode
        || parent.council_message_id != Some(run.parent_message_id)
    {
        return Err(CouncilRepositoryError::InvalidStoredState(
            "council finalization ownership is invalid".to_owned(),
        ));
    }
    let Some(message) = load_council_message_for_update(transaction, run.parent_message_id).await?
    else {
        return Err(CouncilRepositoryError::InvalidStoredState(
            "council parent message is missing during finalization".to_owned(),
        ));
    };
    if message.session_id != parent.id {
        return Err(CouncilRepositoryError::InvalidStoredState(
            "council parent message ownership is invalid".to_owned(),
        ));
    }
    let mut candidates = council_candidates(message.render_metadata.as_ref())?;
    let Some(candidate_index) = candidates.iter().position(|candidate| {
        candidate.child_session_id == child.id && candidate.order == run.candidate_order
    }) else {
        return Err(CouncilRepositoryError::InvalidStoredState(
            "council finalization candidate is missing".to_owned(),
        ));
    };
    if candidates[candidate_index].status != "processing" {
        return Ok(());
    }

    let successful = matches!(completion, CouncilCandidateCompletion::Completed(_));
    candidates[candidate_index].status = if successful {
        "completed".to_owned()
    } else {
        "failed".to_owned()
    };
    candidates[candidate_index].content = match completion {
        CouncilCandidateCompletion::Completed(output) => output.trim().to_owned(),
        CouncilCandidateCompletion::Failed(message) => format!(
            "{} could not respond. {}",
            candidates[candidate_index].persona_name,
            message.trim()
        ),
    };
    if candidates[candidate_index].content.is_empty() {
        return Err(CouncilRepositoryError::InvalidStoredState(
            "council candidate finalization produced empty content".to_owned(),
        ));
    }
    if successful {
        sqlx::query(
            r#"
            UPDATE chat_sessions
            SET branch_start_message_id = $2::bigint::integer,
                updated_at = timezone('UTC', clock_timestamp()),
                last_message_at = timezone('UTC', clock_timestamp())
            WHERE id::bigint = $1
            "#,
        )
        .bind(child.id)
        .bind(child_message_id)
        .execute(&mut **transaction)
        .await?;
    }

    let settled = candidates
        .iter()
        .all(|candidate| candidate.status != "processing");
    let active_child_session_id = resolve_active_candidate(
        &candidates,
        parent.active_child_session_id,
        run.run_kind,
        child.id,
        successful,
        settled,
    );
    let message_list = if settled {
        let user_prompt = transcript_user_prompt(&message.message_list)?;
        let assistant_text = candidates
            .iter()
            .find(|candidate| Some(candidate.child_session_id) == active_child_session_id)
            .map_or(COUNCIL_PREPARING_TEXT, |candidate| {
                candidate.content.as_str()
            });
        serde_json::to_string(&council_transcript(
            &user_prompt,
            assistant_text,
            Utc::now(),
        ))?
    } else {
        message.message_list
    };
    let metadata = council_metadata(
        message.render_metadata.as_ref(),
        &candidates,
        active_child_session_id,
    )?;
    sqlx::query(
        r#"
        UPDATE chat_messages
        SET message_list = $2,
            render_metadata = $3,
            status = $4,
            error = NULL,
            partial_text = NULL,
            tool_progress = NULL
        WHERE id::bigint = $1
        "#,
    )
    .bind(message.id)
    .bind(message_list)
    .bind(metadata)
    .bind(if settled { "completed" } else { "processing" })
    .execute(&mut **transaction)
    .await?;
    sqlx::query(
        r#"
        UPDATE chat_sessions
        SET active_child_session_id = $2::bigint::integer,
            updated_at = timezone('UTC', clock_timestamp()),
            last_message_at = timezone('UTC', clock_timestamp())
        WHERE id::bigint = $1
        "#,
    )
    .bind(parent.id)
    .bind(active_child_session_id)
    .execute(&mut **transaction)
    .await?;
    Ok(())
}

/// Recover the durable council linkage from a rejected branch row and advance its parent.
pub async fn finalize_failed_council_candidate(
    transaction: &mut Transaction<'_, Postgres>,
    child_message_id: i64,
    user_id: i64,
    public_message: &str,
) -> Result<(), CouncilRepositoryError> {
    let row = sqlx::query_as::<_, (i64, Option<Value>)>(
        r#"
        SELECT session_id::bigint, processing_context
        FROM chat_messages
        WHERE id::bigint = $1
        "#,
    )
    .bind(child_message_id)
    .fetch_optional(&mut **transaction)
    .await?;
    let Some((child_session_id, Some(context))) = row else {
        return Ok(());
    };
    let Some(run) = context.get("council_run").filter(|value| !value.is_null()) else {
        return Ok(());
    };
    let run = serde_json::from_value::<CouncilRunContext>(run.clone())?;
    finalize_council_candidate(
        transaction,
        &run,
        user_id,
        child_session_id,
        child_message_id,
        CouncilCandidateCompletion::Failed(public_message),
    )
    .await
}

/// Resolve the visible parent for agent-data projection after a hidden-branch terminal write.
pub async fn visible_council_session_id(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
    session_id: i64,
) -> Result<Option<i64>, CouncilRepositoryError> {
    let row = sqlx::query_as::<_, (i64, Option<i64>)>(
        r#"
        SELECT id::bigint, parent_session_id::bigint
        FROM chat_sessions
        WHERE id::bigint = $1 AND user_id::bigint = $2
        "#,
    )
    .bind(session_id)
    .bind(user_id)
    .fetch_optional(&mut **transaction)
    .await?;
    Ok(row.map(|(id, parent)| parent.unwrap_or(id)))
}

async fn stage_branch_turn(
    transaction: &mut Transaction<'_, Postgres>,
    parent: &CouncilSessionRow,
    child: &CouncilSessionRow,
    user_prompt: &str,
    council_run: CouncilRunContext,
) -> Result<StagedChatTurn, CouncilRepositoryError> {
    let transcript = processing_transcript(user_prompt, Utc::now());
    let message_list = serde_json::to_string(&transcript)?;
    let context = ChatTurnProcessingContext {
        version: 1,
        kind: ChatTurnKind::Council,
        user_prompt: user_prompt.to_owned(),
        source: "council".to_owned(),
        session: ChatTurnSessionSnapshot {
            user_id: child.user_id,
            effective_session_id: child.id,
            visible_session_id: parent.id,
            model: child.llm_model.clone(),
            provider: child.llm_provider.clone(),
            title: child.title.clone(),
            session_type: child.session_type.clone(),
            content_id: child.content_id,
            news_item_id: child.news_item_id,
            parent_session_id: child.parent_session_id,
            topic: child.topic.clone(),
            context_snapshot: child.context_snapshot.clone(),
            is_hidden_from_history: true,
            council_persona_id: child.council_persona_id.clone(),
            council_persona_name: child.council_persona_name.clone(),
            council_persona_prompt: child.council_persona_prompt.clone(),
        },
        screen_context: None,
        council_run: Some(council_run),
    };
    let processing_context = serde_json::to_value(context)?;
    let inserted = sqlx::query_as::<_, InsertedMessageRow>(
        r#"
        INSERT INTO chat_messages (
            session_id, message_list, processing_context, created_at, status
        )
        VALUES (
            $1::bigint::integer, $2, $3,
            timezone('UTC', clock_timestamp()), 'processing'
        )
        RETURNING id::bigint AS id, created_at
        "#,
    )
    .bind(child.id)
    .bind(message_list)
    .bind(&processing_context)
    .fetch_one(&mut **transaction)
    .await?;
    sqlx::query(
        r#"
        UPDATE chat_sessions
        SET last_message_at = $2, updated_at = $2
        WHERE id::bigint = $1
        "#,
    )
    .bind(child.id)
    .bind(inserted.created_at)
    .execute(&mut **transaction)
    .await?;
    Ok(StagedChatTurn {
        visible_session_id: parent.id,
        effective_session_id: child.id,
        message_id: inserted.id,
        created_at: inserted.created_at.and_utc(),
        user_prompt: user_prompt.to_owned(),
        processing_context,
    })
}

async fn insert_child_session(
    transaction: &mut Transaction<'_, Postgres>,
    parent: &CouncilSessionRow,
    persona: &CouncilPersonaSeed,
    context_snapshot: Option<&str>,
    now: NaiveDateTime,
) -> Result<i64, sqlx::Error> {
    sqlx::query_scalar::<_, i64>(
        r#"
        INSERT INTO chat_sessions (
            user_id, content_id, news_item_id, parent_session_id, title, session_type, topic,
            context_snapshot, council_persona_id, council_persona_name, council_persona_prompt,
            council_mode, active_child_session_id, branch_start_message_id, council_message_id,
            is_hidden_from_history, llm_model, llm_provider, created_at, updated_at,
            last_message_at, is_archived
        )
        VALUES (
            $1::bigint::integer, $2::bigint::integer, $3::bigint::integer,
            $4::bigint::integer, $5, $6, $7, $8, $9, $10, $11,
            FALSE, NULL, NULL, NULL, TRUE, $12, $13, $14, $14, $15, FALSE
        )
        RETURNING id::bigint
        "#,
    )
    .bind(parent.user_id)
    .bind(parent.content_id)
    .bind(parent.news_item_id)
    .bind(parent.id)
    .bind(&parent.title)
    .bind(&parent.session_type)
    .bind(&parent.topic)
    .bind(context_snapshot)
    .bind(&persona.id)
    .bind(&persona.display_name)
    .bind(&persona.impersonation_prompt)
    .bind(&parent.llm_model)
    .bind(&parent.llm_provider)
    .bind(now)
    .bind(parent.last_message_at)
    .fetch_one(&mut **transaction)
    .await
}

async fn clone_terminal_history(
    transaction: &mut Transaction<'_, Postgres>,
    source_session_id: i64,
    target_session_id: i64,
) -> Result<(), sqlx::Error> {
    sqlx::query(
        r#"
        INSERT INTO chat_messages (
            session_id, message_list, render_metadata, created_at, status, error
        )
        SELECT $2::bigint::integer, message_list, render_metadata, created_at, status, error
        FROM chat_messages
        WHERE session_id::bigint = $1 AND status <> 'processing'
        ORDER BY created_at, id
        "#,
    )
    .bind(source_session_id)
    .bind(target_session_id)
    .execute(&mut **transaction)
    .await?;
    Ok(())
}

async fn insert_parent_council_message(
    transaction: &mut Transaction<'_, Postgres>,
    parent_session_id: i64,
    user_prompt: &str,
    candidates: &[StoredCouncilCandidate],
    active_child_session_id: Option<i64>,
    now: NaiveDateTime,
) -> Result<InsertedMessageRow, CouncilRepositoryError> {
    let transcript = council_transcript(user_prompt, COUNCIL_PREPARING_TEXT, now.and_utc());
    let message_list = serde_json::to_string(&transcript)?;
    let metadata = council_metadata(None, candidates, active_child_session_id)?;
    Ok(sqlx::query_as::<_, InsertedMessageRow>(
        r#"
        INSERT INTO chat_messages (
            session_id, message_list, render_metadata, created_at, status
        )
        VALUES ($1::bigint::integer, $2, $3, $4, 'processing')
        RETURNING id::bigint AS id, created_at
        "#,
    )
    .bind(parent_session_id)
    .bind(message_list)
    .bind(metadata)
    .bind(now)
    .fetch_one(&mut **transaction)
    .await?)
}

fn council_transcript(
    user_prompt: &str,
    assistant_text: &str,
    created_at: chrono::DateTime<Utc>,
) -> NewslyTranscript {
    NewslyTranscript {
        messages: vec![
            NewslyMessage {
                id: None,
                role: MessageRole::User,
                parts: vec![MessagePart::Request(RequestPart::Text {
                    text: user_prompt.to_owned(),
                })],
                created_at,
                run_id: None,
                provider: None,
                model: None,
                finish_reason: None,
                usage: ProviderUsage::default(),
                metadata: Map::new(),
            },
            NewslyMessage {
                id: None,
                role: MessageRole::Assistant,
                parts: vec![MessagePart::Assistant(AssistantPart::Text {
                    text: assistant_text.to_owned(),
                })],
                created_at,
                run_id: None,
                provider: None,
                model: None,
                finish_reason: None,
                usage: ProviderUsage::default(),
                metadata: Map::new(),
            },
        ],
        ..NewslyTranscript::default()
    }
}

fn transcript_user_prompt(raw: &str) -> Result<String, CouncilRepositoryError> {
    let transcript = decode_transcript(raw)?;
    transcript
        .messages
        .iter()
        .filter(|message| message.role == MessageRole::User)
        .flat_map(|message| message.parts.iter())
        .find_map(|part| match part {
            MessagePart::Request(RequestPart::Text { text }) if !text.trim().is_empty() => {
                Some(text.trim().to_owned())
            }
            _ => None,
        })
        .ok_or_else(|| {
            CouncilRepositoryError::InvalidStoredState(
                "council parent transcript has no user prompt".to_owned(),
            )
        })
}

fn council_candidates(
    metadata: Option<&Value>,
) -> Result<Vec<StoredCouncilCandidate>, CouncilRepositoryError> {
    let candidates = metadata
        .and_then(Value::as_object)
        .and_then(|object| object.get("council_candidates"))
        .cloned()
        .ok_or_else(|| {
            CouncilRepositoryError::InvalidStoredState(
                "council parent metadata has no candidates".to_owned(),
            )
        })?;
    let mut candidates = serde_json::from_value::<Vec<StoredCouncilCandidate>>(candidates)?;
    candidates.sort_by_key(|candidate| candidate.order);
    if !(MIN_COUNCIL_PERSONAS..=MAX_COUNCIL_PERSONAS).contains(&candidates.len())
        || candidates.iter().enumerate().any(|(index, candidate)| {
            candidate.child_session_id <= 0
                || candidate.persona_id.trim().is_empty()
                || candidate.persona_name.trim().is_empty()
                || candidate.content.trim().is_empty()
                || usize::try_from(candidate.order) != Ok(index)
                || !matches!(
                    candidate.status.as_str(),
                    "processing" | "completed" | "failed"
                )
        })
    {
        return Err(CouncilRepositoryError::InvalidStoredState(
            "council candidate metadata is invalid".to_owned(),
        ));
    }
    Ok(candidates)
}

fn council_metadata(
    existing: Option<&Value>,
    candidates: &[StoredCouncilCandidate],
    active_child_session_id: Option<i64>,
) -> Result<Value, serde_json::Error> {
    let mut object = existing
        .and_then(Value::as_object)
        .cloned()
        .unwrap_or_default();
    object
        .entry("feed_options".to_owned())
        .or_insert_with(|| json!([]));
    object.insert(
        "council_candidates".to_owned(),
        serde_json::to_value(candidates)?,
    );
    object.insert(
        "active_council_child_session_id".to_owned(),
        active_child_session_id.map_or(Value::Null, Value::from),
    );
    Ok(Value::Object(object))
}

fn resolve_active_candidate(
    candidates: &[StoredCouncilCandidate],
    current_active: Option<i64>,
    run_kind: CouncilRunKind,
    completed_child_id: i64,
    successful: bool,
    settled: bool,
) -> Option<i64> {
    if run_kind == CouncilRunKind::Retry && successful {
        return Some(completed_child_id);
    }
    if !settled {
        return current_active.or_else(|| {
            candidates
                .first()
                .map(|candidate| candidate.child_session_id)
        });
    }
    if run_kind == CouncilRunKind::Start {
        return candidates
            .iter()
            .find(|candidate| candidate.status == "completed")
            .or_else(|| candidates.first())
            .map(|candidate| candidate.child_session_id);
    }
    current_active
        .filter(|active| {
            candidates
                .iter()
                .any(|candidate| candidate.child_session_id == *active)
        })
        .or_else(|| {
            candidates
                .iter()
                .find(|candidate| candidate.status == "completed")
                .or_else(|| candidates.first())
                .map(|candidate| candidate.child_session_id)
        })
}

fn valid_user_prompt(value: &str) -> bool {
    let length = value.trim().chars().count();
    (1..=MAX_USER_PROMPT_CHARS).contains(&length)
}

fn valid_personas(personas: &[CouncilPersonaSeed]) -> bool {
    if !(MIN_COUNCIL_PERSONAS..=MAX_COUNCIL_PERSONAS).contains(&personas.len()) {
        return false;
    }
    let mut ids = HashSet::new();
    personas.iter().enumerate().all(|(index, persona)| {
        !persona.id.trim().is_empty()
            && persona.id.chars().count() <= 50
            && ids.insert(persona.id.as_str())
            && !persona.display_name.trim().is_empty()
            && persona.display_name.chars().count() <= 80
            && !persona.context_suffix.trim().is_empty()
            && !persona.impersonation_prompt.trim().is_empty()
            && usize::try_from(persona.sort_order) == Ok(index)
    })
}

fn join_context(first: Option<&str>, second: Option<&str>) -> Option<String> {
    let sections = [first, second]
        .into_iter()
        .flatten()
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .collect::<Vec<_>>();
    (!sections.is_empty()).then(|| sections.join("\n\n"))
}

fn preparing_candidate_text(persona_name: &str) -> String {
    format!("{} is preparing a response.", persona_name.trim())
}

async fn load_session_for_update(
    transaction: &mut Transaction<'_, Postgres>,
    session_id: i64,
) -> Result<Option<CouncilSessionRow>, sqlx::Error> {
    sqlx::query_as::<_, CouncilSessionRow>(
        r#"
        SELECT
            id::bigint AS id,
            user_id::bigint AS user_id,
            content_id::bigint AS content_id,
            news_item_id::bigint AS news_item_id,
            parent_session_id::bigint AS parent_session_id,
            title, session_type, topic, context_snapshot,
            council_persona_id, council_persona_name, council_persona_prompt,
            council_mode,
            active_child_session_id::bigint AS active_child_session_id,
            council_message_id::bigint AS council_message_id,
            is_hidden_from_history, llm_model, llm_provider,
            last_message_at, is_archived
        FROM chat_sessions
        WHERE id::bigint = $1
        FOR UPDATE
        "#,
    )
    .bind(session_id)
    .fetch_optional(&mut **transaction)
    .await
}

async fn load_council_message_for_update(
    transaction: &mut Transaction<'_, Postgres>,
    message_id: i64,
) -> Result<Option<CouncilMessageRow>, sqlx::Error> {
    sqlx::query_as::<_, CouncilMessageRow>(
        r#"
        SELECT id::bigint AS id, session_id::bigint AS session_id,
               message_list, render_metadata
        FROM chat_messages
        WHERE id::bigint = $1
        FOR UPDATE
        "#,
    )
    .bind(message_id)
    .fetch_optional(&mut **transaction)
    .await
}

async fn lock_active_user(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
) -> Result<bool, sqlx::Error> {
    Ok(sqlx::query_scalar::<_, i64>(
        "SELECT id::bigint FROM users WHERE id::bigint = $1 AND is_active = TRUE FOR SHARE",
    )
    .bind(user_id)
    .fetch_optional(&mut **transaction)
    .await?
    .is_some())
}

#[derive(Debug, Error)]
pub enum CouncilRepositoryError {
    #[error("council chat database operation failed")]
    Sqlx(#[from] sqlx::Error),
    #[error("council chat JSON operation failed")]
    Json(#[from] serde_json::Error),
    #[error("council chat transcript operation failed")]
    Transcript(#[from] ChatTranscriptError),
    #[error("council chat stored state is invalid: {0}")]
    InvalidStoredState(String),
}
