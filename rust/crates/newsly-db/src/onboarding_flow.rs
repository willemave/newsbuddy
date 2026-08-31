use std::collections::HashSet;

use serde_json::Value;
use sqlx::{FromRow, PgPool, Postgres, Transaction};
use thiserror::Error;

use crate::canonicalize_feed_url;

const NEWS_SEED_LIMIT: i64 = 100;
const FEED_CONTENT_SEED_LIMIT: i64 = 30;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct OnboardingAudioLaneInput {
    pub name: String,
    pub goal: String,
    pub target: String,
    pub queries: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct OnboardingAudioRunInput {
    pub user_id: i64,
    pub topic_summary: String,
    pub inferred_topics: Vec<String>,
    pub lanes: Vec<OnboardingAudioLaneInput>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct OnboardingAudioRunProjection {
    pub run_id: i64,
    pub run_status: String,
    pub topic_summary: Option<String>,
    pub inferred_topics: Vec<String>,
    pub lanes: Vec<OnboardingAudioLaneProjection>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct OnboardingAudioLaneProjection {
    pub name: String,
    pub status: String,
    pub completed_queries: i32,
    pub query_count: i32,
}

#[derive(Debug, Clone, PartialEq, FromRow)]
pub struct AgentOnboardingSuggestionProjection {
    pub id: i64,
    pub suggestion_type: String,
    pub title: Option<String>,
    pub feed_url: Option<String>,
    pub subreddit: Option<String>,
}

#[derive(Debug, Clone, PartialEq, FromRow)]
pub struct ExistingOnboardingFeedConfig {
    pub id: i64,
    pub scraper_type: String,
    pub feed_url: Option<String>,
    pub config: Value,
}

#[derive(Debug, Clone, PartialEq)]
pub struct OnboardingCompletionSource {
    pub scraper_type: String,
    pub title: Option<String>,
    pub feed_url: String,
    pub seed_feed_url: String,
    pub config: Value,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct OnboardingCompletionAggregator {
    pub key: String,
    pub title: Option<String>,
    pub topics: Vec<String>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct OnboardingCompletionInput {
    pub user_id: i64,
    pub discovery_run_id: Option<i64>,
    pub selected_suggestion_ids: Vec<i64>,
    pub sources: Vec<OnboardingCompletionSource>,
    pub subreddits: Vec<String>,
    pub aggregators: Vec<OnboardingCompletionAggregator>,
    pub update_twitter_username: bool,
    pub twitter_username: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct OnboardingCompletionProjection {
    pub configured_source_count: i64,
    pub feed_config_ids: Vec<i64>,
    pub first_edition_run_id: i64,
    pub sources_to_scrape: Vec<String>,
    pub generate_image_content_ids: Vec<i64>,
    pub inbox_count: i64,
    pub tutorial_complete: bool,
    pub has_feed_discovery_task: bool,
}

/// Creates the durable audio-discovery run and lanes inside the caller's short transaction.
/// Queue insertion must be performed by the caller before committing this transaction.
pub async fn create_onboarding_audio_run(
    transaction: &mut Transaction<'_, Postgres>,
    input: &OnboardingAudioRunInput,
) -> Result<OnboardingAudioRunProjection, OnboardingFlowRepositoryError> {
    let user_exists = sqlx::query_scalar::<_, bool>(
        "SELECT EXISTS(SELECT 1 FROM users WHERE id::bigint = $1::bigint AND is_active = TRUE)",
    )
    .bind(input.user_id)
    .fetch_one(&mut **transaction)
    .await?;
    if !user_exists {
        return Err(OnboardingFlowRepositoryError::UserMissingOrInactive);
    }

    let run_id = sqlx::query_scalar::<_, i64>(
        r#"
        INSERT INTO onboarding_discovery_runs (
            user_id,
            status,
            topic_summary,
            inferred_topics,
            created_at
        )
        VALUES (
            $1::bigint::integer,
            'pending',
            $2,
            $3,
            timezone('UTC', now())
        )
        RETURNING id::bigint
        "#,
    )
    .bind(input.user_id)
    .bind(&input.topic_summary)
    .bind(serde_json::to_value(&input.inferred_topics)?)
    .fetch_one(&mut **transaction)
    .await?;

    let mut lanes = Vec::with_capacity(input.lanes.len());
    for lane in &input.lanes {
        let query_count = i32::try_from(lane.queries.len()).unwrap_or(i32::MAX);
        sqlx::query(
            r#"
            INSERT INTO onboarding_discovery_lanes (
                run_id,
                lane_name,
                goal,
                target,
                status,
                query_count,
                completed_queries,
                queries,
                created_at,
                updated_at
            )
            VALUES (
                $1::bigint::integer,
                $2,
                $3,
                $4,
                'queued',
                $5,
                0,
                $6,
                timezone('UTC', now()),
                timezone('UTC', now())
            )
            "#,
        )
        .bind(run_id)
        .bind(&lane.name)
        .bind(&lane.goal)
        .bind(&lane.target)
        .bind(query_count)
        .bind(serde_json::to_value(&lane.queries)?)
        .execute(&mut **transaction)
        .await?;
        lanes.push(OnboardingAudioLaneProjection {
            name: lane.name.clone(),
            status: "queued".to_owned(),
            completed_queries: 0,
            query_count,
        });
    }

    Ok(OnboardingAudioRunProjection {
        run_id,
        run_status: "pending".to_owned(),
        topic_summary: Some(input.topic_summary.clone()),
        inferred_topics: input.inferred_topics.clone(),
        lanes,
    })
}

/// Loads all suggestions for one authenticated agent-onboarding run in insertion order.
pub async fn load_agent_onboarding_suggestions(
    pool: &PgPool,
    user_id: i64,
    run_id: i64,
) -> Result<Vec<AgentOnboardingSuggestionProjection>, OnboardingFlowRepositoryError> {
    require_completed_discovery_run(pool, user_id, run_id).await?;
    Ok(sqlx::query_as::<_, AgentOnboardingSuggestionProjection>(
        r#"
        SELECT
            id::bigint AS id,
            suggestion_type,
            title,
            feed_url,
            subreddit
        FROM onboarding_discovery_suggestions
        WHERE run_id::bigint = $1::bigint
          AND user_id::bigint = $2::bigint
          AND status = 'new'
        ORDER BY id ASC
        "#,
    )
    .bind(run_id)
    .bind(user_id)
    .fetch_all(pool)
    .await?)
}

/// Resolves a completion request only from persisted proposals owned by the authenticated user's
/// completed discovery run. Client-supplied URLs, titles, subreddit names, and profile text never
/// participate in this boundary.
pub async fn load_onboarding_completion_suggestions(
    pool: &PgPool,
    user_id: i64,
    run_id: i64,
    selected_suggestion_ids: &[i64],
) -> Result<Vec<AgentOnboardingSuggestionProjection>, OnboardingFlowRepositoryError> {
    let suggestions = load_agent_onboarding_suggestions(pool, user_id, run_id).await?;
    let selected_ids = selected_suggestion_ids
        .iter()
        .copied()
        .collect::<HashSet<_>>();
    if selected_ids.len() != selected_suggestion_ids.len() {
        return Err(OnboardingFlowRepositoryError::InvalidSuggestionSelection);
    }
    let selected = suggestions
        .into_iter()
        .filter(|suggestion| selected_ids.contains(&suggestion.id))
        .collect::<Vec<_>>();
    if selected.len() != selected_ids.len() {
        return Err(OnboardingFlowRepositoryError::InvalidSuggestionSelection);
    }
    Ok(selected)
}

async fn require_completed_discovery_run(
    pool: &PgPool,
    user_id: i64,
    run_id: i64,
) -> Result<(), OnboardingFlowRepositoryError> {
    let status = sqlx::query_scalar::<_, String>(
        r#"
        SELECT status
        FROM onboarding_discovery_runs
        WHERE id::bigint = $1::bigint
          AND user_id::bigint = $2::bigint
        "#,
    )
    .bind(run_id)
    .bind(user_id)
    .fetch_optional(pool)
    .await?
    .ok_or(OnboardingFlowRepositoryError::DiscoveryRunNotFound)?;
    if status != "completed" {
        return Err(OnboardingFlowRepositoryError::DiscoveryRunNotCompleted);
    }
    Ok(())
}

/// Loads owned feed identities before external validation. The final transaction repeats the
/// canonical identity check while holding the user row, so this prepare read cannot authorize a
/// stale write.
pub async fn list_existing_onboarding_feed_configs(
    pool: &PgPool,
    user_id: i64,
) -> Result<Vec<ExistingOnboardingFeedConfig>, OnboardingFlowRepositoryError> {
    Ok(sqlx::query_as::<_, ExistingOnboardingFeedConfig>(
        r#"
        SELECT
            id::bigint AS id,
            scraper_type,
            feed_url,
            COALESCE(config, '{}'::json) AS config
        FROM user_scraper_configs
        WHERE user_id::bigint = $1::bigint
          AND scraper_type IN ('substack', 'atom', 'podcast_rss')
        ORDER BY id ASC
        "#,
    )
    .bind(user_id)
    .fetch_all(pool)
    .await?)
}

/// Atomically persists onboarding selections and projects the durable task handoff.
///
/// The caller must enqueue every projected task with `QueueKernel` in this same transaction before
/// committing. No provider, feed validation, filesystem, or sandbox work occurs here.
///
/// # Panics
///
/// Panics only if the internally constructed Reddit or aggregator JSON objects violate their
/// local object/string invariants.
pub async fn complete_onboarding_selection(
    transaction: &mut Transaction<'_, Postgres>,
    input: &OnboardingCompletionInput,
) -> Result<OnboardingCompletionProjection, OnboardingFlowRepositoryError> {
    let tutorial_complete = lock_active_user(transaction, input.user_id).await?;
    validate_completion_selection(transaction, input).await?;
    let mut configured_source_count = 0_i64;
    let mut feed_config_ids = Vec::new();
    let mut source_names = HashSet::new();
    let mut sources_to_scrape = Vec::new();

    for source in &input.sources {
        let id = persist_config(
            transaction,
            input.user_id,
            &source.scraper_type,
            source.title.as_deref(),
            &source.feed_url,
            &source.config,
        )
        .await?;
        configured_source_count += 1;
        feed_config_ids.push(id);
    }

    for subreddit in &input.subreddits {
        let feed_url = format!("https://www.reddit.com/r/{subreddit}/");
        let config = serde_json::json!({
            "subreddit": subreddit,
            "feed_url": feed_url,
            "limit": 1,
        });
        persist_config(
            transaction,
            input.user_id,
            "reddit",
            Some(subreddit),
            config
                .get("feed_url")
                .and_then(Value::as_str)
                .expect("reddit feed URL is a string"),
            &config,
        )
        .await?;
        configured_source_count += 1;
        push_unique(&mut sources_to_scrape, &mut source_names, "Reddit");
    }

    for aggregator in &input.aggregators {
        let feed_url = format!("aggregator://{}", aggregator.key);
        let mut config = serde_json::json!({"key": aggregator.key, "limit": 1});
        if !aggregator.topics.is_empty() {
            config
                .as_object_mut()
                .expect("aggregator config is an object")
                .insert(
                    "topics".to_owned(),
                    serde_json::to_value(&aggregator.topics)?,
                );
        }
        persist_config(
            transaction,
            input.user_id,
            "aggregator",
            aggregator.title.as_deref().or(Some(&aggregator.key)),
            &feed_url,
            &config,
        )
        .await?;
        configured_source_count += 1;
        push_unique(&mut sources_to_scrape, &mut source_names, &aggregator.key);
    }

    feed_config_ids.sort_unstable();
    feed_config_ids.dedup();
    sources_to_scrape.sort();

    seed_recent_news(transaction, input.user_id).await?;
    let raw_feed_urls = input
        .sources
        .iter()
        .map(|source| source.seed_feed_url.trim())
        .filter(|value| !value.is_empty())
        .collect::<HashSet<_>>()
        .into_iter()
        .collect::<Vec<_>>();
    let seeded_feed_content_ids =
        seed_selected_feed_content(transaction, input.user_id, &raw_feed_urls).await?;

    if input.update_twitter_username {
        sqlx::query(
            r#"
            UPDATE users
            SET twitter_username = $2,
                has_completed_onboarding = TRUE,
                reading_experience = 'briefing',
                updated_at = timezone('UTC', now())
            WHERE id::bigint = $1::bigint
            "#,
        )
        .bind(input.user_id)
        .bind(&input.twitter_username)
        .execute(&mut **transaction)
        .await?;
    } else {
        sqlx::query(
            r#"
            UPDATE users
            SET has_completed_onboarding = TRUE,
                reading_experience = 'briefing',
                updated_at = timezone('UTC', now())
            WHERE id::bigint = $1::bigint
            "#,
        )
        .bind(input.user_id)
        .execute(&mut **transaction)
        .await?;
    }

    let first_edition_run_id = start_first_edition(transaction, input.user_id).await?;
    let has_feed_discovery_task = has_feed_discovery_task(transaction, input.user_id).await?;
    let generate_image_content_ids =
        eligible_image_content_ids(transaction, &seeded_feed_content_ids).await?;
    let inbox_count = count_unread_inbox(transaction, input.user_id).await?;

    Ok(OnboardingCompletionProjection {
        configured_source_count,
        feed_config_ids,
        first_edition_run_id,
        sources_to_scrape,
        generate_image_content_ids,
        inbox_count,
        tutorial_complete,
        has_feed_discovery_task,
    })
}

async fn validate_completion_selection(
    transaction: &mut Transaction<'_, Postgres>,
    input: &OnboardingCompletionInput,
) -> Result<(), OnboardingFlowRepositoryError> {
    let Some(run_id) = input.discovery_run_id else {
        return if input.selected_suggestion_ids.is_empty() {
            Ok(())
        } else {
            Err(OnboardingFlowRepositoryError::InvalidSuggestionSelection)
        };
    };
    let status = sqlx::query_scalar::<_, String>(
        r#"
        SELECT status
        FROM onboarding_discovery_runs
        WHERE id::bigint = $1::bigint
          AND user_id::bigint = $2::bigint
        FOR UPDATE
        "#,
    )
    .bind(run_id)
    .bind(input.user_id)
    .fetch_optional(&mut **transaction)
    .await?
    .ok_or(OnboardingFlowRepositoryError::DiscoveryRunNotFound)?;
    if status != "completed" {
        return Err(OnboardingFlowRepositoryError::DiscoveryRunNotCompleted);
    }
    let requested_ids = input
        .selected_suggestion_ids
        .iter()
        .copied()
        .collect::<HashSet<_>>();
    if requested_ids.len() != input.selected_suggestion_ids.len() {
        return Err(OnboardingFlowRepositoryError::InvalidSuggestionSelection);
    }
    if requested_ids.is_empty() {
        return Ok(());
    }
    let persisted_ids = sqlx::query_scalar::<_, i64>(
        r#"
        SELECT id::bigint
        FROM onboarding_discovery_suggestions
        WHERE run_id::bigint = $1::bigint
          AND user_id::bigint = $2::bigint
          AND status = 'new'
          AND id::bigint = ANY($3::bigint[])
        "#,
    )
    .bind(run_id)
    .bind(input.user_id)
    .bind(&input.selected_suggestion_ids)
    .fetch_all(&mut **transaction)
    .await?
    .into_iter()
    .collect::<HashSet<_>>();
    if persisted_ids != requested_ids {
        return Err(OnboardingFlowRepositoryError::InvalidSuggestionSelection);
    }
    Ok(())
}

async fn lock_active_user(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
) -> Result<bool, OnboardingFlowRepositoryError> {
    sqlx::query_scalar::<_, bool>(
        r#"
        SELECT has_completed_new_user_tutorial
        FROM users
        WHERE id::bigint = $1::bigint AND is_active = TRUE
        FOR UPDATE
        "#,
    )
    .bind(user_id)
    .fetch_optional(&mut **transaction)
    .await?
    .ok_or(OnboardingFlowRepositoryError::UserMissingOrInactive)
}

async fn persist_config(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
    scraper_type: &str,
    display_name: Option<&str>,
    feed_url: &str,
    config: &Value,
) -> Result<i64, OnboardingFlowRepositoryError> {
    let canonical = canonicalize_feed_url(feed_url);
    let candidates = sqlx::query_as::<_, ExistingOnboardingFeedConfig>(
        r#"
        SELECT
            id::bigint AS id,
            scraper_type,
            feed_url,
            COALESCE(config, '{}'::json) AS config
        FROM user_scraper_configs
        WHERE user_id::bigint = $1::bigint AND scraper_type = $2
        ORDER BY id ASC
        FOR UPDATE
        "#,
    )
    .bind(user_id)
    .bind(scraper_type)
    .fetch_all(&mut **transaction)
    .await?;
    if let Some(existing) = candidates.into_iter().find(|candidate| {
        candidate
            .feed_url
            .as_deref()
            .or_else(|| candidate.config.get("feed_url").and_then(Value::as_str))
            .is_some_and(|stored| canonicalize_feed_url(stored) == canonical)
    }) {
        return Ok(existing.id);
    }

    let inserted = sqlx::query_scalar::<_, i64>(
        r#"
        INSERT INTO user_scraper_configs (
            user_id,
            scraper_type,
            display_name,
            feed_url,
            config,
            is_active,
            created_at,
            updated_at
        )
        VALUES (
            $1::bigint::integer,
            $2,
            $3,
            $4,
            $5,
            TRUE,
            timezone('UTC', now()),
            timezone('UTC', now())
        )
        ON CONFLICT (user_id, scraper_type, feed_url) DO NOTHING
        RETURNING id::bigint
        "#,
    )
    .bind(user_id)
    .bind(scraper_type)
    .bind(display_name)
    .bind(&canonical)
    .bind(config)
    .fetch_optional(&mut **transaction)
    .await?;
    if let Some(id) = inserted {
        return Ok(id);
    }
    sqlx::query_scalar::<_, i64>(
        r#"
        SELECT id::bigint
        FROM user_scraper_configs
        WHERE user_id::bigint = $1::bigint
          AND scraper_type = $2
          AND feed_url = $3
        ORDER BY id ASC
        LIMIT 1
        "#,
    )
    .bind(user_id)
    .bind(scraper_type)
    .bind(canonical)
    .fetch_optional(&mut **transaction)
    .await?
    .ok_or(OnboardingFlowRepositoryError::LostConfigRace)
}

async fn seed_recent_news(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
) -> Result<(), sqlx::Error> {
    sqlx::query(
        r#"
        WITH candidates AS (
            SELECT content.id
            FROM contents AS content
            WHERE content.content_type = 'news'
              AND content.status = 'completed'
              AND (content.classification IS NULL OR content.classification <> 'skip')
              AND NOT EXISTS (
                  SELECT 1
                  FROM content_status AS status
                  WHERE status.user_id::bigint = $1::bigint
                    AND status.content_id = content.id
              )
            ORDER BY content.created_at DESC
            LIMIT $2
        )
        INSERT INTO content_status (user_id, content_id, status, created_at, updated_at)
        SELECT
            $1::bigint::integer,
            candidates.id,
            'inbox',
            timezone('UTC', now()),
            timezone('UTC', now())
        FROM candidates
        ON CONFLICT (user_id, content_id) DO NOTHING
        "#,
    )
    .bind(user_id)
    .bind(NEWS_SEED_LIMIT)
    .execute(&mut **transaction)
    .await?;
    Ok(())
}

async fn seed_selected_feed_content(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
    feed_urls: &[&str],
) -> Result<Vec<i64>, sqlx::Error> {
    if feed_urls.is_empty() {
        return Ok(Vec::new());
    }
    sqlx::query_scalar::<_, i64>(
        r#"
        WITH candidates AS (
            SELECT content.id
            FROM contents AS content
            WHERE content.content_metadata::jsonb ->> 'feed_url' = ANY($2::text[])
              AND content.status = 'completed'
              AND content.content_type IN ('article', 'podcast')
              AND (content.classification IS NULL OR content.classification <> 'skip')
              AND NOT EXISTS (
                  SELECT 1
                  FROM content_status AS status
                  WHERE status.user_id::bigint = $1::bigint
                    AND status.content_id = content.id
              )
            ORDER BY content.created_at DESC
            LIMIT $3
        ), inserted AS (
            INSERT INTO content_status (user_id, content_id, status, created_at, updated_at)
            SELECT
                $1::bigint::integer,
                candidates.id,
                'inbox',
                timezone('UTC', now()),
                timezone('UTC', now())
            FROM candidates
            ON CONFLICT (user_id, content_id) DO NOTHING
            RETURNING content_id::bigint
        )
        SELECT content_id FROM inserted ORDER BY content_id
        "#,
    )
    .bind(user_id)
    .bind(feed_urls)
    .bind(FEED_CONTENT_SEED_LIMIT)
    .fetch_all(&mut **transaction)
    .await
}

async fn start_first_edition(
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

async fn has_feed_discovery_task(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
) -> Result<bool, sqlx::Error> {
    sqlx::query_scalar::<_, bool>(
        r#"
        SELECT EXISTS(
            SELECT 1
            FROM processing_tasks
            WHERE owner_user_id::bigint = $1::bigint
              AND task_type = 'discover_feeds'
              AND status IN ('pending', 'processing', 'completed')
        )
        "#,
    )
    .bind(user_id)
    .fetch_one(&mut **transaction)
    .await
}

async fn count_unread_inbox(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
) -> Result<i64, sqlx::Error> {
    sqlx::query_scalar::<_, i64>(
        r#"
        SELECT count(*)::bigint
        FROM contents AS content
        WHERE content.status = 'completed'
          AND (content.classification IS NULL OR content.classification <> 'skip')
          AND EXISTS (
              SELECT 1
              FROM content_status AS status
              WHERE status.user_id::bigint = $1::bigint
                AND status.content_id = content.id
                AND status.status = 'inbox'
          )
          AND NOT EXISTS (
              SELECT 1
              FROM content_read_status AS read
              WHERE read.user_id::bigint = $1::bigint
                AND read.content_id = content.id
          )
        "#,
    )
    .bind(user_id)
    .fetch_one(&mut **transaction)
    .await
}

#[derive(Debug, FromRow)]
struct ImageCandidateRow {
    id: i64,
    content_type: String,
    content_metadata: Value,
}

async fn eligible_image_content_ids(
    transaction: &mut Transaction<'_, Postgres>,
    content_ids: &[i64],
) -> Result<Vec<i64>, sqlx::Error> {
    if content_ids.is_empty() {
        return Ok(Vec::new());
    }
    let candidates = sqlx::query_as::<_, ImageCandidateRow>(
        r#"
        SELECT
            content.id::bigint AS id,
            content.content_type,
            COALESCE(content.content_metadata, '{}'::json) AS content_metadata
        FROM contents AS content
        WHERE content.id::bigint = ANY($1::bigint[])
          AND content.content_type IN ('article', 'podcast')
          AND content.status IN ('awaiting_image', 'completed')
          AND (content.classification IS NULL OR content.classification <> 'skip')
          AND NOT EXISTS (
              SELECT 1
              FROM processing_tasks AS task
              WHERE task.content_id = content.id
                AND task.task_type = 'generate_image'
                AND task.status IN ('pending', 'processing')
          )
        ORDER BY content.id
        "#,
    )
    .bind(content_ids)
    .fetch_all(&mut **transaction)
    .await?;
    Ok(candidates
        .into_iter()
        .filter(|candidate| {
            let metadata = runtime_metadata(&candidate.content_metadata);
            !metadata.get("image_generated_at").is_some_and(json_truthy)
                && summary_is_readable(&metadata, &candidate.content_type)
        })
        .map(|candidate| candidate.id)
        .collect())
}

fn runtime_metadata(value: &Value) -> serde_json::Map<String, Value> {
    let mut runtime = value.as_object().cloned().unwrap_or_default();
    for namespace in ["domain", "processing"] {
        if let Some(fields) = runtime.get(namespace).and_then(Value::as_object).cloned() {
            runtime.extend(fields);
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

fn summary_is_readable(metadata: &serde_json::Map<String, Value>, content_type: &str) -> bool {
    let Some(summary) = metadata.get("summary") else {
        return false;
    };
    if content_type == "podcast" {
        return json_truthy(summary);
    }
    let Some(summary) = summary.as_object() else {
        return json_truthy(summary);
    };
    if metadata.get("summary_kind").and_then(Value::as_str) == Some("longform_artifact")
        && summary.get("artifact").is_some_and(Value::is_object)
    {
        return summary.get("feed_preview").is_some_and(Value::is_object)
            || metadata.get("feed_preview").is_some_and(Value::is_object)
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

fn push_unique(values: &mut Vec<String>, seen: &mut HashSet<String>, value: &str) {
    let key = value.to_lowercase();
    if seen.insert(key) {
        values.push(value.to_owned());
    }
}

#[derive(Debug, Error)]
pub enum OnboardingFlowRepositoryError {
    #[error("onboarding database operation failed")]
    Sqlx(#[from] sqlx::Error),
    #[error("onboarding JSON projection failed")]
    Json(#[from] serde_json::Error),
    #[error("onboarding user is missing or inactive")]
    UserMissingOrInactive,
    #[error("onboarding discovery run was not found for this user")]
    DiscoveryRunNotFound,
    #[error("onboarding discovery run has not completed")]
    DiscoveryRunNotCompleted,
    #[error("onboarding suggestion selection does not belong to this discovery run")]
    InvalidSuggestionSelection,
    #[error("a concurrent onboarding source insert could not be resolved")]
    LostConfigRace,
}

#[cfg(test)]
mod tests {
    use sqlx::PgPool;

    use super::{
        OnboardingCompletionInput, OnboardingFlowRepositoryError,
        load_onboarding_completion_suggestions, validate_completion_selection,
    };

    async fn create_selection_test_schema(pool: &PgPool) {
        sqlx::query(
            r#"
            CREATE TABLE onboarding_discovery_runs (
                id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                user_id bigint NOT NULL,
                status text NOT NULL
            )
            "#,
        )
        .execute(pool)
        .await
        .expect("test discovery-run table should be created");
        sqlx::query(
            r#"
            CREATE TABLE onboarding_discovery_suggestions (
                id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                run_id bigint NOT NULL,
                user_id bigint NOT NULL,
                suggestion_type text NOT NULL,
                title text,
                feed_url text,
                subreddit text,
                status text NOT NULL
            )
            "#,
        )
        .execute(pool)
        .await
        .expect("test discovery-suggestion table should be created");
    }

    async fn insert_run(pool: &PgPool, user_id: i64, status: &str) -> i64 {
        sqlx::query_scalar::<_, i64>(
            r#"
            INSERT INTO onboarding_discovery_runs (user_id, status)
            VALUES ($1, $2)
            RETURNING id::bigint
            "#,
        )
        .bind(user_id)
        .bind(status)
        .fetch_one(pool)
        .await
        .expect("test discovery run should be inserted")
    }

    async fn insert_suggestion(pool: &PgPool, user_id: i64, run_id: i64) -> i64 {
        sqlx::query_scalar::<_, i64>(
            r#"
            INSERT INTO onboarding_discovery_suggestions (
                run_id,
                user_id,
                suggestion_type,
                feed_url,
                title,
                status
            )
            VALUES (
                $1,
                $2,
                'substack',
                'https://example.com/feed',
                'Example',
                'new'
            )
            RETURNING id::bigint
            "#,
        )
        .bind(run_id)
        .bind(user_id)
        .fetch_one(pool)
        .await
        .expect("test suggestion should be inserted")
    }

    fn completion_input(
        user_id: i64,
        run_id: i64,
        suggestion_ids: Vec<i64>,
    ) -> OnboardingCompletionInput {
        OnboardingCompletionInput {
            user_id,
            discovery_run_id: Some(run_id),
            selected_suggestion_ids: suggestion_ids,
            sources: Vec::new(),
            subreddits: Vec::new(),
            aggregators: Vec::new(),
            update_twitter_username: false,
            twitter_username: None,
        }
    }

    #[sqlx::test(migrations = false)]
    async fn completion_selection_is_scoped_to_owned_completed_run(pool: PgPool) {
        create_selection_test_schema(&pool).await;
        let owner_id = 7;
        let other_user_id = 8;
        let owned_run_id = insert_run(&pool, owner_id, "completed").await;
        let foreign_run_id = insert_run(&pool, other_user_id, "completed").await;
        let pending_run_id = insert_run(&pool, owner_id, "pending").await;
        let owned_suggestion_id = insert_suggestion(&pool, owner_id, owned_run_id).await;
        let foreign_suggestion_id = insert_suggestion(&pool, other_user_id, foreign_run_id).await;

        let selected = load_onboarding_completion_suggestions(
            &pool,
            owner_id,
            owned_run_id,
            &[owned_suggestion_id],
        )
        .await
        .expect("owned completed selection should resolve");
        assert_eq!(
            selected
                .iter()
                .map(|suggestion| suggestion.id)
                .collect::<Vec<_>>(),
            vec![owned_suggestion_id]
        );

        let foreign_run = load_onboarding_completion_suggestions(
            &pool,
            owner_id,
            foreign_run_id,
            &[foreign_suggestion_id],
        )
        .await;
        assert!(matches!(
            foreign_run,
            Err(OnboardingFlowRepositoryError::DiscoveryRunNotFound)
        ));

        let pending =
            load_onboarding_completion_suggestions(&pool, owner_id, pending_run_id, &[]).await;
        assert!(matches!(
            pending,
            Err(OnboardingFlowRepositoryError::DiscoveryRunNotCompleted)
        ));

        let cross_run = load_onboarding_completion_suggestions(
            &pool,
            owner_id,
            owned_run_id,
            &[foreign_suggestion_id],
        )
        .await;
        assert!(matches!(
            cross_run,
            Err(OnboardingFlowRepositoryError::InvalidSuggestionSelection)
        ));

        let mut transaction = pool.begin().await.expect("test transaction should start");
        let final_validation = validate_completion_selection(
            &mut transaction,
            &completion_input(owner_id, owned_run_id, vec![foreign_suggestion_id]),
        )
        .await;
        assert!(matches!(
            final_validation,
            Err(OnboardingFlowRepositoryError::InvalidSuggestionSelection)
        ));
    }
}
