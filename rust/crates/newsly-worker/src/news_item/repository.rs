#![allow(clippy::needless_raw_string_hashes)]

use std::collections::{HashMap, HashSet};

use chrono::{Duration, NaiveDateTime, Utc};
use newsly_domain::{
    AggregatorKey, NewsRelationDocument, RelationExactKey, aggregate_relation_representative,
    can_bridge_relation_clusters,
};
use newsly_queue::{EnqueueRequest, QueueKernel, TaskType};
use serde_json::{Map, Value, json};
use sha2::{Digest, Sha256};
use sqlx::{FromRow, Postgres, Transaction};

use crate::content::{
    UsageInsert, UsageWrite, insert_usage, normalize_external_url as normalize_http_url,
};

use super::input::{
    article_title, choose_article_url, exact_relation_key, existing_summary, metadata_tweet_body,
    normalize_key_points, prefilter_relation_candidates, relation_search_query,
    relevant_links_json, summary_json, summary_title,
};
use super::model::{
    BodyPointer, BodySource, EnrichmentFinalizationPlan, EnrichmentMutation, EnrichmentPreparation,
    NewsApplyOutcome, NewsSnapshot, ProcessFinalizationPlan, ProcessMutation, ProcessPreparation,
    RelationCandidate,
};

const RELATED_LOOKBACK_DAYS: i64 = 14;
const MAX_RELATED_CANDIDATES: i64 = 150;

macro_rules! news_row_columns {
    () => {
        r#"
            id::bigint AS id,
            owner_user_id::bigint AS owner_user_id,
            visibility_scope, platform, source_type, source_label, source_external_id,
            canonical_item_url, canonical_story_url, article_url, article_domain, discussion_url,
            COALESCE(summary_key_points, '[]'::json) AS summary_key_points,
            summary_text, COALESCE(raw_metadata, '{}'::json) AS raw_metadata,
            status, representative_news_item_id::bigint AS representative_news_item_id,
            cluster_size, ingested_at
        "#
    };
}

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
    let Some(row) = load_news_row(transaction, news_item_id).await? else {
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
    let demand_policy = firecrawl_demand_policy(transaction, &row).await?;
    if let Some(content) = find_existing_content(transaction, &article_url).await? {
        snapshot.body_source = BodySource::Stored(BodyPointer {
            storage_provider: content.storage_provider,
            storage_key: content.storage_key,
        });
        return Ok(EnrichmentPreparation::Content {
            snapshot,
            demand_policy,
            content_id: content.id,
            final_url: normalize_http_url(&content.url)
                .or_else(|| content.source_url.as_deref().and_then(normalize_http_url)),
            source_metadata: content.content_metadata.get("source_metadata").cloned(),
        });
    }
    Ok(EnrichmentPreparation::Extract {
        snapshot,
        demand_policy,
        article_url,
    })
}

async fn firecrawl_demand_policy(
    transaction: &mut Transaction<'_, Postgres>,
    row: &NewsRow,
) -> Result<crate::content::FirecrawlDemandPolicy, sqlx::Error> {
    use crate::content::FirecrawlDemandPolicy;
    let Some(key) = row.platform.as_deref().and_then(AggregatorKey::from_key) else {
        return Ok(FirecrawlDemandPolicy::Allowed);
    };
    if row.visibility_scope != "global" || row.owner_user_id.is_some() {
        return Ok(FirecrawlDemandPolicy::Allowed);
    }
    if newsly_db::aggregator_corpus::visible_active_subscriber(
        transaction,
        key.as_str(),
        &row.raw_metadata,
    )
    .await?
    {
        Ok(FirecrawlDemandPolicy::Allowed)
    } else {
        Ok(FirecrawlDemandPolicy::NoSubscriberDemand)
    }
}

pub(super) async fn prepare_processing(
    transaction: &mut Transaction<'_, Postgres>,
    news_item_id: i64,
) -> Result<Option<ProcessPreparation>, NewsRepositoryError> {
    let Some(row) = load_news_row(transaction, news_item_id).await? else {
        return Ok(None);
    };
    let body_source = resolve_body_source(transaction, &row).await?;
    let snapshot = snapshot_from_row(&row, body_source);
    let checkpoint = snapshot.raw_metadata.get("summary_checkpoint");
    let local_summary = checkpoint
        .filter(|value| {
            value.get("fingerprint").and_then(Value::as_str) == Some(snapshot.fingerprint.as_str())
        })
        .and_then(|value| value.get("summary"))
        .and_then(|value| serde_json::from_value(value.clone()).ok())
        .or_else(|| existing_summary(&snapshot));
    let (reusable_summary, reusable_representative_id) = if local_summary.is_some() {
        (local_summary, None)
    } else {
        find_reusable_exact_summary(transaction, &snapshot).await?
    };
    if row.status != "ready" {
        sqlx::query(
            r#"
        UPDATE news_items
        SET status = 'processing', updated_at = timezone('UTC', clock_timestamp())
        WHERE id::bigint = $1 AND status <> 'ready'
        "#,
        )
        .bind(news_item_id)
        .execute(&mut **transaction)
        .await?;
    }
    Ok(Some(ProcessPreparation {
        snapshot,
        reusable_summary,
        reusable_representative_id,
    }))
}

mod checkpoint;
pub(super) use checkpoint::checkpoint_summary;

pub(super) async fn load_relation_candidates(
    transaction: &mut Transaction<'_, Postgres>,
    snapshot: &NewsSnapshot,
    item_document: &NewsRelationDocument,
) -> Result<Vec<RelationCandidate>, NewsRepositoryError> {
    let floor = (Utc::now() - Duration::days(RELATED_LOOKBACK_DAYS)).naive_utc();
    let exact_key = item_document.exact_relation_key.as_ref();
    let title_query = relation_search_query(item_document.primary_title.as_deref());
    let rows = sqlx::query_as::<_, NewsRow>(concat!(
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
        "#,
        news_row_columns!(),
        r#"
        FROM deduplicated
        ORDER BY rank DESC, ingested_at DESC, id DESC
        LIMIT $13
        "#,
    ))
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
            RelationCandidate { document }
        })
        .collect())
}

pub(super) async fn apply_enrichment(
    transaction: &mut Transaction<'static, Postgres>,
    queue: &QueueKernel,
    plan: &EnrichmentFinalizationPlan,
) -> Result<NewsApplyOutcome, NewsRepositoryError> {
    // Demand transitions and denial publication share one short transaction lock.
    // Take it before any source/usage writes; external extraction has already ended.
    let denied = matches!(plan.mutation, EnrichmentMutation::MetadataOnly { .. });
    if denied {
        newsly_db::aggregator_corpus::lock_aggregator_demand(transaction).await?;
    }
    let Some(row) = load_news_row(transaction, plan.snapshot.id).await? else {
        return Ok(NewsApplyOutcome::NewsItemMissing);
    };
    if row.status == "ready"
        && !newsly_db::news_grouping::try_lock_scope(
            transaction,
            &plan.snapshot.visibility_scope,
            plan.snapshot.owner_user_id,
        )
        .await?
    {
        return Ok(NewsApplyOutcome::CandidateChanged);
    }
    if source_fingerprint(&row) != plan.snapshot.fingerprint {
        return Ok(NewsApplyOutcome::SourceChanged);
    }
    if denied
        && firecrawl_demand_policy(transaction, &row).await?
            == crate::content::FirecrawlDemandPolicy::Allowed
    {
        // A subscriber arrived while free extraction ran. Re-prepare this same
        // durable task without publishing a denial or an incomplete summary.
        return Ok(NewsApplyOutcome::DemandChanged);
    }
    let mut metadata = row.raw_metadata.as_object().cloned().unwrap_or_default();
    let article_extraction = apply_enrichment_metadata(
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
            updated_at = timezone('UTC', clock_timestamp())
        WHERE id::bigint = $1
        "#,
    )
    .bind(plan.snapshot.id)
    .bind(Value::Object(metadata))
    .bind(published_final_url(&plan.mutation))
    .bind(enrichment_domain(&plan.mutation))
    .execute(&mut **transaction)
    .await?;
    // Keep enrichment pending until the credit prerequisite recovers. A deferred
    // article must not acquire a metadata-only summary that recovery would reuse.
    if matches!(plan.mutation, EnrichmentMutation::Deferred { .. }) {
        return Ok(NewsApplyOutcome::Applied);
    }
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
        article_extraction = %article_extraction,
        "news article enrichment finalized"
    );
    Ok(NewsApplyOutcome::Applied)
}

pub(super) async fn apply_processing(
    transaction: &mut Transaction<'static, Postgres>,
    queue: &QueueKernel,
    plan: &ProcessFinalizationPlan,
) -> Result<NewsApplyOutcome, NewsRepositoryError> {
    let Some(row) = load_news_row(transaction, plan.snapshot.id).await? else {
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
    if !newsly_db::news_grouping::try_lock_scope(
        transaction,
        &plan.snapshot.visibility_scope,
        plan.snapshot.owner_user_id,
    )
    .await?
    {
        return Ok(NewsApplyOutcome::CandidateChanged);
    }
    let candidates = prefilter_relation_candidates(
        &mutation.item_document,
        load_relation_candidates(transaction, &plan.snapshot, &mutation.item_document).await?,
    );
    let documents = candidates
        .iter()
        .map(|candidate| candidate.document.clone())
        .collect::<Vec<_>>();
    let exact = mutation
        .item_document
        .exact_relation_key
        .as_ref()
        .is_some_and(|key| {
            documents
                .iter()
                .any(|document| document.exact_relation_key.as_ref() == Some(key))
        });
    let texts = if candidates.is_empty() || exact {
        Vec::new()
    } else {
        newsly_domain::prepare_relation_embedding_texts(&mutation.item_document, &documents)
    };
    let authorized_sources = std::iter::once(plan.snapshot.id)
        .chain(documents.iter().map(|document| document.id))
        .collect::<Vec<_>>();
    let vectors = super::relation_preparation::load_vectors(
        transaction,
        &texts,
        &authorized_sources,
        &mutation.relation_model,
    )
    .await?;
    let Some(vectors) = vectors.into_iter().collect::<Option<Vec<_>>>() else {
        return Ok(NewsApplyOutcome::CandidateChanged);
    };
    let store = match super::relation_preparation::vector_store(&texts, vectors) {
        Ok(store) => store,
        Err(error) => {
            tracing::warn!(news_item_id=plan.snapshot.id, error=%error, "invalid cached relation input");
            return Ok(NewsApplyOutcome::CandidateChanged);
        }
    };
    let relation = newsly_domain::related_representatives(
        &mutation.item_document,
        &documents,
        mutation.relation_thresholds,
        &store,
    )
    .map_err(|error| NewsRepositoryError::Relation(error.to_string()))?;
    if relation.accepted_ids.is_empty() && !mutation.links_prepared {
        return Ok(NewsApplyOutcome::CandidateChanged);
    }
    // The unlocked preflight is preparation only. This guarded decision owns publication.
    let relation_trace = serde_json::to_value(&relation).unwrap_or_else(|_| json!({}));
    tracing::info!(
        news_item_id = plan.snapshot.id,
        reused_summary = mutation.used_existing_summary,
        relation_trace = %relation_trace,
        "publishing processed news item"
    );
    persist_summary(transaction, plan, mutation, &row.raw_metadata).await?;
    let representative_id = reconcile_relation(
        transaction,
        plan,
        mutation,
        &candidates,
        &relation.accepted_ids,
    )
    .await?;
    if let Some(previous_root) = row.representative_news_item_id
        && previous_root != representative_id
    {
        recompute_cluster(transaction, previous_root).await?;
    }
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
        let mut prepare = EnqueueRequest::new(TaskType::PrepareNewsLens);
        prepare.priority = -10;
        prepare.payload = Some(Map::from_iter([(
            "news_item_id".to_owned(),
            json!(representative_id),
        )]));
        prepare.dedupe = Some(true);
        prepare.dedupe_key = Some(format!("news-lens:{representative_id}"));
        queue
            .enqueue_many_in_transaction(transaction, vec![prepare])
            .await?;
        enqueue_ready_fanout(transaction, queue, plan).await?;
    }
    Ok(NewsApplyOutcome::Applied)
}

async fn load_news_row(
    transaction: &mut Transaction<'_, Postgres>,
    news_item_id: i64,
) -> Result<Option<NewsRow>, sqlx::Error> {
    sqlx::query_as::<_, NewsRow>(concat!(
        r#"
        SELECT
        "#,
        news_row_columns!(),
        r#"
        FROM news_items
        WHERE id::bigint = $1
        FOR UPDATE
        "#,
    ))
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
    let rows = sqlx::query_as::<_, NewsRow>(concat!(
        r#"
        SELECT
        "#,
        news_row_columns!(),
        r#"
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
    ))
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
        // Full-article evidence must not inherit a metadata-only sibling's
        // summary or representative, including a sibling whose paid replay failed.
        if !matches!(snapshot.body_source, BodySource::None)
            && !candidate
                .raw_metadata
                .get("article_body_ref")
                .is_some_and(Value::is_object)
        {
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
    let mut raw_metadata = row.raw_metadata.clone();
    if let Some(object) = raw_metadata.as_object_mut() {
        // Retain preserves key order with serde_json's preserve_order feature;
        // removing by swap would make polling-only keys perturb the hash.
        object.retain(|key, _| {
            ![
                "summary_checkpoint",
                "processing_error",
                "comment_count",
                "scraped_at",
            ]
            .contains(&key.as_str())
        });
        if let Some(metrics) = object
            .get_mut("aggregator")
            .and_then(|value| value.get_mut("metadata"))
            .and_then(Value::as_object_mut)
        {
            metrics.retain(|key, _| {
                !["score", "comments_count", "upvote_ratio"].contains(&key.as_str())
            });
        }
    }
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
        "raw_metadata": raw_metadata,
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
            status = CASE WHEN status = 'ready' THEN status ELSE $2 END,
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

async fn persist_summary(
    transaction: &mut Transaction<'static, Postgres>,
    plan: &ProcessFinalizationPlan,
    mutation: &ProcessMutation,
    current_metadata: &Value,
) -> Result<(), sqlx::Error> {
    let summary = &mutation.summary;
    // Evidence was fenced above; retain fresh polling metadata on publication.
    let mut metadata = current_metadata.as_object().cloned().unwrap_or_default();
    metadata.insert("summary".to_owned(), summary_json(summary));
    metadata.insert(
        "summary_kind".to_owned(),
        Value::String("short_news".to_owned()),
    );
    metadata.insert("summary_version".to_owned(), Value::from(1));
    metadata.remove("processing_error");
    metadata.remove("summary_checkpoint");
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
    candidates: &[RelationCandidate],
    accepted_ids: &[i64],
) -> Result<i64, NewsRepositoryError> {
    let by_id = candidates
        .iter()
        .map(|candidate| (candidate.document.id, &candidate.document))
        .collect::<HashMap<_, _>>();
    let representative_id = accepted_ids.first().copied().unwrap_or(plan.snapshot.id);
    let representative_document = if representative_id == plan.snapshot.id {
        &mutation.item_document
    } else {
        by_id.get(&representative_id).copied().ok_or(
            NewsRepositoryError::MissingAcceptedCandidate(representative_id),
        )?
    };
    let mut merged_representatives = Vec::new();
    for candidate_id in accepted_ids.iter().copied().skip(1) {
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
        WHERE id::bigint = $1 OR ($1 <> $2 AND representative_news_item_id::bigint = $1)
        "#,
    )
    .bind(plan.snapshot.id)
    .bind(representative_id)
    .execute(&mut **transaction)
    .await?;
    recompute_cluster(transaction, representative_id).await?;
    Ok(representative_id)
}

#[allow(clippy::too_many_lines)]
async fn recompute_cluster(
    transaction: &mut Transaction<'static, Postgres>,
    representative_id: i64,
) -> Result<(), NewsRepositoryError> {
    let rows = sqlx::query_as::<_, NewsRow>(concat!(
        r#"
        SELECT
        "#,
        news_row_columns!(),
        r#"
        FROM news_items
        WHERE id::bigint = $1 OR representative_news_item_id::bigint = $1
        ORDER BY ingested_at, id
        FOR UPDATE
        "#,
    ))
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
    .execute(&mut **transaction)
    .await?;
    sqlx::query(
        r#"
        UPDATE news_items
        SET
            representative_news_item_id = CASE WHEN id::bigint = $1 THEN NULL ELSE $1 END,
            cluster_size = $2,
            updated_at = timezone('UTC', clock_timestamp())
        WHERE id::bigint = ANY($3)
        "#,
    )
    .bind(representative_id)
    .bind(size)
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
    let mut discussion_snippets = Vec::new();
    let mut related_titles = Vec::new();
    push_unique(&mut related_titles, preferred_title);
    for row in rows {
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
    }
    json!({
        "discussion_snippets": discussion_snippets.into_iter().take(5).collect::<Vec<_>>(),
        "related_titles": related_titles,
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
    let briefing_users = sqlx::query_as::<_, (i64, bool)>(
        r#"
        SELECT candidate.user_id, COALESCE(preference.enabled, FALSE)
        FROM unnest($2::bigint[]) AS candidate(user_id)
        LEFT JOIN LATERAL (
            SELECT enabled FROM news_filter_preferences
            WHERE user_id::bigint = candidate.user_id
            ORDER BY revision DESC LIMIT 1
        ) AS preference ON TRUE
        WHERE NOT EXISTS (
            SELECT 1 FROM news_item_read_status
            WHERE news_item_id::bigint = $1 AND user_id::bigint = candidate.user_id
        )
        ORDER BY candidate.user_id
        "#,
    )
    .bind(plan.snapshot.id)
    .bind(&visible_users)
    .fetch_all(&mut **transaction)
    .await?;
    let filtered_users = briefing_users
        .iter()
        .filter_map(|(user_id, enabled)| enabled.then_some(*user_id))
        .collect::<HashSet<_>>();
    let unfiltered_users = briefing_users
        .iter()
        .filter_map(|(user_id, enabled)| (!enabled).then_some(*user_id))
        .collect::<Vec<_>>();
    // Enabled filters admit through the refresh's revisioned filter/seed path. An unversioned
    // row here would be pruned there, so only enqueue their coalesced wake below.
    sqlx::query(
        r#"
        INSERT INTO briefing_pending_sources (
            user_id, lens_key, source_kind, source_id, enqueued_at
        )
        SELECT candidate.user_id::integer, NULL, 'news', $2,
               timezone('UTC', clock_timestamp())
        FROM unnest($1::bigint[]) AS candidate(user_id)
        WHERE NOT EXISTS (
            SELECT 1 FROM onboarding_first_edition_runs
            WHERE user_id::bigint = candidate.user_id
              AND status = 'active' AND NOT news_seed_settled
        )
        ORDER BY candidate.user_id
        ON CONFLICT (user_id, source_kind, source_id) DO NOTHING
        "#,
    )
    .bind(&unfiltered_users)
    .bind(plan.snapshot.id)
    .execute(&mut **transaction)
    .await?;
    let user_ids = briefing_users.iter().map(|(id, _)| *id).collect::<Vec<_>>();
    let pending_counts = sqlx::query_as::<_, (i64, i64, bool)>(
        r#"
        SELECT candidate.user_id, count(pending.user_id)::bigint,
               EXISTS (
                   SELECT 1 FROM processing_tasks AS task
                   WHERE task.owner_user_id::bigint = candidate.user_id
                     AND task.task_type = 'briefing_refresh' AND task.status = 'processing'
                     AND task.dedupe_key = 'briefing_refresh:' || candidate.user_id::text || ':append'
               )
        FROM unnest($1::bigint[]) AS candidate(user_id)
        LEFT JOIN briefing_pending_sources AS pending
          ON pending.user_id::bigint = candidate.user_id AND pending.lens_key IS NULL
        GROUP BY candidate.user_id
        ORDER BY candidate.user_id
        "#,
    )
    .bind(&user_ids)
    .fetch_all(&mut **transaction)
    .await?;
    let mut requests = Vec::with_capacity(pending_counts.len());
    let mut dedupe_keys = Vec::with_capacity(pending_counts.len());
    let mut deadlines = Vec::with_capacity(pending_counts.len());
    let now = Utc::now();
    for (user_id, pending_count, append_running) in pending_counts {
        // A filtered source has no pending row to trigger the batch threshold yet. Wake
        // admission immediately; canonical preparation still owns composition batching.
        let delay = if filtered_users.contains(&user_id)
            || pending_count >= plan.briefing_batch_minimum.max(1)
        {
            0
        } else {
            plan.briefing_debounce_seconds.max(0)
        };
        let available_at = now + Duration::seconds(delay);
        // A running append may have already filtered its snapshot. Preserve the new admission
        // wake as a pending sweep rather than deduplicating it into that processing row.
        let mode = if filtered_users.contains(&user_id) && append_running {
            "sweep"
        } else {
            "append"
        };
        let dedupe_key = format!("briefing_refresh:{user_id}:{mode}");
        let mut request = EnqueueRequest::new(TaskType::BriefingRefresh);
        request.payload = Some(Map::from_iter([
            ("user_id".to_owned(), Value::from(user_id)),
            ("mode".to_owned(), Value::String(mode.to_owned())),
        ]));
        request.owner_user_id = Some(user_id);
        request.dedupe = Some(true);
        request.dedupe_key = Some(dedupe_key.clone());
        request.available_at = Some(available_at);
        dedupe_keys.push(dedupe_key);
        deadlines.push(available_at.naive_utc());
        requests.push(request);
    }
    if !requests.is_empty() {
        queue
            .enqueue_many_in_transaction(transaction, requests)
            .await?;
        sqlx::query(
            r#"
            WITH locked AS MATERIALIZED (
                SELECT task.id, wake.available_at
                FROM processing_tasks AS task
                JOIN unnest($1::text[], $2::timestamp[]) AS wake(dedupe_key, available_at)
                  ON task.dedupe_key = wake.dedupe_key
                WHERE task.status = 'pending' AND task.retry_count = 0
                ORDER BY task.id
                FOR UPDATE OF task
            )
            UPDATE processing_tasks AS task
            SET available_at = LEAST(task.available_at, locked.available_at)
            FROM locked
            WHERE task.id = locked.id
            "#,
        )
        .bind(&dedupe_keys)
        .bind(&deadlines)
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
    if AggregatorKey::from_key(platform).is_none() {
        return Ok(Vec::new());
    }
    sqlx::query_scalar::<_, i64>(
        r#"
        SELECT DISTINCT users.id::bigint
        FROM users
        JOIN user_scraper_configs AS config ON config.user_id = users.id
        WHERE users.is_active IS TRUE
          AND config.scraper_type = 'aggregator'
          AND config.is_active IS TRUE
          AND aggregator_config_admits(config.config::jsonb, $1, $2::json)
        ORDER BY users.id::bigint
        "#,
    )
    .bind(platform)
    .bind(&snapshot.raw_metadata)
    .fetch_all(&mut **transaction)
    .await
}

pub(super) async fn persist_extraction_usage(
    transaction: &mut Transaction<'static, Postgres>,
    task_id: i64,
    user_id: Option<i64>,
    news_item_id: i64,
    writes: &[UsageWrite],
) -> Result<(), NewsRepositoryError> {
    let user_id = if let Some(user_id) = user_id {
        sqlx::query_scalar("SELECT id::bigint FROM users WHERE id::bigint=$1 AND is_active IS TRUE")
            .bind(user_id)
            .fetch_optional(&mut **transaction)
            .await?
    } else {
        None
    };
    for write in writes {
        match write {
            UsageWrite::Extraction(batch) => {
                for (index, event) in batch.events.iter().enumerate() {
                    insert_usage(
                        transaction,
                        UsageInsert {
                            provider: "document_extractor",
                            model: extraction_method_name(batch.method),
                            feature: "document_extraction",
                            operation: extraction_intent_name(batch.intent),
                            request_id: Some(&batch.request_id),
                            task_id,
                            content_id: None,
                            user_id,
                            request_count: (index == 0).then_some(1),
                            resource_count: Some(i32::try_from(event.quantity).unwrap_or(i32::MAX)),
                            input_tokens: None,
                            cache_read_tokens: None,
                            cache_write_tokens: None,
                            output_tokens: None,
                            total_tokens: None,
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
                insert_usage(
                    transaction,
                    UsageInsert {
                        provider: "firecrawl",
                        model: "scrape-v2",
                        feature: "html_extraction",
                        operation: "firecrawl_scrape",
                        request_id: Some(&usage.request_id),
                        task_id,
                        content_id: None,
                        user_id,
                        request_count: Some(1),
                        resource_count: usage.resource_count(),
                        input_tokens: None,
                        cache_read_tokens: None,
                        cache_write_tokens: None,
                        output_tokens: None,
                        total_tokens: None,
                        cost_usd: usage.cost_usd,
                        pricing_version: usage.pricing_version(),
                        metadata: usage.metadata(Some(news_item_id)),
                    },
                )
                .await?;
            }
            UsageWrite::X(_) => {
                return Err(NewsRepositoryError::UnexpectedContentAnalysisUsage);
            }
        }
    }
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

mod error;
pub(super) use error::NewsRepositoryError;

#[cfg(test)]
#[path = "checkpoint_tests.rs"]
mod checkpoint_tests;

#[cfg(test)]
#[path = "firecrawl_usage_tests.rs"]
mod firecrawl_usage_tests;

#[path = "enrichment_metadata.rs"]
mod enrichment_metadata;
use enrichment_metadata::{apply_enrichment_metadata, enrichment_domain, published_final_url};

#[cfg(test)]
#[path = "credit_deferral_tests.rs"]
mod credit_deferral_tests;

#[cfg(test)]
#[path = "demand_tests.rs"]
mod demand_tests;

#[cfg(test)]
#[path = "fanout_tests.rs"]
mod fanout_tests;

#[cfg(test)]
#[path = "grouping_tests.rs"]
mod grouping_tests;
