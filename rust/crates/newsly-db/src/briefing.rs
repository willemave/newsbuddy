use std::collections::{BTreeSet, HashMap, HashSet};

use chrono::{DateTime, NaiveDateTime, Utc};
use serde_json::{Value, json};
use sqlx::{FromRow, PgPool, Postgres, Transaction};
use thiserror::Error;

use crate::briefing_refresh::load_eligible_sources_for_keys;

mod narration;

use narration::{
    document_narration_plans, episode_group_id, legacy_narration_plan, narration_chapter_plans,
    source_snapshot, stable_hash,
};

const DEFAULT_MASTHEAD_TITLE: &str = "The Unread Times";
const DEFAULT_MASTHEAD_DECK: &str = "A fresh edition will appear as unread sources arrive.";
const BRIEFING_NARRATION_KIND: &str = "briefing_narration";
const PUBLIC_AUDIO_EPISODE_ERROR_MESSAGE: &str = "Couldn't prepare audio. Please try again.";

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BriefingStateProjection {
    pub version: i32,
    pub masthead_title: String,
    pub masthead_deck: String,
}

#[derive(Debug, Clone, PartialEq, Eq, FromRow)]
pub struct BriefingLensProjection {
    pub id: i64,
    pub key: String,
    pub tier: String,
    pub title: String,
    pub deck: String,
    pub position: i32,
}

#[derive(Debug, Clone, PartialEq)]
pub struct BriefingSegmentProjection {
    pub id: i64,
    pub created_at: DateTime<Utc>,
    pub status: String,
    pub narration_text: String,
    pub blocks: Value,
    pub source_keys: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BriefingSegmentMetadataProjection {
    pub id: i64,
    pub lens_id: i64,
    pub created_at: DateTime<Utc>,
    pub source_keys: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FirstRunSourceProjection {
    pub display_name: String,
    pub position: i32,
    pub status: String,
    pub processed_item_count: i32,
    pub completed_at: Option<DateTime<Utc>>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BriefingFirstRunProjection {
    pub run_id: i64,
    pub revision: i32,
    pub sources: Vec<FirstRunSourceProjection>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct BriefingIndexProjection {
    pub state: BriefingStateProjection,
    pub lenses: Vec<BriefingLensProjection>,
    pub segments: Vec<BriefingSegmentMetadataProjection>,
    pub read_source_keys: HashSet<String>,
    pub pending_lens_keys: HashSet<String>,
    pub first_run: Option<BriefingFirstRunProjection>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BriefingIndexValidatorProjection {
    pub version: i32,
    pub first_run_id: i64,
    pub first_run_revision: i32,
}

#[derive(Debug, Clone, PartialEq)]
pub struct ContentBriefingSourceProjection {
    pub id: i64,
    pub content_type: String,
    pub url: String,
    pub source_url: Option<String>,
    pub title: Option<String>,
    pub source: Option<String>,
    pub metadata: Value,
    pub created_at: DateTime<Utc>,
    pub publication_date: Option<DateTime<Utc>>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct NewsBriefingSourceProjection {
    pub id: i64,
    pub summary_text: Option<String>,
    pub summary_key_points: Value,
    pub raw_metadata: Value,
    pub article_url: Option<String>,
    pub canonical_story_url: Option<String>,
    pub canonical_item_url: Option<String>,
    pub published_at: Option<DateTime<Utc>>,
    pub processed_at: Option<DateTime<Utc>>,
    pub ingested_at: DateTime<Utc>,
    pub created_at: DateTime<Utc>,
    pub discussion: Option<BriefingDiscussionProjection>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct BriefingDiscussionProjection {
    pub platform: String,
    pub comment_count: Option<i32>,
    pub discussion_url: Option<String>,
    pub summary: Option<Value>,
    pub summary_status: String,
    pub last_refresh_status: String,
    pub summary_generated_at: Option<DateTime<Utc>>,
    pub last_comments_fetched_at: Option<DateTime<Utc>>,
    pub last_count_checked_at: Option<DateTime<Utc>>,
}

#[derive(Debug, Clone, PartialEq)]
pub enum BriefingSourceProjection {
    Content(ContentBriefingSourceProjection),
    News(NewsBriefingSourceProjection),
}

#[derive(Debug, Clone, PartialEq)]
pub struct BriefingLensPageProjection {
    pub state: BriefingStateProjection,
    pub lens: BriefingLensProjection,
    pub segment_count: usize,
    pub all_source_keys: Vec<String>,
    pub read_source_keys: HashSet<String>,
    pub segments: Vec<BriefingSegmentProjection>,
    pub sources: HashMap<String, BriefingSourceProjection>,
    pub has_more: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BriefingLensCursorProjection {
    pub lens_id: i64,
    pub segment_id: i64,
    pub created_at: NaiveDateTime,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BriefingReadMarkProjection {
    pub marked: usize,
    pub retired: usize,
    pub version: i32,
}

#[derive(Debug, Clone, PartialEq)]
pub struct AudioEpisodeProjection {
    pub id: i64,
    pub kind: String,
    pub status: String,
    pub title: String,
    pub source_content_id: Option<i64>,
    pub source_item_ids: Value,
    pub source_snapshot: Value,
    pub script_text: Option<String>,
    pub audio_storage_path: Option<String>,
    pub duration_seconds: Option<i32>,
    pub error_message: Option<String>,
    pub episode_group_id: Option<String>,
    pub chapter_index: Option<i32>,
    pub created_at: DateTime<Utc>,
    pub updated_at: Option<DateTime<Utc>>,
}

#[derive(Debug, Clone, PartialEq)]
pub enum PrepareNarrationOutcome {
    Ready(Vec<AudioEpisodeProjection>),
    LensNotFound,
    Empty,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum BriefingNarrationSelection {
    Lens(String),
    ArticleTier,
    PodcastTier,
    NewsProgram,
}

#[derive(Debug, FromRow)]
struct StateRow {
    version: i32,
    masthead_title: String,
    masthead_deck: String,
}

#[derive(Debug, FromRow)]
struct SegmentRow {
    id: i64,
    created_at: NaiveDateTime,
    status: String,
    narration_text: String,
    blocks: Value,
    source_keys: Value,
}

#[derive(Debug, FromRow)]
struct SegmentWithLensRow {
    id: i64,
    lens_id: i64,
    created_at: NaiveDateTime,
    status: String,
    narration_text: String,
    blocks: Value,
    source_keys: Value,
}

#[derive(Debug, FromRow)]
struct SegmentMetadataRow {
    id: i64,
    lens_id: i64,
    created_at: NaiveDateTime,
    source_keys: Value,
}

#[derive(Debug, FromRow)]
struct FirstRunRow {
    id: i64,
    revision: i32,
}

#[derive(Debug, FromRow)]
struct FirstRunSourceRow {
    display_name: String,
    position: i32,
    status: String,
    processed_item_count: i32,
    completed_at: Option<NaiveDateTime>,
}

#[derive(Debug, FromRow)]
struct ContentSourceRow {
    id: i64,
    content_type: String,
    url: String,
    source_url: Option<String>,
    title: Option<String>,
    source: Option<String>,
    metadata: Value,
    created_at: NaiveDateTime,
    publication_date: Option<NaiveDateTime>,
}

#[derive(Debug, FromRow)]
struct NewsSourceRow {
    id: i64,
    summary_text: Option<String>,
    summary_key_points: Value,
    raw_metadata: Value,
    article_url: Option<String>,
    canonical_story_url: Option<String>,
    canonical_item_url: Option<String>,
    published_at: Option<NaiveDateTime>,
    processed_at: Option<NaiveDateTime>,
    ingested_at: NaiveDateTime,
    created_at: NaiveDateTime,
    discussion_platform: Option<String>,
    discussion_comment_count: Option<i32>,
    discussion_url: Option<String>,
    discussion_summary: Option<Value>,
    discussion_summary_status: Option<String>,
    discussion_last_refresh_status: Option<String>,
    discussion_summary_generated_at: Option<NaiveDateTime>,
    discussion_last_comments_fetched_at: Option<NaiveDateTime>,
    discussion_last_count_checked_at: Option<NaiveDateTime>,
}

#[derive(Debug, FromRow)]
struct AudioEpisodeRow {
    id: i64,
    kind: String,
    status: String,
    title: String,
    source_content_id: Option<i64>,
    source_item_ids: Value,
    source_snapshot: Value,
    script_text: Option<String>,
    audio_storage_path: Option<String>,
    duration_seconds: Option<i32>,
    error_message: Option<String>,
    episode_group_id: Option<String>,
    chapter_index: Option<i32>,
    created_at: NaiveDateTime,
    updated_at: Option<NaiveDateTime>,
}

impl From<AudioEpisodeRow> for AudioEpisodeProjection {
    fn from(row: AudioEpisodeRow) -> Self {
        Self {
            id: row.id,
            kind: row.kind,
            status: row.status,
            title: row.title,
            source_content_id: row.source_content_id,
            source_item_ids: row.source_item_ids,
            source_snapshot: row.source_snapshot,
            script_text: row.script_text,
            audio_storage_path: row.audio_storage_path,
            duration_seconds: row.duration_seconds,
            error_message: row.error_message,
            episode_group_id: row.episode_group_id,
            chapter_index: row.chapter_index,
            created_at: row.created_at.and_utc(),
            updated_at: row.updated_at.map(|value| value.and_utc()),
        }
    }
}

pub async fn load_briefing_index_validator(
    pool: &PgPool,
    user_id: i64,
) -> Result<BriefingIndexValidatorProjection, BriefingRepositoryError> {
    let version = sqlx::query_scalar::<_, Option<i32>>(
        "SELECT version FROM briefing_states WHERE user_id::bigint = $1",
    )
    .bind(user_id)
    .fetch_optional(pool)
    .await?
    .flatten()
    .unwrap_or(0);
    let first_run = sqlx::query_as::<_, FirstRunRow>(
        r#"
        SELECT id::bigint AS id, revision
        FROM onboarding_first_edition_runs
        WHERE user_id::bigint = $1 AND status = 'active'
        ORDER BY id DESC
        LIMIT 1
        "#,
    )
    .bind(user_id)
    .fetch_optional(pool)
    .await?;
    Ok(BriefingIndexValidatorProjection {
        version,
        first_run_id: first_run.as_ref().map_or(0, |run| run.id),
        first_run_revision: first_run.map_or(0, |run| run.revision),
    })
}

pub async fn load_briefing_index(
    pool: &PgPool,
    user_id: i64,
) -> Result<BriefingIndexProjection, BriefingRepositoryError> {
    let state = load_state(pool, user_id).await?;
    let lenses = sqlx::query_as::<_, BriefingLensProjection>(
        r#"
        SELECT id::bigint AS id, key, tier, title, deck, position
        FROM briefing_lenses
        WHERE user_id::bigint = $1 AND status = 'active'
        ORDER BY position, id
        "#,
    )
    .bind(user_id)
    .fetch_all(pool)
    .await?;
    let segments = sqlx::query_as::<_, SegmentMetadataRow>(
        r#"
        SELECT id::bigint AS id, lens_id::bigint AS lens_id, created_at,
               source_keys::jsonb AS source_keys
        FROM briefing_segments
        WHERE user_id::bigint = $1 AND status IN ('active', 'degraded')
        "#,
    )
    .bind(user_id)
    .fetch_all(pool)
    .await?
    .into_iter()
    .map(|row| segment_metadata_from_row(&row))
    .collect::<Vec<_>>();
    let source_keys = dedupe_source_keys(segments.iter().flat_map(|segment| &segment.source_keys));
    let read_source_keys = read_source_keys_pool(pool, user_id, &source_keys).await?;
    let pending_lens_keys = sqlx::query_scalar::<_, String>(
        r#"
        SELECT DISTINCT lens_key
        FROM briefing_pending_sources
        WHERE user_id::bigint = $1 AND lens_key IS NOT NULL
        "#,
    )
    .bind(user_id)
    .fetch_all(pool)
    .await?
    .into_iter()
    .collect();
    let first_run = load_first_run(pool, user_id).await?;
    Ok(BriefingIndexProjection {
        state,
        lenses,
        segments,
        read_source_keys,
        pending_lens_keys,
        first_run,
    })
}

pub async fn load_briefing_lens_page(
    pool: &PgPool,
    user_id: i64,
    lens_key: &str,
    limit: Option<usize>,
    cursor: Option<&BriefingLensCursorProjection>,
) -> Result<Option<BriefingLensPageProjection>, BriefingRepositoryError> {
    let lens = sqlx::query_as::<_, BriefingLensProjection>(
        r#"
        SELECT id::bigint AS id, key, tier, title, deck, position
        FROM briefing_lenses
        WHERE user_id::bigint = $1 AND key = $2 AND status = 'active'
        LIMIT 1
        "#,
    )
    .bind(user_id)
    .bind(lens_key)
    .fetch_optional(pool)
    .await?;
    let Some(lens) = lens else {
        return Ok(None);
    };
    if cursor.is_some_and(|cursor| cursor.lens_id != lens.id) {
        return Err(BriefingRepositoryError::CursorWrongLens);
    }
    let metadata = sqlx::query_as::<_, SegmentMetadataRow>(
        r#"
        SELECT id::bigint AS id, lens_id::bigint AS lens_id, created_at,
               source_keys::jsonb AS source_keys
        FROM briefing_segments
        WHERE lens_id::bigint = $1 AND status IN ('active', 'degraded')
        ORDER BY created_at DESC, id DESC
        "#,
    )
    .bind(lens.id)
    .fetch_all(pool)
    .await?
    .into_iter()
    .map(|row| segment_metadata_from_row(&row))
    .collect::<Vec<_>>();
    if let Some(cursor) = cursor {
        let Some(anchor) = metadata
            .iter()
            .find(|segment| segment.id == cursor.segment_id)
        else {
            return Err(BriefingRepositoryError::StaleCursor);
        };
        if anchor.created_at.naive_utc() != cursor.created_at {
            return Err(BriefingRepositoryError::CursorAnchorMismatch);
        }
    }
    let segment_count = metadata.len();
    let all_source_keys =
        dedupe_source_keys(metadata.iter().flat_map(|segment| &segment.source_keys));
    let read_source_keys = read_source_keys_pool(pool, user_id, &all_source_keys).await?;
    let is_paged = limit.is_some() || cursor.is_some();
    let fetch_limit = if is_paged {
        limit.unwrap_or(12)
    } else {
        segment_count.max(1)
    };
    let rows = if let Some(cursor) = cursor {
        sqlx::query_as::<_, SegmentRow>(
            r#"
            SELECT id::bigint AS id, created_at, status, narration_text,
                   blocks::jsonb AS blocks, source_keys::jsonb AS source_keys
            FROM briefing_segments
            WHERE lens_id::bigint = $1 AND status IN ('active', 'degraded')
              AND (created_at < $2 OR (created_at = $2 AND id::bigint < $3))
            ORDER BY created_at DESC, id DESC
            LIMIT $4
            "#,
        )
        .bind(lens.id)
        .bind(cursor.created_at)
        .bind(cursor.segment_id)
        .bind(i64::try_from(fetch_limit.saturating_add(1)).unwrap_or(i64::MAX))
        .fetch_all(pool)
        .await?
    } else {
        sqlx::query_as::<_, SegmentRow>(
            r#"
            SELECT id::bigint AS id, created_at, status, narration_text,
                   blocks::jsonb AS blocks, source_keys::jsonb AS source_keys
            FROM briefing_segments
            WHERE lens_id::bigint = $1 AND status IN ('active', 'degraded')
            ORDER BY created_at DESC, id DESC
            LIMIT $2
            "#,
        )
        .bind(lens.id)
        .bind(i64::try_from(fetch_limit.saturating_add(1)).unwrap_or(i64::MAX))
        .fetch_all(pool)
        .await?
    };
    let has_more = is_paged && rows.len() > fetch_limit;
    let segments = rows
        .into_iter()
        .take(fetch_limit)
        .map(segment_from_row)
        .collect::<Vec<_>>();
    let page_source_keys =
        dedupe_source_keys(segments.iter().flat_map(|segment| &segment.source_keys));
    let sources = load_sources(pool, user_id, &page_source_keys).await?;
    Ok(Some(BriefingLensPageProjection {
        state: load_state(pool, user_id).await?,
        lens,
        segment_count,
        all_source_keys,
        read_source_keys,
        segments,
        sources,
        has_more,
    }))
}

pub async fn mark_briefing_sources_read(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
    source_keys: &[String],
) -> Result<BriefingReadMarkProjection, BriefingRepositoryError> {
    let version = ensure_and_lock_state(transaction, user_id).await?;
    mark_sources_locked(transaction, user_id, source_keys, version).await
}

pub async fn mark_briefing_lens_read(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
    lens_key: &str,
) -> Result<Option<BriefingReadMarkProjection>, BriefingRepositoryError> {
    let version = ensure_and_lock_state(transaction, user_id).await?;
    let lens_id = sqlx::query_scalar::<_, i64>(
        r#"
        SELECT id::bigint FROM briefing_lenses
        WHERE user_id::bigint = $1 AND key = $2 AND status = 'active'
        "#,
    )
    .bind(user_id)
    .bind(lens_key)
    .fetch_optional(&mut **transaction)
    .await?;
    let Some(lens_id) = lens_id else {
        return Ok(None);
    };
    let raw_keys = sqlx::query_scalar::<_, Value>(
        r#"
        SELECT source_keys::jsonb FROM briefing_segments
        WHERE lens_id::bigint = $1 AND user_id::bigint = $2
          AND status IN ('active', 'degraded')
        "#,
    )
    .bind(lens_id)
    .bind(user_id)
    .fetch_all(&mut **transaction)
    .await?;
    let source_keys = dedupe_owned_source_keys(raw_keys.iter().flat_map(json_string_array));
    let read_keys = read_source_keys_transaction(transaction, user_id, &source_keys).await?;
    let keys_to_mark = source_keys
        .into_iter()
        .filter(|key| key.starts_with("news:") || !read_keys.contains(key))
        .collect::<Vec<_>>();
    mark_sources_locked(transaction, user_id, &keys_to_mark, version)
        .await
        .map(Some)
}

pub async fn ensure_briefing_state_version(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
) -> Result<i32, BriefingRepositoryError> {
    ensure_and_lock_state(transaction, user_id).await
}

pub async fn expedite_pending_briefing_refresh(
    transaction: &mut Transaction<'_, Postgres>,
    task_id: i64,
    available_at: DateTime<Utc>,
) -> Result<bool, BriefingRepositoryError> {
    let updated = sqlx::query(
        r#"
        UPDATE processing_tasks
        SET available_at = $2
        WHERE id::bigint = $1 AND status = 'pending'
          AND (available_at IS NULL OR available_at > $2)
        "#,
    )
    .bind(task_id)
    .bind(available_at.naive_utc())
    .execute(&mut **transaction)
    .await?
    .rows_affected();
    Ok(updated > 0)
}

pub async fn recent_briefing_dig_count(
    pool: &PgPool,
    user_id: i64,
) -> Result<i64, BriefingRepositoryError> {
    Ok(sqlx::query_scalar::<_, i64>(
        r#"
        SELECT count(*)::bigint FROM vendor_usage_records
        WHERE user_id::bigint = $1 AND feature = 'briefing_dig'
          AND created_at >= timezone('UTC', now()) - interval '1 hour'
        "#,
    )
    .bind(user_id)
    .fetch_one(pool)
    .await?)
}

pub async fn record_briefing_dig_usage(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
    operation: &str,
    provider: &str,
    model: &str,
    request_id: &str,
    input_tokens: Option<i64>,
    output_tokens: Option<i64>,
    metadata: Value,
) -> Result<(), BriefingRepositoryError> {
    sqlx::query(
        r#"
        INSERT INTO vendor_usage_records (
            provider, model, feature, operation, source, request_id, user_id,
            input_tokens, output_tokens, total_tokens, metadata, created_at,
            request_count
        ) VALUES (
            $1, $2, 'briefing_dig', $3, 'api', $4, $5::bigint::integer,
            $6::bigint::integer, $7::bigint::integer,
            CASE WHEN $6::bigint IS NULL AND $7::bigint IS NULL THEN NULL
                 ELSE coalesce($6::bigint, 0) + coalesce($7::bigint, 0) END::integer,
            $8::jsonb, timezone('UTC', now()), 1
        )
        "#,
    )
    .bind(provider)
    .bind(model)
    .bind(operation)
    .bind(request_id)
    .bind(user_id)
    .bind(input_tokens)
    .bind(output_tokens)
    .bind(metadata)
    .execute(&mut **transaction)
    .await?;
    Ok(())
}

pub async fn prepare_briefing_narration(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
    selection: &BriefingNarrationSelection,
    chaptered: bool,
) -> Result<PrepareNarrationOutcome, BriefingRepositoryError> {
    let (lens_key, tier, program_key, program_title, scope) = match selection {
        BriefingNarrationSelection::Lens(key) => {
            (Some(key.as_str()), None, key.as_str(), "Briefing", None)
        }
        BriefingNarrationSelection::ArticleTier => (
            None,
            Some("longform"),
            "articles",
            "Articles",
            Some("article_tier"),
        ),
        BriefingNarrationSelection::PodcastTier => (
            None,
            Some("audio"),
            "podcasts",
            "Podcasts",
            Some("podcast_tier"),
        ),
        BriefingNarrationSelection::NewsProgram => (
            None,
            Some("news"),
            "news",
            "News Briefing",
            Some("news_program"),
        ),
    };
    let lenses = sqlx::query_as::<_, BriefingLensProjection>(
        r#"
        SELECT id::bigint AS id, key, tier, title, deck, position
        FROM briefing_lenses
        WHERE user_id::bigint = $1 AND status = 'active'
          AND ($2::text IS NULL OR key = $2)
          AND ($3::text IS NULL OR tier = $3)
        ORDER BY position, id
        "#,
    )
    .bind(user_id)
    .bind(lens_key)
    .bind(tier)
    .fetch_all(&mut **transaction)
    .await?;
    if lenses.is_empty() && lens_key.is_some() {
        return Ok(PrepareNarrationOutcome::LensNotFound);
    }
    if lenses.is_empty() {
        return Ok(PrepareNarrationOutcome::Empty);
    }
    let lens_ids = lenses.iter().map(|lens| lens.id).collect::<Vec<_>>();
    let segment_rows = sqlx::query_as::<_, SegmentWithLensRow>(
        r#"
        SELECT id::bigint AS id, lens_id::bigint AS lens_id, created_at, status,
               narration_text, blocks::jsonb AS blocks, source_keys::jsonb AS source_keys
        FROM briefing_segments
        WHERE lens_id::bigint = ANY($1::bigint[]) AND status IN ('active', 'degraded')
        ORDER BY lens_id, created_at DESC, id DESC
        "#,
    )
    .bind(&lens_ids)
    .fetch_all(&mut **transaction)
    .await?;
    let mut segments_by_lens = HashMap::<i64, Vec<BriefingSegmentProjection>>::new();
    for row in segment_rows {
        segments_by_lens
            .entry(row.lens_id)
            .or_default()
            .push(segment_from_parts(
                row.id,
                row.created_at,
                row.status,
                row.narration_text,
                row.blocks,
                &row.source_keys,
            ));
    }
    let ordered_segments = lenses
        .iter()
        .flat_map(|lens| segments_by_lens.remove(&lens.id).unwrap_or_default())
        .collect::<Vec<_>>();
    let source_keys = dedupe_source_keys(
        ordered_segments
            .iter()
            .flat_map(|segment| &segment.source_keys),
    );
    let sources = load_eligible_sources_for_keys(transaction, user_id, &source_keys).await?;
    let mut plans = if !chaptered {
        legacy_narration_plan(&ordered_segments)
    } else if scope == Some("article_tier") || scope == Some("podcast_tier") {
        document_narration_plans(&ordered_segments)
    } else {
        narration_chapter_plans(&ordered_segments, 5 * 60)
    };
    for plan in &mut plans {
        plan.source_keys.retain(|key| sources.contains_key(key));
    }
    plans.retain(|plan| !plan.source_keys.is_empty());
    if plans.is_empty() {
        return Ok(PrepareNarrationOutcome::Empty);
    }
    let prompt_version = if scope.is_some() {
        4
    } else if chaptered {
        3
    } else {
        2
    };
    let first_lens = &lenses[0];
    let display_title = if scope.is_some() {
        program_title.to_owned()
    } else {
        first_lens.title.clone()
    };
    let episode_group_id = chaptered.then(|| {
        episode_group_id(
            program_key,
            &display_title,
            scope,
            prompt_version,
            &plans,
            &sources,
        )
    });
    let chapter_count = plans.len();
    let mut episodes = Vec::with_capacity(chapter_count);
    for plan in plans {
        let snapshot = source_snapshot(
            program_key,
            &display_title,
            scope,
            episode_group_id.as_deref(),
            chapter_count,
            &plan,
            &sources,
            chaptered,
        );
        let input_hash = if let Some(group_id) = &episode_group_id {
            stable_hash(&json!({
                "prompt_version": prompt_version,
                "episode_group_id": group_id,
                "chapter_index": plan.index,
                "source_snapshot": snapshot,
            }))
        } else {
            stable_hash(&json!({
                "prompt_version": prompt_version,
                "source_snapshot": snapshot,
            }))
        };
        let title = if scope == Some("article_tier") || scope == Some("podcast_tier") {
            plan.source_keys
                .first()
                .and_then(|key| sources.get(key))
                .map_or_else(
                    || format!("Chapter {}", plan.index + 1),
                    |source| source.title.clone(),
                )
        } else if chaptered {
            format!("{} — Chapter {}", display_title, plan.index + 1)
        } else {
            format!("{} briefing", first_lens.title)
        };
        let estimated_duration = if chaptered {
            plan.duration_seconds
        } else {
            i32::max(
                30,
                i32::try_from(plan.narration_text.len() / 14).unwrap_or(i32::MAX),
            )
        };
        let script = scope.is_none().then(|| {
            json!({
                "title": title,
                "estimated_duration_seconds": estimated_duration,
                "turns": [{"speaker": "host", "text": plan.narration_text}],
            })
        });
        let script_text = scope.is_none().then_some(plan.narration_text.as_str());
        let model = scope.is_none().then_some("deterministic");
        let row = sqlx::query_as::<_, AudioEpisodeRow>(
            r#"
            INSERT INTO audio_episodes (
                user_id, kind, status, title, input_hash, episode_group_id,
                chapter_index, source_item_ids, source_snapshot, script,
                script_text, prompt_version, model, audio_content_type,
                duration_seconds, share_enabled, created_at, updated_at
            ) VALUES (
                $1::bigint::integer, 'briefing_narration', 'pending', $2, $3,
                $4, $5, '[]'::jsonb, $6::jsonb, $7::jsonb, $8, $9,
                $10, 'audio/mpeg', $11, FALSE,
                timezone('UTC', now()), timezone('UTC', now())
            )
            ON CONFLICT (user_id, kind, input_hash) DO UPDATE SET
                title = EXCLUDED.title,
                episode_group_id = EXCLUDED.episode_group_id,
                chapter_index = EXCLUDED.chapter_index,
                source_item_ids = EXCLUDED.source_item_ids,
                source_snapshot = EXCLUDED.source_snapshot,
                script = CASE WHEN audio_episodes.status = 'completed' THEN audio_episodes.script
                              ELSE EXCLUDED.script END,
                script_text = CASE WHEN audio_episodes.status = 'completed' THEN audio_episodes.script_text
                                   ELSE EXCLUDED.script_text END,
                prompt_version = EXCLUDED.prompt_version,
                model = CASE WHEN audio_episodes.status = 'completed' THEN audio_episodes.model
                             ELSE EXCLUDED.model END,
                status = CASE WHEN audio_episodes.status = 'failed' THEN 'pending'
                              ELSE audio_episodes.status END,
                error_message = CASE WHEN audio_episodes.status = 'failed' THEN NULL
                                     ELSE audio_episodes.error_message END,
                audio_storage_path = CASE WHEN audio_episodes.status = 'failed' THEN NULL
                                          ELSE audio_episodes.audio_storage_path END,
                started_at = CASE WHEN audio_episodes.status = 'failed' THEN NULL
                                  ELSE audio_episodes.started_at END,
                completed_at = CASE WHEN audio_episodes.status = 'failed' THEN NULL
                                    ELSE audio_episodes.completed_at END,
                duration_seconds = CASE
                    WHEN audio_episodes.status = 'completed' THEN audio_episodes.duration_seconds
                    ELSE EXCLUDED.duration_seconds
                END,
                updated_at = timezone('UTC', now())
            RETURNING
                id::bigint AS id, kind, status, title,
                source_content_id::bigint AS source_content_id,
                source_item_ids::jsonb AS source_item_ids,
                source_snapshot::jsonb AS source_snapshot, script_text,
                audio_storage_path, duration_seconds, error_message,
                episode_group_id, chapter_index, created_at, updated_at
            "#,
        )
        .bind(user_id)
        .bind(&title)
        .bind(input_hash)
        .bind(episode_group_id.as_deref())
        .bind(chaptered.then_some(plan.index))
        .bind(snapshot)
        .bind(script)
        .bind(script_text)
        .bind(prompt_version)
        .bind(model)
        .bind(chaptered.then_some(plan.duration_seconds))
        .fetch_one(&mut **transaction)
        .await?;
        episodes.push(row.into());
    }
    Ok(PrepareNarrationOutcome::Ready(episodes))
}

pub async fn load_briefing_narration(
    pool: &PgPool,
    user_id: i64,
    episode_group_id: &str,
) -> Result<Vec<AudioEpisodeProjection>, BriefingRepositoryError> {
    Ok(sqlx::query_as::<_, AudioEpisodeRow>(
        r#"
        SELECT id::bigint AS id, kind, status, title,
               source_content_id::bigint AS source_content_id,
               source_item_ids::jsonb AS source_item_ids,
               source_snapshot::jsonb AS source_snapshot, script_text,
               audio_storage_path, duration_seconds, error_message,
               episode_group_id, chapter_index, created_at, updated_at
        FROM audio_episodes
        WHERE user_id::bigint = $1 AND kind = 'briefing_narration'
          AND episode_group_id = $2
        ORDER BY chapter_index, id
        "#,
    )
    .bind(user_id)
    .bind(episode_group_id)
    .fetch_all(pool)
    .await?
    .into_iter()
    .map(Into::into)
    .collect())
}

pub const fn public_audio_episode_error_message() -> &'static str {
    PUBLIC_AUDIO_EPISODE_ERROR_MESSAGE
}

async fn load_state(
    pool: &PgPool,
    user_id: i64,
) -> Result<BriefingStateProjection, BriefingRepositoryError> {
    let state = sqlx::query_as::<_, StateRow>(
        r#"
        SELECT version, masthead_title, masthead_deck
        FROM briefing_states WHERE user_id::bigint = $1
        "#,
    )
    .bind(user_id)
    .fetch_optional(pool)
    .await?;
    Ok(state.map_or_else(
        || BriefingStateProjection {
            version: 0,
            masthead_title: DEFAULT_MASTHEAD_TITLE.to_owned(),
            masthead_deck: DEFAULT_MASTHEAD_DECK.to_owned(),
        },
        |state| BriefingStateProjection {
            version: state.version,
            masthead_title: state.masthead_title,
            masthead_deck: state.masthead_deck,
        },
    ))
}

async fn load_first_run(
    pool: &PgPool,
    user_id: i64,
) -> Result<Option<BriefingFirstRunProjection>, BriefingRepositoryError> {
    let run = sqlx::query_as::<_, FirstRunRow>(
        r#"
        SELECT id::bigint AS id, revision
        FROM onboarding_first_edition_runs
        WHERE user_id::bigint = $1 AND status = 'active'
        ORDER BY id DESC LIMIT 1
        "#,
    )
    .bind(user_id)
    .fetch_optional(pool)
    .await?;
    let Some(run) = run else {
        return Ok(None);
    };
    let sources = sqlx::query_as::<_, FirstRunSourceRow>(
        r#"
        SELECT display_name, position, status, processed_item_count, completed_at
        FROM onboarding_first_edition_sources
        WHERE run_id::bigint = $1
        ORDER BY completed_at ASC NULLS LAST, position, id
        "#,
    )
    .bind(run.id)
    .fetch_all(pool)
    .await?
    .into_iter()
    .map(|source| FirstRunSourceProjection {
        display_name: source.display_name,
        position: source.position,
        status: source.status,
        processed_item_count: source.processed_item_count.max(0),
        completed_at: source.completed_at.map(|value| value.and_utc()),
    })
    .collect();
    Ok(Some(BriefingFirstRunProjection {
        run_id: run.id,
        revision: run.revision,
        sources,
    }))
}

async fn load_sources(
    pool: &PgPool,
    user_id: i64,
    source_keys: &[String],
) -> Result<HashMap<String, BriefingSourceProjection>, BriefingRepositoryError> {
    let (content_ids, news_ids) = parse_source_ids(source_keys);
    let mut found = HashMap::new();
    if !content_ids.is_empty() {
        let rows = sqlx::query_as::<_, ContentSourceRow>(
            r#"
            SELECT content.id::bigint AS id, content.content_type, content.url,
                   content.source_url, content.title, content.source,
                   content.content_metadata::jsonb AS metadata,
                   content.created_at, content.publication_date
            FROM contents AS content
            WHERE content.id::bigint = ANY($2::bigint[])
              AND EXISTS (
                  SELECT 1 FROM content_status AS user_status
                  WHERE user_status.user_id::bigint = $1
                    AND user_status.content_id = content.id
              )
            "#,
        )
        .bind(user_id)
        .bind(&content_ids)
        .fetch_all(pool)
        .await?;
        for row in rows {
            let id = row.id;
            found.insert(
                format!("content:{id}"),
                BriefingSourceProjection::Content(ContentBriefingSourceProjection {
                    id,
                    content_type: row.content_type,
                    url: row.url,
                    source_url: row.source_url,
                    title: row.title,
                    source: row.source,
                    metadata: row.metadata,
                    created_at: row.created_at.and_utc(),
                    publication_date: row.publication_date.map(|value| value.and_utc()),
                }),
            );
        }
    }
    if !news_ids.is_empty() {
        let rows = sqlx::query_as::<_, NewsSourceRow>(
            r#"
            WITH valid_aggregators AS (
                SELECT lower(btrim(config::jsonb ->> 'key')) AS source_key,
                       CASE WHEN jsonb_typeof(config::jsonb -> 'topics') = 'array'
                            THEN config::jsonb -> 'topics' ELSE '[]'::jsonb END AS topics
                FROM user_scraper_configs
                WHERE user_id::bigint = $1 AND scraper_type = 'aggregator'
                  AND is_active IS TRUE
                  AND lower(btrim(config::jsonb ->> 'key')) = ANY(ARRAY[
                      'brutalist', 'finurls', 'hackernews', 'mediagazer',
                      'memeorandum', 'sciurls', 'techmeme'
                  ])
            )
            SELECT news.id::bigint AS id, news.summary_text,
                   news.summary_key_points::jsonb AS summary_key_points,
                   news.raw_metadata::jsonb AS raw_metadata, news.article_url,
                   news.canonical_story_url, news.canonical_item_url,
                   news.published_at, news.processed_at, news.ingested_at,
                   news.created_at, discussion.platform AS discussion_platform,
                   discussion.comment_count AS discussion_comment_count,
                   discussion.discussion_url,
                   discussion.summary::jsonb AS discussion_summary,
                   discussion.summary_status AS discussion_summary_status,
                   discussion.last_refresh_status AS discussion_last_refresh_status,
                   discussion.summary_generated_at AS discussion_summary_generated_at,
                   discussion.last_comments_fetched_at AS discussion_last_comments_fetched_at,
                   discussion.last_count_checked_at AS discussion_last_count_checked_at
            FROM news_items AS news
            LEFT JOIN news_item_discussions AS discussion
              ON discussion.news_item_id = news.id
            WHERE news.id::bigint = ANY($2::bigint[])
              AND (
                  (news.visibility_scope = 'user' AND news.owner_user_id::bigint = $1)
                  OR (
                      news.visibility_scope = 'global'
                      AND EXISTS (
                          SELECT 1 FROM valid_aggregators AS selected
                          WHERE selected.source_key = lower(btrim(coalesce(news.platform, '')))
                            AND (
                                selected.source_key <> 'brutalist'
                                OR jsonb_array_length(selected.topics) = 0
                                OR lower(btrim(coalesce(
                                    news.raw_metadata::jsonb #>> '{aggregator,topic}', ''
                                ))) IN (
                                    SELECT lower(btrim(value))
                                    FROM jsonb_array_elements_text(selected.topics) AS value
                                )
                            )
                      )
                  )
              )
            "#,
        )
        .bind(user_id)
        .bind(&news_ids)
        .fetch_all(pool)
        .await?;
        for row in rows {
            let id = row.id;
            let discussion = row
                .discussion_platform
                .map(|platform| BriefingDiscussionProjection {
                    platform,
                    comment_count: row.discussion_comment_count,
                    discussion_url: row.discussion_url,
                    summary: row.discussion_summary,
                    summary_status: row
                        .discussion_summary_status
                        .unwrap_or_else(|| "not_ready".to_owned()),
                    last_refresh_status: row
                        .discussion_last_refresh_status
                        .unwrap_or_else(|| "pending".to_owned()),
                    summary_generated_at: row
                        .discussion_summary_generated_at
                        .map(|value| value.and_utc()),
                    last_comments_fetched_at: row
                        .discussion_last_comments_fetched_at
                        .map(|value| value.and_utc()),
                    last_count_checked_at: row
                        .discussion_last_count_checked_at
                        .map(|value| value.and_utc()),
                });
            found.insert(
                format!("news:{id}"),
                BriefingSourceProjection::News(NewsBriefingSourceProjection {
                    id,
                    summary_text: row.summary_text,
                    summary_key_points: row.summary_key_points,
                    raw_metadata: row.raw_metadata,
                    article_url: row.article_url,
                    canonical_story_url: row.canonical_story_url,
                    canonical_item_url: row.canonical_item_url,
                    published_at: row.published_at.map(|value| value.and_utc()),
                    processed_at: row.processed_at.map(|value| value.and_utc()),
                    ingested_at: row.ingested_at.and_utc(),
                    created_at: row.created_at.and_utc(),
                    discussion,
                }),
            );
        }
    }
    Ok(found)
}

async fn read_source_keys_pool(
    pool: &PgPool,
    user_id: i64,
    source_keys: &[String],
) -> Result<HashSet<String>, BriefingRepositoryError> {
    let (content_ids, news_ids) = parse_source_ids(source_keys);
    let content = if content_ids.is_empty() {
        Vec::new()
    } else {
        sqlx::query_scalar::<_, i64>(
            r#"
            SELECT DISTINCT content_id::bigint FROM content_read_status
            WHERE user_id::bigint = $1 AND content_id::bigint = ANY($2::bigint[])
            "#,
        )
        .bind(user_id)
        .bind(&content_ids)
        .fetch_all(pool)
        .await?
    };
    let news = if news_ids.is_empty() {
        Vec::new()
    } else {
        sqlx::query_scalar::<_, i64>(read_news_keys_sql())
            .bind(user_id)
            .bind(&news_ids)
            .fetch_all(pool)
            .await?
    };
    Ok(content
        .into_iter()
        .map(|id| format!("content:{id}"))
        .chain(news.into_iter().map(|id| format!("news:{id}")))
        .collect())
}

async fn read_source_keys_transaction(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
    source_keys: &[String],
) -> Result<HashSet<String>, BriefingRepositoryError> {
    let (content_ids, news_ids) = parse_source_ids(source_keys);
    let content = if content_ids.is_empty() {
        Vec::new()
    } else {
        sqlx::query_scalar::<_, i64>(
            r#"
            SELECT DISTINCT content_id::bigint FROM content_read_status
            WHERE user_id::bigint = $1 AND content_id::bigint = ANY($2::bigint[])
            "#,
        )
        .bind(user_id)
        .bind(&content_ids)
        .fetch_all(&mut **transaction)
        .await?
    };
    let news = if news_ids.is_empty() {
        Vec::new()
    } else {
        sqlx::query_scalar::<_, i64>(read_news_keys_sql())
            .bind(user_id)
            .bind(&news_ids)
            .fetch_all(&mut **transaction)
            .await?
    };
    Ok(content
        .into_iter()
        .map(|id| format!("content:{id}"))
        .chain(news.into_iter().map(|id| format!("news:{id}")))
        .collect())
}

const fn read_news_keys_sql() -> &'static str {
    r#"
    SELECT requested.id::bigint
    FROM unnest($2::bigint[]) AS requested(id)
    JOIN news_items AS requested_news ON requested_news.id::bigint = requested.id
    WHERE EXISTS (
        SELECT 1
        FROM news_items AS member
        JOIN news_item_read_status AS read_status ON read_status.news_item_id = member.id
        WHERE read_status.user_id::bigint = $1
          AND coalesce(member.representative_news_item_id, member.id) =
              coalesce(requested_news.representative_news_item_id, requested_news.id)
    )
    "#
}

async fn ensure_and_lock_state(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
) -> Result<i32, BriefingRepositoryError> {
    sqlx::query(
        r#"
        INSERT INTO briefing_states (user_id, version, masthead_title, masthead_deck)
        VALUES ($1::bigint::integer, 0, $2, $3)
        ON CONFLICT (user_id) DO NOTHING
        "#,
    )
    .bind(user_id)
    .bind(DEFAULT_MASTHEAD_TITLE)
    .bind(DEFAULT_MASTHEAD_DECK)
    .execute(&mut **transaction)
    .await?;
    Ok(sqlx::query_scalar::<_, i32>(
        "SELECT version FROM briefing_states WHERE user_id::bigint = $1 FOR UPDATE",
    )
    .bind(user_id)
    .fetch_one(&mut **transaction)
    .await?)
}

async fn mark_sources_locked(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
    source_keys: &[String],
    version: i32,
) -> Result<BriefingReadMarkProjection, BriefingRepositoryError> {
    let (content_ids, news_ids) = parse_source_ids(source_keys);
    let content_marked = if content_ids.is_empty() {
        0
    } else {
        sqlx::query_scalar::<_, i64>(
            r#"
            WITH existing AS (
                SELECT id FROM contents WHERE id::bigint = ANY($2::bigint[])
            ), upserted AS (
                INSERT INTO content_read_status (user_id, content_id, read_at, created_at)
                SELECT $1::bigint::integer, id, timezone('UTC', now()), timezone('UTC', now())
                FROM existing
                ON CONFLICT (user_id, content_id) DO UPDATE SET read_at = EXCLUDED.read_at
            )
            SELECT count(*)::bigint FROM existing
            "#,
        )
        .bind(user_id)
        .bind(&content_ids)
        .fetch_one(&mut **transaction)
        .await?
    };
    let news_marked = if news_ids.is_empty() {
        0
    } else {
        sqlx::query_scalar::<_, i64>(
            r#"
            WITH authorized AS (
                SELECT news.id, news.representative_news_item_id
                FROM news_items AS news
                WHERE news.id::bigint = ANY($2::bigint[])
                  AND EXISTS (
                      SELECT 1 FROM briefing_segments AS segment
                      WHERE segment.user_id::bigint = $1
                        AND segment.source_keys::jsonb @>
                            jsonb_build_array('news:' || news.id::text)
                  )
            ), targets AS (
                SELECT id AS target_id FROM authorized
                UNION SELECT representative_news_item_id FROM authorized
                WHERE representative_news_item_id IS NOT NULL
            ), inserted AS (
                INSERT INTO news_item_read_status (user_id, news_item_id, read_at, created_at)
                SELECT $1::bigint::integer, target_id,
                       timezone('UTC', now()), timezone('UTC', now())
                FROM targets
                ON CONFLICT (user_id, news_item_id) DO NOTHING
                RETURNING news_item_id
            )
            SELECT count(*)::bigint
            FROM inserted JOIN authorized ON authorized.id = inserted.news_item_id
            "#,
        )
        .bind(user_id)
        .bind(&news_ids)
        .fetch_one(&mut **transaction)
        .await?
    };
    let retired = sqlx::query_scalar::<_, i64>(
        r#"
        WITH retired AS (
            UPDATE briefing_segments AS segment
            SET status = 'retired', updated_at = timezone('UTC', now())
            WHERE segment.user_id::bigint = $1
              AND segment.status IN ('active', 'degraded')
              AND jsonb_array_length(segment.source_keys::jsonb) > 0
              AND NOT EXISTS (
                  SELECT 1
                  FROM jsonb_array_elements_text(segment.source_keys::jsonb) AS source(key)
                  WHERE (
                      source.key LIKE 'content:%'
                      AND NOT EXISTS (
                          SELECT 1 FROM content_read_status AS read_status
                          WHERE read_status.user_id::bigint = $1
                            AND read_status.content_id::bigint =
                                substring(source.key FROM 9)::bigint
                      )
                  ) OR (
                      source.key LIKE 'news:%'
                      AND NOT EXISTS (
                          SELECT 1
                          FROM news_items AS requested_news
                          JOIN news_items AS member ON
                              coalesce(member.representative_news_item_id, member.id) =
                              coalesce(requested_news.representative_news_item_id, requested_news.id)
                          JOIN news_item_read_status AS read_status
                            ON read_status.news_item_id = member.id
                           AND read_status.user_id::bigint = $1
                          WHERE requested_news.id::bigint = substring(source.key FROM 6)::bigint
                      )
                  ) OR (source.key NOT LIKE 'content:%' AND source.key NOT LIKE 'news:%')
              )
            RETURNING id
        )
        SELECT count(*)::bigint FROM retired
        "#,
    )
    .bind(user_id)
    .fetch_one(&mut **transaction)
    .await?;
    let marked = content_marked.saturating_add(news_marked);
    let visible_change = marked > 0 || retired > 0;
    let next_version = if visible_change {
        sqlx::query_scalar::<_, i32>(
            r#"
            UPDATE briefing_states SET version = version + 1
            WHERE user_id::bigint = $1 RETURNING version
            "#,
        )
        .bind(user_id)
        .fetch_one(&mut **transaction)
        .await?
    } else {
        version
    };
    Ok(BriefingReadMarkProjection {
        marked: usize::try_from(marked).unwrap_or(usize::MAX),
        retired: usize::try_from(retired).unwrap_or(usize::MAX),
        version: next_version,
    })
}

fn segment_from_row(row: SegmentRow) -> BriefingSegmentProjection {
    segment_from_parts(
        row.id,
        row.created_at,
        row.status,
        row.narration_text,
        row.blocks,
        &row.source_keys,
    )
}

fn segment_from_parts(
    id: i64,
    created_at: NaiveDateTime,
    status: String,
    narration_text: String,
    blocks: Value,
    source_keys: &Value,
) -> BriefingSegmentProjection {
    BriefingSegmentProjection {
        id,
        created_at: created_at.and_utc(),
        status,
        narration_text,
        blocks,
        source_keys: json_string_array(source_keys),
    }
}

fn segment_metadata_from_row(row: &SegmentMetadataRow) -> BriefingSegmentMetadataProjection {
    BriefingSegmentMetadataProjection {
        id: row.id,
        lens_id: row.lens_id,
        created_at: row.created_at.and_utc(),
        source_keys: json_string_array(&row.source_keys),
    }
}

fn parse_source_ids(source_keys: &[String]) -> (Vec<i64>, Vec<i64>) {
    let mut content_ids = BTreeSet::new();
    let mut news_ids = BTreeSet::new();
    for key in source_keys {
        let Some((kind, raw_id)) = key.split_once(':') else {
            continue;
        };
        let Ok(id) = raw_id.parse::<i64>() else {
            continue;
        };
        if id <= 0 {
            continue;
        }
        match kind {
            "content" => {
                content_ids.insert(id);
            }
            "news" => {
                news_ids.insert(id);
            }
            _ => {}
        }
    }
    (
        content_ids.into_iter().collect(),
        news_ids.into_iter().collect(),
    )
}

fn dedupe_source_keys<'a>(keys: impl IntoIterator<Item = &'a String>) -> Vec<String> {
    let mut seen = HashSet::new();
    keys.into_iter()
        .filter(|key| seen.insert((*key).clone()))
        .cloned()
        .collect()
}

fn dedupe_owned_source_keys(keys: impl IntoIterator<Item = String>) -> Vec<String> {
    let mut seen = HashSet::new();
    keys.into_iter()
        .filter(|key| seen.insert(key.clone()))
        .collect()
}

fn json_string_array(value: &Value) -> Vec<String> {
    value
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .map(str::to_owned)
        .collect()
}

#[derive(Debug, Error)]
pub enum BriefingRepositoryError {
    #[error("briefing database operation failed")]
    Sqlx(#[from] sqlx::Error),
    #[error("Briefing cursor belongs to another Lens")]
    CursorWrongLens,
    #[error("Briefing cursor anchor is no longer active")]
    StaleCursor,
    #[error("Briefing cursor anchor does not match")]
    CursorAnchorMismatch,
}
