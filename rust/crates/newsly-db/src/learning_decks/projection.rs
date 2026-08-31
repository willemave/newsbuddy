//! User and hosted Learning Deck read projections, including legacy-run fallback.

use super::{
    AssertSqlSafe, HostedLearningDeckProjection, HostedLearningDeckRow,
    LearningDeckAttemptProjection, LearningDeckProjection, LearningDeckRepositoryError,
    LearningDeckRow, LearningDeckTimelineProjection, Map, PgPool, Value,
    common::{
        clean_optional, json_clean_text, json_object, parse_utc, resolve_display_title,
        string_values,
    },
};

/// Lists current decks using the canonical LLM-task ledger with legacy-run fallback.
///
/// # Errors
///
/// Returns [`LearningDeckRepositoryError::Sqlx`] when PostgreSQL cannot complete the query.
pub async fn list_learning_decks(
    pool: &PgPool,
    user_id: i64,
) -> Result<Vec<LearningDeckProjection>, LearningDeckRepositoryError> {
    let rows = sqlx::query_as::<_, LearningDeckRow>(AssertSqlSafe(format!(
        "{LEARNING_DECK_PROJECTION_SQL} WHERE deck.user_id::bigint = $1 AND deck.deleted_at IS NULL ORDER BY deck.updated_at DESC, deck.id DESC"
    )))
    .bind(user_id)
    .fetch_all(pool)
    .await?;
    Ok(rows.into_iter().map(LearningDeckRow::project).collect())
}

/// Gets one current, user-owned deck.
///
/// # Errors
///
/// Returns [`LearningDeckRepositoryError::Sqlx`] when PostgreSQL cannot complete the query.
pub async fn get_learning_deck(
    pool: &PgPool,
    user_id: i64,
    deck_id: i64,
) -> Result<Option<LearningDeckProjection>, LearningDeckRepositoryError> {
    let row = sqlx::query_as::<_, LearningDeckRow>(AssertSqlSafe(format!(
        "{LEARNING_DECK_PROJECTION_SQL} WHERE deck.user_id::bigint = $1 AND deck.id::bigint = $2 AND deck.deleted_at IS NULL"
    )))
    .bind(user_id)
    .bind(deck_id)
    .fetch_optional(pool)
    .await?;
    Ok(row.map(LearningDeckRow::project))
}

/// Gets the persisted hosting pointers and access state for one non-deleted deck.
///
/// # Errors
///
/// Returns [`LearningDeckRepositoryError::Sqlx`] when PostgreSQL cannot complete the query.
pub async fn get_hosted_learning_deck(
    pool: &PgPool,
    deck_id: i64,
) -> Result<Option<HostedLearningDeckProjection>, LearningDeckRepositoryError> {
    let row = sqlx::query_as::<_, HostedLearningDeckRow>(
        r#"
        SELECT
            id::bigint AS id,
            user_id::bigint AS user_id,
            artifact_storage_prefix,
            deck_object_key,
            source_notes_html_object_key,
            artifact_object_keys,
            share_enabled,
            share_token_hash,
            share_token_nonce,
            latest_successful_task_id::bigint AS latest_successful_task_id,
            latest_successful_run_id::bigint AS latest_successful_run_id
        FROM learning_decks
        WHERE id::bigint = $1 AND deleted_at IS NULL
        "#,
    )
    .bind(deck_id)
    .fetch_optional(pool)
    .await?;
    Ok(row.map(HostedLearningDeckRow::project))
}

impl LearningDeckRow {
    fn project(self) -> LearningDeckProjection {
        let latest_successful_attempt_id = self
            .latest_successful_task_id
            .or(self.latest_successful_run_id);
        let latest_attempt = if let (Some(id), Some(status), Some(created_at)) =
            (self.task_id, self.task_status, self.task_created_at)
        {
            Some(LearningDeckAttemptProjection {
                id,
                status: attempt_status_for_wire(&status),
                interests_prompt: self
                    .task_input_json
                    .as_ref()
                    .and_then(|value| json_clean_text(value, "interests_prompt")),
                timeline: parse_timeline(self.task_status_history.as_ref(), true),
                error_type: self.task_error_type,
                error_message: self.task_error_message,
                started_at: self.task_started_at.map(|value| value.and_utc()),
                completed_at: self.task_completed_at.map(|value| value.and_utc()),
                created_at: created_at.and_utc(),
                updated_at: self.task_updated_at.map(|value| value.and_utc()),
            })
        } else if let (Some(id), Some(status), Some(created_at)) =
            (self.run_id, self.run_status, self.run_created_at)
        {
            Some(LearningDeckAttemptProjection {
                id,
                status,
                interests_prompt: clean_optional(self.run_interests_prompt.as_deref())
                    .map(str::to_owned),
                timeline: parse_timeline(self.run_timeline.as_ref(), false),
                error_type: None,
                error_message: self.run_error_message,
                started_at: self.run_started_at.map(|value| value.and_utc()),
                completed_at: self.run_completed_at.map(|value| value.and_utc()),
                created_at: created_at.and_utc(),
                updated_at: self.run_updated_at.map(|value| value.and_utc()),
            })
        } else {
            None
        };
        let source_metadata = json_object(self.source_metadata);
        let content_metadata = self
            .content_metadata
            .as_ref()
            .map_or_else(Map::new, |value| json_object(value.clone()));
        let title = resolve_display_title(
            &source_metadata,
            &content_metadata,
            self.content_title.as_deref(),
            self.source_title.as_deref(),
            &self.stored_title,
        );
        let artifact_keys = string_values(&self.artifact_object_keys);
        let thumbnail_key = self
            .artifact_storage_prefix
            .as_ref()
            .map(|prefix| format!("{prefix}/assets/thumbnail.png"));
        LearningDeckProjection {
            id: self.id,
            user_id: self.user_id,
            title,
            source_kind: self.source_kind,
            source_url: self.source_url,
            source_content_id: self.source_content_id,
            source_metadata,
            share_enabled: self.share_enabled,
            viewer_available: self.deck_object_key.is_some()
                && latest_successful_attempt_id.is_some(),
            source_notes_available: self.source_notes_html_object_key.is_some()
                && latest_successful_attempt_id.is_some(),
            thumbnail_available: latest_successful_attempt_id.is_some()
                && thumbnail_key.is_some_and(|key| artifact_keys.contains(&key)),
            artifact_storage_prefix: self.artifact_storage_prefix,
            latest_successful_attempt_id,
            latest_attempt,
            created_at: self.created_at.and_utc(),
            updated_at: self.updated_at.map(|value| value.and_utc()),
        }
    }
}

impl HostedLearningDeckRow {
    fn project(self) -> HostedLearningDeckProjection {
        HostedLearningDeckProjection {
            id: self.id,
            user_id: self.user_id,
            artifact_storage_prefix: self.artifact_storage_prefix,
            deck_object_key: self.deck_object_key,
            source_notes_html_object_key: self.source_notes_html_object_key,
            artifact_object_keys: string_values(&self.artifact_object_keys),
            share_enabled: self.share_enabled,
            share_token_hash: clean_optional(self.share_token_hash.as_deref()).map(str::to_owned),
            share_token_nonce: clean_optional(self.share_token_nonce.as_deref()).map(str::to_owned),
            latest_successful_attempt_id: self
                .latest_successful_task_id
                .or(self.latest_successful_run_id),
        }
    }
}

pub(super) fn parse_timeline(
    value: Option<&Value>,
    task_projection: bool,
) -> Vec<LearningDeckTimelineProjection> {
    value
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(|entry| {
            let entry = entry.as_object()?;
            let note = clean_optional(Some(entry.get("note")?.as_str()?))?.to_owned();
            let raw_status = entry.get("status")?.as_str()?;
            let status = if task_projection {
                attempt_status_for_wire(raw_status)
            } else {
                raw_status.to_owned()
            };
            let created_at = parse_utc(entry.get("created_at")?.as_str()?)?;
            Some(LearningDeckTimelineProjection {
                status,
                note,
                created_at,
            })
        })
        .collect()
}

pub(super) fn attempt_status_for_wire(status: &str) -> String {
    match status {
        "running" | "awaiting_approval" => "generating",
        "applying" => "publishing",
        "cancelled" => "failed",
        value => value,
    }
    .to_owned()
}

pub(super) fn public_error_message(
    error_type: Option<&str>,
    error_message: Option<&str>,
) -> Option<String> {
    let error_message = clean_optional(error_message)?;
    let mapped = match error_type {
        Some("agent_execution_failed") => {
            Some("Learning Deck generation failed. Please try again.")
        }
        Some("artifact_contract_failed") => {
            Some("Learning Deck validation failed. Please try again.")
        }
        Some("source_not_found") => Some("Source content no longer exists"),
        Some("source_processing_failed") => {
            Some("Source content processing failed. Please try again.")
        }
        Some("source_text_unavailable") => Some("Source content does not have readable text"),
        Some("source_pipeline_stalled") => {
            Some("Source content is still being prepared. Please try again.")
        }
        _ => None,
    };
    if let Some(mapped) = mapped {
        return Some(mapped.to_owned());
    }
    let lowered = error_message.to_ascii_lowercase();
    if ["[sql:", "sqlalchemy", "psycopg", "unique constraint"]
        .iter()
        .any(|marker| lowered.contains(marker))
    {
        Some("Learning Deck generation failed. Please try again.".to_owned())
    } else {
        Some(error_message.to_owned())
    }
}

const LEARNING_DECK_PROJECTION_SQL: &str = r#"
    SELECT
        deck.id::bigint AS id,
        deck.user_id::bigint AS user_id,
        deck.source_kind,
        deck.source_url,
        deck.source_content_id::bigint AS source_content_id,
        deck.source_title,
        deck.source_metadata,
        deck.title AS stored_title,
        deck.artifact_storage_prefix,
        deck.deck_object_key,
        deck.source_notes_html_object_key,
        deck.artifact_object_keys,
        deck.share_enabled,
        deck.created_at,
        deck.updated_at,
        deck.latest_successful_task_id::bigint AS latest_successful_task_id,
        deck.latest_successful_run_id::bigint AS latest_successful_run_id,
        source_content.title AS content_title,
        source_content.content_metadata AS content_metadata,
        latest_task.id::bigint AS task_id,
        latest_task.status AS task_status,
        latest_task.input_json AS task_input_json,
        latest_task.status_history AS task_status_history,
        latest_task.error_type AS task_error_type,
        latest_task.error_message AS task_error_message,
        latest_task.started_at AS task_started_at,
        latest_task.completed_at AS task_completed_at,
        latest_task.created_at AS task_created_at,
        latest_task.updated_at AS task_updated_at,
        latest_run.id::bigint AS run_id,
        latest_run.status AS run_status,
        latest_run.interests_prompt AS run_interests_prompt,
        latest_run.timeline AS run_timeline,
        latest_run.error_message AS run_error_message,
        latest_run.started_at AS run_started_at,
        latest_run.completed_at AS run_completed_at,
        latest_run.created_at AS run_created_at,
        latest_run.updated_at AS run_updated_at
    FROM learning_decks AS deck
    LEFT JOIN contents AS source_content ON source_content.id = deck.source_content_id
    LEFT JOIN LATERAL (
        SELECT candidate.*
        FROM llm_tasks AS candidate
        WHERE candidate.task_kind = 'learning_deck' AND candidate.subject_id = deck.id
        ORDER BY (candidate.id = deck.latest_task_id) DESC, candidate.created_at DESC, candidate.id DESC
        LIMIT 1
    ) AS latest_task ON TRUE
    LEFT JOIN LATERAL (
        SELECT candidate.*
        FROM learning_deck_runs AS candidate
        WHERE latest_task.id IS NULL AND candidate.deck_id = deck.id
        ORDER BY (candidate.id = deck.latest_run_id) DESC, candidate.created_at DESC, candidate.id DESC
        LIMIT 1
    ) AS latest_run ON TRUE
"#;

// Keep the public error mapping beside the persisted projection even though presentation happens
// in the HTTP crate. This prevents raw provider/validator/SQL details from escaping through any
// future Rust consumer of the same projection.
impl LearningDeckAttemptProjection {
    #[must_use]
    pub fn public_error_message(&self) -> Option<String> {
        public_error_message(self.error_type.as_deref(), self.error_message.as_deref())
    }
}
