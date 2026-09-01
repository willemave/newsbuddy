//! Deck deletion and public-share lifecycle transitions.

use super::{
    ACTIVE_ATTEMPT_STATUSES, BTreeSet, DeletedLearningDeck, DisableLearningDeckShareOutcome,
    EnableLearningDeckShareOutcome, LearningDeckRepositoryError, Postgres, Transaction, Utc, Value,
    common::{collect_string_values, legacy_naive_iso, status_history_entry},
    json,
};

/// Soft-deletes a deck, cancels active attempts, and returns immutable external-cleanup work.
///
/// # Errors
///
/// Returns [`LearningDeckRepositoryError::Sqlx`] when PostgreSQL cannot finalize the deletion.
pub async fn delete_learning_deck(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
    deck_id: i64,
) -> Result<Option<DeletedLearningDeck>, LearningDeckRepositoryError> {
    let deck_keys = sqlx::query_scalar::<_, Value>(
        "SELECT artifact_object_keys FROM learning_decks WHERE id::bigint = $1 AND user_id::bigint = $2 AND deleted_at IS NULL FOR UPDATE",
    )
    .bind(deck_id)
    .bind(user_id)
    .fetch_optional(&mut **transaction)
    .await?;
    let Some(deck_keys) = deck_keys else {
        return Ok(None);
    };
    let run_keys = sqlx::query_scalar::<_, Value>(
        "SELECT artifact_object_keys FROM learning_deck_runs WHERE deck_id::bigint = $1",
    )
    .bind(deck_id)
    .fetch_all(&mut **transaction)
    .await?;
    let now = Utc::now();
    let task_entry =
        status_history_entry("cancelled", "cancelled", "Learning Deck was deleted", now);
    sqlx::query(
        r#"
        UPDATE llm_tasks
        SET status = 'cancelled',
            workflow_state = 'cancelled',
            error_type = 'deck_deleted',
            error_message = 'Learning Deck was deleted',
            completed_at = timezone('UTC', now()),
            updated_at = timezone('UTC', now()),
            status_history = COALESCE(status_history, '[]'::jsonb) || jsonb_build_array($2::jsonb)
        WHERE task_kind = 'learning_deck'
          AND subject_id::bigint = $1
          AND status = ANY($3)
        "#,
    )
    .bind(deck_id)
    .bind(task_entry)
    .bind(ACTIVE_ATTEMPT_STATUSES)
    .execute(&mut **transaction)
    .await?;
    let run_entry = json!({
        "status": "cancelled",
        "note": "Learning Deck was deleted",
        "created_at": legacy_naive_iso(now),
    });
    sqlx::query(
        r#"
        UPDATE learning_deck_runs
        SET status = 'cancelled',
            error_message = 'Learning Deck was deleted',
            completed_at = timezone('UTC', now()),
            updated_at = timezone('UTC', now()),
            timeline = COALESCE(timeline, '[]'::jsonb) || jsonb_build_array($2::jsonb)
        WHERE deck_id::bigint = $1
          AND status IN ('queued', 'preparing', 'generating', 'validating', 'publishing')
        "#,
    )
    .bind(deck_id)
    .bind(run_entry)
    .execute(&mut **transaction)
    .await?;
    sqlx::query(
        r#"
        UPDATE learning_decks
        SET deleted_at = timezone('UTC', now()),
            share_enabled = FALSE,
            updated_at = timezone('UTC', now())
        WHERE id::bigint = $1
        "#,
    )
    .bind(deck_id)
    .execute(&mut **transaction)
    .await?;
    let mut keys = BTreeSet::new();
    collect_string_values(&deck_keys, &mut keys);
    for value in run_keys {
        collect_string_values(&value, &mut keys);
    }
    Ok(Some(DeletedLearningDeck {
        object_keys: keys.into_iter().collect(),
    }))
}

/// Locks an owned deck and returns the durable nonce used to construct its share token.
///
/// # Errors
///
/// Returns [`LearningDeckRepositoryError::Sqlx`] when PostgreSQL cannot lock the row.
pub async fn prepare_enable_learning_deck_share(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
    deck_id: i64,
    generated_nonce: &str,
) -> Result<EnableLearningDeckShareOutcome, LearningDeckRepositoryError> {
    let row = sqlx::query_as::<_, (Option<i64>, Option<i64>, Option<String>)>(
        r#"
        SELECT latest_successful_task_id::bigint, latest_successful_run_id::bigint, share_token_nonce
        FROM learning_decks
        WHERE id::bigint = $1 AND user_id::bigint = $2 AND deleted_at IS NULL
        FOR UPDATE
        "#,
    )
    .bind(deck_id)
    .bind(user_id)
    .fetch_optional(&mut **transaction)
    .await?;
    let Some((successful_task, successful_run, nonce)) = row else {
        return Ok(EnableLearningDeckShareOutcome::DeckNotFound);
    };
    if successful_task.is_none() && successful_run.is_none() {
        return Ok(EnableLearningDeckShareOutcome::DeckNotReady);
    }
    Ok(EnableLearningDeckShareOutcome::Ready {
        deck_id,
        nonce: nonce.unwrap_or_else(|| generated_nonce.to_owned()),
    })
}

/// Persists the share token digest after it has been signed outside the repository.
///
/// # Errors
///
/// Returns [`LearningDeckRepositoryError::Sqlx`] when PostgreSQL cannot persist the state.
pub async fn persist_learning_deck_share(
    transaction: &mut Transaction<'_, Postgres>,
    deck_id: i64,
    nonce: &str,
    token_hash: &str,
) -> Result<(), LearningDeckRepositoryError> {
    sqlx::query(
        r#"
        UPDATE learning_decks
        SET share_token_nonce = $2,
            share_token_hash = $3,
            share_enabled = TRUE,
            updated_at = timezone('UTC', now())
        WHERE id::bigint = $1
        "#,
    )
    .bind(deck_id)
    .bind(nonce)
    .bind(token_hash)
    .execute(&mut **transaction)
    .await?;
    Ok(())
}

/// Disables public sharing without rotating the stable token nonce.
///
/// # Errors
///
/// Returns [`LearningDeckRepositoryError::Sqlx`] when PostgreSQL cannot update the row.
pub async fn disable_learning_deck_share(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
    deck_id: i64,
) -> Result<DisableLearningDeckShareOutcome, LearningDeckRepositoryError> {
    let result = sqlx::query(
        r#"
        UPDATE learning_decks
        SET share_enabled = FALSE, updated_at = timezone('UTC', now())
        WHERE id::bigint = $1 AND user_id::bigint = $2 AND deleted_at IS NULL
        "#,
    )
    .bind(deck_id)
    .bind(user_id)
    .execute(&mut **transaction)
    .await?;
    Ok(if result.rows_affected() == 0 {
        DisableLearningDeckShareOutcome::DeckNotFound
    } else {
        DisableLearningDeckShareOutcome::Disabled
    })
}
