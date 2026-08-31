//! Canonical content resolution and transactional source rebinding for generation.

use super::{
    BTreeSet, CanonicalContentRow, LearningDeckRepositoryError, LearningDeckSourceProjection, Map,
    PersistedDeckSourceRow, Postgres, Transaction, Value,
    common::{
        clean_optional, clean_title, coerce_i64, content_display_title, extract_x_status_id,
        json_object, processing_value,
    },
};

/// Rebuilds and, when needed, canonically rebinds the source used by a generation worker.
///
/// This is the single canonical-content owner shared by the HTTP create/retry path and the
/// `run_llm_task` executor. The caller supplies the surrounding short transaction and must commit
/// before reading content objects or starting any external agent work.
pub(crate) async fn prepare_learning_deck_generation_source(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
    deck_id: i64,
) -> Result<LearningDeckSourceProjection, LearningDeckRepositoryError> {
    persisted_source_with_canonical_rebind(transaction, user_id, deck_id).await
}

pub(super) async fn persisted_source_with_canonical_rebind(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
    deck_id: i64,
) -> Result<LearningDeckSourceProjection, LearningDeckRepositoryError> {
    let deck = sqlx::query_as::<_, PersistedDeckSourceRow>(
        r#"
        SELECT source_kind, source_identity, source_url,
               source_content_id::bigint AS source_content_id,
               source_title, title, source_metadata
        FROM learning_decks
        WHERE id::bigint = $1 AND user_id::bigint = $2 AND deleted_at IS NULL
        FOR UPDATE
        "#,
    )
    .bind(deck_id)
    .bind(user_id)
    .fetch_one(&mut **transaction)
    .await?;
    if deck.source_kind != "content" {
        if deck.source_kind != "github_repo" {
            return Err(LearningDeckRepositoryError::UnsupportedSource(
                deck.source_kind,
            ));
        }
        return Ok(persisted_deck_source(deck));
    }
    let content_id = deck
        .source_content_id
        .ok_or(LearningDeckRepositoryError::DeckContentSourceMissing)?;
    let canonical = resolve_canonical_content(transaction, content_id).await?;
    if canonical.id == content_id {
        return Ok(persisted_deck_source(deck));
    }
    let mut source = canonical_content_source(&canonical);
    if let Some(submission) = deck
        .source_metadata
        .as_object()
        .and_then(|metadata| metadata.get("submission"))
        .and_then(Value::as_object)
    {
        source
            .source_metadata
            .insert("submission".to_owned(), Value::Object(submission.clone()));
    }
    relink_canonical_user_state(transaction, content_id, canonical.id).await?;
    let identity_owned = sqlx::query_scalar::<_, bool>(
        r#"
        SELECT EXISTS(
            SELECT 1 FROM learning_decks
            WHERE user_id::bigint = $1
              AND source_identity = $2
              AND deleted_at IS NULL
              AND id::bigint <> $3
        )
        "#,
    )
    .bind(user_id)
    .bind(&source.source_identity)
    .bind(deck_id)
    .fetch_one(&mut **transaction)
    .await?;
    if identity_owned {
        source.source_identity.clone_from(&deck.source_identity);
    }
    let title = if clean_title(Some(&deck.title)).is_some() {
        deck.title
    } else {
        source.source_title.clone()
    };
    sqlx::query(
        r#"
        UPDATE learning_decks
        SET source_identity = $2,
            source_content_id = $3::bigint::integer,
            source_url = $4,
            source_title = $5,
            source_metadata = $6,
            title = $7,
            updated_at = timezone('UTC', now())
        WHERE id::bigint = $1
        "#,
    )
    .bind(deck_id)
    .bind(&source.source_identity)
    .bind(source.source_content_id)
    .bind(&source.source_url)
    .bind(&source.source_title)
    .bind(Value::Object(source.source_metadata.clone()))
    .bind(title)
    .execute(&mut **transaction)
    .await?;
    Ok(source)
}

pub(super) fn persisted_deck_source(deck: PersistedDeckSourceRow) -> LearningDeckSourceProjection {
    LearningDeckSourceProjection {
        source_kind: deck.source_kind,
        source_identity: deck.source_identity,
        source_url: deck.source_url,
        source_content_id: deck.source_content_id,
        source_title: deck.source_title.unwrap_or(deck.title),
        source_metadata: json_object(deck.source_metadata),
    }
}

pub(super) async fn resolve_canonical_content(
    transaction: &mut Transaction<'_, Postgres>,
    starting_id: i64,
) -> Result<CanonicalContentRow, LearningDeckRepositoryError> {
    let mut current = load_canonical_content(transaction, starting_id).await?;
    let mut visited = BTreeSet::new();
    for _ in 0..64 {
        if !visited.insert(current.id) {
            break;
        }
        let canonical_id = processing_value(&current.content_metadata, "canonical_content_id")
            .and_then(coerce_i64)
            .filter(|value| *value > 0 && !visited.contains(value));
        if let Some(canonical_id) = canonical_id {
            match load_canonical_content_optional(transaction, canonical_id).await? {
                Some(canonical) => {
                    current = canonical;
                    continue;
                }
                None => break,
            }
        }
        if let Some(recovered) = recover_unrecorded_duplicate(transaction, &current).await? {
            current = recovered;
        }
        break;
    }
    Ok(current)
}

pub(super) async fn load_canonical_content(
    transaction: &mut Transaction<'_, Postgres>,
    content_id: i64,
) -> Result<CanonicalContentRow, LearningDeckRepositoryError> {
    load_canonical_content_optional(transaction, content_id)
        .await?
        .ok_or(LearningDeckRepositoryError::ContentSourceMissing)
}

pub(super) async fn load_canonical_content_optional(
    transaction: &mut Transaction<'_, Postgres>,
    content_id: i64,
) -> Result<Option<CanonicalContentRow>, sqlx::Error> {
    sqlx::query_as::<_, CanonicalContentRow>(
        r#"
        SELECT id::bigint AS id, content_type, url, source_url, title, status, content_metadata
        FROM contents WHERE id::bigint = $1 FOR UPDATE
        "#,
    )
    .bind(content_id)
    .fetch_optional(&mut **transaction)
    .await
}

pub(super) async fn recover_unrecorded_duplicate(
    transaction: &mut Transaction<'_, Postgres>,
    content: &CanonicalContentRow,
) -> Result<Option<CanonicalContentRow>, LearningDeckRepositoryError> {
    if content.content_type != "unknown" {
        return Ok(None);
    }
    let latest_analysis_failed = sqlx::query_scalar::<_, String>(
        r#"
        SELECT status FROM processing_tasks
        WHERE content_id::bigint = $1 AND task_type = 'analyze_url'
        ORDER BY id DESC LIMIT 1
        "#,
    )
    .bind(content.id)
    .fetch_optional(&mut **transaction)
    .await?
    .is_some_and(|status| status == "failed");
    if !latest_analysis_failed {
        return Ok(None);
    }
    let candidates = sqlx::query_as::<_, CanonicalContentRow>(
        r#"
        SELECT id::bigint AS id, content_type, url, source_url, title, status, content_metadata
        FROM contents
        WHERE id::bigint <> $1
          AND content_type <> 'unknown'
          AND status IN ('completed', 'awaiting_image')
          AND (url = ANY($2) OR source_url = ANY($2))
        ORDER BY id
        FOR SHARE
        "#,
    )
    .bind(content.id)
    .bind(
        [
            content.url.clone(),
            content.source_url.clone().unwrap_or_default(),
        ]
        .into_iter()
        .filter(|value| !value.is_empty())
        .collect::<Vec<_>>(),
    )
    .fetch_all(&mut **transaction)
    .await?;
    if let Some(candidate) = candidates.into_iter().next() {
        return Ok(Some(candidate));
    }
    let Some(tweet_id) = extract_x_status_id(&content.url)
        .or_else(|| content.source_url.as_deref().and_then(extract_x_status_id))
    else {
        return Ok(None);
    };
    let broad = sqlx::query_as::<_, CanonicalContentRow>(
        r#"
        SELECT id::bigint AS id, content_type, url, source_url, title, status, content_metadata
        FROM contents
        WHERE id::bigint <> $1
          AND content_type <> 'unknown'
          AND status IN ('completed', 'awaiting_image')
          AND (url LIKE $2 OR source_url LIKE $2)
        ORDER BY id
        FOR SHARE
        "#,
    )
    .bind(content.id)
    .bind(format!("%{tweet_id}%"))
    .fetch_all(&mut **transaction)
    .await?;
    Ok(broad.into_iter().find(|candidate| {
        extract_x_status_id(&candidate.url).as_deref() == Some(tweet_id.as_str())
            || candidate
                .source_url
                .as_deref()
                .and_then(extract_x_status_id)
                .as_deref()
                == Some(tweet_id.as_str())
    }))
}

pub(super) async fn relink_canonical_user_state(
    transaction: &mut Transaction<'_, Postgres>,
    loser_content_id: i64,
    winner_content_id: i64,
) -> Result<(), sqlx::Error> {
    for statement in [
        r#"
        INSERT INTO content_status (user_id, content_id, status, created_at, updated_at)
        SELECT user_id, $2, status, created_at, updated_at FROM content_status WHERE content_id::bigint = $1
        ON CONFLICT (user_id, content_id) DO NOTHING
        "#,
        r#"
        INSERT INTO content_read_status (user_id, content_id, read_at, created_at)
        SELECT user_id, $2, read_at, created_at FROM content_read_status WHERE content_id::bigint = $1
        ON CONFLICT (user_id, content_id) DO UPDATE
        SET read_at = GREATEST(content_read_status.read_at, EXCLUDED.read_at)
        "#,
        r#"
        INSERT INTO content_knowledge_saves (user_id, content_id, saved_at, created_at)
        SELECT user_id, $2, saved_at, created_at FROM content_knowledge_saves WHERE content_id::bigint = $1
        ON CONFLICT (user_id, content_id) DO UPDATE
        SET saved_at = GREATEST(content_knowledge_saves.saved_at, EXCLUDED.saved_at)
        "#,
        r#"
        INSERT INTO content_unlikes (user_id, content_id, unliked_at, created_at)
        SELECT user_id, $2, unliked_at, created_at FROM content_unlikes WHERE content_id::bigint = $1
        ON CONFLICT (user_id, content_id) DO UPDATE
        SET unliked_at = GREATEST(content_unlikes.unliked_at, EXCLUDED.unliked_at)
        "#,
    ] {
        sqlx::query(statement)
            .bind(loser_content_id)
            .bind(winner_content_id)
            .execute(&mut **transaction)
            .await?;
    }
    for statement in [
        "DELETE FROM content_status WHERE content_id::bigint = $1",
        "DELETE FROM content_read_status WHERE content_id::bigint = $1",
        "DELETE FROM content_knowledge_saves WHERE content_id::bigint = $1",
        "DELETE FROM content_unlikes WHERE content_id::bigint = $1",
    ] {
        sqlx::query(statement)
            .bind(loser_content_id)
            .execute(&mut **transaction)
            .await?;
    }
    sqlx::query(
        "UPDATE chat_sessions SET content_id = $2::bigint::integer, updated_at = timezone('UTC', now()) WHERE content_id::bigint = $1",
    )
    .bind(loser_content_id)
    .bind(winner_content_id)
    .execute(&mut **transaction)
    .await?;
    Ok(())
}

pub(super) fn canonical_content_source(row: &CanonicalContentRow) -> LearningDeckSourceProjection {
    LearningDeckSourceProjection {
        source_kind: "content".to_owned(),
        source_identity: format!("content:{}", row.id),
        source_url: clean_optional(row.source_url.as_deref())
            .map(str::to_owned)
            .or_else(|| Some(row.url.clone())),
        source_content_id: Some(row.id),
        source_title: content_display_title(row.id, row.title.as_deref(), &row.content_metadata),
        source_metadata: Map::from_iter([
            (
                "content_type".to_owned(),
                Value::from(row.content_type.clone()),
            ),
            ("status".to_owned(), Value::from(row.status.clone())),
        ]),
    }
}
