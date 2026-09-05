use chrono::{DateTime, NaiveDateTime, Utc};
use serde_json::{Map, Value, json};
use sqlx::types::Json;
use sqlx::{FromRow, Postgres, Transaction};
use thiserror::Error;

const SYNC_INTERVAL_GRACE_SECONDS: i64 = 5;
const BOOKMARKS_CHANNEL: &str = "bookmarks";

#[derive(Debug, Clone, PartialEq)]
pub struct PreparedXSync {
    pub user_id: i64,
    pub connection_id: i64,
    pub provider_user_id: Option<String>,
    pub provider_username: Option<String>,
    pub access_token_encrypted: Option<String>,
    pub refresh_token_encrypted: Option<String>,
    pub token_expires_at: Option<DateTime<Utc>>,
    pub scopes: Vec<String>,
    pub expected_access_token_encrypted: Option<String>,
    pub expected_refresh_token_encrypted: Option<String>,
    pub last_synced_item_id: Option<String>,
    pub bookmark_last_synced_at: Option<DateTime<Utc>>,
    pub skip_bookmarks: bool,
    pub bookmark_cursor: Option<String>,
    pub bookmark_pending_newest: Option<String>,
}

#[derive(Debug, Clone, PartialEq)]
pub enum PrepareXSyncOutcome {
    Prepared(Box<PreparedXSync>),
    UserMissing,
    NotConnected,
    SkippedRecently,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct XSyncConnectionUpdate<'a> {
    pub provider_user_id: Option<&'a str>,
    pub provider_username: Option<&'a str>,
    pub access_token_encrypted: &'a str,
    pub refresh_token_encrypted: Option<&'a str>,
    pub token_expires_at: Option<DateTime<Utc>>,
    pub scopes: &'a [String],
}

#[derive(Debug, Clone, PartialEq)]
pub struct NewXSyncUsage<'a> {
    pub model: &'a str,
    pub feature: &'a str,
    pub operation: &'a str,
    pub request_id: &'a str,
    pub task_id: i64,
    pub user_id: i64,
    pub request_count: i32,
    pub resource_count: i32,
    pub resource_ids: &'a [String],
    pub unit_cost_usd: Option<f64>,
    pub channel: Option<&'a str>,
}

#[derive(Debug, FromRow)]
struct ConnectionRow {
    connection_id: i64,
    provider_user_id: Option<String>,
    provider_username: Option<String>,
    access_token_encrypted: Option<String>,
    refresh_token_encrypted: Option<String>,
    token_expires_at: Option<NaiveDateTime>,
    scopes: Option<Json<Value>>,
}

#[derive(Debug, FromRow)]
struct SyncStateRow {
    last_synced_at: Option<NaiveDateTime>,
    last_synced_item_id: Option<String>,
    sync_metadata: Option<Json<Value>>,
}

#[derive(Debug, FromRow)]
struct ImageCandidateRow {
    content_type: String,
    status: String,
    classification: Option<String>,
    content_metadata: Value,
    has_active_image_task: bool,
}

/// Loads a connection-free X synchronization plan in one short transaction.
///
/// This function may create the integration's empty sync-state row, but it never decrypts a token
/// or performs provider I/O. The caller must commit or roll back before starting HTTP work.
///
/// # Errors
///
/// Returns an error when PostgreSQL cannot read or initialize the connection's sync state.
#[allow(clippy::too_many_lines)]
pub async fn prepare_x_sync(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
    force: bool,
    now: DateTime<Utc>,
    sync_min_interval_minutes: i64,
    bookmark_min_interval_minutes: i64,
) -> Result<PrepareXSyncOutcome, XSyncRepositoryError> {
    let user_exists = sqlx::query_scalar::<_, bool>(
        "SELECT EXISTS(SELECT 1 FROM users WHERE id::bigint = $1::bigint)",
    )
    .bind(user_id)
    .fetch_one(&mut **transaction)
    .await?;
    if !user_exists {
        return Ok(PrepareXSyncOutcome::UserMissing);
    }

    let connection = sqlx::query_as::<_, ConnectionRow>(
        r#"
        SELECT
            id::bigint AS connection_id,
            provider_user_id,
            provider_username,
            access_token_encrypted,
            refresh_token_encrypted,
            token_expires_at,
            scopes
        FROM user_integration_connections
        WHERE user_id::bigint = $1::bigint
          AND provider = 'x'
          AND is_active IS TRUE
        FOR SHARE
        "#,
    )
    .bind(user_id)
    .fetch_optional(&mut **transaction)
    .await?;
    let Some(connection) = connection else {
        return Ok(PrepareXSyncOutcome::NotConnected);
    };
    let access_token_encrypted = clean_optional(connection.access_token_encrypted);

    sqlx::query(
        r#"
        INSERT INTO user_integration_sync_state (
            connection_id,
            last_status,
            sync_metadata,
            created_at,
            updated_at
        )
        VALUES (
            $1::bigint::integer,
            'never_synced',
            '{}'::json,
            timezone('UTC', now()),
            timezone('UTC', now())
        )
        ON CONFLICT (connection_id) DO NOTHING
        "#,
    )
    .bind(connection.connection_id)
    .execute(&mut **transaction)
    .await?;
    let sync_state = sqlx::query_as::<_, SyncStateRow>(
        r#"
        SELECT last_synced_at, last_synced_item_id, sync_metadata
        FROM user_integration_sync_state
        WHERE connection_id::bigint = $1::bigint
        "#,
    )
    .bind(connection.connection_id)
    .fetch_one(&mut **transaction)
    .await?;

    let sync_metadata = sync_state
        .sync_metadata
        .map_or_else(|| json!({}), |metadata| metadata.0);
    let bookmark_state = sync_metadata
        .get(BOOKMARKS_CHANNEL)
        .and_then(Value::as_object);
    let bookmark_in_progress = bookmark_state
        .and_then(|state| state.get("in_progress"))
        .and_then(Value::as_bool)
        .unwrap_or(false);
    let bookmark_cursor = bookmark_state
        .and_then(|state| state.get("continuation"))
        .and_then(Value::as_str)
        .map(ToOwned::to_owned);
    let bookmark_pending_newest = bookmark_state
        .and_then(|state| state.get("pending_newest_item_id"))
        .and_then(Value::as_str)
        .map(ToOwned::to_owned);
    if !force
        && !bookmark_in_progress
        && sync_state.last_synced_at.is_some_and(|last_synced_at| {
            within_interval(last_synced_at.and_utc(), now, sync_min_interval_minutes)
        })
    {
        return Ok(PrepareXSyncOutcome::SkippedRecently);
    }

    let bookmark_last_synced_at = bookmark_state
        .and_then(|state| state.get("last_synced_at"))
        .and_then(Value::as_str)
        .and_then(parse_utc_datetime);
    let last_synced_item_id = bookmark_state
        .and_then(|state| state.get("last_synced_item_id"))
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(ToOwned::to_owned)
        .or_else(|| clean_optional(sync_state.last_synced_item_id));
    let skip_bookmarks = !bookmark_in_progress
        && bookmark_last_synced_at.is_some_and(|last_synced_at| {
            within_interval(last_synced_at, now, bookmark_min_interval_minutes)
        });
    let refresh_token_encrypted = clean_optional(connection.refresh_token_encrypted);
    Ok(PrepareXSyncOutcome::Prepared(Box::new(PreparedXSync {
        user_id,
        connection_id: connection.connection_id,
        provider_user_id: clean_optional(connection.provider_user_id),
        provider_username: clean_optional(connection.provider_username),
        access_token_encrypted: access_token_encrypted.clone(),
        refresh_token_encrypted: refresh_token_encrypted.clone(),
        token_expires_at: connection.token_expires_at.map(|value| value.and_utc()),
        scopes: json_strings(connection.scopes.map(|scopes| scopes.0)),
        expected_access_token_encrypted: access_token_encrypted,
        expected_refresh_token_encrypted: refresh_token_encrypted,
        last_synced_item_id,
        bookmark_last_synced_at,
        skip_bookmarks,
        bookmark_cursor,
        bookmark_pending_newest,
    })))
}

/// Locks the exact still-active connection generation used by an external synchronization plan.
///
/// # Errors
///
/// Returns an error when PostgreSQL cannot lock the active user or integration connection.
pub async fn lock_current_x_sync_connection(
    transaction: &mut Transaction<'_, Postgres>,
    plan: &PreparedXSync,
) -> Result<bool, XSyncRepositoryError> {
    // Match the account-deletion lock order: user first, then integration connection. If the
    // account was deactivated while provider I/O ran, the obsolete output is discarded.
    let active_user = sqlx::query_scalar::<_, i64>(
        r#"
        SELECT id::bigint
        FROM users
        WHERE id::bigint = $1::bigint
          AND is_active IS TRUE
        FOR SHARE
        "#,
    )
    .bind(plan.user_id)
    .fetch_optional(&mut **transaction)
    .await?;
    if active_user.is_none() {
        return Ok(false);
    }
    let locked = sqlx::query_scalar::<_, i64>(
        r#"
        SELECT id::bigint
        FROM user_integration_connections
        WHERE id::bigint = $1::bigint
          AND user_id::bigint = $2::bigint
          AND provider = 'x'
          AND is_active IS TRUE
          AND access_token_encrypted IS NOT DISTINCT FROM $3
          AND refresh_token_encrypted IS NOT DISTINCT FROM $4
        FOR UPDATE
        "#,
    )
    .bind(plan.connection_id)
    .bind(plan.user_id)
    .bind(&plan.expected_access_token_encrypted)
    .bind(&plan.expected_refresh_token_encrypted)
    .fetch_optional(&mut **transaction)
    .await?;
    Ok(locked.is_some())
}

/// Persists refreshed credentials and provider identity for a fenced X sync plan.
///
/// # Errors
///
/// Returns an error when PostgreSQL cannot update the connection or user profile.
pub async fn persist_x_sync_connection_update(
    transaction: &mut Transaction<'_, Postgres>,
    plan: &PreparedXSync,
    update: &XSyncConnectionUpdate<'_>,
) -> Result<(), XSyncRepositoryError> {
    let scopes = Json(update.scopes);
    sqlx::query(
        r#"
        UPDATE user_integration_connections
        SET provider_user_id = COALESCE($2, provider_user_id),
            provider_username = COALESCE($3, provider_username),
            access_token_encrypted = $4,
            refresh_token_encrypted = $5,
            token_expires_at = $6,
            scopes = CASE WHEN json_array_length($7::json) > 0 THEN $7 ELSE scopes END,
            updated_at = timezone('UTC', now())
        WHERE id::bigint = $1::bigint
        "#,
    )
    .bind(plan.connection_id)
    .bind(update.provider_user_id)
    .bind(update.provider_username)
    .bind(update.access_token_encrypted)
    .bind(update.refresh_token_encrypted)
    .bind(update.token_expires_at.map(|value| value.naive_utc()))
    .bind(scopes)
    .execute(&mut **transaction)
    .await?;
    if let Some(username) = update.provider_username {
        sqlx::query(
            r#"
            UPDATE users
            SET twitter_username = COALESCE(NULLIF(twitter_username, ''), $2),
                updated_at = timezone('UTC', now())
            WHERE id::bigint = $1::bigint
            "#,
        )
        .bind(plan.user_id)
        .bind(username.to_ascii_lowercase())
        .execute(&mut **transaction)
        .await?;
    }
    Ok(())
}

/// Deactivates a connection whose OAuth refresh failed permanently.
///
/// # Errors
///
/// Returns an error when PostgreSQL cannot clear credentials or update sync status.
pub async fn mark_x_sync_reauth_required(
    transaction: &mut Transaction<'_, Postgres>,
    plan: &PreparedXSync,
    reason: &str,
    recorded_at: DateTime<Utc>,
) -> Result<(), XSyncRepositoryError> {
    sqlx::query(
        r#"
        UPDATE user_integration_connections
        SET is_active = FALSE,
            access_token_encrypted = NULL,
            refresh_token_encrypted = NULL,
            token_expires_at = NULL,
            connection_metadata = (
                COALESCE(connection_metadata::jsonb, '{}'::jsonb)
                || jsonb_build_object(
                    'reauth_required',
                    jsonb_build_object('reason', $2, 'recorded_at', $3)
                )
            )::json,
            updated_at = timezone('UTC', now())
        WHERE id::bigint = $1::bigint
        "#,
    )
    .bind(plan.connection_id)
    .bind(truncate(reason, 1_000))
    .bind(recorded_at.to_rfc3339())
    .execute(&mut **transaction)
    .await?;
    upsert_sync_status(
        transaction,
        plan.connection_id,
        "reauth_required",
        Some("X connection requires reauthentication after token refresh failed"),
    )
    .await
}

/// Records a retryable or terminal synchronization failure without consuming its cooldown.
///
/// # Errors
///
/// Returns an error when PostgreSQL cannot update sync status.
pub async fn mark_x_sync_failed(
    transaction: &mut Transaction<'_, Postgres>,
    connection_id: i64,
    error: &str,
) -> Result<(), XSyncRepositoryError> {
    upsert_sync_status(
        transaction,
        connection_id,
        "failed",
        Some(truncate(error, 2_000)),
    )
    .await
}

/// Publishes the completed bookmark checkpoint and channel summary.
///
/// # Errors
///
/// Returns an error when PostgreSQL cannot upsert the sync state.
pub async fn complete_x_sync(
    transaction: &mut Transaction<'_, Postgres>,
    connection_id: i64,
    status: &str,
    newest_item_id: Option<&str>,
    sync_metadata: &Value,
    completed_at: DateTime<Utc>,
) -> Result<(), XSyncRepositoryError> {
    sqlx::query(
        r#"
        INSERT INTO user_integration_sync_state (
            connection_id,
            cursor,
            last_synced_item_id,
            last_synced_at,
            last_status,
            last_error,
            sync_metadata,
            created_at,
            updated_at
        )
        VALUES (
            $1::bigint::integer,
            NULL,
            $3,
            $5,
            $2,
            NULL,
            $4,
            timezone('UTC', now()),
            timezone('UTC', now())
        )
        ON CONFLICT (connection_id) DO UPDATE
        SET cursor = NULL,
            last_synced_item_id = COALESCE(EXCLUDED.last_synced_item_id, user_integration_sync_state.last_synced_item_id),
            last_synced_at = EXCLUDED.last_synced_at,
            last_status = EXCLUDED.last_status,
            last_error = NULL,
            sync_metadata = EXCLUDED.sync_metadata,
            updated_at = timezone('UTC', now())
        "#,
    )
    .bind(connection_id)
    .bind(status)
    .bind(newest_item_id)
    .bind(sync_metadata)
    .bind(completed_at.naive_utc())
    .execute(&mut **transaction)
    .await?;
    Ok(())
}

/// Resolves a still-existing content row from the per-connection bookmark ledger.
///
/// # Errors
///
/// Returns an error when PostgreSQL cannot read or validate the ledger row.
pub async fn find_reusable_x_bookmark_content(
    transaction: &mut Transaction<'_, Postgres>,
    connection_id: i64,
    external_item_id: &str,
) -> Result<Option<i64>, XSyncRepositoryError> {
    let content_id = sqlx::query_scalar::<_, Option<i64>>(
        r#"
        SELECT content_id::bigint
        FROM user_integration_synced_items
        WHERE connection_id::bigint = $1::bigint
          AND channel = 'bookmarks'
          AND external_item_id = $2
        "#,
    )
    .bind(connection_id)
    .bind(external_item_id)
    .fetch_optional(&mut **transaction)
    .await?
    .flatten();
    let Some(content_id) = content_id else {
        return Ok(None);
    };
    let exists = sqlx::query_scalar::<_, bool>(
        "SELECT EXISTS(SELECT 1 FROM contents WHERE id::bigint = $1::bigint)",
    )
    .bind(content_id)
    .fetch_one(&mut **transaction)
    .await?;
    Ok(exists.then_some(content_id))
}

/// Follows canonical-content links to the durable Knowledge destination.
///
/// # Errors
///
/// Returns an error when PostgreSQL cannot read a content row in the chain.
pub async fn resolve_x_bookmark_destination(
    transaction: &mut Transaction<'_, Postgres>,
    content_id: i64,
) -> Result<Option<i64>, XSyncRepositoryError> {
    let mut current = content_id;
    let mut last_existing = None;
    let mut visited = Vec::with_capacity(4);
    for _ in 0..32 {
        if visited.contains(&current) {
            return Ok(Some(current));
        }
        visited.push(current);
        let row = sqlx::query_as::<_, (i64, Value)>(
            r#"
            SELECT id::bigint, COALESCE(content_metadata, '{}'::json)
            FROM contents
            WHERE id::bigint = $1::bigint
            FOR SHARE
            "#,
        )
        .bind(current)
        .fetch_optional(&mut **transaction)
        .await?;
        let Some((durable_id, metadata)) = row else {
            return Ok(last_existing);
        };
        last_existing = Some(durable_id);
        let canonical_id = metadata
            .get("processing")
            .and_then(|processing| processing.get("canonical_content_id"))
            .or_else(|| metadata.get("canonical_content_id"))
            .and_then(json_positive_i64);
        let Some(canonical_id) = canonical_id else {
            return Ok(Some(durable_id));
        };
        if visited.contains(&canonical_id) {
            return Ok(Some(durable_id));
        }
        current = canonical_id;
    }
    Ok(last_existing)
}

/// Merges an X bookmark snapshot and provenance into content metadata.
///
/// # Errors
///
/// Returns an error when PostgreSQL cannot update the content row.
pub async fn persist_x_bookmark_snapshot(
    transaction: &mut Transaction<'_, Postgres>,
    content_id: i64,
    snapshot_updates: &Value,
) -> Result<(), XSyncRepositoryError> {
    sqlx::query(
        r#"
        UPDATE contents
        SET content_metadata = (
                COALESCE(content_metadata::jsonb, '{}'::jsonb) || $2::jsonb
            )::json,
            updated_at = timezone('UTC', now())
        WHERE id::bigint = $1::bigint
        "#,
    )
    .bind(content_id)
    .bind(snapshot_updates)
    .execute(&mut **transaction)
    .await?;
    Ok(())
}

/// Upserts a per-connection bookmark-to-content ledger entry.
///
/// # Errors
///
/// Returns an error when PostgreSQL cannot persist the ledger row.
pub async fn upsert_x_bookmark_ledger(
    transaction: &mut Transaction<'_, Postgres>,
    connection_id: i64,
    external_item_id: &str,
    content_id: i64,
    item_url: &str,
    seen_at: DateTime<Utc>,
) -> Result<(), XSyncRepositoryError> {
    sqlx::query(
        r#"
        INSERT INTO user_integration_synced_items (
            connection_id,
            channel,
            external_item_id,
            content_id,
            item_url,
            first_synced_at,
            last_seen_at,
            created_at,
            updated_at
        )
        VALUES (
            $1::bigint::integer,
            'bookmarks',
            $2,
            $3::bigint::integer,
            $4,
            $5,
            $5,
            timezone('UTC', now()),
            timezone('UTC', now())
        )
        ON CONFLICT (connection_id, channel, external_item_id) DO UPDATE
        SET content_id = EXCLUDED.content_id,
            item_url = EXCLUDED.item_url,
            last_seen_at = EXCLUDED.last_seen_at,
            updated_at = timezone('UTC', now())
        "#,
    )
    .bind(connection_id)
    .bind(external_item_id)
    .bind(content_id)
    .bind(item_url)
    .bind(seen_at.naive_utc())
    .execute(&mut **transaction)
    .await?;
    Ok(())
}

/// Removes a Knowledge save that still points at a superseded bookmark shell.
///
/// # Errors
///
/// Returns an error when PostgreSQL cannot delete the stale save.
pub async fn remove_stale_x_bookmark_save(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
    shell_content_id: i64,
    destination_content_id: i64,
) -> Result<bool, XSyncRepositoryError> {
    if shell_content_id == destination_content_id {
        return Ok(false);
    }
    let removed = sqlx::query(
        "DELETE FROM content_knowledge_saves WHERE user_id::bigint = $1::bigint AND content_id::bigint = $2::bigint",
    )
    .bind(user_id)
    .bind(shell_content_id)
    .execute(&mut **transaction)
    .await?
    .rows_affected()
        > 0;
    Ok(removed)
}

/// Idempotently saves the canonical bookmark destination to the user's Knowledge library.
///
/// # Errors
///
/// Returns an error when PostgreSQL cannot insert the Knowledge save.
pub async fn save_x_bookmark_destination(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
    content_id: i64,
) -> Result<bool, XSyncRepositoryError> {
    let inserted = sqlx::query(
        r#"
        INSERT INTO content_knowledge_saves (user_id, content_id, saved_at, created_at)
        VALUES (
            $1::bigint::integer,
            $2::bigint::integer,
            timezone('UTC', now()),
            timezone('UTC', now())
        )
        ON CONFLICT (user_id, content_id) DO NOTHING
        "#,
    )
    .bind(user_id)
    .bind(content_id)
    .execute(&mut **transaction)
    .await?
    .rows_affected()
        > 0;
    Ok(inserted)
}

/// Returns whether a visible canonical destination needs a generated long-form image.
///
/// # Errors
///
/// Returns an error when PostgreSQL cannot inspect the content and active image tasks.
pub async fn x_bookmark_destination_needs_image(
    transaction: &mut Transaction<'_, Postgres>,
    content_id: i64,
) -> Result<bool, XSyncRepositoryError> {
    let candidate = sqlx::query_as::<_, ImageCandidateRow>(
        r#"
        SELECT
            content_type,
            status,
            classification,
            COALESCE(content_metadata, '{}'::json) AS content_metadata,
            EXISTS (
                SELECT 1
                FROM processing_tasks
                WHERE content_id::bigint = $1::bigint
                  AND task_type = 'generate_image'
                  AND status IN ('pending', 'processing')
            ) AS has_active_image_task
        FROM contents
        WHERE id::bigint = $1::bigint
        "#,
    )
    .bind(content_id)
    .fetch_optional(&mut **transaction)
    .await?;
    let Some(candidate) = candidate else {
        return Ok(false);
    };
    if !matches!(candidate.content_type.as_str(), "article" | "podcast")
        || !matches!(candidate.status.as_str(), "awaiting_image" | "completed")
        || candidate.classification.as_deref() == Some("skip")
        || candidate.has_active_image_task
    {
        return Ok(false);
    }
    let runtime = runtime_metadata(&candidate.content_metadata);
    Ok(!runtime.get("image_generated_at").is_some_and(json_truthy)
        && summary_is_readable(&runtime, &candidate.content_type))
}

/// Persists one X request with UTC-day resource billing deduplication.
///
/// # Errors
///
/// Returns an error when PostgreSQL cannot inspect prior resources or persist the usage row.
pub async fn record_x_sync_usage(
    transaction: &mut Transaction<'_, Postgres>,
    usage: &NewXSyncUsage<'_>,
) -> Result<bool, XSyncRepositoryError> {
    let active = sqlx::query_scalar::<_, bool>(
        "SELECT EXISTS(SELECT 1 FROM users WHERE id::bigint = $1::bigint AND is_active IS TRUE)",
    )
    .bind(usage.user_id)
    .fetch_one(&mut **transaction)
    .await?;
    if !active {
        return Ok(false);
    }
    let mut resource_ids = usage.resource_ids.to_vec();
    resource_ids.sort();
    resource_ids.dedup();
    let billable_resource_count = if resource_ids.is_empty() {
        0_i32
    } else {
        let count = sqlx::query_scalar::<_, i64>(
            r#"
            SELECT count(*)::bigint
            FROM unnest($2::text[]) AS candidate(resource_id)
            WHERE NOT EXISTS (
                SELECT 1
                FROM vendor_usage_records AS prior
                CROSS JOIN LATERAL jsonb_array_elements_text(
                    CASE
                        WHEN jsonb_typeof(COALESCE(prior.metadata::jsonb, '{}'::jsonb) -> 'resource_ids') = 'array'
                        THEN COALESCE(prior.metadata::jsonb, '{}'::jsonb) -> 'resource_ids'
                        ELSE '[]'::jsonb
                    END
                ) AS prior_resource(resource_id)
                WHERE prior.provider = 'x'
                  AND prior.model = $1
                  AND prior.created_at >= date_trunc('day', timezone('UTC', now()))
                  AND prior.created_at < date_trunc('day', timezone('UTC', now())) + interval '1 day'
                  AND prior_resource.resource_id = candidate.resource_id
            )
            "#,
        )
        .bind(usage.model)
        .bind(&resource_ids)
        .fetch_one(&mut **transaction)
        .await?;
        i32::try_from(count).unwrap_or(i32::MAX)
    };
    let mut metadata = Map::from_iter([
        ("resource_ids".to_owned(), Value::from(resource_ids)),
        (
            "billable_resource_count".to_owned(),
            Value::from(billable_resource_count),
        ),
        (
            "billing_deduplication_window".to_owned(),
            Value::from("utc_day"),
        ),
    ]);
    if let Some(channel) = usage.channel {
        metadata.insert("channel".to_owned(), Value::from(channel));
    }
    let cost_usd = usage
        .unit_cost_usd
        .map(|unit_cost| unit_cost * f64::from(billable_resource_count));
    sqlx::query(
        r#"
        INSERT INTO vendor_usage_records (
            provider,
            model,
            feature,
            operation,
            source,
            request_id,
            task_id,
            user_id,
            request_count,
            resource_count,
            cost_usd,
            currency,
            pricing_version,
            metadata,
            created_at
        )
        VALUES (
            'x', $1, $2, $3, 'rust_worker', $4, $5::bigint::integer, $6::bigint::integer,
            $7, $8, $9, 'USD', '2026-08-02', $10, timezone('UTC', clock_timestamp())
        )
        "#,
    )
    .bind(usage.model)
    .bind(usage.feature)
    .bind(usage.operation)
    .bind(usage.request_id)
    .bind(usage.task_id)
    .bind(usage.user_id)
    .bind(usage.request_count)
    .bind(usage.resource_count)
    .bind(cost_usd)
    .bind(Value::Object(metadata))
    .execute(&mut **transaction)
    .await?;
    Ok(true)
}

async fn upsert_sync_status(
    transaction: &mut Transaction<'_, Postgres>,
    connection_id: i64,
    status: &str,
    error: Option<&str>,
) -> Result<(), XSyncRepositoryError> {
    sqlx::query(
        r#"
        INSERT INTO user_integration_sync_state (
            connection_id,
            last_status,
            last_error,
            sync_metadata,
            created_at,
            updated_at
        )
        VALUES (
            $1::bigint::integer,
            $2,
            $3,
            '{}'::json,
            timezone('UTC', now()),
            timezone('UTC', now())
        )
        ON CONFLICT (connection_id) DO UPDATE
        SET last_status = EXCLUDED.last_status,
            last_error = EXCLUDED.last_error,
            updated_at = timezone('UTC', now())
        "#,
    )
    .bind(connection_id)
    .bind(status)
    .bind(error)
    .execute(&mut **transaction)
    .await?;
    Ok(())
}

fn within_interval(last: DateTime<Utc>, now: DateTime<Utc>, minutes: i64) -> bool {
    let elapsed = now.signed_duration_since(last).num_seconds();
    elapsed.saturating_add(SYNC_INTERVAL_GRACE_SECONDS) < minutes.saturating_mul(60)
}

fn parse_utc_datetime(value: &str) -> Option<DateTime<Utc>> {
    DateTime::parse_from_rfc3339(value)
        .map(|value| value.with_timezone(&Utc))
        .ok()
        .or_else(|| {
            NaiveDateTime::parse_from_str(value, "%Y-%m-%dT%H:%M:%S%.f")
                .map(|value| value.and_utc())
                .ok()
        })
}

fn json_strings(value: Option<Value>) -> Vec<String> {
    value
        .and_then(|value| value.as_array().cloned())
        .unwrap_or_default()
        .into_iter()
        .filter_map(|value| value.as_str().map(str::trim).map(ToOwned::to_owned))
        .filter(|value| !value.is_empty())
        .collect()
}

fn clean_optional(value: Option<String>) -> Option<String> {
    value
        .map(|value| value.trim().to_owned())
        .filter(|value| !value.is_empty())
}

fn json_positive_i64(value: &Value) -> Option<i64> {
    value
        .as_i64()
        .or_else(|| value.as_str().and_then(|value| value.parse().ok()))
        .filter(|value| *value > 0)
}

fn runtime_metadata(value: &Value) -> serde_json::Map<String, Value> {
    let mut runtime = value.as_object().cloned().unwrap_or_default();
    for namespace in ["domain", "processing"] {
        if let Some(fields) = runtime.get(namespace).and_then(Value::as_object).cloned() {
            for (key, value) in fields {
                runtime.insert(key, value);
            }
        }
    }
    runtime
}

fn json_truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(value) => *value,
        Value::Number(value) => value.as_f64().is_some_and(|value| value != 0.0),
        Value::String(value) => !value.is_empty(),
        Value::Array(value) => !value.is_empty(),
        Value::Object(value) => !value.is_empty(),
    }
}

fn summary_is_readable(runtime: &serde_json::Map<String, Value>, content_type: &str) -> bool {
    let Some(summary) = runtime.get("summary") else {
        return false;
    };
    if content_type == "podcast" {
        return json_truthy(summary);
    }
    let Some(summary) = summary.as_object() else {
        return json_truthy(summary);
    };
    if runtime.get("summary_kind").and_then(Value::as_str) == Some("longform_artifact")
        && summary.get("artifact").is_some_and(Value::is_object)
    {
        return summary.get("feed_preview").is_some_and(Value::is_object)
            || runtime.get("feed_preview").is_some_and(Value::is_object)
            || summary.get("one_line").is_some_and(json_truthy);
    }
    [
        "one_line",
        "overview",
        "summary",
        "hook",
        "takeaway",
        "editorial_narrative",
        "bullet_points",
        "key_points",
        "points",
        "insights",
        "artifact",
    ]
    .iter()
    .any(|key| summary.get(*key).is_some_and(json_truthy))
}

fn truncate(value: &str, max_chars: usize) -> &str {
    value
        .char_indices()
        .nth(max_chars)
        .map_or(value, |(index, _)| &value[..index])
}

#[derive(Debug, Error)]
pub enum XSyncRepositoryError {
    #[error("X synchronization database operation failed")]
    Sqlx(#[from] sqlx::Error),
}
