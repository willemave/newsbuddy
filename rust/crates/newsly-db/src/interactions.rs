use chrono::NaiveDateTime;
use serde_json::{Map, Value};
use sqlx::{Postgres, Transaction};
use thiserror::Error;

#[derive(Debug, Clone)]
pub struct NewFeedback<'a> {
    pub user_id: i64,
    pub message: &'a str,
    pub source: &'a str,
    pub app_version: Option<&'a str>,
    pub build_number: Option<&'a str>,
    pub platform: Option<&'a str>,
    pub os_version: Option<&'a str>,
    pub device_model: Option<&'a str>,
}

pub async fn insert_feedback(
    transaction: &mut Transaction<'_, Postgres>,
    feedback: &NewFeedback<'_>,
) -> Result<i64, InteractionRepositoryError> {
    let id = sqlx::query_scalar::<_, i64>(
        r#"
        INSERT INTO user_feedback (
            user_id, message, source, app_version, build_number, platform, os_version,
            device_model, created_at
        )
        VALUES ($1::bigint::integer, $2, $3, $4, $5, $6, $7, $8, timezone('UTC', now()))
        RETURNING id::bigint
        "#,
    )
    .bind(feedback.user_id)
    .bind(feedback.message)
    .bind(feedback.source)
    .bind(feedback.app_version)
    .bind(feedback.build_number)
    .bind(feedback.platform)
    .bind(feedback.os_version)
    .bind(feedback.device_model)
    .fetch_one(&mut **transaction)
    .await?;
    Ok(id)
}

#[derive(Debug, Clone)]
pub struct NewContentInteraction<'a> {
    pub user_id: i64,
    pub content_id: i64,
    pub interaction_id: &'a str,
    pub interaction_type: &'a str,
    pub occurred_at: NaiveDateTime,
    pub surface: Option<&'a str>,
    pub context_data: Map<String, Value>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ContentInteractionInsertResult {
    pub recorded: bool,
    pub id: i64,
}

pub async fn insert_content_interaction(
    transaction: &mut Transaction<'_, Postgres>,
    interaction: &NewContentInteraction<'_>,
) -> Result<ContentInteractionInsertResult, InteractionRepositoryError> {
    let content_exists = sqlx::query_scalar::<_, bool>(
        "SELECT EXISTS(SELECT 1 FROM contents WHERE id::bigint = $1::bigint)",
    )
    .bind(interaction.content_id)
    .fetch_one(&mut **transaction)
    .await?;
    if !content_exists {
        return Err(InteractionRepositoryError::ContentNotFound(
            interaction.content_id,
        ));
    }

    let inserted = sqlx::query_scalar::<_, i64>(
        r#"
        INSERT INTO analytics_interactions (
            user_id, content_id, interaction_type, interaction_id, surface, context_data,
            occurred_at, created_at
        )
        VALUES (
            $1::bigint::integer,
            $2::bigint::integer,
            $3,
            $4,
            $5,
            $6,
            $7,
            timezone('UTC', now())
        )
        ON CONFLICT (user_id, interaction_id) DO NOTHING
        RETURNING id::bigint
        "#,
    )
    .bind(interaction.user_id)
    .bind(interaction.content_id)
    .bind(interaction.interaction_type)
    .bind(interaction.interaction_id)
    .bind(interaction.surface)
    .bind(Value::Object(interaction.context_data.clone()))
    .bind(interaction.occurred_at)
    .fetch_optional(&mut **transaction)
    .await?;
    if let Some(id) = inserted {
        return Ok(ContentInteractionInsertResult { recorded: true, id });
    }
    let id = sqlx::query_scalar::<_, i64>(
        r#"
        SELECT id::bigint
        FROM analytics_interactions
        WHERE user_id::bigint = $1::bigint AND interaction_id = $2
        "#,
    )
    .bind(interaction.user_id)
    .bind(interaction.interaction_id)
    .fetch_one(&mut **transaction)
    .await?;
    Ok(ContentInteractionInsertResult {
        recorded: false,
        id,
    })
}

#[derive(Debug, Error)]
pub enum InteractionRepositoryError {
    #[error("content {0} was not found")]
    ContentNotFound(i64),
    #[error("interaction database operation failed")]
    Sqlx(#[from] sqlx::Error),
}
