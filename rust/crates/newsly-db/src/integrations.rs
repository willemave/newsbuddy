use chrono::{DateTime, NaiveDateTime, Utc};
use serde_json::Value;
use sqlx::types::Json;
use sqlx::{FromRow, PgPool, Postgres, Transaction};
use thiserror::Error;

#[derive(Debug, FromRow)]
struct UserLlmIntegrationRow {
    provider: String,
    configured: bool,
    updated_at: Option<NaiveDateTime>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct UserLlmIntegrationProjection {
    pub provider: String,
    pub configured: bool,
    pub updated_at: Option<DateTime<Utc>>,
}

#[derive(Debug, FromRow)]
struct XConnectionRow {
    connection_id: Option<i64>,
    is_active: Option<bool>,
    token_configured: Option<bool>,
    provider_user_id: Option<String>,
    provider_username: Option<String>,
    scopes: Option<Json<Value>>,
    last_synced_at: Option<NaiveDateTime>,
    last_status: Option<String>,
    last_error: Option<String>,
    twitter_username: Option<String>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct XConnectionProjection {
    pub connection_id: Option<i64>,
    pub connected: bool,
    pub is_active: bool,
    pub provider_user_id: Option<String>,
    pub provider_username: Option<String>,
    pub scopes: Vec<String>,
    pub last_synced_at: Option<DateTime<Utc>>,
    pub last_status: Option<String>,
    pub last_error: Option<String>,
    pub twitter_username: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PreparedXOAuthExchange {
    pub connection_id: i64,
    pub code_verifier: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum PrepareXOAuthExchangeOutcome {
    Prepared(PreparedXOAuthExchange),
    NotInitialized,
    MissingPendingState,
    InvalidPendingState,
    StateMismatch,
    Expired,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct XDisconnectPlan {
    pub connection_id: i64,
    pub encrypted_token: Option<String>,
    pub token_type_hint: &'static str,
}

pub async fn find_x_connection(
    pool: &PgPool,
    user_id: i64,
) -> Result<Option<XConnectionProjection>, IntegrationRepositoryError> {
    let row = sqlx::query_as::<_, XConnectionRow>(
        r#"
        SELECT
            connection.id::bigint AS connection_id,
            connection.is_active,
            connection.access_token_encrypted IS NOT NULL
                AND connection.access_token_encrypted <> '' AS token_configured,
            connection.provider_user_id,
            connection.provider_username,
            connection.scopes,
            sync_state.last_synced_at,
            sync_state.last_status,
            sync_state.last_error,
            users.twitter_username
        FROM users
        LEFT JOIN user_integration_connections AS connection
          ON connection.user_id = users.id
         AND connection.provider = 'x'
        LEFT JOIN user_integration_sync_state AS sync_state
          ON sync_state.connection_id = connection.id
        WHERE users.id::bigint = $1::bigint
        "#,
    )
    .bind(user_id)
    .fetch_optional(pool)
    .await?;
    Ok(row.map(project_x_connection))
}

pub async fn store_x_oauth_pending(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
    twitter_username: Option<&str>,
    state: &str,
    code_verifier: &str,
    created_at: DateTime<Utc>,
    scopes: &[String],
) -> Result<bool, IntegrationRepositoryError> {
    if let Some(username) = twitter_username {
        sqlx::query(
            r#"
            UPDATE users
            SET twitter_username = $2,
                updated_at = timezone('UTC', now())
            WHERE id::bigint = $1::bigint
            "#,
        )
        .bind(user_id)
        .bind(username)
        .execute(&mut **transaction)
        .await?;
    }

    let scopes = Json(scopes);
    let result = sqlx::query(
        r#"
        INSERT INTO user_integration_connections (
            user_id,
            provider,
            scopes,
            is_active,
            connection_metadata,
            created_at,
            updated_at
        )
        VALUES (
            $1::bigint::integer,
            'x',
            $5,
            FALSE,
            json_build_object(
                'oauth_pending',
                json_build_object('state', $2, 'code_verifier', $3, 'created_at', $4)
            ),
            timezone('UTC', now()),
            timezone('UTC', now())
        )
        ON CONFLICT (user_id, provider) DO UPDATE
        SET scopes = EXCLUDED.scopes,
            connection_metadata = (
                COALESCE(user_integration_connections.connection_metadata::jsonb, '{}'::jsonb)
                || jsonb_build_object(
                    'oauth_pending',
                    jsonb_build_object('state', $2, 'code_verifier', $3, 'created_at', $4)
                )
            )::json,
            updated_at = timezone('UTC', now())
        "#,
    )
    .bind(user_id)
    .bind(state)
    .bind(code_verifier)
    .bind(created_at.to_rfc3339())
    .bind(scopes)
    .execute(&mut **transaction)
    .await?;
    Ok(result.rows_affected() > 0)
}

pub async fn prepare_x_oauth_exchange(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
    supplied_state: &str,
    now: DateTime<Utc>,
) -> Result<PrepareXOAuthExchangeOutcome, IntegrationRepositoryError> {
    let row = sqlx::query_as::<_, (i64, Option<Json<Value>>)>(
        r#"
        SELECT id::bigint, connection_metadata
        FROM user_integration_connections
        WHERE user_id::bigint = $1::bigint
          AND provider = 'x'
        FOR UPDATE
        "#,
    )
    .bind(user_id)
    .fetch_optional(&mut **transaction)
    .await?;
    let Some((connection_id, metadata)) = row else {
        return Ok(PrepareXOAuthExchangeOutcome::NotInitialized);
    };
    let Some(pending) = metadata
        .as_ref()
        .and_then(|metadata| metadata.0.get("oauth_pending"))
        .and_then(Value::as_object)
    else {
        return Ok(PrepareXOAuthExchangeOutcome::MissingPendingState);
    };
    let (Some(expected_state), Some(code_verifier), Some(created_at)) = (
        pending.get("state").and_then(Value::as_str),
        pending.get("code_verifier").and_then(Value::as_str),
        pending.get("created_at").and_then(Value::as_str),
    ) else {
        return Ok(PrepareXOAuthExchangeOutcome::InvalidPendingState);
    };
    if expected_state != supplied_state {
        return Ok(PrepareXOAuthExchangeOutcome::StateMismatch);
    }
    let Ok(created_at) = DateTime::parse_from_rfc3339(created_at) else {
        return Ok(PrepareXOAuthExchangeOutcome::Expired);
    };
    if now
        .signed_duration_since(created_at.with_timezone(&Utc))
        .num_seconds()
        > 20 * 60
    {
        return Ok(PrepareXOAuthExchangeOutcome::Expired);
    }
    Ok(PrepareXOAuthExchangeOutcome::Prepared(
        PreparedXOAuthExchange {
            connection_id,
            code_verifier: code_verifier.to_owned(),
        },
    ))
}

#[allow(clippy::too_many_arguments)]
pub async fn finalize_x_oauth_exchange(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
    connection_id: i64,
    expected_state: &str,
    provider_user_id: &str,
    provider_username: Option<&str>,
    access_token_encrypted: &str,
    refresh_token_encrypted: Option<&str>,
    token_expires_at: Option<DateTime<Utc>>,
    scopes: &[String],
    connected_at: DateTime<Utc>,
) -> Result<bool, IntegrationRepositoryError> {
    let scopes = Json(scopes);
    let updated = sqlx::query(
        r#"
        UPDATE user_integration_connections
        SET provider_user_id = $4,
            provider_username = $5,
            access_token_encrypted = $6,
            refresh_token_encrypted = $7,
            token_expires_at = $8,
            scopes = $9,
            is_active = TRUE,
            connection_metadata = (
                (COALESCE(connection_metadata::jsonb, '{}'::jsonb) - 'oauth_pending')
                || jsonb_build_object('connected_at', $10)
            )::json,
            updated_at = timezone('UTC', now())
        WHERE id::bigint = $2::bigint
          AND user_id::bigint = $1::bigint
          AND provider = 'x'
          AND connection_metadata::jsonb #>> '{oauth_pending,state}' = $3
        "#,
    )
    .bind(user_id)
    .bind(connection_id)
    .bind(expected_state)
    .bind(provider_user_id)
    .bind(provider_username)
    .bind(access_token_encrypted)
    .bind(refresh_token_encrypted)
    .bind(token_expires_at.map(|value| value.naive_utc()))
    .bind(scopes)
    .bind(connected_at.to_rfc3339())
    .execute(&mut **transaction)
    .await?
    .rows_affected()
        > 0;
    if !updated {
        return Ok(false);
    }
    if let Some(username) = provider_username {
        sqlx::query(
            r#"
            UPDATE users
            SET twitter_username = $2,
                updated_at = timezone('UTC', now())
            WHERE id::bigint = $1::bigint
            "#,
        )
        .bind(user_id)
        .bind(username)
        .execute(&mut **transaction)
        .await?;
    }
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
        VALUES ($1::bigint::integer, 'connected', NULL, '{}'::json, timezone('UTC', now()), timezone('UTC', now()))
        ON CONFLICT (connection_id) DO UPDATE
        SET last_status = CASE
                WHEN NULLIF(user_integration_sync_state.last_status, '') IS NULL THEN 'connected'
                ELSE user_integration_sync_state.last_status
            END,
            last_error = NULL,
            updated_at = timezone('UTC', now())
        "#,
    )
    .bind(connection_id)
    .execute(&mut **transaction)
    .await?;
    Ok(true)
}

pub async fn prepare_x_disconnect(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
) -> Result<Option<XDisconnectPlan>, IntegrationRepositoryError> {
    let row = sqlx::query_as::<_, (i64, Option<String>, Option<String>)>(
        r#"
        SELECT id::bigint, refresh_token_encrypted, access_token_encrypted
        FROM user_integration_connections
        WHERE user_id::bigint = $1::bigint
          AND provider = 'x'
        FOR UPDATE
        "#,
    )
    .bind(user_id)
    .fetch_optional(&mut **transaction)
    .await?;
    Ok(row.map(|(connection_id, refresh, access)| {
        if refresh.is_some() {
            XDisconnectPlan {
                connection_id,
                encrypted_token: refresh,
                token_type_hint: "refresh_token",
            }
        } else {
            XDisconnectPlan {
                connection_id,
                encrypted_token: access,
                token_type_hint: "access_token",
            }
        }
    }))
}

pub async fn finalize_x_disconnect(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
    connection_id: i64,
    disconnected_at: DateTime<Utc>,
) -> Result<bool, IntegrationRepositoryError> {
    let updated = sqlx::query(
        r#"
        UPDATE user_integration_connections
        SET is_active = FALSE,
            access_token_encrypted = NULL,
            refresh_token_encrypted = NULL,
            token_expires_at = NULL,
            connection_metadata = (
                (COALESCE(connection_metadata::jsonb, '{}'::jsonb) - 'oauth_pending')
                || jsonb_build_object('disconnected_at', $3)
            )::json,
            updated_at = timezone('UTC', now())
        WHERE id::bigint = $2::bigint
          AND user_id::bigint = $1::bigint
          AND provider = 'x'
        "#,
    )
    .bind(user_id)
    .bind(connection_id)
    .bind(disconnected_at.to_rfc3339())
    .execute(&mut **transaction)
    .await?
    .rows_affected()
        > 0;
    if !updated {
        return Ok(false);
    }
    sqlx::query(
        r#"
        INSERT INTO user_integration_sync_state (
            connection_id,
            last_synced_at,
            last_status,
            last_error,
            sync_metadata,
            created_at,
            updated_at
        )
        VALUES (
            $1::bigint::integer,
            $2,
            'disconnected',
            NULL,
            '{}'::json,
            timezone('UTC', now()),
            timezone('UTC', now())
        )
        ON CONFLICT (connection_id) DO UPDATE
        SET last_synced_at = EXCLUDED.last_synced_at,
            last_status = 'disconnected',
            last_error = NULL,
            updated_at = timezone('UTC', now())
        "#,
    )
    .bind(connection_id)
    .bind(disconnected_at.naive_utc())
    .execute(&mut **transaction)
    .await?;
    Ok(true)
}

fn project_x_connection(row: XConnectionRow) -> XConnectionProjection {
    let scopes = row
        .scopes
        .and_then(|scopes| scopes.0.as_array().cloned())
        .unwrap_or_default()
        .into_iter()
        .filter_map(|value| value.as_str().map(str::trim).map(ToOwned::to_owned))
        .filter(|value| !value.is_empty())
        .collect();
    let is_active = row.is_active.unwrap_or(false);
    XConnectionProjection {
        connection_id: row.connection_id,
        connected: is_active && row.token_configured.unwrap_or(false),
        is_active,
        provider_user_id: row.provider_user_id,
        provider_username: row.provider_username,
        scopes,
        last_synced_at: row.last_synced_at.map(|value| value.and_utc()),
        last_status: row.last_status,
        last_error: row.last_error,
        twitter_username: row.twitter_username,
    }
}

pub async fn list_user_llm_integrations(
    pool: &PgPool,
    user_id: i64,
) -> Result<Vec<UserLlmIntegrationProjection>, IntegrationRepositoryError> {
    let rows = sqlx::query_as::<_, UserLlmIntegrationRow>(
        r#"
        SELECT
            provider,
            access_token_encrypted IS NOT NULL AND access_token_encrypted <> '' AS configured,
            updated_at
        FROM user_integration_connections
        WHERE user_id::bigint = $1::bigint
          AND provider = ANY(ARRAY['anthropic', 'openai']::text[])
        ORDER BY provider ASC
        "#,
    )
    .bind(user_id)
    .fetch_all(pool)
    .await?;
    Ok(rows.into_iter().map(project).collect())
}

pub async fn upsert_user_llm_integration(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
    provider: &str,
    encrypted_api_key: &str,
) -> Result<UserLlmIntegrationProjection, IntegrationRepositoryError> {
    let row = sqlx::query_as::<_, UserLlmIntegrationRow>(
        r#"
        INSERT INTO user_integration_connections (
            user_id,
            provider,
            access_token_encrypted,
            is_active,
            connection_metadata,
            created_at,
            updated_at
        )
        VALUES (
            $1::bigint::integer,
            $2,
            $3,
            TRUE,
            json_build_object('kind', 'llm_api_key'),
            timezone('UTC', now()),
            timezone('UTC', now())
        )
        ON CONFLICT (user_id, provider) DO UPDATE
        SET access_token_encrypted = EXCLUDED.access_token_encrypted,
            is_active = TRUE,
            connection_metadata = (
                COALESCE(user_integration_connections.connection_metadata::jsonb, '{}'::jsonb)
                || jsonb_build_object('kind', 'llm_api_key')
            )::json,
            updated_at = timezone('UTC', now())
        RETURNING
            provider,
            access_token_encrypted IS NOT NULL AND access_token_encrypted <> '' AS configured,
            updated_at
        "#,
    )
    .bind(user_id)
    .bind(provider)
    .bind(encrypted_api_key)
    .fetch_one(&mut **transaction)
    .await?;
    Ok(project(row))
}

pub async fn delete_user_llm_integration(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
    provider: &str,
) -> Result<bool, IntegrationRepositoryError> {
    Ok(sqlx::query(
        r#"
        DELETE FROM user_integration_connections
        WHERE user_id::bigint = $1::bigint
          AND provider = $2
        "#,
    )
    .bind(user_id)
    .bind(provider)
    .execute(&mut **transaction)
    .await?
    .rows_affected()
        > 0)
}

pub async fn user_llm_integration_configured(
    pool: &PgPool,
    user_id: i64,
    provider: &str,
) -> Result<bool, IntegrationRepositoryError> {
    Ok(sqlx::query_scalar::<_, bool>(
        r#"
        SELECT EXISTS(
            SELECT 1
            FROM user_integration_connections
            WHERE user_id::bigint = $1::bigint
              AND provider = $2
              AND access_token_encrypted IS NOT NULL
              AND access_token_encrypted <> ''
        )
        "#,
    )
    .bind(user_id)
    .bind(provider)
    .fetch_one(pool)
    .await?)
}

fn project(row: UserLlmIntegrationRow) -> UserLlmIntegrationProjection {
    UserLlmIntegrationProjection {
        provider: row.provider,
        configured: row.configured,
        updated_at: row.updated_at.map(|value| value.and_utc()),
    }
}

#[derive(Debug, Error)]
pub enum IntegrationRepositoryError {
    #[error("integration database operation failed")]
    Sqlx(#[from] sqlx::Error),
}
