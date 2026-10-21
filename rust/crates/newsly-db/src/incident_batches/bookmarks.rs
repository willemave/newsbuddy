//! Explicit, audited reconciliation of reviewed pre-Knowledge X imports.
use chrono::NaiveDateTime;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use sqlx::{FromRow, PgPool};
use uuid::Uuid;

const OPERATION: &str = "legacy_bookmark_reconciliation";

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct BookmarkReconciliationScope {
    pub user_id: i64,
    pub content_ids: Vec<i64>,
    pub imported_before: NaiveDateTime,
    pub expected_version: i64,
}

#[derive(Debug, Clone, Serialize, Deserialize, FromRow)]
pub struct BookmarkBeforeState {
    pub content_id: i64,
    pub imported_at: NaiveDateTime,
    pub inbox_created_at: NaiveDateTime,
    pub inbox_updated_at: Option<NaiveDateTime>,
    pub saved_at: Option<chrono::DateTime<chrono::Utc>>,
}

#[derive(Debug, Clone, Serialize, Deserialize, FromRow)]
pub struct BookmarkSegmentBeforeState {
    pub id: i64,
    pub status: String,
    pub updated_at: NaiveDateTime,
    pub source_keys: Value,
}

#[derive(Debug, Clone, Serialize, Deserialize, FromRow)]
pub struct BookmarkPendingBeforeState {
    pub id: i64,
    pub source_id: i64,
    pub row: Value,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BookmarkReconciliationReport {
    pub batch_id: Uuid,
    pub applied: bool,
    pub replayed: bool,
    pub version_before: i64,
    pub version_after: i64,
    pub bookmarks: Vec<BookmarkBeforeState>,
    pub segments: Vec<BookmarkSegmentBeforeState>,
    pub pending: Vec<BookmarkPendingBeforeState>,
}

fn rejected(message: &str) -> sqlx::Error {
    sqlx::Error::Protocol(message.to_owned())
}

/// Dry-run defaults belong to the caller; applying always requires exact scope and audit authority.
/// Locks share the user -> Briefing-state order used by publication, fencing in-flight composers.
pub async fn reconcile_legacy_bookmarks(
    pool: &PgPool,
    scope: &BookmarkReconciliationScope,
    batch_id: Uuid,
    actor: &str,
    reason: &str,
    apply: bool,
) -> Result<BookmarkReconciliationReport, sqlx::Error> {
    if scope.user_id <= 0
        || scope.expected_version < 0
        || scope.content_ids.is_empty()
        || scope.content_ids.len() > 100
        || scope.content_ids.iter().any(|id| *id <= 0)
        || scope.content_ids.windows(2).any(|pair| pair[0] >= pair[1])
        || actor.trim().is_empty()
        || reason.trim().is_empty()
    {
        return Err(rejected(
            "supply an owner, 1..100 sorted unique positive content IDs, version, actor and reason",
        ));
    }
    let mut tx = pool.begin().await?;
    sqlx::query("SET LOCAL lock_timeout = '5s'")
        .execute(&mut *tx)
        .await?;
    // Fence publication's FOR SHARE while allowing foreign-key KEY SHARE locks from reads/saves.
    let active: Option<i64> = sqlx::query_scalar(
        "SELECT id::bigint FROM users WHERE id::bigint=$1 AND is_active FOR NO KEY UPDATE",
    )
    .bind(scope.user_id)
    .fetch_optional(&mut *tx)
    .await?;
    if active.is_none() {
        return Err(rejected("active owner not found"));
    }
    let previous: Option<(String, String, String, Value)> = sqlx::query_as(
        "SELECT operation,actor,reason,targets FROM operator_incident_batches WHERE batch_id=$1 FOR UPDATE",
    ).bind(batch_id).fetch_optional(&mut *tx).await?;
    if let Some((operation, prior_actor, prior_reason, targets)) = previous {
        if operation != OPERATION
            || prior_actor != actor
            || prior_reason != reason
            || targets.get("scope") != Some(&json!(scope))
        {
            return Err(rejected(
                "batch ID already belongs to different authority or scope",
            ));
        }
        let mut report: BookmarkReconciliationReport = serde_json::from_value(
            targets
                .get("before")
                .cloned()
                .ok_or_else(|| rejected("missing batch before-state"))?,
        )
        .map_err(|_| rejected("invalid batch before-state"))?;
        report.applied = apply;
        report.replayed = true;
        tx.rollback().await?;
        return Ok(report);
    }
    let version: i64 = sqlx::query_scalar(
        "SELECT version::bigint FROM briefing_states WHERE user_id::bigint=$1 FOR UPDATE",
    )
    .bind(scope.user_id)
    .fetch_one(&mut *tx)
    .await?;
    if version != scope.expected_version {
        return Err(rejected(
            "Briefing version changed; rerun the dry-run inventory",
        ));
    }
    // Require the original untouched inbox membership. A later deliberate add is not a legacy row.
    let bookmarks = sqlx::query_as::<_, BookmarkBeforeState>(r"
        SELECT c.id::bigint AS content_id,c.created_at AS imported_at,
               s.created_at AS inbox_created_at,s.updated_at AS inbox_updated_at,k.saved_at
        FROM contents c JOIN content_status s ON s.content_id=c.id AND s.user_id::bigint=$1
        LEFT JOIN content_knowledge_saves k ON k.content_id=c.id AND k.user_id::bigint=$1
        WHERE c.id::bigint=ANY($2::bigint[]) AND c.content_type='article' AND c.status='completed'
          AND c.platform='twitter' AND s.status='inbox'
          AND c.created_at<$3 AND s.created_at<$3 AND COALESCE(s.updated_at,s.created_at)<$3
          AND COALESCE(c.content_metadata::jsonb#>>'{processing,submitted_via}',c.content_metadata::jsonb->>'submitted_via',c.content_metadata::jsonb#>>'{domain,submitted_via}')='x_bookmarks'
          AND COALESCE(c.content_metadata::jsonb#>>'{processing,submitted_by_user_id}',c.content_metadata::jsonb->>'submitted_by_user_id',c.content_metadata::jsonb#>>'{domain,submitted_by_user_id}')=$1::text
          AND c.url ~ '^https://(www\.)?(x\.com|twitter\.com)/[^/?#]+/status/[0-9]+/?$'
        ORDER BY c.id FOR NO KEY UPDATE OF c,s
    ").bind(scope.user_id).bind(&scope.content_ids).bind(scope.imported_before)
        .fetch_all(&mut *tx).await?;
    if bookmarks
        .iter()
        .map(|row| row.content_id)
        .collect::<Vec<_>>()
        != scope.content_ids
    {
        return Err(rejected(
            "inventory changed or includes a non-legacy bookmark; review exact targets",
        ));
    }
    let saved: Vec<(i64, chrono::DateTime<chrono::Utc>)> = sqlx::query_as(
        "SELECT content_id::bigint,saved_at FROM content_knowledge_saves WHERE user_id::bigint=$1 AND content_id::bigint=ANY($2::bigint[]) ORDER BY content_id FOR UPDATE",
    ).bind(scope.user_id).bind(&scope.content_ids).fetch_all(&mut *tx).await?;
    if bookmarks.iter().any(|row| {
        row.saved_at
            != saved
                .iter()
                .find(|(id, _)| *id == row.content_id)
                .map(|(_, date)| *date)
    }) {
        return Err(rejected(
            "Knowledge membership changed while locking the inventory; rerun dry-run",
        ));
    }
    let keys = scope
        .content_ids
        .iter()
        .map(|id| format!("content:{id}"))
        .collect::<Vec<_>>();
    let segments = sqlx::query_as::<_, BookmarkSegmentBeforeState>(
        "SELECT id::bigint,status,updated_at,source_keys FROM briefing_segments WHERE user_id::bigint=$1 AND status IN ('active','degraded') AND source_keys ?| $2::text[] ORDER BY id FOR UPDATE",
    ).bind(scope.user_id).bind(&keys).fetch_all(&mut *tx).await?;
    for segment in &segments {
        let Some(sources) = segment.source_keys.as_array() else {
            return Err(rejected("invalid segment coverage"));
        };
        if sources.iter().any(|source| {
            !source
                .as_str()
                .is_some_and(|key| keys.iter().any(|owned| owned == key))
        }) {
            return Err(rejected(
                "mixed-source segment requires a separate coverage-preserving repair",
            ));
        }
    }
    // Do not invalidate historical or in-flight audio without a separately reviewed audio repair.
    let has_audio: bool = sqlx::query_scalar(
        "SELECT EXISTS(SELECT 1 FROM audio_episodes WHERE user_id::bigint=$1 AND kind='briefing_narration' AND (source_snapshot->'source_keys') ?| $2::text[])",
    ).bind(scope.user_id).bind(&keys).fetch_one(&mut *tx).await?;
    if has_audio {
        return Err(rejected(
            "existing narration covers these sources; review audio settlement before applying",
        ));
    }
    let pending = sqlx::query_as::<_, BookmarkPendingBeforeState>(
        "SELECT p.id::bigint,p.source_id::bigint,to_jsonb(p) AS row FROM briefing_pending_sources p WHERE p.user_id::bigint=$1 AND p.source_kind='content' AND p.source_id::bigint=ANY($2::bigint[]) ORDER BY p.id FOR UPDATE",
    ).bind(scope.user_id).bind(&scope.content_ids).fetch_all(&mut *tx).await?;
    let report = BookmarkReconciliationReport {
        batch_id,
        applied: apply,
        replayed: false,
        version_before: version,
        version_after: version
            .checked_add(1)
            .ok_or_else(|| rejected("Briefing version exhausted"))?,
        bookmarks,
        segments,
        pending,
    };
    if !apply {
        tx.rollback().await?;
        return Ok(report);
    }
    super::record_batch(
        &mut tx,
        batch_id,
        OPERATION,
        actor,
        reason,
        &json!({"scope":scope,"before":report}),
    )
    .await?;
    let inserted = sqlx::query(
        r"
        INSERT INTO content_knowledge_saves(user_id,content_id,saved_at,created_at)
        SELECT $1::bigint::integer,id,created_at AT TIME ZONE 'UTC',now()
        FROM contents WHERE id::bigint=ANY($2::bigint[])
        ON CONFLICT (user_id,content_id) DO NOTHING
    ",
    )
    .bind(scope.user_id)
    .bind(&scope.content_ids)
    .execute(&mut *tx)
    .await?
    .rows_affected();
    let expected_inserted = report
        .bookmarks
        .iter()
        .filter(|row| row.saved_at.is_none())
        .count();
    if inserted != expected_inserted as u64 {
        return Err(rejected(
            "Knowledge membership changed during repair; review a fresh inventory",
        ));
    }
    let archived = sqlx::query("UPDATE content_status SET status='archived',updated_at=timezone('UTC',now()) WHERE user_id::bigint=$1 AND content_id::bigint=ANY($2::bigint[]) AND status='inbox'")
        .bind(scope.user_id).bind(&scope.content_ids).execute(&mut *tx).await?.rows_affected();
    let segment_ids = report.segments.iter().map(|row| row.id).collect::<Vec<_>>();
    let retired = sqlx::query("UPDATE briefing_segments SET status='retired',updated_at=timezone('UTC',now()) WHERE id::bigint=ANY($1::bigint[]) AND status IN ('active','degraded')")
        .bind(&segment_ids).execute(&mut *tx).await?.rows_affected();
    let removed = sqlx::query("DELETE FROM briefing_pending_sources WHERE user_id::bigint=$1 AND source_kind='content' AND source_id::bigint=ANY($2::bigint[])")
        .bind(scope.user_id).bind(&scope.content_ids).execute(&mut *tx).await?.rows_affected();
    if archived != scope.content_ids.len() as u64
        || retired != report.segments.len() as u64
        || removed != report.pending.len() as u64
    {
        return Err(rejected(
            "locked repair inventory changed; transaction rolled back",
        ));
    }
    sqlx::query("UPDATE briefing_states SET version=$2 WHERE user_id::bigint=$1")
        .bind(scope.user_id)
        .bind(report.version_after)
        .execute(&mut *tx)
        .await?;
    tx.commit().await?;
    Ok(report)
}
