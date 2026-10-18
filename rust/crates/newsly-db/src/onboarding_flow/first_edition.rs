//! Creation of the first-edition run and its selected-source projections.
use super::OnboardingFlowRepositoryError;
use serde_json::Value;
use sqlx::{FromRow, Postgres, Transaction};

pub(super) async fn start_first_edition(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
) -> Result<i64, OnboardingFlowRepositoryError> {
    sqlx::query(
        r#"
        UPDATE onboarding_first_edition_runs
        SET status = 'expired',
            completed_at = timezone('UTC', now())
        WHERE user_id::bigint = $1::bigint AND status = 'active'
        "#,
    )
    .bind(user_id)
    .execute(&mut **transaction)
    .await?;
    let run_id = sqlx::query_scalar::<_, i64>(
        r#"
        INSERT INTO onboarding_first_edition_runs (
            user_id,
            status,
            revision,
            started_at,
            completed_at
        )
        VALUES (
            $1::bigint::integer,
            'active',
            1,
            timezone('UTC', now()),
            NULL
        )
        RETURNING id::bigint
        "#,
    )
    .bind(user_id)
    .fetch_one(&mut **transaction)
    .await?;
    let configs = sqlx::query_as::<_, FirstEditionConfigRow>(
        r#"
        SELECT
            id::bigint AS id,
            scraper_type,
            display_name,
            COALESCE(config, '{}'::json) AS config
        FROM user_scraper_configs
        WHERE user_id::bigint = $1::bigint AND is_active = TRUE
        ORDER BY created_at ASC, id ASC
        "#,
    )
    .bind(user_id)
    .fetch_all(&mut **transaction)
    .await?;
    for (position, config) in configs.iter().filter_map(first_edition_spec).enumerate() {
        sqlx::query(
            r#"
            INSERT INTO onboarding_first_edition_sources (
                run_id,
                source_key,
                display_name,
                source_kind,
                "position",
                status,
                processed_item_count,
                completed_at
            )
            VALUES (
                $1::bigint::integer,
                $2,
                $3,
                $4,
                $5,
                'queued',
                0,
                NULL
            )
            "#,
        )
        .bind(run_id)
        .bind(config.source_key)
        .bind(config.display_name)
        .bind(config.source_kind)
        .bind(i32::try_from(position).unwrap_or(i32::MAX))
        .execute(&mut **transaction)
        .await?;
    }
    Ok(run_id)
}

#[derive(Debug, FromRow)]
struct FirstEditionConfigRow {
    id: i64,
    scraper_type: String,
    display_name: Option<String>,
    config: Value,
}

struct FirstEditionSpec {
    source_key: String,
    display_name: String,
    source_kind: &'static str,
}

fn first_edition_spec(config: &FirstEditionConfigRow) -> Option<FirstEditionSpec> {
    let config_name = config.config.get("name").and_then(Value::as_str);
    let display_name = config
        .display_name
        .as_deref()
        .or(config_name)
        .unwrap_or(&config.scraper_type)
        .to_owned();
    match config.scraper_type.as_str() {
        "substack" | "atom" | "podcast_rss" => Some(FirstEditionSpec {
            source_key: format!("feed:{}", config.id),
            display_name,
            source_kind: "feed",
        }),
        "aggregator" => {
            let key = config
                .config
                .get("key")
                .and_then(Value::as_str)
                .unwrap_or(&display_name)
                .trim()
                .to_lowercase();
            Some(FirstEditionSpec {
                source_key: format!("scraper:{key}"),
                display_name,
                source_kind: "aggregator",
            })
        }
        "reddit" => {
            let subreddit = config
                .config
                .get("subreddit")
                .and_then(Value::as_str)
                .unwrap_or(&display_name)
                .trim()
                .strip_prefix("r/")
                .unwrap_or_else(|| {
                    config
                        .config
                        .get("subreddit")
                        .and_then(Value::as_str)
                        .unwrap_or(&display_name)
                        .trim()
                })
                .trim_matches('/');
            Some(FirstEditionSpec {
                source_key: format!("reddit:{}", config.id),
                display_name: format!("r/{subreddit}"),
                source_kind: "reddit",
            })
        }
        _ => None,
    }
}
