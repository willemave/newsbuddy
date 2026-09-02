//! Stable deck upserts and canonical LLM-task attempt creation or retry.

use super::{
    ACTIVE_ATTEMPT_STATUSES, ActiveAttemptRow, AttemptStateRow, CreateLearningDeckOutcome,
    LearningDeckRepositoryError, LearningDeckSourceProjection, Map, Postgres,
    RetryLearningDeckOutcome, Transaction, Utc, Value,
    canonical::persisted_source_with_canonical_rebind,
    common::{clean_optional, clean_title, json_clean_text, status_history_entry},
    json,
};

/// Creates or updates the stable deck and creates one LLM-task attempt when no attempt is active.
///
/// # Errors
///
/// Returns [`LearningDeckRepositoryError`] when PostgreSQL fails or the active-attempt uniqueness
/// fence is crossed by a concurrent request.
pub async fn create_or_rerun_learning_deck(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
    source: &LearningDeckSourceProjection,
    interests_prompt: Option<&str>,
    sandbox_root: &str,
) -> Result<CreateLearningDeckOutcome, LearningDeckRepositoryError> {
    let deck_id = upsert_learning_deck(transaction, user_id, source).await?;
    if let Some(active) = active_learning_deck_attempt(transaction, user_id).await? {
        return Ok(if active.subject_id == Some(deck_id) {
            CreateLearningDeckOutcome::ExistingActiveAttempt { deck_id }
        } else {
            CreateLearningDeckOutcome::AnotherDeckActive
        });
    }
    let task_id = insert_learning_deck_attempt(
        transaction,
        user_id,
        deck_id,
        source,
        clean_optional(interests_prompt),
        None,
        sandbox_root,
    )
    .await?;
    sqlx::query(
        "UPDATE learning_decks SET latest_task_id = $2::bigint::integer, updated_at = timezone('UTC', now()) WHERE id::bigint = $1",
    )
    .bind(deck_id)
    .bind(task_id)
    .execute(&mut **transaction)
    .await?;
    Ok(CreateLearningDeckOutcome::AttemptCreated { deck_id, task_id })
}

/// Retries only a failed/cancelled latest attempt and returns an active retry idempotently.
///
/// # Errors
///
/// Returns [`LearningDeckRepositoryError`] when PostgreSQL or canonical-source reconciliation
/// fails.
pub async fn retry_learning_deck(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
    deck_id: i64,
    sandbox_root: &str,
) -> Result<RetryLearningDeckOutcome, LearningDeckRepositoryError> {
    let owned = sqlx::query_scalar::<_, i64>(
        "SELECT id::bigint FROM learning_decks WHERE id::bigint = $1 AND user_id::bigint = $2 AND deleted_at IS NULL FOR UPDATE",
    )
    .bind(deck_id)
    .bind(user_id)
    .fetch_optional(&mut **transaction)
    .await?;
    if owned.is_none() {
        return Ok(RetryLearningDeckOutcome::DeckNotFound);
    }

    if let Some(active) = active_learning_deck_attempt(transaction, user_id).await? {
        let is_retry = active
            .input_json
            .as_object()
            .and_then(|input| input.get("retry_of_attempt_id"))
            .and_then(Value::as_i64)
            .is_some();
        return Ok(if active.subject_id == Some(deck_id) && is_retry {
            RetryLearningDeckOutcome::ExistingActiveRetry { deck_id }
        } else if active.subject_id == Some(deck_id) {
            RetryLearningDeckOutcome::NoFailedAttempt
        } else {
            RetryLearningDeckOutcome::AnotherDeckActive
        });
    }

    let Some(latest) = latest_attempt_state(transaction, deck_id).await? else {
        return Ok(RetryLearningDeckOutcome::NoFailedAttempt);
    };
    if latest.status != "failed" && latest.status != "cancelled" {
        return Ok(RetryLearningDeckOutcome::NoFailedAttempt);
    }
    let source = persisted_source_with_canonical_rebind(transaction, user_id, deck_id).await?;
    let task_id = insert_learning_deck_attempt(
        transaction,
        user_id,
        deck_id,
        &source,
        clean_optional(latest.interests_prompt.as_deref()),
        Some(latest.id),
        sandbox_root,
    )
    .await?;
    sqlx::query(
        "UPDATE learning_decks SET latest_task_id = $2::bigint::integer, updated_at = timezone('UTC', now()) WHERE id::bigint = $1",
    )
    .bind(deck_id)
    .bind(task_id)
    .execute(&mut **transaction)
    .await?;
    Ok(RetryLearningDeckOutcome::AttemptCreated { deck_id, task_id })
}

#[must_use]
pub fn is_active_learning_deck_conflict(error: &LearningDeckRepositoryError) -> bool {
    matches!(error, LearningDeckRepositoryError::Sqlx(sqlx::Error::Database(database))
        if database.constraint() == Some("uq_llm_tasks_learning_deck_user_active"))
}

pub(super) async fn upsert_learning_deck(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
    source: &LearningDeckSourceProjection,
) -> Result<i64, LearningDeckRepositoryError> {
    let mut deck_id = sqlx::query_scalar::<_, i64>(
        r#"
        SELECT id::bigint
        FROM learning_decks
        WHERE user_id::bigint = $1
          AND source_identity = $2
          AND deleted_at IS NULL
        FOR UPDATE
        "#,
    )
    .bind(user_id)
    .bind(&source.source_identity)
    .fetch_optional(&mut **transaction)
    .await?;
    if deck_id.is_none()
        && source.source_kind == "content"
        && let Some(content_id) = source.source_content_id
    {
        deck_id = sqlx::query_scalar::<_, i64>(
            r#"
                SELECT id::bigint
                FROM learning_decks
                WHERE user_id::bigint = $1
                  AND source_content_id::bigint = $2
                  AND deleted_at IS NULL
                ORDER BY updated_at DESC, id DESC
                LIMIT 1
                FOR UPDATE
                "#,
        )
        .bind(user_id)
        .bind(content_id)
        .fetch_optional(&mut **transaction)
        .await?;
    }
    let deck_id = if let Some(deck_id) = deck_id {
        deck_id
    } else {
        let inserted = sqlx::query_scalar::<_, i64>(
            r#"
            INSERT INTO learning_decks (
                user_id, source_kind, source_identity, source_url, source_content_id,
                source_title, source_metadata, title, artifact_object_keys, share_enabled,
                created_at, updated_at
            )
            VALUES (
                $1::bigint::integer, $2, $3, $4, $5::bigint::integer,
                $6, $7, $6, '[]'::jsonb, FALSE,
                timezone('UTC', now()), timezone('UTC', now())
            )
            ON CONFLICT (user_id, source_identity) WHERE deleted_at IS NULL DO NOTHING
            RETURNING id::bigint
            "#,
        )
        .bind(user_id)
        .bind(&source.source_kind)
        .bind(&source.source_identity)
        .bind(&source.source_url)
        .bind(source.source_content_id)
        .bind(&source.source_title)
        .bind(Value::Object(source.source_metadata.clone()))
        .fetch_optional(&mut **transaction)
        .await?;
        match inserted {
            Some(deck_id) => return Ok(deck_id),
            None => {
                sqlx::query_scalar::<_, i64>(
                    r#"
                SELECT id::bigint FROM learning_decks
                WHERE user_id::bigint = $1 AND source_identity = $2 AND deleted_at IS NULL
                FOR UPDATE
                "#,
                )
                .bind(user_id)
                .bind(&source.source_identity)
                .fetch_one(&mut **transaction)
                .await?
            }
        }
    };
    let existing = sqlx::query_as::<_, (String, Value)>(
        "SELECT title, source_metadata FROM learning_decks WHERE id::bigint = $1 FOR UPDATE",
    )
    .bind(deck_id)
    .fetch_one(&mut **transaction)
    .await?;
    let mut metadata = source.source_metadata.clone();
    if !metadata.contains_key("submission")
        && let Some(submission) = existing
            .1
            .as_object()
            .and_then(|value| value.get("submission"))
            .and_then(Value::as_object)
    {
        metadata.insert("submission".to_owned(), Value::Object(submission.clone()));
    }
    let title = if clean_title(Some(&existing.0)).is_some() {
        existing.0
    } else {
        source.source_title.clone()
    };
    sqlx::query(
        r#"
        UPDATE learning_decks
        SET source_kind = $2,
            source_identity = $3,
            source_url = $4,
            source_content_id = $5::bigint::integer,
            source_title = $6,
            source_metadata = $7,
            title = $8,
            updated_at = timezone('UTC', now())
        WHERE id::bigint = $1
        "#,
    )
    .bind(deck_id)
    .bind(&source.source_kind)
    .bind(&source.source_identity)
    .bind(&source.source_url)
    .bind(source.source_content_id)
    .bind(&source.source_title)
    .bind(Value::Object(metadata))
    .bind(title)
    .execute(&mut **transaction)
    .await?;
    Ok(deck_id)
}

pub(super) async fn active_learning_deck_attempt(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
) -> Result<Option<ActiveAttemptRow>, sqlx::Error> {
    sqlx::query_as::<_, ActiveAttemptRow>(
        r#"
        SELECT subject_id::bigint AS subject_id, input_json
        FROM llm_tasks
        WHERE user_id::bigint = $1
          AND task_kind = 'learning_deck'
          AND status = ANY($2)
        ORDER BY created_at DESC, id DESC
        LIMIT 1
        FOR UPDATE
        "#,
    )
    .bind(user_id)
    .bind(ACTIVE_ATTEMPT_STATUSES)
    .fetch_optional(&mut **transaction)
    .await
}

#[allow(clippy::too_many_arguments)]
pub(super) async fn insert_learning_deck_attempt(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
    deck_id: i64,
    source: &LearningDeckSourceProjection,
    interests_prompt: Option<&str>,
    retry_of_attempt_id: Option<i64>,
    sandbox_root: &str,
) -> Result<i64, LearningDeckRepositoryError> {
    let now = Utc::now();
    let mut input = Map::from_iter([
        ("deck_id".to_owned(), Value::from(deck_id)),
        (
            "source".to_owned(),
            json!({
                "source_kind": source.source_kind,
                "source_identity": source.source_identity,
                "source_url": source.source_url,
                "source_content_id": source.source_content_id,
                "source_title": source.source_title,
                "source_metadata": source.source_metadata,
            }),
        ),
        (
            "interests_prompt".to_owned(),
            interests_prompt.map_or(Value::Null, Value::from),
        ),
    ]);
    if let Some(retry_of_attempt_id) = retry_of_attempt_id {
        input.insert(
            "retry_of_attempt_id".to_owned(),
            Value::from(retry_of_attempt_id),
        );
    }
    let task_id = sqlx::query_scalar::<_, i64>(
        r#"
        INSERT INTO llm_tasks (
            user_id, task_kind, mode, workflow_key, workflow_version, subject_id,
            workflow_state, status, approval_policy, allowed_actions, tool_policy,
            prompt_pack, input_json, output_json, artifact_manifest, usage_json,
            status_history, created_at, updated_at
        )
        VALUES (
            $1::bigint::integer, 'learning_deck', 'learning_deck_presentation',
            'learning_deck.presentation.v1', 1, $2::bigint::integer,
            'queued', 'queued', '{"default":"auto_apply"}'::jsonb,
            '["create_learning_deck"]'::jsonb,
            '{"execute_bash":true,"web_search":true,"files":"read_write"}'::jsonb,
            'learning_deck.presentation', $3, '{}'::jsonb, '{}'::jsonb, '{}'::jsonb,
            $4, $5, $5
        )
        RETURNING id::bigint
        "#,
    )
    .bind(user_id)
    .bind(deck_id)
    .bind(Value::Object(input))
    .bind(Value::Array(vec![status_history_entry(
        "queued",
        "queued",
        "LLM task created",
        now,
    )]))
    .bind(now.naive_utc())
    .fetch_one(&mut **transaction)
    .await?;
    let normalized_root = sandbox_root.trim_end_matches('/');
    sqlx::query(
        r#"
        UPDATE llm_tasks
        SET workspace_path = $2
        WHERE id::bigint = $1
        "#,
    )
    .bind(task_id)
    .bind(format!("{normalized_root}/tasks/{task_id}"))
    .execute(&mut **transaction)
    .await?;
    Ok(task_id)
}

pub(super) async fn latest_attempt_state(
    transaction: &mut Transaction<'_, Postgres>,
    deck_id: i64,
) -> Result<Option<AttemptStateRow>, LearningDeckRepositoryError> {
    let task = sqlx::query_as::<_, (i64, String, Value)>(
        r#"
        SELECT task.id::bigint, task.status, task.input_json
        FROM learning_decks AS deck
        JOIN LATERAL (
            SELECT candidate.*
            FROM llm_tasks AS candidate
            WHERE candidate.task_kind = 'learning_deck' AND candidate.subject_id = deck.id
            ORDER BY (candidate.id = deck.latest_task_id) DESC, candidate.created_at DESC, candidate.id DESC
            LIMIT 1
        ) AS task ON TRUE
        WHERE deck.id::bigint = $1
        "#,
    )
    .bind(deck_id)
    .fetch_optional(&mut **transaction)
    .await?;
    if let Some((id, status, input)) = task {
        return Ok(Some(AttemptStateRow {
            id,
            status,
            interests_prompt: json_clean_text(&input, "interests_prompt"),
        }));
    }
    Ok(sqlx::query_as::<_, AttemptStateRow>(
        r#"
        SELECT run.id::bigint AS id, run.status, run.interests_prompt
        FROM learning_decks AS deck
        JOIN LATERAL (
            SELECT candidate.*
            FROM learning_deck_runs AS candidate
            WHERE candidate.deck_id = deck.id
            ORDER BY (candidate.id = deck.latest_run_id) DESC, candidate.created_at DESC, candidate.id DESC
            LIMIT 1
        ) AS run ON TRUE
        WHERE deck.id::bigint = $1
        "#,
    )
    .bind(deck_id)
    .fetch_optional(&mut **transaction)
    .await?)
}
