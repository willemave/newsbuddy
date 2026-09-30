use chrono::{DateTime, Duration, Utc};
use serde_json::Value;
use sqlx::{AssertSqlSafe, FromRow, Postgres, Transaction};

use super::{NewsCategoryPending, NewsCategorySnapshot, NewsCategoryStory};
use crate::briefing_refresh::{
    BriefingRefreshRepositoryError, SourceNewsRow, preparation, sources,
};
use crate::news_lens_embeddings::{ENCODER_VERSION, input_hash};

#[derive(FromRow)]
struct CachedStory {
    #[sqlx(flatten)]
    source: SourceNewsRow,
    event_id: i64,
    input_hash: String,
    vector: Value,
    dimensions: i32,
}

pub async fn load_snapshot(
    tx: &mut Transaction<'_, Postgres>,
    user_id: i64,
    cutoff: DateTime<Utc>,
    model: &str,
) -> Result<Option<NewsCategorySnapshot>, BriefingRefreshRepositoryError> {
    let version: Option<i32> = sqlx::query_scalar(
        "SELECT s.version FROM briefing_states s JOIN users u ON u.id=s.user_id WHERE s.user_id::bigint=$1 AND u.is_active IS TRUE",
    ).bind(user_id).fetch_optional(&mut **tx).await?;
    let Some(version) = version else {
        return Ok(None);
    };
    // Include recent read items in fitting; reading never adds their coverage back.
    // Use the same visibility SQL as normal Briefing preparation.
    let rows = sqlx::query_as::<_, CachedStory>(AssertSqlSafe(format!(r#"
        WITH visible_news AS ({})
        SELECT news.id::bigint AS id,
          coalesce(news.representative_news_item_id,news.id)::bigint AS event_id,
          news.summary_text,
          news.summary_key_points::jsonb AS summary_key_points,
          news.raw_metadata::jsonb AS raw_metadata, news.article_url,
          news.canonical_story_url, news.canonical_item_url, news.published_at,
          news.processed_at, news.ingested_at, news.created_at,
          e.input_hash, e.vector, e.dimensions
        FROM visible_news news
        JOIN news_lens_embeddings e ON e.news_item_id=news.id AND e.model=$2
        JOIN news_lens_embedding_models m ON m.model=e.model AND m.dimensions=e.dimensions
        WHERE greatest(news.ingested_at,coalesce(news.processed_at,news.ingested_at)) BETWEEN $3 AND $4
          AND e.encoder_version=$5 AND e.dimensions=jsonb_array_length(e.vector)
        ORDER BY greatest(news.ingested_at,coalesce(news.processed_at,news.ingested_at)) DESC, news.id DESC
        LIMIT 1500
    "#, sources::visible_news_sql())))
        .bind(user_id).bind(model).bind((cutoff-Duration::days(14)).naive_utc())
        .bind(cutoff.naive_utc()).bind(ENCODER_VERSION).fetch_all(&mut **tx).await?;
    let mut stories = Vec::with_capacity(rows.len());
    let mut corpus = Vec::new();
    for row in rows.into_iter().rev() {
        let event_id = row.event_id;
        let news = row.source;
        let available_at = news
            .ingested_at
            .max(news.processed_at.unwrap_or(news.ingested_at))
            .and_utc();
        let source = sources::source_from_news(
            news.id,
            news.summary_text.as_deref(),
            &news.summary_key_points,
            &news.raw_metadata,
            news.article_url.as_deref(),
            news.canonical_story_url.as_deref(),
            news.canonical_item_url.as_deref(),
            news.published_at,
            news.processed_at,
            news.ingested_at,
            news.created_at,
        );
        if input_hash(&source.embedding_text()) != row.input_hash {
            continue;
        }
        let Ok(vector) = serde_json::from_value::<Vec<f64>>(row.vector) else {
            continue;
        };
        if vector.len() != usize::try_from(row.dimensions).unwrap_or(0)
            || vector.is_empty()
            || vector.iter().any(|v| !v.is_finite())
            || !vector.iter().any(|v| *v != 0.0)
        {
            continue;
        }
        corpus.push((
            source.id,
            event_id,
            row.input_hash,
            available_at.timestamp(),
        ));
        stories.push(NewsCategoryStory {
            source,
            event_id,
            available_at,
            vector,
        });
    }
    let lenses = preparation::semantic_lens_rows(tx, user_id)
        .await?
        .into_iter()
        .map(preparation::semantic_lens_from_row)
        .collect::<Result<Vec<_>, _>>()?;
    let all_keys = sqlx::query_scalar::<_, String>(
        "SELECT key FROM briefing_lenses WHERE user_id::bigint=$1 ORDER BY key",
    )
    .bind(user_id)
    .fetch_all(&mut **tx)
    .await?;
    let pending = sqlx::query_as::<_, NewsCategoryPending>(
        "SELECT id::bigint AS id, source_id::bigint AS source_id, lens_key FROM briefing_pending_sources WHERE user_id::bigint=$1 AND source_kind='news' ORDER BY id",
    ).bind(user_id).fetch_all(&mut **tx).await?;
    let configs: Vec<(i64, Value, bool)> = sqlx::query_as(
        "SELECT id::bigint,config::jsonb,is_active FROM user_scraper_configs WHERE user_id::bigint=$1 AND scraper_type='aggregator' ORDER BY id",
    ).bind(user_id).fetch_all(&mut **tx).await?;
    let corpus_hash = input_hash(&format!("{model}|{ENCODER_VERSION}|{corpus:?}|{configs:?}"));
    let fingerprint = input_hash(&format!(
        "{corpus_hash}|{version}|{lenses:?}|{all_keys:?}|{pending:?}"
    ));
    Ok(Some(NewsCategorySnapshot {
        user_id,
        cutoff,
        model: model.into(),
        stories,
        lenses,
        all_keys,
        pending,
        fingerprint,
        corpus_hash,
    }))
}

pub async fn lock_snapshot_inputs(
    tx: &mut Transaction<'_, Postgres>,
    snapshot: &NewsCategorySnapshot,
) -> Result<(), sqlx::Error> {
    // Configuration mutations already share-lock the owner; an exclusive owner
    // lock also prevents new subscription rows from appearing during publication.
    sqlx::query("SELECT id FROM users WHERE id::bigint=$1 FOR UPDATE")
        .bind(snapshot.user_id)
        .execute(&mut **tx)
        .await?;
    sqlx::query("SELECT user_id FROM briefing_states WHERE user_id::bigint=$1 FOR UPDATE")
        .bind(snapshot.user_id)
        .execute(&mut **tx)
        .await?;
    sqlx::query("SELECT id FROM briefing_lenses WHERE user_id::bigint=$1 ORDER BY id FOR UPDATE")
        .bind(snapshot.user_id)
        .execute(&mut **tx)
        .await?;
    sqlx::query(
        "SELECT id FROM briefing_pending_sources WHERE user_id::bigint=$1 ORDER BY id FOR UPDATE",
    )
    .bind(snapshot.user_id)
    .execute(&mut **tx)
    .await?;
    let ids: Vec<i64> = snapshot.stories.iter().map(|s| s.source.id).collect();
    sqlx::query("SELECT id FROM news_items WHERE id::bigint=ANY($1) ORDER BY id FOR SHARE")
        .bind(&ids)
        .execute(&mut **tx)
        .await?;
    sqlx::query("SELECT news_item_id FROM news_lens_embeddings WHERE news_item_id::bigint=ANY($1) AND model=$2 ORDER BY news_item_id FOR SHARE")
        .bind(&ids).bind(&snapshot.model).execute(&mut **tx).await?;
    Ok(())
}
