use chrono::NaiveDateTime;
use serde::Serialize;
use serde_json::Value;
use sqlx::{FromRow, PgPool};
use thiserror::Error;

const MAX_TITLE_CLUSTERING_ROWS: i64 = 100_000;

#[derive(Debug, Clone, PartialEq, Serialize, FromRow)]
pub(crate) struct TitleClusteringSourceRow {
    pub(crate) content_id: i32,
    pub(crate) content_type: String,
    pub(crate) url: String,
    pub(crate) title: Option<String>,
    pub(crate) source: Option<String>,
    pub(crate) status: String,
    pub(crate) classification: Option<String>,
    pub(crate) content_metadata: Value,
    pub(crate) created_at: NaiveDateTime,
    pub(crate) updated_at: Option<NaiveDateTime>,
    pub(crate) processed_at: Option<NaiveDateTime>,
    pub(crate) publication_date: Option<NaiveDateTime>,
    pub(crate) platform: Option<String>,
    pub(crate) source_url: Option<String>,
    pub(crate) news_item_id: Option<i32>,
    pub(crate) news_item_status: Option<String>,
    pub(crate) news_item_summary_title: Option<String>,
    pub(crate) news_item_article_title: Option<String>,
    pub(crate) news_item_summary_text: Option<String>,
    pub(crate) news_item_article_url: Option<String>,
    pub(crate) news_item_article_domain: Option<String>,
    pub(crate) news_item_discussion_url: Option<String>,
    pub(crate) news_item_source_label: Option<String>,
    pub(crate) news_item_source_type: Option<String>,
    pub(crate) news_item_visibility_scope: Option<String>,
    pub(crate) news_item_representative_id: Option<i32>,
    pub(crate) news_item_cluster_size: Option<i32>,
    pub(crate) news_item_ingested_at: Option<NaiveDateTime>,
}

/// Loads bounded, read-only relational inputs for offline title-clustering evaluation.
///
/// The snapshot deliberately excludes content bodies, user data, task payloads, and credentials.
///
/// # Errors
///
/// Returns [`EvalExportError::InvalidLimit`] when the requested row count is out of bounds, or
/// [`EvalExportError::Sqlx`] when `PostgreSQL` cannot execute or decode the read-only query.
pub(crate) async fn load_title_clustering_source(
    pool: &PgPool,
    limit: i64,
) -> Result<Vec<TitleClusteringSourceRow>, EvalExportError> {
    if !(1..=MAX_TITLE_CLUSTERING_ROWS).contains(&limit) {
        return Err(EvalExportError::InvalidLimit {
            maximum: MAX_TITLE_CLUSTERING_ROWS,
        });
    }

    Ok(sqlx::query_as::<_, TitleClusteringSourceRow>(
        r"
        SELECT
            c.id AS content_id,
            c.content_type,
            c.url,
            c.title,
            c.source,
            c.status,
            c.classification,
            c.content_metadata,
            c.created_at,
            c.updated_at,
            c.processed_at,
            c.publication_date,
            c.platform,
            c.source_url,
            ni.id AS news_item_id,
            ni.status AS news_item_status,
            NULLIF(BTRIM(ni.raw_metadata -> 'summary' ->> 'title'), '')
                AS news_item_summary_title,
            NULLIF(BTRIM(ni.raw_metadata -> 'article' ->> 'title'), '')
                AS news_item_article_title,
            ni.summary_text AS news_item_summary_text,
            COALESCE(
                NULLIF(BTRIM(ni.article_url), ''),
                NULLIF(BTRIM(ni.raw_metadata -> 'article' ->> 'url'), '')
            ) AS news_item_article_url,
            ni.article_domain AS news_item_article_domain,
            ni.discussion_url AS news_item_discussion_url,
            ni.source_label AS news_item_source_label,
            ni.source_type AS news_item_source_type,
            ni.visibility_scope AS news_item_visibility_scope,
            ni.representative_news_item_id AS news_item_representative_id,
            ni.cluster_size AS news_item_cluster_size,
            ni.ingested_at AS news_item_ingested_at
        FROM contents AS c
        LEFT JOIN news_items AS ni ON ni.legacy_content_id = c.id
        ORDER BY c.id DESC, ni.id DESC
        LIMIT $1
        ",
    )
    .bind(limit)
    .fetch_all(pool)
    .await?)
}

#[derive(Debug, Error)]
pub(crate) enum EvalExportError {
    #[error("limit must be between 1 and {maximum}")]
    InvalidLimit { maximum: i64 },
    #[error(transparent)]
    Sqlx(#[from] sqlx::Error),
}
