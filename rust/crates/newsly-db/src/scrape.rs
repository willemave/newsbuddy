use std::collections::{BTreeMap, BTreeSet};

use chrono::{DateTime, Utc};
use serde_json::{Map, Value, json};
use sha2::{Digest, Sha256};
use sqlx::{FromRow, PgPool, Postgres, Transaction};
use thiserror::Error;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ScrapeConfigSnapshot {
    pub id: i64,
    pub user_id: i64,
    pub scraper_type: String,
    pub display_name: Option<String>,
    pub feed_url: Option<String>,
    pub config: Value,
    pub fingerprint: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PreparedScrapeSources {
    pub first_edition_user_id: Option<i64>,
    pub configs: Vec<ScrapeConfigSnapshot>,
}

#[derive(Debug, FromRow)]
struct ScrapeConfigRow {
    id: i64,
    user_id: i64,
    scraper_type: String,
    display_name: Option<String>,
    feed_url: Option<String>,
    config: Value,
}

/// Snapshot active scheduled-source configuration in a bounded transaction. When a first-edition
/// run is supplied, user-scoped providers are restricted to that run's active owner.
pub async fn prepare_scrape_sources(
    pool: &PgPool,
    first_edition_run_id: Option<i64>,
) -> Result<PreparedScrapeSources, ScrapeRepositoryError> {
    let mut transaction = pool.begin().await?;
    let first_edition_user_id = if let Some(run_id) = first_edition_run_id {
        sqlx::query_scalar::<_, i64>(
            r#"
            SELECT run.user_id::bigint
            FROM onboarding_first_edition_runs AS run
            JOIN users AS owner ON owner.id = run.user_id
            WHERE run.id::bigint = $1 AND run.status = 'active' AND owner.is_active IS TRUE
            FOR SHARE OF run, owner
            "#,
        )
        .bind(run_id)
        .fetch_optional(&mut *transaction)
        .await?
        .ok_or(ScrapeRepositoryError::FirstEditionRunUnavailable(run_id))?
        .into()
    } else {
        None
    };
    let rows = sqlx::query_as::<_, ScrapeConfigRow>(
        r#"
        SELECT
            config.id::bigint AS id,
            config.user_id::bigint AS user_id,
            config.scraper_type,
            config.display_name,
            config.feed_url,
            config.config::jsonb AS config
        FROM user_scraper_configs AS config
        JOIN users AS owner ON owner.id = config.user_id AND owner.is_active IS TRUE
        WHERE config.is_active IS TRUE
          AND config.scraper_type IN ('aggregator', 'reddit', 'substack', 'atom', 'podcast_rss')
          AND ($1::bigint IS NULL OR config.user_id::bigint = $1)
        ORDER BY config.id
        FOR SHARE OF config, owner
        "#,
    )
    .bind(first_edition_user_id)
    .fetch_all(&mut *transaction)
    .await?;
    transaction.commit().await?;
    let configs = rows
        .into_iter()
        .map(|row| ScrapeConfigSnapshot {
            id: row.id,
            user_id: row.user_id,
            scraper_type: row.scraper_type.clone(),
            display_name: row.display_name.clone(),
            feed_url: row.feed_url.clone(),
            config: row.config.clone(),
            fingerprint: config_fingerprint(&row),
        })
        .collect();
    Ok(PreparedScrapeSources {
        first_edition_user_id,
        configs,
    })
}

/// Re-lock source configs and return only exact snapshot matches. Results from disabled, deleted,
/// or edited user sources are discarded rather than leaking into a different audience.
pub async fn matching_scrape_config_ids(
    transaction: &mut Transaction<'_, Postgres>,
    snapshots: &[ScrapeConfigSnapshot],
) -> Result<BTreeSet<i64>, ScrapeRepositoryError> {
    if snapshots.is_empty() {
        return Ok(BTreeSet::new());
    }
    let ids = snapshots
        .iter()
        .map(|snapshot| snapshot.id)
        .collect::<Vec<_>>();
    let rows = sqlx::query_as::<_, ScrapeConfigRow>(
        r#"
        SELECT
            config.id::bigint AS id,
            config.user_id::bigint AS user_id,
            config.scraper_type,
            config.display_name,
            config.feed_url,
            config.config::jsonb AS config
        FROM user_scraper_configs AS config
        JOIN users AS owner ON owner.id = config.user_id AND owner.is_active IS TRUE
        WHERE config.id::bigint = ANY($1::bigint[]) AND config.is_active IS TRUE
        ORDER BY config.id
        FOR UPDATE OF config
        "#,
    )
    .bind(&ids)
    .fetch_all(&mut **transaction)
    .await?;
    let expected = snapshots
        .iter()
        .map(|snapshot| (snapshot.id, snapshot.fingerprint.as_str()))
        .collect::<BTreeMap<_, _>>();
    Ok(rows
        .iter()
        .filter(|row| {
            expected
                .get(&row.id)
                .is_some_and(|fingerprint| **fingerprint == config_fingerprint(row))
        })
        .map(|row| row.id)
        .collect())
}

#[derive(Debug, Clone, PartialEq)]
pub struct ScrapedContentRecord {
    pub url: String,
    pub source_url: String,
    pub title: Option<String>,
    pub content_type: String,
    pub user_id: i64,
    pub source: Option<String>,
    pub platform: String,
    pub metadata: Value,
    pub published_at: Option<DateTime<Utc>>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct PersistedContentRecord {
    pub content_id: i64,
    pub created: bool,
}

pub async fn persist_scraped_content(
    transaction: &mut Transaction<'_, Postgres>,
    record: &ScrapedContentRecord,
) -> Result<PersistedContentRecord, ScrapeRepositoryError> {
    let inserted = sqlx::query_scalar::<_, i64>(
        r#"
        INSERT INTO contents (
            content_type, url, source_url, title, source, platform, is_aggregate,
            status, error_message, retry_count, classification, content_metadata,
            created_at, updated_at, publication_date
        )
        VALUES (
            $1, $2, $3, $4, $5, $6, FALSE,
            'new', NULL, 0, NULL, $7,
            timezone('UTC', now()), timezone('UTC', now()), $8
        )
        ON CONFLICT (url, content_type) DO NOTHING
        RETURNING id::bigint
        "#,
    )
    .bind(&record.content_type)
    .bind(&record.url)
    .bind(&record.source_url)
    .bind(&record.title)
    .bind(&record.source)
    .bind(&record.platform)
    .bind(&record.metadata)
    .bind(record.published_at.map(|value| value.naive_utc()))
    .fetch_optional(&mut **transaction)
    .await?;
    let (content_id, created) = if let Some(content_id) = inserted {
        (content_id, true)
    } else {
        let content_id = sqlx::query_scalar::<_, i64>(
            r#"
            SELECT id::bigint FROM contents
            WHERE url = $1 AND content_type = $2
            ORDER BY id
            LIMIT 1
            FOR KEY SHARE
            "#,
        )
        .bind(&record.url)
        .bind(&record.content_type)
        .fetch_one(&mut **transaction)
        .await?;
        (content_id, false)
    };
    sqlx::query(
        r#"
        INSERT INTO content_status (user_id, content_id, status, created_at, updated_at)
        VALUES ($1::bigint::integer, $2::bigint::integer, 'inbox', timezone('UTC', now()), timezone('UTC', now()))
        ON CONFLICT (user_id, content_id) DO UPDATE
        SET status = 'inbox', updated_at = EXCLUDED.updated_at
        "#,
    )
    .bind(record.user_id)
    .bind(content_id)
    .execute(&mut **transaction)
    .await?;
    Ok(PersistedContentRecord {
        content_id,
        created,
    })
}

#[derive(Debug, Clone, PartialEq)]
pub struct ScrapedNewsRecord {
    pub visibility_scope: String,
    pub owner_user_id: Option<i64>,
    pub platform: String,
    pub source_type: String,
    pub source_label: Option<String>,
    pub source_external_id: Option<String>,
    pub user_scraper_config_id: Option<i64>,
    pub canonical_item_url: Option<String>,
    pub canonical_story_url: Option<String>,
    pub article_url: Option<String>,
    pub article_domain: Option<String>,
    pub discussion_url: Option<String>,
    pub article_title: Option<String>,
    pub summary_key_points: Vec<String>,
    pub summary_text: Option<String>,
    pub raw_metadata: Value,
    pub status: String,
    pub published_at: Option<DateTime<Utc>>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct PersistedNewsRecord {
    pub news_item_id: i64,
    pub created: bool,
    pub discussion_refresh_ready: bool,
}

pub async fn persist_scraped_news(
    transaction: &mut Transaction<'_, Postgres>,
    record: &ScrapedNewsRecord,
) -> Result<PersistedNewsRecord, ScrapeRepositoryError> {
    if !matches!(record.visibility_scope.as_str(), "global" | "user") {
        return Err(ScrapeRepositoryError::InvalidRecord(
            "news visibility_scope must be global or user",
        ));
    }
    if record.visibility_scope == "user" && record.owner_user_id.is_none() {
        return Err(ScrapeRepositoryError::InvalidRecord(
            "user-scoped news requires owner_user_id",
        ));
    }
    if !matches!(
        record.status.as_str(),
        "new" | "processing" | "ready" | "failed"
    ) {
        return Err(ScrapeRepositoryError::InvalidRecord(
            "news status is unsupported",
        ));
    }
    let ingest_key = news_ingest_key(record)?;
    let existing_id = find_existing_news(transaction, record, &ingest_key).await?;
    let (news_item_id, created) = if let Some(news_item_id) = existing_id {
        sqlx::query(
            r#"
            UPDATE news_items
            SET ingest_key = $2,
                visibility_scope = $3,
                owner_user_id = $4::bigint::integer,
                platform = $5,
                source_type = $6,
                source_label = $7,
                source_external_id = $8,
                user_scraper_config_id = $9::bigint::integer,
                canonical_item_url = COALESCE($10, canonical_item_url),
                canonical_story_url = COALESCE($11, canonical_story_url),
                article_url = COALESCE($12, article_url),
                article_domain = COALESCE($13, article_domain),
                discussion_url = COALESCE($14, discussion_url),
                summary_key_points = CASE
                    WHEN jsonb_array_length($15::jsonb) > 0 THEN $15::jsonb
                    ELSE summary_key_points::jsonb
                END::json,
                summary_text = COALESCE($16, summary_text),
                raw_metadata = (
                    COALESCE(raw_metadata, '{}'::json)::jsonb || $17::jsonb
                )::json,
                status = CASE WHEN status = 'ready' AND $18 <> 'ready' THEN status ELSE $18 END,
                published_at = COALESCE($19, published_at),
                updated_at = timezone('UTC', now())
            WHERE id::bigint = $1
            "#,
        )
        .bind(news_item_id)
        .bind(&ingest_key)
        .bind(&record.visibility_scope)
        .bind(record.owner_user_id)
        .bind(&record.platform)
        .bind(&record.source_type)
        .bind(&record.source_label)
        .bind(&record.source_external_id)
        .bind(record.user_scraper_config_id)
        .bind(&record.canonical_item_url)
        .bind(&record.canonical_story_url)
        .bind(&record.article_url)
        .bind(&record.article_domain)
        .bind(&record.discussion_url)
        .bind(json!(record.summary_key_points))
        .bind(&record.summary_text)
        .bind(&record.raw_metadata)
        .bind(&record.status)
        .bind(record.published_at.map(|value| value.naive_utc()))
        .execute(&mut **transaction)
        .await?;
        (news_item_id, false)
    } else {
        let news_item_id = sqlx::query_scalar::<_, i64>(
            r#"
            INSERT INTO news_items (
                ingest_key, visibility_scope, owner_user_id, platform, source_type,
                source_label, source_external_id, user_scraper_config_id,
                canonical_item_url, canonical_story_url, article_url, article_domain,
                discussion_url, summary_key_points, summary_text, raw_metadata, status,
                published_at, ingested_at, cluster_size, created_at, updated_at
            )
            VALUES (
                $1, $2, $3::bigint::integer, $4, $5,
                $6, $7, $8::bigint::integer,
                $9, $10, $11, $12,
                $13, $14, $15, $16, $17,
                $18, timezone('UTC', now()), 1, timezone('UTC', now()), timezone('UTC', now())
            )
            RETURNING id::bigint
            "#,
        )
        .bind(&ingest_key)
        .bind(&record.visibility_scope)
        .bind(record.owner_user_id)
        .bind(&record.platform)
        .bind(&record.source_type)
        .bind(&record.source_label)
        .bind(&record.source_external_id)
        .bind(record.user_scraper_config_id)
        .bind(&record.canonical_item_url)
        .bind(&record.canonical_story_url)
        .bind(&record.article_url)
        .bind(&record.article_domain)
        .bind(&record.discussion_url)
        .bind(json!(record.summary_key_points))
        .bind(&record.summary_text)
        .bind(&record.raw_metadata)
        .bind(&record.status)
        .bind(record.published_at.map(|value| value.naive_utc()))
        .fetch_one(&mut **transaction)
        .await?;
        (news_item_id, true)
    };
    let discussion_refresh_ready = sync_news_discussion(transaction, news_item_id, record).await?;
    Ok(PersistedNewsRecord {
        news_item_id,
        created,
        discussion_refresh_ready,
    })
}

async fn find_existing_news(
    transaction: &mut Transaction<'_, Postgres>,
    record: &ScrapedNewsRecord,
    ingest_key: &str,
) -> Result<Option<i64>, sqlx::Error> {
    if let Some(external_id) = record.source_external_id.as_deref() {
        let id = sqlx::query_scalar::<_, i64>(
            r#"
            SELECT id::bigint FROM news_items
            WHERE visibility_scope = $1
              AND owner_user_id IS NOT DISTINCT FROM $2::bigint::integer
              AND platform = $3 AND source_external_id = $4
            ORDER BY id LIMIT 1 FOR UPDATE
            "#,
        )
        .bind(&record.visibility_scope)
        .bind(record.owner_user_id)
        .bind(&record.platform)
        .bind(external_id)
        .fetch_optional(&mut **transaction)
        .await?;
        if id.is_some() {
            return Ok(id);
        }
    }
    for value in [
        record.canonical_item_url.as_deref(),
        record.discussion_url.as_deref(),
        record.canonical_story_url.as_deref(),
    ] {
        let Some(value) = value else { continue };
        let id = sqlx::query_scalar::<_, i64>(
            r#"
            SELECT id::bigint FROM news_items
            WHERE visibility_scope = $1
              AND owner_user_id IS NOT DISTINCT FROM $2::bigint::integer
              AND (canonical_item_url = $3 OR discussion_url = $3 OR canonical_story_url = $3)
            ORDER BY id LIMIT 1 FOR UPDATE
            "#,
        )
        .bind(&record.visibility_scope)
        .bind(record.owner_user_id)
        .bind(value)
        .fetch_optional(&mut **transaction)
        .await?;
        if id.is_some() {
            return Ok(id);
        }
    }
    sqlx::query_scalar::<_, i64>(
        "SELECT id::bigint FROM news_items WHERE ingest_key = $1 FOR UPDATE",
    )
    .bind(ingest_key)
    .fetch_optional(&mut **transaction)
    .await
}

async fn sync_news_discussion(
    transaction: &mut Transaction<'_, Postgres>,
    news_item_id: i64,
    record: &ScrapedNewsRecord,
) -> Result<bool, sqlx::Error> {
    if !matches!(record.platform.as_str(), "hackernews" | "reddit")
        || record.discussion_url.is_none()
        || record.source_external_id.is_none()
    {
        return Ok(false);
    }
    let aggregator = record.raw_metadata.get("aggregator");
    let author = aggregator
        .and_then(|value| value.get("author"))
        .and_then(Value::as_str);
    let details = aggregator.and_then(|value| value.get("metadata"));
    let score = details
        .and_then(|value| value.get("score"))
        .and_then(Value::as_i64)
        .and_then(|value| i32::try_from(value).ok());
    let comment_count = record
        .raw_metadata
        .get("comment_count")
        .and_then(Value::as_i64)
        .or_else(|| {
            details
                .and_then(|value| value.get("comments_count"))
                .and_then(Value::as_i64)
        })
        .and_then(|value| i32::try_from(value).ok());
    sqlx::query(
        r#"
        INSERT INTO news_item_discussions (
            news_item_id, platform, external_id, discussion_url, title, author,
            score, comment_count, last_count_checked_at, summary_status,
            summary_incremental_update_count, last_refresh_status, created_at, updated_at
        )
        VALUES (
            $1::bigint::integer, $2, $3, $4, $5, $6,
            $7, $8, timezone('UTC', now()), 'not_ready',
            0, 'pending', timezone('UTC', now()), timezone('UTC', now())
        )
        ON CONFLICT (news_item_id) DO UPDATE
        SET platform = EXCLUDED.platform,
            external_id = EXCLUDED.external_id,
            discussion_url = EXCLUDED.discussion_url,
            title = COALESCE(EXCLUDED.title, news_item_discussions.title),
            author = COALESCE(EXCLUDED.author, news_item_discussions.author),
            score = COALESCE(EXCLUDED.score, news_item_discussions.score),
            comment_count = COALESCE(EXCLUDED.comment_count, news_item_discussions.comment_count),
            last_count_checked_at = EXCLUDED.last_count_checked_at,
            updated_at = EXCLUDED.updated_at
        "#,
    )
    .bind(news_item_id)
    .bind(&record.platform)
    .bind(&record.source_external_id)
    .bind(&record.discussion_url)
    .bind(&record.article_title)
    .bind(author)
    .bind(score)
    .bind(comment_count)
    .execute(&mut **transaction)
    .await?;
    Ok(record.status == "ready")
}

/// Backfill missing supported discussion rows and return the highest-priority due refreshes.
pub async fn due_discussion_refresh_ids(
    transaction: &mut Transaction<'_, Postgres>,
    limit: i64,
) -> Result<Vec<i64>, ScrapeRepositoryError> {
    let limit = limit.clamp(1, 1_000);
    sqlx::query(
        r#"
        INSERT INTO news_item_discussions (
            news_item_id, platform, external_id, discussion_url, title,
            summary_status, summary_incremental_update_count, last_refresh_status,
            created_at, updated_at
        )
        SELECT
            item.id,
            CASE WHEN item.platform = 'hn' THEN 'hackernews' ELSE item.platform END,
            item.source_external_id,
            COALESCE(item.discussion_url, item.canonical_item_url),
            COALESCE(item.raw_metadata #>> '{summary,title}', item.raw_metadata #>> '{article,title}'),
            'not_ready', 0, 'pending', timezone('UTC', now()), timezone('UTC', now())
        FROM news_items AS item
        WHERE item.status = 'ready'
          AND item.representative_news_item_id IS NULL
          AND item.platform IN ('hackernews', 'hn', 'reddit')
          AND item.source_external_id IS NOT NULL
          AND COALESCE(item.discussion_url, item.canonical_item_url) IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM news_item_discussions AS existing
              WHERE existing.news_item_id = item.id
          )
          AND (
              (item.visibility_scope = 'user' AND EXISTS (
                  SELECT 1 FROM users AS owner
                  WHERE owner.id = item.owner_user_id AND owner.is_active IS TRUE
              ))
              OR
              (item.visibility_scope = 'global' AND EXISTS (
                  SELECT 1
                  FROM user_scraper_configs AS config
                  JOIN users AS owner ON owner.id = config.user_id AND owner.is_active IS TRUE
                  WHERE config.is_active IS TRUE
                    AND config.scraper_type = 'aggregator'
                    AND lower(config.config::jsonb ->> 'key') = lower(item.platform)
              ))
          )
        ORDER BY COALESCE(item.published_at, item.ingested_at, item.created_at) DESC
        LIMIT $1
        ON CONFLICT (news_item_id) DO NOTHING
        "#,
    )
    .bind(limit.saturating_mul(2))
    .execute(&mut **transaction)
    .await?;
    Ok(sqlx::query_scalar::<_, i64>(
        r#"
        SELECT discussion.news_item_id::bigint
        FROM news_item_discussions AS discussion
        JOIN news_items AS item ON item.id = discussion.news_item_id
        WHERE discussion.platform IN ('hackernews', 'reddit')
          AND discussion.external_id IS NOT NULL
          AND discussion.discussion_url IS NOT NULL
          AND discussion.last_refresh_status NOT IN ('gone', 'unsupported')
          AND (discussion.next_refresh_after IS NULL OR discussion.next_refresh_after <= timezone('UTC', now()))
          AND item.status = 'ready'
          AND item.representative_news_item_id IS NULL
          AND (
              (item.visibility_scope = 'user' AND EXISTS (
                  SELECT 1 FROM users AS owner
                  WHERE owner.id = item.owner_user_id AND owner.is_active IS TRUE
              ))
              OR
              (item.visibility_scope = 'global' AND EXISTS (
                  SELECT 1
                  FROM user_scraper_configs AS config
                  JOIN users AS owner ON owner.id = config.user_id AND owner.is_active IS TRUE
                  WHERE config.is_active IS TRUE
                    AND config.scraper_type = 'aggregator'
                    AND lower(config.config::jsonb ->> 'key') = lower(item.platform)
              ))
          )
        ORDER BY
            CASE WHEN discussion.summary IS NULL OR discussion.summary_status <> 'completed'
                OR discussion.raw_comments_ref IS NULL THEN 1 ELSE 0 END DESC,
            (COALESCE(discussion.comment_count, 0) - COALESCE(discussion.fetched_comment_count, 0)) DESC,
            COALESCE(item.published_at, item.processed_at, item.ingested_at, item.created_at) DESC,
            discussion.next_refresh_after NULLS FIRST,
            discussion.updated_at
        LIMIT $1
        "#,
    )
    .bind(limit)
    .fetch_all(&mut **transaction)
    .await?)
}

pub async fn record_first_edition_scrape_result(
    transaction: &mut Transaction<'_, Postgres>,
    run_id: i64,
    source: &str,
    success: bool,
    processed_count: i64,
    processed_by_config_id: &BTreeMap<i64, i64>,
) -> Result<bool, ScrapeRepositoryError> {
    let run = sqlx::query_scalar::<_, i64>(
        r#"
        SELECT id::bigint FROM onboarding_first_edition_runs
        WHERE id::bigint = $1 AND status = 'active'
        FOR UPDATE
        "#,
    )
    .bind(run_id)
    .fetch_optional(&mut **transaction)
    .await?;
    if run.is_none() {
        return Ok(false);
    }
    let normalized = normalize_source_name(source);
    let rows = sqlx::query_as::<_, FirstEditionSourceRow>(
        r#"
        SELECT id::bigint AS id, source_key, source_kind, status, processed_item_count
        FROM onboarding_first_edition_sources
        WHERE run_id::bigint = $1 AND source_kind IN ('aggregator', 'reddit')
        ORDER BY id
        FOR UPDATE
        "#,
    )
    .bind(run_id)
    .fetch_all(&mut **transaction)
    .await?;
    let outcome = if success { "processed" } else { "unavailable" };
    let mut matched = 0_i64;
    let mut changed = false;
    for row in rows {
        let count = if row.source_kind == "reddit" && normalized == "reddit" {
            let Some(config_id) = row
                .source_key
                .strip_prefix("reddit:")
                .and_then(|value| value.parse::<i64>().ok())
            else {
                continue;
            };
            processed_by_config_id.get(&config_id).copied().unwrap_or(0)
        } else if row.source_kind == "aggregator"
            && row
                .source_key
                .strip_prefix("scraper:")
                .is_some_and(|key| normalize_source_name(key) == normalized)
        {
            processed_count
        } else {
            continue;
        };
        matched += 1;
        if row.status == "processed"
            || (row.status == outcome && i64::from(row.processed_item_count) == count)
        {
            continue;
        }
        sqlx::query(
            r#"
            UPDATE onboarding_first_edition_sources
            SET status = $1,
                processed_item_count = $2,
                completed_at = COALESCE(completed_at, timezone('UTC', now()))
            WHERE id::bigint = $3
            "#,
        )
        .bind(outcome)
        .bind(i32::try_from(count.max(0)).unwrap_or(i32::MAX))
        .bind(row.id)
        .execute(&mut **transaction)
        .await?;
        changed = true;
    }
    if changed {
        sqlx::query(
            "UPDATE onboarding_first_edition_runs SET revision = revision + 1 WHERE id::bigint = $1",
        )
        .bind(run_id)
        .execute(&mut **transaction)
        .await?;
    }
    Ok(matched > 0)
}

#[derive(Debug, FromRow)]
struct FirstEditionSourceRow {
    id: i64,
    source_key: String,
    source_kind: String,
    status: String,
    processed_item_count: i32,
}

fn news_ingest_key(record: &ScrapedNewsRecord) -> Result<String, ScrapeRepositoryError> {
    let mut material = Map::new();
    material.insert(
        "visibility_scope".to_owned(),
        Value::String(record.visibility_scope.clone()),
    );
    material.insert(
        "owner_user_id".to_owned(),
        record.owner_user_id.map_or(Value::Null, Value::from),
    );
    if let Some(external_id) = record.source_external_id.as_deref() {
        material.insert(
            "identity_type".to_owned(),
            Value::String("platform_source_external_id".to_owned()),
        );
        material.insert(
            "platform".to_owned(),
            Value::String(record.platform.clone()),
        );
        material.insert(
            "source_external_id".to_owned(),
            Value::String(external_id.to_owned()),
        );
    } else if let Some(url) = record.canonical_item_url.as_deref() {
        material.insert(
            "identity_type".to_owned(),
            Value::String("canonical_item_url".to_owned()),
        );
        material.insert(
            "canonical_item_url".to_owned(),
            Value::String(url.to_owned()),
        );
    } else if let Some(url) = record.discussion_url.as_deref() {
        material.insert(
            "identity_type".to_owned(),
            Value::String("discussion_url".to_owned()),
        );
        material.insert("discussion_url".to_owned(), Value::String(url.to_owned()));
    } else if let Some(url) = record.canonical_story_url.as_deref() {
        material.insert(
            "identity_type".to_owned(),
            Value::String("canonical_story_url".to_owned()),
        );
        material.insert(
            "canonical_story_url".to_owned(),
            Value::String(url.to_owned()),
        );
    } else {
        material.insert(
            "identity_type".to_owned(),
            Value::String("title_url_fallback".to_owned()),
        );
        material.insert(
            "platform".to_owned(),
            Value::String(record.platform.clone()),
        );
        material.insert(
            "source_type".to_owned(),
            Value::String(record.source_type.clone()),
        );
        material.insert(
            "article_title".to_owned(),
            record
                .article_title
                .clone()
                .map_or(Value::Null, Value::String),
        );
        material.insert(
            "article_url".to_owned(),
            record
                .article_url
                .clone()
                .map_or(Value::Null, Value::String),
        );
    }
    let encoded = serde_json::to_vec(&material)?;
    Ok(hex_sha256(&encoded))
}

fn config_fingerprint(row: &ScrapeConfigRow) -> String {
    let value = json!({
        "id": row.id,
        "user_id": row.user_id,
        "scraper_type": row.scraper_type,
        "display_name": row.display_name,
        "feed_url": row.feed_url,
        "config": row.config,
    });
    hex_sha256(&serde_json::to_vec(&value).expect("JSON values always serialize"))
}

fn hex_sha256(value: &[u8]) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let digest = Sha256::digest(value);
    let mut output = String::with_capacity(digest.len() * 2);
    for byte in digest {
        output.push(char::from(HEX[usize::from(byte >> 4)]));
        output.push(char::from(HEX[usize::from(byte & 0x0f)]));
    }
    output
}

fn normalize_source_name(value: &str) -> String {
    value
        .trim()
        .to_ascii_lowercase()
        .chars()
        .filter(char::is_ascii_alphanumeric)
        .collect()
}

#[derive(Debug, Error)]
pub enum ScrapeRepositoryError {
    #[error("scrape repository query failed")]
    Database(#[from] sqlx::Error),
    #[error("first-edition run {0} is missing, inactive, or owned by an inactive user")]
    FirstEditionRunUnavailable(i64),
    #[error("scrape record is invalid: {0}")]
    InvalidRecord(&'static str),
    #[error("scrape identity serialization failed")]
    Json(#[from] serde_json::Error),
}

impl ScrapeRepositoryError {
    pub const fn retryable(&self) -> bool {
        matches!(self, Self::Database(_))
    }
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::{ScrapedNewsRecord, news_ingest_key};

    fn record() -> ScrapedNewsRecord {
        ScrapedNewsRecord {
            visibility_scope: "global".to_owned(),
            owner_user_id: None,
            platform: "hackernews".to_owned(),
            source_type: "Hacker News".to_owned(),
            source_label: Some("example.com".to_owned()),
            source_external_id: Some("123".to_owned()),
            user_scraper_config_id: None,
            canonical_item_url: Some("https://news.ycombinator.com/item?id=123".to_owned()),
            canonical_story_url: Some("https://example.com/story".to_owned()),
            article_url: Some("https://example.com/story".to_owned()),
            article_domain: Some("example.com".to_owned()),
            discussion_url: Some("https://news.ycombinator.com/item?id=123".to_owned()),
            article_title: Some("Story".to_owned()),
            summary_key_points: Vec::new(),
            summary_text: None,
            raw_metadata: json!({}),
            status: "new".to_owned(),
            published_at: None,
        }
    }

    #[test]
    fn ingest_key_prefers_platform_external_identity() {
        let first = news_ingest_key(&record()).expect("identity should serialize");
        let mut changed = record();
        changed.article_url = Some("https://mirror.example/story".to_owned());
        assert_eq!(
            first,
            news_ingest_key(&changed).expect("identity should serialize")
        );
    }
}
