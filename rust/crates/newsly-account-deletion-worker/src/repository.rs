use std::collections::BTreeSet;
use std::error::Error;
use std::path::{Component, Path, PathBuf};

use newsly_worker::{HandlerFinalizerFuture, TaskFinalizer, TaskFinalizerResult};
use serde_json::{Map, Value};
use sqlx::{FromRow, PgPool, Postgres, Transaction};
use thiserror::Error;

use crate::registry::USER_OWNED_RELATIONS;

#[derive(Debug, Clone, Copy, Eq, PartialEq)]
pub(crate) enum XTokenType {
    Refresh,
    Access,
}

impl XTokenType {
    pub(crate) const fn as_str(self) -> &'static str {
        match self {
            Self::Refresh => "refresh_token",
            Self::Access => "access_token",
        }
    }
}

#[derive(Debug, Clone, Eq, PartialEq)]
pub(crate) struct XGrant {
    pub encrypted_token: String,
    pub token_type_hint: XTokenType,
}

#[derive(Debug, Clone)]
pub(crate) struct AccountCleanupPlan {
    pub user_id: i64,
    pub x_grants: Vec<XGrant>,
    pub audio_paths: Vec<PathBuf>,
    pub object_keys: Vec<String>,
    pub media_audio_root: PathBuf,
    pub personal_markdown_root: PathBuf,
}

#[derive(Debug, FromRow)]
struct UserCleanupRow {
    is_active: bool,
}

#[derive(Debug, FromRow)]
struct IntegrationTokenRow {
    refresh_token_encrypted: Option<String>,
    access_token_encrypted: Option<String>,
}

#[derive(Debug, FromRow)]
struct DeckArtifactRow {
    artifact_object_keys: Value,
    deck_object_key: Option<String>,
    source_notes_object_key: Option<String>,
    source_notes_html_object_key: Option<String>,
    agent_log_object_key: Option<String>,
}

#[derive(Debug, FromRow)]
struct LlmArtifactRow {
    artifact_manifest: Value,
    agent_log_object_key: Option<String>,
}

/// Creates an owned, immutable external cleanup plan and commits before returning it.
#[allow(clippy::too_many_lines)]
pub(crate) async fn prepare_cleanup_plan(
    pool: &PgPool,
    user_id: i64,
    media_audio_root: &Path,
    personal_markdown_root: &Path,
) -> Result<Option<AccountCleanupPlan>, AccountRepositoryError> {
    let mut transaction = pool.begin().await?;
    let user = sqlx::query_as::<_, UserCleanupRow>(
        r"
        SELECT is_active
        FROM users
        WHERE id::bigint = $1
        FOR SHARE
        ",
    )
    .bind(user_id)
    .fetch_optional(&mut *transaction)
    .await?;
    let Some(user) = user else {
        transaction.commit().await?;
        return Ok(None);
    };
    if user.is_active {
        return Err(AccountRepositoryError::ActiveUser(user_id));
    }

    let token_rows = sqlx::query_as::<_, IntegrationTokenRow>(
        r"
        SELECT refresh_token_encrypted, access_token_encrypted
        FROM user_integration_connections
        WHERE user_id::bigint = $1
        ORDER BY id
        ",
    )
    .bind(user_id)
    .fetch_all(&mut *transaction)
    .await?;
    let x_grants = token_rows
        .into_iter()
        .filter_map(|row| {
            row.refresh_token_encrypted
                .filter(|token| !token.is_empty())
                .map(|encrypted_token| XGrant {
                    encrypted_token,
                    token_type_hint: XTokenType::Refresh,
                })
                .or_else(|| {
                    row.access_token_encrypted
                        .filter(|token| !token.is_empty())
                        .map(|encrypted_token| XGrant {
                            encrypted_token,
                            token_type_hint: XTokenType::Access,
                        })
                })
        })
        .collect();

    let raw_audio_paths = sqlx::query_scalar::<_, String>(
        r"
        SELECT audio_storage_path
        FROM audio_episodes
        WHERE user_id::bigint = $1 AND audio_storage_path IS NOT NULL
        ORDER BY id
        ",
    )
    .bind(user_id)
    .fetch_all(&mut *transaction)
    .await?;
    let mut audio_paths = BTreeSet::new();
    for raw_path in raw_audio_paths {
        if raw_path.trim().is_empty() {
            continue;
        }
        audio_paths.insert(validate_audio_path(&raw_path, media_audio_root)?);
    }

    let mut object_keys = BTreeSet::new();
    let deck_rows = sqlx::query_as::<_, DeckArtifactRow>(
        r"
        SELECT
            COALESCE(artifact_object_keys, '[]'::jsonb) AS artifact_object_keys,
            deck_object_key,
            source_notes_object_key,
            source_notes_html_object_key,
            NULL::text AS agent_log_object_key
        FROM learning_decks
        WHERE user_id::bigint = $1
        UNION ALL
        SELECT
            COALESCE(artifact_object_keys, '[]'::jsonb) AS artifact_object_keys,
            deck_object_key,
            source_notes_object_key,
            source_notes_html_object_key,
            agent_log_object_key
        FROM learning_deck_runs
        WHERE user_id::bigint = $1
        ",
    )
    .bind(user_id)
    .fetch_all(&mut *transaction)
    .await?;
    for row in deck_rows {
        collect_string_array(&row.artifact_object_keys, &mut object_keys);
        collect_optional_key(row.deck_object_key, &mut object_keys);
        collect_optional_key(row.source_notes_object_key, &mut object_keys);
        collect_optional_key(row.source_notes_html_object_key, &mut object_keys);
        collect_optional_key(row.agent_log_object_key, &mut object_keys);
    }
    let llm_rows = sqlx::query_as::<_, LlmArtifactRow>(
        r"
        SELECT COALESCE(artifact_manifest, '{}'::jsonb) AS artifact_manifest, agent_log_object_key
        FROM llm_tasks
        WHERE user_id::bigint = $1
        ORDER BY id
        ",
    )
    .bind(user_id)
    .fetch_all(&mut *transaction)
    .await?;
    for row in llm_rows {
        collect_manifest_keys(&row.artifact_manifest, &mut object_keys);
        collect_optional_key(row.agent_log_object_key, &mut object_keys);
    }

    let personal_markdown_root = user_directory(personal_markdown_root, user_id)?;
    transaction.commit().await?;
    Ok(Some(AccountCleanupPlan {
        user_id,
        x_grants,
        audio_paths: audio_paths.into_iter().collect(),
        object_keys: object_keys.into_iter().collect(),
        media_audio_root: media_audio_root.to_path_buf(),
        personal_markdown_root,
    }))
}

fn collect_manifest_keys(manifest: &Value, keys: &mut BTreeSet<String>) {
    let Some(manifest) = manifest.as_object() else {
        return;
    };
    if let Some(value) = manifest.get("artifact_object_keys") {
        collect_string_array(value, keys);
    }
    for field in [
        "deck_object_key",
        "source_notes_object_key",
        "source_notes_html_object_key",
        "thumbnail_object_key",
    ] {
        collect_optional_key(
            manifest
                .get(field)
                .and_then(Value::as_str)
                .map(str::to_owned),
            keys,
        );
    }
}

fn collect_string_array(value: &Value, keys: &mut BTreeSet<String>) {
    let Some(values) = value.as_array() else {
        return;
    };
    for value in values {
        collect_optional_key(value.as_str().map(str::to_owned), keys);
    }
}

fn collect_optional_key(value: Option<String>, keys: &mut BTreeSet<String>) {
    if let Some(value) = value.map(|value| value.trim().to_owned())
        && !value.is_empty()
    {
        keys.insert(value);
    }
}

fn validate_audio_path(
    raw_path: &str,
    media_audio_root: &Path,
) -> Result<PathBuf, AccountRepositoryError> {
    let path = PathBuf::from(raw_path);
    if !path.is_absolute()
        || path == media_audio_root
        || !path.starts_with(media_audio_root)
        || path.components().any(|component| {
            matches!(
                component,
                Component::ParentDir | Component::CurDir | Component::Prefix(_)
            )
        })
    {
        return Err(AccountRepositoryError::UnsafeAudioPath(raw_path.to_owned()));
    }
    Ok(path)
}

fn user_directory(root: &Path, user_id: i64) -> Result<PathBuf, AccountRepositoryError> {
    if user_id <= 0 || !root.is_absolute() || root == Path::new("/") {
        return Err(AccountRepositoryError::UnsafeUserRoot(root.to_path_buf()));
    }
    let path = root.join(user_id.to_string());
    if path == root || !path.starts_with(root) {
        return Err(AccountRepositoryError::UnsafeUserRoot(path));
    }
    Ok(path)
}

#[derive(Debug, Clone)]
pub(crate) struct AccountDeletionFinalizer {
    user_id: i64,
    current_task_id: i64,
}

impl AccountDeletionFinalizer {
    pub(crate) const fn new(user_id: i64, current_task_id: i64) -> Self {
        Self {
            user_id,
            current_task_id,
        }
    }

    async fn apply_inner(
        &self,
        transaction: &mut Transaction<'static, Postgres>,
    ) -> Result<(), AccountRepositoryError> {
        let active = sqlx::query_scalar::<_, bool>(
            r"
            SELECT is_active
            FROM users
            WHERE id::bigint = $1
            FOR UPDATE
            ",
        )
        .bind(self.user_id)
        .fetch_optional(&mut **transaction)
        .await?;
        if active == Some(true) {
            return Err(AccountRepositoryError::ActiveUser(self.user_id));
        }
        if active.is_some() {
            delete_indirect_rows(transaction, self.user_id).await?;
            scrub_shared_content_metadata(transaction, self.user_id).await?;
            null_shared_approver_references(transaction, self.user_id).await?;
            delete_direct_rows(transaction, self.user_id, self.current_task_id).await?;
            sqlx::query("DELETE FROM users WHERE id::bigint = $1")
                .bind(self.user_id)
                .execute(&mut **transaction)
                .await?;
        }
        // The current row is deliberately ownerless and survives account-row deletion so the
        // queue kernel can complete it. Remove its private payload and active dedupe identity in
        // the same transaction as the account purge and final queue transition.
        sqlx::query(
            r"
            UPDATE processing_tasks
            SET payload = '{}'::json, dedupe_key = NULL
            WHERE id::bigint = $1
            ",
        )
        .bind(self.current_task_id)
        .execute(&mut **transaction)
        .await?;
        Ok(())
    }
}

impl TaskFinalizer for AccountDeletionFinalizer {
    fn apply<'a>(
        &'a self,
        transaction: &'a mut Transaction<'static, Postgres>,
    ) -> HandlerFinalizerFuture<'a> {
        Box::pin(async move {
            self.apply_inner(transaction)
                .await
                .map_err(|error| Box::new(error) as Box<dyn Error + Send + Sync>)?;
            Ok(TaskFinalizerResult::Keep)
        })
    }
}

async fn delete_indirect_rows(
    transaction: &mut Transaction<'static, Postgres>,
    user_id: i64,
) -> Result<(), sqlx::Error> {
    const DELETIONS: &[&str] = &[
        "DELETE FROM chat_messages WHERE session_id IN (SELECT id FROM chat_sessions WHERE user_id::bigint = $1)",
        "DELETE FROM user_integration_sync_state WHERE connection_id IN (SELECT id FROM user_integration_connections WHERE user_id::bigint = $1)",
        "DELETE FROM user_integration_synced_items WHERE connection_id IN (SELECT id FROM user_integration_connections WHERE user_id::bigint = $1)",
        "DELETE FROM onboarding_discovery_lanes WHERE run_id IN (SELECT id FROM onboarding_discovery_runs WHERE user_id::bigint = $1)",
        "DELETE FROM onboarding_first_edition_sources WHERE run_id IN (SELECT id FROM onboarding_first_edition_runs WHERE user_id::bigint = $1)",
        "DELETE FROM llm_task_actions WHERE llm_task_id IN (SELECT id FROM llm_tasks WHERE user_id::bigint = $1)",
        "DELETE FROM news_item_discussions WHERE news_item_id IN (SELECT id FROM news_items WHERE owner_user_id::bigint = $1)",
    ];
    for statement in DELETIONS {
        sqlx::query(*statement)
            .bind(user_id)
            .execute(&mut **transaction)
            .await?;
    }
    Ok(())
}

async fn null_shared_approver_references(
    transaction: &mut Transaction<'static, Postgres>,
    user_id: i64,
) -> Result<(), sqlx::Error> {
    const UPDATES: &[&str] = &[
        "UPDATE cli_link_sessions SET approved_by_user_id = NULL WHERE approved_by_user_id::bigint = $1",
        "UPDATE llm_task_actions SET approved_by_user_id = NULL WHERE approved_by_user_id::bigint = $1",
        "UPDATE user_api_keys SET created_by_admin_user_id = NULL WHERE created_by_admin_user_id::bigint = $1",
    ];
    for statement in UPDATES {
        sqlx::query(*statement)
            .bind(user_id)
            .execute(&mut **transaction)
            .await?;
    }
    Ok(())
}

async fn delete_direct_rows(
    transaction: &mut Transaction<'static, Postgres>,
    user_id: i64,
    current_task_id: i64,
) -> Result<(), sqlx::Error> {
    for relation in USER_OWNED_RELATIONS {
        let mut query = sqlx::query(relation.delete_sql).bind(user_id);
        if relation.excludes_current_task {
            query = query.bind(current_task_id);
        }
        query.execute(&mut **transaction).await?;
    }
    Ok(())
}

#[derive(Debug, FromRow)]
struct ContentMetadataRow {
    id: i64,
    content_metadata: Value,
}

async fn scrub_shared_content_metadata(
    transaction: &mut Transaction<'static, Postgres>,
    user_id: i64,
) -> Result<(), sqlx::Error> {
    let rows = sqlx::query_as::<_, ContentMetadataRow>(
        r"
        SELECT id::bigint AS id, content_metadata
        FROM contents
        WHERE
            content_metadata::jsonb ->> 'submitted_by_user_id' = $1::text
            OR (content_metadata::jsonb -> 'share_and_chat_user_ids') @> jsonb_build_array($1::bigint)
            OR (content_metadata::jsonb -> 'share_and_chat_requests') @> jsonb_build_array(jsonb_build_object('user_id', $1::bigint))
            OR content_metadata::jsonb -> 'processing' ->> 'submitted_by_user_id' = $1::text
            OR (content_metadata::jsonb -> 'processing' -> 'share_and_chat_user_ids') @> jsonb_build_array($1::bigint)
            OR (content_metadata::jsonb -> 'processing' -> 'share_and_chat_requests') @> jsonb_build_array(jsonb_build_object('user_id', $1::bigint))
        ORDER BY id
        FOR UPDATE
        ",
    )
    .bind(user_id)
    .fetch_all(&mut **transaction)
    .await?;
    for row in rows {
        let cleaned = remove_user_references(row.content_metadata, user_id);
        sqlx::query("UPDATE contents SET content_metadata = $1 WHERE id::bigint = $2")
            .bind(cleaned)
            .bind(row.id)
            .execute(&mut **transaction)
            .await?;
    }
    Ok(())
}

fn remove_user_references(mut metadata: Value, user_id: i64) -> Value {
    let Some(root) = metadata.as_object_mut() else {
        return metadata;
    };
    scrub_metadata_mapping(root, user_id);
    if let Some(Value::Object(processing)) = root.get_mut("processing") {
        scrub_metadata_mapping(processing, user_id);
    }
    metadata
}

fn scrub_metadata_mapping(metadata: &mut Map<String, Value>, user_id: i64) {
    if metadata
        .get("submitted_by_user_id")
        .and_then(coerce_positive_integer)
        == Some(user_id)
    {
        metadata.remove("submitted_by_user_id");
    }

    match metadata.get_mut("share_and_chat_user_ids") {
        Some(Value::Array(values)) => {
            values.retain(|value| coerce_positive_integer(value) != Some(user_id));
            if values.is_empty() {
                metadata.remove("share_and_chat_user_ids");
            }
        }
        Some(value) if coerce_positive_integer(value) == Some(user_id) => {
            metadata.remove("share_and_chat_user_ids");
        }
        _ => {}
    }

    if let Some(Value::Array(requests)) = metadata.get_mut("share_and_chat_requests") {
        requests.retain(|request| {
            request
                .as_object()
                .and_then(|request| request.get("user_id"))
                .and_then(coerce_positive_integer)
                != Some(user_id)
        });
        if requests.is_empty() {
            metadata.remove("share_and_chat_requests");
        }
    }
}

fn coerce_positive_integer(value: &Value) -> Option<i64> {
    let parsed = match value {
        Value::Number(value) => value.as_i64(),
        Value::String(value) => value.parse::<i64>().ok(),
        Value::Bool(value) => Some(i64::from(*value)),
        Value::Null | Value::Array(_) | Value::Object(_) => None,
    }?;
    (parsed > 0).then_some(parsed)
}

#[derive(Debug, Error)]
pub(crate) enum AccountRepositoryError {
    #[error("account deletion cannot purge active user {0}")]
    ActiveUser(i64),
    #[error("audio artifact path is outside MEDIA_BASE_DIR/audio_episodes: {0}")]
    UnsafeAudioPath(String),
    #[error("unsafe account-owned filesystem root: {0}")]
    UnsafeUserRoot(PathBuf),
    #[error("account deletion database operation failed")]
    Database(#[from] sqlx::Error),
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::remove_user_references;

    #[test]
    fn shared_metadata_scrub_preserves_other_users() {
        let metadata = json!({
            "submitted_by_user_id": 7,
            "share_and_chat_user_ids": [7, 8],
            "share_and_chat_requests": [
                {"user_id": 7, "initial_message": "private"},
                {"user_id": 8, "initial_message": "keep"}
            ],
            "processing": {
                "submitted_by_user_id": "7",
                "share_and_chat_user_ids": [7, 8],
                "share_and_chat_requests": [{"user_id": 7}, {"user_id": 8}]
            }
        });
        assert_eq!(
            remove_user_references(metadata, 7),
            json!({
                "share_and_chat_user_ids": [8],
                "share_and_chat_requests": [{"user_id": 8, "initial_message": "keep"}],
                "processing": {
                    "share_and_chat_user_ids": [8],
                    "share_and_chat_requests": [{"user_id": 8}]
                }
            })
        );
    }
}
