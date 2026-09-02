#![allow(clippy::needless_raw_string_hashes)]

use std::collections::{HashMap, HashSet};

use chrono::{Duration, NaiveDateTime, Utc};
use newsly_domain::{
    NewsRelationDocument, RelationExactKey, aggregate_relation_representative,
    can_bridge_relation_clusters,
};
use newsly_queue::{EnqueueRequest, QueueError, QueueKernel, TaskType};
use serde_json::{Map, Value, json};
use sha2::{Digest, Sha256};
use sqlx::{FromRow, Postgres, Transaction};
use thiserror::Error;

use crate::content::UsageWrite;

use super::input::{
    article_title, choose_article_url, exact_relation_key, existing_summary, metadata_tweet_body,
    normalize_http_url, normalize_key_points, relation_search_query, relevant_links_json,
    summary_json, summary_title,
};
use super::model::{
    BodyPointer, BodySource, EnrichmentFinalizationPlan, EnrichmentMutation, EnrichmentPreparation,
    ModelUsageWrite, NewsApplyOutcome, NewsSnapshot, ProcessFinalizationPlan, ProcessMutation,
    ProcessPreparation, RelationCandidate,
};

const RELATED_LOOKBACK_DAYS: i64 = 14;
const MAX_RELATED_CANDIDATES: i64 = 150;
const SUPPORTED_AGGREGATORS: [&str; 7] = [
    "brutalist",
    "finurls",
    "hackernews",
    "mediagazer",
    "memeorandum",
    "sciurls",
    "techmeme",
];

#[derive(Debug, FromRow)]
struct NewsRow {
    id: i64,
    owner_user_id: Option<i64>,
    visibility_scope: String,
    platform: Option<String>,
    source_type: Option<String>,
    source_label: Option<String>,
    source_external_id: Option<String>,
    canonical_item_url: Option<String>,
    canonical_story_url: Option<String>,
    article_url: Option<String>,
    article_domain: Option<String>,
    discussion_url: Option<String>,
    summary_key_points: Value,
    summary_text: Option<String>,
    raw_metadata: Value,
    status: String,
    representative_news_item_id: Option<i64>,
    cluster_size: i32,
    ingested_at: NaiveDateTime,
}

#[derive(Debug, FromRow)]
struct ExistingContentRow {
    id: i64,
    url: String,
    source_url: Option<String>,
    content_metadata: Value,
    storage_provider: String,
    storage_key: String,
}

pub(super) async fn prepare_enrichment(
    transaction: &mut Transaction<'_, Postgres>,
    news_item_id: i64,
) -> Result<EnrichmentPreparation, NewsRepositoryError> {
    let Some(row) = load_news_row(transaction, news_item_id, true).await? else {
        return Ok(EnrichmentPreparation::NotFound);
    };
    let mut snapshot = snapshot_from_row(&row, BodySource::None);
    if snapshot
        .raw_metadata
        .get("article_body_ref")
        .is_some_and(Value::is_object)
    {
        return Ok(EnrichmentPreparation::Existing { snapshot });
    }
    if let Some((text, source_url)) = metadata_tweet_body(&snapshot) {
        return Ok(EnrichmentPreparation::Metadata {
            snapshot,
            text,
            source_url,
        });
    }
    let Some(article_url) = choose_article_url(&snapshot) else {
        return Ok(EnrichmentPreparation::Skip {
            snapshot,
            reason: "No outbound article URL to enrich".to_owned(),
        });
    };
    if let Some(content) = find_existing_content(transaction, &article_url).await? {
        snapshot.body_source = BodySource::Stored(BodyPointer {
            storage_provider: content.storage_provider,
            storage_key: content.storage_key,
        });
        return Ok(EnrichmentPreparation::Content {
            snapshot,
            content_id: content.id,
            final_url: normalize_http_url(&content.url)
                .or_else(|| content.source_url.as_deref().and_then(normalize_http_url)),
            source_metadata: content.content_metadata.get("source_metadata").cloned(),
        });
    }
    Ok(EnrichmentPreparation::Extract {
        snapshot,
        article_url,
    })
}

pub(super) async fn prepare_processing(
    transaction: &mut Transaction<'_, Postgres>,
    news_item_id: i64,
) -> Result<Option<ProcessPreparation>, NewsRepositoryError> {
    let Some(row) = load_news_row(transaction, news_item_id, true).await? else {
        return Ok(None);
    };
    let body_source = resolve_body_source(transaction, &row).await?;
    let snapshot = snapshot_from_row(&row, body_source);
    let local_summary = existing_summary(&snapshot);
    let (reusable_summary, reusable_representative_id) = if local_summary.is_some() {
        (local_summary, None)
    } else {
        find_reusable_exact_summary(transaction, &snapshot).await?
    };
    sqlx::query(
        r#"
        UPDATE news_items
        SET status = 'processing', updated_at = timezone('UTC', clock_timestamp())
        WHERE id::bigint = $1
        "#,
    )
    .bind(news_item_id)
    .execute(&mut **transaction)
    .await?;
    Ok(Some(ProcessPreparation {
        snapshot,
        reusable_summary,
        reusable_representative_id,
    }))
}

pub(super) async fn load_relation_candidates(
    transaction: &mut Transaction<'_, Postgres>,
    snapshot: &NewsSnapshot,
    item_document: &NewsRelationDocument,
) -> Result<Vec<RelationCandidate>, NewsRepositoryError> {
    let floor = (Utc::now() - Duration::days(RELATED_LOOKBACK_DAYS)).naive_utc();
    let exact_key = item_document.exact_relation_key.as_ref();
    let title_query = relation_search_query(item_document.primary_title.as_deref());
    let rows = sqlx::query_as::<_, NewsRow>(
        r#"
        WITH candidates AS (
            SELECT item.*,
                   1000000.0::real AS rank
            FROM news_items AS item
            WHERE item.status = 'ready'
              AND item.representative_news_item_id IS NULL
              AND item.id::bigint <> $1
              AND item.visibility_scope = $2
              AND item.owner_user_id IS NOT DISTINCT FROM $3::bigint::integer
              AND item.ingested_at >= $4
              AND (
                    ($5 = 'story' AND
                     COALESCE(item.canonical_story_url, item.article_url) IN ($6, $7, $8, $9))
                 OR ($5 = 'item' AND
                     COALESCE(item.canonical_item_url, item.discussion_url) IN ($6, $7, $8, $9))
                 OR ($5 = 'external' AND item.platform = $10 AND item.source_external_id = $11)
              )
            UNION ALL
            SELECT item.*,
                   ts_rank_cd(
                       setweight(to_tsvector('english', COALESCE(item.raw_metadata->'summary'->>'title', '')), 'A') ||
                       setweight(to_tsvector('english', COALESCE(item.raw_metadata->'article'->>'title', '')), 'A') ||
                       setweight(to_tsvector('english', COALESCE(item.raw_metadata->'cluster'->>'related_titles', '')), 'B'),
                       to_tsquery('english', $12)
                   ) AS rank
            FROM news_items AS item
            WHERE $12 <> ''
              AND item.status = 'ready'
              AND item.representative_news_item_id IS NULL
              AND item.id::bigint <> $1
              AND item.visibility_scope = $2
              AND item.owner_user_id IS NOT DISTINCT FROM $3::bigint::integer
              AND item.ingested_at >= $4
              AND (
                   setweight(to_tsvector('english', COALESCE(item.raw_metadata->'summary'->>'title', '')), 'A') ||
                   setweight(to_tsvector('english', COALESCE(item.raw_metadata->'article'->>'title', '')), 'A') ||
                   setweight(to_tsvector('english', COALESCE(item.raw_metadata->'cluster'->>'related_titles', '')), 'B')
              ) @@ to_tsquery('english', $12)
        ), deduplicated AS (
            SELECT DISTINCT ON (id) *
            FROM candidates
            ORDER BY id, rank DESC, ingested_at DESC
        )
        SELECT
            id::bigint AS id,
            owner_user_id::bigint AS owner_user_id,
            visibility_scope, platform, source_type, source_label, source_external_id,
            canonical_item_url, canonical_story_url, article_url, article_domain, discussion_url,
            COALESCE(summary_key_points, '[]'::json) AS summary_key_points,
            summary_text, COALESCE(raw_metadata, '{}'::json) AS raw_metadata,
            status, representative_news_item_id::bigint AS representative_news_item_id,
            cluster_size, ingested_at
        FROM deduplicated
        ORDER BY rank DESC, ingested_at DESC, id DESC
        LIMIT $13
        "#,
    )
    .bind(snapshot.id)
    .bind(&snapshot.visibility_scope)
    .bind(snapshot.owner_user_id)
    .bind(floor)
    .bind(exact_key.map_or("", |key| key.kind.as_str()))
    .bind(exact_url_variant(exact_key, 0))
    .bind(exact_url_variant(exact_key, 1))
    .bind(exact_url_variant(exact_key, 2))
    .bind(exact_url_variant(exact_key, 3))
    .bind(snapshot.platform.as_deref().unwrap_or(""))
    .bind(snapshot.source_external_id.as_deref().unwrap_or(""))
    .bind(title_query.as_deref().unwrap_or(""))
    .bind(MAX_RELATED_CANDIDATES)
    .fetch_all(&mut **transaction)
    .await?;

    Ok(rows
        .into_iter()
        .map(|row| {
            let snapshot = snapshot_from_row(&row, BodySource::None);
            let document = relation_document_from_snapshot(&snapshot);
            RelationCandidate {
                fingerprint: relation_fingerprint(&document),
                document,
            }
        })
        .collect())
}

pub(super) async fn apply_enrichment(
    transaction: &mut Transaction<'static, Postgres>,
    queue: &QueueKernel,
    plan: &EnrichmentFinalizationPlan,
) -> Result<NewsApplyOutcome, NewsRepositoryError> {
    persist_extraction_usage(
        transaction,
        plan.task_id,
        plan.snapshot.owner_user_id,
        plan.snapshot.id,
        &plan.usage,
    )
    .await?;
    let Some(row) = load_news_row(transaction, plan.snapshot.id, true).await? else {
        return Ok(NewsApplyOutcome::NewsItemMissing);
    };
    if source_fingerprint(&row) != plan.snapshot.fingerprint {
        return Ok(NewsApplyOutcome::SourceChanged);
    }
    let mut metadata = row.raw_metadata.as_object().cloned().unwrap_or_default();
    let (status, source, article_url, final_url, strategy, error, extracted_chars) =
        apply_enrichment_metadata(
            &mut metadata,
            &plan.snapshot,
            &plan.mutation,
            plan.finalized_at,
        );
    sqlx::query(
        r#"
        UPDATE news_items
        SET
            raw_metadata = $2,
            article_url = COALESCE($3, article_url),
            canonical_story_url = COALESCE(canonical_story_url, $3),
            article_domain = COALESCE(article_domain, $4),
            enrichment_updated_at = $5,
            updated_at = timezone('UTC', clock_timestamp())
        WHERE id::bigint = $1
        "#,
    )
    .bind(plan.snapshot.id)
    .bind(Value::Object(metadata))
    .bind(published_final_url(&plan.mutation))
    .bind(enrichment_domain(&plan.mutation))
    .bind(plan.finalized_at.naive_utc())
    .execute(&mut **transaction)
    .await?;
    let mut request = EnqueueRequest::new(TaskType::ProcessNewsItem);
    request.payload = Some(Map::from_iter([(
        "news_item_id".to_owned(),
        Value::from(plan.snapshot.id),
    )]));
    request.dedupe = Some(true);
    queue
        .enqueue_many_in_transaction(transaction, vec![request])
        .await?;
    tracing::info!(
        news_item_id = plan.snapshot.id,
        status,
        source = ?source,
        article_url = ?article_url,
        final_url = ?final_url,
        strategy = ?strategy,
        error = ?error,
        extracted_chars,
        "news article enrichment finalized"
    );
    Ok(NewsApplyOutcome::Applied)
}

pub(super) async fn apply_processing(
    transaction: &mut Transaction<'static, Postgres>,
    queue: &QueueKernel,
    plan: &ProcessFinalizationPlan,
) -> Result<NewsApplyOutcome, NewsRepositoryError> {
    if let Some(mutation) = &plan.mutation {
        for usage in &mutation.usage {
            persist_model_usage(transaction, plan, usage).await?;
        }
    }
    for usage in &plan.failure_usage {
        persist_model_usage(transaction, plan, usage).await?;
    }
    let Some(row) = load_news_row(transaction, plan.snapshot.id, true).await? else {
        return Ok(NewsApplyOutcome::NewsItemMissing);
    };
    if source_fingerprint(&row) != plan.snapshot.fingerprint {
        reset_processing_status_for_defer(transaction, plan.snapshot.id).await?;
        return Ok(NewsApplyOutcome::SourceChanged);
    }
    let Some(mutation) = &plan.mutation else {
        apply_processing_failure(transaction, plan).await?;
        return Ok(NewsApplyOutcome::Applied);
    };
    if !candidate_fingerprints_match(transaction, &mutation.candidates).await? {
        reset_processing_status_for_defer(transaction, plan.snapshot.id).await?;
        return Ok(NewsApplyOutcome::CandidateChanged);
    }
    tracing::info!(
        news_item_id = plan.snapshot.id,
        reused_summary = mutation.used_existing_summary,
        relation_trace = %mutation.relation_trace,
        "publishing processed news item"
    );
    persist_summary(transaction, plan, mutation).await?;
    let representative_id = reconcile_relation(transaction, plan, mutation).await?;
    if representative_id == plan.snapshot.id {
        if let Some(links) = &mutation.relevant_links
            && !links.is_empty()
        {
            sqlx::query(
                r#"
                UPDATE news_items
                SET raw_metadata = jsonb_set(
                        COALESCE(raw_metadata, '{}'::json)::jsonb,
                        '{article_relevant_links}', $2::jsonb, true
                    )::json,
                    updated_at = timezone('UTC', clock_timestamp())
                WHERE id::bigint = $1
                "#,
            )
            .bind(plan.snapshot.id)
            .bind(relevant_links_json(links))
            .execute(&mut **transaction)
            .await?;
        }
        enqueue_ready_fanout(transaction, queue, plan).await?;
    }
    Ok(NewsApplyOutcome::Applied)
}

async fn load_news_row(
    transaction: &mut Transaction<'_, Postgres>,
    news_item_id: i64,
    lock: bool,
) -> Result<Option<NewsRow>, sqlx::Error> {
    let query = if lock {
        r#"
        SELECT
            id::bigint AS id,
            owner_user_id::bigint AS owner_user_id,
            visibility_scope, platform, source_type, source_label, source_external_id,
            canonical_item_url, canonical_story_url, article_url, article_domain, discussion_url,
            COALESCE(summary_key_points, '[]'::json) AS summary_key_points,
            summary_text, COALESCE(raw_metadata, '{}'::json) AS raw_metadata,
            status, representative_news_item_id::bigint AS representative_news_item_id,
            cluster_size, ingested_at
        FROM news_items
        WHERE id::bigint = $1
        FOR UPDATE
        "#
    } else {
        r#"
        SELECT
            id::bigint AS id,
            owner_user_id::bigint AS owner_user_id,
            visibility_scope, platform, source_type, source_label, source_external_id,
            canonical_item_url, canonical_story_url, article_url, article_domain, discussion_url,
            COALESCE(summary_key_points, '[]'::json) AS summary_key_points,
            summary_text, COALESCE(raw_metadata, '{}'::json) AS raw_metadata,
            status, representative_news_item_id::bigint AS representative_news_item_id,
            cluster_size, ingested_at
        FROM news_items
        WHERE id::bigint = $1
        "#
    };
    sqlx::query_as::<_, NewsRow>(query)
        .bind(news_item_id)
        .fetch_optional(&mut **transaction)
        .await
}

async fn find_existing_content(
    transaction: &mut Transaction<'_, Postgres>,
    article_url: &str,
) -> Result<Option<ExistingContentRow>, sqlx::Error> {
    sqlx::query_as::<_, ExistingContentRow>(
        r#"
        SELECT
            content.id::bigint AS id,
            content.url,
            content.source_url,
            COALESCE(content.content_metadata, '{}'::json) AS content_metadata,
            body.storage_provider,
            body.storage_key
        FROM contents AS content
        JOIN content_bodies AS body
          ON body.content_id = content.id AND body.variant = 'source'
        WHERE content.content_type = 'article'
          AND (content.url = $1 OR content.source_url = $1)
        ORDER BY content.id
        LIMIT 1
        "#,
    )
    .bind(article_url)
    .fetch_optional(&mut **transaction)
    .await
}

async fn resolve_body_source(
    transaction: &mut Transaction<'_, Postgres>,
    row: &NewsRow,
) -> Result<BodySource, sqlx::Error> {
    if let Some(reference) = row
        .raw_metadata
        .get("article_body_ref")
        .and_then(Value::as_object)
    {
        match reference.get("kind").and_then(Value::as_str) {
            Some("inline") => {
                if let Some(text) = reference.get("text").and_then(Value::as_str) {
                    return Ok(BodySource::Inline(text.to_owned()));
                }
            }
            Some("storage") => {
                if let (Some(provider), Some(key)) = (
                    reference.get("storage_provider").and_then(Value::as_str),
                    reference.get("storage_key").and_then(Value::as_str),
                ) {
                    return Ok(BodySource::Stored(BodyPointer {
                        storage_provider: provider.to_owned(),
                        storage_key: key.to_owned(),
                    }));
                }
            }
            Some("content") => {
                if let Some(content_id) = reference.get("content_id").and_then(Value::as_i64)
                    && let Some(pointer) = content_body_pointer(transaction, content_id).await?
                {
                    return Ok(BodySource::Stored(pointer));
                }
            }
            _ => {}
        }
    }
    let Some(article_url) = row
        .article_url
        .as_deref()
        .or(row.canonical_story_url.as_deref())
        .and_then(normalize_http_url)
    else {
        return Ok(BodySource::None);
    };
    Ok(find_existing_content(transaction, &article_url)
        .await?
        .map_or(BodySource::None, |content| {
            BodySource::Stored(BodyPointer {
                storage_provider: content.storage_provider,
                storage_key: content.storage_key,
            })
        }))
}

async fn content_body_pointer(
    transaction: &mut Transaction<'_, Postgres>,
    content_id: i64,
) -> Result<Option<BodyPointer>, sqlx::Error> {
    sqlx::query_as::<_, (String, String)>(
        r#"
        SELECT body.storage_provider, body.storage_key
        FROM contents AS content
        JOIN content_bodies AS body
          ON body.content_id = content.id AND body.variant = 'source'
        WHERE content.id::bigint = $1 AND content.content_type = 'article'
        "#,
    )
    .bind(content_id)
    .fetch_optional(&mut **transaction)
    .await
    .map(|row| {
        row.map(|(storage_provider, storage_key)| BodyPointer {
            storage_provider,
            storage_key,
        })
    })
}

async fn find_reusable_exact_summary(
    transaction: &mut Transaction<'_, Postgres>,
    snapshot: &NewsSnapshot,
) -> Result<(Option<newsly_providers::NewsSummary>, Option<i64>), NewsRepositoryError> {
    let Some(key) = exact_relation_key(snapshot) else {
        return Ok((None, None));
    };
    let floor = (Utc::now() - Duration::days(RELATED_LOOKBACK_DAYS)).naive_utc();
    let rows = sqlx::query_as::<_, NewsRow>(
        r#"
        SELECT
            id::bigint AS id,
            owner_user_id::bigint AS owner_user_id,
            visibility_scope, platform, source_type, source_label, source_external_id,
            canonical_item_url, canonical_story_url, article_url, article_domain, discussion_url,
            COALESCE(summary_key_points, '[]'::json) AS summary_key_points,
            summary_text, COALESCE(raw_metadata, '{}'::json) AS raw_metadata,
            status, representative_news_item_id::bigint AS representative_news_item_id,
            cluster_size, ingested_at
        FROM news_items
        WHERE status = 'ready'
          AND representative_news_item_id IS NULL
          AND id::bigint <> $1
          AND visibility_scope = $2
          AND owner_user_id IS NOT DISTINCT FROM $3::bigint::integer
          AND ingested_at >= $4
          AND (
                ($5 = 'story' AND COALESCE(canonical_story_url, article_url) IN ($6, $7, $8, $9))
             OR ($5 = 'item' AND COALESCE(canonical_item_url, discussion_url) IN ($6, $7, $8, $9))
             OR ($5 = 'external' AND platform = $10 AND source_external_id = $11)
          )
        ORDER BY ingested_at DESC, id DESC
        "#,
    )
    .bind(snapshot.id)
    .bind(&snapshot.visibility_scope)
    .bind(snapshot.owner_user_id)
    .bind(floor)
    .bind(&key.kind)
    .bind(exact_url_variant(Some(&key), 0))
    .bind(exact_url_variant(Some(&key), 1))
    .bind(exact_url_variant(Some(&key), 2))
    .bind(exact_url_variant(Some(&key), 3))
    .bind(snapshot.platform.as_deref().unwrap_or(""))
    .bind(snapshot.source_external_id.as_deref().unwrap_or(""))
    .fetch_all(&mut **transaction)
    .await?;
    for row in rows {
        let candidate = snapshot_from_row(&row, BodySource::None);
        if exact_relation_key(&candidate).as_ref() != Some(&key) {
            continue;
        }
        if let Some(summary) = existing_summary(&candidate) {
            return Ok((Some(summary), Some(candidate.id)));
        }
    }
    Ok((None, None))
}

fn snapshot_from_row(row: &NewsRow, body_source: BodySource) -> NewsSnapshot {
    NewsSnapshot {
        id: row.id,
        owner_user_id: row.owner_user_id,
        visibility_scope: row.visibility_scope.clone(),
        platform: row.platform.clone(),
        source_type: row.source_type.clone(),
        source_label: row.source_label.clone(),
        source_external_id: row.source_external_id.clone(),
        canonical_item_url: row.canonical_item_url.clone(),
        canonical_story_url: row.canonical_story_url.clone(),
        article_url: row.article_url.clone(),
        article_domain: row.article_domain.clone(),
        discussion_url: row.discussion_url.clone(),
        summary_key_points: normalize_key_points(&row.summary_key_points),
        summary_text: row.summary_text.clone(),
        raw_metadata: row.raw_metadata.clone(),
        ingested_at: chrono::DateTime::from_naive_utc_and_offset(row.ingested_at, Utc),
        body_source,
        fingerprint: source_fingerprint(row),
    }
}

fn source_fingerprint(row: &NewsRow) -> String {
    stable_sha256(&json!({
        "id": row.id,
        "owner_user_id": row.owner_user_id,
        "visibility_scope": row.visibility_scope,
        "platform": row.platform,
        "source_type": row.source_type,
        "source_label": row.source_label,
        "source_external_id": row.source_external_id,
        "canonical_item_url": row.canonical_item_url,
        "canonical_story_url": row.canonical_story_url,
        "article_url": row.article_url,
        "article_domain": row.article_domain,
        "discussion_url": row.discussion_url,
        "summary_key_points": row.summary_key_points,
        "summary_text": row.summary_text,
        "raw_metadata": row.raw_metadata,
        "representative_news_item_id": row.representative_news_item_id,
        "cluster_size": row.cluster_size,
        "ingested_at": row.ingested_at,
    }))
}

fn relation_document_from_snapshot(snapshot: &NewsSnapshot) -> NewsRelationDocument {
    let metadata = snapshot.raw_metadata.as_object();
    let primary_title = metadata
        .and_then(|metadata| {
            metadata
                .get("cluster")
                .and_then(Value::as_object)
                .and_then(|cluster| cluster.get("related_titles"))
                .and_then(Value::as_array)
                .and_then(|titles| titles.first())
                .and_then(Value::as_str)
                .map(str::to_owned)
        })
        .or_else(|| metadata.and_then(summary_title))
        .or_else(|| metadata.and_then(article_title));
    let related_titles = metadata
        .and_then(|metadata| metadata.get("cluster"))
        .and_then(Value::as_object)
        .and_then(|cluster| cluster.get("related_titles"))
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .map(str::to_owned)
        .take(6)
        .collect();
    NewsRelationDocument {
        id: snapshot.id,
        primary_title,
        related_titles,
        summary_key_points: snapshot.summary_key_points.clone(),
        summary_text: snapshot.summary_text.clone(),
        article_domain: snapshot.article_domain.clone(),
        source_label: snapshot.source_label.clone(),
        platform: snapshot.platform.clone(),
        exact_relation_key: exact_relation_key(snapshot),
        ingested_at: Some(snapshot.ingested_at),
    }
}

fn relation_fingerprint(document: &NewsRelationDocument) -> String {
    stable_sha256(&serde_json::to_value(document).unwrap_or(Value::Null))
}

fn stable_sha256(value: &Value) -> String {
    let bytes = serde_json::to_vec(value).expect("JSON value serializes");
    Sha256::digest(bytes)
        .iter()
        .fold(String::with_capacity(64), |mut output, byte| {
            use std::fmt::Write as _;
            write!(output, "{byte:02x}").expect("writing to String cannot fail");
            output
        })
}

fn exact_url_variant(key: Option<&RelationExactKey>, index: usize) -> String {
    let Some(key) = key.filter(|key| matches!(key.kind.as_str(), "story" | "item")) else {
        return String::new();
    };
    let suffix = key.value.strip_prefix("https://").unwrap_or(&key.value);
    match index {
        0 => key.value.clone(),
        1 => format!("http://{suffix}"),
        2 => suffix.to_owned(),
        _ => format!("//{suffix}"),
    }
}

#[allow(clippy::too_many_lines, clippy::type_complexity)]
fn apply_enrichment_metadata(
    metadata: &mut Map<String, Value>,
    snapshot: &NewsSnapshot,
    mutation: &EnrichmentMutation,
    finalized_at: chrono::DateTime<Utc>,
) -> (
    &'static str,
    Option<&'static str>,
    Option<String>,
    Option<String>,
    Option<String>,
    Option<String>,
    i32,
) {
    let updated_at = finalized_at.to_rfc3339();
    let result = match mutation {
        EnrichmentMutation::Existing => {
            let url = metadata
                .get("article_body_ref")
                .and_then(Value::as_object)
                .and_then(|reference| {
                    reference
                        .get("final_url")
                        .or_else(|| reference.get("source_url"))
                })
                .and_then(Value::as_str)
                .and_then(normalize_http_url);
            let article_url = snapshot
                .article_url
                .clone()
                .or_else(|| snapshot.canonical_story_url.clone())
                .or_else(|| url.clone());
            (
                "completed",
                Some("existing"),
                article_url,
                url,
                None,
                None,
                0,
            )
        }
        EnrichmentMutation::Metadata { text, source_url } => {
            metadata.insert(
                "article_body_ref".to_owned(),
                json!({
                    "kind": "inline",
                    "text": text,
                    "source_url": source_url,
                    "updated_at": updated_at,
                }),
            );
            (
                "completed",
                Some("metadata"),
                source_url.clone(),
                source_url.clone(),
                None,
                None,
                i32::try_from(text.chars().count()).unwrap_or(i32::MAX),
            )
        }
        EnrichmentMutation::Content {
            content_id,
            article_url,
            final_url,
            extracted_chars,
            source_metadata,
        } => {
            metadata.insert(
                "article_body_ref".to_owned(),
                json!({
                    "kind": "content",
                    "content_id": content_id,
                    "variant": "source",
                    "source_url": article_url,
                    "updated_at": updated_at,
                }),
            );
            if let Some(source_metadata) =
                source_metadata.as_ref().filter(|value| value.is_object())
            {
                metadata.insert("source_metadata".to_owned(), source_metadata.clone());
            }
            (
                "completed",
                Some("content"),
                Some(article_url.clone()),
                final_url.clone(),
                None,
                None,
                *extracted_chars,
            )
        }
        EnrichmentMutation::Storage {
            article_url,
            final_url,
            title,
            extraction_method,
            body,
            ..
        } => {
            metadata.insert(
                "article_body_ref".to_owned(),
                json!({
                    "kind": "storage",
                    "storage_provider": body.storage_provider,
                    "storage_bucket": body.storage_bucket,
                    "storage_key": body.storage_key,
                    "content_format": body.content_format,
                    "sha256": body.sha256,
                    "byte_size": body.byte_size,
                    "char_count": body.char_count,
                    "source_url": article_url,
                    "final_url": final_url,
                    "updated_at": updated_at,
                }),
            );
            if let Some(title) = title {
                let article = metadata
                    .entry("article".to_owned())
                    .or_insert_with(|| Value::Object(Map::new()));
                if let Some(article) = article.as_object_mut()
                    && !article.get("title").is_some_and(Value::is_string)
                {
                    article.insert("title".to_owned(), Value::String(title.clone()));
                }
            }
            (
                "completed",
                Some("storage"),
                Some(article_url.clone()),
                Some(final_url.clone()),
                Some(extraction_method.clone()),
                None,
                body.char_count,
            )
        }
        EnrichmentMutation::Skipped {
            article_url,
            reason,
        } => (
            "skipped",
            None,
            article_url.clone(),
            article_url.clone(),
            None,
            Some(reason.clone()),
            0,
        ),
        EnrichmentMutation::Failed {
            article_url,
            final_url,
            strategy,
            reason,
        } => (
            "failed",
            None,
            article_url.clone(),
            final_url.clone(),
            strategy.clone(),
            Some(reason.clone()),
            0,
        ),
    };
    metadata.insert(
        "article_extraction".to_owned(),
        json!({
            "status": result.0,
            "source": result.1,
            "article_url": result.2,
            "final_url": result.3,
            "strategy": result.4,
            "error": result.5,
            "extracted_chars": result.6,
            "updated_at": updated_at,
        }),
    );
    result
}

fn enrichment_domain(mutation: &EnrichmentMutation) -> Option<&str> {
    match mutation {
        EnrichmentMutation::Storage { article_domain, .. } => article_domain.as_deref(),
        _ => None,
    }
}

fn published_final_url(mutation: &EnrichmentMutation) -> Option<&str> {
    match mutation {
        EnrichmentMutation::Storage { final_url, .. } => Some(final_url),
        _ => None,
    }
}

async fn reset_processing_status_for_defer(
    transaction: &mut Transaction<'static, Postgres>,
    news_item_id: i64,
) -> Result<(), sqlx::Error> {
    sqlx::query(
        r#"
        UPDATE news_items
        SET status = 'new', updated_at = timezone('UTC', clock_timestamp())
        WHERE id::bigint = $1 AND status = 'processing'
        "#,
    )
    .bind(news_item_id)
    .execute(&mut **transaction)
    .await?;
    Ok(())
}

async fn apply_processing_failure(
    transaction: &mut Transaction<'static, Postgres>,
    plan: &ProcessFinalizationPlan,
) -> Result<(), sqlx::Error> {
    let status = if plan.terminal_failure {
        "failed"
    } else {
        "new"
    };
    sqlx::query(
        r#"
        UPDATE news_items
        SET
            status = $2,
            raw_metadata = jsonb_set(
                COALESCE(raw_metadata, '{}'::json)::jsonb,
                '{processing_error}', to_jsonb($3::text), true
            )::json,
            processed_at = CASE WHEN $2 = 'failed' THEN $4 ELSE processed_at END,
            updated_at = timezone('UTC', clock_timestamp())
        WHERE id::bigint = $1
        "#,
    )
    .bind(plan.snapshot.id)
    .bind(status)
    .bind(plan.failure.as_deref().unwrap_or("News processing failed"))
    .bind(plan.finalized_at.naive_utc())
    .execute(&mut **transaction)
    .await?;
    Ok(())
}

async fn candidate_fingerprints_match(
    transaction: &mut Transaction<'static, Postgres>,
    candidates: &[RelationCandidate],
) -> Result<bool, NewsRepositoryError> {
    if candidates.is_empty() {
        return Ok(true);
    }
    let ids = candidates
        .iter()
        .map(|candidate| candidate.document.id)
        .collect::<Vec<_>>();
    let rows = sqlx::query_as::<_, NewsRow>(
        r#"
        SELECT
            id::bigint AS id,
            owner_user_id::bigint AS owner_user_id,
            visibility_scope, platform, source_type, source_label, source_external_id,
            canonical_item_url, canonical_story_url, article_url, article_domain, discussion_url,
            COALESCE(summary_key_points, '[]'::json) AS summary_key_points,
            summary_text, COALESCE(raw_metadata, '{}'::json) AS raw_metadata,
            status, representative_news_item_id::bigint AS representative_news_item_id,
            cluster_size, ingested_at
        FROM news_items
        WHERE id::bigint = ANY($1)
        ORDER BY id
        FOR UPDATE
        "#,
    )
    .bind(&ids)
    .fetch_all(&mut **transaction)
    .await?;
    if rows.len() != candidates.len() {
        return Ok(false);
    }
    let expected = candidates
        .iter()
        .map(|candidate| (candidate.document.id, candidate.fingerprint.as_str()))
        .collect::<HashMap<_, _>>();
    Ok(rows.iter().all(|row| {
        row.status == "ready"
            && row.representative_news_item_id.is_none()
            && expected.get(&row.id).is_some_and(|expected| {
                let snapshot = snapshot_from_row(row, BodySource::None);
                relation_fingerprint(&relation_document_from_snapshot(&snapshot)) == **expected
            })
    }))
}

async fn persist_summary(
    transaction: &mut Transaction<'static, Postgres>,
    plan: &ProcessFinalizationPlan,
    mutation: &ProcessMutation,
) -> Result<(), sqlx::Error> {
    let summary = &mutation.summary;
    let mut metadata = plan
        .snapshot
        .raw_metadata
        .as_object()
        .cloned()
        .unwrap_or_default();
    metadata.insert("summary".to_owned(), summary_json(summary));
    metadata.insert(
        "summary_kind".to_owned(),
        Value::String("short_news".to_owned()),
    );
    metadata.insert("summary_version".to_owned(), Value::from(1));
    metadata.remove("processing_error");
    let summary_section = metadata
        .entry("summary".to_owned())
        .or_insert_with(|| Value::Object(Map::new()));
    if let Some(section) = summary_section.as_object_mut() {
        section.insert("title".to_owned(), Value::String(summary.title.clone()));
    }
    sqlx::query(
        r#"
        UPDATE news_items
        SET
            summary_key_points = $2,
            summary_text = $3,
            raw_metadata = $4,
            article_url = COALESCE($5, article_url),
            canonical_story_url = COALESCE($5, canonical_story_url),
            status = 'ready',
            processed_at = $6,
            updated_at = timezone('UTC', clock_timestamp())
        WHERE id::bigint = $1
        "#,
    )
    .bind(plan.snapshot.id)
    .bind(serde_json::to_value(&summary.key_points).unwrap_or_else(|_| json!([])))
    .bind(&summary.summary)
    .bind(Value::Object(metadata))
    .bind(&summary.article_url)
    .bind(plan.finalized_at.naive_utc())
    .execute(&mut **transaction)
    .await?;
    Ok(())
}

async fn reconcile_relation(
    transaction: &mut Transaction<'static, Postgres>,
    plan: &ProcessFinalizationPlan,
    mutation: &ProcessMutation,
) -> Result<i64, NewsRepositoryError> {
    let by_id = mutation
        .candidates
        .iter()
        .map(|candidate| (candidate.document.id, &candidate.document))
        .collect::<HashMap<_, _>>();
    let representative_id = mutation
        .accepted_ids
        .first()
        .copied()
        .unwrap_or(plan.snapshot.id);
    let representative_document = if representative_id == plan.snapshot.id {
        &mutation.item_document
    } else {
        by_id.get(&representative_id).copied().ok_or(
            NewsRepositoryError::MissingAcceptedCandidate(representative_id),
        )?
    };
    let mut merged_representatives = Vec::new();
    for candidate_id in mutation.accepted_ids.iter().copied().skip(1) {
        let candidate = by_id
            .get(&candidate_id)
            .copied()
            .ok_or(NewsRepositoryError::MissingAcceptedCandidate(candidate_id))?;
        if can_bridge_relation_clusters(representative_document, candidate) {
            merged_representatives.push(candidate_id);
        }
    }
    for merged_id in &merged_representatives {
        sqlx::query(
            r#"
            UPDATE news_items
            SET representative_news_item_id = $2,
                updated_at = timezone('UTC', clock_timestamp())
            WHERE id::bigint = $1 OR representative_news_item_id::bigint = $1
            "#,
        )
        .bind(*merged_id)
        .bind(representative_id)
        .execute(&mut **transaction)
        .await?;
    }
    sqlx::query(
        r#"
        UPDATE news_items
        SET representative_news_item_id = CASE WHEN id::bigint = $2 THEN NULL ELSE $2 END,
            updated_at = timezone('UTC', clock_timestamp())
        WHERE id::bigint = $1
        "#,
    )
    .bind(plan.snapshot.id)
    .bind(representative_id)
    .execute(&mut **transaction)
    .await?;
    recompute_cluster(transaction, representative_id, plan.finalized_at).await?;
    Ok(representative_id)
}

#[allow(clippy::too_many_lines)]
async fn recompute_cluster(
    transaction: &mut Transaction<'static, Postgres>,
    representative_id: i64,
    finalized_at: chrono::DateTime<Utc>,
) -> Result<(), NewsRepositoryError> {
    let rows = sqlx::query_as::<_, NewsRow>(
        r#"
        SELECT
            id::bigint AS id,
            owner_user_id::bigint AS owner_user_id,
            visibility_scope, platform, source_type, source_label, source_external_id,
            canonical_item_url, canonical_story_url, article_url, article_domain, discussion_url,
            COALESCE(summary_key_points, '[]'::json) AS summary_key_points,
            summary_text, COALESCE(raw_metadata, '{}'::json) AS raw_metadata,
            status, representative_news_item_id::bigint AS representative_news_item_id,
            cluster_size, ingested_at
        FROM news_items
        WHERE id::bigint = $1 OR representative_news_item_id::bigint = $1
        ORDER BY ingested_at, id
        FOR UPDATE
        "#,
    )
    .bind(representative_id)
    .fetch_all(&mut **transaction)
    .await?;
    let representative = rows.iter().find(|row| row.id == representative_id).ok_or(
        NewsRepositoryError::MissingRepresentative(representative_id),
    )?;
    let documents = rows
        .iter()
        .map(|row| {
            let snapshot = snapshot_from_row(row, BodySource::None);
            relation_document_from_snapshot(&snapshot)
        })
        .collect::<Vec<_>>();
    let representative_document = documents
        .iter()
        .find(|document| document.id == representative_id)
        .expect("representative document follows row");
    let aggregate = aggregate_relation_representative(representative_document, &documents);
    let evidence = rows
        .iter()
        .max_by_key(|row| {
            let document = documents
                .iter()
                .find(|document| document.id == row.id)
                .expect("document follows row");
            (document_evidence_len(document), row.ingested_at, row.id)
        })
        .expect("cluster has representative");
    let cluster = cluster_payload(&rows, aggregate.primary_title.as_deref());
    let mut metadata = representative
        .raw_metadata
        .as_object()
        .cloned()
        .unwrap_or_default();
    metadata.insert("cluster".to_owned(), cluster);
    let size = i32::try_from(rows.len()).unwrap_or(i32::MAX);
    sqlx::query(
        r#"
        UPDATE news_items
        SET
            summary_text = COALESCE($2, summary_text),
            summary_key_points = CASE
                WHEN json_array_length($3::json) > 0 THEN $3::json
                ELSE summary_key_points
            END,
            article_url = COALESCE($4, article_url),
            canonical_story_url = COALESCE($5, canonical_story_url),
            article_domain = COALESCE($6, article_domain),
            raw_metadata = $7,
            representative_news_item_id = NULL,
            cluster_size = $8,
            enrichment_updated_at = $9,
            updated_at = timezone('UTC', clock_timestamp())
        WHERE id::bigint = $1
        "#,
    )
    .bind(representative_id)
    .bind(&aggregate.summary_text)
    .bind(serde_json::to_value(&aggregate.summary_key_points).unwrap_or_else(|_| json!([])))
    .bind(&evidence.article_url)
    .bind(&evidence.canonical_story_url)
    .bind(&aggregate.article_domain)
    .bind(Value::Object(metadata))
    .bind(size)
    .bind(finalized_at.naive_utc())
    .execute(&mut **transaction)
    .await?;
    sqlx::query(
        r#"
        UPDATE news_items
        SET
            representative_news_item_id = CASE WHEN id::bigint = $1 THEN NULL ELSE $1 END,
            cluster_size = $2,
            enrichment_updated_at = $3,
            updated_at = timezone('UTC', clock_timestamp())
        WHERE id::bigint = ANY($4)
        "#,
    )
    .bind(representative_id)
    .bind(size)
    .bind(finalized_at.naive_utc())
    .bind(rows.iter().map(|row| row.id).collect::<Vec<_>>())
    .execute(&mut **transaction)
    .await?;
    Ok(())
}

fn document_evidence_len(document: &NewsRelationDocument) -> usize {
    let title = document
        .primary_title
        .as_deref()
        .and_then(super::input::clean_string)
        .map(|title| format!("Title: {title}"));
    let provenance = [
        ("Domain", document.article_domain.as_deref()),
        ("Source surface", document.source_label.as_deref()),
        ("Platform", document.platform.as_deref()),
    ]
    .into_iter()
    .filter_map(|(label, value)| {
        value
            .and_then(super::input::clean_string)
            .map(|value| format!("{label}: {value}"))
    })
    .collect::<Vec<_>>()
    .join("\n");
    let key_points = document
        .summary_key_points
        .iter()
        .take(5)
        .filter_map(|point| super::input::clean_string(point))
        .collect::<Vec<_>>();
    let mut content = Vec::new();
    if !key_points.is_empty() {
        content.push(format!(
            "Key points:\n{}",
            key_points
                .iter()
                .map(|point| format!("- {point}"))
                .collect::<Vec<_>>()
                .join("\n")
        ));
    }
    if let Some(summary) = document
        .summary_text
        .as_deref()
        .and_then(super::input::clean_string)
    {
        content.push(format!("Summary: {summary}"));
    }
    [
        title,
        (!provenance.is_empty()).then_some(provenance),
        (!content.is_empty()).then(|| content.join("\n")),
    ]
    .into_iter()
    .flatten()
    .collect::<Vec<_>>()
    .join("\n")
    .len()
}

fn cluster_payload(rows: &[NewsRow], preferred_title: Option<&str>) -> Value {
    let mut source_labels = Vec::new();
    let mut domains = Vec::new();
    let mut discussion_snippets = Vec::new();
    let mut related_titles = Vec::new();
    let mut latest = None::<NaiveDateTime>;
    push_unique(&mut related_titles, preferred_title);
    for row in rows {
        push_unique(&mut source_labels, row.source_label.as_deref());
        push_unique(&mut domains, row.article_domain.as_deref());
        let metadata = row.raw_metadata.as_object();
        push_unique(
            &mut related_titles,
            metadata.and_then(summary_title).as_deref(),
        );
        push_unique(
            &mut related_titles,
            metadata.and_then(article_title).as_deref(),
        );
        if let Some(titles) = metadata
            .and_then(|metadata| metadata.get("cluster"))
            .and_then(Value::as_object)
            .and_then(|cluster| cluster.get("related_titles"))
            .and_then(Value::as_array)
        {
            for title in titles.iter().filter_map(Value::as_str) {
                push_unique(&mut related_titles, Some(title));
            }
        }
        if let Some(comment) = metadata
            .and_then(|metadata| metadata.get("top_comment"))
            .and_then(Value::as_object)
            .and_then(|comment| comment.get("text"))
            .and_then(Value::as_str)
        {
            push_unique(&mut discussion_snippets, Some(comment));
        }
        latest = Some(latest.map_or(row.ingested_at, |value| value.max(row.ingested_at)));
    }
    json!({
        "member_ids": rows.iter().map(|row| row.id).collect::<Vec<_>>(),
        "source_labels": source_labels,
        "domains": domains,
        "discussion_snippets": discussion_snippets.into_iter().take(5).collect::<Vec<_>>(),
        "related_titles": related_titles,
        "latest_member_ingested_at": latest.map(|value| value.to_string()),
    })
}

fn push_unique(output: &mut Vec<String>, value: Option<&str>) {
    let Some(value) = value
        .map(str::split_whitespace)
        .map(|parts| parts.collect::<Vec<_>>().join(" "))
        .filter(|value| !value.is_empty())
    else {
        return;
    };
    if !output.iter().any(|existing| existing == &value) {
        output.push(value);
    }
}

async fn enqueue_ready_fanout(
    transaction: &mut Transaction<'static, Postgres>,
    queue: &QueueKernel,
    plan: &ProcessFinalizationPlan,
) -> Result<(), NewsRepositoryError> {
    let visible_users = visible_user_ids(transaction, &plan.snapshot).await?;
    let visible_users = lock_active_users(transaction, &visible_users).await?;
    if visible_users.is_empty() {
        return Ok(());
    }
    let read_users = sqlx::query_scalar::<_, i64>(
        r#"
        SELECT user_id::bigint
        FROM news_item_read_status
        WHERE news_item_id::bigint = $1 AND user_id::bigint = ANY($2)
        "#,
    )
    .bind(plan.snapshot.id)
    .bind(&visible_users)
    .fetch_all(&mut **transaction)
    .await?
    .into_iter()
    .collect::<HashSet<_>>();
    let briefing_users = visible_users
        .iter()
        .copied()
        .filter(|user_id| !read_users.contains(user_id))
        .collect::<Vec<_>>();
    for user_id in &briefing_users {
        sqlx::query(
            r#"
            INSERT INTO briefing_pending_sources (
                user_id, lens_key, source_kind, source_id, enqueued_at
            )
            VALUES ($1, NULL, 'news', $2, timezone('UTC', clock_timestamp()))
            ON CONFLICT (user_id, source_kind, source_id) DO NOTHING
            "#,
        )
        .bind(*user_id)
        .bind(plan.snapshot.id)
        .execute(&mut **transaction)
        .await?;
    }
    let mut requests = Vec::new();
    let mut deadlines = Vec::new();
    for user_id in briefing_users {
        let pending_count = sqlx::query_scalar::<_, i64>(
            r#"
            SELECT count(*)::bigint
            FROM briefing_pending_sources
            WHERE user_id::bigint = $1 AND lens_key IS NULL
            "#,
        )
        .bind(user_id)
        .fetch_one(&mut **transaction)
        .await?;
        let delay = if pending_count >= plan.briefing_batch_minimum.max(1) {
            0
        } else {
            plan.briefing_debounce_seconds.max(0)
        };
        let available_at = Utc::now() + Duration::seconds(delay);
        let dedupe_key = format!("briefing_refresh:{user_id}:append");
        let mut request = EnqueueRequest::new(TaskType::BriefingRefresh);
        request.payload = Some(Map::from_iter([
            ("user_id".to_owned(), Value::from(user_id)),
            ("mode".to_owned(), Value::String("append".to_owned())),
        ]));
        request.owner_user_id = Some(user_id);
        request.dedupe = Some(true);
        request.dedupe_key = Some(dedupe_key.clone());
        request.available_at = Some(available_at);
        deadlines.push((dedupe_key, available_at));
        requests.push(request);
    }
    if !requests.is_empty() {
        queue
            .enqueue_many_in_transaction(transaction, requests)
            .await?;
    }
    for (dedupe_key, available_at) in deadlines {
        sqlx::query(
            r#"
            UPDATE processing_tasks
            SET available_at = LEAST(available_at, $2)
            WHERE dedupe_key = $1 AND status = 'pending'
            "#,
        )
        .bind(dedupe_key)
        .bind(available_at.naive_utc())
        .execute(&mut **transaction)
        .await?;
    }
    Ok(())
}

async fn lock_active_users(
    transaction: &mut Transaction<'static, Postgres>,
    user_ids: &[i64],
) -> Result<Vec<i64>, sqlx::Error> {
    if user_ids.is_empty() {
        return Ok(Vec::new());
    }
    sqlx::query_scalar::<_, i64>(
        r#"
        SELECT id::bigint
        FROM users
        WHERE id::bigint = ANY($1) AND is_active IS TRUE
        ORDER BY id
        FOR SHARE
        "#,
    )
    .bind(user_ids)
    .fetch_all(&mut **transaction)
    .await
}

async fn visible_user_ids(
    transaction: &mut Transaction<'static, Postgres>,
    snapshot: &NewsSnapshot,
) -> Result<Vec<i64>, sqlx::Error> {
    if snapshot.visibility_scope == "user" {
        let Some(owner_user_id) = snapshot.owner_user_id else {
            return Ok(Vec::new());
        };
        return sqlx::query_scalar::<_, i64>(
            "SELECT id::bigint FROM users WHERE id::bigint = $1 AND is_active IS TRUE",
        )
        .bind(owner_user_id)
        .fetch_optional(&mut **transaction)
        .await
        .map(|row| row.into_iter().collect());
    }
    if snapshot.visibility_scope != "global" {
        return Ok(Vec::new());
    }
    let platform = snapshot.platform.as_deref().unwrap_or_default();
    if !SUPPORTED_AGGREGATORS.contains(&platform.to_ascii_lowercase().as_str()) {
        return Ok(Vec::new());
    }
    let topic = snapshot
        .raw_metadata
        .pointer("/aggregator/topic")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_ascii_lowercase();
    sqlx::query_scalar::<_, i64>(
        r#"
        SELECT DISTINCT users.id::bigint
        FROM users
        JOIN user_scraper_configs AS config ON config.user_id = users.id
        WHERE users.is_active IS TRUE
          AND config.scraper_type = 'aggregator'
          AND config.is_active IS TRUE
          AND lower(COALESCE(config.config->>'key', '')) = lower($1)
          AND (
              lower($1) <> 'brutalist'
              OR jsonb_array_length(COALESCE(config.config::jsonb->'topics', '[]'::jsonb)) = 0
              OR EXISTS (
                  SELECT 1
                  FROM jsonb_array_elements_text(
                      COALESCE(config.config::jsonb->'topics', '[]'::jsonb)
                  ) AS selected(topic)
                  WHERE lower(btrim(selected.topic)) = $2
              )
          )
        ORDER BY users.id::bigint
        "#,
    )
    .bind(platform)
    .bind(topic)
    .fetch_all(&mut **transaction)
    .await
}

async fn persist_model_usage(
    transaction: &mut Transaction<'static, Postgres>,
    plan: &ProcessFinalizationPlan,
    write: &ModelUsageWrite,
) -> Result<(), sqlx::Error> {
    let total = write
        .usage
        .input_tokens
        .saturating_add(write.usage.output_tokens);
    sqlx::query(
        r#"
        INSERT INTO vendor_usage_records (
            provider, model, feature, operation, source, request_id, task_id, user_id,
            input_tokens, cache_read_tokens, cache_write_tokens, output_tokens,
            total_tokens, request_count, currency, metadata, created_at
        )
        VALUES (
            $1, $2, $3, $4, 'queue', $5, $6,
            (SELECT id FROM users WHERE id::bigint = $7 AND is_active IS TRUE),
            $8, $9, $10, $11, $12, $13, 'USD', $14,
            timezone('UTC', clock_timestamp())
        )
        "#,
    )
    .bind(&write.provider)
    .bind(&write.model)
    .bind(write.feature)
    .bind(write.operation)
    .bind(&write.provider_response_id)
    .bind(plan.task_id)
    .bind(plan.snapshot.owner_user_id)
    .bind(saturating_i32(write.usage.input_tokens))
    .bind(saturating_i32(write.usage.cached_input_tokens))
    .bind(saturating_i32(write.usage.cache_write_tokens))
    .bind(saturating_i32(write.usage.output_tokens))
    .bind(saturating_i32(total))
    .bind(saturating_i32(write.usage.request_count))
    .bind(&write.metadata)
    .execute(&mut **transaction)
    .await?;
    Ok(())
}

async fn persist_extraction_usage(
    transaction: &mut Transaction<'static, Postgres>,
    task_id: i64,
    user_id: Option<i64>,
    news_item_id: i64,
    writes: &[UsageWrite],
) -> Result<(), NewsRepositoryError> {
    for write in writes {
        match write {
            UsageWrite::Extraction(batch) => {
                for (index, event) in batch.events.iter().enumerate() {
                    insert_resource_usage(
                        transaction,
                        ResourceUsageInsert {
                            provider: "document_extractor",
                            model: extraction_method_name(batch.method),
                            feature: "document_extraction",
                            operation: extraction_intent_name(batch.intent),
                            request_id: &batch.request_id,
                            task_id,
                            user_id,
                            request_count: (index == 0).then_some(1),
                            resource_count: Some(i32::try_from(event.quantity).unwrap_or(i32::MAX)),
                            cost_usd: None,
                            pricing_version: None,
                            metadata: json!({
                                "news_item_id": news_item_id,
                                "kind": event.kind,
                                "quantity": event.quantity,
                                "unit": event.unit,
                            }),
                        },
                    )
                    .await?;
                }
            }
            UsageWrite::Firecrawl(usage) => {
                insert_resource_usage(
                    transaction,
                    ResourceUsageInsert {
                        provider: "firecrawl",
                        model: "scrape-v2",
                        feature: "html_extraction",
                        operation: "firecrawl_scrape",
                        request_id: &usage.request_id,
                        task_id,
                        user_id,
                        request_count: Some(1),
                        resource_count: Some(1),
                        cost_usd: usage.cost_usd,
                        pricing_version: Some("configured-v1"),
                        metadata: json!({
                            "news_item_id": news_item_id,
                            "url": usage.url,
                            "status_code": usage.status_code,
                        }),
                    },
                )
                .await?;
            }
            UsageWrite::Model(_) | UsageWrite::X(_) => {
                return Err(NewsRepositoryError::UnexpectedContentAnalysisUsage);
            }
        }
    }
    Ok(())
}

struct ResourceUsageInsert<'a> {
    provider: &'a str,
    model: &'a str,
    feature: &'a str,
    operation: &'a str,
    request_id: &'a str,
    task_id: i64,
    user_id: Option<i64>,
    request_count: Option<i32>,
    resource_count: Option<i32>,
    cost_usd: Option<f64>,
    pricing_version: Option<&'a str>,
    metadata: Value,
}

async fn insert_resource_usage(
    transaction: &mut Transaction<'static, Postgres>,
    write: ResourceUsageInsert<'_>,
) -> Result<(), sqlx::Error> {
    sqlx::query(
        r#"
        INSERT INTO vendor_usage_records (
            provider, model, feature, operation, source, request_id, task_id, user_id,
            request_count, resource_count, cost_usd, currency, pricing_version, metadata, created_at
        )
        VALUES (
            $1, $2, $3, $4, 'queue', $5, $6,
            (SELECT id FROM users WHERE id::bigint = $7 AND is_active IS TRUE),
            $8, $9, $10, 'USD', $11, $12, timezone('UTC', clock_timestamp())
        )
        "#,
    )
    .bind(write.provider)
    .bind(write.model)
    .bind(write.feature)
    .bind(write.operation)
    .bind(write.request_id)
    .bind(write.task_id)
    .bind(write.user_id)
    .bind(write.request_count)
    .bind(write.resource_count)
    .bind(write.cost_usd)
    .bind(write.pricing_version)
    .bind(write.metadata)
    .execute(&mut **transaction)
    .await?;
    Ok(())
}

fn extraction_intent_name(intent: newsly_extraction::ExtractIntent) -> &'static str {
    match intent {
        newsly_extraction::ExtractIntent::StaticAnalyze => "static_analyze",
        newsly_extraction::ExtractIntent::ExtractArticle => "extract_article",
        newsly_extraction::ExtractIntent::ResolvePubmed => "resolve_pubmed",
    }
}

fn extraction_method_name(method: Option<newsly_extraction::ExtractionMethod>) -> &'static str {
    match method {
        Some(newsly_extraction::ExtractionMethod::StaticReadability) => "static_readability",
        Some(newsly_extraction::ExtractionMethod::Crawl4ai) => "crawl4ai",
        None => "policy-v1",
    }
}

fn saturating_i32(value: u64) -> i32 {
    i32::try_from(value).unwrap_or(i32::MAX)
}

#[derive(Debug, Error)]
pub(super) enum NewsRepositoryError {
    #[error("content-analysis usage was passed to a news-item extraction finalizer")]
    UnexpectedContentAnalysisUsage,
    #[error("accepted relation candidate {0} was not loaded")]
    MissingAcceptedCandidate(i64),
    #[error("news representative {0} disappeared")]
    MissingRepresentative(i64),
    #[error(transparent)]
    Sqlx(#[from] sqlx::Error),
    #[error(transparent)]
    Queue(#[from] QueueError),
    #[error(transparent)]
    ContentSubmission(#[from] newsly_db::ContentSubmissionRepositoryError),
    #[error(transparent)]
    Json(#[from] serde_json::Error),
}
