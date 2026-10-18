//! Bounded, user-scoped News admission. Provider calls never run in this repository.
use serde_json::Value;
use sqlx::{AssertSqlSafe, FromRow, PgConnection, PgPool, Postgres, Transaction};
use uuid::Uuid;

use crate::news_filter_preferences::{NewsFilterPreferenceRecord, load_current};
use crate::news_visibility::{SUBSCRIBED_AGGREGATORS_CTE, visible_news_predicate};

pub const EVALUATOR_VERSION: &str = "jev-1.14-20261014-prose-choice-v1-p90";
pub const CANDIDATE_LIMIT: usize = 128;

#[derive(Debug, Clone, FromRow)]
pub struct NewsFilterCandidate {
    pub news_item_id: i64,
    pub input_hash: String,
    pub title: String,
    pub summary: String,
    pub key_points: Vec<String>,
}
#[derive(Debug, Clone, Default)]
pub struct NewsFilterPass {
    pub revision: i64,
    pub unavailable: Vec<(i64, String)>,
    pub examined: bool,
    pub more_work: bool,
}
#[derive(Debug)]
pub struct NewsFilterPreparation {
    pub usage_day: chrono::NaiveDate,
    pub preference: NewsFilterPreferenceRecord,
    pub candidates: Vec<NewsFilterCandidate>,
    pub lease_token: Uuid,
    pub more_work: bool,
    pub examined: bool,
}
#[derive(Debug)]
pub struct NewsFilterStoredDecision {
    pub news_item_id: i64,
    pub input_hash: String,
    pub choice: String,
    pub no_match_probability: f64,
    pub probabilities: Value,
}
#[derive(Debug, Clone)]
pub struct NewsFilterUsage {
    pub news_item_id: i64,
    pub generation_id: Option<String>,
    pub input_tokens: u64,
    pub output_tokens: u64,
    pub cost_usd: Option<f64>,
    pub outcome: String,
}
#[derive(Debug, FromRow)]
pub struct NewsFilterListRow {
    pub news_item_id: i64,
    pub filter_status: String,
}

// Static module-owned SQL fragments only; runtime values are always bound.
fn evidence_columns() -> &'static str {
    r"news.id::bigint AS news_item_id, news_filter_input_hash(news) AS input_hash,
    COALESCE(NULLIF(btrim(news.raw_metadata::jsonb #>> '{summary,title}'),''),
             NULLIF(btrim(news.raw_metadata::jsonb #>> '{article,title}'),''),
             left(btrim(news.summary_text),120),'News item') AS title,
    COALESCE(btrim(news.summary_text),'') AS summary,
    ARRAY(SELECT jsonb_array_elements_text(CASE WHEN jsonb_typeof(news.summary_key_points::jsonb)='array'
          THEN news.summary_key_points::jsonb ELSE '[]'::jsonb END)) AS key_points"
}
fn candidate_predicate() -> String {
    format!(
        r"news.status='ready' AND news.representative_news_item_id IS NULL AND {}
    AND COALESCE(news.published_at,news.ingested_at) >= timezone('UTC',now())-interval '7 days'
    AND NOT EXISTS(SELECT 1 FROM news_item_read_status r JOIN news_items member ON member.id=r.news_item_id
        WHERE r.user_id=$1::bigint AND COALESCE(member.representative_news_item_id,member.id)=news.id)
    AND NOT EXISTS(SELECT 1 FROM briefing_segments s WHERE s.user_id=$1::bigint
        AND s.status IN ('active','degraded') AND s.source_keys::jsonb ? ('news:' || news.id::text))",
        visible_news_predicate("news")
    )
}

/// User-row locking has the same order as Settings/onboarding: user, budget, Briefing state.
pub async fn lock_active_user(
    connection: &mut PgConnection,
    user_id: i64,
) -> Result<bool, sqlx::Error> {
    Ok(sqlx::query_scalar::<_, i64>(
        "SELECT id::bigint FROM users WHERE id::bigint=$1 AND is_active FOR SHARE",
    )
    .bind(user_id)
    .fetch_optional(connection)
    .await?
    .is_some())
}

pub async fn prepare_refresh(
    transaction: &mut Transaction<'_, Postgres>,
    task_id: i64,
    user_id: i64,
    expected_lease: Uuid,
) -> Result<Option<NewsFilterPreparation>, sqlx::Error> {
    if !lock_active_user(transaction, user_id).await? {
        return Ok(None);
    }
    let preference = load_current(transaction, user_id).await?;
    if !preference.enabled {
        return Ok(None);
    }
    let lease_token=sqlx::query_scalar::<_,Uuid>("SELECT lease_token FROM processing_tasks WHERE id::bigint=$1 AND owner_user_id::bigint=$2 AND task_type='briefing_refresh' AND lease_token=$3 AND status='processing' AND lease_expires_at>timezone('UTC',clock_timestamp()) FOR SHARE")
        .bind(task_id).bind(user_id).bind(expected_lease).fetch_one(&mut **transaction).await?;
    let mut candidates:Vec<NewsFilterCandidate>=sqlx::query_as(AssertSqlSafe(format!(r"{SUBSCRIBED_AGGREGATORS_CTE}
        SELECT {} FROM news_items news WHERE {}
        AND NOT EXISTS(SELECT 1 FROM news_filter_decisions d WHERE d.user_id=$1::bigint AND d.news_item_id=news.id
            AND d.revision=$2 AND d.input_hash=news_filter_input_hash(news) AND d.evaluator_version=$3)
        AND NOT EXISTS(SELECT 1 FROM briefing_pending_sources p WHERE p.user_id=$1::bigint AND p.source_kind='news' AND p.source_id=news.id AND p.filter_revision=$2 AND p.filter_input_hash=news_filter_input_hash(news) AND p.filter_outcome='unavailable')
        ORDER BY row_number() OVER(PARTITION BY news.platform,news.raw_metadata::jsonb #>> '{{aggregator,topic}}'
            ORDER BY COALESCE(news.published_at,news.ingested_at) DESC,news.id DESC),
            COALESCE(news.published_at,news.ingested_at) DESC,news.id DESC LIMIT 129",evidence_columns(),candidate_predicate())))
        .bind(user_id).bind(preference.revision).bind(EVALUATOR_VERSION).fetch_all(&mut **transaction).await?;
    // A retry may arrive after decisions committed but before the first cohort froze.
    // Existing cached exclusions (and old sources) still count as an examined corpus.
    let examined = if candidates.is_empty() {
        let visible = visible_news_predicate("news");
        sqlx::query_scalar::<_, bool>(AssertSqlSafe(format!(
            r"{SUBSCRIBED_AGGREGATORS_CTE}
            SELECT EXISTS(SELECT 1 FROM news_items news WHERE news.status='ready'
                AND news.representative_news_item_id IS NULL AND {visible})"
        )))
        .bind(user_id)
        .fetch_one(&mut **transaction)
        .await?
    } else {
        true
    };
    let usage_day =
        sqlx::query_scalar::<_, chrono::NaiveDate>("SELECT (now() AT TIME ZONE 'UTC')::date")
            .fetch_one(&mut **transaction)
            .await?;
    let remaining =
        reserve_background(transaction, user_id, candidates.len().min(CANDIDATE_LIMIT)).await?;
    let more_work = candidates.len() > remaining && remaining > 0;
    candidates.truncate(remaining);
    Ok(Some(NewsFilterPreparation {
        usage_day,
        preference,
        candidates,
        lease_token,
        more_work,
        examined,
    }))
}
async fn ensure_usage(connection: &mut PgConnection, user_id: i64) -> Result<(), sqlx::Error> {
    sqlx::query("INSERT INTO news_filter_daily_usage(user_id,day) VALUES($1::bigint::integer,(now() AT TIME ZONE 'UTC')::date) ON CONFLICT DO NOTHING").bind(user_id).execute(connection).await?;
    Ok(())
}
async fn reserve_background(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
    count: usize,
) -> Result<usize, sqlx::Error> {
    ensure_usage(transaction, user_id).await?;
    let used:i32=sqlx::query_scalar("SELECT background_count FROM news_filter_daily_usage WHERE user_id=$1::bigint AND day=(now() AT TIME ZONE 'UTC')::date FOR UPDATE")
        .bind(user_id).fetch_one(&mut **transaction).await?;
    let reserved = count.min(usize::try_from(500 - used).unwrap_or(0));
    sqlx::query("UPDATE news_filter_daily_usage SET background_count=background_count+$2 WHERE user_id=$1::bigint AND day=(now() AT TIME ZONE 'UTC')::date")
        .bind(user_id).bind(i32::try_from(reserved).unwrap_or(500)).execute(&mut **transaction).await?;
    Ok(reserved)
}

/// Immutable inserts cannot replace an earlier outcome. A stale identity is never read by admission.
pub async fn store_decisions(
    transaction: &mut Transaction<'_, Postgres>,
    task_id: i64,
    user_id: i64,
    preparation: &NewsFilterPreparation,
    decisions: &[NewsFilterStoredDecision],
) -> Result<bool, sqlx::Error> {
    if !lock_active_user(transaction, user_id).await? {
        return Ok(false);
    }
    let lease=sqlx::query_scalar::<_,Uuid>("SELECT lease_token FROM processing_tasks WHERE id::bigint=$1 AND owner_user_id::bigint=$2 AND lease_token=$3 AND status='processing' AND lease_expires_at>timezone('UTC',clock_timestamp()) FOR SHARE")
        .bind(task_id).bind(user_id).bind(preparation.lease_token).fetch_optional(&mut **transaction).await?;
    if lease.is_none() {
        return Ok(false);
    }
    for decision in decisions {
        sqlx::query("INSERT INTO news_filter_decisions(user_id,news_item_id,revision,input_hash,evaluator_version,choice,no_match_probability,probabilities) SELECT $1::bigint::integer,n.id,$3,$4,$5,$6,$7,$8 FROM news_items n WHERE n.id::bigint=$2 AND n.status='ready' AND n.representative_news_item_id IS NULL AND news_filter_input_hash(n)=$4 ON CONFLICT DO NOTHING")
            .bind(user_id).bind(decision.news_item_id).bind(preparation.preference.revision).bind(&decision.input_hash).bind(EVALUATOR_VERSION)
            .bind(&decision.choice).bind(decision.no_match_probability).bind(&decision.probabilities).execute(&mut **transaction).await?;
    }
    Ok(true)
}

pub async fn load_preview_candidates(
    pool: &PgPool,
    user_id: i64,
    limit: usize,
) -> Result<Vec<NewsFilterCandidate>, sqlx::Error> {
    sqlx::query_as(AssertSqlSafe(format!(r"{SUBSCRIBED_AGGREGATORS_CTE}
        SELECT {} FROM news_items news WHERE {} ORDER BY COALESCE(news.published_at,news.ingested_at) DESC,news.id DESC LIMIT $2",
        evidence_columns(),candidate_predicate())))
        .bind(user_id).bind(i64::try_from(limit.min(20)).unwrap_or(20)).fetch_all(pool).await
}

pub async fn reserve_preview(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
    count: usize,
) -> Result<Option<Uuid>, sqlx::Error> {
    if count > 20 || !lock_active_user(transaction, user_id).await? {
        return Ok(None);
    }
    ensure_usage(transaction, user_id).await?;
    let token = Uuid::new_v4();
    sqlx::query_scalar("UPDATE news_filter_daily_usage SET preview_count=preview_count+$2,preview_token=$3,preview_busy_until=now()+interval '20 seconds' WHERE user_id=$1::bigint AND day=(now() AT TIME ZONE 'UTC')::date AND preview_count+$2<=200 AND (preview_busy_until IS NULL OR preview_busy_until<=now()) RETURNING preview_token")
        .bind(user_id).bind(i32::try_from(count).unwrap_or(20)).bind(token).fetch_optional(&mut **transaction).await
}
pub async fn release_preview(
    pool: &PgPool,
    user_id: i64,
    token: Uuid,
    unused: usize,
) -> Result<(), sqlx::Error> {
    sqlx::query("UPDATE news_filter_daily_usage SET preview_count=GREATEST(0,preview_count-$3),preview_token=NULL,preview_busy_until=NULL WHERE user_id=$1::bigint AND preview_token=$2")
        .bind(user_id).bind(token).bind(i32::try_from(unused.min(20)).unwrap_or(20)).execute(pool).await?;
    Ok(())
}

/// Release only slots from this exact reservation day that never dispatched an HTTP request.
pub async fn refund_background(
    connection: &mut PgConnection,
    user_id: i64,
    day: chrono::NaiveDate,
    unused: usize,
) -> Result<(), sqlx::Error> {
    sqlx::query("UPDATE news_filter_daily_usage SET background_count=GREATEST(0,background_count-$3) WHERE user_id=$1::bigint AND day=$2")
        .bind(user_id).bind(day).bind(i32::try_from(unused.min(CANDIDATE_LIMIT)).unwrap_or(128)).execute(connection).await?;
    Ok(())
}

pub async fn record_usage(
    connection: &mut PgConnection,
    user_id: i64,
    task_id: Option<i64>,
    usage: &NewsFilterUsage,
) -> Result<(), sqlx::Error> {
    let tokens = |value: u64| i32::try_from(value).unwrap_or(i32::MAX);
    sqlx::query(r"INSERT INTO vendor_usage_records(provider,model,feature,operation,source,request_id,task_id,user_id,
        request_count,input_tokens,output_tokens,total_tokens,currency,cost_usd,cost_basis,metadata,created_at)
        VALUES('typesafe','jev-1.14-20261014','news_filter',$1,$2,$3,$4::bigint::integer,(SELECT id FROM users WHERE id=$5::bigint FOR KEY SHARE),
        1,$6,$7,$8,'USD',$9,CASE WHEN $9::numeric IS NULL THEN NULL ELSE 'provider_reported' END,$10,timezone('UTC',now()))")
        .bind(if task_id.is_some(){"admission"}else{"preview"})
        .bind(if task_id.is_some(){"queue"}else{"api"}).bind(&usage.generation_id).bind(task_id).bind(user_id)
        .bind(tokens(usage.input_tokens)).bind(tokens(usage.output_tokens)).bind(tokens(usage.input_tokens.saturating_add(usage.output_tokens)))
        .bind(usage.cost_usd).bind(serde_json::json!({"news_item_id":usage.news_item_id,"outcome":usage.outcome,"reported_cost_usd":usage.cost_usd,"billing_known":usage.cost_usd.is_some()}))
        .execute(connection).await?;
    Ok(())
}

pub async fn list_news_ids(
    pool: &PgPool,
    user_id: i64,
    filtered: bool,
    before_id: Option<i64>,
    limit: usize,
) -> Result<Vec<NewsFilterListRow>, sqlx::Error> {
    // A filtered collection starts from applicable negative decisions; don't hash all history.
    let negative_only = if filtered {
        "AND pref.enabled AND d.choice='no_match' AND d.no_match_probability>=0.90 AND COALESCE(news.published_at,news.ingested_at)>=timezone('UTC',now())-interval '7 days'"
    } else {
        ""
    };
    sqlx::query_as(AssertSqlSafe(format!(r"{SUBSCRIBED_AGGREGATORS_CTE}, preference AS (
       SELECT revision,enabled FROM news_filter_preferences WHERE user_id=$1::bigint ORDER BY revision DESC LIMIT 1
    ), items AS (
      SELECT news.id::bigint AS news_item_id,
        CASE WHEN NOT COALESCE(pref.enabled,false) THEN 'off'
        WHEN COALESCE(news.published_at,news.ingested_at)<timezone('UTC',now())-interval '7 days' THEN 'outside_window'
        WHEN d.choice='no_match' AND d.no_match_probability>=0.90 THEN 'filtered_out'
        WHEN d.choice='match' THEN 'matched'
        WHEN d.choice IS NOT NULL THEN 'uncertain'
        WHEN p.filter_outcome='unavailable' AND p.filter_revision=pref.revision AND p.filter_input_hash=news_filter_input_hash(news) THEN 'unavailable'
        WHEN EXISTS(SELECT 1 FROM briefing_segments s WHERE s.user_id=$1::bigint AND s.status IN ('active','degraded') AND s.source_keys::jsonb ? ('news:' || news.id::text)) THEN 'published'
        WHEN EXISTS(SELECT 1 FROM news_item_read_status r JOIN news_items member ON member.id=r.news_item_id WHERE r.user_id=$1::bigint AND COALESCE(member.representative_news_item_id,member.id)=news.id) THEN 'read'
        WHEN EXISTS(SELECT 1 FROM news_filter_daily_usage u WHERE u.user_id=$1::bigint AND u.day=(now() AT TIME ZONE 'UTC')::date AND u.background_count>=500) THEN 'deferred'
        ELSE 'pending' END AS filter_status
      FROM news_items news LEFT JOIN preference pref ON true
      LEFT JOIN news_filter_decisions d ON d.user_id=$1::bigint AND d.news_item_id=news.id AND d.revision=pref.revision
         AND d.input_hash=news_filter_input_hash(news) AND d.evaluator_version=$4
      LEFT JOIN briefing_pending_sources p ON p.user_id=$1::bigint AND p.source_kind='news' AND p.source_id=news.id
      WHERE news.status='ready' AND news.representative_news_item_id IS NULL AND {}
        AND ($2::bigint IS NULL OR news.id::bigint<$2) {negative_only}
    ) SELECT news_item_id,filter_status FROM items WHERE NOT $3 OR filter_status='filtered_out' ORDER BY news_item_id DESC LIMIT $5",visible_news_predicate("news"))))
      .bind(user_id).bind(before_id).bind(filtered).bind(EVALUATOR_VERSION).bind(i64::try_from(limit.min(51)).unwrap_or(51)).fetch_all(pool).await
}

#[cfg(test)]
mod tests;
