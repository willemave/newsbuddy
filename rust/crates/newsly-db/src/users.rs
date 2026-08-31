use chrono::{DateTime, Utc};
use serde_json::Value;
use sqlx::{FromRow, PgPool, Postgres, Transaction};
use thiserror::Error;

#[derive(Debug, Clone, PartialEq, FromRow)]
pub struct UserProfileProjection {
    pub id: i64,
    pub apple_id: String,
    pub email: String,
    pub full_name: Option<String>,
    pub twitter_username: Option<String>,
    pub council_personas: Option<Value>,
    pub is_admin: bool,
    pub is_active: bool,
    pub has_completed_new_user_tutorial: bool,
    pub has_completed_onboarding: bool,
    pub reading_experience: String,
    pub created_at: DateTime<Utc>,
    pub updated_at: DateTime<Utc>,
    pub has_x_bookmark_sync: bool,
}

#[derive(Debug, Clone, Default)]
pub struct UserProfilePatch {
    pub full_name: Option<Option<String>>,
    pub twitter_username: Option<Option<String>>,
    pub council_personas: Option<Value>,
    pub reading_experience: Option<String>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct AppleUserUpsert {
    pub profile: UserProfileProjection,
    pub is_new_user: bool,
}

#[derive(Debug, Clone, Default)]
pub struct DebugUserPatch {
    pub has_completed_onboarding: Option<bool>,
    pub has_completed_new_user_tutorial: Option<bool>,
    pub reading_experience: Option<String>,
}

/// Find an Apple identity or create its active Newsly account. Existing users
/// retain their stored profile fields, matching the established sign-in behavior.
///
/// # Errors
///
/// Returns [`UserProfileRepositoryError`] when `PostgreSQL` cannot insert or
/// load the resulting profile.
pub async fn find_or_create_apple_user(
    transaction: &mut Transaction<'_, Postgres>,
    apple_id: &str,
    email: &str,
    full_name: Option<&str>,
) -> Result<AppleUserUpsert, UserProfileRepositoryError> {
    let inserted_id = sqlx::query_scalar::<_, i64>(
        r"
        INSERT INTO users (
            apple_id,
            email,
            full_name,
            is_admin,
            is_active,
            has_completed_new_user_tutorial,
            has_completed_onboarding,
            reading_experience,
            created_at,
            updated_at
        )
        VALUES (
            $1,
            $2,
            $3,
            FALSE,
            TRUE,
            FALSE,
            FALSE,
            'briefing',
            timezone('UTC', now()),
            timezone('UTC', now())
        )
        ON CONFLICT (apple_id) DO NOTHING
        RETURNING id::bigint
        ",
    )
    .bind(apple_id)
    .bind(email)
    .bind(full_name)
    .fetch_optional(&mut **transaction)
    .await?;
    let (user_id, is_new_user) = if let Some(user_id) = inserted_id {
        (user_id, true)
    } else {
        let user_id = sqlx::query_scalar::<_, i64>(
            "SELECT id::bigint FROM users WHERE apple_id = $1 FOR SHARE",
        )
        .bind(apple_id)
        .fetch_one(&mut **transaction)
        .await?;
        (user_id, false)
    };
    let profile = load_profile(&mut **transaction, user_id)
        .await?
        .ok_or(UserProfileRepositoryError::LostUser(user_id))?;
    Ok(AppleUserUpsert {
        profile,
        is_new_user,
    })
}

/// Create a local debug identity or update an explicitly selected account.
/// This function does not decide whether debug authentication is enabled.
///
/// # Errors
///
/// Returns [`UserProfileRepositoryError`] when the selected account does not
/// exist or PostgreSQL cannot persist and load the profile.
pub async fn create_or_update_debug_user(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: Option<i64>,
    apple_id: &str,
    email: &str,
    patch: &DebugUserPatch,
) -> Result<AppleUserUpsert, UserProfileRepositoryError> {
    let (user_id, is_new_user) = if let Some(user_id) = user_id {
        let exists = sqlx::query_scalar::<_, bool>(
            "SELECT EXISTS(SELECT 1 FROM users WHERE id::bigint = $1 FOR UPDATE)",
        )
        .bind(user_id)
        .fetch_one(&mut **transaction)
        .await?;
        if !exists {
            return Err(UserProfileRepositoryError::UserNotFound(user_id));
        }
        (user_id, false)
    } else {
        let user_id = sqlx::query_scalar::<_, i64>(
            r"
            INSERT INTO users (
                apple_id,
                email,
                full_name,
                is_admin,
                is_active,
                has_completed_new_user_tutorial,
                has_completed_onboarding,
                reading_experience,
                created_at,
                updated_at
            )
            VALUES ($1, $2, 'Debug User', FALSE, TRUE, FALSE, FALSE, 'briefing', NOW(), NOW())
            RETURNING id::bigint
            ",
        )
        .bind(apple_id)
        .bind(email)
        .fetch_one(&mut **transaction)
        .await?;
        (user_id, true)
    };
    sqlx::query(
        r"
        UPDATE users
        SET
            has_completed_onboarding = COALESCE($2, has_completed_onboarding),
            has_completed_new_user_tutorial = COALESCE($3, has_completed_new_user_tutorial),
            reading_experience = COALESCE($4, reading_experience),
            updated_at = CASE
                WHEN $2::boolean IS NOT NULL OR $3::boolean IS NOT NULL OR $4::text IS NOT NULL
                THEN NOW()
                ELSE updated_at
            END
        WHERE id::bigint = $1
        ",
    )
    .bind(user_id)
    .bind(patch.has_completed_onboarding)
    .bind(patch.has_completed_new_user_tutorial)
    .bind(&patch.reading_experience)
    .execute(&mut **transaction)
    .await?;
    let profile = load_profile(&mut **transaction, user_id)
        .await?
        .ok_or(UserProfileRepositoryError::LostUser(user_id))?;
    Ok(AppleUserUpsert {
        profile,
        is_new_user,
    })
}

/// Atomically deactivate an active account while holding its row lock.
///
/// # Errors
///
/// Returns [`UserProfileRepositoryError`] when PostgreSQL cannot update the
/// account.
pub async fn deactivate_active_user(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
) -> Result<bool, UserProfileRepositoryError> {
    let deactivated = sqlx::query_scalar::<_, i64>(
        r"
        UPDATE users
        SET is_active = FALSE, updated_at = NOW()
        WHERE id::bigint = $1 AND is_active = TRUE
        RETURNING id::bigint
        ",
    )
    .bind(user_id)
    .fetch_optional(&mut **transaction)
    .await?;
    Ok(deactivated.is_some())
}

pub async fn find_user_profile(
    pool: &PgPool,
    user_id: i64,
) -> Result<Option<UserProfileProjection>, UserProfileRepositoryError> {
    load_profile(pool, user_id).await
}

pub async fn update_user_profile(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
    patch: &UserProfilePatch,
) -> Result<Option<UserProfileProjection>, UserProfileRepositoryError> {
    if let Some(full_name) = &patch.full_name {
        sqlx::query("UPDATE users SET full_name = $1, updated_at = NOW() WHERE id = $2")
            .bind(full_name)
            .bind(user_id)
            .execute(&mut **transaction)
            .await?;
    }
    if let Some(twitter_username) = &patch.twitter_username {
        sqlx::query("UPDATE users SET twitter_username = $1, updated_at = NOW() WHERE id = $2")
            .bind(twitter_username)
            .bind(user_id)
            .execute(&mut **transaction)
            .await?;
    }
    if let Some(council_personas) = &patch.council_personas {
        sqlx::query("UPDATE users SET council_personas = $1, updated_at = NOW() WHERE id = $2")
            .bind(council_personas)
            .bind(user_id)
            .execute(&mut **transaction)
            .await?;
    }
    if let Some(reading_experience) = &patch.reading_experience {
        sqlx::query("UPDATE users SET reading_experience = $1, updated_at = NOW() WHERE id = $2")
            .bind(reading_experience)
            .bind(user_id)
            .execute(&mut **transaction)
            .await?;
    }
    load_profile(&mut **transaction, user_id).await
}

async fn load_profile<'e, E>(
    executor: E,
    user_id: i64,
) -> Result<Option<UserProfileProjection>, UserProfileRepositoryError>
where
    E: sqlx::Executor<'e, Database = Postgres>,
{
    let row = sqlx::query_as::<_, UserProfileProjection>(
        r#"
        SELECT
            app_user.id::bigint AS id,
            app_user.apple_id,
            app_user.email,
            app_user.full_name,
            app_user.twitter_username,
            app_user.council_personas,
            app_user.is_admin,
            app_user.is_active,
            app_user.has_completed_new_user_tutorial,
            app_user.has_completed_onboarding,
            app_user.reading_experience,
            app_user.created_at,
            app_user.updated_at,
            EXISTS (
                SELECT 1
                FROM user_integration_connections AS connection
                WHERE connection.user_id = app_user.id
                  AND connection.provider = 'x'
                  AND connection.is_active = TRUE
                  AND COALESCE(connection.access_token_encrypted, '') <> ''
            ) AS has_x_bookmark_sync
        FROM users AS app_user
        WHERE app_user.id = $1
        "#,
    )
    .bind(user_id)
    .fetch_optional(executor)
    .await?;
    Ok(row)
}

#[derive(Debug, Error)]
pub enum UserProfileRepositoryError {
    #[error("user profile database operation failed")]
    Sqlx(#[from] sqlx::Error),
    #[error("user {0} disappeared while its profile was being loaded")]
    LostUser(i64),
    #[error("user {0} was not found")]
    UserNotFound(i64),
}
