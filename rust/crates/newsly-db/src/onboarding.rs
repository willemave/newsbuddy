use serde_json::Value;
use sqlx::types::Json;
use sqlx::{FromRow, PgPool, Postgres, Transaction};
use thiserror::Error;

#[derive(Debug, FromRow)]
struct DiscoveryRunRow {
    id: i64,
    status: String,
    topic_summary: Option<String>,
    inferred_topics: Json<Value>,
    error_message: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, FromRow)]
pub struct OnboardingDiscoveryLaneProjection {
    pub name: String,
    pub status: String,
    pub completed_queries: i32,
    pub query_count: i32,
}

#[derive(Debug, Clone, PartialEq, FromRow)]
pub struct OnboardingSuggestionProjection {
    pub id: i64,
    pub suggestion_type: String,
    pub title: Option<String>,
    pub site_url: Option<String>,
    pub feed_url: Option<String>,
    pub subreddit: Option<String>,
    pub rationale: Option<String>,
    pub score: Option<f64>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct OnboardingDiscoveryStatusProjection {
    pub run_id: i64,
    pub run_status: String,
    pub topic_summary: Option<String>,
    pub inferred_topics: Vec<String>,
    pub lanes: Vec<OnboardingDiscoveryLaneProjection>,
    pub suggestions: Option<Vec<OnboardingSuggestionProjection>>,
    pub error_message: Option<String>,
}

pub async fn find_onboarding_discovery_status(
    pool: &PgPool,
    user_id: i64,
    run_id: i64,
) -> Result<Option<OnboardingDiscoveryStatusProjection>, OnboardingRepositoryError> {
    let run = sqlx::query_as::<_, DiscoveryRunRow>(
        r#"
        SELECT
            id::bigint AS id,
            status,
            topic_summary,
            inferred_topics,
            error_message
        FROM onboarding_discovery_runs
        WHERE id::bigint = $1::bigint
          AND user_id::bigint = $2::bigint
        "#,
    )
    .bind(run_id)
    .bind(user_id)
    .fetch_optional(pool)
    .await?;
    let Some(run) = run else {
        return Ok(None);
    };

    let lanes = sqlx::query_as::<_, OnboardingDiscoveryLaneProjection>(
        r#"
        SELECT
            COALESCE(lane_name, '') AS name,
            COALESCE(status, 'queued') AS status,
            COALESCE(completed_queries, 0) AS completed_queries,
            COALESCE(query_count, 0) AS query_count
        FROM onboarding_discovery_lanes
        WHERE run_id::bigint = $1::bigint
        ORDER BY id ASC
        "#,
    )
    .bind(run.id)
    .fetch_all(pool)
    .await?;

    let suggestions = if run.status == "completed" {
        Some(
            sqlx::query_as::<_, OnboardingSuggestionProjection>(
                r#"
                SELECT
                    id::bigint AS id,
                    suggestion_type,
                    title,
                    site_url,
                    feed_url,
                    subreddit,
                    rationale,
                    score
                FROM onboarding_discovery_suggestions
                WHERE run_id::bigint = $1::bigint
                  AND status = 'new'
                ORDER BY COALESCE(score, 0) DESC
                "#,
            )
            .bind(run.id)
            .fetch_all(pool)
            .await?,
        )
    } else {
        None
    };

    let inferred_topics = run
        .inferred_topics
        .0
        .as_array()
        .map(|values| {
            values
                .iter()
                .filter_map(Value::as_str)
                .map(ToOwned::to_owned)
                .collect()
        })
        .unwrap_or_default();

    Ok(Some(OnboardingDiscoveryStatusProjection {
        run_id: run.id,
        run_status: run.status,
        topic_summary: run.topic_summary,
        inferred_topics,
        lanes,
        suggestions,
        error_message: run.error_message,
    }))
}

/// Marks the user's tutorial complete and closes the latest active first-edition run.
///
/// Both mutations belong to the same short transaction. No provider or sandbox work is
/// performed while the transaction is open.
pub async fn complete_onboarding_tutorial(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
) -> Result<bool, OnboardingRepositoryError> {
    let user_exists = sqlx::query_scalar::<_, i64>(
        r#"
        UPDATE users
        SET has_completed_new_user_tutorial = TRUE,
            updated_at = timezone('UTC', now())
        WHERE id::bigint = $1::bigint
        RETURNING id::bigint
        "#,
    )
    .bind(user_id)
    .fetch_optional(&mut **transaction)
    .await?
    .is_some();

    if !user_exists {
        return Ok(false);
    }

    sqlx::query(
        r#"
        UPDATE onboarding_first_edition_runs
        SET status = 'completed',
            completed_at = timezone('UTC', now()),
            revision = COALESCE(revision, 0) + 1
        WHERE id = (
            SELECT id
            FROM onboarding_first_edition_runs
            WHERE user_id::bigint = $1::bigint
              AND status = 'active'
            ORDER BY id DESC
            LIMIT 1
            FOR UPDATE
        )
        "#,
    )
    .bind(user_id)
    .execute(&mut **transaction)
    .await?;

    Ok(true)
}

#[derive(Debug, Error)]
pub enum OnboardingRepositoryError {
    #[error("onboarding database operation failed")]
    Sqlx(#[from] sqlx::Error),
}
