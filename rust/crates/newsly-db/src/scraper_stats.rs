use std::collections::{BTreeSet, HashMap, HashSet};
use std::time::Duration;

use chrono::{DateTime, NaiveDate, NaiveDateTime, TimeDelta, Utc};
use serde_json::Value;
use sqlx::{FromRow, PgPool};
use thiserror::Error;

use crate::{ScraperConfigProjection, canonicalize_feed_url};

#[derive(Debug, Clone, Default, PartialEq)]
pub struct ScraperConfigStatsProjection {
    pub last_fetch_at: Option<DateTime<Utc>>,
    pub ingestion_error: Option<String>,
    pub total_count: i64,
    pub completed_count: i64,
    pub unread_count: i64,
    pub processing_count: i64,
    pub latest_processed_at: Option<DateTime<Utc>>,
    pub latest_publication_at: Option<DateTime<Utc>>,
    pub next_expected_at: Option<DateTime<Utc>>,
    pub average_interval_hours: Option<f64>,
    pub interval_sample_size: usize,
}

#[derive(Debug, Default)]
struct WorkingStats {
    response: ScraperConfigStatsProjection,
    publication_dates: BTreeSet<DateTime<Utc>>,
    completed_ids: HashSet<i64>,
    processing_candidates: HashSet<i64>,
    checked_out_processing_ids: HashSet<i64>,
}

/// Derive the established per-source counters without retaining a transaction or ORM identity.
///
/// # Errors
///
/// Returns [`ScraperStatsRepositoryError`] when a statistics query fails.
pub async fn get_scraper_config_stats(
    pool: &PgPool,
    user_id: i64,
    configs: &[ScraperConfigProjection],
    checkout_timeout: Duration,
) -> Result<HashMap<i64, ScraperConfigStatsProjection>, ScraperStatsRepositoryError> {
    if configs.is_empty() {
        return Ok(HashMap::new());
    }
    let content_rows = load_content_rows(pool, user_id).await?;
    let index = ConfigIndex::new(configs);
    let allowed_content = AllowedContent::new(configs);
    let processing_cutoff = Utc::now()
        - TimeDelta::try_seconds(i64::try_from(checkout_timeout.as_secs()).unwrap_or(i64::MAX))
            .unwrap_or(TimeDelta::MAX);
    let mut working = configs
        .iter()
        .map(|config| (config.id, WorkingStats::default()))
        .collect::<HashMap<_, _>>();
    let mut matched_content_ids = HashSet::new();

    for content in content_rows {
        if !allowed_content.includes(&content) {
            continue;
        }
        let Some(config_id) = index.match_content(&content) else {
            continue;
        };
        let Some(stats) = working.get_mut(&config_id) else {
            continue;
        };
        matched_content_ids.insert(content.id);
        stats.response.total_count += 1;

        let publication_at = publication_at(&content);
        stats.publication_dates.insert(publication_at);
        if stats
            .response
            .latest_publication_at
            .is_none_or(|current| publication_at > current)
        {
            stats.response.latest_publication_at = Some(publication_at);
        }
        if let Some(processed_at) = content.processed_at.map(|value| value.and_utc())
            && stats
                .response
                .latest_processed_at
                .is_none_or(|current| processed_at > current)
        {
            stats.response.latest_processed_at = Some(processed_at);
        }

        if content.status == "completed" && content.classification.as_deref() != Some("skip") {
            stats.response.completed_count += 1;
            stats.completed_ids.insert(content.id);
        }
        if matches!(
            content.status.as_str(),
            "new" | "pending" | "processing" | "awaiting_image"
        ) {
            stats.processing_candidates.insert(content.id);
            if content.checked_out_by.is_some()
                && content
                    .checked_out_at
                    .map(|value| value.and_utc())
                    .is_some_and(|checked_out_at| checked_out_at >= processing_cutoff)
            {
                stats.checked_out_processing_ids.insert(content.id);
            }
        }
    }

    let completed_ids = working
        .values()
        .flat_map(|stats| stats.completed_ids.iter().copied())
        .collect::<Vec<_>>();
    let matched_ids = matched_content_ids.into_iter().collect::<Vec<_>>();
    let (read_ids, active_task_ids) = tokio::try_join!(
        load_read_content_ids(pool, user_id, &completed_ids),
        load_active_task_content_ids(pool, &matched_ids)
    )?;

    let observations = sqlx::query_as::<_, (i64, Option<DateTime<Utc>>, Option<String>)>("SELECT config.id::bigint, health.last_success_at, health.error_code FROM user_scraper_configs AS config LEFT JOIN source_ingestion_health AS health ON health.source_key = CASE WHEN config.scraper_type = 'aggregator' THEN 'aggregator:' || (config.config::jsonb ->> 'key') ELSE 'config:' || config.id::text END WHERE config.user_id::bigint = $1")
        .bind(user_id).fetch_all(pool).await?;
    for (id, success, error) in observations {
        if let Some(stats) = working.get_mut(&id) {
            stats.response.last_fetch_at = success;
            stats.response.ingestion_error = error;
        }
    }
    Ok(working
        .into_iter()
        .map(|(config_id, mut stats)| {
            stats.response.unread_count =
                i64::try_from(stats.completed_ids.difference(&read_ids).count())
                    .unwrap_or(i64::MAX);
            stats.response.processing_count = i64::try_from(
                stats
                    .processing_candidates
                    .intersection(&active_task_ids)
                    .chain(stats.checked_out_processing_ids.iter())
                    .copied()
                    .collect::<HashSet<_>>()
                    .len(),
            )
            .unwrap_or(i64::MAX);
            let (next_expected_at, average_interval_hours, sample_size) =
                estimate_next_expected_at(stats.publication_dates);
            stats.response.next_expected_at = next_expected_at;
            stats.response.average_interval_hours = average_interval_hours;
            stats.response.interval_sample_size = sample_size;
            (config_id, stats.response)
        })
        .collect())
}

#[derive(Debug, FromRow)]
struct ContentStatsRow {
    id: i64,
    status: String,
    classification: Option<String>,
    processed_at: Option<NaiveDateTime>,
    publication_date: Option<NaiveDateTime>,
    created_at: Option<NaiveDateTime>,
    source: Option<String>,
    content_metadata: Value,
    checked_out_by: Option<String>,
    checked_out_at: Option<NaiveDateTime>,
    content_type: String,
    platform: Option<String>,
}

async fn load_content_rows(
    pool: &PgPool,
    user_id: i64,
) -> Result<Vec<ContentStatsRow>, ScraperStatsRepositoryError> {
    Ok(sqlx::query_as::<_, ContentStatsRow>(
        r"
        SELECT
            content.id::bigint AS id,
            content.status,
            content.classification,
            content.processed_at,
            content.publication_date,
            content.created_at,
            content.source,
            content.content_metadata,
            content.checked_out_by,
            content.checked_out_at,
            content.content_type,
            content.platform
        FROM contents AS content
        JOIN content_status AS membership ON membership.content_id = content.id
        WHERE membership.user_id::bigint = $1
          AND membership.status = 'inbox'
          AND (
              content.content_type IN ('article', 'podcast')
              OR (content.platform = 'youtube' AND content.content_type <> 'news')
          )
        ",
    )
    .bind(user_id)
    .fetch_all(pool)
    .await?)
}

async fn load_read_content_ids(
    pool: &PgPool,
    user_id: i64,
    content_ids: &[i64],
) -> Result<HashSet<i64>, ScraperStatsRepositoryError> {
    if content_ids.is_empty() {
        return Ok(HashSet::new());
    }
    Ok(sqlx::query_scalar::<_, i64>(
        r"
        SELECT content_id::bigint
        FROM content_read_status
        WHERE user_id::bigint = $1 AND content_id::bigint = ANY($2::bigint[])
        ",
    )
    .bind(user_id)
    .bind(content_ids)
    .fetch_all(pool)
    .await?
    .into_iter()
    .collect())
}

async fn load_active_task_content_ids(
    pool: &PgPool,
    content_ids: &[i64],
) -> Result<HashSet<i64>, ScraperStatsRepositoryError> {
    if content_ids.is_empty() {
        return Ok(HashSet::new());
    }
    Ok(sqlx::query_scalar::<_, i64>(
        r"
        SELECT DISTINCT content_id::bigint
        FROM processing_tasks
        WHERE content_id::bigint = ANY($1::bigint[])
          AND status IN ('pending', 'processing')
        ",
    )
    .bind(content_ids)
    .fetch_all(pool)
    .await?
    .into_iter()
    .collect())
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
enum AllowedContentKind {
    Article,
    Podcast,
    Youtube,
}

#[derive(Debug)]
struct AllowedContent {
    kinds: HashSet<AllowedContentKind>,
}

impl AllowedContent {
    fn new(configs: &[ScraperConfigProjection]) -> Self {
        let mut kinds = HashSet::new();
        for config in configs {
            match config.scraper_type.as_str() {
                "substack" | "atom" => {
                    kinds.insert(AllowedContentKind::Article);
                }
                "podcast_rss" => {
                    kinds.insert(AllowedContentKind::Podcast);
                }
                "youtube" => {
                    kinds.insert(AllowedContentKind::Youtube);
                }
                _ => {}
            }
        }
        Self { kinds }
    }

    fn includes(&self, content: &ContentStatsRow) -> bool {
        self.kinds.is_empty()
            || (self.kinds.contains(&AllowedContentKind::Article)
                && content.content_type == "article")
            || (self.kinds.contains(&AllowedContentKind::Podcast)
                && content.content_type == "podcast")
            || (self.kinds.contains(&AllowedContentKind::Youtube)
                && content.platform.as_deref() == Some("youtube")
                && content.content_type != "news")
    }
}

#[derive(Debug)]
struct ConfigIndex {
    ids: HashSet<i64>,
    feed_urls: HashMap<String, Vec<i64>>,
    sources: HashMap<String, Vec<i64>>,
}

impl ConfigIndex {
    fn new(configs: &[ScraperConfigProjection]) -> Self {
        let mut by_feed_url: HashMap<String, Vec<i64>> = HashMap::new();
        let mut by_source: HashMap<String, Vec<i64>> = HashMap::new();
        for config in configs {
            let object = config.config.as_object();
            let feed_url = config.feed_url.as_deref().or_else(|| {
                object
                    .and_then(|value| value.get("feed_url"))
                    .and_then(Value::as_str)
            });
            if let Some(feed_url) = normalized_feed_url(feed_url) {
                by_feed_url.entry(feed_url).or_default().push(config.id);
            }
            let mut labels = Vec::with_capacity(3);
            labels.extend(config.display_name.iter().cloned());
            labels.extend(
                object
                    .and_then(|value| value.get("name"))
                    .and_then(Value::as_str)
                    .map(str::to_owned),
            );
            labels.extend(feed_url.and_then(feed_domain));
            for label in labels {
                let label = label.trim().to_lowercase();
                if !label.is_empty() {
                    by_source.entry(label).or_default().push(config.id);
                }
            }
        }
        Self {
            ids: configs.iter().map(|config| config.id).collect(),
            feed_urls: by_feed_url,
            sources: by_source,
        }
    }

    fn match_content(&self, content: &ContentStatsRow) -> Option<i64> {
        let metadata = content.content_metadata.as_object();
        if let Some(config_id) = metadata
            .and_then(|value| value.get("feed_config_id"))
            .and_then(coerce_config_id)
            .filter(|config_id| self.ids.contains(config_id))
        {
            return Some(config_id);
        }
        if let Some(feed_url) = metadata
            .and_then(|value| value.get("feed_url"))
            .and_then(Value::as_str)
            .and_then(|value| normalized_feed_url(Some(value)))
            && let Some(matches) = self.feed_urls.get(&feed_url)
            && matches.len() == 1
        {
            return matches.first().copied();
        }
        let source = content.source.as_deref().or_else(|| {
            metadata
                .and_then(|value| value.get("source"))
                .and_then(Value::as_str)
        });
        let source = source?.trim().to_lowercase();
        if source.is_empty() {
            return None;
        }
        self.sources
            .get(&source)
            .filter(|matches| matches.len() == 1)
            .and_then(|matches| matches.first().copied())
    }
}

fn coerce_config_id(value: &Value) -> Option<i64> {
    value.as_i64().or_else(|| {
        value
            .as_str()
            .filter(|value| value.bytes().all(|byte| byte.is_ascii_digit()))?
            .parse()
            .ok()
    })
}

fn normalized_feed_url(value: Option<&str>) -> Option<String> {
    let value = value?.trim();
    (!value.is_empty()).then(|| canonicalize_feed_url(value))
}

fn feed_domain(value: &str) -> Option<String> {
    let (_, remainder) = value.split_once("://")?;
    let authority = remainder
        .split(['/', '?', '#'])
        .next()
        .unwrap_or_default()
        .trim()
        .to_lowercase();
    (!authority.is_empty()).then_some(authority)
}

fn publication_at(content: &ContentStatsRow) -> DateTime<Utc> {
    content
        .publication_date
        .map(|value| value.and_utc())
        .or_else(|| {
            content
                .content_metadata
                .as_object()
                .and_then(|metadata| metadata.get("publication_date"))
                .and_then(parse_metadata_date)
        })
        .or_else(|| content.created_at.map(|value| value.and_utc()))
        .unwrap_or_else(Utc::now)
}

fn parse_metadata_date(value: &Value) -> Option<DateTime<Utc>> {
    let value = value.as_str()?.trim();
    DateTime::parse_from_rfc3339(value)
        .map(|value| value.with_timezone(&Utc))
        .ok()
        .or_else(|| parse_naive_date_time(value).map(|value| value.and_utc()))
        .or_else(|| {
            NaiveDate::parse_from_str(value, "%Y-%m-%d")
                .ok()
                .and_then(|value| value.and_hms_opt(0, 0, 0))
                .map(|value| value.and_utc())
        })
}

fn parse_naive_date_time(value: &str) -> Option<NaiveDateTime> {
    [
        "%Y-%m-%dT%H:%M:%S%.f",
        "%Y-%m-%d %H:%M:%S%.f",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
    ]
    .into_iter()
    .find_map(|format| NaiveDateTime::parse_from_str(value, format).ok())
}

fn estimate_next_expected_at(
    dates: BTreeSet<DateTime<Utc>>,
) -> (Option<DateTime<Utc>>, Option<f64>, usize) {
    let dates = dates.into_iter().rev().collect::<Vec<_>>();
    if dates.len() < 2 {
        return (None, None, 0);
    }
    let intervals = dates
        .windows(2)
        .take(4)
        .map(|pair| pair[0].signed_duration_since(pair[1]))
        .filter(|interval| *interval > TimeDelta::zero())
        .collect::<Vec<_>>();
    if intervals.is_empty() {
        return (None, None, 0);
    }
    let sample_size = intervals.len();
    let divisor = i32::try_from(sample_size).expect("at most four intervals are sampled");
    let average_interval = intervals.into_iter().sum::<TimeDelta>() / divisor;
    let average_hours = average_interval
        .to_std()
        .ok()
        .map(|duration| duration.as_secs_f64() / 3_600.0);
    let predicted = dates[0].checked_add_signed(average_interval);
    (predicted, average_hours, sample_size)
}

#[derive(Debug, Error)]
pub enum ScraperStatsRepositoryError {
    #[error("scraper statistics database operation failed")]
    Sqlx(#[from] sqlx::Error),
}

#[cfg(test)]
mod tests {
    use std::collections::BTreeSet;

    use chrono::{TimeZone, Utc};

    use super::estimate_next_expected_at;

    #[test]
    fn expected_interval_uses_at_most_four_recent_samples() {
        let dates = (0..6)
            .map(|day| Utc.with_ymd_and_hms(2026, 3, 1 + day, 8, 0, 0).unwrap())
            .collect::<BTreeSet<_>>();
        let (predicted, average, count) = estimate_next_expected_at(dates);
        assert_eq!(count, 4);
        assert_eq!(average, Some(24.0));
        assert_eq!(
            predicted,
            Some(Utc.with_ymd_and_hms(2026, 3, 7, 8, 0, 0).unwrap())
        );
    }
}
